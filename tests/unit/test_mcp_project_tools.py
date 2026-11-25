"""Verify MCP wysna_project_check and opt-in wysna_project_test across Seed and Selfhost."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys

import pytest

from compiler.Bootstrap import ensure_selfhost_compiler_binary
from tests.unit.test_agent_project_tests import project
from tests.unit.test_phase5_native_agent_autonomy import McpLineClient
from tooling.McpServer import McpServer

ROOT = Path(__file__).resolve().parents[2]
SEED = [sys.executable, str(ROOT / 'main.py')]


@pytest.fixture(params=['seed', 'selfhost'])
def toolchain(request):
    if request.param == 'seed':
        return SEED
    return [ensure_selfhost_compiler_binary(str(ROOT))]


def _call_mcp(cli, root: Path, tool_name: str, arguments: dict, *, allow_exec: bool = False):
    if cli == SEED:
        server = McpServer(str(root), allow_exec=allow_exec)
        res = server.handle({
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': tool_name, 'arguments': arguments},
        })['result']
        return bool(res['isError']), json.loads(res['content'][0]['text'])
    cmd = cli + ['mcp', '--root', str(root)]
    if allow_exec:
        cmd.append('--allow-exec')
    with subprocess.Popen(
        cmd,
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as proc:
        try:
            client = McpLineClient(proc)
            assert 'serverInfo' in client.request('initialize', {})['result']
            return client.call_tool(tool_name, arguments)
        finally:
            proc.kill()
            proc.wait(timeout=10)


def test_mcp_project_check_and_list_work_without_exec(toolchain, tmp_path):
    root = project(tmp_path, 'print("mcp-ok")\n')
    failed, checked = _call_mcp(toolchain, root, 'wysna_project_check', {'manifest': 'wysna.toml'})
    assert not failed and checked['status'] == 'ok', checked

    failed, listed = _call_mcp(toolchain, root, 'wysna_project_test', {
        'manifest': 'wysna.toml',
        'list': True,
    })
    assert not failed and listed['status'] == 'ok', listed
    assert listed['result']['cases'] and listed['result']['plan_hash']

    # Execution without --allow-exec must be rejected with W5002 usage_error.
    failed, denied = _call_mcp(toolchain, root, 'wysna_project_test', {
        'manifest': 'wysna.toml',
        'list': False,
    }, allow_exec=False)
    assert not failed
    assert denied['status'] == 'usage_error'
    assert denied['diagnostics'][0]['code'] == 'W5002'

    # Confined root still blocks outside manifests.
    failed_outside, outside = _call_mcp(toolchain, root, 'wysna_project_check', {'manifest': '../outside/wysna.toml'})
    assert failed_outside and 'outside the server root' in outside['error']

    # Broken project surfaces diagnostic_error cleanly through wysna_project_check.
    (root / 'src/main.wysna').write_text('let bad: int = "oops"\n', encoding='utf-8')
    failed_bad, bad_res = _call_mcp(toolchain, root, 'wysna_project_check', {'manifest': 'wysna.toml'})
    assert not failed_bad
    assert bad_res['status'] == 'diagnostic_error'
    assert bad_res['diagnostics'][0]['code'] == 'W2001'


def test_seed_mcp_executes_project_test_with_allow_exec(tmp_path):
    root = project(tmp_path, 'print("mcp-exec-pass")\n')
    _, listed = _call_mcp(SEED, root, 'wysna_project_test', {'manifest': 'wysna.toml', 'list': True})
    plan_hash = listed['result']['plan_hash']

    failed, executed = _call_mcp(SEED, root, 'wysna_project_test', {
        'manifest': 'wysna.toml',
        'expect_plan_hash': plan_hash,
    }, allow_exec=True)
    assert not failed and executed['status'] == 'ok', executed
    assert executed['result']['passed'] == 1
    assert executed['result']['cases'][0]['stdout'].splitlines() == ['mcp-exec-pass']


def test_concurrent_selfhost_servers_keep_diagnostics_in_their_session(tmp_path):
    cli = [ensure_selfhost_compiler_binary(str(ROOT))]
    roots = []
    for index in range(6):
        root = tmp_path / str(index)
        root.mkdir()
        (root / 'main.wysna').write_text(f'let value_{index}: int = "wrong"\n', encoding='utf-8')
        roots.append(root)

    def check(root):
        failed, payload = _call_mcp(cli, root, 'wysna_check', {'path': 'main.wysna'})
        assert not failed, payload
        diagnostic, = payload['diagnostics']
        assert diagnostic['code'] == 'W2001'
        assert Path(diagnostic['primary_span']['source_id']).resolve() == (root / 'main.wysna').resolve()

    with ThreadPoolExecutor(max_workers=len(roots)) as executor:
        list(executor.map(check, roots))
