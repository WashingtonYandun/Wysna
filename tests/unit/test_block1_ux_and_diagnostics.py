"""Block 1 tests: native CLI ergonomics, run-cache, human diagnostics, and runtime panics."""

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from common.Platform import compiler_command, executable_name
from compiler.Bootstrap import ensure_selfhost_compiler_binary
from tools.package_native import COMPONENTS, host_platform

ROOT = Path(__file__).resolve().parents[2]


def _compile_driver(tmp_path: Path) -> Path:
    binary = tmp_path / executable_name("wysna")
    target_triple = COMPONENTS["zig"][host_platform()]["target"]
    flags = [
        "-std=c99",
        f"-I{ROOT}",
        '-DWYSNA_NATIVE_VERSION="1.1.0-test"',
        f'-DWYSNA_NATIVE_TARGET="{target_triple}"',
        '-DWYSNA_NATIVE_STACK_LINK_ARGUMENT=""',
        str(ROOT / "packaging/native/driver.c"),
        str(ROOT / "runtime/wysna_runtime_lib.c"),
        "-o",
        str(binary),
    ]
    if os.name != "nt":
        flags += ["-lm"]
        if os.uname().sysname == "Linux":
            flags += ["-pthread", "-DWYSNA_LINK_PTHREAD"]
    res = subprocess.run(compiler_command() + flags, cwd=ROOT, capture_output=True, text=True, timeout=120)
    assert res.returncode == 0, res.stderr
    return binary


def _find_cached_zig() -> Path | None:
    cache_home = os.environ.get("WYSNA_CACHE_HOME")
    candidates = []
    if cache_home:
        candidates.append(Path(cache_home))
    candidates.append(Path.home() / ".cache/wysna")
    candidates.append(Path(os.environ.get("LOCALAPPDATA", "")) / "Wysna")
    for base in candidates:
        if not base.exists():
            continue
        for match in base.rglob(executable_name("zig")):
            if match.is_file():
                return match
    installed = Path.home() / ".local/share/wysna/toolchain/zig" / executable_name("zig")
    if installed.is_file():
        return installed
    return None


def _compile_and_run_wysna(tmp_path: Path, compiler: str, name: str, source_text: str, *, extra_env: dict[str, str] | None = None):
    src = tmp_path / f"{name}.wysna"
    c_file = tmp_path / f"{name}.c"
    exe = tmp_path / executable_name(name)
    src.write_text(source_text, encoding="utf-8")
    comp = subprocess.run([compiler, str(src), str(c_file)], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert comp.returncode == 0, comp.stderr
    math_flags = [] if os.name == "nt" else ["-lm"]
    built = subprocess.run(
        compiler_command() + ["-std=c99", f"-I{ROOT}", str(c_file)] + math_flags + ["-o", str(exe)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert built.returncode == 0, built.stderr
    run_env = os.environ.copy()
    if extra_env:
        run_env.update(extra_env)
    return subprocess.run([str(exe)], cwd=tmp_path, env=run_env, capture_output=True, text=True, timeout=30)


def test_driver_help_repl_and_equals_flag_parsing(tmp_path):
    driver = _compile_driver(tmp_path)

    no_args = subprocess.run([str(driver)], capture_output=True, text=True, timeout=15)
    assert no_args.returncode == 2
    assert "Usage: wysna" in no_args.stdout

    help_run = subprocess.run([str(driver), "run", "--help"], capture_output=True, text=True, timeout=15)
    assert help_run.returncode == 0
    assert "Usage: wysna" in help_run.stdout

    repl_text = subprocess.run([str(driver), "repl"], capture_output=True, text=True, timeout=15)
    assert repl_text.returncode == 2
    assert "[W2013]" in repl_text.stderr
    assert "Interactive REPL is not available" in repl_text.stderr

    repl_json = subprocess.run([str(driver), "repl", "--output-format=json-v1"], capture_output=True, text=True, timeout=15)
    assert repl_json.returncode == 2
    payload = json.loads(repl_json.stdout)
    assert payload["diagnostics"][0]["code"] == "W2013"

    bad_flag = subprocess.run([str(driver), "check", "x.wysna", "--nonexistent"], capture_output=True, text=True, timeout=15)
    assert bad_flag.returncode == 2
    assert "[W5002]" in bad_flag.stderr
    assert "Unknown or invalid option" in bad_flag.stderr

    # Verify --profile=release is accepted by sdk_parse for check -> rejected cleanly by semantic rule only if wrong command,
    # and accepted cleanly by build/run source check.
    missing_src = tmp_path / "does_not_exist.wysna"
    eq_flag = subprocess.run(
        [str(driver), "run", "--profile=release", "--timeout-ms=5000", str(missing_src)],
        capture_output=True,
        text=True,
        timeout=15,
    )
    assert eq_flag.returncode == 1
    assert "[W5001]" in eq_flag.stderr


def _setup_test_sdk(tmp_path: Path) -> Path:
    zig = _find_cached_zig()
    if zig is None:
        pytest.skip("No cached or installed zig binary found locally")
    compiler = Path(ensure_selfhost_compiler_binary(str(ROOT)))
    sdk_root = tmp_path / "sdk"
    (sdk_root / "bin").mkdir(parents=True)
    (sdk_root / "libexec").mkdir(parents=True)
    (sdk_root / "toolchain/zig").mkdir(parents=True)
    (sdk_root / "runtime").mkdir(parents=True)
    shutil.copytree(ROOT / "stdlib", sdk_root / "stdlib")
    for hdr in (ROOT / "runtime").iterdir():
        if hdr.is_file() and hdr.suffix in (".h", ".def"):
            shutil.copy2(hdr, sdk_root / "runtime" / hdr.name)

    driver = sdk_root / "bin" / executable_name("wysna")
    shutil.copy2(_compile_driver(tmp_path), driver)
    shutil.copy2(compiler, sdk_root / "libexec" / executable_name("wysna-compiler"))
    zig_dst = sdk_root / "toolchain/zig" / executable_name("zig")
    if os.name == "nt":
        shutil.copy2(zig, zig_dst)
    else:
        os.symlink(zig, zig_dst)
    return driver


def test_driver_run_cache_reuses_and_invalidates_compiled_binary(tmp_path):
    driver = _setup_test_sdk(tmp_path)

    cache_home = tmp_path / "cache"
    env = dict(os.environ, WYSNA_CACHE_HOME=str(cache_home))
    script = tmp_path / "hello.wysna"
    script.write_text('fn main() {\n  print("v1")\n}\n', encoding="utf-8")

    first = subprocess.run([str(driver), "run", "--profile=dev", str(script)], env=env, capture_output=True, text=True, timeout=120)
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == "v1"

    run_cache_dir = cache_home / "run-cache"
    cached_bins = [p for p in run_cache_dir.iterdir() if p.is_file()]
    assert len(cached_bins) == 1

    # Corrupt/remove libexec compiler briefly or verify cache hit runs without invoking compiler.
    # Replace compiler symlink/file with failing stub? Better: verify exact cached binary stays valid & returns v1,
    # then modify source and verify invalidation produces v2 and a second cached artifact.
    second = subprocess.run([str(driver), "run", "--profile=dev", str(script)], env=env, capture_output=True, text=True, timeout=30)
    assert second.returncode == 0, second.stderr
    assert second.stdout.strip() == "v1"
    assert len([p for p in run_cache_dir.iterdir() if p.is_file()]) == 1

    script.write_text('fn main() {\n  print("v2")\n}\n', encoding="utf-8")
    third = subprocess.run([str(driver), "run", "--profile=dev", str(script)], env=env, capture_output=True, text=True, timeout=120)
    assert third.returncode == 0, third.stderr
    assert third.stdout.strip() == "v2"
    assert len([p for p in run_cache_dir.iterdir() if p.is_file()]) == 2


def test_selfhost_diagnostics_emit_human_help_and_identifier_suggestions(tmp_path):
    compiler = ensure_selfhost_compiler_binary(str(ROOT))

    typo_src = tmp_path / "typo.wysna"
    typo_src.write_text('fn main() {\n  let counter = 1\n  print(coutner)\n}\n', encoding="utf-8")
    typo_res = subprocess.run([compiler, str(typo_src), str(tmp_path / "typo.c")], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert typo_res.returncode != 0
    assert "[W1001]" in typo_res.stderr
    assert "unknown identifier: coutner" in typo_res.stderr
    assert "help: Did you mean 'counter'?" in typo_res.stderr

    typo_json = subprocess.run(
        [compiler, "check", str(typo_src), "--output-format=json-v1"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert typo_json.returncode != 0
    parsed = json.loads(typo_json.stdout)
    assert parsed["diagnostics"][0]["data"].get("help") == "Did you mean 'counter'?"

    mismatch_src = tmp_path / "mismatch.wysna"
    mismatch_src.write_text('fn main() {\n  let x: int = 1.5\n}\n', encoding="utf-8")
    mismatch_res = subprocess.run([compiler, str(mismatch_src), str(tmp_path / "mismatch.c")], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert mismatch_res.returncode != 0
    assert "help: Convert explicitly using int(...) or float(...)" in mismatch_res.stderr

    mut_src = tmp_path / "mut.wysna"
    mut_src.write_text('fn main() {\n  let xs = [1]\n  xs.append(2)\n}\n', encoding="utf-8")
    mut_res = subprocess.run([compiler, str(mut_src), str(tmp_path / "mut.c")], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert mut_res.returncode != 0
    assert "help: Declare 'xs' as mutable: 'let mut xs'." in mut_res.stderr


def test_runtime_panic_messages_include_context_and_os_errors(tmp_path):
    compiler = ensure_selfhost_compiler_binary(str(ROOT))

    list_oob = _compile_and_run_wysna(
        tmp_path,
        compiler,
        "list_oob",
        'fn main() {\n  let xs = [10, 20]\n  print(xs[5])\n}\n',
    )
    assert list_oob.returncode != 0
    assert "[W4002]" in list_oob.stderr
    assert "List index out of bounds (index 5, length 2)" in list_oob.stderr

    dict_miss = _compile_and_run_wysna(
        tmp_path,
        compiler,
        "dict_miss",
        'fn main() {\n  let d: dict[str, int] = {"a": 1}\n  print(d["missing"])\n}\n',
    )
    assert dict_miss.returncode != 0
    assert "[W4002]" in dict_miss.stderr
    assert "Dictionary key not found (use d.get(key) for optional lookup)" in dict_miss.stderr

    io_err = _compile_and_run_wysna(
        tmp_path,
        compiler,
        "io_err",
        'fn main() {\n  match read_file("missing_block1_probe.txt") {\n    Ok(s) => print(s)\n    Err(e) => print(e.message)\n  }\n}\n',
        extra_env={"WYSNA_MEM_CHECK": "1"},
    )
    assert io_err.returncode == 0, io_err.stderr
    assert "Failed to open file 'missing_block1_probe.txt':" in io_err.stdout


def test_driver_run_cache_supports_local_imports_and_invalidates_on_dependency_edit(tmp_path):
    driver = _setup_test_sdk(tmp_path)

    cache_home = tmp_path / "cache"
    env = dict(os.environ, WYSNA_CACHE_HOME=str(cache_home))
    helper = tmp_path / "helper.wysna"
    main_src = tmp_path / "app.wysna"
    helper.write_text('pub fn label() -> str {\n  return "dep-v1"\n}\n', encoding="utf-8")
    main_src.write_text('import "helper.wysna" as h\nfn main() {\n  print(h.label())\n}\n', encoding="utf-8")

    first = subprocess.run([str(driver), "run", "--profile=dev", str(main_src)], env=env, capture_output=True, text=True, timeout=120)
    assert first.returncode == 0, first.stderr
    assert first.stdout.strip() == "dep-v1"
    run_cache_dir = cache_home / "run-cache"
    assert len([p for p in run_cache_dir.iterdir() if p.is_file()]) == 1

    second = subprocess.run([str(driver), "run", "--profile=dev", str(main_src)], env=env, capture_output=True, text=True, timeout=30)
    assert second.returncode == 0, second.stderr
    assert second.stdout.strip() == "dep-v1"
    assert len([p for p in run_cache_dir.iterdir() if p.is_file()]) == 1

    helper.write_text('pub fn label() -> str {\n  return "dep-v2"\n}\n', encoding="utf-8")
    third = subprocess.run([str(driver), "run", "--profile=dev", str(main_src)], env=env, capture_output=True, text=True, timeout=120)
    assert third.returncode == 0, third.stderr
    assert third.stdout.strip() == "dep-v2"
    assert len([p for p in run_cache_dir.iterdir() if p.is_file()]) == 2


def test_seed_checker_diagnostic_parity(tmp_path):
    from tooling.FixCLI import collect_diagnostics

    cases = [
        ("seed_list.wysna", 'fn main() -> unit {\n  let mut xs = [1]\n  xs.push(2)\n}\n', "W3002", "List has no method 'push'"),
        ("seed_dict.wysna", 'fn main() -> unit {\n  let d = {"a": 1}\n  d.missing()\n}\n', "W3002", "Dict has no method 'missing'"),
        ("seed_enum.wysna", 'enum Mode { Fast, Safe }\nfn main() -> unit {\n  let m = Mode.Fast\n  m.get("k")\n}\n', "W3002", "Enum 'Mode' has no member or variant 'get'"),
        ("seed_err.wysna", 'fn f() -> int ! IoError {\n  return IoError { message: "bad" }\n}\n', "W2004", "Wrap the error value with Err(...)"),
        ("seed_u8.wysna", 'fn main() -> unit {\n  let n: int = 12\n  let _b: u8 = n\n}\n', "W2001", "u8(x) ?? 0"),
    ]
    for name, src, exp_code, exp_needle in cases:
        path = tmp_path / name
        diags = collect_diagnostics(str(path), src)
        assert diags, name
        first = diags[0]
        assert first["code"] == exp_code, (name, first)
        blob = first["message"] + " " + str(first.get("data", {}).get("help", ""))
        assert exp_needle in blob, (name, first)
