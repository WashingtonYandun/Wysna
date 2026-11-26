"""Verify bounded inspect context budgets, fetch handles, and call uncertainty boundaries across Seed, Selfhost, and MCP."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from compiler.Bootstrap import ensure_selfhost_compiler_binary
from tests.unit.test_phase5_native_agent_autonomy import McpLineClient
from tooling.McpServer import McpServer

ROOT = Path(__file__).resolve().parents[2]
SEED = [sys.executable, str(ROOT / 'main.py')]


@pytest.fixture(params=['seed', 'selfhost'])
def toolchain(request):
    if request.param == 'seed':
        return SEED
    return [ensure_selfhost_compiler_binary(str(ROOT))]


def _run_cli(cli, *args):
    res = subprocess.run(
        cli + list(args) + ['--output-format=json-v1'],
        cwd=ROOT, capture_output=True, encoding='utf-8', timeout=90,
    )
    assert res.stdout.startswith('{'), (res.stdout, res.stderr)
    return res.returncode, json.loads(res.stdout)


def _call_mcp(cli, root: Path, tool_name: str, arguments: dict):
    if cli == SEED:
        raw = McpServer(str(root)).handle({
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': tool_name, 'arguments': arguments},
        })['result']['content'][0]['text']
        return json.loads(raw)
    with subprocess.Popen(
        cli + ['mcp', '--root', str(root)],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as proc:
        try:
            client = McpLineClient(proc)
            assert 'serverInfo' in client.request('initialize', {})['result']
            _, payload = client.call_tool(tool_name, arguments)
            return payload
        finally:
            proc.kill()
            proc.wait(timeout=10)


def test_inspect_max_bytes_truncates_utf8_safely_and_emits_resumable_refs(toolchain, tmp_path):
    target = tmp_path / 'sample.wysna'
    target.write_text(
        'pub fn alpha() -> int { return 1 }\n'
        'pub fn beta() -> str { return "áéíóú — ok" }\n'
        'pub fn orchestrate() -> str {\n'
        '    let prefix = "áéíóú"\n'
        '    let n = alpha()\n'
        '    return prefix + ":" + beta() + ":" + str(n)\n'
        '}\n',
        encoding='utf-8',
    )

    code, full_res = _run_cli(toolchain, 'inspect', str(target), '--symbol', 'orchestrate', '--with-deps')
    assert code == 0, full_res
    full_source = full_res['result']['source']
    assert full_res['result']['budget']['truncated'] is False
    assert full_res['result']['omitted'] == []

    # Tight budget must truncate source safely on UTF-8 boundary and omit dependencies with fetch refs.
    code, part1 = _run_cli(toolchain, 'inspect', str(target), '--symbol', 'orchestrate', '--with-deps', '--max-bytes', '45')
    assert code == 0, part1
    r1 = part1['result']
    assert r1['budget']['max_bytes'] == 45
    assert r1['budget']['used_bytes'] <= 45
    assert r1['budget']['truncated'] is True
    assert r1['dependencies'] == []
    slice_refs = [item for item in r1['omitted'] if item['kind'] == 'source_slice']
    dep_refs = [item for item in r1['omitted'] if item['kind'] == 'dependency']
    assert len(slice_refs) == 1
    assert len(dep_refs) >= 2
    next_offset = slice_refs[0]['ref']['source_offset']

    # Follow source_slice ref using MCP to assemble remaining source bytes without splitting characters.
    part2_mcp = _call_mcp(toolchain, tmp_path, 'wysna_inspect', {
        'path': 'sample.wysna',
        'symbol': slice_refs[0]['ref']['symbol'],
        'source_offset': next_offset,
        'max_bytes': 200,
    })
    assert part2_mcp['status'] == 'ok', part2_mcp
    assert r1['source'] + part2_mcp['result']['source'] == full_source

    # Follow one omitted dependency ref.
    first_dep_ref = dep_refs[0]['ref']
    dep_payload = _call_mcp(toolchain, tmp_path, 'wysna_inspect', {
        'path': 'sample.wysna',
        'symbol': first_dep_ref['symbol'],
    })
    assert dep_payload['status'] == 'ok', dep_payload
    assert dep_payload['result']['source']

    # Index mode budget + offset pagination.
    code_full, idx_full = _run_cli(toolchain, 'inspect', str(target))
    assert code_full == 0, idx_full
    first_entry_bytes = len(
        json.dumps(idx_full['result']['symbols'][0], ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    )
    page1_budget = first_entry_bytes + 16
    page2_budget = idx_full['result']['budget']['used_bytes'] + 128
    code, idx1 = _run_cli(toolchain, 'inspect', str(target), '--max-bytes', str(page1_budget))
    assert code == 0, idx1
    assert idx1['result']['budget']['used_bytes'] <= page1_budget
    assert idx1['result']['budget']['truncated'] is True
    assert len(idx1['result']['symbols']) >= 1
    idx_ref = idx1['result']['omitted'][0]['ref']
    idx2 = _call_mcp(toolchain, tmp_path, 'wysna_inspect', {
        'path': 'sample.wysna',
        'offset': idx_ref['offset'],
        'max_bytes': page2_budget,
    })
    assert idx2['status'] == 'ok', idx2
    combined_ids = [s['id'] for s in idx1['result']['symbols']] + [s['id'] for s in idx2['result']['symbols']]
    assert len(combined_ids) == 3
    assert set(combined_ids) == {'fn:alpha', 'fn:beta', 'fn:orchestrate'}


def test_graph_and_inspect_distinguish_resolved_calls_from_uncertainty_boundaries(toolchain, tmp_path):
    target = tmp_path / 'calls.wysna'
    target.write_text(
        'extern "C" fn fabs(x: float) -> float\n'
        'interface Greeter { fn greet(name: str) -> str }\n'
        'class Casual implements Greeter {\n'
        '    pub fn greet(name: str) -> str { return "hi " + name }\n'
        '}\n'
        'fn helper(x: int) -> int { return x + 1 }\n'
        'pub fn direct_only(x: int) -> int { return helper(x) }\n'
        'pub fn via_interface(g: Greeter) -> str { return g.greet("w") }\n'
        'pub fn via_closure(op: fn(int) -> int, x: int) -> int { return op(x) }\n'
        'pub fn via_ffi(v: float) -> float { return fabs(v) }\n',
        encoding='utf-8',
    )

    code, graph_payload = _run_cli(toolchain, 'graph', str(target))
    assert code == 0, graph_payload
    gres = graph_payload['result']
    assert {'virtual-dispatch', 'indirect-call', 'c-ffi'} <= set(gres['analysis_boundaries'])

    by_id = {n['id']: n for n in gres['nodes']}
    assert by_id['fn:direct_only']['call_resolution']['complete'] is True
    assert by_id['fn:direct_only']['call_resolution']['resolved_calls'] == ['fn:helper']
    assert by_id['fn:direct_only']['call_resolution']['uncertain_calls'] == []

    assert by_id['fn:via_interface']['call_resolution']['complete'] is False
    assert any(u['kind'] == 'virtual_dispatch' for u in by_id['fn:via_interface']['call_resolution']['uncertain_calls'])

    assert by_id['fn:via_closure']['call_resolution']['complete'] is False
    assert any(u['kind'] == 'indirect_call' for u in by_id['fn:via_closure']['call_resolution']['uncertain_calls'])

    assert by_id['fn:via_ffi']['call_resolution']['complete'] is False
    assert any(u['kind'] == 'extern_ffi' for u in by_id['fn:via_ffi']['call_resolution']['uncertain_calls'])

    inspect_direct = _call_mcp(toolchain, tmp_path, 'wysna_inspect', {'path': 'calls.wysna', 'symbol': 'direct_only'})
    assert inspect_direct['result']['call_resolution']['complete'] is True
    assert inspect_direct['result']['analysis_boundaries'] == []

    inspect_iface = _call_mcp(toolchain, tmp_path, 'wysna_inspect', {'path': 'calls.wysna', 'symbol': 'via_interface'})
    assert inspect_iface['result']['call_resolution']['complete'] is False
    assert 'virtual-dispatch' in inspect_iface['result']['analysis_boundaries']


def test_inspect_exact_parity_and_batch_symbols(toolchain, tmp_path):
    target = tmp_path / 'batch.wysna'
    target.write_text(
        'pub fn one(x: int) -> int { return x + 1 }\n'
        'pub fn two(x: int) -> int { return one(x) * 2 }\n'
        'pub fn three(x: int) -> int { return two(x) - 1 }\n',
        encoding='utf-8',
    )

    code_idx, idx_payload = _run_cli(toolchain, 'inspect', str(target))
    assert code_idx == 0, idx_payload
    assert {'schema_version', 'file', 'graph_hash', 'summary', 'symbols', 'budget', 'omitted', 'analysis_boundaries'} <= set(idx_payload['result'])
    assert all('qualified_name' in s for s in idx_payload['result']['symbols'])

    code_one, one_payload = _run_cli(toolchain, 'inspect', str(target), '--symbol', 'two', '--with-deps')
    assert code_one == 0, one_payload
    res_one = one_payload['result']
    assert 'node' in res_one and 'symbol' not in res_one
    assert {'schema_version', 'file', 'graph_hash', 'node', 'source', 'dependencies', 'impact', 'call_resolution', 'budget', 'omitted', 'analysis_boundaries'} <= set(res_one)
    assert res_one['node']['name'] == 'two'
    assert res_one['impact']['impact_count'] >= 1

    code_batch, batch_payload = _run_cli(toolchain, 'inspect', str(target), '--symbols', 'one,three')
    assert code_batch == 0, batch_payload
    bres = batch_payload['result']
    assert bres['mode'] == 'batch'
    assert [it['node']['name'] for it in bres['items']] == ['one', 'three']
    assert bres['budget']['requested_count'] == 2
    assert bres['budget']['returned_count'] == 2

    mcp_batch = _call_mcp(toolchain, tmp_path, 'wysna_inspect', {
        'path': 'batch.wysna',
        'symbols': ['one', 'two', 'three'],
        'max_bytes': 45,
    })
    assert mcp_batch['status'] == 'ok', mcp_batch
    mb = mcp_batch['result']
    assert mb['mode'] == 'batch'
    assert mb['budget']['truncated'] is True
    assert len(mb['omitted']) >= 1

    # Validate strict InspectResult JSON Schema if jsonschema is installed.
    import importlib
    schema_doc = json.loads((ROOT / 'schemas/wysna-tool-result.v1.schema.json').read_text(encoding='utf-8'))
    try:
        js = importlib.import_module('jsonschema')
        for payload in (idx_payload, one_payload, batch_payload, mcp_batch):
            js.Draft202012Validator(schema_doc).validate(payload)
    except ImportError:
        pass


def test_inspect_diagnostic_span_reads_type_invalid_source_with_utf8_budget(toolchain, tmp_path):
    path = tmp_path / 'context.wysna'
    raw = ('# earlier á🦙\r\n'
           'fn combine(left: int, right: int) -> int { left + right }\r\n'
           'combine(1)\r\n').encode('utf-8')
    path.write_bytes(raw)
    code, checked = _run_cli(toolchain, 'check', str(path))
    assert code == 1, checked
    diagnostic = next(item for item in checked['diagnostics'] if item['code'] == 'W2005')
    anchor = diagnostic['related'][0]['span']
    code, inspected = _run_cli(toolchain, 'inspect', str(path), '--span', json.dumps(anchor), '--max-bytes', '48')
    assert code == 0, inspected
    result = inspected['result']
    assert result['schema_version'] == 'wysna.inspect-span/1' and result['span'] == anchor
    assert 'combine(left: int' in result['source']
    assert result['source'].encode() == raw[result['context_start_byte']:result['context_end_byte']]
    assert result['budget']['used_bytes'] <= 48

    reply = _call_mcp(toolchain, tmp_path, 'wysna_inspect', {'path': path.name, 'span': anchor, 'max_bytes': 48})
    assert reply['result'] == result

    middle = raw.index('🦙'.encode()) + 1
    invalid = dict(anchor, start_byte=middle, end_byte=middle)
    code, malformed = _run_cli(toolchain, 'inspect', str(path), '--span', json.dumps(invalid))
    assert code == 2 and malformed['diagnostics'][0]['code'] == 'W5002'

    path.write_bytes(raw + b'\r\n')
    code, stale = _run_cli(toolchain, 'inspect', str(path), '--span', json.dumps(anchor))
    assert code == 1 and stale['diagnostics'][0]['code'] == 'W5005'
