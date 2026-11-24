"""Cross-engine ownership, panic, and C99 emission contracts for Wave 3 optimizations."""
import os
from pathlib import Path
import tempfile

from compiler.CompilerService import CompilerService
from main import ensure_selfhost_compiler_binary
from tests.engine_harness import verify

ROOT = Path(__file__).resolve().parents[2]


def _emit_c(source: str, label: str) -> str:
    service = CompilerService()
    return service.emit_c(service.check(source, label)).source


def _verify_all_engines(source: str, label: str, expected: str):
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    with tempfile.TemporaryDirectory(prefix=f"wysna-{label}-") as directory:
        ok, detail = verify(
            source,
            label,
            selfhost_bin,
            directory,
            expected=expected,
            strict=True,
            sanitize=os.environ.get("WYSNA_SANITIZE") == "1",
        )
        assert ok, detail


def test_for_loop_hoisting_borrowing_and_cow_cross_engine():
    source = """
struct Entry {
    tag: str,
    val: int
}

fn tag_score(e: Entry) -> int {
    return len(e.tag) * 10 + e.val
}

fn find_first_over(items: list[Entry], limit: int) -> str {
    for it in items {
        if it.val > limit {
            return it.tag
        }
    }
    return "none"
}

fn collect_long_tags(rows: list[list[Entry]]) -> int {
    let mut acc = 0
    for row in rows {
        for cell in row {
            if cell.val < 0 {
                continue
            }
            if cell.val > 100 {
                break
            }
            acc = acc + tag_score(cell)
        }
    }
    return acc
}

fn main() {
    let mut xs = [
        Entry { tag: "alpha_long_tag", val: 5 },
        Entry { tag: "beta_long_tag", val: 20 },
        Entry { tag: "gamma_long_tag", val: 200 }
    ]
    let mut ys = xs
    let mut sum = 0
    for x in xs {
        ys.append(Entry { tag: "extra_appended", val: 1 })
        sum = sum + tag_score(x)
    }
    let mut skipped_sum = 0
    for z in xs {
        if z.val < 10 {
            continue
        }
        if z.val > 50 {
            break
        }
        skipped_sum = skipped_sum + tag_score(z)
    }
    let rows = [xs, [Entry { tag: "inner_one", val: 7 }, Entry { tag: "skip_neg", val: -1 }]]
    let matrix_sum = collect_long_tags(rows)
    let hit = find_first_over(xs, 10)
    print(sum)
    print(skipped_sum)
    print(len(ys))
    print(matrix_sum)
    print(hit)
}
"""
    emission = _emit_c(source, "wave3_for_borrow.wysna")
    assert "wysna_list_iter_begin_at(" in emission
    assert "wysna_list_load(_ibuf_" in emission
    _verify_all_engines(
        source,
        "wave3_for_borrow.wysna",
        "635\n150\n6\n392\nbeta_long_tag\n",
    )


def test_list_set_cow_u8_nonscalar_and_oob_panic():
    source = """
fn main() {
    let base = [10, 20, 30]
    let mut alias = base
    alias[1] = 99
    let mut words = ["first_long_str", "second_long_str"]
    let words_alias = words
    words[0] = "replaced_long_str"
    let mut raw: list[u8] = [1, 2, 3]
    let raw_alias = raw
    raw[1] = 250
    print(base[1])
    print(alias[1])
    print(words_alias[0])
    print(words[0])
    print(int(raw_alias[1]))
    print(int(raw[1]))
}
"""
    emission = _emit_c(source, "wave3_list_set.wysna")
    assert "wysna_list_set_at(" in emission
    assert "wysna_index_store_at(" in emission
    _verify_all_engines(
        source,
        "wave3_list_set.wysna",
        "20\n99\nfirst_long_str\nreplaced_long_str\n2\n250\n",
    )

    oob_source = """
fn main() {
    let mut xs = [1, 2]
    xs[5] = 9
}
"""
    _verify_all_engines(oob_source, "wave3_list_set_oob.wysna", "# RUNTIME_PANIC: W4002\n")


def test_dict_int_fast_contains_and_set_including_large_keys_and_cow():
    source = """
fn main() {
    let mut counts: dict[int, int] = {}
    for i in 0..40 {
        counts[i] = i * 3
    }
    counts[5] = 777
    let big_pos = 1125899906842624
    let big_neg = -1125899906842624
    let mut labels: dict[int, str] = {}
    labels[big_pos] = "upper_big_key_val"
    labels[big_neg] = "lower_big_key_val"
    let snapshot = counts
    counts[0] = 999
    print(len(counts))
    print(counts.contains(5))
    print(counts.contains(999))
    print(counts[5])
    print(snapshot[0])
    print(counts[0])
    print(labels.contains(big_pos))
    print(labels.contains(big_neg))
    print(labels[big_pos])
    print(labels[big_neg])
}
"""
    emission = _emit_c(source, "wave3_dict_int.wysna")
    assert "wysna_dict_contains_i64(" in emission
    assert "wysna_dict_set_i64_at(" in emission
    assert "wysna_dict_len_i64(" in emission
    _verify_all_engines(
        source,
        "wave3_dict_int.wysna",
        "40\ntrue\nfalse\n777\n0\n999\ntrue\ntrue\nupper_big_key_val\nlower_big_key_val\n",
    )


def test_struct_pool_and_parallel_map_memcheck():
    source = """
import "std/algorithms" as alg

struct PairRec {
    a: int,
    b: str,
    c: bool
}

fn make_rec(n: int) -> PairRec {
    return PairRec { a: n, b: "worker_item_label", c: n % 2 == 0 }
}

fn main() {
    let mut total = 0
    for i in 0..120 {
        let p = PairRec { a: i, b: "pooled_struct_label", c: true }
        total = total + p.a + len(p.b)
    }
    let items = alg.parallel_map([1, 2, 3, 4, 5, 6, 7, 8], make_rec)
    let mut par_sum = 0
    for it in items {
        par_sum = par_sum + it.a + (if it.c { 1 } else { 0 })
    }
    print(total)
    print(par_sum)
}
"""
    _verify_all_engines(source, "wave3_struct_pool_par.wysna", "9420\n40\n")


def test_leaf_frame_elision_len_unwrap_and_memchr_contracts():
    source = """
struct Pt {
    x: int,
    y: int
}

fn add_pt(p: Pt) -> int {
    return p.x + p.y
}

fn safe_div(a: int, b: int) -> int {
    return a / b
}

fn unwrap_num(opt: int?) -> int {
    return opt ?? 11
}

fn unwrap_res(r: int ! str) -> int {
    match r {
        Ok(v) => v,
        Err(_e) => -1,
    }
}

fn unwrap_err_str(r: str ! int) -> int {
    match r {
        Ok(_s) => 0,
        Err(code) => code,
    }
}

fn main() {
    let p = Pt { x: 10, y: 32 }
    let xs = [1, 2, 3]
    let d = {"a": 1, "b": 2}
    let t = (1, "two", 3, true)
    let text = "hello_memchr_needle_tail"
    print(add_pt(p))
    print(safe_div(84, 2))
    print(unwrap_num(Some(5)))
    print(unwrap_num(None))
    print(unwrap_res(Ok(77)))
    print(unwrap_err_str(Err(13)))
    print(len(xs) + len(d) + len(t))
    print(text.contains(""))
    print(text.contains("l"))
    print(text.contains("needle"))
    print(text.contains("tail"))
    print(text.contains("missing_sub"))
    print(len("abcde"))
}
"""
    emission = _emit_c(source, "wave3_leaf_ops.wysna")
    fn_add_idx = emission.rindex("static int64_t wysna_fn_add_pt(")
    fn_add_end = emission.index("\n}\n", fn_add_idx)
    assert "wysna_runtime_push_frame" not in emission[fn_add_idx:fn_add_end]

    fn_div_idx = emission.rindex("static int64_t wysna_fn_safe_div(")
    fn_div_end = emission.index("\n}\n", fn_div_idx)
    assert "wysna_runtime_push_frame" in emission[fn_div_idx:fn_div_end]

    assert "wysna_list_len_i64(" in emission
    assert "wysna_dict_len_i64(" in emission
    assert "wysna_tuple_len_i64(" in emission
    assert "wysna_builtin_len(" in emission  # kept for str
    assert "wysna_peek_optional(" in emission
    assert "wysna_peek_ok(" in emission
    assert "wysna_peek_err(" in emission
    _verify_all_engines(
        source,
        "wave3_leaf_ops.wysna",
        "42\n42\n5\n11\n77\n13\n9\ntrue\ntrue\ntrue\ntrue\nfalse\n5\n",
    )
