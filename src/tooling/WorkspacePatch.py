"""Check coordinated source edits against one project revision before applying."""
import difflib
import hashlib
from pathlib import PurePosixPath

from compiler.CompilerService import CompilerService
from error.Error import WysnaError
from project.Resolver import ProjectResolver
from project.Testing import _snapshot_digest, build_test_plan, project_revision, project_source_snapshot
from tooling.SourceWrite import write_verified_sources


def _load_edits(root, files, request):
    if not isinstance(request, list) or not request:
        raise ValueError('Workspace edits must be a nonempty array')
    edits = {}
    seen = set()
    for edit in request:
        if not isinstance(edit, dict) or set(edit) != {'path', 'source'}:
            raise ValueError('Each workspace edit requires path and source')
        name, source = edit['path'], edit['source']
        if not isinstance(name, str) or not isinstance(source, str):
            raise ValueError('Workspace edit path and source must be strings')
        relative = PurePosixPath(name)
        if (relative.is_absolute() or '..' in relative.parts or relative.as_posix() != name
                or name not in files or name in seen):
            raise ValueError(f'Workspace edit must identify one existing project source: {name!r}')
        seen.add(name)
        path = root / name
        if path.resolve() != path:
            raise ValueError(f'Workspace edit must use a direct project source path: {name!r}')
        before = path.read_bytes()
        if hashlib.sha256(before).hexdigest() != files[name]:
            raise WysnaError(f'Source changed during workspace planning: {name}', code='W5005', stage='patch')
        after = source.encode('utf-8')
        if before != after:
            edits[name] = (path, before, after)
    if not edits:
        raise ValueError('Workspace edit contains no changes')
    return edits


def _check_overlay(resolver, entries, edits):
    root = resolver.manifest.root
    inputs = {}
    for entry in entries:
        service = CompilerService(project_root=root, project_resolver=resolver)
        for path, _, after in edits.values():
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
    edits = _load_edits(root, files, request)
    _, tests = build_test_plan(resolver, discover=discover)
    if tests['workspace_revision'] != before['workspace_revision']:
        raise WysnaError('Workspace changed during edit planning', code='W5005', stage='patch')
    targets = [target.entry for target in manifest.targets.values()]
    cases = tests['cases']
    entries = sorted(set([*targets, *edits, *[case['entry'] for case in cases]]))
    inputs = _check_overlay(resolver, entries, edits)
    modifications = []
    for name, (_, original, replacement) in sorted(edits.items()):
        previous = original.decode('utf-8').splitlines(keepends=True)
        updated = replacement.decode('utf-8').splitlines(keepends=True)
        diff = ''.join(difflib.unified_diff(previous, updated, fromfile='a/' + name, tofile='b/' + name))
        files[name] = hashlib.sha256(replacement).hexdigest()
        modifications.append({'path': name, 'before_sha256': hashlib.sha256(original).hexdigest(),
                              'after_sha256': files[name], 'diff': diff})
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
        write_verified_sources(edits.values(), verify_revision)
    return {**payload, 'plan_hash': plan_hash, 'written': apply}
