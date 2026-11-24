"""Keep patch previews and test plans bound to the same checked source revision."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

from compiler.Bootstrap import ensure_selfhost_compiler_binary
from error.Error import WysnaError
from tests.unit.test_agent_project_tests import project
from tests.unit.test_phase5_native_agent_autonomy import McpLineClient
from tooling import SourceWrite
from tooling.McpServer import McpServer
from tooling.WorkspacePatch import plan_workspace_patch

ROOT = Path(__file__).resolve().parents[2]
SEED = [sys.executable, str(ROOT / 'main.py')]


@pytest.fixture(params=['seed', 'selfhost'])
def toolchain(request):
    if request.param == 'seed':
        return SEED
    return [ensure_selfhost_compiler_binary(str(ROOT))]


def _run_cli(cli, *args):
    return subprocess.run(
        cli + list(args),
        cwd=ROOT, capture_output=True, encoding='utf-8', timeout=90,
    )


def _call_mcp(cli, root: Path, tool_name: str, arguments: dict):
    if cli == SEED:
        res = McpServer(str(root)).handle({
            'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
            'params': {'name': tool_name, 'arguments': arguments},
        })['result']
        return bool(res['isError']), json.loads(res['content'][0]['text'])
    with subprocess.Popen(
        cli + ['mcp', '--root', str(root)],
        cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ) as proc:
        try:
            client = McpLineClient(proc)
            assert 'serverInfo' in client.request('initialize', {})['result']
            return client.call_tool(tool_name, arguments)
        finally:
            proc.kill()
            proc.wait(timeout=10)


def _paired_edits(root):
    provider = root / 'src/provider.wysna'
    provider.write_text('pub fn value() -> int { 1 }\n', encoding='utf-8')
    application = root / 'src/main.wysna'
    application.write_text('import "provider.wysna" as source\nprint(source.value())\n', encoding='utf-8')
    request = [
        {'path': 'src/provider.wysna', 'source': 'pub fn value(v: int) -> int { v + 1 }\n'},
        {'path': 'src/main.wysna', 'source': 'import "provider.wysna" as source\nprint(source.value(1))\n'},
    ]
    return provider, application, request


def test_workspace_patch_checks_and_applies_all_edits_together(toolchain, tmp_path):
    root = project(tmp_path)
    manifest = root / 'wysna.toml'
    provider, app, request = _paired_edits(root)
    baseline = [file.read_bytes() for file in (provider, app)]

    bad_edit_file = root / 'bad_changes.json'
    bad_edit_file.write_text(json.dumps(request[:1]), encoding='utf-8')
    rejected = _run_cli(toolchain, 'patch', '--manifest', str(manifest), '--edits', str(bad_edit_file), '--output-format=json-v1')
    assert rejected.returncode != 0
    assert [file.read_bytes() for file in (provider, app)] == baseline

    request[0]['source'] += 'pub fn compatible() -> int { 2 }\n'
    (root / 'tests/example.wysna').write_text(
        'import "provider.wysna" as source\nprint(source.compatible())\n', encoding='utf-8')
    edit_file = root / 'changes.json'
    edit_file.write_text(json.dumps(request), encoding='utf-8')
    args = ['patch', '--manifest', str(manifest), '--edits', str(edit_file), '--output-format=json-v1']
    previewed = _run_cli(toolchain, *args)
    assert previewed.returncode == 0, previewed.stderr + previewed.stdout
    preview = json.loads(previewed.stdout)['result']
    assert preview['schema_version'] == 'wysna.workspace-patch/1'
    assert preview['selected_test_ids'] == ['test:example']
    assert preview['tests'][0]['impact_status'] == 'affected'
    assert preview['tests'][0]['sources'] == ['src/provider.wysna']
    assert preview['changes'] and all(c['diff'] for c in preview['changes'])
    assert [file.read_bytes() for file in (provider, app)] == baseline

    applied = _run_cli(toolchain, *args, '--expect-plan-hash', preview['plan_hash'], '--apply')
    assert applied.returncode == 0, applied.stderr + applied.stdout
    assert json.loads(applied.stdout)['result']['written'] is True
    assert provider.read_text(encoding='utf-8') == request[0]['source']
    assert app.read_text(encoding='utf-8') == request[1]['source']

    list_args = ['project', 'test', '--manifest', str(manifest), '--list', '--output-format=json-v1']
    if toolchain == SEED:
        list_args.append('--engine=seed')
    listed = _run_cli(toolchain, *list_args)
    assert listed.returncode == 0, listed.stderr + listed.stdout
    test_plan = json.loads(listed.stdout)
    assert test_plan['result']['workspace_revision'] == preview['workspace_revision_after']

    if toolchain == SEED:
        exec_cli = SEED
        exec_plan_hash = test_plan['result']['plan_hash']
    else:
        seed_listed = _run_cli(SEED, 'project', 'test', '--engine=seed', '--manifest', str(manifest), '--list', '--output-format=json-v1')
        assert seed_listed.returncode == 0, seed_listed.stderr + seed_listed.stdout
        seed_plan = json.loads(seed_listed.stdout)
        exec_cli = SEED
        exec_plan_hash = seed_plan['result']['plan_hash']

    executed = _run_cli(exec_cli, 'project', 'test', '--engine=seed', '--manifest', str(manifest),
                        '--expect-plan-hash', exec_plan_hash, '--output-format=json-v1')
    assert executed.returncode == 0, executed.stderr + executed.stdout
    report = json.loads(executed.stdout)
    assert report['result']['cases'][0]['stdout'].splitlines() == ['2']


def test_stale_workspace_leaves_every_file_unmodified(toolchain, tmp_path):
    root = project(tmp_path)
    manifest = root / 'wysna.toml'
    provider, app, request = _paired_edits(root)
    original = [file.read_bytes() for file in (provider, app)]
    edit_file = root / 'changes.json'
    edit_file.write_text(json.dumps(request), encoding='utf-8')

    previewed = _run_cli(toolchain, 'patch', '--manifest', str(manifest), '--edits', str(edit_file), '--output-format=json-v1')
    assert previewed.returncode == 0, previewed.stderr + previewed.stdout
    preview = json.loads(previewed.stdout)['result']

    (root / 'tests/helper.wysna').write_text('fn helper() -> int { 2 }\n', encoding='utf-8')
    stale = _run_cli(toolchain, 'patch', '--manifest', str(manifest), '--edits', str(edit_file),
                     '--expect-plan-hash', preview['plan_hash'], '--apply', '--output-format=json-v1')
    assert stale.returncode != 0
    stale_payload = json.loads(stale.stdout)
    assert stale_payload['diagnostics'][0]['code'] == 'W5005'
    assert [file.read_bytes() for file in (provider, app)] == original


def test_mcp_workspace_patch_previews_and_applies(toolchain, tmp_path):
    root = project(tmp_path)
    provider, app, request = _paired_edits(root)
    baseline = [file.read_bytes() for file in (provider, app)]

    failed, preview_env = _call_mcp(toolchain, root, 'wysna_patch', {
        'manifest': 'wysna.toml',
        'edits': request,
    })
    assert not failed and preview_env['status'] == 'ok', preview_env
    preview = preview_env['result']
    assert preview['written'] is False
    assert [file.read_bytes() for file in (provider, app)] == baseline

    failed, apply_env = _call_mcp(toolchain, root, 'wysna_patch', {
        'manifest': 'wysna.toml',
        'edits': request,
        'expect_plan_hash': preview['plan_hash'],
        'apply': True,
    })
    assert not failed and apply_env['status'] == 'ok', apply_env
    assert apply_env['result']['written'] is True
    assert provider.read_text(encoding='utf-8') == request[0]['source']
    assert app.read_text(encoding='utf-8') == request[1]['source']


def test_workspace_write_rolls_back_a_failed_later_replacement(tmp_path, monkeypatch):
    first = tmp_path / 'first.wysna'
    second = tmp_path / 'second.wysna'
    first.write_bytes(b'old first')
    second.write_bytes(b'old second')
    real_replace = SourceWrite.os.replace

    def fail_second(source, destination):
        if destination == second:
            raise OSError('simulated publication failure')
        real_replace(source, destination)

    monkeypatch.setattr(SourceWrite.os, 'replace', fail_second)
    with pytest.raises(OSError, match='simulated publication failure'):
        SourceWrite.write_verified_sources([(first, b'old first', b'new first'),
                                              (second, b'old second', b'new second')], lambda: None)
    assert first.read_bytes() == b'old first'
    assert second.read_bytes() == b'old second'


def test_workspace_patch_supports_symbol_hunks_create_and_delete(toolchain, tmp_path):
    root = project(tmp_path)
    manifest = root / 'wysna.toml'
    provider, app, _ = _paired_edits(root)
    unused = root / 'src/unused.wysna'
    unused.write_text('pub fn legacy() -> int { 0 }\n', encoding='utf-8')
    keep = root / 'src/keep.wysna'
    keep.write_text('pub fn bump(x: int) -> int { return x + 1 }\n', encoding='utf-8')

    # Deleting a file imported by main must fail verification before applying.
    app.write_text('import "unused.wysna" as u\nprint(u.legacy())\n', encoding='utf-8')
    bad_del = root / 'bad_del.json'
    bad_del.write_text(json.dumps([{'path': 'src/unused.wysna', 'delete': True}]), encoding='utf-8')
    rejected = _run_cli(toolchain, 'patch', '--manifest', str(manifest), '--edits', str(bad_del), '--output-format=json-v1')
    assert rejected.returncode != 0
    assert unused.exists()

    # Now restore app using symbol replacement, modify keep via hunks, create src/extra/new_mod.wysna, and delete unused.
    edits = [
        {'path': 'src/extra/new_mod.wysna', 'create': True, 'source': 'pub fn factor() -> int { return 10 }\n'},
        {'path': 'src/keep.wysna', 'hunks': [{'old_text': 'return x + 1', 'new_text': 'return x + 2'}]},
        {'path': 'src/unused.wysna', 'delete': True},
        {
            'path': 'src/main.wysna',
            'source': 'import "extra/new_mod.wysna" as extra\nimport "keep.wysna" as keep\nprint(keep.bump(extra.factor()))\n',
        },
    ]
    # Also test symbol edit in isolation before combined edit.
    sym_file = root / 'sym_edit.json'
    sym_file.write_text(json.dumps([
        {'path': 'src/provider.wysna', 'symbol': 'value', 'replacement': 'pub fn value() -> int { return 42 }'},
    ]), encoding='utf-8')
    sym_preview = _run_cli(toolchain, 'patch', '--manifest', str(manifest), '--edits', str(sym_file), '--output-format=json-v1')
    assert sym_preview.returncode == 0, sym_preview.stderr + sym_preview.stdout
    assert '+pub fn value() -> int { return 42 }' in json.loads(sym_preview.stdout)['result']['changes'][0]['diff']

    edit_file = root / 'wave2_edits.json'
    edit_file.write_text(json.dumps(edits), encoding='utf-8')
    previewed = _run_cli(toolchain, 'patch', '--manifest', str(manifest), '--edits', str(edit_file), '--output-format=json-v1')
    assert previewed.returncode == 0, previewed.stderr + previewed.stdout
    preview = json.loads(previewed.stdout)['result']
    actions = {c['path']: c['action'] for c in preview['changes']}
    assert actions == {
        'src/extra/new_mod.wysna': 'create',
        'src/keep.wysna': 'modify',
        'src/main.wysna': 'modify',
        'src/unused.wysna': 'delete',
    }
    assert not (root / 'src/extra/new_mod.wysna').exists()
    assert unused.exists()

    applied = _run_cli(
        toolchain, 'patch', '--manifest', str(manifest), '--edits', str(edit_file),
        '--expect-plan-hash', preview['plan_hash'], '--apply', '--output-format=json-v1',
    )
    assert applied.returncode == 0, applied.stderr + applied.stdout
    assert (root / 'src/extra/new_mod.wysna').exists()
    assert 'return x + 2' in keep.read_text(encoding='utf-8')
    assert not unused.exists()

    # Protected manifest entry cannot be deleted.
    prot_del = root / 'prot_del.json'
    prot_del.write_text(json.dumps([{'path': 'src/main.wysna', 'delete': True}]), encoding='utf-8')
    prot_res = _run_cli(toolchain, 'patch', '--manifest', str(manifest), '--edits', str(prot_del), '--output-format=json-v1')
    assert prot_res.returncode != 0

    # Hunk with multiple or zero matches is rejected.
    ambig_hunk = root / 'ambig_hunk.json'
    ambig_hunk.write_text(json.dumps([{'path': 'src/keep.wysna', 'hunks': [{'old_text': 'not_present', 'new_text': 'x'}]}]), encoding='utf-8')
    ambig_res = _run_cli(toolchain, 'patch', '--manifest', str(manifest), '--edits', str(ambig_hunk), '--output-format=json-v1')
    assert ambig_res.returncode != 0

    # MCP supports the new edit forms cleanly too.
    failed, mcp_prev = _call_mcp(toolchain, root, 'wysna_patch', {
        'manifest': 'wysna.toml',
        'edits': [{'path': 'src/keep.wysna', 'symbol': 'bump', 'replacement': 'pub fn bump(x: int) -> int { return x + 3 }'}],
    })
    assert not failed and mcp_prev['status'] == 'ok', mcp_prev


def test_workspace_operations_rolls_back_mixed_create_modify_delete(tmp_path, monkeypatch):
    keep = tmp_path / 'keep.wysna'
    remove_me = tmp_path / 'remove.wysna'
    create_me = tmp_path / 'sub/new.wysna'
    keep.write_bytes(b'old keep')
    remove_me.write_bytes(b'old remove')
    real_replace = SourceWrite.os.replace

    def fail_on_keep(source, destination):
        if destination == keep:
            raise OSError('simulated mixed failure')
        real_replace(source, destination)

    monkeypatch.setattr(SourceWrite.os, 'replace', fail_on_keep)
    with pytest.raises(OSError, match='simulated mixed failure'):
        SourceWrite.write_workspace_operations(
            [
                ('create', create_me, None, b'created'),
                ('delete', remove_me, b'old remove', None),
                ('modify', keep, b'old keep', b'new keep'),
            ],
            lambda: None,
        )
    assert keep.read_bytes() == b'old keep'
    assert remove_me.read_bytes() == b'old remove'
    assert not create_me.exists()
