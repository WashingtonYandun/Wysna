"""Keep patch previews and test plans bound to the same checked source revision."""
import json

import pytest

from error.Error import WysnaError
from tests.unit.test_agent_project_tests import machine, project
from tests.unit.test_project_scaffold import run_cli
from tooling import SourceWrite
from tooling.WorkspacePatch import plan_workspace_patch


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


def test_workspace_patch_checks_and_applies_all_edits_together(tmp_path):
    root = project(tmp_path)
    manifest = root / 'wysna.toml'
    provider, app, request = _paired_edits(root)
    baseline = [file.read_bytes() for file in (provider, app)]
    with pytest.raises(WysnaError):
        plan_workspace_patch(manifest, request[:1])
    request[0]['source'] += 'pub fn compatible() -> int { 2 }\n'
    (root / 'tests/example.wysna').write_text(
        'import "provider.wysna" as source\nprint(source.compatible())\n', encoding='utf-8')
    edit_file = root / 'changes.json'
    edit_file.write_text(json.dumps(request), encoding='utf-8')
    args = ['patch', '--manifest', str(manifest), '--edits', str(edit_file), '--output-format=json-v1']
    previewed = run_cli(root, *args)
    assert previewed.returncode == 0, previewed.stderr + previewed.stdout
    preview = json.loads(previewed.stdout)['result']
    assert preview['selected_test_ids'] == ['test:example']
    assert preview['tests'][0]['impact_status'] == 'affected'
    assert [file.read_bytes() for file in (provider, app)] == baseline
    applied = run_cli(root, *args, '--expect-plan-hash', preview['plan_hash'], '--apply')
    assert applied.returncode == 0, applied.stderr + applied.stdout
    assert json.loads(applied.stdout)['result']['written']
    assert provider.read_text() == request[0]['source']
    assert app.read_text() == request[1]['source']
    listed, test_plan = machine(root, '--engine=seed', '--list')
    assert listed.returncode == 0, test_plan
    assert test_plan['result']['workspace_revision'] == preview['workspace_revision_after']
    executed, report = machine(root, '--engine=seed', '--expect-plan-hash', test_plan['result']['plan_hash'])
    assert executed.returncode == 0, report
    assert report['result']['cases'][0]['stdout'] == '2\n'


def test_stale_workspace_leaves_every_file_unmodified(tmp_path):
    root = project(tmp_path)
    manifest = root / 'wysna.toml'
    provider, app, request = _paired_edits(root)
    original = [file.read_bytes() for file in (provider, app)]
    preview = plan_workspace_patch(manifest, request)
    (root / 'tests/helper.wysna').write_text('fn helper() -> int { 2 }\n', encoding='utf-8')
    with pytest.raises(WysnaError) as failure:
        plan_workspace_patch(manifest, request, expected_hash=preview['plan_hash'], apply=True)
    assert failure.value.code == 'W5005'
    assert [file.read_bytes() for file in (provider, app)] == original


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
