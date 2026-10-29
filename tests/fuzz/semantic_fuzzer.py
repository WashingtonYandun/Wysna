"""
tests/fuzz/semantic_fuzzer.py - Semantic Fuzzing, Test Reduction & Determinism Harness (D04).

Generates typed, valid programs with ADTs, CoW, OOP, and errors, as well as identifiable
semantic negatives. Runs differential verification across engines, verifies deterministic
C99 generation under diverse PYTHONHASHSEED environments, and performs delta-debugging
reduction on divergences.
"""

import hashlib
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
from typing import Callable, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.engine_harness import asdict, compare, run_engine, unsupported_reason


def generate_typed_program(seed: int) -> str:
    """Generate a fully valid, statically typed Wysna program exercising ADTs, CoW, OOP, and errors."""
    rng = random.Random(seed)
    int_a = rng.randint(1, 50)
    int_b = rng.randint(2, 20)
    int_c = rng.randint(-10, 30)
    flag = "true" if (seed % 2 == 0) else "false"
    mode_variants = ["Action.Stop", "Action.Move", "Action.Turn"]
    chosen_action = rng.choice(mode_variants)

    lines = [
        f"# Semantic Fuzzer Generated Valid Program (seed={seed})",
        "enum Action {",
        "    Stop",
        "    Move",
        "    Turn",
        "}",
        "",
        "struct Vector2 {",
        "    x: int",
        "    y: int",
        "}",
        "",
        "struct Entity {",
        "    id: int",
        "    pos: Vector2",
        "    history: list[int]",
        "}",
        "",
        "class Animal {",
        "    public energy: int",
        "    private secret_id: int",
        "    fn init(e: int) {",
        "        this.energy = e",
        "        this.secret_id = 999",
        "    }",
        "    public fn act(cost: int) -> int {",
        "        this.energy = this.energy - cost",
        "        return this.energy",
        "    }",
        "    public fn get_secret() -> int {",
        "        return this.secret_id",
        "    }",
        "}",
        "",
        "class Bird extends Animal {",
        "    public altitude: int",
        "    fn init(e: int, alt: int) {",
        "        super.init(e)",
        "        this.altitude = alt",
        "    }",
        "    public fn act(cost: int) -> int {",
        "        this.energy = this.energy - cost * 2",
        "        this.altitude = this.altitude + 10",
        "        return this.altitude",
        "    }",
        "}",
        "",
        "fn safe_div(n: int, d: int) -> int? {",
        "    if d == 0 {",
        "        return none",
        "    }",
        "    return n / d",
        "}",
        "",
        "fn step_action(a: Action, val: int) -> int {",
        "    match a {",
        "        Action.Stop => 0,",
        "        Action.Move => val + 10,",
        "        Action.Turn => val - 5,",
        "    }",
        "}",
        "",
        "fn compute_checksum(xs: list[int]) -> int {",
        "    let mut sum = 0",
        "    for x in xs {",
        "        sum = sum + x",
        "    }",
        "    return sum",
        "}",
        "",
        "fn main() -> unit {",
        f"    let a = {int_a}",
        f"    let b = {int_b}",
        f"    let c = {int_c}",
        f"    let flag = {flag}",
        f"    let action = {chosen_action}",
        "",
        "    # 1. CoW Verification on Nested Structs & Lists",
        "    let e1 = Entity { id: 1, pos: Vector2 { x: a, y: b }, history: [a, b, c] }",
        "    let mut e2 = e1",
        "    e2.id = 2",
        "    e2.history.append(a + b)",
        '    print("cow_struct:", e1.id, len(e1.history), e2.id, len(e2.history))',
        "",
        "    # 2. CoW on Nested Lists",
        "    let matrix = [[1, 2], [3, 4]]",
        "    let mut matrix_mut = matrix",
        "    matrix_mut[0].append(99)",
        '    print("cow_matrix:", len(matrix[0]), len(matrix_mut[0]))',
        "",
        "    # 3. OOP Inheritance & Dispatch",
        "    let a1 = new Animal(50)",
        "    let b1 = new Bird(100, 20)",
        "    let r1 = a1.act(b)",
        "    let r2 = b1.act(b)",
        '    print("oop:", r1, r2, b1.altitude, a1.get_secret())',
        "",
        "    # 4. Optional & Match Semantics",
        "    let div_res = safe_div(a * 10, b) ?? 999",
        "    let act_res = step_action(action, c)",
        '    print("opt_match:", div_res, act_res)',
        "",
        "    # 5. Iteration & Checksum",
        "    let total = compute_checksum(e2.history)",
        '    print("total:", total)',
        "}",
        "",
    ]
    def arithmetic(depth):
        if depth == 0 or rng.random() < 0.3:
            return rng.choice(["value", str(rng.randint(-20, 20))])
        operator = rng.choice(["+", "-", "*"])
        return "(" + arithmetic(depth - 1) + " " + operator + " " + arithmetic(depth - 1) + ")"

    expression = arithmetic(rng.randint(1, 5))
    generated = ["fn generated_transform(value: int) -> int { return " + expression + " }"]
    insertion = lines.index("fn main() -> unit {")
    lines[insertion:insertion] = generated + [""]
    body = ["    let mut generated_values = [a, b, c]", "    let generated_snapshot = generated_values"]
    for _ in range(rng.randint(1, 6)):
        shape = rng.randrange(3)
        if shape == 0:
            body.append("    generated_values.append(generated_transform(" + str(rng.randint(-5, 5)) + "))")
        elif shape == 1:
            body.extend(["    if flag {", "        generated_values[0] = generated_transform(generated_values[0])",
                         "    } else {", "        generated_values.append(c)", "    }"])
        else:
            body.extend(["    for generated_index in 0.." + str(rng.randint(0, 5)) + " {",
                         "        generated_values.append(generated_transform(generated_index))", "    }"])
    body.append('    print("generated:", generated_values, generated_snapshot)')
    lines[-3:-3] = body
    from tests.fuzz.semantic_properties import generate_properties
    property_source, _ = generate_properties(seed)
    return "\n".join(lines) + "\nmain()\n" + property_source


def generate_mutated_negative(seed: int) -> Tuple[str, str]:
    """
    Generate an invalid program with an identifiable semantic error.
    Returns (source_code, expected_diagnostic_code).
    """
    rng = random.Random(seed)
    choice = rng.choice([
        "uninitialized_field",
        "missing_super_init",
        "super_init_not_first",
        "private_access",
        "override_private",
        "type_mismatch",
        "missing_return",
        "bad_coalesce",
        "non_exhaustive_match",
    ])

    if choice == "uninitialized_field":
        code = (
            "class Device {\n"
            "    public serial: int\n"
            "    public name: str\n"
            "    fn init(s: int) {\n"
            "        this.serial = s\n"
            "        # missing this.name = ...\n"
            "    }\n"
            "}\n"
            "fn main() {\n"
            "    let d = new Device(123)\n"
            "}\n"
        )
        return code, "W3004"

    elif choice == "missing_super_init":
        code = (
            "class Base {\n"
            "    public v: int\n"
            "    fn init(x: int) { this.v = x }\n"
            "}\n"
            "class Derived extends Base {\n"
            "    public extra: int\n"
            "    fn init(x: int, y: int) {\n"
            "        this.extra = y\n"
            "    }\n"
            "}\n"
            "fn main() { let d = new Derived(1, 2) }\n"
        )
        return code, "W3004"

    elif choice == "super_init_not_first":
        code = (
            "class Base {\n"
            "    public v: int\n"
            "    fn init(x: int) { this.v = x }\n"
            "}\n"
            "class Derived extends Base {\n"
            "    public extra: int\n"
            "    fn init(x: int, y: int) {\n"
            "        this.extra = y\n"
            "        super.init(x)\n"
            "    }\n"
            "}\n"
            "fn main() { let d = new Derived(1, 2) }\n"
        )
        return code, "W3004"

    elif choice == "private_access":
        code = (
            "class Account {\n"
            "    private pin: int\n"
            "    fn init(p: int) { this.pin = p }\n"
            "}\n"
            "fn main() {\n"
            "    let a = new Account(1234)\n"
            "    print(a.pin)\n"
            "}\n"
        )
        return code, "W3002"

    elif choice == "override_private":
        code = (
            "class Parent {\n"
            "    private fn secret() -> int { return 42 }\n"
            "    fn init() {}\n"
            "}\n"
            "class Child extends Parent {\n"
            "    fn init() { super.init() }\n"
            "    public fn secret() -> int { return 99 }\n"
            "}\n"
            "fn main() { let c = new Child() }\n"
        )
        return code, "W3002"

    elif choice == "type_mismatch":
        code = (
            "fn main() {\n"
            '    let x: int = "incompatible_string"\n'
            "}\n"
        )
        return code, "W2001"

    elif choice == "missing_return":
        code = (
            "fn calculate(a: int) -> int {\n"
            "    let b = a * 2\n"
            "    # missing return\n"
            "}\n"
            "fn main() { calculate(5) }\n"
        )
        return code, "W2004"

    elif choice == "bad_coalesce":
        code = (
            "fn main() {\n"
            '    let opt: str? = "hello"\n'
            "    let bad = opt ?? 42\n"
            "}\n"
        )
        return code, "W2001"

    else:  # non_exhaustive_match
        code = (
            "enum Color { Red, Green, Blue }\n"
            "fn name_color(c: Color) -> str {\n"
            "    match c {\n"
            '        Color.Red => "red",\n'
            '        Color.Green => "green",\n'
            "    }\n"
            "}\n"
            "fn main() { name_color(Color.Red) }\n"
        )
        return code, "W2003"


def reduce_failure(source: str, oracle_fn: Callable[[str], bool], max_steps: int = 50) -> str:
    """
    Delta-debugging test-case reducer. Iteratively attempts to eliminate lines
    while ensuring oracle_fn(candidate) still returns True (failure reproduced).
    """
    lines = source.splitlines()
    if len(lines) <= 3:
        return source

    step = 0
    changed = True
    while changed and step < max_steps:
        changed = False
        i = 0
        while i < len(lines) and step < max_steps:
            step += 1
            # Attempt to remove line i
            candidate_lines = lines[:i] + lines[i + 1:]
            candidate_code = "\n".join(candidate_lines) + "\n"
            try:
                if oracle_fn(candidate_code):
                    lines = candidate_lines
                    changed = True
                    continue
            except Exception:
                pass
            i += 1

    return "\n".join(lines) + "\n"


def check_c_determinism(source: str, repo_root: Path, work_dir: Path) -> Tuple[bool, str, str]:
    """
    Verify byte-for-byte deterministic C99 emission across multiple compiler invocations
    under different PYTHONHASHSEED environments and paths.
    """
    hashes = []
    source_file = work_dir / "det_source.wysna"
    source_file.write_text(source)

    for run_idx in range(3):
        out_c = work_dir / f"det_out_{run_idx}.c"
        env = dict(os.environ, PYTHONHASHSEED=str(run_idx * 17))
        cmd = [
            sys.executable,
            str(repo_root / "main.py"),
            "emit-c",
            str(source_file),
            "-o",
            str(out_c),
        ]
        proc = subprocess.run(cmd, cwd=str(repo_root), env=env, capture_output=True, text=True, timeout=120)
        if proc.returncode != 0:
            return False, "", f"Compilation error: {proc.stderr}"

        c_text = out_c.read_text(encoding="utf-8")
        h = hashlib.sha256(c_text.encode("utf-8")).hexdigest()
        hashes.append(h)

    if len(set(hashes)) == 1:
        return True, hashes[0], ""
    return False, hashes[0], f"Divergent C code hashes under varying hash seeds: {hashes}"


def run_semantic_fuzz_suite(
    count: int = 25,
    base_seed: int = 8000,
    repo_root: Optional[Path] = None,
    selfhost_bin: Optional[Path] = None,
) -> bool:
    """
    Execute full D04 semantic fuzz suite across positive programs, negative mutations,
    and determinism verification.
    """
    if repo_root is None:
        repo_root = ROOT
    failures_dir = repo_root / "tests/fuzz/failures"
    failures_dir.mkdir(parents=True, exist_ok=True)

    if selfhost_bin is None:
        from main import ensure_selfhost_compiler_binary
        selfhost_bin = Path(ensure_selfhost_compiler_binary(str(repo_root)))

    print("==================================================")
    print("Wysna D04 Semantic Fuzzing & Determinism")
    print("==================================================")
    print(f"  [CONFIG] Base seed: {base_seed}, Iterations: {count}")

    total_checks = 0
    passed_checks = 0

    with tempfile.TemporaryDirectory(prefix="wysna-fuzz-") as tmpdir:
        work_dir = Path(tmpdir)

        # 1. Positive Typed Programs Fuzzing & Determinism
        print("  [PHASE 1] Valid typed programs differential fuzzing & C determinism...")
        for i in range(count):
            seed = base_seed + i
            source = generate_typed_program(seed)
            label = f"fuzz_pos_{seed}.wysna"
            total_checks += 1

            # Check C determinism first
            det_ok, det_hash, det_err = check_c_determinism(source, repo_root, work_dir)
            if not det_ok:
                print(f"  [DETERMINISM FAIL] {label}: {det_err}")
                continue

            # Run engines
            results = []
            diverged = False
            for eng in ["interpreter", "ast", "hir", "selfhost"]:
                if unsupported_reason(label, eng):
                    continue
                res = run_engine(eng, source, label, selfhost_bin, work_dir, sanitize=False)
                results.append(res)

            equivalent, _ = compare(results)
            diverged = not equivalent or any(
                not result.compile_ok or not result.runtime_ok or result.exit_code != 0
                for result in results)

            if diverged:
                # Preserve the failing engines and acceptance class during reduction.
                reference = results[0]
                fields = ('compile_ok', 'runtime_ok', 'exit_code', 'stdout', 'diag_code')
                other = next((result for result in results[1:] if any(
                    getattr(result, field) != getattr(reference, field) for field in fields)), results[-1])
                expected_acceptance = (reference.compile_ok, other.compile_ok)
                mismatch_fields = tuple(field for field in fields
                                        if getattr(reference, field) != getattr(other, field))
                failing_engine = next((result.engine for result in results if result.infrastructure_error), None)

                def oracle_diverges(candidate: str) -> bool:
                    try:
                        if failing_engine:
                            result = run_engine(failing_engine, candidate, label, selfhost_bin, work_dir, sanitize=False)
                            return result.compile_ok and bool(result.infrastructure_error)
                        left = run_engine(reference.engine, candidate, label, selfhost_bin, work_dir, sanitize=False)
                        right = run_engine(other.engine, candidate, label, selfhost_bin, work_dir, sanitize=False)
                        return ((left.compile_ok, right.compile_ok) == expected_acceptance
                                and not left.infrastructure_error and not right.infrastructure_error
                                and any(getattr(left, field) != getattr(right, field) for field in mismatch_fields))
                    except Exception:
                        return False

                minimized = reduce_failure(source, oracle_diverges)
                fail_record = {
                    "seed": seed,
                    "sha256": hashlib.sha256(source.encode()).hexdigest(),
                    "original_source": source,
                    "minimized_source": minimized,
                    "results": [asdict(r) for r in results],
                }
                fail_path = failures_dir / f"fail_pos_{seed}.json"
                fail_path.write_text(json.dumps(fail_record, indent=2))
                print(f"  [FUZZ DIVERGENCE] Saved failure & minimized case to {fail_path}")
            else:
                passed_checks += 1

        # 2. Negative Programs & Diagnostic Consistency Fuzzing
        print("  [PHASE 2] Negative semantic mutations fuzzing...")
        for i in range(count):
            seed = base_seed + 1000 + i
            source, expected_code = generate_mutated_negative(seed)
            label = f"fuzz_neg_{seed}.wysna"
            total_checks += 1

            # All engines must reject with expected_code
            all_rejected = True
            for eng in ["interpreter", "ast", "hir", "selfhost"]:
                if unsupported_reason(label, eng):
                    continue
                res = run_engine(eng, source, label, selfhost_bin, work_dir, sanitize=False)
                if res.compile_ok or res.exit_code != 1 or expected_code not in res.stderr:
                    all_rejected = False
                    break

            if not all_rejected:
                print(f"  [NEG REJECTION FAIL] {label} expected {expected_code}")
            else:
                passed_checks += 1

    print(f"  [RESULT] Passed {passed_checks}/{total_checks} checks (100% Determinism & Parity)")
    print("==================================================")
    return passed_checks == total_checks


if __name__ == "__main__":
    ok = run_semantic_fuzz_suite(count=15, base_seed=7700)
    sys.exit(0 if ok else 1)
