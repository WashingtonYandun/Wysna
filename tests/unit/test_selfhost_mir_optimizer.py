"""Tests for Selfhost MIR optimization passes (constant folding, CFG pruning, dead scalar op elimination)."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from compiler.Bootstrap import ensure_selfhost_compiler_binary


def _emit_mir_selfhost(tmp_path: Path, source: str) -> dict:
    compiler = ensure_selfhost_compiler_binary(str(ROOT))
    program = tmp_path / "prog.wysna"
    program.write_text(source, encoding="utf-8")
    proc = subprocess.run(
        [compiler, "--emit-mir", str(program)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)["module"]


def _run_selfhost(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    compiler = ensure_selfhost_compiler_binary(str(ROOT))
    program = tmp_path / "prog.wysna"
    program.write_text(source, encoding="utf-8")
    env = {
        **os.environ,
        "WYSNA_SELFHOST_BIN": compiler,
        "PYTHONPATH": f"{ROOT / 'src'}{os.pathsep}{ROOT}",
    }
    return subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "run", "--engine=compiler", str(program)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )


def test_selfhost_mir_folds_constants_and_prunes_dead_branches(tmp_path: Path):
    source = (
        "fn compute() -> int {\n"
        "    let a = 6 * 7\n"
        "    let b = a + 8\n"
        "    if b > 40 {\n"
        "        return b\n"
        "    }\n"
        "    return 999\n"
        "}\n"
        "print(compute())\n"
    )
    module = _emit_mir_selfhost(tmp_path, source)
    fn = next(f for f in module["functions"] if f["name"] == "wysna_fn_compute")
    opcodes = [op["opcode"] for blk in fn["blocks"] for op in blk["operations"]]
    # 6*7, +8, and >40 are folded cleanly to constants and dead branch is pruned.
    assert len(fn["blocks"]) <= 2, fn["blocks"]
    assert "runtime" not in opcodes, opcodes
    const_ints = [op["data"].get("value") for blk in fn["blocks"] for op in blk["operations"] if op["opcode"] == "const"]
    assert 999 not in const_ints, const_ints

    run_proc = _run_selfhost(tmp_path, source)
    assert run_proc.returncode == 0, run_proc.stderr
    assert run_proc.stdout == "50\n"


def test_selfhost_mir_preserves_division_by_zero_trap(tmp_path: Path):
    source = (
        "fn fail_div() -> int {\n"
        "    let zero = 2 - 2\n"
        "    return 10 / zero\n"
        "}\n"
        "print(fail_div())\n"
    )
    run_proc = _run_selfhost(tmp_path, source)
    assert run_proc.returncode != 0
    assert "W4001" in run_proc.stderr
