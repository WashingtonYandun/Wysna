"""Keep patch previews and test plans bound to the same checked source revision."""
import pytest

from error.Error import WysnaError
from project.Resolver import ProjectResolver
from project.Testing import build_test_plan
from tests.unit.test_agent_project_tests import project
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
    preview = plan_workspace_patch(manifest, request)
    assert preview['selected_test_ids'] == ['test:example']
    assert preview['tests'][0]['impact_status'] == 'uncertain'
    assert [file.read_bytes() for file in (provider, app)] == baseline
    applied = plan_workspace_patch(manifest, request, expected_hash=preview['plan_hash'], apply=True)
    assert applied['written']
    assert provider.read_text() == request[0]['source']
    assert app.read_text() == request[1]['source']
    _, test_plan = build_test_plan(ProjectResolver.load(manifest))
    assert test_plan['workspace_revision'] == preview['workspace_revision_after']


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
