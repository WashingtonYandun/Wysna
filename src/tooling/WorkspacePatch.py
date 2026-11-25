"""Check coordinated source edits (modify/symbol/hunks/create/delete) against one project revision before applying."""
import difflib
import hashlib
from pathlib import PurePosixPath

from compiler.CompilerService import CompilerService
from error.Error import WysnaError
from project.Resolver import ProjectResolver
from project.Testing import _snapshot_digest, build_test_plan, project_revision, project_source_snapshot
from tooling.SourceWrite import write_workspace_operations


def _validate_rel_wysna(name):
    if not isinstance(name, str) or not name:
        return False
    relative = PurePosixPath(name)
    if (relative.is_absolute() or '..' in relative.parts or relative.as_posix() != name
            or relative.suffix != '.wysna'):
        return False
    return True


def _assert_no_symlink_ancestors(root, target):
    cur = target
    while cur != root:
        if cur.exists() and cur.resolve() != cur:
            return False
        if cur == cur.parent:
            return False
        cur = cur.parent
    return True


def _apply_hunks(name, path, original_bytes, hunks):
    if not isinstance(hunks, list) or not hunks:
        raise WysnaError(f"Workspace edit 'hunks' must be a non-empty array for {name!r}",
                         file=str(path), code='W5002', stage='patch')
    text = original_bytes.decode('utf-8')
    for idx, hunk in enumerate(hunks):
        if not isinstance(hunk, dict) or set(hunk) != {'old_text', 'new_text'}:
            raise WysnaError(f"Hunk #{idx} requires 'old_text' and 'new_text'",
                             file=str(path), code='W5002', stage='patch')
        old_text, new_text = hunk['old_text'], hunk['new_text']
        if not isinstance(old_text, str) or not isinstance(new_text, str) or not old_text:
            raise WysnaError(f"Hunk #{idx} requires non-empty string 'old_text' and string 'new_text'",
                             file=str(path), code='W5002', stage='patch')
        matches = text.count(old_text)
        if matches != 1:
            raise WysnaError(
                f"Hunk #{idx} matched {matches} times in '{name}' (expected 1)",
                file=str(path), code='W5002', stage='patch',
            )
        text = text.replace(old_text, new_text, 1)
    return text.encode('utf-8')


def _apply_symbol_edit(resolver, root, name, path, original_bytes, symbol_name, replacement):
    from compiler.SemanticGraph import resolve_graph_symbol
    if not isinstance(symbol_name, str) or not symbol_name.strip() or not isinstance(replacement, str):
        raise WysnaError(f"Symbol edit requires non-empty 'symbol' and string 'replacement' for {name!r}",
                         file=str(path), code='W5002', stage='patch')
    service = CompilerService(project_root=root, project_resolver=resolver)
    checked = service.check_file(path)
    graph = service.graph(checked)
    node = resolve_graph_symbol(graph, symbol_name, 'patch')
    if node is None:
        raise WysnaError(f"Unknown symbol '{symbol_name}' in '{name}'",
                         file=str(path), code='W1001', stage='patch')
    if node['file'] != str(path.resolve()):
        raise WysnaError(
            f"Target symbol '{symbol_name}' belongs to '{node['file']}', not '{path}'",
            file=str(path), code='W5002', stage='patch',
        )
    return original_bytes[:node['start_byte']] + replacement.encode('utf-8') + original_bytes[node['end_byte']:]


def _load_edits(resolver, root, files, request, protected_entries):
    if not isinstance(request, list) or not request:
        raise ValueError('Workspace edits must be a nonempty array')
    edits = {}
    seen = set()
    for edit in request:
        if not isinstance(edit, dict) or 'path' not in edit:
            raise ValueError('Each workspace edit requires path')
        name = edit['path']
        if not _validate_rel_wysna(name) or name in seen:
            raise ValueError(f'Workspace edit must use a unique relative .wysna path: {name!r}')
        seen.add(name)
        keys = set(edit) - {'path'}
        path = root / name

        if keys == {'create', 'source'}:
            if edit['create'] is not True or not isinstance(edit['source'], str):
                raise ValueError('Create workspace edit requires create=true and string source')
            if name in files or path.exists() or not _assert_no_symlink_ancestors(root, path):
                raise WysnaError(f"Cannot create existing or symlinked workspace path: '{name}'",
                                 file=str(path), code='W5002', stage='patch')
            after = edit['source'].encode('utf-8')
            edits[name] = ('create', path, None, after)
            continue

        if keys == {'delete'}:
            if edit['delete'] is not True:
                raise ValueError('Delete workspace edit requires delete=true')
            if name not in files or path.resolve() != path:
                raise WysnaError(f"Delete workspace edit must reference one existing project source: '{name}'",
                                 file=str(path), code='W5002', stage='patch')
            if name in protected_entries:
                raise WysnaError(f"Cannot delete protected manifest entry: '{name}'",
                                 file=str(path), code='W5002', stage='patch')
            before = path.read_bytes()
            if hashlib.sha256(before).hexdigest() != files[name]:
                raise WysnaError(f'Source changed during workspace planning: {name}', code='W5005', stage='patch')
            edits[name] = ('delete', path, before, None)
            continue

        if name not in files or path.resolve() != path:
            raise ValueError(f'Workspace edit must identify one existing project source: {name!r}')
        before = path.read_bytes()
        if hashlib.sha256(before).hexdigest() != files[name]:
            raise WysnaError(f'Source changed during workspace planning: {name}', code='W5005', stage='patch')

        if keys == {'source'}:
            source = edit['source']
            if not isinstance(source, str):
                raise ValueError('Workspace edit path and source must be strings')
            after = source.encode('utf-8')
        elif keys == {'symbol', 'replacement'}:
            after = _apply_symbol_edit(resolver, root, name, path, before, edit['symbol'], edit['replacement'])
        elif keys == {'hunks'}:
            after = _apply_hunks(name, path, before, edit['hunks'])
        else:
            raise ValueError(
                f"Unsupported workspace edit keys for {name!r}; expected source, symbol+replacement, hunks, create+source, or delete"
            )
        if before != after:
            edits[name] = ('modify', path, before, after)

    if not edits:
        raise ValueError('Workspace edit contains no changes')
    return edits


def _check_overlay(resolver, entries, edits):
    root = resolver.manifest.root
    inputs = {}
    for entry in entries:
        service = CompilerService(project_root=root, project_resolver=resolver)
        for action, path, _, after in edits.values():
            if action == 'delete':
                service.mark_deleted(str(path))
            else:
                service.set_overlay(str(path), after.decode('utf-8'))
        service.check_file(root / entry)
        inputs[entry] = set(service.source_texts())
    return inputs


def plan_workspace_patch(manifest_path, request, *, expected_hash=None, apply=False,
                         discover=False, frozen=False):
    if apply and not expected_hash:
        raise ValueError('Applying workspace edits requires --expect-plan-hash from a successful preview')
    resolver = ProjectResolver.load(manifest_path)
    if frozen:
        resolver.verify_lock()
    manifest = resolver.manifest
    root = manifest.root
    files = dict(project_source_snapshot(root))
    before = project_revision(resolver, list(files.items()))
    _, tests = build_test_plan(resolver, discover=discover)
    if tests['workspace_revision'] != before['workspace_revision']:
        raise WysnaError('Workspace changed during edit planning', code='W5005', stage='patch')
    targets = [target.entry for target in manifest.targets.values()]
    cases = tests['cases']
    protected = set(targets) | {case['entry'] for case in cases}
    edits = _load_edits(resolver, root, files, request, protected)
    active_edits = [name for name, (action, _, _, _) in edits.items() if action != 'delete']
    entries = sorted(set([*targets, *active_edits, *[case['entry'] for case in cases]]))
    inputs = _check_overlay(resolver, entries, edits)
    modifications = []
    for name, (action, _, original, replacement) in sorted(edits.items()):
        previous = [] if original is None else original.decode('utf-8').splitlines(keepends=True)
        updated = [] if replacement is None else replacement.decode('utf-8').splitlines(keepends=True)
        fromfile = '/dev/null' if action == 'create' else 'a/' + name
        tofile = '/dev/null' if action == 'delete' else 'b/' + name
        diff = ''.join(difflib.unified_diff(previous, updated, fromfile=fromfile, tofile=tofile))
        before_sha = None if original is None else hashlib.sha256(original).hexdigest()
        after_sha = None if replacement is None else hashlib.sha256(replacement).hexdigest()
        if action == 'delete':
            files.pop(name, None)
        else:
            files[name] = after_sha
        modifications.append({
            'path': name,
            'action': action,
            'before_sha256': before_sha,
            'after_sha256': after_sha,
            'diff': diff,
        })
    impact = []
    for case in cases:
        causes = [name for name in sorted(edits) if str(root / name) in inputs[case['entry']]]
        impact.append({'id': case['id'], 'entry': case['entry'],
                       'impact_status': 'affected' if causes else 'uncertain', 'sources': causes})
    after = project_revision(resolver, sorted(files.items()))
    payload = {'schema_version': 'wysna.workspace-patch/1',
               'workspace_revision_before': before['workspace_revision'],
               'workspace_revision_after': after['workspace_revision'],
               'changes': modifications, 'checked': entries, 'tests': impact,
               'selected_test_ids': [case['id'] for case in cases]}
    plan_hash = _snapshot_digest(payload)

    def verify_revision():
        if project_revision(resolver)['workspace_revision'] != before['workspace_revision']:
            raise WysnaError('Workspace changed during edit planning', code='W5005', stage='patch')

    verify_revision()
    if expected_hash and plan_hash != expected_hash:
        raise WysnaError('Workspace edit plan is stale', code='W5005', stage='patch')
    if apply:
        write_workspace_operations(edits.values(), verify_revision)
    return {**payload, 'plan_hash': plan_hash, 'written': apply}
