"""Verify Selfhost multi-error recovery and cascade poison suppression across CLI and MCP."""
import json
from pathlib import Path
import subprocess

import pytest

from compiler.Bootstrap import ensure_selfhost_compiler_binary
from tests.unit.test_agent_protocol_schema import _assert_matches_protocol
from tests.unit.test_phase5_native_agent_autonomy import McpLineClient

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_DOC = json.loads((ROOT / 'schemas/wysna-tool-result.v1.schema.json').read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def stage2():
    return ensure_selfhost_compiler_binary(str(ROOT))


def test_selfhost_collects_multiple_independent_errors_in_one_pass(stage2, tmp_path):
    bad = tmp_path / 'multi_err.wysna'
    bad.write_text(
        'fn helper(a: int) -> int {\n'
        '    return a\n'
        '}\n'
        'fn first(x: int) -> int {\n'
        '    let n: int = "wrong"\n'
        '    let y = helper(1, 2)\n'
        '    let imm = 5\n'
        '    imm = 6\n'
        '    return "bad"\n'
        '}\n'
        'fn second() -> int {\n'
        '    let z = 1\n'
        '    let z = 2\n'
        '    let k = 1\n'
        '}\n',
        encoding='utf-8',
    )

    cli = subprocess.run(
        [stage2, 'check', str(bad), '--output-format=json-v1'],
        cwd=ROOT, capture_output=True, encoding='utf-8', timeout=60,
    )
    assert cli.returncode == 1, (cli.stdout, cli.stderr)
    payload = json.loads(cli.stdout)
    _assert_matches_protocol(payload, SCHEMA_DOC)
    assert payload['status'] == 'diagnostic_error'
    codes = [d['code'] for d in payload['diagnostics']]
    assert len(codes) >= 5, payload['diagnostics']
    for expected_code in ('W2001', 'W2005', 'W3001', 'W2004', 'W1002'):
        assert expected_code in codes, f'missing {expected_code} in {codes}'

    # Verify --diag-array emits the same multi-diagnostic batch.
    arr = subprocess.run(
        [stage2, 'check', str(bad), '--diag-array'],
        cwd=ROOT, capture_output=True, encoding='utf-8', timeout=60,
    )
    assert arr.returncode == 1
    arr_items = json.loads(arr.stdout)
    assert len(arr_items) == len(payload['diagnostics'])

    # Verify MCP wysna_check surfaces the full batch without dropping diagnostics.
    with subprocess.Popen(
        [stage2, 'mcp', '--root', str(tmp_path)],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as proc:
        try:
            client = McpLineClient(proc)
            assert 'serverInfo' in client.request('initialize', {})['result']
            _, mcp_payload = client.call_tool('wysna_check', {'path': 'multi_err.wysna'})
        finally:
            proc.kill()
            proc.wait(timeout=10)
    _assert_matches_protocol(mcp_payload, SCHEMA_DOC)
    assert len(mcp_payload['diagnostics']) == len(payload['diagnostics'])


def test_selfhost_suppresses_cascade_after_unknown_identifier(stage2, tmp_path):
    target = tmp_path / 'poison.wysna'
    target.write_text(
        'fn calc() -> int {\n'
        '    let v = missing_symbol + 1\n'
        '    return v\n'
        '}\n'
        'fn independent_err() -> int {\n'
        '    return "still_reported"\n'
        '}\n',
        encoding='utf-8',
    )
    res = subprocess.run(
        [stage2, 'check', str(target), '--output-format=json-v1'],
        cwd=ROOT, capture_output=True, encoding='utf-8', timeout=60,
    )
    assert res.returncode == 1, (res.stdout, res.stderr)
    payload = json.loads(res.stdout)
    _assert_matches_protocol(payload, SCHEMA_DOC)
    codes = [d['code'] for d in payload['diagnostics']]
    assert 'W1001' in codes
    assert 'W2004' in codes
    assert 'W2001' not in codes, f'unexpected cascade W2001 after W1001: {payload["diagnostics"]}'
