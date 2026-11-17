"""Verification tests for Phase 1: Runtime C99 capability sandbox and incremental session caching."""

from pathlib import Path
import io
import os
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _compile_and_run_c(tmp_path: Path, c_code: str, defines: list[str], *, strict_warnings: bool = False):
    c_path = tmp_path / "sandbox_prog.c"
    bin_path = tmp_path / "sandbox_prog"
    c_path.write_text(c_code, encoding="utf-8")
    warning_flags = (
        ["-Wall", "-Wextra", "-Wno-unused-function", "-Werror"]
        if strict_warnings
        else ["-Wno-unused-value"]
    )
    flags = [
        os.environ.get("CC", "cc"),
        "-std=c99",
        *warning_flags,
        "-fsanitize=address,undefined",
        "-fno-omit-frame-pointer",
        "-I" + str(ROOT),
        *defines,
        str(c_path),
        "-lpthread",
        "-lm",
        "-o",
        str(bin_path),
    ]
    compiled = subprocess.run(flags, capture_output=True, text=True, timeout=60)
    assert compiled.returncode == 0, compiled.stderr
    env = dict(os.environ, ASAN_OPTIONS="detect_leaks=0", UBSAN_OPTIONS="halt_on_error=1")
    return subprocess.run([str(bin_path)], capture_output=True, text=True, timeout=30, env=env)


def test_runtime_c_sandbox_denies_every_guarded_capability(tmp_path):
    """Every WYSNA_DENY_CAP_* guard compiles under -Werror and deterministically traps with W5006."""
    from compiler.CompilerService import CompilerService

    cases = [
        ("stdio", "-DWYSNA_DENY_CAP_STDIO=1", 'print("should-fail")\n'),
        ("arguments", "-DWYSNA_DENY_CAP_ARGUMENTS=1", "let a = args()\nprint(len(a))\n"),
        ("environment", "-DWYSNA_DENY_CAP_ENVIRONMENT=1", 'let v = env("PATH")\nprint(v)\n'),
        ("clock", "-DWYSNA_DENY_CAP_CLOCK=1", "let t = unix_time()\nprint(t)\n"),
        (
            "filesystem.read",
            "-DWYSNA_DENY_CAP_FILESYSTEM_READ=1",
            'let r = read_file("missing.txt")\nprint(r)\n',
        ),
        (
            "filesystem.write",
            "-DWYSNA_DENY_CAP_FILESYSTEM_WRITE=1",
            'let w = write_file("forbidden.txt", "x")\nprint(w)\n',
        ),
        (
            "process-control",
            "-DWYSNA_DENY_CAP_PROCESS_CONTROL=1",
            "exit(0)\n",
        ),
    ]
    service = CompilerService(project_root=str(tmp_path))
    for cap_name, macro_flag, source in cases:
        checked = service.check(source, str(tmp_path / f"{cap_name.replace('.', '_')}.wysna"))
        c_code = service.emit_c(checked).source
        res = _compile_and_run_c(tmp_path, c_code, ["-DWYSNA_SANDBOX_ENFORCED=1", macro_flag])
        assert res.returncode == 1, (cap_name, res.returncode, res.stderr)
        assert "[W5006]" in res.stderr, (cap_name, res.stderr)
        assert cap_name in res.stderr, (cap_name, res.stderr)

    # Host-level network & process & native-ffi traps verified directly through C runtime bridge
    host_cases = [
        (
            "network",
            "-DWYSNA_DENY_CAP_NETWORK=1",
            "void* b = wysna_host_buffer_new();\n"
            "(void)wysna_host_http_async_start(b, b, b, b, 100, 1024);\n",
        ),
        (
            "processes",
            "-DWYSNA_DENY_CAP_PROCESSES=1",
            "void* b = wysna_host_buffer_new();\n"
            "(void)wysna_host_process_run(b, b, 1000);\n",
        ),
        (
            "filesystem.read",
            "-DWYSNA_DENY_CAP_FILESYSTEM_READ=1",
            "void* b = wysna_host_buffer_new();\n"
            "(void)wysna_host_fs(b, NULL, 0);\n",
        ),
        (
            "filesystem.write",
            "-DWYSNA_DENY_CAP_FILESYSTEM_WRITE=1",
            "void* b = wysna_host_buffer_new();\n"
            "(void)wysna_host_fs(b, NULL, 2);\n",
        ),
        (
            "clock",
            "-DWYSNA_DENY_CAP_CLOCK=1",
            "(void)wysna_host_monotonic_ms();\n",
        ),
        (
            "environment",
            "-DWYSNA_DENY_CAP_ENVIRONMENT=1",
            "void* b = wysna_host_buffer_new();\n"
            "(void)wysna_host_getcwd(b);\n",
        ),
    ]
    for cap_name, macro_flag, snippet in host_cases:
        c_prog = f'#include "runtime/wysna_runtime.h"\nint main(void) {{\n{snippet}\nreturn 0;\n}}\n'
        res = _compile_and_run_c(tmp_path, c_prog, ["-DWYSNA_SANDBOX_ENFORCED=1", macro_flag], strict_warnings=True)
        assert res.returncode == 1, (cap_name, res.returncode, res.stderr)
        assert "[W5006]" in res.stderr and cap_name in res.stderr, (cap_name, res.stderr)

    # Also verify native-ffi wrapper guard actually traps with W5006 when WYSNA_DENY_CAP_NATIVE_FFI is enabled
    ffi_checked = service.check(
        'extern "C" fn fabs(x: float) -> float\nprint(fabs(-3.5))\n',
        str(tmp_path / "ffi_guard.wysna"),
    )
    ffi_c = service.emit_c(ffi_checked).source
    assert "WYSNA_DENY_CAP_NATIVE_FFI" in ffi_c
    ffi_res = _compile_and_run_c(tmp_path, ffi_c, ["-DWYSNA_SANDBOX_ENFORCED=1", "-DWYSNA_DENY_CAP_NATIVE_FFI=1"])
    assert ffi_res.returncode == 1, ffi_res.stderr
    assert "[W5006]" in ffi_res.stderr and "native-ffi" in ffi_res.stderr, ffi_res.stderr

    # Verify spawn scheduler still joins cleanly when WYSNA_DENY_CAP_CLOCK is enabled
    spawn_src = (
        'import "std/async" as a\n'
        "let t = a.spawn[int](|| -> int { return 21 + 21 })\n"
        "let r = a.wait[int](t) else err { 0 }\n"
        "print(r)\n"
    )
    spawn_checked = service.check(spawn_src, str(tmp_path / "spawn_sandbox.wysna"))
    spawn_c = service.emit_c(spawn_checked).source
    spawn_res = _compile_and_run_c(tmp_path, spawn_c, ["-DWYSNA_SANDBOX_ENFORCED=1", "-DWYSNA_DENY_CAP_CLOCK=1"])
    assert spawn_res.returncode == 0, spawn_res.stderr
    assert spawn_res.stdout.strip() == "42"


def test_incremental_session_reuses_unchanged_modules_and_applies_interface_cutoff(tmp_path):
    """Editing entry leaves imports cached, and non-interface dependency edits skip rechecking dependents."""
    from compiler.CompilationSession import CompilationSession
    from error.Error import WysnaError
    from runtime.Interpreter import Interpreter

    shared = tmp_path / "shared.wysna"
    sibling = tmp_path / "sibling.wysna"
    importer = tmp_path / "importer.wysna"
    entry = tmp_path / "main.wysna"

    shared.write_text("pub fn factor() -> int { return 2 }\n", encoding="utf-8")
    sibling.write_text("pub fn bonus() -> int { return 10 }\n", encoding="utf-8")
    importer.write_text(
        'import "shared.wysna" as s\npub fn compute(x: int) -> int { return x * s.factor() }\n',
        encoding="utf-8",
    )
    main_v1 = (
        'import "importer.wysna" as imp\n'
        'import "sibling.wysna" as sib\n'
        "print(imp.compute(5) + sib.bonus())\n"
    )
    entry.write_text(main_v1, encoding="utf-8")

    session = CompilationSession(str(tmp_path))
    u1 = session.check_source(main_v1, str(entry))
    assert session.check_counts[str(shared.resolve())] == 1
    assert session.check_counts[str(sibling.resolve())] == 1
    assert session.check_counts[str(importer.resolve())] == 1
    assert session.check_counts[str(entry.resolve())] == 1

    # 1. Edit only main.wysna: all imported modules must be reused without re-parse/re-check.
    main_v2 = (
        'import "importer.wysna" as imp\n'
        'import "sibling.wysna" as sib\n'
        "print(imp.compute(6) + sib.bonus())\n"
    )
    u2 = session.check_source(main_v2, str(entry))
    assert u2.generation == u1.generation + 1
    assert session.check_counts[str(shared.resolve())] == 1
    assert session.check_counts[str(sibling.resolve())] == 1
    assert session.check_counts[str(importer.resolve())] == 1
    assert session.check_counts[str(entry.resolve())] == 2

    out = io.StringIO()
    Interpreter(stdout=out).interpret(u2.program)
    assert out.getvalue().strip() == "22"

    # 2. Edit shared.wysna body WITHOUT changing public interface signature -> cutoff prevents rechecking importer/main.
    shared.write_text("pub fn factor() -> int { return 3 }\n", encoding="utf-8")
    cutoff_before = session.interface_cutoff_hits
    u3 = session.check_source(main_v2, str(entry))
    assert session.interface_cutoff_hits > cutoff_before
    assert session.check_counts[str(shared.resolve())] == 2
    assert session.check_counts[str(sibling.resolve())] == 1
    assert session.check_counts[str(importer.resolve())] == 1
    assert session.check_counts[str(entry.resolve())] == 2

    out3 = io.StringIO()
    Interpreter(stdout=out3).interpret(u3.program)
    assert out3.getvalue().strip() == "28"

    # Linking old generation u2 must still fail W9999.
    with pytest.raises(WysnaError) as exc:
        session.link(u2.program)
    assert exc.value.code == "W9999"

    # 3. Edit shared.wysna PUBLIC signature (int -> str) -> invalidates importer and catches error in main/importer.
    shared.write_text('pub fn factor() -> str { return "three" }\n', encoding="utf-8")
    with pytest.raises(WysnaError):
        session.check_source(main_v2, str(entry))
