"""Block 3A/3B: Native SDK --sandbox (W5006) and W6001/W6002 lint parity in CLI and Stage-3 LSP."""
import json
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.Platform import executable_name
from tools.build_selfhost_dev import assemble_dev_sdk


def _has_zig(driver: Path) -> bool:
    return (driver.parent.parent / "toolchain/zig" / executable_name("zig")).is_file()


@pytest.fixture(scope="module")
def native_driver() -> Path:
    report = assemble_dev_sdk(["driver", "zig-shim", "compiler"])
    return Path(report["driver"])


def _run_native(driver: Path, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(driver), *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def _lsp_frame(payload: dict) -> bytes:
    body = json.dumps(payload).encode("utf-8")
    return f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body


def _lsp_read_messages(raw: bytes) -> list[dict]:
    msgs = []
    pos = 0
    while pos < len(raw):
        sep = raw.find(b"\r\n\r\n", pos)
        if sep < 0:
            break
        headers = raw[pos:sep].decode("ascii", errors="replace")
        length = 0
        for line in headers.splitlines():
            if line.lower().startswith("content-length:"):
                length = int(line.split(":", 1)[1].strip())
        start = sep + 4
        end = start + length
        if end > len(raw):
            break
        msgs.append(json.loads(raw[start:end].decode("utf-8")))
        pos = end
    return msgs


# ---- Block 3B: W6001 & W6002 Lints in Native SDK check + LSP ----


def test_native_discovery_advertises_packages_and_closed_language_contracts(native_driver, tmp_path):
    result = _run_native(native_driver, tmp_path, 'capabilities', '--output-format=json-v1')
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)['result']
    commands = {'packages.pack', 'packages.fetch', 'packages.add', 'packages.remove', 'packages.restore'}
    assert commands <= set(payload['commands'])
    assert commands <= set(payload['machine_output']['commands'])
    syntax = _run_native(native_driver, tmp_path, 'skills', 'syntax', '--output-format=json-v1')
    assert syntax.returncode == 0, syntax.stderr
    content = json.loads(syntax.stdout)['result']['content']
    assert '@must_use' in content and '@deprecated' in content and 'W6003' in content
    workflow = _run_native(native_driver, tmp_path, 'skills', 'project-tests', '--output-format=json-v1')
    assert workflow.returncode == 0, workflow.stderr
    assert 'packages restore' in json.loads(workflow.stdout)['result']['content']


def test_native_check_reports_w6001_discarded_expression_json_and_text(native_driver, tmp_path):
    src = tmp_path / "asi.wysna"
    src.write_text(
        "fn f(a: int, b: int) -> int {\n"
        "    let x = a\n"
        "    -b\n"
        "    return x\n"
        "}\n"
        "fn main() { print(f(5, 3)) }\n",
        encoding="utf-8",
    )
    res_json = _run_native(native_driver, tmp_path, "check", "--output-format=json-v1", str(src))
    assert res_json.returncode == 0, res_json.stderr
    payload = json.loads(res_json.stdout)
    assert payload["status"] == "ok"
    assert len(payload["diagnostics"]) == 1
    diag = payload["diagnostics"][0]
    assert diag["code"] == "W6001"
    assert diag["severity"] == "warning"
    assert diag["stage"] == "lint"
    assert "end of the previous line" in diag["data"]["help"]

    res_txt = _run_native(native_driver, tmp_path, "check", str(src))
    assert res_txt.returncode == 0
    assert res_txt.stdout == ""
    assert "warning[W6001]" in res_txt.stderr


def test_native_check_does_not_lint_calls_assignments_or_tail_returns(native_driver, tmp_path):
    src = tmp_path / "clean.wysna"
    src.write_text(
        "fn g(x: int) -> int { x + 1 }\n"
        "fn main() {\n"
        "    let mut n = 0\n"
        "    g(n)\n"
        "    n = 2\n"
        "    print(n)\n"
        "}\n",
        encoding="utf-8",
    )
    res = _run_native(native_driver, tmp_path, "check", "--output-format=json-v1", str(src))
    assert res.returncode == 0, res.stderr
    assert json.loads(res.stdout)["diagnostics"] == []


def test_native_check_reports_w6002_arc_cycle_and_clears_with_weak(native_driver, tmp_path):
    cyclic = tmp_path / "cycle.wysna"
    cyclic.write_text(
        "class A { public b: B? }\n"
        "class B { public a: A? }\n"
        "fn main() { print(1) }\n",
        encoding="utf-8",
    )
    res = _run_native(native_driver, tmp_path, "check", "--output-format=json-v1", str(cyclic))
    assert res.returncode == 0, res.stderr
    payload = json.loads(res.stdout)
    assert [d["code"] for d in payload["diagnostics"]] == ["W6002"]
    assert payload["diagnostics"][0]["severity"] == "warning"
    assert payload["diagnostics"][0]["stage"] == "lint"

    acyclic = tmp_path / "weak_ok.wysna"
    acyclic.write_text(
        "class A { public b: B? }\n"
        "class B { public a: Weak[A] }\n"
        "fn main() { print(1) }\n",
        encoding="utf-8",
    )
    res_ok = _run_native(native_driver, tmp_path, "check", "--output-format=json-v1", str(acyclic))
    assert res_ok.returncode == 0, res_ok.stderr
    assert json.loads(res_ok.stdout)["diagnostics"] == []


def test_native_lsp_publishes_w6001_and_w6002_warnings(native_driver, tmp_path):
    doc = tmp_path / "lints.wysna"
    text = (
        "class A { public b: B? }\n"
        "class B { public a: A? }\n"
        "fn f(x: int) -> int {\n"
        "    x + 1\n"
        "    return x\n"
        "}\n"
        "fn main() { print(f(1)) }\n"
    )
    doc.write_text(text, encoding="utf-8")
    uri = doc.resolve().as_uri()
    stream = (
        _lsp_frame({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"rootUri": tmp_path.resolve().as_uri(), "capabilities": {}}})
        + _lsp_frame({"jsonrpc": "2.0", "method": "initialized", "params": {}})
        + _lsp_frame({"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": {"uri": uri, "languageId": "wysna", "version": 1, "text": text}}})
        + _lsp_frame({"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": None})
        + _lsp_frame({"jsonrpc": "2.0", "method": "exit", "params": None})
    )
    proc = subprocess.run(
        [str(native_driver), "lsp"],
        cwd=tmp_path,
        input=stream,
        capture_output=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    messages = _lsp_read_messages(proc.stdout)
    published = [
        m["params"]["diagnostics"]
        for m in messages
        if m.get("method") == "textDocument/publishDiagnostics" and m["params"]["uri"] == uri
    ]
    assert published, messages
    diags = published[-1]
    codes = {d["code"] for d in diags}
    assert codes == {"W6001", "W6002"}, diags
    assert all(d["severity"] == 2 for d in diags), diags


# ---- Block 3A: --sandbox & --allow / --deny (W5006) in Native SDK ----


def test_native_sandbox_allows_pure_and_emits_c99_deny_guards(native_driver, tmp_path):
    pure = tmp_path / "pure.wysna"
    pure.write_text('fn main() { print("safe") }\nmain()\n', encoding="utf-8")

    if _has_zig(native_driver):
        run_res = _run_native(native_driver, tmp_path, "run", "--sandbox", str(pure))
        assert run_res.returncode == 0 and run_res.stdout.strip() == "safe", run_res.stderr

    c_out = tmp_path / "pure.c"
    emit_res = _run_native(native_driver, tmp_path, "emit-c", "--sandbox", str(pure), "-o", str(c_out))
    assert emit_res.returncode == 0, emit_res.stderr
    c_src = c_out.read_text(encoding="utf-8")
    assert "#define WYSNA_SANDBOX_ENFORCED 1" in c_src
    assert "#define WYSNA_DENY_CAP_FILESYSTEM_WRITE 1" in c_src
    assert "#define WYSNA_DENY_CAP_NETWORK 1" in c_src
    assert "#define WYSNA_DENY_CAP_STDIO 1" not in c_src


def test_native_sandbox_blocks_write_file_before_side_effects_text_and_json(native_driver, tmp_path):
    impure = tmp_path / "impure.wysna"
    marker = tmp_path / "marker.txt"
    impure.write_text(
        f'write_file("{marker.as_posix()}", "boom") else err {{ print(err.message) }}\n'
        'print("done")\n',
        encoding="utf-8",
    )

    refused_run = _run_native(native_driver, tmp_path, "run", "--sandbox", str(impure))
    assert refused_run.returncode == 1
    assert "[W5006]" in refused_run.stderr
    assert "filesystem.write" in refused_run.stderr
    assert not marker.exists()

    bin_out = tmp_path / "impure_bin"
    refused_json = _run_native(
        native_driver, tmp_path, "build", "--sandbox", "--output-format=json-v1", str(impure), "-o", str(bin_out)
    )
    assert refused_json.returncode == 1, refused_json.stdout + refused_json.stderr
    payload = json.loads(refused_json.stdout)
    assert payload["diagnostics"][0]["code"] == "W5006"
    assert payload["diagnostics"][0]["stage"] == "capability-policy"
    assert not bin_out.exists()

    # Granting filesystem.write + stdio succeeds and writes the marker (when zig/cc shim is available)
    if _has_zig(native_driver):
        allowed = _run_native(
            native_driver, tmp_path, "run", "--sandbox", "--allow=stdio,filesystem.write", str(impure)
        )
        assert allowed.returncode == 0, allowed.stderr
        assert marker.read_text(encoding="utf-8") == "boom"
    else:
        c_allowed = tmp_path / "allowed.c"
        emit_ok = _run_native(
            native_driver, tmp_path, "emit-c", "--sandbox", "--allow=stdio,filesystem.write", str(impure), "-o", str(c_allowed)
        )
        assert emit_ok.returncode == 0, emit_ok.stderr
        c_src = c_allowed.read_text(encoding="utf-8")
        assert "#define WYSNA_DENY_CAP_FILESYSTEM_WRITE 1" not in c_src
        assert "#define WYSNA_DENY_CAP_NETWORK 1" in c_src


def test_native_sandbox_reachability_and_untrusted_ffi_and_invalid_options(native_driver, tmp_path):
    # Importing std/files without calling filesystem ops passes under --allow=stdio
    unused_import = tmp_path / "unused_import.wysna"
    unused_import.write_text('import "std/files" as files\nprint("ok")\n', encoding="utf-8")
    if _has_zig(native_driver):
        ok = _run_native(native_driver, tmp_path, "run", "--allow=stdio", str(unused_import))
        assert ok.returncode == 0 and ok.stdout.strip() == "ok", ok.stderr
    else:
        c_unused = tmp_path / "unused.c"
        ok = _run_native(native_driver, tmp_path, "emit-c", "--sandbox", "--allow=stdio", str(unused_import), "-o", str(c_unused))
        assert ok.returncode == 0, ok.stderr

    # Untrusted extern "C" is rejected even if unused
    foreign = tmp_path / "foreign.wysna"
    foreign.write_text('extern "C" fn custom_c_fn(x: int) -> int\nprint("ok")\n', encoding="utf-8")
    blocked = _run_native(native_driver, tmp_path, "run", "--sandbox", str(foreign))
    assert blocked.returncode == 1 and "[W5006]" in blocked.stderr
    assert "untrusted foreign declarations" in blocked.stderr

    # Combining --sandbox with --library is rejected as W5002 usage error (exit 2)
    combo = _run_native(native_driver, tmp_path, "build", "--sandbox", "--library", "m", str(unused_import))
    assert combo.returncode == 2 and "[W5002]" in combo.stderr

    # Combining --allow and --deny is rejected as W5002 usage error (exit 2)
    both = _run_native(native_driver, tmp_path, "run", "--allow=stdio", "--deny=network", str(unused_import))
    assert both.returncode == 2 and "[W5002]" in both.stderr
