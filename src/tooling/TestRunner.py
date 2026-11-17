"""Targeted test runner supporting Seed interpretation and native Compiler execution."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from typing import List, Optional

from common.Paths import get_repo_root
from compiler.Bootstrap import ensure_selfhost_compiler_binary
from compiler.NativeToolchain import NativeToolchain


def _panic_expectation(expected_text: str) -> Optional[str]:
    if not expected_text:
        return None
    first = expected_text.strip().splitlines()[0].strip() if expected_text.strip() else ""
    if first.startswith("# RUNTIME_PANIC:"):
        return first[len("# RUNTIME_PANIC:"):].strip()
    return None


def run_single_test_native(
    file_path: Path,
    repo_root: Path,
    selfhost_bin: str,
    timeout_ms: int = 30000,
    sanitize: bool = False,
) -> bool:
    """Compile and execute a test file using the native selfhost compiler binary."""
    toolchain = NativeToolchain(repo_root)
    source_str = file_path.read_text(encoding="utf-8")
    expected_path = file_path.with_suffix(".expected")
    expected_text = expected_path.read_text(encoding="utf-8") if expected_path.is_file() else None
    panic_code = _panic_expectation(expected_text) if expected_text is not None else None

    t0 = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix="wysna-test-native-") as temp_dir:
        temp_bin = Path(temp_dir) / "test_bin"
        try:
            emission = toolchain.emit_compiler(selfhost_bin, str(file_path))
            built = toolchain.build(emission.source, temp_bin, sanitize=sanitize)
        except Exception as exc:
            duration_ms = (time.perf_counter() - t0) * 1000.0
            print(f"[FAIL] {file_path} (native compilation failed in {duration_ms:.1f}ms): {exc}", file=sys.stderr)
            return False

        try:
            res = subprocess.run(
                [built.output_path],
                capture_output=True,
                text=True,
                cwd=str(repo_root),
                timeout=timeout_ms / 1000.0,
            )
        except subprocess.TimeoutExpired:
            print(f"[FAIL] {file_path} (native execution timed out after {timeout_ms}ms)", file=sys.stderr)
            return False

    duration_ms = (time.perf_counter() - t0) * 1000.0
    if panic_code is not None:
        if res.returncode != 0 and panic_code in res.stderr:
            print(f"[PASS] {file_path} (native panic {panic_code}, {duration_ms:.1f}ms)")
            return True
        print(f"[FAIL] {file_path} (expected panic {panic_code}, exit {res.returncode}, stderr: {res.stderr.strip()})", file=sys.stderr)
        return False

    if res.returncode != 0:
        print(f"[FAIL] {file_path} (native execution exited {res.returncode}, stderr: {res.stderr.strip()})", file=sys.stderr)
        return False

    if expected_text is not None and res.stdout != expected_text:
        print(f"[FAIL] {file_path} (native output mismatch: expected {len(expected_text)} bytes, got {len(res.stdout)} bytes)", file=sys.stderr)
        return False

    print(f"[PASS] {file_path} (native, {duration_ms:.1f}ms)")
    return True


def run_single_test_seed(
    file_path: Path,
    repo_root: Path,
    timeout_ms: int = 30000,
) -> bool:
    """Execute a test file using the Seed interpreter pipeline."""
    from tooling.SourceCommand import run_pipeline
    expected_path = file_path.with_suffix(".expected")
    expected_text = expected_path.read_text(encoding="utf-8") if expected_path.is_file() else None
    panic_code = _panic_expectation(expected_text) if expected_text is not None else None

    t0 = time.perf_counter()
    import io
    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()
    old_stdout = sys.stdout
    old_stderr = sys.stderr
    sys.stdout = stdout_buf
    sys.stderr = stderr_buf
    exit_code = 0
    try:
        source = file_path.read_text(encoding="utf-8")
        exit_code = run_pipeline(source, file_name=str(file_path))
    except SystemExit as se:
        exit_code = se.code if isinstance(se.code, int) else 1
    except Exception as exc:
        exit_code = 1
        stderr_buf.write(str(exc))
    finally:
        sys.stdout = old_stdout
        sys.stderr = old_stderr

    duration_ms = (time.perf_counter() - t0) * 1000.0
    out = stdout_buf.getvalue()
    err = stderr_buf.getvalue()

    if panic_code is not None:
        if exit_code != 0 and panic_code in err:
            print(f"[PASS] {file_path} (seed panic {panic_code}, {duration_ms:.1f}ms)")
            return True
        print(f"[FAIL] {file_path} (seed expected panic {panic_code}, exit {exit_code}, err: {err.strip()})", file=sys.stderr)
        return False

    if exit_code != 0:
        print(f"[FAIL] {file_path} (seed exited {exit_code}, err: {err.strip()})", file=sys.stderr)
        return False

    if expected_text is not None and out != expected_text:
        print(f"[FAIL] {file_path} (seed output mismatch)", file=sys.stderr)
        return False

    print(f"[PASS] {file_path} (seed, {duration_ms:.1f}ms)")
    return True


def run_tests(
    targets: List[str],
    engine: str = "seed",
    sanitize: bool = False,
    timeout_ms: int = 30000,
    repo_root: Optional[Path] = None,
) -> int:
    """Entry point for the Wysna CLI test runner."""
    if repo_root is None:
        repo_root = Path(get_repo_root())

    # If no explicit targets and engine is compiler, discover native runtime tests
    if not targets and engine == "compiler":
        import json
        profiles_path = repo_root / "tests/engine_profiles.json"
        unsupported = set()
        if profiles_path.is_file():
            try:
                prof_data = json.loads(profiles_path.read_text(encoding="utf-8"))
                unsupported = set(prof_data.get("unsupported", {}).get("selfhost", []))
            except Exception:
                pass
        runtime_files = sorted((repo_root / "tests/runtime").glob("*.wysna"))
        targets = [
            str(p) for p in runtime_files
            if p.name not in unsupported and p.relative_to(repo_root).as_posix() not in unsupported
        ]

    if not targets:
        print("No test targets to run.")
        return 0

    print("==================================================")
    print(f"Wysna Test Runner [engine={engine}, {len(targets)} targets]")
    print("==================================================")

    selfhost_bin = None
    if engine == "compiler":
        selfhost_bin = ensure_selfhost_compiler_binary(str(repo_root))

    passed = 0
    failed = 0
    total_start = time.perf_counter()

    for target in targets:
        target_path = Path(target)
        if not target_path.is_file():
            print(f"[FAIL] File not found: {target}", file=sys.stderr)
            failed += 1
            continue

        ok = False
        if engine == "compiler":
            ok = run_single_test_native(
                target_path, repo_root, selfhost_bin, timeout_ms=timeout_ms, sanitize=sanitize
            )
        else:
            ok = run_single_test_seed(target_path, repo_root, timeout_ms=timeout_ms)

        if ok:
            passed += 1
        else:
            failed += 1

    total_time = time.perf_counter() - total_start
    print("--------------------------------------------------")
    print(f"Summary: {passed} passed, {failed} failed in {total_time:.2f}s")
    print("==================================================")
    return 0 if failed == 0 else 1
