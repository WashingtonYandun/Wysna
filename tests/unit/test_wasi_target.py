"""End-to-end gate for the first Seed C99 to WASI command target."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from common.WasmModule import inspect_wasm, validate_wasi_command


ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "main.py"


def _invoke(project_root, *arguments):
    return subprocess.run([sys.executable, str(CLI), *arguments], cwd=project_root,
                          capture_output=True, text=True, env=os.environ.copy(), timeout=120)


def _manifest():
    return '''manifest_version = 1

[package]
name = "wasi-smoke"
version = "0.1.0"
language = "0.5"

[toolchain]
compiler = "0.5.0"
stdlib = "bundled"

[sources]
root = "src"

[targets.native]
entry = "src/main.wysna"
profile = "native-cli"

[targets.wasi]
entry = "src/main.wysna"
profile = "wasi-command"
fuel = 100000000
memory_limit_mb = 64

[[targets.wasi.preopens]]
path = "data"
access = "read-only"

[targets.no-files]
entry = "src/main.wysna"
profile = "wasi-command"

[targets.process]
entry = "src/process.wysna"
profile = "wasi-command"

[targets.http]
entry = "src/http.wysna"
profile = "wasi-command"

[targets.loop]
entry = "src/loop.wysna"
profile = "wasi-command"
fuel = 100000
memory_limit_mb = 8
'''


def _assert(condition, message):
    if not condition:
        raise AssertionError(message)


def run():
    sdk_path = os.environ.get("WASI_SDK_PATH")
    _assert(sdk_path and Path(sdk_path).is_dir(),
            "WASI_SDK_PATH must point to the pinned wasi-sdk installation")

    with tempfile.TemporaryDirectory(prefix="wysna-wasi-ci-") as temporary:
        project_root = Path(temporary)
        (project_root / "src").mkdir()
        (project_root / "data").mkdir()
        (project_root / "build").mkdir()
        (project_root / "wysna.toml").write_text(_manifest(), encoding="utf-8")
        (project_root / "data" / "input.txt").write_text("fixture\n", encoding="utf-8")
        (project_root / "src" / "main.wysna").write_text('''import "std/environment"
import "std/fs"
import "std/json"

fn main() -> unit {
    let arguments = environment.arguments()
    print("ARG", arguments[1])
    let document = json.parse("{\\"number\\":18446744073709551615,\\"text\\":\\"Wysna 🚀\\"}") else error {
        print("JSON-ERROR")
        return
    }
    let encoded = json.stringify(document) else error {
        print("JSON-ERROR")
        return
    }
    print("JSON", encoded)
    let present = fs.exists("data/input.txt") else err {
        print("FILE-DENIED")
        return
    }
    if present { print("FILE-OK") } else { print("FILE-MISSING") }
}
''', encoding="utf-8")
        (project_root / "src" / "process.wysna").write_text('''import "std/process"

fn main() -> unit { print("unavailable") }
''', encoding="utf-8")
        (project_root / "src" / "http.wysna").write_text('''import "std/http"

fn main() -> unit { print("unavailable") }
''', encoding="utf-8")
        (project_root / "src" / "write_check.wysna").write_text('''import "std/fs"

fn main() -> unit {
    fs.remove("data/input.txt") else err {
        print("WRITE-DENIED")
        return
    }
    print("WRITE-ALLOWED")
}
''', encoding="utf-8")
        (project_root / "src" / "loop.wysna").write_text('''fn main() -> unit {
    while true { }
}
''', encoding="utf-8")

        native_build = _invoke(project_root, "project", "build", "--manifest",
                               str(project_root / "wysna.toml"), "--target", "native",
                               "--output-format=json-v1", "-o", str(project_root / "build" / "native"))
        _assert(native_build.returncode == 0, "native comparison build failed: " + native_build.stderr)
        native_result = json.loads(native_build.stdout)
        native_path = Path(native_result["artifacts"][0]["path"])
        native_run = subprocess.run([str(native_path), "proof"], cwd=project_root,
                                    capture_output=True, text=True, timeout=30)
        _assert(native_run.returncode == 0, "native comparison executable failed")

        wasi_build = _invoke(project_root, "project", "build", "--manifest",
                             str(project_root / "wysna.toml"), "--target", "wasi",
                             "--output-format=json-v1", "-o", str(project_root / "build" / "app"))
        _assert(wasi_build.returncode == 0, "WASI build failed: " + wasi_build.stdout + wasi_build.stderr)
        build_result = json.loads(wasi_build.stdout)
        _assert(build_result["target"] == {"profile": "wasi-command", "triple": "wasm32-wasip1"},
                "WASI target metadata is incomplete")
        artifact = next(item for item in build_result["artifacts"] if item["kind"] == "wasm_module")
        wasm_path = Path(artifact["path"])
        _assert(wasm_path.suffix == ".wasm" and wasm_path.read_bytes().startswith(b"\x00asm\x01\x00\x00\x00"),
                "WASI build did not produce a core WebAssembly module")
        provenance_artifact = next(item for item in build_result["artifacts"]
                                   if item["kind"] == "build_provenance")
        provenance = json.loads(provenance_artifact["content"])
        _assert(provenance["wasi"]["stack_size_bytes"] == 1_048_576,
                "WASI build provenance does not record the reserved guest stack")
        module_info = inspect_wasm(wasm_path)
        validate_wasi_command(module_info)
        _assert(all(item["module"] == "wasi_snapshot_preview1" for item in module_info.imports),
                "WASI module contains a non-WASI host import")

        report_path = project_root / "build" / "run-report.json"
        wasi_run = _invoke(project_root, "project", "run", "--manifest",
                           str(project_root / "wysna.toml"), "--target", "wasi",
                           "--report-json", str(report_path), "--", "proof")
        if not report_path.is_file():
            report_detail = "run report was not written"
        else:
            report_detail = report_path.read_text(encoding="utf-8")
        _assert(wasi_run.returncode == 0,
                f"WASI execution failed (exit={wasi_run.returncode}, "
                f"stdout={wasi_run.stdout!r}, stderr={wasi_run.stderr!r}, "
                f"report={report_detail})")
        run_report = json.loads(report_path.read_text(encoding="utf-8"))
        _assert(run_report["execution"]["kind"] == "wasm", "WASI execution evidence is missing")
        _assert(run_report["execution"]["fuel_remaining"] < 100_000_000,
                "WASI fuel accounting was not applied")
        _assert(wasi_run.stdout == native_run.stdout,
                f"native/WASI output differs: {native_run.stdout!r} != {wasi_run.stdout!r}")

        write_report_path = project_root / "build" / "write-report.json"
        write_run = _invoke(project_root, "project", "run", "--manifest",
                            str(project_root / "wysna.toml"), "--target", "wasi",
                            "--entry", "src/write_check.wysna", "--report-json",
                            str(write_report_path))
        _assert(write_run.returncode == 0 and "WRITE-DENIED" in write_run.stdout,
                "read-only preopen did not reject a guest write")
        _assert((project_root / "data" / "input.txt").read_text(encoding="utf-8") == "fixture\n",
                "read-only preopen changed the host file")

        for target_name in ("no-files", "process", "http"):
            result = _invoke(project_root, "project", "build", "--manifest",
                             str(project_root / "wysna.toml"), "--target", target_name,
                             "--output-format=json-v1", "-o", str(project_root / "build" / target_name))
            _assert(result.returncode == 1, f"unsupported target '{target_name}' was not rejected")
            diagnostic = json.loads(result.stdout)["diagnostics"][0]
            _assert(diagnostic["code"] == "W2013",
                    f"unsupported target '{target_name}' did not produce a capability diagnostic")

        loop_report_path = project_root / "build" / "loop-report.json"
        loop_run = _invoke(project_root, "project", "run", "--manifest",
                           str(project_root / "wysna.toml"), "--target", "loop",
                           "--report-json", str(loop_report_path))
        _assert(loop_run.returncode == 5, "fuel-limited loop did not trap")
        loop_report = json.loads(loop_report_path.read_text(encoding="utf-8"))
        _assert(loop_report["status"] == "execution_error",
                "fuel exhaustion was not reported as a guest execution error")


if __name__ == "__main__":
    run()
    print("WASI target integration checks passed")
