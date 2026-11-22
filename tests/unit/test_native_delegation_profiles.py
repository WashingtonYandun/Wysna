"""Hybrid routing keeps Seed-owned target profiles out of the native driver."""
from pathlib import Path
from unittest.mock import patch

import pytest

from project.Scaffold import create_project
from tooling.NativeDelegation import _project_uses_wasm_profile, maybe_delegate_native


@pytest.mark.parametrize('arguments', [[], ['--target', 'app'], ['--target=app'],
                                       ['--manifest-dir'], ['--manifest-file'], ['--nested']])
def test_wasi_default_or_explicit_target_stays_on_seed(tmp_path, monkeypatch, arguments):
    root, _ = create_project('hybrid', tmp_path)
    manifest = root / 'wysna.toml'
    manifest.write_text(manifest.read_text().replace('profile = "native-cli"', 'profile = "wasi-command"') +
                        '\n[targets.native]\nentry = "src/main.wysna"\nprofile = "native-cli"\n')
    monkeypatch.chdir(root)
    if arguments == ['--manifest-dir']:
        arguments = ['--manifest=' + str(root)]
    elif arguments == ['--manifest-file']:
        arguments = ['--manifest', str(manifest)]
    elif arguments == ['--nested']:
        directory = root / 'src/nested'
        directory.mkdir()
        monkeypatch.chdir(directory)
        arguments = []
    for name in ('WYSNA_ENGINE', 'WYSNA_DELEGATE_NATIVE'):
        monkeypatch.delenv(name, raising=False)
    driver = tmp_path / 'native-driver'
    driver.write_text('exists')
    monkeypatch.setenv('WYSNA_NATIVE_DRIVER', str(driver))
    assert _project_uses_wasm_profile(['check', *arguments])
    with patch('tooling.NativeDelegation.os.execv', side_effect=AssertionError('WASI must stay on Seed')):
        assert maybe_delegate_native(['project', 'check', *arguments]) is None
    assert not _project_uses_wasm_profile(['build', '--target=native'])


def test_program_arguments_do_not_select_a_toolchain(tmp_path, monkeypatch):
    root, _ = create_project('hybrid', tmp_path)
    monkeypatch.chdir(root)
    assert not _project_uses_wasm_profile(['run', '--', '--target=web'])
