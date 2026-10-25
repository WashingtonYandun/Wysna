import hashlib
import json
from pathlib import Path
import subprocess
import sys

from error.Error import WysnaError
from tooling.ToolResult import diagnostic_from_error

ROOT = Path(__file__).resolve().parents[2]


def run_cli(*args):
    return subprocess.run(
        [sys.executable, str(ROOT / "main.py"), *map(str, args)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_check_json_v1_success_has_one_versioned_envelope(tmp_path):
    source = tmp_path / "valid.wysna"
    source.write_text("fn main() -> unit { print(42) }\n", encoding="utf-8")

    result = run_cli("check", "--output-format=json-v1", source)

    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.count("\n") == 1
    assert payload["schema_version"] == "wysna.tool-result/1"
    assert payload["command"] == "check"
    assert payload["status"] == "ok"
    assert payload["tool_exit_code"] == payload["process_exit_code"] == 0
    assert payload["diagnostics"] == []


def test_check_json_v1_diagnostic_uses_exact_source_hash_and_byte_position(tmp_path):
    source_bytes = '## café\r\nlet value: int = "wrong"\r\n'.encode("utf-8")
    source = tmp_path / "invalid.wysna"
    source.write_bytes(source_bytes)

    result = run_cli("check", "--output-format=json-v1", source)

    payload = json.loads(result.stdout)
    diagnostic = payload["diagnostics"][0]
    span = diagnostic["primary_span"]
    assert result.returncode == 1
    assert result.stderr == ""
    assert payload["status"] == "diagnostic_error"
    assert diagnostic["code"] == "W2001"
    assert span["source_sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert source_bytes[span["start_byte"]:span["end_byte"]] == b"let"
    assert span["start_byte"] == source_bytes.index(b"let")


def test_check_json_v1_uses_utf8_token_ranges_and_exact_crlf_snapshot(tmp_path):
    source_bytes = '# 🚀\r\nlet value: int = "wrong"\r\n'.encode("utf-8")
    source = tmp_path / "unicode-invalid.wysna"
    source.write_bytes(source_bytes)

    result = run_cli("check", "--output-format=json-v1", source)

    payload = json.loads(result.stdout)
    span = payload["diagnostics"][0]["primary_span"]
    assert result.returncode == 1
    assert span["source_sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert source_bytes[span["start_byte"]:span["end_byte"]] == b"let"
    assert span["start_byte"] == source_bytes.index(b"let")


def test_check_json_v1_resolves_imported_diagnostics_to_import_snapshot(tmp_path):
    entry = tmp_path / "main.wysna"
    library = tmp_path / "library.wysna"
    library_bytes = b'pub fn broken() -> int {\r\n    let value: int = "wrong"\r\n    return 0\r\n}\r\n'
    entry.write_bytes(b'import "library.wysna"\r\nfn main() -> unit {}\r\n')
    library.write_bytes(library_bytes)

    result = run_cli("check", "--output-format=json-v1", entry)

    payload = json.loads(result.stdout)
    span = payload["diagnostics"][0]["primary_span"]
    assert result.returncode == 1
    assert span["source_id"] == str(library.resolve())
    assert span["source_sha256"] == hashlib.sha256(library_bytes).hexdigest()
    assert library_bytes[span["start_byte"]:span["end_byte"]] == b"let"


def test_check_json_v1_reports_lexer_character_range_and_missing_location_as_null(tmp_path):
    malformed = tmp_path / "malformed.wysna"
    malformed.write_text("let value = 1__2\n", encoding="utf-8")
    lexical_result = run_cli("check", "--output-format=json-v1", malformed)
    lexical_payload = json.loads(lexical_result.stdout)
    lexical_span = lexical_payload["diagnostics"][0]["primary_span"]
    assert lexical_payload["diagnostics"][0]["code"] == "W0002"
    assert malformed.read_bytes()[lexical_span["start_byte"]:lexical_span["end_byte"]] == b"_"

    syntax = tmp_path / "missing-expression.wysna"
    syntax.write_text("let value =", encoding="utf-8")
    syntax_result = run_cli("check", "--output-format=json-v1", syntax)
    syntax_payload = json.loads(syntax_result.stdout)
    syntax_span = syntax_payload["diagnostics"][0]["primary_span"]
    assert syntax_payload["diagnostics"][0]["code"] == "W0001"
    assert syntax_span["start_byte"] == syntax_span["end_byte"] == len(syntax.read_bytes())

    source_text = syntax.read_bytes().decode("utf-8")
    unlocated = WysnaError("Host failure without a source location", file=str(syntax), code="W9999")
    unlocated_diagnostic = diagnostic_from_error(unlocated, {str(syntax): source_text})
    assert unlocated_diagnostic["primary_span"] is None


def test_check_json_v1_serializes_usage_and_file_errors(tmp_path):
    usage_result = run_cli("check", "--output-format=json-v1", "--unknown")
    missing_file = tmp_path / "missing.wysna"
    file_result = run_cli("check", "--output-format=json-v1", missing_file)
    invalid_utf8 = tmp_path / "invalid-utf8.wysna"
    invalid_utf8.write_bytes(b"\xff")
    decode_result = run_cli("check", "--output-format=json-v1", invalid_utf8)

    usage_payload = json.loads(usage_result.stdout)
    file_payload = json.loads(file_result.stdout)
    decode_payload = json.loads(decode_result.stdout)
    assert usage_result.returncode == 2
    assert usage_result.stderr == ""
    assert usage_payload["status"] == "usage_error"
    assert usage_payload["tool_exit_code"] == 2
    assert usage_payload["diagnostics"][0]["code"] == "W5002"
    assert file_result.returncode == 3
    assert file_result.stderr == ""
    assert file_payload["status"] == "toolchain_error"
    assert file_payload["tool_exit_code"] == 3
    assert file_payload["diagnostics"][0]["code"] == "W5003"
    assert decode_result.returncode == 3
    assert decode_result.stderr == ""
    assert decode_payload["status"] == "toolchain_error"
    assert decode_payload["diagnostics"][0]["code"] == "W5003"


def test_check_json_v1_rejects_legacy_json_combination(tmp_path):
    source = tmp_path / "valid.wysna"
    source.write_text("fn main() -> unit { }\n", encoding="utf-8")

    result = run_cli("check", "--json", "--output-format=json-v1", source)

    payload = json.loads(result.stdout)
    assert result.returncode == 2
    assert result.stderr == ""
    assert payload["status"] == "usage_error"


def test_run_passes_json_v1_looking_argument_after_separator(tmp_path):
    source = tmp_path / "argv.wysna"
    source.write_text("fn main() -> unit { print(args()[1]) }\n", encoding="utf-8")

    result = run_cli("run", source, "--", "--output-format=json-v1")

    assert result.returncode == 0
    assert result.stdout == "--output-format=json-v1\n"
    assert result.stderr == ""


def test_run_report_json_preserves_program_streams_and_reports_application_exit(tmp_path):
    source = tmp_path / "run.wysna"
    report = tmp_path / "run-result.json"
    source.write_text(
        'fn main() -> unit { print("program output"); exit(7) }\n',
        encoding="utf-8",
    )

    result = run_cli(
        "run", "--report-json", report, "--invocation-id", "task-42", source,
    )

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert result.returncode == 7
    assert result.stdout == "program output\n"
    assert result.stderr == ""
    assert payload["schema_version"] == "wysna.tool-result/1"
    assert payload["command"] == "run"
    assert payload["status"] == "ok"
    assert payload["invocation_id"] == "task-42"
    assert payload["tool_exit_code"] == 0
    assert payload["process_exit_code"] == 7
    assert payload["execution"] == {
        "kind": "application_exit",
        "exit_code": 7,
        "signal": None,
    }


def test_run_report_json_exposes_pending_snapshot_to_the_program(tmp_path):
    source = tmp_path / "read-report.wysna"
    report = tmp_path / "result.json"
    source.write_text(
        "fn main() -> unit {\n"
        "    let snapshot = read_file(args()[1]) else err { return }\n"
        "    print(snapshot)\n"
        "}\n",
        encoding="utf-8",
    )

    result = run_cli("run", "--report-json", report, source, "--", report)

    pending = json.loads(result.stdout)
    final = json.loads(report.read_text(encoding="utf-8"))
    assert result.returncode == 0
    assert result.stderr == ""
    assert pending["status"] == "pending"
    assert pending["invocation_id"] == final["invocation_id"]
    assert final["status"] == "ok"


def test_run_report_json_reports_pre_execution_diagnostics(tmp_path):
    source = tmp_path / "invalid.wysna"
    report = tmp_path / "result.json"
    source.write_text("fn main() -> unit { let value: int = \"wrong\" }\n", encoding="utf-8")

    result = run_cli("run", "--report-json", report, source)

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr
    assert payload["status"] == "diagnostic_error"
    assert payload["tool_exit_code"] == payload["process_exit_code"] == 1
    assert payload["execution"] is None
    assert payload["diagnostics"][0]["code"] == "W2001"


def test_run_report_json_hashes_the_unmodified_crlf_source_snapshot(tmp_path):
    source_bytes = b'fn main() -> unit {\r\n    let value: int = "wrong"\r\n}\r\n'
    source = tmp_path / "crlf-invalid.wysna"
    report = tmp_path / "result.json"
    source.write_bytes(source_bytes)

    result = run_cli("run", "--report-json", report, source)

    payload = json.loads(report.read_text(encoding="utf-8"))
    span = payload["diagnostics"][0]["primary_span"]
    assert result.returncode == 1
    assert payload["status"] == "diagnostic_error"
    assert span["source_sha256"] == hashlib.sha256(source_bytes).hexdigest()
    assert source_bytes[span["start_byte"]:span["end_byte"]] == b"let"


def test_run_report_json_rejects_source_overwrite(tmp_path):
    source = tmp_path / "source.wysna"
    original = b"fn main() -> unit { print(42) }\n"
    source.write_bytes(original)

    result = run_cli("run", "--report-json", source, source)

    assert result.returncode == 2
    assert result.stdout == ""
    assert "must not overwrite the source" in result.stderr
    assert source.read_bytes() == original


def test_run_report_json_does_not_start_program_when_pending_write_fails(tmp_path):
    source = tmp_path / "run.wysna"
    report = tmp_path / "missing-directory" / "result.json"
    source.write_text('fn main() -> unit { print("started") }\n', encoding="utf-8")

    result = run_cli("run", "--report-json", report, source)

    assert result.returncode == 3
    assert result.stdout == ""
    assert "unable to initialize JSON report" in result.stderr
    assert not report.parent.exists()


def test_fmt_json_v1_returns_inline_formatted_source_and_hash(tmp_path):
    source = tmp_path / "format.wysna"
    source.write_text("fn main() -> unit { print(42) }\n", encoding="utf-8")

    result = run_cli("fmt", "--output-format=json-v1", source)

    payload = json.loads(result.stdout)
    artifact = payload["artifacts"][0]
    content = artifact["content"]
    assert result.returncode == 0
    assert result.stderr == ""
    assert result.stdout.count("\n") == 1
    assert payload["command"] == "fmt"
    assert payload["status"] == "ok"
    assert artifact["kind"] == "formatted_source"
    assert artifact["media_type"] == "text/x-wysna"
    assert artifact["sha256"] == hashlib.sha256(content.encode("utf-8")).hexdigest()
    assert source.read_text(encoding="utf-8") == "fn main() -> unit { print(42) }\n"


def test_fmt_json_v1_write_reports_the_file_snapshot(tmp_path):
    source = tmp_path / "format.wysna"
    source.write_text("fn main() -> unit { print(42) }\n", encoding="utf-8")

    result = run_cli("fmt", "--write", "--output-format=json-v1", source)

    payload = json.loads(result.stdout)
    artifact = payload["artifacts"][0]
    formatted_bytes = source.read_bytes()
    assert result.returncode == 0
    assert result.stderr == ""
    assert payload["command"] == "fmt"
    assert artifact["path"] == str(source)
    assert artifact["sha256"] == hashlib.sha256(formatted_bytes).hexdigest()
    assert "content" not in artifact


def test_emit_c_json_v1_reports_created_c_artifact(tmp_path):
    source = tmp_path / "emit.wysna"
    output = tmp_path / "build" / "emit.c"
    source.write_text("fn main() -> unit { print(42) }\n", encoding="utf-8")
    output.parent.mkdir()

    result = run_cli("emit-c", "--output-format=json-v1", source, "-o", output)

    payload = json.loads(result.stdout)
    artifact = payload["artifacts"][0]
    assert result.returncode == 0
    assert result.stderr == ""
    assert payload["command"] == "emit-c"
    assert payload["status"] == "ok"
    assert artifact["kind"] == "c_source"
    assert artifact["media_type"] == "text/x-c"
    assert artifact["path"] == str(output)
    assert artifact["sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()


def test_build_json_v1_reports_native_binary_artifact(tmp_path):
    source = tmp_path / "build.wysna"
    output = tmp_path / "build" / "app"
    source.write_text("fn main() -> unit { print(42) }\n", encoding="utf-8")
    output.parent.mkdir()

    result = run_cli("build", "--output-format=json-v1", source, "-o", output)

    payload = json.loads(result.stdout)
    artifact = payload["artifacts"][0]
    assert result.returncode == 0
    assert result.stderr == ""
    assert payload["command"] == "build"
    assert payload["status"] == "ok"
    assert artifact["kind"] == "native_executable"
    assert artifact["media_type"] == "application/octet-stream"
    assert artifact["sha256"] == hashlib.sha256(Path(artifact["path"]).read_bytes()).hexdigest()


def test_codegen_json_v1_rejects_source_overwrite(tmp_path):
    source = tmp_path / "source.wysna"
    original = b"fn main() -> unit { print(42) }\n"
    source.write_bytes(original)

    result = run_cli("emit-c", "--output-format=json-v1", source, "-o", source)

    payload = json.loads(result.stdout)
    assert result.returncode == 2
    assert result.stderr == ""
    assert payload["command"] == "emit-c"
    assert payload["status"] == "usage_error"
    assert payload["diagnostics"][0]["code"] == "W5002"
    assert source.read_bytes() == original


def test_json_v1_reports_output_artifact_failures(tmp_path):
    source = tmp_path / "source.wysna"
    source.write_text("fn main() -> unit { print(42) }\n", encoding="utf-8")
    output = tmp_path / "missing-directory" / "program.c"

    result = run_cli("emit-c", "--output-format=json-v1", source, "-o", output)

    payload = json.loads(result.stdout)
    assert result.returncode == 3
    assert result.stderr == ""
    assert payload["command"] == "emit-c"
    assert payload["status"] == "toolchain_error"
    assert payload["diagnostics"][0]["code"] == "W5004"
    assert payload["artifacts"] == []
