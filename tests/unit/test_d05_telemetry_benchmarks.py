"""
test_d05_telemetry_benchmarks.py - Conformance suite for Milestone D05: Telemetry and Performance.

Verifies:
1. CLI --stats telemetry structure (monomorphization, codegen, timing_ms, peak_rss_kb).
2. Separation of stats: check outputs JSON to stdout, run/build output stats to stderr, preserving program stdout clean.
3. Benchmark suite conformance (Wysna Seed Interpreter == Wysna Seed HIR C99 == Wysna Compiler Stage-2 C99).
"""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_cli_stats_telemetry_fields():
    print("  [TEST] CLI --stats telemetry structure and phase timings...")
    test_program = ROOT / "tests/runtime/generic_tree_recursive.wysna"
    proc = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "emit-c", str(test_program), "--stats", "-o", os.devnull],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=30,
    )
    assert proc.returncode == 0, f"CLI error: {proc.stderr}"
    data = json.loads(proc.stderr)
    assert data.get("status") == "ok", "Expected status ok in JSON stats"
    telemetry = data.get("telemetry", {})
    assert "monomorphization" in telemetry, "Expected monomorphization in telemetry"
    assert "codegen" in telemetry, "Expected codegen in telemetry"
    assert "timing_ms" in telemetry, "Expected timing_ms in telemetry"
    assert "peak_rss_kb" in telemetry, "Expected peak_rss_kb in telemetry"

    timing = telemetry["timing_ms"]
    assert "lex" in timing and timing["lex"] >= 0
    assert "parse" in timing and timing["parse"] >= 0
    assert "check" in timing and timing["check"] >= 0
    assert "hir" in timing and timing["hir"] >= 0
    assert "codegen" in timing and timing["codegen"] >= 0
    assert "total" in timing and timing["total"] > 0
    assert telemetry["peak_rss_kb"] > 0, "Expected positive peak RSS memory"


def test_cli_stats_stdout_separation():
    print("  [TEST] Separation of stats from program stdout (oracle cleanliness)...")
    with tempfile.TemporaryDirectory() as td:
        source = Path(td) / "oracle_test.wysna"
        source.write_text('fn main() { print("CLEAN_STDOUT_PAYLOAD") }\n')

        # 1. run --stats must output payload ONLY to stdout, stats to stderr
        res = subprocess.run(
            [sys.executable, str(ROOT / "main.py"), "run", str(source), "--stats"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=20,
        )
        assert res.returncode == 0, res.stderr
        assert res.stdout == "CLEAN_STDOUT_PAYLOAD\n", f"Stdout contaminated: {res.stdout!r}"
        stats = json.loads(res.stderr)
        assert stats["status"] == "ok"
        assert "timing_ms" in stats["telemetry"]

        # 2. check --stats must output pure JSON to stdout
        check_res = subprocess.run(
            [sys.executable, str(ROOT / "main.py"), "check", str(source), "--stats"],
            capture_output=True,
            text=True,
            cwd=str(ROOT),
            timeout=20,
        )
        assert check_res.returncode == 0, check_res.stderr
        check_json = json.loads(check_res.stdout)
        assert check_json["status"] == "ok"
        assert "timing_ms" in check_json["telemetry"]


def test_benchmark_harness_conformance():
    print("  [TEST] Benchmark harness equivalence and execution...")
    from bench.run_bench import run_benchmark_suite
    from main import ensure_selfhost_compiler_binary
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    # Run a subset of benchmarks or full suite
    ok = run_benchmark_suite(str(ROOT), selfhost_bin, update_baseline=False)
    assert ok, "Benchmark suite must pass with equivalence across engines"


def run_all() -> bool:
    print("==================================================")
    print("Wysna D05 Telemetry & Benchmarks Suite")
    print("==================================================")
    try:
        test_cli_stats_telemetry_fields()
        test_cli_stats_stdout_separation()
        test_benchmark_harness_conformance()
        print("==================================================")
        print("ALL D05 TELEMETRY & BENCHMARK TESTS PASSED.")
        print("==================================================")
        return True
    except Exception as e:
        print(f"FAILED: {e}")
        import traceback
        traceback.print_exc()
        return False


if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
