"""User-facing diagnostics and their machine-readable source ranges."""

import json
from pathlib import Path
import subprocess
import sys

from error.Error import WysnaError
from tooling.DiagnosticRender import render_diagnostic
from tooling.LanguageServer import LanguageServer

ROOT = Path(__file__).resolve().parents[2]


def test_none_mismatch_has_actionable_human_and_json_diagnostics(tmp_path):
    source = tmp_path / "example.wysna"
    source.write_text("let name: str = none\n", encoding="utf-8")
    command = [sys.executable, str(ROOT / "main.py"), "check", str(source)]
    human = subprocess.run(command, capture_output=True, text=True, cwd=tmp_path)
    assert human.returncode == 1
    assert ":1:17" in human.stderr
    assert "1 | let name: str = none" in human.stderr
    assert "  |                 ^^^^" in human.stderr
    assert "help: If this value can be absent" in human.stderr

    machine = subprocess.run(command + ["--output-format=json-v1"],
                             capture_output=True, text=True, cwd=tmp_path)
    diagnostic = json.loads(machine.stdout)["diagnostics"][0]
    span = diagnostic["primary_span"]
    assert machine.returncode == 1 and machine.stderr == ""
    assert source.read_bytes()[span["start_byte"]:span["end_byte"]] == b"none"
    assert diagnostic["data"]["help"] == "If this value can be absent, consider an optional type."


def test_excerpt_uses_snapshot_and_sanitizes_terminal_controls():
    source = "let value = 1\t\x1b[31m\n"
    error = WysnaError("invalid expression", line=1, col=13, file="example.wysna")
    error.start_byte = 12
    error.end_byte = 13
    result = render_diagnostic(error, {"example.wysna": source})
    assert "let value = 1" in result
    assert "\x1b" not in result
    assert "^" in result


def test_header_and_help_sanitize_terminal_controls():
    filename = "evil\x1b[31m.wysna"
    source = "let value = 1\n"
    error = WysnaError(
        "bad \x1b[31mred\x1b[0m message\ninjected",
        line=1,
        col=1,
        file=filename,
        help="visit \x1b]8;;https://example.invalid\x07here",
    )
    result = render_diagnostic(error, {filename: source})
    lines = result.splitlines()
    assert result.count("\n") == 4
    assert lines[0].startswith("[W0001] evil")
    assert "\x1b" not in result and "\x07" not in result
    assert "injected" in lines[0]
    assert lines[-1].strip().startswith("= help: visit")


def test_unlocated_header_sanitizes_terminal_controls():
    error = WysnaError("bad \x1b[2Jtext", file="evil\x1b[0m.wysna", code="W9999")
    result = render_diagnostic(error, {})
    assert result == "[W9999] evil.wysna - bad text"


def test_unterminated_osc_does_not_swallow_following_text():
    error = WysnaError("bad \x1b]0;title\nnext \x07line", file="evil.wysna")
    result = render_diagnostic(error, {"evil.wysna": "let value = 1\n"})
    header = result.splitlines()[0]
    assert "\x1b" not in result and "\x07" not in result
    assert "next" in header and "line" in header


def test_runtime_diagnostic_header_is_sanitized(capsys):
    from tooling.SourceCommand import _print_human_diagnostics

    error = WysnaError("bad \x1b[31mred", line=1, col=1,
                       file="evil\x1b[0m.wysna", code="W4002", stage="runtime")
    _print_human_diagnostics(error, {})
    result = capsys.readouterr().err
    assert "\x1b" not in result
    assert "[W4002]" in result and "bad red" in result and "evil.wysna" in result


def test_unknown_source_location_does_not_invent_excerpt():
    error = WysnaError("unlocated failure", file="missing.wysna", code="W9999")
    assert render_diagnostic(error, {"missing.wysna": "some text"}) == (
        "[W9999] missing.wysna - unlocated failure"
    )


def test_editor_diagnostic_uses_precise_range_and_help(tmp_path):
    messages = []
    server = LanguageServer(messages.append)
    uri = (tmp_path / "editor.wysna").as_uri()
    server.update(uri, "let name: str = none\n", 1)
    diagnostic = messages[-1]["params"]["diagnostics"][0]
    assert diagnostic["source"] == "Wysna"
    assert diagnostic["range"] == {
        "start": {"line": 0, "character": 16},
        "end": {"line": 0, "character": 20},
    }
    assert "Help: If this value can be absent" in diagnostic["message"]


def test_editor_range_counts_utf16_code_units(tmp_path):
    messages = []
    server = LanguageServer(messages.append)
    uri = (tmp_path / "unicode.wysna").as_uri()
    source = 'let emoji = "😀"; let name: str = none\n'
    server.update(uri, source, 1)
    diagnostic = messages[-1]["params"]["diagnostics"][0]
    start = len(source[:source.index("none")].encode("utf-16-le")) // 2
    assert diagnostic["range"] == {
        "start": {"line": 0, "character": start},
        "end": {"line": 0, "character": start + 4},
    }


def test_project_check_reports_imported_source_excerpt(tmp_path):
    source_dir = tmp_path / "src"
    source_dir.mkdir()
    (tmp_path / "wysna.toml").write_text(
        'manifest_version = 1\n[package]\nname = "example"\nversion = "0.1.0"\n'
        'language = "0.5"\n[toolchain]\ncompiler = "0.5.0"\nstdlib = "bundled"\n'
        '[sources]\nroot = "src"\n[targets.app]\nentry = "src/main.wysna"\n'
        'profile = "native-cli"\n', encoding="utf-8")
    (source_dir / "main.wysna").write_text('import "bad.wysna"\n', encoding="utf-8")
    imported = source_dir / "bad.wysna"
    imported.write_text('let value: int = "wrong"\n', encoding="utf-8")
    result = subprocess.run([sys.executable, str(ROOT / "main.py"), "project", "check"],
                            cwd=tmp_path, capture_output=True, text=True)
    assert result.returncode == 1
    assert str(imported) in result.stderr
    assert 'let value: int = "wrong"' in result.stderr
    assert "^^^^^^^" in result.stderr
