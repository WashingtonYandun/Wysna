"""Agent-first semantic graph, surgical inspect, checked patch, and versioned skills CLI."""
import argparse
import hashlib
import os
from pathlib import Path
import sys

from common.Version import WYSNA_VERSION
from compiler.CompilerService import CompilerService
from compiler.SemanticGraph import (
    build_semantic_graph,
    compute_blast_radius,
    find_graph_node,
    query_semantic_graph,
)
from error.Error import WysnaError
from tooling.SourceWrite import read_source_snapshot, write_verified_source
from tooling.ToolResult import diagnostic_from_error, diagnostic_record, tool_result, write_tool_result


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _init_service(source_path: Path, manifest: Path | None, frozen: bool):
    service = CompilerService()
    resolver = None
    if manifest is not None:
        from project.Resolver import ProjectResolver
        resolver = ProjectResolver.load(manifest)
        if frozen:
            resolver.verify_lock()
        if not source_path.resolve().is_relative_to(resolver.manifest.source_root.resolve()):
            raise ValueError('Source must belong to the project source root')
        service = CompilerService(project_root=resolver.manifest.root, project_resolver=resolver)
    elif frozen:
        raise ValueError('--frozen requires --manifest')
    return service, resolver


def _load_checked(source_path: Path, manifest: Path | None = None, frozen: bool = False):
    service, resolver = _init_service(source_path, manifest, frozen)
    try:
        checked = service.check_file(source_path)
    except UnicodeDecodeError as error:
        raise WysnaError(
            'Source is not valid UTF-8',
            file=str(source_path.resolve()),
            code='W1001',
            stage='lexer',
        ) from error
    return service, resolver, checked


def _handle_cli_error(command: str, error: Exception, service: CompilerService | None, json_output: bool) -> int:
    semantic = isinstance(error, WysnaError)
    filesystem = isinstance(error, OSError)
    code = 1 if semantic else 3 if filesystem else 2
    if json_output:
        if semantic:
            sources = service.source_texts() if service is not None else {}
            diagnostics = [
                diagnostic_from_error(item, sources)
                for item in getattr(error, 'diagnostics', (error,))
            ]
        else:
            diagnostics = [
                diagnostic_record(
                    'W5004' if filesystem else 'W5002',
                    'filesystem' if filesystem else command,
                    str(error),
                )
            ]
        status = 'diagnostic_error' if semantic else 'toolchain_error' if filesystem else 'usage_error'
        write_tool_result(tool_result(command, status, code, diagnostics=diagnostics), sys.stdout)
    else:
        print(str(error), file=sys.stderr)
    return code


def run_graph_command(argv) -> int:
    """Inspect compile-time relationships, interface implementations, calls, and blast radius."""
    parser = _Parser(prog='wysna graph', description=__doc__)
    parser.add_argument('source', nargs='?')
    parser.add_argument('--symbol', help='Filter graph around a symbol name or node ID')
    parser.add_argument(
        '--rel',
        choices=('implements', 'hierarchy', 'usages', 'callers', 'callees', 'impact'),
        help='Relationship projection around the target symbol or whole module',
    )
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--frozen', action='store_true')
    parser.add_argument('--no-effects', action='store_true', help='Skip MIR effect enrichment')
    parser.add_argument('--output-format', choices=('json-v1',))
    json_output = '--output-format=json-v1' in argv or '--output-format' in argv
    service = None
    try:
        options = parser.parse_args(list(argv))
        json_output = options.output_format == 'json-v1'
        if options.source is None:
            if options.manifest is None or not options.symbol or options.rel != 'impact':
                raise ValueError('Provide a source path or use --manifest PATH --symbol NAME --rel impact')
            from tooling.ProjectImpact import project_impact
            impact = project_impact(options.manifest, options.symbol, frozen=options.frozen)
            if json_output:
                envelope = tool_result('graph', 'ok', 0)
                envelope['result'] = impact
                write_tool_result(envelope, sys.stdout)
            else:
                for entry in impact['entries']:
                    status = entry['impact_status'].replace('-', ' ')
                    print(f"{entry['kind']} {entry['name']} ({entry['entry']}): {status}")
                    for path in entry['paths']:
                        chain = ''.join(f" --{edge['kind']}--> {edge['to']}" for edge in path['chain'])
                        print(f"  {path['entry_symbol']}{chain}")
                    if entry['unanalyzed_boundaries']:
                        print("  External boundaries: " + ', '.join(entry['unanalyzed_boundaries']))
            return 0
        service, resolver, checked = _load_checked(Path(options.source), options.manifest, options.frozen)
        payload = service.graph(
            checked,
            symbol=options.symbol,
            relation=options.rel,
            include_effects=not options.no_effects,
        )
        if resolver is not None:
            payload['project'] = resolver.lock_data()
        if options.symbol and payload.get('found') is False:
            raise WysnaError(
                f"Symbol '{options.symbol}' not found in semantic graph",
                file=str(Path(options.source).resolve()),
                code='W1001',
                stage='graph',
            )
        if json_output:
            envelope = tool_result('graph', 'ok', 0)
            envelope['result'] = payload
            write_tool_result(envelope, sys.stdout)
        else:
            print(f"Semantic Graph ({payload['schema_version']}) hash={payload['graph_hash']}")
            if 'node' in payload and payload['node'] is not None:
                node = payload['node']
                print(f"Symbol: {node['id']} ({node['file']}:{node['line']})")
                impact = payload.get('impact') or {}
                print(f"Blast radius: {impact.get('impact_count', 0)} transitive dependent(s)")
                for edge in payload.get('edges', ()):
                    ctx = f" [{edge['context']}]" if edge.get('context') else ''
                    print(f"  {edge['from']} --{edge['kind']}{ctx}--> {edge['to']}")
            else:
                summary = payload.get('summary', {})
                print(
                    f"Nodes: {summary.get('nodes', 0)} | Edges: {summary.get('edges', 0)} | "
                    f"Interfaces: {summary.get('interfaces', 0)} | Classes: {summary.get('classes', 0)} | "
                    f"Functions: {summary.get('functions', 0)}"
                )
                for edge in payload.get('edges', ()):
                    print(f"  {edge['from']} --{edge['kind']}--> {edge['to']}")
        return 0
    except (WysnaError, OSError, UnicodeError, ValueError) as error:
        return _handle_cli_error('graph', error, service, json_output)


def inspect_source(
    path: str,
    *,
    symbol: str | None = None,
    with_deps: bool = False,
    service: CompilerService | None = None,
    manifest: Path | None = None,
    frozen: bool = False,
) -> dict:
    """Return compact declaration index or surgical symbol slice + graph context."""
    source_path = Path(path)
    if service is None:
        service, resolver, checked = _load_checked(source_path, manifest, frozen)
    else:
        resolver = None
        checked = service.check_file(source_path)

    full_graph = service.graph(checked)
    if symbol is None:
        compact_symbols = [
            {
                'id': node['id'],
                'name': node['name'],
                'kind': node['kind'],
                'file': node['file'],
                'line': node['line'],
                'end_line': node['end_line'],
                'signature': node.get('signature', f"{node['kind']} {node['name']}"),
            }
            for node in full_graph['nodes']
            if node['kind'] != 'entrypoint'
        ]
        result = {
            'schema_version': 'wysna.inspect/1',
            'graph_hash': full_graph['graph_hash'],
            'summary': full_graph['summary'],
            'symbols': compact_symbols,
        }
        if resolver is not None:
            result['project'] = resolver.lock_data()
        return result

    node = find_graph_node(full_graph, symbol)
    if node is None:
        raise WysnaError(
            f"Unknown symbol '{symbol}'",
            file=str(source_path.resolve()),
            code='W1001',
            stage='inspect',
        )

    snap = checked.sources.get(node['file'])
    text = snap.text if snap is not None else ''
    raw_bytes = text.encode('utf-8')
    snippet = raw_bytes[node['start_byte']:node['end_byte']].decode('utf-8', errors='replace')
    blast = compute_blast_radius(full_graph, node['id'])
    nodes_by_id = {n['id']: n for n in full_graph['nodes']}

    result = {
        'schema_version': 'wysna.inspect/1',
        'graph_hash': full_graph['graph_hash'],
        'symbol': node,
        'source': snippet,
        'impact': blast,
    }
    if with_deps:
        dep_ids = []
        for field_name in ('implements', 'super_class', 'uses_types', 'calls', 'instantiates'):
            value = node.get(field_name)
            if isinstance(value, str) and value:
                dep_ids.append(value)
            elif isinstance(value, list):
                dep_ids.extend(value)
        deps = []
        seen_deps = set()
        for dep_id in dep_ids:
            if dep_id in seen_deps or dep_id not in nodes_by_id:
                continue
            seen_deps.add(dep_id)
            dep = nodes_by_id[dep_id]
            deps.append({
                'id': dep['id'],
                'name': dep['name'],
                'kind': dep['kind'],
                'signature': dep.get('signature', f"{dep['kind']} {dep['name']}"),
                'file': dep['file'],
                'line': dep['line'],
            })
        result['dependencies'] = deps
    if resolver is not None:
        result['project'] = resolver.lock_data()
    return result


def run_inspect_command(argv) -> int:
    """Extract a surgical, token-efficient symbol slice with its graph dependencies."""
    parser = _Parser(prog='wysna inspect', description=__doc__)
    parser.add_argument('source')
    parser.add_argument('--symbol', help='Target function, class, interface, struct, enum, or Class.method')
    parser.add_argument('--with-deps', action='store_true', help='Include signatures of referenced symbols')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--frozen', action='store_true')
    parser.add_argument('--output-format', choices=('json-v1',))
    json_output = '--output-format=json-v1' in argv or '--output-format' in argv
    try:
        options = parser.parse_args(list(argv))
        json_output = options.output_format == 'json-v1'
        payload = inspect_source(
            options.source,
            symbol=options.symbol,
            with_deps=options.with_deps,
            manifest=options.manifest,
            frozen=options.frozen,
        )
        if json_output:
            envelope = tool_result('inspect', 'ok', 0)
            envelope['result'] = payload
            write_tool_result(envelope, sys.stdout)
        else:
            if 'symbols' in payload:
                print(f"Inspect Index hash={payload['graph_hash']}")
                for sym in payload['symbols']:
                    print(f"  {sym['id']} ({sym['line']}-{sym['end_line']}): {sym['signature']}")
            else:
                sym = payload['symbol']
                print(f"{sym['id']} ({sym['file']}:{sym['line']}-{sym['end_line']}) hash={payload['graph_hash']}")
                print(payload['source'])
                print(f"# impact: {payload['impact']['impact_count']} dependent(s)")
        return 0
    except (WysnaError, OSError, UnicodeError, ValueError) as error:
        return _handle_cli_error('inspect', error, None, json_output)


def patch_source(
    path: str,
    symbol: str,
    replacement: str,
    *,
    expect_graph_hash: str | None = None,
    apply: bool = False,
    service: CompilerService | None = None,
    before_write_hook=None,
) -> dict:
    """Semantically replace one declaration in memory, check it, and optionally write to disk."""
    target_path, original_text, before_sha256 = read_source_snapshot(path)
    active_service = service or CompilerService()

    # Overlay ensures we inspect/check against exact caller text consistently.
    active_service.set_overlay(str(target_path), original_text)
    try:
        checked = active_service.check(original_text, str(target_path))
        before_graph = active_service.graph(checked)
    finally:
        active_service.remove_overlay(str(target_path))

    before_hash = before_graph['graph_hash']
    if expect_graph_hash and before_hash != expect_graph_hash:
        raise WysnaError(
            f"Stale graph hash: expected '{expect_graph_hash}', current is '{before_hash}'",
            file=str(target_path),
            code='W5005',
            stage='patch',
        )

    node = find_graph_node(before_graph, symbol)
    if node is None:
        raise WysnaError(
            f"Target symbol '{symbol}' not found for patch",
            file=str(target_path),
            code='W1001',
            stage='patch',
        )
    if os.path.realpath(node['file']) != os.path.realpath(str(target_path)):
        raise WysnaError(
            f"Symbol '{symbol}' is declared in '{node['file']}', not in '{target_path}'",
            file=str(target_path),
            code='W5002',
            stage='patch',
        )
    if not replacement.strip():
        raise ValueError('Replacement code must not be empty')

    raw_bytes = original_text.encode('utf-8')
    start_b, end_b = node['start_byte'], node['end_byte']
    clean_rep = replacement.rstrip('\n')
    patched_bytes = raw_bytes[:start_b] + clean_rep.encode('utf-8') + raw_bytes[end_b:]
    patched_text = patched_bytes.decode('utf-8')

    # Verify the complete patched file in memory before touching disk.
    verify_service = CompilerService(project_root=target_path.parent)
    verify_service.set_overlay(str(target_path), patched_text)
    try:
        after_checked = verify_service.check(patched_text, str(target_path))
        after_graph = verify_service.graph(after_checked)
    finally:
        verify_service.remove_overlay(str(target_path))

    changed = patched_text != original_text
    written = False
    if apply and changed:
        write_verified_source(
            target_path,
            patched_text,
            expected_sha256=before_sha256,
            stage='patch',
            before_lock_hook=before_write_hook,
        )
        written = True

    return {
        'schema_version': 'wysna.patch/1',
        'path': str(target_path),
        'symbol': node['id'],
        'graph_hash_before': before_hash,
        'graph_hash_after': after_graph['graph_hash'],
        'source_sha256_after': hashlib.sha256(patched_text.encode('utf-8')).hexdigest(),
        'changed': changed,
        'written': written,
        'patched_source': patched_text if not apply else None,
        'impact_after': compute_blast_radius(after_graph, node['id']),
    }


def run_patch_command(argv) -> int:
    """Apply a hash-verified, typechecked semantic declaration patch."""
    parser = _Parser(prog='wysna patch', description=__doc__)
    parser.add_argument('source')
    parser.add_argument('--symbol', required=True, help='Target declaration name or ID (e.g. draw or MyClass.draw)')
    parser.add_argument('--with', dest='replacement_inline', help='Replacement Wysna declaration text')
    parser.add_argument('--with-file', dest='replacement_file', type=Path, help='File containing replacement code')
    parser.add_argument('--expect-graph-hash', help='Require matching graph hash before patching')
    parser.add_argument('--apply', action='store_true', help='Write patched file after typecheck passes')
    parser.add_argument('--output-format', choices=('json-v1',))
    json_output = '--output-format=json-v1' in argv or '--output-format' in argv
    try:
        options = parser.parse_args(list(argv))
        json_output = options.output_format == 'json-v1'
        if (options.replacement_inline is None) == (options.replacement_file is None):
            raise ValueError('Provide exactly one of --with or --with-file')
        replacement = (
            options.replacement_file.read_bytes().decode('utf-8')
            if options.replacement_file is not None
            else options.replacement_inline
        )
        payload = patch_source(
            options.source,
            options.symbol,
            replacement,
            expect_graph_hash=options.expect_graph_hash,
            apply=options.apply,
        )
        if json_output:
            result_body = {k: v for k, v in payload.items() if v is not None}
            envelope = tool_result('patch', 'ok', 0)
            envelope['result'] = result_body
            write_tool_result(envelope, sys.stdout)
        else:
            verb = 'Applied patch' if payload['written'] else 'Verified patch (dry-run)'
            print(
                f"{verb} to {payload['symbol']}: "
                f"{payload['graph_hash_before']} -> {payload['graph_hash_after']}"
            )
        return 0
    except (WysnaError, OSError, UnicodeError, ValueError) as error:
        return _handle_cli_error('patch', error, None, json_output)


_SKILLS_TOPICS = {
    'overview': (
        f'# Wysna v{WYSNA_VERSION} — Agent-First Native Language & Toolchain\n'
        '- Default workflow uses checked static inspection before execution.\n'
        '- Memory model: Single-threaded ARC + Copy-on-Write (`Weak[C]` breaks strong cycles).\n'
        '- Error channel: Typed values `T ! E`, unwrap with `?` or `else err { fallback }`.\n'
        '- Core agent loop: `wysna skills` -> `wysna graph` -> `wysna inspect` -> '
        '`wysna patch` -> `wysna fix` -> `wysna check`.\n'
        '- Topics available: overview, agent-loop, syntax, graph, patch, contracts, stdlib.'
    ),
    'agent-loop': (
        '# Agent Coding Loop (Token-Efficient, Hash-Guarded)\n'
        '1. Discover relationships: `wysna graph <file.wysna> --output-format=json-v1`\n'
        '2. Inspect one symbol instead of reading whole files: '
        '`wysna inspect <file.wysna> --symbol <name> --with-deps --output-format=json-v1`\n'
        '3. Submit checked edits guarded by graph hash: '
        '`wysna patch <file.wysna> --symbol <name> --expect-graph-hash <hash> --with <code > --apply`\n'
        '4. Auto-repair mechanical diagnostics: `wysna fix --apply --output-format=json-v1 <file.wysna>`\n'
        '5. Decode any Wxxxx error: `wysna explain --code W2001 --output-format=json-v1`\n'
        '6. Verify & bound runtime: `wysna run --max-steps=100000 --max-heap-mb=64 <file.wysna>`'
    ),
    'syntax': (
        '# Wysna Canonical Syntax Quick Reference\n'
        '- Bindings: `let x = 1` (immutable), `let mut y = 2` (mutable).\n'
        '- Functions: `fn add(a: int, b: int) -> int { return a + b }`\n'
        '- Cooperative Async: `async fn fetch_id() -> int { ... }`, `let v = await task`.\n'
        '- Optionals & Errors: `T?`, `T ! E`, unwrap `?`, recover `expr else err { fallback }`.\n'
        '- OOP & Contracts: `interface Drawable { fn draw() -> str }`, '
        '`class Rect implements Drawable { fn draw() -> str { ... } }`.\n'
        '- Lambdas: `|n: int| => n * n` (capture outer variables by immutable snapshot).\n'
        '- Ranges & Loops: `for i in 0..10 { ... }`, inclusive `0..=10`.'
    ),
    'graph': (
        '# Semantic Relationship Graph (`wysna graph`)\n'
        '- `wysna graph <file> --output-format=json-v1`: full nodes, edges, and `graph_hash`.\n'
        '- `--symbol <name> --rel implements`: find all classes implementing an interface (or vice-versa).\n'
        '- `--symbol <name> --rel usages`: find every class, function, field, and param using the type.\n'
        '- `--symbol <name> --rel callers|callees`: traverse the resolved call graph.\n'
        '- `--symbol <name> --rel impact`: compute transitive blast radius before refactoring.'
    ),
    'patch': (
        '# Checked Semantic Patching (`wysna patch`)\n'
        '- Dry-run check in memory (default): validates syntax, types, and interface contracts before disk writes.\n'
        '- Optimistic locking: pass `--expect-graph-hash graph:...`; stale hashes fail with `[W5005]`.\n'
        '- Target classes, interfaces, structs, enums, functions, or `ClassName.method`.'
    ),
    'contracts': (
        '# Contracts, Schemas, Quotas & Trace Replay\n'
        '- Pre/postconditions: `fn f(x: int) -> int requires x > 0 ensures result >= 0 { ... }` (runtime `[W4005]`).\n'
        '- Tool Schema export: `wysna schema --emit-codecs --output-format=json-v1 <file.wysna>`.\n'
        '- Deterministic quotas: `--max-steps=<N>`, `--max-heap-mb=<N>` (fail-stop `[W4004]`).\n'
        '- Offline effect cassettes: `--record-trace <path>`, `--replay-trace <path>`.'
    ),
    'stdlib': (
        '# Standard Library (18 Modules)\n'
        '- Data & Text: `std/math`, `std/text`, `std/bytes`, `std/regex`, `std/collections`, `std/json`, `std/csv`, `std/parse`.\n'
        '- Systems & I/O: `std/fs`, `std/files`, `std/buffer`, `std/env`, `std/path`, `std/process`, `std/time`.\n'
        '- Network, Concurrency & Security: `std/http`, `std/async`, `std/algorithms` (`parallel_map`), `std/crypto`.'
    ),
}


def get_skill_topic(topic: str = 'overview') -> dict:
    """Return version-matched agent guidance for the requested skill topic."""
    if topic not in _SKILLS_TOPICS:
        available = ', '.join(sorted(_SKILLS_TOPICS))
        raise ValueError(f"Unknown skill topic '{topic}'. Available topics: {available}")
    return {
        'schema_version': 'wysna.skills/1',
        'compiler_version': WYSNA_VERSION,
        'topic': topic,
        'available_topics': sorted(_SKILLS_TOPICS),
        'content': _SKILLS_TOPICS[topic],
    }


def run_skills_command(argv) -> int:
    """Serve version-matched agent workflow guides directly from the compiler."""
    parser = _Parser(prog='wysna skills', description=__doc__)
    parser.add_argument('topic', nargs='?', default='overview', help='Skill topic (default: overview)')
    parser.add_argument('--output-format', choices=('json-v1',))
    json_output = '--output-format=json-v1' in argv or '--output-format' in argv
    try:
        options = parser.parse_args(list(argv))
        json_output = options.output_format == 'json-v1'
        payload = get_skill_topic(options.topic)
        if json_output:
            envelope = tool_result('skills', 'ok', 0)
            envelope['result'] = payload
            write_tool_result(envelope, sys.stdout)
        else:
            print(payload['content'])
        return 0
    except ValueError as error:
        return _handle_cli_error('skills', error, None, json_output)
