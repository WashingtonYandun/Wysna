"""Block 1 contracts: <5 s per-unit Selfhost dev builds, SIMP-R01 delegation, and actionable target diagnostics."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.build_selfhost_dev import DEV_SDK_DIR, DEV_UNITS_DIR, assemble_dev_sdk, build_wysna_unit, _find_or_build_emitter


@pytest.fixture(scope="module")
def dev_sdk():
    report = assemble_dev_sdk(["driver", "zig-shim", "project", "mcp"])
    return report


def _run_cli(cwd: Path, *args: str, env_extra: dict | None = None, input_text: str | None = None):
    env = os.environ.copy()
    env.pop("WYSNA_ENGINE", None)
    env.pop("WYSNA_DELEGATE_NATIVE", None)
    env.pop("WYSNA_NATIVE_DRIVER", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(ROOT / "main.py"), *args],
        cwd=str(cwd),
        env=env,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=60,
    )


def test_selfhost_bundle_is_in_sync():
    res = subprocess.run(
        [sys.executable, str(ROOT / "tools/bundle_selfhost.py"), "--check"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert res.returncode == 0, res.stderr + res.stdout


def test_per_unit_emission_is_bounded_and_selectively_cached(dev_sdk):
    emitter, _ = _find_or_build_emitter(rebuild=False)
    unit_limits = {
        "mcp": (2500, 2048),      # max LOC, max emitted C KiB (vs ~19.8k LOC / ~13 MiB monolith)
        "project": (4200, 3072),
    }
    for unit, (max_lines, max_c_kb) in unit_limits.items():
        unit_cache = DEV_UNITS_DIR / unit
        if unit_cache.exists():
            shutil.rmtree(unit_cache)
        binary, meta = build_wysna_unit(unit, emitter, opt="-O0")
        assert binary.is_file()
        assert meta["cache_hit"] is False
        assert meta["lines"] <= max_lines, f"unit '{unit}' grew beyond split budget: {meta}"
        assert meta["c_kb"] <= max_c_kb, f"unit '{unit}' emitted C exceeded split budget: {meta}"
        assert meta["total_s"] < 30.0, f"unit '{unit}' stalled unexpectedly: {meta}"

        cached_binary, cached_meta = build_wysna_unit(unit, emitter, opt="-O0")
        assert cached_binary == binary
        assert cached_meta["cache_hit"] is True
        assert cached_meta["total_s"] < 1.0


@pytest.mark.parametrize(("command", "target_args"), [
    ("run", ["--target=wasi"]),
    ("run", ["--target", "wasi"]),
    ("run", ["--target=wasi-command"]),
    ("run", ["--target=wasm32-wasip1"]),
    ("build", ["--target=web"]),
    ("build", ["--target=browser-library"]),
])
def test_driver_unsupported_commands_emit_actionable_w2013(dev_sdk, tmp_path, command, target_args):
    src = tmp_path / "hello.wysna"
    src.write_text('print("hi")\n', encoding="utf-8")
    driver = dev_sdk["driver"]

    text_res = subprocess.run(
        [driver, command, str(src), *target_args],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=30,
    )
    assert text_res.returncode == 2
    assert "[W2013]" in text_res.stderr
    assert "Seed distribution" in text_res.stderr

    json_res = subprocess.run(
        [driver, command, str(src), *target_args, "--output-format=json-v1"],
        cwd=str(tmp_path), capture_output=True, text=True, timeout=30,
    )
    assert json_res.returncode == 2, json_res.stderr + json_res.stdout
    payload = json.loads(json_res.stdout)
    assert payload["invocation_id"] == "native"
    assert payload["diagnostics"][0]["code"] == "W2013"
    assert "Seed distribution" in payload["diagnostics"][0]["message"]


def _make_driver_spy(tmp_path: Path, real_driver: str) -> tuple[Path | None, Path]:
    log_path = tmp_path / "native_delegation.log"
    if os.name == "nt":
        return None, log_path
    spy = tmp_path / "wysna_spy.sh"
    spy.write_text(f'#!/bin/sh\necho "$*" >> "{log_path}"\nexec "{real_driver}" "$@"\n', encoding="utf-8")
    spy.chmod(0o755)
    return spy, log_path


def test_packages_command_is_native_only(dev_sdk, tmp_path):
    spy, log_path = _make_driver_spy(tmp_path, dev_sdk["driver"])
    extra = {"WYSNA_NATIVE_DRIVER": str(spy)} if spy else None
    res = _run_cli(tmp_path, "packages", "--output-format=json-v1", env_extra=extra)
    assert res.returncode == 2, res.stderr + res.stdout
    payload = json.loads(res.stdout)
    assert payload["command"] == "packages"
    assert payload["diagnostics"][0]["code"] == "W5002"
    if spy:
        assert "packages" in log_path.read_text(encoding="utf-8")

    missing = _run_cli(
        tmp_path, "packages", "--output-format=json-v1",
        env_extra={"WYSNA_NATIVE_DRIVER": str(tmp_path / "missing-driver")},
    )
    assert missing.returncode == 3
    err_payload = json.loads(missing.stdout)
    assert err_payload["diagnostics"][0]["code"] == "W5001"
    assert "build_selfhost_dev.py" in err_payload["diagnostics"][0]["message"]


def test_simp_r01_delegates_project_and_schema_with_seed_opt_out(dev_sdk, tmp_path):
    spy, log_path = _make_driver_spy(tmp_path, dev_sdk["driver"])
    spy_env = {"WYSNA_NATIVE_DRIVER": str(spy)} if spy else {}

    created = _run_cli(tmp_path, "project", "new", "demo", "--output-format=json-v1", env_extra=spy_env)
    assert created.returncode == 0, created.stderr + created.stdout
    assert json.loads(created.stdout)["invocation_id"] == "native"

    proj_root = tmp_path / "demo"
    listed = _run_cli(proj_root, "project", "test", "--list", "--output-format=json-v1", env_extra=spy_env)
    assert listed.returncode == 0, listed.stderr + listed.stdout
    listed_payload = json.loads(listed.stdout)
    assert listed_payload["invocation_id"] == "native"
    assert listed_payload["result"]["selected"] == 1

    checked = _run_cli(proj_root, "project", "check", "--output-format=json-v1", env_extra=spy_env)
    assert checked.returncode == 0, checked.stderr + checked.stdout
    assert json.loads(checked.stdout)["invocation_id"] == "native"

    before_seed_lines = len(log_path.read_text(encoding="utf-8").splitlines()) if spy else 0
    seed_opt_out = _run_cli(proj_root, "project", "test", "--list", "--engine=seed", "--output-format=json-v1", env_extra=spy_env)
    assert seed_opt_out.returncode == 0, seed_opt_out.stderr + seed_opt_out.stdout
    assert json.loads(seed_opt_out.stdout)["invocation_id"] != "native"
    if spy:
        assert len(log_path.read_text(encoding="utf-8").splitlines()) == before_seed_lines

    sample = tmp_path / "sample.wysna"
    sample.write_text("pub struct Item { id: int }\n", encoding="utf-8")
    schema_native = _run_cli(tmp_path, "schema", str(sample), "--output-format=json-v1", env_extra=spy_env)
    assert schema_native.returncode == 0, schema_native.stderr + schema_native.stdout
    assert json.loads(schema_native.stdout)["status"] == "ok"
    if spy:
        assert len(log_path.read_text(encoding="utf-8").splitlines()) == before_seed_lines + 1

    schema_seed = _run_cli(
        tmp_path, "schema", str(sample), "--output-format=json-v1",
        env_extra={**spy_env, "WYSNA_DELEGATE_NATIVE": "0"},
    )
    assert schema_seed.returncode == 0, schema_seed.stderr + schema_seed.stdout
    if spy:
        assert len(log_path.read_text(encoding="utf-8").splitlines()) == before_seed_lines + 1


def test_simp_r01_delegates_mcp_and_lsp_to_split_native_units(dev_sdk, tmp_path):
    sample = tmp_path / "ok.wysna"
    sample.write_text("pub fn add(a: int, b: int) -> int { return a + b }\n", encoding="utf-8")
    mcp_requests = "\n".join([
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}}),
        json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "wysna_check", "arguments": {"path": "ok.wysna"}}}),
    ]) + "\n"
    mcp_res = _run_cli(tmp_path, "mcp", "--root", str(tmp_path), input_text=mcp_requests)
    assert mcp_res.returncode == 0, mcp_res.stderr + mcp_res.stdout
    lines = [json.loads(line) for line in mcp_res.stdout.splitlines() if line.strip()]
    assert len(lines) == 2
    assert lines[0]["result"]["serverInfo"]["name"] == "wysna"
    tool_content = json.loads(lines[1]["result"]["content"][0]["text"])
    assert tool_content["status"] == "ok"

    init_body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"rootUri": None}})
    shut_body = json.dumps({"jsonrpc": "2.0", "id": 2, "method": "shutdown", "params": None})
    exit_body = json.dumps({"jsonrpc": "2.0", "method": "exit"})
    lsp_input = "".join(f"Content-Length: {len(body.encode('utf-8'))}\r\n\r\n{body}" for body in (init_body, shut_body, exit_body))
    lsp_res = _run_cli(tmp_path, "lsp", input_text=lsp_input)
    assert lsp_res.returncode == 0, lsp_res.stderr + lsp_res.stdout
    assert '"capabilities"' in lsp_res.stdout
