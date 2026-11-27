"""Block 2 tests: multi-TU C splitter, unboxed dict[int, T] reads, and memory safety."""

import os
from pathlib import Path
import subprocess

from common.Platform import compiler_command, executable_name
from compiler.Bootstrap import ensure_selfhost_compiler_binary
from tools.split_c_tu import split_c_translation_unit

ROOT = Path(__file__).resolve().parents[2]


def test_split_c_translation_unit_matches_monolithic_behavior(tmp_path):
    compiler = ensure_selfhost_compiler_binary(str(ROOT))
    source_text = """
enum Color { Red, Rgb(int, int, int) }
struct Point { x: int, y: int }
class Acc {
    priv total: int
    fn init(start: int) { this.total = start }
    pub fn add(delta: int) -> int {
        this.total = this.total + delta
        return this.total
    }
}
fn apply(cb: fn(int) -> int, v: int) -> int { return cb(v) }
fn main() {
    let mut p = Point { x: 3, y: 4 }
    let a = Acc(10)
    let c = Color.Red
    let c2 = Color.Rgb(1, 2, 3)
    let out = apply(|n: int| => n * 2, a.add(p.x + p.y))
    match c {
        Color.Red => print("red", out)
        Color.Rgb(r, g, b) => print(r, g, b)
    }
    match c2 {
        Color.Red => print("red2")
        Color.Rgb(r, g, b) => print(r + g + b)
    }
}
"""
    src = tmp_path / "poly.wysna"
    mono_c = tmp_path / "poly.c"
    src.write_text(source_text, encoding="utf-8")
    emitted = subprocess.run([compiler, str(src), str(mono_c)], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert emitted.returncode == 0, emitted.stderr

    units_dir = tmp_path / "units"
    header, c_files = split_c_translation_unit(mono_c, units_dir, chunks=4)
    assert header.is_file()
    assert len(c_files) >= 2

    math_flags = [] if os.name == "nt" else ["-lm"]
    cc = compiler_command()

    mono_bin = tmp_path / executable_name("poly_mono")
    b_mono = subprocess.run(
        cc + ["-std=c99", "-O2", f"-I{ROOT}", str(mono_c)] + math_flags + ["-o", str(mono_bin)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert b_mono.returncode == 0, b_mono.stderr

    obj_files = []
    for c_file in c_files:
        obj = c_file.with_suffix(".o")
        b_unit = subprocess.run(
            cc + ["-std=c99", "-O2", f"-I{ROOT}", f"-I{units_dir}", "-c", str(c_file), "-o", str(obj)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert b_unit.returncode == 0, (c_file.name, b_unit.stderr)
        obj_files.append(str(obj))

    split_bin = tmp_path / executable_name("poly_split")
    b_split = subprocess.run(
        cc + ["-std=c99", "-O2"] + obj_files + math_flags + ["-o", str(split_bin)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert b_split.returncode == 0, b_split.stderr

    r_mono = subprocess.run([str(mono_bin)], env=dict(os.environ, WYSNA_MEM_CHECK="1"), capture_output=True, text=True, timeout=30)
    r_split = subprocess.run([str(split_bin)], env=dict(os.environ, WYSNA_MEM_CHECK="1"), capture_output=True, text=True, timeout=30)
    assert r_mono.returncode == 0, r_mono.stderr
    assert r_split.returncode == 0, r_split.stderr
    assert r_split.stdout == r_mono.stdout


def test_unboxed_dict_int_reads_and_large_growth_under_memcheck(tmp_path):
    compiler = ensure_selfhost_compiler_binary(str(ROOT))
    math_flags = [] if os.name == "nt" else ["-lm"]
    cc = compiler_command()

    # 1. Verify codegen lowers dict[int, int/float/bool] reads cleanly and handles 48-bit & >48-bit keys + growth
    src_ok = tmp_path / "dict_fast.wysna"
    c_ok = tmp_path / "dict_fast.c"
    bin_ok = tmp_path / executable_name("dict_fast")
    src_ok.write_text(
        """
fn main() {
    let mut ints: dict[int, int] = {}
    for i in 0..5000 {
        ints[i] = i * 3 + 1
    }
    let big = 36028797018963968 + 42
    ints[big] = 9999999999999999
    let mut sum = 0
    for i in 0..5000 {
        sum = sum + ints[i]
    }
    let floats: dict[int, float] = {1: 1.5, 2: 2.5}
    let bools: dict[int, bool] = {10: true, 20: false}
    let copy_ints = ints
    let mut mutated = copy_ints
    mutated[0] = 77
    print(sum, ints[big], ints[0], mutated[0], floats[2], bools[10], bools[20])
}
""",
        encoding="utf-8",
    )
    res_emit = subprocess.run([compiler, str(src_ok), str(c_ok)], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert res_emit.returncode == 0, res_emit.stderr
    c_text = c_ok.read_text(encoding="utf-8")
    assert "wysna_dict_get_int_at" in c_text
    assert "wysna_dict_get_float_at" in c_text
    assert "wysna_dict_get_bool_at" in c_text

    built = subprocess.run(
        cc + ["-std=c99", "-O2", f"-I{ROOT}", str(c_ok)] + math_flags + ["-o", str(bin_ok)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert built.returncode == 0, built.stderr
    ran = subprocess.run([str(bin_ok)], env=dict(os.environ, WYSNA_MEM_CHECK="1"), capture_output=True, text=True, timeout=30)
    assert ran.returncode == 0, ran.stderr
    expected_sum = sum(i * 3 + 1 for i in range(5000))
    assert ran.stdout.strip() == f"{expected_sum} 9999999999999999 1 77 2.5 true false"

    # 2. Verify missing key on unboxed fast path reports exact located W4002 panic
    src_miss = tmp_path / "dict_miss_fast.wysna"
    c_miss = tmp_path / "dict_miss_fast.c"
    bin_miss = tmp_path / executable_name("dict_miss_fast")
    src_miss.write_text('fn main() {\n    let d: dict[int, int] = {1: 42}\n    print(d[999])\n}\n', encoding="utf-8")
    res_miss_emit = subprocess.run([compiler, str(src_miss), str(c_miss)], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert res_miss_emit.returncode == 0, res_miss_emit.stderr
    built_miss = subprocess.run(
        cc + ["-std=c99", "-O2", f"-I{ROOT}", str(c_miss)] + math_flags + ["-o", str(bin_miss)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert built_miss.returncode == 0, built_miss.stderr
    ran_miss = subprocess.run([str(bin_miss)], capture_output=True, text=True, timeout=30)
    assert ran_miss.returncode != 0
    assert "[W4002]" in ran_miss.stderr
    assert "dict_miss_fast.wysna:3:12" in ran_miss.stderr
    assert "Dictionary key not found (use d.get(key) for optional lookup)" in ran_miss.stderr
