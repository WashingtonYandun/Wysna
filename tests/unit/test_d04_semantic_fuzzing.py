"""
test_d04_semantic_fuzzing.py - Conformance suite for Milestone D04: Semantic Fuzzing & Determinism.

Verifies:
1. Typed valid program generation and byte-for-byte C99 determinism across PYTHONHASHSEED.
2. Semantic negative mutations rejection and diagnostic alignment across engines.
3. Delta-debugging test-case reduction (failure minimizer).
4. Preservation of frontend smoke fuzzer with 0 tracebacks.
"""

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.fuzz.semantic_fuzzer import (
    check_c_determinism,
    generate_mutated_negative,
    generate_typed_program,
    reduce_failure,
    run_semantic_fuzz_suite,
)


def test_typed_program_generation_and_c_determinism():
    print("  [TEST] Typed valid program generation & C99 determinism across hash seeds...")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        work_dir = Path(td)
        for seed in [101, 202, 303]:
            code = generate_typed_program(seed)
            assert "fn main() -> unit" in code, "Generated program must contain main"
            assert "enum Action" in code, "Generated program must contain ADT enum"
            assert "class Animal" in code, "Generated program must contain OOP class"
            ok, h, err = check_c_determinism(code, ROOT, work_dir)
            assert ok, f"Determinism failure on seed {seed}: {err}"
            assert len(h) == 64, "Expected valid SHA256 hex string"


def test_semantic_negative_mutations():
    print("  [TEST] Semantic negative mutations and diagnostic code generation...")
    expected_codes = set()
    for seed in range(500, 520):
        code, diag_code = generate_mutated_negative(seed)
        assert diag_code in ("W3004", "W3002", "W2001", "W2004", "W2003"), f"Unexpected code {diag_code}"
        expected_codes.add(diag_code)
        assert "fn main" in code, "Mutated negative must have main function"

    assert len(expected_codes) >= 4, f"Expected diverse error codes, got {expected_codes}"


def test_failure_reduction_delta_debugging():
    print("  [TEST] Delta-debugging failure reducer...")
    bloated_code = (
        "fn unused1() -> int { return 1 }\n"
        "fn unused2() -> int { return 2 }\n"
        "fn main() {\n"
        '    let x: int = "bad_type"\n'
        "    let y = 10\n"
        "    let z = y + 20\n"
        "}\n"
    )

    # Oracle checks if the string 'bad_type' is still assigned to an int
    def oracle_has_type_error(candidate: str) -> bool:
        return 'let x: int = "bad_type"' in candidate

    reduced = reduce_failure(bloated_code, oracle_has_type_error, max_steps=20)
    assert 'let x: int = "bad_type"' in reduced, "Reducer must preserve the target fault"
    assert "unused1" not in reduced, "Reducer should remove irrelevant declarations"
    assert "unused2" not in reduced, "Reducer should remove irrelevant declarations"


def test_smoke_frontend_fuzzer_preserved():
    print("  [TEST] Preservation of existing frontend smoke fuzzer (0 tracebacks)...")
    from tests.differential.differential_test import run_fuzz_suite
    ok = run_fuzz_suite(fuzz_count=50, base_seed=9500, repo_root=str(ROOT))
    assert ok, "Smoke fuzz suite must pass with 0 internal tracebacks"


def run_all() -> bool:
    print("==================================================")
    print("Wysna D04 Semantic Fuzzing & Determinism Suite")
    print("==================================================")
    try:
        test_typed_program_generation_and_c_determinism()
        test_semantic_negative_mutations()
        test_failure_reduction_delta_debugging()
        test_smoke_frontend_fuzzer_preserved()
        # Run a fast 5-iteration live fuzz suite
        ok = run_semantic_fuzz_suite(count=5, base_seed=7900)
        assert ok, "Semantic fuzz suite failed"
        print("==================================================")
        print("ALL D04 SEMANTIC FUZZING TESTS PASSED.")
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
