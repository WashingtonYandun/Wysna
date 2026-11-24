"""Protect graph queries and checked edits from ambiguous declaration names."""
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


@pytest.fixture
def program(tmp_path):
    (tmp_path / 'north').mkdir()
    (tmp_path / 'south').mkdir()
    north = tmp_path / 'north/score.wysna'
    south = tmp_path / 'south/score.wysna'
    north.write_text('pub fn score(value: int) -> int { return value + 1 }\n', encoding='utf-8')
    south.write_text('pub fn score(value: str) -> str { return value + "!" }\n', encoding='utf-8')
    entry = tmp_path / 'main.wysna'
    entry.write_text(
        'from "north/score.wysna" use score as north_score\n'
        'from "south/score.wysna" use score as south_score\n'
        'pub fn north_result() -> int { return north_score(2) }\n'
        'pub fn south_result() -> str { return south_score("ok") }\n',
        encoding='utf-8')
    return entry, north, south


def run(cli, *args):
    result = subprocess.run(cli + list(args) + ['--output-format=json-v1'],
                            cwd=ROOT, capture_output=True, encoding='utf-8', timeout=90)
    assert result.stdout.startswith('{'), (result.stdout, result.stderr)
    return result.returncode, json.loads(result.stdout)


def test_agent_queries_require_identity_and_use_verified_call_targets(toolchain, program):
    entry, north, south = program
    code, explanation = run(toolchain, 'explain', '--code', 'W1005')
    assert code == 0 and explanation['result']['code'] == 'W1005', explanation
    snapshots = {file: file.read_bytes() for file in program}
    selectors = []
    for command in ('graph', 'inspect', 'patch'):
        patch = ['--with', 'fn score(value: int) -> int { return value }', '--apply'] if command == 'patch' else []
        code, payload = run(toolchain, command, str(entry), '--symbol', 'score', *patch)
        assert code == 1, payload
        diagnostic = payload['diagnostics'][0]
        assert diagnostic['code'] == 'W1005' and diagnostic['stage'] == command
        assert diagnostic['primary_span'] is None
        data = diagnostic['data']
        assert data['kind'] == 'ambiguous_symbol' and data['symbol'] == 'score'
        assert data['graph_hash'].startswith('graph:')
        candidates = data['candidates']
        assert len(candidates) == 2
        assert len({candidate['id'] for candidate in candidates}) == 2
        assert {Path(candidate['file']) for candidate in candidates} == {north, south}
        assert candidates == sorted(candidates, key=lambda candidate: candidate['id'])
        selectors.append(candidates)
    assert selectors[0] == selectors[1] == selectors[2]

    by_file = {Path(candidate['file']): candidate for candidate in selectors[0]}
    for path, caller, expected_signature in [
        (north, 'fn:north_result', 'value: int'), (south, 'fn:south_result', 'value: str'),
    ]:
        selected = by_file[path]
        assert expected_signature in selected['signature']
        code, payload = run(toolchain, 'graph', str(entry), '--symbol', selected['id'], '--rel', 'callers')
        assert code == 0, payload
        assert [(edge['from'], edge['to']) for edge in payload['result']['edges']] == [(caller, selected['id'])]
        code, payload = run(toolchain, 'inspect', str(entry), '--symbol', selected['id'])
        assert code == 0 and expected_signature in payload['result']['source'], payload

    code, payload = run(toolchain, 'patch', str(entry), '--symbol', by_file[north]['id'],
                        '--with', 'fn score(value: int) -> int { return value }', '--apply')
    assert code != 0 and payload['diagnostics'][0]['code'] == 'W5002'
    assert {file: file.read_bytes() for file in program} == snapshots


def test_mcp_retains_symbol_candidates(toolchain, program):
    entry, _, _ = program
    arguments = {'path': entry.name, 'symbol': 'score'}
    if toolchain == SEED:
        payload = McpServer(str(entry.parent)).tool_wysna_graph(arguments)
    else:
        with subprocess.Popen(toolchain + ['mcp', '--root', str(entry.parent)],
                              cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE) as process:
            try:
                client = McpLineClient(process)
                error, payload = client.call_tool('wysna_graph', arguments)
                assert not error, payload
            finally:
                process.terminate()
    assert payload['status'] == 'diagnostic_error'
    assert payload['diagnostics'][0]['code'] == 'W1005'
    assert len(payload['diagnostics'][0]['data']['candidates']) == 2
