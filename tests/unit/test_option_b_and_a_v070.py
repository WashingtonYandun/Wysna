"""Regression suite for Option B (WVal64/hash cache + selfhost --fmt) and Option A (parallel OS pool + async timers)."""
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile

import pytest

from common.Paths import get_repo_root
from common.Platform import compiler_command, executable_name
from compiler.NativeToolchain import NativeToolchain
from main import ensure_selfhost_compiler_binary
from tests.engine_harness import ENGINES, run_engine

ROOT = Path(get_repo_root())


def test_wval64_nan_tagging_and_string_hash_cache():
    c_code = r'''#include "runtime/wysna_runtime.h"
#include <assert.h>
#include <stddef.h>
#include <stdint.h>

typedef char CheckWVal8[sizeof(WVal) == 8 ? 1 : -1];
typedef char CheckWVal64[sizeof(WVal64) == 8 ? 1 : -1];
typedef char CheckOffset[offsetof(WVal, bits) == 0 ? 1 : -1];
typedef char CheckLit48[sizeof(WStringLiteral) == 48 ? 1 : -1];

int main(int argc, char** argv) {
    wysna_init_args(argc, argv);

    /* 1. Unit, None, Bool */
    WVal64 u64 = wysna_val64_pack(wysna_unit());
    WVal64 n64 = wysna_val64_pack(wysna_none());
    WVal64 bt = wysna_val64_pack(wysna_bool(true));
    WVal64 bf = wysna_val64_pack(wysna_bool(false));
    assert(wysna_tag(wysna_val64_unpack(u64)) == W_UNIT);
    assert(wysna_tag(wysna_val64_unpack(n64)) == W_NONE);
    assert(wysna_tag(wysna_val64_unpack(bt)) == W_BOOL && wysna_as_bool(wysna_val64_unpack(bt)) == true);
    assert(wysna_tag(wysna_val64_unpack(bf)) == W_BOOL && wysna_as_bool(wysna_val64_unpack(bf)) == false);
    assert(!wysna_val64_eq(u64, n64));
    assert(wysna_val64_eq(bt, bt) && !wysna_val64_eq(bt, bf));

    /* 2. IEEE-754 Doubles including NaN & -0.0 */
    WVal64 f1 = wysna_val64_pack(wysna_float(-3.125));
    WVal64 f2 = wysna_val64_pack(wysna_float(0.0));
    WVal64 f3 = wysna_val64_pack(wysna_float(-0.0));
    WVal64 fnan = wysna_val64_pack(wysna_float(NAN));
    assert(wysna_val64_is_double(f1) && wysna_as_float(wysna_val64_unpack(f1)) == -3.125);
    assert(wysna_val64_eq(f2, f3));
    assert(!wysna_val64_eq(fnan, fnan));
    assert(isnan(wysna_as_float(wysna_val64_unpack(fnan))));

    /* 3. 48-bit signed integers */
    int64_t max48 = (INT64_C(1) << 47) - 1;
    int64_t min48 = -(INT64_C(1) << 47);
    assert(wysna_val64_can_pack(wysna_int(max48)));
    assert(wysna_val64_can_pack(wysna_int(min48)));
    WVal over48 = wysna_int(max48 + 1);
    WVal under48 = wysna_int(min48 - 1);
    assert(!wysna_val64_can_pack(over48));
    assert(!wysna_val64_can_pack(under48));
    assert(wysna_as_int(over48) == max48 + 1 && wysna_as_int(under48) == min48 - 1);
    wysna_release(over48);
    wysna_release(under48);
    WVal64 i_pos = wysna_val64_pack(wysna_int(max48));
    WVal64 i_neg = wysna_val64_pack(wysna_int(min48));
    WVal64 i_minus1 = wysna_val64_pack(wysna_int(-1));
    assert(wysna_as_int(wysna_val64_unpack(i_pos)) == max48);
    assert(wysna_as_int(wysna_val64_unpack(i_neg)) == min48);
    assert(wysna_as_int(wysna_val64_unpack(i_minus1)) == -1);

    /* 4. Small String Optimization (0..6 bytes, zero alloc read + NUL safe) */
    WVal s0 = wysna_str("");
    WVal s3 = wysna_str_n("a\0b", 3);
    WVal s6 = wysna_str("abcdef");
    WVal s6b = wysna_copy_str("abcdef");
    WVal64 p0 = wysna_val64_pack(s0);
    WVal64 p3 = wysna_val64_pack(s3);
    WVal64 p6 = wysna_val64_pack(s6);
    WVal64 p6b = wysna_val64_pack(s6b);
    assert(wysna_str_len(wysna_val64_unpack(p0)) == 0 && !wysna_val64_eq(p0, p3));
    assert(p6.bits == p6b.bits);
    assert(wysna_val64_eq(p6, p6b));
    char buf[7];
    size_t blen = 0;
    assert(wysna_val64_read_sso(p3, buf, &blen) && blen == 3 && buf[0] == 'a' && buf[1] == '\0' && buf[2] == 'b');
    WVal u3 = wysna_val64_unpack(p3);
    char u3_buf[7];
    size_t u3_len = 0;
    const char* u3_data = wysna_str_bytes(u3, u3_buf, &u3_len);
    assert(wysna_tag(u3) == W_STR && u3_len == 3 && memcmp(u3_data, "a\0b", 3) == 0);
    wysna_release(u3);
    wysna_release(s6b);

    /* 5. Long owned string (>6 bytes) + hash_cache O(1) memoization */
    WVal long_dyn = wysna_copy_str("wysna_agent_graph_key_long");
    WVal long_borrow = wysna_str("wysna_agent_graph_key_long");
    static WStringLiteral lit = {0};
    WVal long_lit = wysna_literal_str_n(&lit, "wysna_agent_graph_key_long", 26);
    assert(wysna_str_owner(long_dyn)->hash_cache == 0);
    uint64_t h1 = wysna_key_hash(long_dyn);
    uint64_t h2 = wysna_key_hash(long_borrow);
    uint64_t h3 = wysna_key_hash(long_lit);
    assert(h1 != 0 && h1 == h2 && h2 == h3);
    assert(wysna_str_owner(long_dyn)->hash_cache == (uint32_t)h1);
    assert(lit.buffer.hash_cache == (uint32_t)h3);

    WVal64 p_long_dyn = wysna_val64_pack(long_dyn);
    WVal64 p_long_lit = wysna_val64_pack(long_lit);
    assert(wysna_val64_eq(p_long_dyn, p_long_lit));

    WVal d = wysna_make_dict(1, &long_dyn, &long_lit);
    assert(wysna_str_owner(wysna_as_dict(d)->keys[0])->hash_cache == (uint32_t)h1);
    assert(wysna_dict_find(wysna_as_dict(d), long_borrow) == 0);

    wysna_release(d);
    wysna_release(long_dyn);
    wysna_release(long_lit);
    wysna_runtime_cleanup();
    return 0;
}
'''
    compiler = compiler_command()
    with tempfile.TemporaryDirectory(prefix="wysna-wval64-") as tmpdir:
        c_path = Path(tmpdir) / "test_wval64.c"
        bin_path = Path(tmpdir) / executable_name("test_wval64")
        c_path.write_text(c_code, encoding="utf-8")
        flags = ["-std=c99", "-Wall", "-Wextra", "-Wno-unused-function", "-Werror", f"-I{ROOT}", str(c_path)]
        if os.name != "nt":
            flags += ["-fsanitize=address,undefined", "-fno-sanitize-recover=all", "-lm"]
        compiled = subprocess.run(compiler + flags + ["-o", str(bin_path)], capture_output=True, text=True, timeout=120)
        assert compiled.returncode == 0, compiled.stderr
        env = dict(os.environ, WYSNA_MEM_CHECK="1")
        if sys.platform == "darwin":
            env["ASAN_OPTIONS"] = "detect_leaks=0"
        ran = subprocess.run([str(bin_path)], env=env, capture_output=True, text=True, timeout=30)
        assert ran.returncode == 0, ran.stderr


def test_selfhost_native_formatter_cli_and_toolchain():
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    toolchain = NativeToolchain(ROOT)
    messy = (
        "fn helper(x: int) -> int {\r\n"
        "\t  if x > 0 {\r\n"
        "return x + 1\r\n"
        "  } else {\r\n"
        "      return 0\r\n"
        "    }\r\n"
        "}\r\n\r\n\r\n"
        "print(helper(5))   # keep brace in comment }\r\n"
    )
    expected = (
        "fn helper(x: int) -> int {\n"
        "    if x > 0 {\n"
        "        return x + 1\n"
        "    } else {\n"
        "        return 0\n"
        "    }\n"
        "}\n\n"
        "print(helper(5))   # keep brace in comment }\n"
    )
    with tempfile.TemporaryDirectory(prefix="wysna-selfhost-fmt-") as tmpdir:
        src = Path(tmpdir) / "messy.wysna"
        src.write_bytes(messy.encode("utf-8"))
        out1, _ = toolchain.format_compiler(selfhost_bin, src, write=False)
        assert out1 == expected, repr(out1)

        write_out, _ = toolchain.format_compiler(selfhost_bin, src, write=True)
        assert "Formatted" in write_out
        written = src.read_text(encoding="utf-8")
        assert written == expected

        out2, _ = toolchain.format_compiler(selfhost_bin, src, write=False)
        assert out2 == expected, "Formatter must be strictly idempotent"

        # Formatted code still compiles and executes via selfhost binary.
        bin_out = Path(tmpdir) / executable_name("messy_bin")
        emitted = toolchain.emit_compiler(selfhost_bin, src)
        toolchain.build(emitted.source, str(bin_out))
        ran = subprocess.run([str(bin_out)], capture_output=True, text=True, timeout=30)
        assert ran.returncode == 0
        assert ran.stdout.strip() == "6"

        # Invalid source must fail formatting rather than printing broken code.
        bad = Path(tmpdir) / "bad.wysna"
        bad.write_text("let x: int = \"bad\"\n", encoding="utf-8")
        proc = subprocess.run([selfhost_bin, "--stdlib-root", str(ROOT / "stdlib"), "--fmt", str(bad)],
                              capture_output=True, text=True, timeout=30)
        assert proc.returncode != 0


def test_parallel_os_worker_pool_with_cow_and_memcheck_all_engines():
    source = '''import "std/algorithms" as alg

fn mutate_and_sum(seed_list: list[int], delta: int) -> int {
    let mut local = seed_list
    local.append(delta)
    let mut s = 0
    for v in local {
        s = s + v
    }
    return s
}

fn main() -> unit {
    let base = [1, 2]
    let bias = 10
    let work: list[list[int]] = [base, base, base, base, base, base, base, base]
    let mapped = alg.parallel_map[list[int], int](work, |item: list[int]| => mutate_and_sum(item, bias))
    let xs = [1, 2, 3, 4, 5, 6]
    let batched = alg.parallel_batch[int, int](xs, 2, |n: int| => n * n)
    let filtered = alg.parallel_filter[int](xs, |n: int| => n % 2 == 0)
    let reduced = alg.parallel_reduce[int, int](xs, 0, |n: int| => n * 2, |a: int, b: int| => a + b)
    print("BASE", len(base), base)
    print("MAPPED", mapped)
    print("BATCHED", batched)
    print("FILTERED", filtered)
    print("REDUCED", reduced)
}

main()
'''
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    prev_mem = os.environ.get("WYSNA_MEM_CHECK")
    os.environ["WYSNA_MEM_CHECK"] = "1"
    try:
        with tempfile.TemporaryDirectory(prefix="wysna-parallel-cow-") as tmpdir:
            for engine in ENGINES:
                res = run_engine(engine, source, "parallel_cow", selfhost_bin, Path(tmpdir) / engine)
                assert res.exit_code == 0, f"{engine} failed: {res.stderr}"
                assert res.stdout == (
                    "BASE 2 [1, 2]\n"
                    "MAPPED [13, 13, 13, 13, 13, 13, 13, 13]\n"
                    "BATCHED [1, 4, 9, 16, 25, 36]\n"
                    "FILTERED [2, 4, 6]\n"
                    "REDUCED 42\n"
                ), f"{engine} unexpected stdout: {res.stdout!r}"
    finally:
        if prev_mem is None:
            os.environ.pop("WYSNA_MEM_CHECK", None)
        else:
            os.environ["WYSNA_MEM_CHECK"] = prev_mem


def test_cooperative_async_sleep_spawn_and_race_all_engines():
    source = '''import "std/async" as a

fn main() -> unit {
    let t_one = a.sleep_ms(5)
    let single = await t_one
    let raced = a.race[int]([a.sleep_ms(45), a.sleep_ms(3)]) else err { -1 }
    let fallback_list: list[int] = []
    let both = a.all[int]([a.sleep_ms(5), a.sleep_ms(8)]) else err { fallback_list }
    let spawned = await a.spawn[int](|| => 42)
    print("ASYNC", single, raced, both, spawned)
}

main()
'''
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    prev_mem = os.environ.get("WYSNA_MEM_CHECK")
    os.environ["WYSNA_MEM_CHECK"] = "1"
    try:
        with tempfile.TemporaryDirectory(prefix="wysna-async-timers-") as tmpdir:
            for engine in ENGINES:
                res = run_engine(engine, source, "async_timers", selfhost_bin, Path(tmpdir) / engine)
                assert res.exit_code == 0, f"{engine} failed: {res.stderr}"
                assert res.stdout.strip() == "ASYNC 5 3 [5, 8] 42", f"{engine} unexpected stdout: {res.stdout!r}"
    finally:
        if prev_mem is None:
            os.environ.pop("WYSNA_MEM_CHECK", None)
        else:
            os.environ["WYSNA_MEM_CHECK"] = prev_mem
