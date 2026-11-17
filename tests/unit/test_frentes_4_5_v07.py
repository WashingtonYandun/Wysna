"""Regression suite for Frente 4 (concurrent OS-thread spawn, interprocedural CoW, W07 benchmarks) & Frente 5 (native driver multi-file test discovery)."""
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

from bench.run_bench import run_extended_benchmarks
from common.Platform import compiler_command, executable_name
from compiler.CompilerService import CompilerService
from compiler.LlvmToolchain import LlvmToolchain
from main import ensure_selfhost_compiler_binary
from tests.engine_harness import ENGINES, run_engine
from tests.unit.test_mir import lower


ROOT = Path(__file__).resolve().parents[2]


def _run_llvm_with_env(source: str, workdir: Path, env=None) -> subprocess.CompletedProcess:
    service = CompilerService(project_root=ROOT)
    checked = service.check(source, file=str(workdir / "prog.wysna"))
    emission = service.emit_llvm(checked)
    toolchain = LlvmToolchain()
    toolchain.verify(emission.source)
    built = toolchain.build(emission, workdir / "llvm_out")
    proc_env = dict(os.environ if env is None else env)
    return subprocess.run([built.output_path], cwd=ROOT, env=proc_env, capture_output=True, text=True, timeout=30)


class Frentes4And5Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
        cls.service = CompilerService(project_root=ROOT)

    def test_frente4a_spawn_real_concurrency_and_race_and_managed_values_all_engines(self):
        source = '''import "std/async" as a

fn slow_work() -> int {
    let _ = a.wait(a.sleep_ms(60))
    return 111
}

fn fast_work() -> int {
    let _ = a.wait(a.sleep_ms(3))
    return 222
}

fn main() -> unit {
    let t_slow = a.spawn[int](|| => slow_work())
    let t_fast = a.spawn[int](|| => fast_work())
    let winner = a.race[int]([t_slow, t_fast]) else err { -1 }

    let t_list = a.spawn[list[int]](|| => [7, 8, 9])
    let awaited_list = await t_list

    let t_str = a.spawn[str](|| => "spawn-" + "ok")
    let waited_str = a.wait(t_str) else err { "err" }

    let empty_ints: list[int] = []
    let both = a.all[int]([t_slow, t_fast]) else err { empty_ints }
    print("RACE", winner, "LIST", len(awaited_list), awaited_list[2], "STR", waited_str, "ALL", both[0], both[1])
}

main()
'''
        prev_mem = os.environ.get("WYSNA_MEM_CHECK")
        os.environ["WYSNA_MEM_CHECK"] = "1"
        try:
            with tempfile.TemporaryDirectory(prefix="wysna-f4-spawn-") as tmpdir:
                tmp = Path(tmpdir)
                for engine in ENGINES:
                    sanitize = engine in ("ast", "hir", "selfhost") and os.name != "nt"
                    res = run_engine(engine, source, "spawn_concurrent", selfhost_bin=self.selfhost_bin,
                                     directory=tmp / engine, sanitize=sanitize)
                    self.assertEqual(res.exit_code, 0, f"{engine}: {res.stderr}")
                    self.assertEqual(
                        res.stdout,
                        "RACE 222 LIST 3 9 STR spawn-ok ALL 111 222\n",
                        f"{engine}: {res.stdout!r}",
                    )
                llvm_res = _run_llvm_with_env(source, tmp / "llvm")
                self.assertEqual(llvm_res.returncode, 0, llvm_res.stderr)
                self.assertEqual(llvm_res.stdout, "RACE 222 LIST 3 9 STR spawn-ok ALL 111 222\n")
        finally:
            if prev_mem is None:
                os.environ.pop("WYSNA_MEM_CHECK", None)
            else:
                os.environ["WYSNA_MEM_CHECK"] = prev_mem

    def test_frente4a_spawn_abandoned_loser_cleans_up_with_zero_leaks(self):
        source = '''import "std/async" as a

fn loser_work() -> str {
    let _ = a.wait(a.sleep_ms(30))
    return "loser-" + "heap"
}

fn main() -> unit {
    let slow = a.spawn[str](|| => loser_work())
    let fast = a.spawn[str](|| => "winner-" + "heap")
    let first = a.race[str]([slow, fast]) else err { "fail" }
    print(first)
}

main()
'''
        prev_mem = os.environ.get("WYSNA_MEM_CHECK")
        os.environ["WYSNA_MEM_CHECK"] = "1"
        try:
            with tempfile.TemporaryDirectory(prefix="wysna-f4-loser-") as tmpdir:
                for engine in ("hir", "selfhost"):
                    sanitize = os.name != "nt"
                    res = run_engine(engine, source, "spawn_loser", selfhost_bin=self.selfhost_bin,
                                     directory=Path(tmpdir) / engine, sanitize=sanitize)
                    self.assertEqual(res.exit_code, 0, f"{engine}: {res.stderr}")
                    self.assertEqual(res.stdout.strip(), "winner-heap")
        finally:
            if prev_mem is None:
                os.environ.pop("WYSNA_MEM_CHECK", None)
            else:
                os.environ["WYSNA_MEM_CHECK"] = prev_mem

    def test_frente4b_interprocedural_cow_eliminates_read_clones_and_detaches(self):
        source = '''fn summarize(items: list[int]) -> int {
    return len(items) + items[0]
}

fn forward_read(items: list[int]) -> int {
    return summarize(items) + 1
}

fn main() -> unit {
    let mut data = [1, 2, 3, 4]
    if forward_read(data) > 0 {
        data.append(5)
    }
    print(len(data), forward_read(data))
}

main()
'''
        module, _ = lower(source, optimize=True)
        by_name = {fn.name: fn for fn in module.functions}
        for name in ("wysna_fn_summarize", "wysna_fn_forward_read"):
            clones = [op for b in by_name[name].blocks for op in b.operations if op.opcode == "clone"]
            self.assertEqual(clones, [], f"{name} retained redundant clones: {clones}")

        main_clones = [
            op for b in by_name["wysna_fn_main"].blocks for op in b.operations
            if op.opcode == "clone"
        ]
        self.assertEqual(main_clones, [], f"main retained call clones: {main_clones}")

        # Verify actual native execution preserves semantic equivalence and triggers zero CoW list detaches.
        with tempfile.TemporaryDirectory(prefix="wysna-f4-cow-") as tmpdir:
            tmp = Path(tmpdir)
            checked = self.service.check(source, file=str(tmp / "cow.wysna"))
            c_src = self.service.emit_c(checked).source
            c_path = tmp / "cow.c"
            bin_path = tmp / executable_name("cow")
            c_path.write_text(c_src, encoding="utf-8")
            math_flags = [] if os.name == "nt" else ["-lm"]
            subprocess.run(compiler_command() + ["-std=c99", "-O2", f"-I{ROOT}", str(c_path), *math_flags, "-o", str(bin_path)],
                           check=True, capture_output=True, text=True)
            res = subprocess.run([str(bin_path)],
                                 env={**os.environ, "WYSNA_COW_METRICS": "1", "WYSNA_MEM_CHECK": "1"},
                                 capture_output=True, text=True, timeout=15)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(res.stdout, "5 7\n")
            self.assertIn("detach_list=0", res.stderr)
            self.assertIn("copied_slots=0", res.stderr)

    def test_frente4b_snapshot_preserved_when_source_mutates_across_blocks(self):
        source = '''fn read_len(xs: list[int]) -> int { return len(xs) }
fn main() -> unit {
    let mut xs = [1, 2]
    let snap = xs
    xs.append(3)
    print(read_len(snap), read_len(xs))
}
main()
'''
        with tempfile.TemporaryDirectory(prefix="wysna-f4-snap-") as tmpdir:
            res = run_engine("hir", source, "snap", selfhost_bin=self.selfhost_bin, directory=Path(tmpdir))
            self.assertEqual(res.exit_code, 0, res.stderr)
            self.assertEqual(res.stdout, "2 3\n")

    def test_frente4b_w07_extended_memory_hash_and_wval64_benchmarks(self):
        ext = run_extended_benchmarks(str(ROOT), self.selfhost_bin)
        names = {p["name"] for p in ext["programs"]}
        self.assertEqual(names, {"memory_pressure.wysna", "dict_hash_cache.wysna"})
        self.assertIsNotNone(ext["wval64"])
        self.assertIn("sizeof(WVal)=8 sizeof(WVal64)=8", ext["wval64"])
        self.assertIn("sum_match=1", ext["wval64"])

    @unittest.skipIf(os.name == "nt", "POSIX shell compiler shim used in unit harness")
    def test_frente5_native_driver_multi_file_test_directory_and_expected_parity(self):
        with tempfile.TemporaryDirectory(prefix="wysna-f5-sdk-") as tmpdir:
            sdk = Path(tmpdir) / "sdk"
            (sdk / "bin").mkdir(parents=True)
            (sdk / "libexec").mkdir(parents=True)
            (sdk / "toolchain" / "zig").mkdir(parents=True)
            os.symlink(ROOT / "stdlib", sdk / "stdlib")
            os.symlink(ROOT / "runtime", sdk / "runtime")
            shutil.copy2(self.selfhost_bin, sdk / "libexec" / "wysna-compiler")

            # Host C compiler shim matching `zig cc -target ... -mcpu=baseline ...` invocation in driver.c
            cc_cmd = compiler_command()[0]
            shim = sdk / "toolchain" / "zig" / "zig"
            shim.write_text(f'''#!/usr/bin/env python3
import subprocess, sys
args = sys.argv[1:]
if args and args[0] == "cc":
    args = args[1:]
out = []
skip = False
for a in args:
    if skip:
        skip = False
        continue
    if a == "-target":
        skip = True
        continue
    if a.startswith("-mcpu="):
        continue
    out.append(a)
raise SystemExit(subprocess.run([{cc_cmd!r}] + out).returncode)
''', encoding="utf-8")
            shim.chmod(shim.stat().st_mode | stat.S_IXUSR)

            driver_bin = sdk / "bin" / "wysna"
            subprocess.run(
                compiler_command() + [
                    "-std=c99", "-O1",
                    '-DWYSNA_NATIVE_VERSION="0.7.0"',
                    '-DWYSNA_NATIVE_TARGET="host"',
                    f"-I{ROOT}",
                    str(ROOT / "packaging/native/driver.c"),
                    "-lm",
                    "-o", str(driver_bin),
                ],
                check=True, capture_output=True, text=True,
            )

            tests_dir = Path(tmpdir) / "suite"
            tests_dir.mkdir()
            (tests_dir / "01_plain.wysna").write_text("print(1 + 2)\n", encoding="utf-8")
            (tests_dir / "02_expected.wysna").write_text('print("hello-native")\n', encoding="utf-8")
            (tests_dir / "02_expected.expected").write_text("hello-native\n", encoding="utf-8")
            (tests_dir / "03_panic.wysna").write_text("let a = [1]\nprint(a[5])\n", encoding="utf-8")
            (tests_dir / "03_panic.expected").write_text("# RUNTIME_PANIC: W4002\n", encoding="utf-8")

            ok_run = subprocess.run([str(driver_bin), "test", str(tests_dir)],
                                    capture_output=True, text=True, timeout=120)
            self.assertEqual(ok_run.returncode, 0, ok_run.stdout + ok_run.stderr)
            self.assertIn("01_plain.wysna (native)", ok_run.stdout)
            self.assertIn("02_expected.wysna (native)", ok_run.stdout)
            self.assertIn("03_panic.wysna (native panic W4002)", ok_run.stdout)
            self.assertIn("Ran 3 test(s): 3 passed, 0 failed.", ok_run.stdout)

            # Introduce a failing expectation and confirm non-zero exit code
            (tests_dir / "04_bad.wysna").write_text("print(42)\n", encoding="utf-8")
            (tests_dir / "04_bad.expected").write_text("99\n", encoding="utf-8")
            bad_run = subprocess.run([str(driver_bin), "test", str(tests_dir)],
                                     capture_output=True, text=True, timeout=120)
            self.assertNotEqual(bad_run.returncode, 0)
            self.assertIn("[FAIL]", bad_run.stderr)
            self.assertIn("Ran 4 test(s): 3 passed, 1 failed.", bad_run.stdout)


if __name__ == "__main__":
    unittest.main()
