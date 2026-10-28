"""New projects must work from an installed-style CLI without overwriting data."""

import hashlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from project.Manifest import ManifestIOError
from project.Resolver import ProjectResolver
from project.Scaffold import ScaffoldError, create_project
from tooling.ProjectCLI import run_project_command

ROOT = Path(__file__).resolve().parents[2]


def run_cli(cwd, *args):
    return subprocess.run([sys.executable, str(ROOT / "main.py"), *args], cwd=cwd,
                          capture_output=True, text=True, timeout=60)


def test_new_project_check_run_lock_and_test(tmp_path):
    created = run_cli(tmp_path, "project", "new", "example-tool")
    assert created.returncode == 0, created.stderr
    project = tmp_path / "example-tool"
    assert (project / "wysna.toml").is_file()
    assert (project / "src/main.wysna").is_file()
    assert (project / "tests/example.wysna").is_file()
    checked = run_cli(project, "project", "check")
    executed = run_cli(project, "project", "run")
    locked = run_cli(project, "project", "lock")
    tested = run_cli(project, "project", "test", "--frozen")
    assert checked.returncode == 0, checked.stderr
    assert executed.returncode == 0 and executed.stdout == "Hello, Wysna!\n", executed.stderr
    assert locked.returncode == 0, locked.stderr
    assert tested.returncode == 0 and "[PASSED] example" in tested.stdout, (tested.stdout, tested.stderr)


def test_new_project_json_lists_exact_artifacts_and_capability(tmp_path):
    result = run_cli(tmp_path, "project", "new", "machine", "--output-format=json-v1")
    payload = json.loads(result.stdout)
    assert result.returncode == 0 and result.stderr == ""
    assert payload["schema_version"] == "wysna.tool-result/1"
    assert payload["command"] == "project.new" and payload["status"] == "ok"
    assert len(payload["artifacts"]) == 3
    for artifact in payload["artifacts"]:
        path = Path(artifact["path"])
        assert path.is_file() and (tmp_path / "machine") in path.parents
        assert artifact["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    capabilities = json.loads(run_cli(tmp_path, "capabilities", "--output-format=json-v1").stdout)
    assert "project.new" in capabilities["result"]["commands"]
    assert "project.new" in capabilities["result"]["machine_output"]["commands"]


@pytest.mark.parametrize("name", ["../escape", "bad/name", "UPPER", "-bad", "bad_name"])
def test_invalid_names_are_rejected_without_writes(tmp_path, name):
    result = run_cli(tmp_path, "project", "new", name, "--output-format=json-v1")
    payload = json.loads(result.stdout)
    assert result.returncode == 2 and payload["status"] == "usage_error"
    assert list(tmp_path.iterdir()) == []


def test_existing_destination_is_preserved(tmp_path):
    existing = tmp_path / "example"
    existing.mkdir()
    sentinel = existing / "keep.txt"
    sentinel.write_text("user data", encoding="utf-8")
    result = run_cli(tmp_path, "project", "new", "example")
    assert result.returncode == 2
    assert "already exists" in result.stderr
    assert sentinel.read_text(encoding="utf-8") == "user data"
    assert list(existing.iterdir()) == [sentinel]
    machine = run_cli(tmp_path, "project", "new", "example", "--output-format=json-v1")
    payload = json.loads(machine.stdout)
    assert machine.returncode == 2 and machine.stderr == ""
    assert payload["status"] == "usage_error" and payload["artifacts"] == []
    assert sentinel.read_text(encoding="utf-8") == "user data"


def test_existing_file_and_symlink_are_preserved(tmp_path):
    existing = tmp_path / "existing"
    existing.write_text("user data", encoding="utf-8")
    result = run_cli(tmp_path, "project", "new", "existing")
    assert result.returncode == 2 and existing.read_text(encoding="utf-8") == "user data"

    link = tmp_path / "linked"
    try:
        link.symlink_to(tmp_path / "missing", target_is_directory=True)
    except OSError:
        pytest.skip("creating directory symlinks is unavailable on this host")
    result = run_cli(tmp_path, "project", "new", "linked")
    assert result.returncode == 2 and link.is_symlink()
    assert not (tmp_path / "missing").exists()


def test_filesystem_failure_returns_machine_readable_error(monkeypatch, capsys):
    def fail(name, parent):
        raise ScaffoldError("disk is unavailable")

    monkeypatch.setattr("tooling.ProjectCLI.create_project", fail)
    result = run_project_command(["new", "example", "--output-format", "json-v1"])
    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert result == 3 and output.err == ""
    assert payload["command"] == "project.new" and payload["status"] == "toolchain_error"
    assert payload["diagnostics"][0]["code"] == "W5004"


@pytest.mark.parametrize('wrapped', [False, True])
def test_project_filesystem_failures_are_not_usage_errors(monkeypatch, capsys, wrapped):
    def denied(_):
        if wrapped:
            try:
                raise PermissionError('denied access')
            except OSError as cause:
                raise ManifestIOError('cannot read manifest') from cause
        raise PermissionError('denied access')

    monkeypatch.setattr('tooling.ProjectCLI._find_manifest', denied)
    code = run_project_command(['check'])
    human = capsys.readouterr()
    assert code == 3 and '[W5004]' in human.err and human.out == ''
    code = run_project_command(['check', '--output-format=json-v1'])
    machine = capsys.readouterr()
    payload = json.loads(machine.out)
    assert code == 3 and machine.err == ''
    assert payload['status'] == 'toolchain_error' and payload['diagnostics'][0]['code'] == 'W5004'


def test_unreadable_manifest_reports_filesystem_error(tmp_path, monkeypatch, capsys):
    project, _ = create_project('example', tmp_path)
    monkeypatch.chdir(project)
    original = Path.read_bytes

    def denied(path):
        if path.name == 'wysna.toml':
            raise PermissionError('manifest access denied')
        return original(path)

    monkeypatch.setattr(Path, 'read_bytes', denied)
    code = run_project_command(['check', '--output-format=json-v1'])
    payload = json.loads(capsys.readouterr().out)
    assert code == 3 and payload['status'] == 'toolchain_error'
    assert payload['diagnostics'][0]['code'] == 'W5004'


def test_unreadable_lockfile_reports_filesystem_error(tmp_path, monkeypatch, capsys):
    project, _ = create_project('example', tmp_path)
    ProjectResolver.load(project / 'wysna.toml').write_lock()
    monkeypatch.chdir(project)
    original = Path.read_text

    def denied(path, *args, **kwargs):
        if path.name == 'wysna.lock':
            raise PermissionError('lockfile access denied')
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, 'read_text', denied)
    code = run_project_command(['check', '--frozen', '--output-format=json-v1'])
    payload = json.loads(capsys.readouterr().out)
    assert code == 3 and payload['status'] == 'toolchain_error'
    assert payload['diagnostics'][0]['code'] == 'W5004'


def test_creation_failure_preserves_external_changes(tmp_path, monkeypatch):
    original = Path.open

    def fail_after_external_change(path, *args, **kwargs):
        if path.name == "main.wysna":
            (tmp_path / "example" / "wysna.toml").write_text("user modification", encoding="utf-8")
            raise OSError("simulated write failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_after_external_change)
    with pytest.raises(ScaffoldError, match="partially created directory"):
        create_project("example", tmp_path)
    assert (tmp_path / "example" / "wysna.toml").read_text(encoding="utf-8") == "user modification"


def test_creation_failure_cleans_unchanged_files(tmp_path, monkeypatch):
    original = Path.open

    def fail_on_source(path, *args, **kwargs):
        if path.name == "main.wysna":
            raise OSError("simulated write failure")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", fail_on_source)
    with pytest.raises(ScaffoldError):
        create_project("example", tmp_path)
    assert not (tmp_path / "example").exists()
