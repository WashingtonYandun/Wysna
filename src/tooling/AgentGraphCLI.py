"""Agent-first semantic graph, surgical inspect, checked patch, and versioned skills CLI."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from common.Version import WYSNA_VERSION
from common.AgentContract import skill_topics
from compiler.CompilerService import CompilerService
from compiler.SemanticGraph import (
    analysis_boundaries_for_nodes,
    build_semantic_graph,
    compute_blast_radius,
    resolve_graph_symbol,
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
            sources = getattr(error, 'source_texts', None) or (service.source_texts() if service is not None else {})
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
    parser.add_argument('--role', help='List classes with this configured architecture role')
    parser.add_argument('--target', help='Select the project target for --role')
    parser.add_argument('--no-effects', action='store_true', help='Skip MIR effect enrichment')
    parser.add_argument('--output-format', choices=('json-v1',))
    json_output = '--output-format=json-v1' in argv or '--output-format' in argv
    service = None
    try:
        options = parser.parse_args(list(argv))
        json_output = options.output_format == 'json-v1'
        if options.role is not None:
            from project.Architecture import select_role
            from project.Resolver import ProjectResolver

            if not options.manifest or options.symbol or options.rel or (options.source and options.target):
                raise ValueError('--role requires --manifest and accepts one source or --target')
            resolver = ProjectResolver.load(options.manifest)
            manifest = resolver.manifest
            if options.source:
                source = Path(options.source)
            else:
                default_target = next(iter(manifest.targets)) if len(manifest.targets) == 1 else 'app'
                target = manifest.targets.get(options.target or default_target)
                if target is None:
                    raise ValueError('Unknown project target')
                source = manifest.root / target.entry
            service, _, checked = _load_checked(source, options.manifest, options.frozen)
            graph = service.graph(checked, include_effects=False)
            payload = {
                'schema_version': 'wysna.graph/1', 'mode': 'role', 'entry_file': graph['entry_file'],
                'graph_hash': graph['graph_hash'], 'role': options.role,
                'nodes': select_role(manifest, graph, options.role),
            }
            if json_output:
                envelope = tool_result('graph', 'ok', 0)
                envelope['result'] = payload
                write_tool_result(envelope, sys.stdout)
            else:
                for node in payload['nodes']:
                    print(f"{node['qualified_name']} ({node['file']}:{node['line']})")
            return 0
        if options.target:
            raise ValueError('--target requires --role')
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


def _utf8_slice_budget(text: str, start_byte: int, max_bytes: int | None) -> tuple[str, int, int, int]:
    total_bytes = len(text.encode('utf-8'))
    byte_pos = 0
    char_start = len(text)
    clamped_start = total_bytes
    for idx, ch in enumerate(text):
        if byte_pos >= start_byte:
            char_start = idx
            clamped_start = byte_pos
            break
        byte_pos += len(ch.encode('utf-8'))
    if max_bytes is None:
        sliced = text[char_start:]
        return sliced, clamped_start, total_bytes - clamped_start, total_bytes
    used = 0
    char_end = char_start
    for idx in range(char_start, len(text)):
        width = len(text[idx].encode('utf-8'))
        if used + width > max_bytes:
            break
        used += width
        char_end = idx + 1
    return text[char_start:char_end], clamped_start, used, total_bytes


def _validate_non_negative_int(value: int, flag: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f'{flag} must be a non-negative integer')
    return value


def _validate_positive_int(value: int | None, flag: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f'{flag} must be a positive integer')
    return value


def _inspect_single_node(
    checked,
    full_graph: dict,
    node: dict,
    *,
    with_deps: bool,
    max_bytes: int | None,
    source_offset: int,
    nodes_by_id: dict,
) -> tuple[dict, list[dict], int]:
    snap = checked.sources.get(node['file'])
    text = snap.text if snap is not None else ''
    raw_bytes = text.encode('utf-8')
    full_snippet = raw_bytes[node['start_byte']:node['end_byte']].decode('utf-8', errors='replace')
    snippet, clamped_start, source_used, source_total = _utf8_slice_budget(full_snippet, source_offset, max_bytes)
    next_source_offset = clamped_start + source_used
    used_bytes = source_used
    omitted = []
    if next_source_offset < source_total:
        omitted.append({
            'kind': 'source_slice',
            'symbol_id': node['id'],
            'file': node['file'],
            'start_byte': node['start_byte'] + next_source_offset,
            'end_byte': node['end_byte'],
            'omitted_bytes': source_total - next_source_offset,
            'ref': {
                'command': 'inspect',
                'symbol': node['id'],
                'source_offset': next_source_offset,
            },
        })

    blast = compute_blast_radius(full_graph, node['id'])
    included_nodes = [node]
    call_resolution = node.get('call_resolution') or {
        'resolved_calls': list(node.get('calls', [])),
        'uncertain_calls': [],
        'complete': not bool(node.get('indirect_call', False)),
    }

    deps = []
    if with_deps:
        dep_ids = []
        for field_name in ('implements', 'super_class', 'uses_types', 'calls', 'instantiates'):
            value = node.get(field_name)
            if isinstance(value, str) and value:
                dep_ids.append(value)
            elif isinstance(value, list):
                dep_ids.extend(value)
        for dep_id in sorted(set(dep_ids)):
            if dep_id not in nodes_by_id or dep_id == node['id']:
                continue
            dep = nodes_by_id[dep_id]
            dep_entry = {
                'id': dep['id'],
                'name': dep['name'],
                'kind': dep['kind'],
                'signature': dep.get('signature', f"{dep['kind']} {dep['name']}"),
                'file': dep['file'],
                'line': dep['line'],
            }
            dep_bytes = len(json.dumps(dep_entry, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
            if max_bytes is not None and used_bytes + dep_bytes > max_bytes:
                omitted.append({
                    'kind': 'dependency',
                    'symbol_id': dep['id'],
                    'name': dep['name'],
                    'signature': dep_entry['signature'],
                    'file': dep['file'],
                    'line': dep['line'],
                    'estimated_bytes': dep_bytes,
                    'ref': {
                        'command': 'inspect',
                        'symbol': dep['id'],
                    },
                })
                continue
            deps.append(dep_entry)
            included_nodes.append(dep)
            used_bytes += dep_bytes

    payload = {
        'node': node,
        'source': snippet,
        'dependencies': deps,
        'impact': blast,
        'call_resolution': call_resolution,
        'budget': {
            'max_bytes': max_bytes,
            'used_bytes': used_bytes,
            'source_offset': clamped_start,
            'source_total_bytes': source_total,
            'truncated': bool(omitted),
            'omitted_count': len(omitted),
        },
        'omitted': omitted,
    }
    return payload, included_nodes, used_bytes


def inspect_source(
    path: str,
    *,
    symbol: str | None = None,
    symbols: list[str] | None = None,
    with_deps: bool = False,
    max_bytes: int | None = None,
    source_offset: int = 0,
    offset: int = 0,
    service: CompilerService | None = None,
    manifest: Path | None = None,
    frozen: bool = False,
) -> dict:
    """Return compact declaration index, single symbol slice, or bounded batch of symbols."""
    max_bytes = _validate_positive_int(max_bytes, '--max-bytes')
    source_offset = _validate_non_negative_int(source_offset, '--source-offset')
    offset = _validate_non_negative_int(offset, '--offset')

    if symbols is not None:
        if symbol is not None:
            raise WysnaError('inspect cannot combine --symbol and --symbols', code='W5002', stage='usage')
        if not isinstance(symbols, list) or not symbols or any(not isinstance(s, str) or not s.strip() for s in symbols):
            raise WysnaError('inspect --symbols must be a non-empty list of symbol names', code='W5002', stage='usage')

    source_path = Path(path)
    if service is None:
        service, resolver, checked = _load_checked(source_path, manifest, frozen)
    else:
        resolver = None
        checked = service.check_file(source_path)

    full_graph = service.graph(checked)
    nodes_by_id = {n['id']: n for n in full_graph['nodes']}
    if symbol is None and symbols is None:
        all_symbols = [
            {
                'id': node['id'],
                'name': node['name'],
                'qualified_name': node.get('qualified_name', node['name']),
                'kind': node['kind'],
                'file': node['file'],
                'line': node['line'],
                'end_line': node['end_line'],
                'signature': node.get('signature', f"{node['kind']} {node['name']}"),
            }
            for node in full_graph['nodes']
            if node['kind'] != 'entrypoint'
        ]
        clamped_offset = min(offset, len(all_symbols))
        selected = []
        used_bytes = 0
        next_idx = clamped_offset
        truncated = False
        for idx in range(clamped_offset, len(all_symbols)):
            entry = all_symbols[idx]
            entry_bytes = len(json.dumps(entry, ensure_ascii=False, separators=(',', ':')).encode('utf-8'))
            if max_bytes is not None and used_bytes + entry_bytes > max_bytes:
                truncated = True
                break
            selected.append(entry)
            used_bytes += entry_bytes
            next_idx = idx + 1
        remaining = len(all_symbols) - next_idx
        omitted = []
        if truncated and remaining > 0:
            omitted.append({
                'kind': 'symbol_index',
                'omitted_count': remaining,
                'next_offset': next_idx,
                'ref': {'command': 'inspect', 'offset': next_idx},
            })
        result = {
            'schema_version': 'wysna.inspect/1',
            'file': str(source_path.resolve()),
            'graph_hash': full_graph['graph_hash'],
            'summary': full_graph['summary'],
            'symbols': selected,
            'budget': {
                'max_bytes': max_bytes,
                'used_bytes': used_bytes,
                'offset': clamped_offset,
                'total_symbols': len(all_symbols),
                'truncated': bool(omitted),
                'omitted_count': remaining if omitted else 0,
            },
            'omitted': omitted,
            'analysis_boundaries': full_graph.get('analysis_boundaries', []),
        }
        if resolver is not None:
            result['project'] = resolver.lock_data()
        return result

    if symbols is not None:
        resolved_nodes = []
        for req_sym in symbols:
            target_node = resolve_graph_symbol(full_graph, req_sym, 'inspect')
            if target_node is None:
                raise WysnaError(
                    f"Unknown symbol '{req_sym}'",
                    file=str(source_path.resolve()),
                    code='W1001',
                    stage='inspect',
                )
            resolved_nodes.append(target_node)

        items = []
        omitted = []
        included_nodes = []
        used_bytes = 0
        for idx, target_node in enumerate(resolved_nodes):
            remaining_budget = None if max_bytes is None else max(0, max_bytes - used_bytes)
            if remaining_budget is not None and remaining_budget == 0:
                for rem_node in resolved_nodes[idx:]:
                    omitted.append({
                        'kind': 'symbol',
                        'symbol_id': rem_node['id'],
                        'name': rem_node['name'],
                        'ref': {'command': 'inspect', 'symbol': rem_node['id']},
                    })
                break
            item_payload, item_nodes, item_used = _inspect_single_node(
                checked,
                full_graph,
                target_node,
                with_deps=with_deps,
                max_bytes=remaining_budget,
                source_offset=source_offset if idx == 0 else 0,
                nodes_by_id=nodes_by_id,
            )
            used_bytes += item_used
            included_nodes.extend(item_nodes)
            items.append(item_payload)
            if item_payload['omitted'] and max_bytes is not None and used_bytes >= max_bytes:
                for rem_node in resolved_nodes[idx + 1:]:
                    omitted.append({
                        'kind': 'symbol',
                        'symbol_id': rem_node['id'],
                        'name': rem_node['name'],
                        'ref': {'command': 'inspect', 'symbol': rem_node['id']},
                    })
                break

        result = {
            'schema_version': 'wysna.inspect/1',
            'mode': 'batch',
            'file': str(source_path.resolve()),
            'graph_hash': full_graph['graph_hash'],
            'items': items,
            'budget': {
                'max_bytes': max_bytes,
                'used_bytes': used_bytes,
                'requested_count': len(resolved_nodes),
                'returned_count': len(items),
                'truncated': bool(omitted) or any(bool(item['omitted']) for item in items),
                'omitted_count': len(omitted),
            },
            'omitted': omitted,
            'analysis_boundaries': analysis_boundaries_for_nodes(included_nodes),
        }
        if resolver is not None:
            result['project'] = resolver.lock_data()
        return result

    node = resolve_graph_symbol(full_graph, symbol, 'inspect')
    if node is None:
        raise WysnaError(
            f"Unknown symbol '{symbol}'",
            file=str(source_path.resolve()),
            code='W1001',
            stage='inspect',
        )

    item_payload, included_nodes, _used = _inspect_single_node(
        checked,
        full_graph,
        node,
        with_deps=with_deps,
        max_bytes=max_bytes,
        source_offset=source_offset,
        nodes_by_id=nodes_by_id,
    )
    result = {
        'schema_version': 'wysna.inspect/1',
        'file': node['file'],
        'graph_hash': full_graph['graph_hash'],
        **item_payload,
        'analysis_boundaries': analysis_boundaries_for_nodes(included_nodes),
    }
    if resolver is not None:
        result['project'] = resolver.lock_data()
    return result


def run_inspect_command(argv) -> int:
    """Extract a surgical, token-efficient symbol slice with its graph dependencies."""
    parser = _Parser(prog='wysna inspect', description=__doc__)
    parser.add_argument('source')
    parser.add_argument('--symbol', action='append', help='Target function, class, interface, struct, enum, or Class.method (repeatable)')
    parser.add_argument('--symbols', help='Comma-separated symbol list for batch inspect')
    parser.add_argument('--with-deps', action='store_true', help='Include signatures of referenced symbols')
    parser.add_argument('--max-bytes', type=int, help='Maximum UTF-8 byte budget for returned source/context')
    parser.add_argument('--source-offset', type=int, default=0, help='UTF-8 byte offset within target symbol source')
    parser.add_argument('--offset', type=int, default=0, help='Starting index offset when listing symbols')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--frozen', action='store_true')
    parser.add_argument('--output-format', choices=('json-v1',))
    json_output = '--output-format=json-v1' in argv or '--output-format' in argv
    try:
        options = parser.parse_args(list(argv))
        json_output = options.output_format == 'json-v1'
        sym_list = list(options.symbol or [])
        if options.symbols is not None:
            if sym_list:
                raise WysnaError('inspect cannot combine --symbol and --symbols', code='W5002', stage='usage')
            parsed_csv = [item.strip() for item in options.symbols.split(',') if item.strip()]
            if not parsed_csv:
                raise WysnaError('inspect --symbols must list at least one symbol', code='W5002', stage='usage')
            single_symbol = None
            batch_symbols = parsed_csv
        elif len(sym_list) > 1:
            single_symbol = None
            batch_symbols = sym_list
        elif len(sym_list) == 1:
            single_symbol = sym_list[0]
            batch_symbols = None
        else:
            single_symbol = None
            batch_symbols = None
        payload = inspect_source(
            options.source,
            symbol=single_symbol,
            symbols=batch_symbols,
            with_deps=options.with_deps,
            max_bytes=options.max_bytes,
            source_offset=options.source_offset,
            offset=options.offset,
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
            elif 'items' in payload:
                print(f"Inspect Batch ({len(payload['items'])}) hash={payload['graph_hash']}")
                for item in payload['items']:
                    sym = item['node']
                    print(f"{sym['id']} ({sym['file']}:{sym['line']}-{sym['end_line']})")
                    print(item['source'])
            else:
                sym = payload['node']
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

    node = resolve_graph_symbol(before_graph, symbol, 'patch')
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
    if node['start_byte'] >= node['end_byte']:
        raise WysnaError('Patch target has no editable declaration range',
                         file=str(target_path), code='W5002', stage='patch')
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
    parser.add_argument('source', nargs='?')
    parser.add_argument('--symbol', help='Target declaration name or ID (e.g. draw or MyClass.draw)')
    parser.add_argument('--with', dest='replacement_inline', help='Replacement Wysna declaration text')
    parser.add_argument('--with-file', dest='replacement_file', type=Path, help='File containing replacement code')
    parser.add_argument('--expect-graph-hash', help='Require matching graph hash before patching')
    parser.add_argument('--apply', action='store_true', help='Write patched file after typecheck passes')
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--edits', type=Path, help='JSON array of workspace sources to replace together')
    parser.add_argument('--expect-plan-hash', help='Require an exact inspected workspace edit plan')
    parser.add_argument('--discover', action='store_true')
    parser.add_argument('--frozen', action='store_true')
    parser.add_argument('--output-format', choices=('json-v1',))
    json_output = '--output-format=json-v1' in argv or '--output-format' in argv
    try:
        options = parser.parse_args(list(argv))
        json_output = options.output_format == 'json-v1'
        if options.edits is not None:
            if options.manifest is None or any((options.source, options.symbol, options.replacement_inline,
                                                options.replacement_file, options.expect_graph_hash)):
                raise ValueError('Workspace patch requires --manifest and --edits, without single-source options')
            from tooling.WorkspacePatch import plan_workspace_patch
            edits = json.loads(options.edits.read_text(encoding='utf-8'))
            result = plan_workspace_patch(options.manifest, edits, expected_hash=options.expect_plan_hash,
                                          apply=options.apply, discover=options.discover, frozen=options.frozen)
            if json_output:
                envelope = tool_result('patch', 'ok', 0)
                envelope['result'] = result
                write_tool_result(envelope, sys.stdout)
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if not options.source or not options.symbol or any((options.manifest, options.expect_plan_hash,
                                                             options.discover, options.frozen)):
            raise ValueError('Single-source patch requires SOURCE and --symbol')
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


_SKILLS_TOPICS = skill_topics()

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
