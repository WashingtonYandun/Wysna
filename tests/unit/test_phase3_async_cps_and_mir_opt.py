"""Phase 3 tests: stackless CPS async fn coroutines and MIR scalar/loop optimizations."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from compiler.CompilerService import CompilerService
from compiler.LlvmToolchain import LlvmToolchain
from main import ensure_selfhost_compiler_binary
from tests.engine_harness import ENGINES, compare, run_engine


def _llvm_available() -> bool:
    try:
        LlvmToolchain()
        return True
    except Exception:
        return False


CONCURRENT_ASYNC_SOURCE = '''import "std/async"

async fn worker(label: str, delay_ms: int, base: int) -> int {
    print("START " + label)
    let _ = await async.sleep_ms(delay_ms)
    print("DONE " + label)
    return base + 10
}

async fn main_task() -> int {
    let t1 = worker("a", 50, 1)
    let t2 = worker("b", 90, 2)
    let t3 = worker("c", 130, 3)
    print("CREATED")
    let vals = async.all([t1, t2, t3]) else err {
        print("ERR " + err)
        return 0
    }
    let winner = async.race([worker("slow", 220, 90), worker("fast", 25, 7)]) else err {
        print("RACE_ERR " + err)
        return 0
    }
    print("SUM " + str(vals[0] + vals[1] + vals[2]))
    print("WINNER " + str(winner))
    return vals[0] + vals[1] + vals[2] + winner
}

fn main() -> unit {
    let total = await main_task()
    print("TOTAL " + str(total))
}
'''

LOOPS_NESTED_ERRORS_SOURCE = '''import "std/async"

struct FrameState {
    tag: str
    acc: int
}

async fn inner_step(prefix: str, step: int) -> str {
    let _ = await async.sleep_ms(5)
    return prefix + ":" + str(step)
}

async fn accumulate_async(prefix: str, count: int) -> FrameState {
    let mut total: int = 0
    let mut trace: str = ""
    for i in 0..count {
        let part = await inner_step(prefix, i)
        trace = trace + part + ";"
        total = total + i + 1
    }
    let mut w: int = 0
    while w < 2 {
        let _ = await async.sleep_ms(5)
        w = w + 1
        total = total + 10
    }
    return FrameState { tag: trace, acc: total }
}

async fn fallible_div(a: int, b: int) -> int ! str {
    let _ = await async.sleep_ms(5)
    if b == 0 {
        return Err("div_zero")
    }
    return Ok(a / b)
}

async fn propagate_div(a: int, b: int) -> int ! str {
    let q = await fallible_div(a, b)?
    return Ok(q + 5)
}

fn main() -> unit {
    let st = await accumulate_async("k", 3)
    print("TRACE " + st.tag)
    print("ACC " + str(st.acc))

    let ok_val = await propagate_div(20, 4) else err {
        print("UNEXPECTED " + err)
        return
    }
    print("PROP_OK " + str(ok_val))

    let fallback = await propagate_div(20, 0) else err {
        print("CAUGHT " + err)
        -1
    }
    print("PROP_ERR " + str(fallback))

    let paren_fallback = (await fallible_div(9, 0)) else err {
        print("PAREN_CAUGHT " + err)
        -2
    }
    print("PAREN_ERR " + str(paren_fallback))
}
'''

UNAWAITED_CLEANUP_SOURCE = '''import "std/async"

async fn build_message(name: str, fail_now: bool) -> str ! str {
    let msg = "payload:" + name
    let _ = await async.sleep_ms(5)
    if fail_now {
        return Err("err:" + msg)
    }
    return Ok(msg)
}

fn main() -> unit {
    let dropped_ok = build_message("never_awaited", false)
    let dropped_err = build_message("fails_later", true)
    let ok_one = await build_message("awaited", false) else err {
        print("BAD " + err)
        return
    }
    let bad_one = await build_message("fails_now", true) else err {
        print("HANDLED " + err)
        "recovered"
    }
    print("OK " + ok_one)
    print("REC " + bad_one)
    let _unused_ok = dropped_ok
    let _unused_err = dropped_err
}
'''


COOPERATIVE_ENGINES = ("interpreter", "hir", "selfhost")


class Phase3AsyncCpsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selfhost_compiler = ensure_selfhost_compiler_binary(str(ROOT))
        cls.has_llvm = _llvm_available()

    def _run_all_engines_parity(self, source: str, label: str, *, sanitize: bool = True, engines=ENGINES):
        with tempfile.TemporaryDirectory(prefix="wysna_p3_") as tmp:
            work_dir = Path(tmp)
            results = [
                run_engine(engine, source, label, self.selfhost_compiler, work_dir, sanitize=sanitize, run_timeout=30)
                for engine in engines
            ]
            ok, detail = compare(results)
            self.assertTrue(ok, f"{label} parity failure: {detail}\nResults: {[vars(r) for r in results]}")
            for res in results:
                self.assertTrue(res.compile_ok, f"{res.engine} failed to compile: {res.stderr}")
                self.assertEqual(res.exit_code, 0, f"{res.engine} failed at runtime: {res.stderr}")
            return results[0]

    def _run_llvm_parity(self, source: str, expected_stdout: str):
        if not self.has_llvm:
            return
        with tempfile.TemporaryDirectory(prefix="wysna_p3_llvm_") as tmp:
            work_dir = Path(tmp)
            src_path = work_dir / "main.wysna"
            src_path.write_text(source, encoding="utf-8")
            service = CompilerService(project_root=str(work_dir))
            checked = service.check_file(src_path)
            emission = service.emit_llvm(checked, profile="dev")
            built = LlvmToolchain(profile="dev").build(emission, work_dir / "prog_llvm")
            completed = subprocess.run(
                [str(built.output_path)],
                capture_output=True,
                text=True,
                timeout=15,
                env={**os.environ, "WYSNA_MEM_CHECK": "1", "WYSNA_MEM_STATS": "1"},
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertEqual(completed.stdout, expected_stdout)

    def test_lazy_coroutine_start_and_concurrent_all_race_parity(self):
        ref = self._run_all_engines_parity(CONCURRENT_ASYNC_SOURCE, "p3_concurrent_async", engines=COOPERATIVE_ENGINES)
        lines = ref.stdout.splitlines()
        self.assertGreater(len(lines), 0)
        # Deferred coroutine start: CREATED prints before any worker body executes.
        self.assertEqual(lines[0], "CREATED", ref.stdout)
        self.assertEqual(
            lines,
            [
                "CREATED",
                "START a",
                "START b",
                "START c",
                "DONE a",
                "DONE b",
                "DONE c",
                "START slow",
                "START fast",
                "DONE fast",
                "SUM 36",
                "WINNER 17",
                "TOTAL 53",
            ],
        )
        self._run_llvm_parity(CONCURRENT_ASYNC_SOURCE, ref.stdout)

        # Also check AST non-racing deferred-start + async.all parity across all 4 engines.
        deferred_all = '''import "std/async"

async fn add_delayed(x: int) -> int {
    print("RUN " + str(x))
    let _ = await async.sleep_ms(5)
    return x + 10
}

fn main() -> unit {
    let t1 = add_delayed(1)
    let t2 = add_delayed(2)
    print("BEFORE")
    let vals = async.all([t1, t2]) else err {
        print("ERR " + err)
        return
    }
    print("RES " + str(vals[0] + vals[1]))
}
'''
        all_ref = self._run_all_engines_parity(deferred_all, "p3_deferred_all_engines")
        self.assertEqual(all_ref.stdout, "BEFORE\nRUN 1\nRUN 2\nRES 23\n")

        # Concurrency check: 50/90/130ms async.all (~130ms) + 25ms vs 220ms async.race (~25ms)
        # must finish well under 300ms (< serial ~490ms) across native engines.
        for engine in ("hir", "selfhost"):
            with tempfile.TemporaryDirectory(prefix=f"wysna_p3_time_{engine}_") as tmp:
                work_dir = Path(tmp)
                res = run_engine(engine, CONCURRENT_ASYNC_SOURCE, f"time_{engine}", self.selfhost_compiler, work_dir, sanitize=False)
                self.assertEqual(res.exit_code, 0, vars(res))
                binary = work_dir / engine / "program"
                t0 = time.perf_counter()
                proc = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
                elapsed = time.perf_counter() - t0
                self.assertEqual(proc.returncode, 0, proc.stderr)
                self.assertLess(elapsed, 0.30, f"{engine} serialized async.all/race ({elapsed:.3f}s)")

    def test_loops_nested_calls_and_fallible_async_errors(self):
        ref = self._run_all_engines_parity(LOOPS_NESTED_ERRORS_SOURCE, "p3_loops_nested_errors")
        expected = "\n".join([
            "TRACE k:0;k:1;k:2;",
            "ACC 26",
            "PROP_OK 10",
            "CAUGHT div_zero",
            "PROP_ERR -1",
            "PAREN_CAUGHT div_zero",
            "PAREN_ERR -2",
        ]) + "\n"
        self.assertEqual(ref.stdout, expected)
        self._run_llvm_parity(LOOPS_NESTED_ERRORS_SOURCE, expected)

    def test_unawaited_and_failed_coroutines_cleanup_without_leaks(self):
        ref = self._run_all_engines_parity(UNAWAITED_CLEANUP_SOURCE, "p3_unawaited_cleanup")
        expected = "\n".join([
            "HANDLED err:payload:fails_now",
            "OK payload:awaited",
            "REC recovered",
        ]) + "\n"
        self.assertEqual(ref.stdout, expected)
        self._run_llvm_parity(UNAWAITED_CLEANUP_SOURCE, expected)

    def test_extern_async_fn_rejected(self):
        source = 'extern "C" async fn bad_foreign(x: int) -> int\nfn main() -> unit { }\n'
        with tempfile.TemporaryDirectory(prefix="wysna_p3_extern_") as tmp:
            src_path = Path(tmp) / "main.wysna"
            src_path.write_text(source, encoding="utf-8")
            service = CompilerService(project_root=tmp)
            with self.assertRaises(Exception) as ctx:
                service.check_file(src_path)
            self.assertRegex(str(ctx.exception), r"W0001|W2001")


class Phase3MirOptimizationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.selfhost_compiler = ensure_selfhost_compiler_binary(str(ROOT))
        cls.has_llvm = _llvm_available()

    def test_scalar_constant_folding_and_dead_branch_cascade(self):
        from mir.Exchange import export_module
        from mir.Lowering import Lowering
        from mir.Optimizer import optimize
        from mir.Verifier import Verifier

        source = '''fn folded() -> int {
    let a = 2 + 3 * 4
    let b = a - 4
    let c = -b + 20
    if a == 14 and b == 10 and c == 10 {
        return a + b + c
    } else {
        return 999
    }
}

fn c_trunc_div_mod(x: int, y: int) -> int {
    let q = x / y
    let r = x % y
    return q * 10 + r
}

fn runtime_div_zero(zero: int) -> int {
    return 10 / zero
}

fn main() -> unit {
    print(folded(), c_trunc_div_mod(-7, 2))
}
'''
        with tempfile.TemporaryDirectory(prefix="wysna_p3_fold_") as tmp:
            src_path = Path(tmp) / "main.wysna"
            src_path.write_text(source, encoding="utf-8")
            service = CompilerService(project_root=tmp)
            checked = service.check_file(src_path)
            hir = service.lower_hir(checked)
            module = Lowering(checked._interner, checked.source.file).lower(hir, optimize=True)
            fn_folded = next(f for f in module.functions if f.name == "wysna_fn_folded")
            opcodes = [op.opcode for b in fn_folded.blocks for op in b.operations]
            terms = [b.terminator.kind for b in fn_folded.blocks if b.terminator]
            # Constant expressions and conditional branch cascade collapse completely.
            self.assertNotIn("runtime", opcodes)
            self.assertNotIn("branch", terms)
            # Idempotence & soundness check.
            first = export_module(module, checked._interner)
            second = export_module(optimize(module, checked._interner), checked._interner)
            self.assertEqual(first, second)
            Verifier(checked._interner).verify(module)

            # Division by zero must remain a runtime op (not folded away).
            fn_div = next(f for f in module.functions if f.name == "wysna_fn_runtime_div_zero")
            self.assertTrue(
                any(op.opcode == "runtime" and op.data.get("name") == "wysna_div_at" for b in fn_div.blocks for op in b.operations)
            )

            # Parity check across all 4 engines: folded() == 34, c_trunc_div_mod(-7, 2) == -31.
            results = [run_engine(e, source, "p3_fold", self.selfhost_compiler, Path(tmp), sanitize=True) for e in ENGINES]
            ok, detail = compare(results)
            self.assertTrue(ok, detail)
            self.assertEqual(results[0].stdout.strip(), "34 -31")

    def test_coalesce_temporary_stores_and_dead_placeholders(self):
        from mir.Lowering import Lowering

        source = '''struct Pt {
    x: int
    y: int
}

fn compute(n: int, flag: bool) -> int {
    let mut s = 0
    for i in 0..n {
        let step = Pt { x: i, y: i * 2 }
        let copy = step
        s = s + copy.x + copy.y
    }
    if flag {
        return s
    } else {
        return s + 1
    }
}

fn main() -> unit {
    print(compute(5, true))
}
'''
        with tempfile.TemporaryDirectory(prefix="wysna_p3_coalesce_") as tmp:
            src_path = Path(tmp) / "main.wysna"
            src_path.write_text(source, encoding="utf-8")
            service = CompilerService(project_root=tmp)
            checked = service.check_file(src_path)
            hir = service.lower_hir(checked)
            unopt = Lowering(checked._interner, checked.source.file).lower(hir, optimize=False)
            opt = Lowering(checked._interner, checked.source.file).lower(hir, optimize=True)
            fn_unopt = next(f for f in unopt.functions if f.name == "wysna_fn_compute")
            fn_opt = next(f for f in opt.functions if f.name == "wysna_fn_compute")
            stores_unopt = sum(1 for b in fn_unopt.blocks for op in b.operations if op.opcode == "store")
            stores_opt = sum(1 for b in fn_opt.blocks for op in b.operations if op.opcode == "store")
            self.assertLess(stores_opt, stores_unopt)
            self.assertFalse(any(op.data.get("placeholder") for b in fn_opt.blocks for op in b.operations))

            results = [run_engine(e, source, "p3_coalesce", self.selfhost_compiler, Path(tmp), sanitize=True) for e in ENGINES]
            ok, detail = compare(results)
            self.assertTrue(ok, detail)
            self.assertEqual(results[0].stdout.strip(), "30")

    def test_unboxed_range_count_and_div_mod_c99_seed_and_selfhost(self):
        source = '''fn run_loops(n: int, denom: int, scale: float) -> int {
    let mut acc = 0
    for i in 0..=n {
        acc = acc + (i / denom) + (i % (denom + 1))
    }
    let ratio = scale / 2.0
    if ratio > 1.0 {
        acc = acc + 1
    }
    return acc
}

fn main() -> unit {
    print(run_loops(10, 3, 5.0))
}
'''
        with tempfile.TemporaryDirectory(prefix="wysna_p3_unbox_") as tmp:
            work_dir = Path(tmp)
            src_path = work_dir / "main.wysna"
            src_path.write_text(source, encoding="utf-8")
            service = CompilerService(project_root=tmp)
            checked = service.check_file(src_path)
            seed_c = service.emit_c(checked).source

            selfhost_c_path = work_dir / "selfhost_out.c"
            proc = subprocess.run(
                [self.selfhost_compiler, str(src_path), str(selfhost_c_path)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                timeout=30,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            selfhost_c = selfhost_c_path.read_text(encoding="utf-8")

            for label, c_text in (("seed", seed_c), ("selfhost", selfhost_c)):
                fn_start = c_text.find("wysna_fn_run_loops(")
                self.assertNotEqual(fn_start, -1, label)
                # Ensure body after forward declaration is checked.
                fn_body_start = c_text.find("wysna_fn_run_loops(", fn_start + 1)
                self.assertNotEqual(fn_body_start, -1, label)
                fn_end = c_text.find("\n}\n", fn_body_start)
                body = c_text[fn_body_start:fn_end]
                self.assertIn("wysna_range_count_i64(", body, label)
                self.assertIn("wysna_div_i64_at(", body, label)
                self.assertIn("wysna_mod_i64_at(", body, label)
                self.assertIn("wysna_div_f64_at(", body, label)
                self.assertNotIn("wysna_range_count(", body, label)
                self.assertNotIn("wysna_div_at(", body, label)
                self.assertNotIn("wysna_mod_at(", body, label)

            results = [run_engine(e, source, "p3_unbox", self.selfhost_compiler, work_dir, sanitize=True) for e in ENGINES]
            ok, detail = compare(results)
            self.assertTrue(ok, detail)
            self.assertEqual(results[0].stdout.strip(), "31")

            # Zero division panic contract must preserve W4001 across all engines.
            div_zero_src = '''fn crash(d: int) -> int { return 10 / d }
fn main() -> unit { print(crash(0)) }
'''
            panic_results = [run_engine(e, div_zero_src, "p3_divzero", self.selfhost_compiler, work_dir, sanitize=True) for e in ENGINES]
            ok_p, detail_p = compare(panic_results, expected="# RUNTIME_PANIC: W4001\n")
            self.assertTrue(ok_p, detail_p)


if __name__ == "__main__":
    unittest.main()
