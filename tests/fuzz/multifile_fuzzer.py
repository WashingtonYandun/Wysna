"""FUZZ-A01: Seeded multi-file differential fuzzer for generics, error unions, closures, and async."""
import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import random
import shutil
import sys
import tempfile
from typing import Callable

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.engine_harness import ENGINES, asdict, compare, run_engine


@dataclass(frozen=True)
class MultiFileCase:
    seed: int
    files: dict[str, str]
    entry: str
    expected_error: str | None = None


def generate_multifile_positive(seed: int) -> MultiFileCase:
    """Generate a valid multi-file project combining generics, errors, closures, CoW, OOP, and async."""
    rng = random.Random(seed)
    a = rng.randint(2, 25)
    b = rng.randint(1, 15)
    c = rng.randint(-10, 20)
    threshold = rng.randint(5, 30)
    trigger_err = rng.choice(["ok", "int_err", "str_err", "plain_err"])
    use_from_import = bool(rng.getrandbits(1))
    use_guard = bool(rng.getrandbits(1))
    use_async = bool(rng.getrandbits(1))
    use_closure = bool(rng.getrandbits(1))
    use_returned_closure = bool(rng.getrandbits(1))
    use_cow_update = bool(rng.getrandbits(1))
    use_generic_struct_spread = bool(rng.getrandbits(1))
    use_dict_ops = bool(rng.getrandbits(1))
    use_tuple_destructure = bool(rng.getrandbits(1))
    use_generic_class = bool(rng.getrandbits(1))
    use_async_optional = bool(rng.getrandbits(1))

    errors_src = """pub struct DomainErr[T] {
    code: int
    detail: T
}

pub enum Fault[T] {
    Empty
    Rejected(T)
}

pub fn make_int_err(code: int, v: int) -> DomainErr[int] {
    return DomainErr[int] { code: code, detail: v }
}

pub fn make_str_err(code: int, msg: str) -> DomainErr[str] {
    return DomainErr[str] { code: code, detail: msg }
}
"""

    domain_import = (
        'from "errors.wysna" use { DomainErr, Fault, make_int_err, make_str_err }\n'
        if use_from_import else
        'import "errors.wysna" as errs\n'
    )
    err_t_int = "DomainErr[int]" if use_from_import else "errs.DomainErr[int]"
    err_t_str = "DomainErr[str]" if use_from_import else "errs.DomainErr[str]"
    fault_int = "Fault[int]" if use_from_import else "errs.Fault[int]"
    mk_int = "make_int_err" if use_from_import else "errs.make_int_err"
    mk_str = "make_str_err" if use_from_import else "errs.make_str_err"

    domain_lines = [
        domain_import,
        "pub struct Metric {",
        "    label: str",
        "    value: int requires value >= -1000",
        "    tags: list[str]",
        "}",
        "",
        "pub struct Boxed[T] {",
        "    tag: str",
        "    item: T",
        "}",
        "",
        "pub interface Transformer[T] {",
        "    fn apply(x: T) -> T",
        "}",
        "",
        "pub class Scaler[T] implements Transformer[T] {",
        "    public factor: int",
        "    public fallback: T",
        "    fn init(factor: int, fallback: T) {",
        "        this.factor = factor",
        "        this.fallback = fallback",
        "    }",
        "    public fn apply(x: T) -> T {",
        "        return x",
        "    }",
        "    public fn pair_with[R](x: T, other: R) -> (T, R) {",
        "        return (x, other)",
        "    }",
        "}",
        "",
        "pub class BoostedScaler[T] extends Scaler[T] {",
        "    public bonus: int",
        "    fn init(factor: int, fallback: T, bonus: int) {",
        "        super.init(factor, fallback)",
        "        this.bonus = bonus",
        "    }",
        "    public fn apply(x: T) -> T {",
        "        return x",
        "    }",
        "}",
        "",
        "pub fn run_transformer[T](t: Transformer[T], value: T) -> T {",
        "    return t.apply(value)",
        "}",
        "",
        "pub fn mutate_metric_copy(m: Metric, extra: str) -> Metric {",
        "    let mut local = m",
        "    local.tags.append(extra)",
        "    local.value = local.value + 10",
        "    return local",
        "}",
        "",
        "pub fn make_adder(base: int) -> fn(int) -> int {",
        "    return |x: int| -> int { return x + base }",
        "}",
        "",
        f"pub fn classify_fault(f: {fault_int}) -> int {{",
        "    return match f {",
        f"        {'Fault' if use_from_import else 'errs.Fault'}.Empty => -1,",
        f"        {'Fault' if use_from_import else 'errs.Fault'}.Rejected(v) => v * 3,",
        "    }",
        "}",
        "",
        f"pub fn validate_input(mode: str, n: int) -> int ! {err_t_int} | {err_t_str} | str {{",
        '    if mode == "int_err" {',
        f"        return Err({mk_int}(10, n))",
        "    }",
        '    if mode == "str_err" {',
        f'        return Err({mk_str}(20, "bad:" + str(n)))',
        "    }",
        '    if mode == "plain_err" {',
        '        return Err("plain")',
        "    }",
        "    return n * 2 + 1",
        "}",
        "",
        "pub fn apply_fn[T](x: T, f: fn(T) -> T) -> T {",
        "    return f(x)",
        "}",
    ]
    if use_async:
        domain_lines.extend([
            "",
            f"pub async fn async_step(mode: str, n: int) -> int ! {err_t_int} | {err_t_str} | str {{",
            "    return validate_input(mode, n)",
            "}",
            "",
            f"pub async fn async_validate(mode: str, n: int) -> int ! {err_t_int} | {err_t_str} | str {{",
            "    let v = await async_step(mode, n)?",
            "    return v + 3",
            "}",
        ])
    if use_async_optional:
        domain_lines.extend([
            "",
            "pub async fn async_lookup(k: int) -> int? {",
            "    if k % 2 == 0 {",
            "        return Some(k * 5)",
            "    }",
            "    return None",
            "}",
        ])

    pipeline_lines = [
        'import "errors.wysna" as errs',
        'import "domain.wysna" as dom',
        "",
        "pub async fn run_pipeline(mode: str, n: int) -> str {" if use_async else "pub fn run_pipeline(mode: str, n: int) -> str {",
    ]
    if use_async:
        pipeline_lines.append("    let value = await dom.async_validate(mode, n) else issue {")
    else:
        pipeline_lines.append("    let value = dom.validate_input(mode, n) else issue {")
    pipeline_lines.extend([
        "        let label = match issue {",
        f"            errs.DomainErr[int](e) {'if e.detail > ' + str(threshold) if use_guard else ''} => \"high-int:\" + str(e.code) + \":\" + str(e.detail),",
    ])
    if use_guard:
        pipeline_lines.append('            errs.DomainErr[int](e) => "low-int:" + str(e.code) + ":" + str(e.detail),')
    pipeline_lines.extend([
        '            errs.DomainErr[str](e) => "str:" + str(e.code) + ":" + e.detail,',
        '            str(s) => "plain:" + s,',
        "        }",
        "        return label",
        "    }",
        '    return "ok:" + str(value)',
        "}",
    ])

    main_lines = [
        'import "errors.wysna" as errs',
        'import "domain.wysna" as dom',
        'import "pipeline.wysna" as pipe',
        "",
        "fn main() -> unit {",
        f"    let a = {a}",
        f"    let b = {b}",
        f"    let c = {c}",
        f'    let res = {"await " if use_async else ""}pipe.run_pipeline("{trigger_err}", a + b)',
        '    print("pipe:", res)',
        "    let fscore = dom.classify_fault(errs.Fault[int].Rejected(a))",
        '    print("fault:", fscore)',
    ]

    if use_async_optional:
        main_lines.extend([
            "    let maybe_v = await dom.async_lookup(a) ?? -7",
            '    print("async_opt:", maybe_v)',
        ])

    if use_closure:
        main_lines.extend([
            "    let offset = a + c",
            "    let shift = |x: int| -> int { return x + offset }",
            "    let shifted = dom.apply_fn(b, shift)",
            '    print("closure:", shifted)',
        ])

    if use_returned_closure:
        main_lines.extend([
            "    let adder = dom.make_adder(a + 3)",
            "    let added = dom.apply_fn(b, adder)",
            '    print("ret_closure:", added)',
        ])

    if use_cow_update:
        main_lines.extend([
            '    let m1 = dom.Metric { label: "base", value: a, tags: ["x", "y"] }',
            "    let m2 = dom.Metric { ..m1, value: a + b }",
            "    let m_other = dom.mutate_metric_copy(m1, \"ext\")",
            "    let mut m3 = m2",
            '    m3.tags.append("z")',
            '    print("cow:", m1.value, len(m1.tags), m2.value, len(m2.tags), len(m3.tags), m_other.value, len(m_other.tags))',
        ])

    if use_generic_struct_spread:
        main_lines.extend([
            '    let bx1 = dom.Boxed[int] { tag: "init", item: a }',
            '    let bx2 = dom.Boxed[int] { ..bx1, item: a + b }',
            '    print("gen_struct:", bx1.tag, bx1.item, bx2.tag, bx2.item)',
        ])

    if use_generic_class:
        main_lines.extend([
            "    let sc = dom.BoostedScaler[int](b, 0, 5)",
            "    let kept = dom.run_transformer(sc, a)",
            '    let paired = sc.pair_with(kept, "tag")',
        ])
        if use_tuple_destructure:
            main_lines.extend([
                "    let (p0, p1) = paired",
                '    print("gen_cls:", p0, p1)',
            ])
        else:
            main_lines.append('    print("gen_cls:", paired.0, paired.1)')

    if use_dict_ops:
        main_lines.extend([
            '    let mut counts: dict[str, int] = { "a": a, "b": b }',
            '    let removed = counts.remove("a") ?? -1',
            '    counts["c"] = a + b',
            "    let mut acc = 0",
            "    for k, v in counts {",
            "        acc += v + len(k)",
            "    }",
            '    print("dict:", removed, acc, len(counts))',
            "    counts.clear()",
            '    print("dict_empty:", counts.is_empty())',
        ])

    main_lines.extend([
        "}",
        "main()",
        "",
    ])

    files = {
        "errors.wysna": errors_src,
        "domain.wysna": "\n".join(domain_lines) + "\n",
        "pipeline.wysna": "\n".join(pipeline_lines) + "\n",
        "main.wysna": "\n".join(main_lines),
    }
    return MultiFileCase(seed=seed, files=files, entry="main.wysna", expected_error=None)


NEGATIVE_KINDS = [
    "non_exhaustive_imported_generic_err",
    "incompatible_async_error_propagation",
    "closure_mutates_captured_local",
    "generic_error_specialization_collapse",
    "cross_module_private_field",
    "tuple_destructure_arity_mismatch",
    "missing_super_init_generic_subclass",
    "nested_match_branch_type_mismatch",
    "cross_module_non_pub_function",
    "cross_module_immutable_struct_field_assign",
    "generic_constraint_violation_cross_module",
    "spread_type_mismatch_cross_module",
]


def generate_multifile_negative(seed: int, kind: str | None = None) -> MultiFileCase:
    """Generate multi-file invalid programs whose rejection must agree across Seed and Selfhost without W9999."""
    rng = random.Random(seed)
    chosen = kind or rng.choice(NEGATIVE_KINDS)

    if chosen == "non_exhaustive_imported_generic_err":
        files = {
            "errs.wysna": (
                "pub struct Fail[T] { payload: T }\n"
                'pub fn boom() -> int ! Fail[int] | Fail[str] { return Err(Fail[int] { payload: 1 }) }\n'
            ),
            "main.wysna": (
                'import "errs.wysna" as errs\n'
                "fn main() {\n"
                "    let v = errs.boom() else e {\n"
                "        match e { errs.Fail[int](x) => x.payload }\n"
                "    }\n"
                "    print(v)\n"
                "}\n"
            ),
        }
        return MultiFileCase(seed=seed, files=files, entry="main.wysna", expected_error="W2003")

    if chosen == "incompatible_async_error_propagation":
        files = {
            "errs.wysna": (
                "pub struct Fail[T] { payload: T }\n"
                'pub async fn step() -> int ! Fail[str] { return Err(Fail[str] { payload: "no" }) }\n'
            ),
            "main.wysna": (
                'import "errs.wysna" as errs\n'
                "async fn run() -> int ! errs.Fail[int] | str {\n"
                "    return await errs.step()?\n"
                "}\n"
                "fn main() { let _ = await run() else e { 0 } }\n"
            ),
        }
        return MultiFileCase(seed=seed, files=files, entry="main.wysna", expected_error="W2006")

    if chosen == "closure_mutates_captured_local":
        files = {
            "main.wysna": (
                "fn main() {\n"
                "    let mut xs = [1, 2]\n"
                "    let f = |v: int| -> unit { xs.append(v) }\n"
                "    f(3)\n"
                "}\n"
            ),
        }
        return MultiFileCase(seed=seed, files=files, entry="main.wysna", expected_error="W3001")

    if chosen == "generic_error_specialization_collapse":
        files = {
            "main.wysna": (
                "struct BoxErr[T] { v: T }\n"
                "fn collapse[T](x: T) -> int ! BoxErr[T] | BoxErr[int] {\n"
                "    return Err(BoxErr[int] { v: 1 })\n"
                "}\n"
                "fn main() { let _ = collapse(5) else e { 0 } }\n"
            ),
        }
        return MultiFileCase(seed=seed, files=files, entry="main.wysna", expected_error="W2001")

    if chosen == "cross_module_private_field":
        files = {
            "vault.wysna": (
                "pub class Vault {\n"
                "    private secret: int\n"
                "    fn init(s: int) { this.secret = s }\n"
                "}\n"
            ),
            "main.wysna": (
                'import "vault.wysna" as v\n'
                "fn main() {\n"
                "    let box = v.Vault(42)\n"
                "    print(box.secret)\n"
                "}\n"
            ),
        }
        return MultiFileCase(seed=seed, files=files, entry="main.wysna", expected_error="W3002")

    if chosen == "tuple_destructure_arity_mismatch":
        files = {
            "main.wysna": (
                'fn pair() -> (int, str) { return (1, "ok") }\n'
                "fn main() {\n"
                "    let (a, b, c) = pair()\n"
                "    print(a, b, c)\n"
                "}\n"
            ),
        }
        return MultiFileCase(seed=seed, files=files, entry="main.wysna", expected_error="W2001")

    if chosen == "missing_super_init_generic_subclass":
        files = {
            "base.wysna": (
                "pub class Base[T] {\n"
                "    public item: T\n"
                "    fn init(item: T) { this.item = item }\n"
                "}\n"
            ),
            "main.wysna": (
                'import "base.wysna" as b\n'
                "class Sub extends b.Base[int] {\n"
                "    public extra: int\n"
                "    fn init(extra: int) { this.extra = extra }\n"
                "}\n"
                "fn main() { let _ = Sub(1) }\n"
            ),
        }
        return MultiFileCase(seed=seed, files=files, entry="main.wysna", expected_error="W3004")

    if chosen == "cross_module_non_pub_function":
        files = {
            "secret.wysna": (
                "fn hidden() -> int { return 7 }\n"
                "pub fn visible() -> int { return hidden() }\n"
            ),
            "main.wysna": (
                'import "secret.wysna" as s\n'
                "fn main() { print(s.hidden()) }\n"
            ),
        }
        return MultiFileCase(seed=seed, files=files, entry="main.wysna", expected_error="W1001")

    if chosen == "cross_module_immutable_struct_field_assign":
        files = {
            "rec.wysna": "pub struct Point { x: int, y: int }\n",
            "main.wysna": (
                'import "rec.wysna" as r\n'
                "fn main() {\n"
                "    let p = r.Point { x: 1, y: 2 }\n"
                "    p.x = 3\n"
                "}\n"
            ),
        }
        return MultiFileCase(seed=seed, files=files, entry="main.wysna", expected_error="W3001")

    if chosen == "generic_constraint_violation_cross_module":
        files = {
            "iface.wysna": (
                "pub interface Named { fn name() -> str }\n"
                "pub struct Holder[T: Named] { value: T }\n"
            ),
            "main.wysna": (
                'import "iface.wysna" as m\n'
                "fn main() {\n"
                "    let h = m.Holder[int] { value: 1 }\n"
                "    print(h.value)\n"
                "}\n"
            ),
        }
        return MultiFileCase(seed=seed, files=files, entry="main.wysna", expected_error="W2001")

    if chosen == "spread_type_mismatch_cross_module":
        files = {
            "rec.wysna": (
                "pub struct A { x: int }\n"
                "pub struct B { x: int }\n"
            ),
            "main.wysna": (
                'import "rec.wysna" as r\n'
                "fn main() {\n"
                "    let b = r.B { x: 2 }\n"
                "    let a = r.A { ..b, x: 3 }\n"
                "    print(a.x)\n"
                "}\n"
            ),
        }
        return MultiFileCase(seed=seed, files=files, entry="main.wysna", expected_error="W2001")

    # nested_match_branch_type_mismatch
    files = {
        "main.wysna": (
            "enum E { A(int), B }\n"
            "fn bad(e: E, flag: bool) -> int {\n"
            "    return match e {\n"
            '        E.A(n) => match flag { true => n, false => "oops" },\n'
            "        E.B => 0,\n"
            "    }\n"
            "}\n"
            "fn main() { print(bad(E.B, true)) }\n"
        ),
    }
    return MultiFileCase(seed=seed, files=files, entry="main.wysna", expected_error="W2001")


def materialize_case(case: MultiFileCase, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    for rel, content in case.files.items():
        path = directory / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return directory / case.entry


def _rewrite_relative_imports(source: str, base_dir: Path, files: dict[str, str]) -> str:
    rewritten = source
    for rel in files:
        if rel != "main.wysna":
            rewritten = rewritten.replace(f'"{rel}"', f'"{(base_dir / rel).as_posix()}"')
    return rewritten


def reduce_multifile_failure(
    case: MultiFileCase,
    oracle_fn: Callable[[dict[str, str]], bool],
    max_steps: int = 40,
) -> dict[str, str]:
    """Delta-debug lines across files in a multi-file project while preserving failure."""
    reduced = dict(case.files)
    steps = 0
    for name in sorted(reduced):
        lines = reduced[name].splitlines()
        idx = 0
        while idx < len(lines) and steps < max_steps:
            steps += 1
            candidate_lines = lines[:idx] + lines[idx + 1:]
            candidate = dict(reduced)
            candidate[name] = "\n".join(candidate_lines) + "\n"
            try:
                if oracle_fn(candidate):
                    lines = candidate_lines
                    reduced = candidate
                    continue
            except Exception:
                pass
            idx += 1
    return reduced


def save_divergence_artifact(divergence: dict, failure_dir: Path) -> Path:
    failure_dir.mkdir(parents=True, exist_ok=True)
    out_dir = failure_dir / f"fuzz_a01_{divergence['kind']}_seed_{divergence['seed']}"
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in divergence["files"].items():
        target = out_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    (out_dir / "report.json").write_text(
        json.dumps(
            {
                "kind": divergence["kind"],
                "seed": divergence["seed"],
                "reason": divergence.get("reason"),
                "expected_error": divergence.get("expected_error"),
                "results": divergence["results"],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return out_dir


def run_multifile_case(case: MultiFileCase, selfhost_bin: Path, work_root: Path, sanitize: bool = False):
    case_dir = work_root / f"case_{case.seed}"
    if case_dir.exists():
        shutil.rmtree(case_dir)
    materialize_case(case, case_dir)
    for rel in list(case.files):
        if rel != case.entry:
            p = case_dir / rel
            p.write_text(_rewrite_relative_imports(p.read_text(encoding="utf-8"), case_dir, case.files), encoding="utf-8")
    entry_src = _rewrite_relative_imports(case.files[case.entry], case_dir, case.files)
    results = []
    for engine in ENGINES:
        res = run_engine(engine, entry_src, f"fuzz_mf_{case.seed}.wysna", str(selfhost_bin), case_dir, sanitize=sanitize)
        results.append(res)
    return results


def run_multifile_fuzz_campaign(
    positive_count: int = 30,
    negative_count: int = 24,
    base_seed: int = 9100,
    selfhost_bin: Path | None = None,
    sanitize: bool = False,
    save_failures_dir: Path | None = None,
) -> dict:
    if selfhost_bin is None:
        from compiler.Bootstrap import ensure_selfhost_compiler_binary
        selfhost_bin = Path(ensure_selfhost_compiler_binary(str(ROOT)))
    divergences = []
    with tempfile.TemporaryDirectory(prefix="wysna-fuzz-a01-") as tmp:
        work = Path(tmp)
        for i in range(positive_count):
            seed = base_seed + i
            case = generate_multifile_positive(seed)
            results = run_multifile_case(case, selfhost_bin, work, sanitize=sanitize)
            eq, reason = compare(results)
            bad = (not eq) or any(
                (not r.compile_ok) or (not r.runtime_ok) or r.exit_code != 0 or r.infrastructure_error
                for r in results
            )
            if bad:
                div = {
                    "kind": "positive",
                    "seed": seed,
                    "reason": reason,
                    "files": case.files,
                    "results": [asdict(r) for r in results],
                }
                if save_failures_dir is not None:
                    save_divergence_artifact(div, save_failures_dir)
                divergences.append(div)
        for i in range(negative_count):
            seed = base_seed + 5000 + i
            kind = NEGATIVE_KINDS[i % len(NEGATIVE_KINDS)] if i < len(NEGATIVE_KINDS) else None
            case = generate_multifile_negative(seed, kind=kind)
            results = run_multifile_case(case, selfhost_bin, work, sanitize=False)
            bad = False
            for r in results:
                if r.compile_ok or r.exit_code == 0 or "[W9999]" in r.stderr or (case.expected_error and case.expected_error not in r.stderr):
                    bad = True
                    break
            if bad:
                div = {
                    "kind": "negative",
                    "seed": seed,
                    "expected_error": case.expected_error,
                    "files": case.files,
                    "results": [asdict(r) for r in results],
                }
                if save_failures_dir is not None:
                    save_divergence_artifact(div, save_failures_dir)
                divergences.append(div)
    return {
        "positive_count": positive_count,
        "negative_count": negative_count,
        "base_seed": base_seed,
        "divergences": divergences,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="FUZZ-A01 multi-file four-engine differential fuzzer")
    parser.add_argument("--positive", type=int, default=20)
    parser.add_argument("--negative", type=int, default=12)
    parser.add_argument("--seed", type=int, default=9100)
    parser.add_argument("--selfhost-bin", type=Path, default=None)
    parser.add_argument("--sanitize", action="store_true")
    parser.add_argument("--save-failures", type=Path, default=None)
    args = parser.parse_args(argv)
    summary = run_multifile_fuzz_campaign(
        positive_count=args.positive,
        negative_count=args.negative,
        base_seed=args.seed,
        selfhost_bin=args.selfhost_bin,
        sanitize=args.sanitize,
        save_failures_dir=args.save_failures,
    )
    print(json.dumps({
        "positive_count": summary["positive_count"],
        "negative_count": summary["negative_count"],
        "divergence_count": len(summary["divergences"]),
    }, indent=2))
    for div in summary["divergences"][:5]:
        print("--- DIVERGENCE ---", div["kind"], div["seed"], div.get("reason") or div.get("expected_error"))
        for r in div["results"]:
            print(" ", r["engine"], "compile_ok=", r["compile_ok"], "exit=", r["exit_code"], "stdout=", repr(r["stdout"][:80]), "stderr=", repr(r["stderr"][:180]))
    return 0 if not summary["divergences"] else 1


if __name__ == "__main__":
    sys.exit(main())
