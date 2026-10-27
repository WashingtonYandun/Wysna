"""
test_generic_hardening.py - Conformance tests for generic hardening and code bloat metrics.

Verifies:
1. Canonical GenericInstanceKey hash and equality semantics.
2. Monomorphization caching and deduplication telemetry.
3. Instantiation depth budget (W2011) without finite-growth false positives.
4. Direct recursive struct cycle detection (W2011).
5. CLI --stats output format and telemetry fields.
"""

import json
import tempfile
import os
import subprocess
import sys
from checker.TypeChecker import TypeChecker, GenericInstanceKey
from parser.Parser import Parser
from error.Error import TypeError


def test_generic_instance_key():
    k1 = GenericInstanceKey("Box", (1, 2))
    k2 = GenericInstanceKey("Box", (1, 2))
    k3 = GenericInstanceKey("Box", (1, 3))
    k4 = GenericInstanceKey("Pair", (1, 2))

    assert k1 == k2, "Identical keys must compare equal"
    assert hash(k1) == hash(k2), "Identical keys must have equal hashes"
    assert k1 != k3, "Keys with different type IDs must not compare equal"
    assert k1 != k4, "Keys with different template names must not compare equal"

    d = {k1: "cached"}
    assert d.get(k2) == "cached", "Key lookup in dictionary must succeed via hash/eq"
    print("  [PASS] GenericInstanceKey hashing and equality")


def test_canonical_deduplication_telemetry():
    code = """
    struct Box[T] {
        val: T
    }

    fn get_val[T](b: Box[T]) -> T {
        return b.val
    }

    let b1 = Box(10)
    let b2 = Box(20)
    let v1 = get_val(b1)
    let v2 = get_val(b2)
    """
    ast = Parser(file="test_dedup.wysna").parse(code)
    checker = TypeChecker(file="test_dedup.wysna")
    checker.check(ast)

    telem = checker.telemetry
    assert telem.specialized_structs == 1, f"Expected 1 specialized struct, got {telem.specialized_structs}"
    assert telem.specialized_functions == 1, f"Expected 1 specialized fn, got {telem.specialized_functions}"
    assert telem.specialization_requests > 2, f"Expected >2 specialization requests, got {telem.specialization_requests}"
    assert telem.deduplicated_hits > 0, f"Expected >0 deduplicated hits, got {telem.deduplicated_hits}"
    print("  [PASS] Canonical deduplication telemetry")


def test_unbounded_instantiation_budget():
    code = """
    fn expand[T](x: T) {
        expand([x])
    }

    expand(10)
    """
    ast = Parser(file="test_growth.wysna").parse(code)
    checker = TypeChecker(file="test_growth.wysna")
    caught = False
    try:
        checker.check(ast)
    except TypeError as err:
        assert err.code == "W2011", f"Expected W2011, got {err.code}"
        assert "depth limit exceeded" in str(err), f"Expected resource limit message, got {err}"
        caught = True
    assert caught, "Expected TypeError W2011 for structural growth cycle"
    print("  [PASS] Unbounded expansion depth budget (W2011)")


def test_direct_struct_recursion_cycle_detection():
    code = """
    struct Node[T] {
        val: T,
        next: Node[T]
    }

    let n: Node[int] = Node(1, Node(2, 0))
    """
    ast = Parser(file="test_direct.wysna").parse(code)
    checker = TypeChecker(file="test_direct.wysna")
    caught = False
    try:
        checker.check(ast)
    except TypeError as err:
        assert err.code == "W2011", f"Expected W2011, got {err.code}"
        assert "Recursive generic struct cycle" in str(err), f"Expected recursive cycle message, got {err}"
        caught = True
    assert caught, "Expected TypeError W2011 for direct struct recursion"
    print("  [PASS] Direct struct recursion cycle detection (W2011)")


def test_cli_stats_output():
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    main_py = os.path.join(repo_root, "main.py")
    test_program = os.path.join(repo_root, "tests", "runtime", "generic_tree_recursive.wysna")
    proc = subprocess.run(
        [sys.executable, main_py, "emit-c", test_program, "--stats", "-o", os.devnull],
        capture_output=True,
        text=True,
        cwd=repo_root,
        timeout=20
    )
    assert proc.returncode == 0, f"CLI error: {proc.stderr}"
    data = json.loads(proc.stderr)
    assert data.get("status") == "ok", "Expected status ok in JSON stats"
    telemetry = data.get("telemetry", {})
    assert "monomorphization" in telemetry, "Expected monomorphization in telemetry"
    assert "codegen" in telemetry, "Expected codegen in telemetry"
    mono = telemetry["monomorphization"]
    assert mono["specialized_structs"] == 1, f"Expected 1 specialized struct, got {mono['specialized_structs']}"
    assert mono["specialized_enums"] == 1, f"Expected 1 specialized enum, got {mono['specialized_enums']}"
    codegen = telemetry["codegen"]
    assert codegen["generated_c_lines"] > 0, "Expected generated_c_lines > 0"
    print("  [PASS] CLI --stats JSON output and telemetry verification")


def test_hardening_regressions():
    from checker.WysnaType import IntType, TupleType, ResultType, OptionalType, ListType, FunctionType
    from codegen.CCodeGen import CCodeGen
    from hir.HirBuilder import HirBuilder
    from hir.HirCCodeGen import HirCCodeGen

    key = GenericInstanceKey("Box", (3,))
    for attr, value in [("type_ids", (4,)), ("template_name", "Other")]:
        try:
            setattr(key, attr, value)
        except AttributeError:
            pass
        else:
            raise AssertionError("Specialization keys must be immutable")

    checker = TypeChecker()
    assert checker._type_complexity(TupleType([])) == 1
    assert checker._type_complexity(TupleType([IntType, ListType(IntType)])) == 4
    assert checker._type_complexity(ResultType(IntType, OptionalType(IntType))) == 4
    assert checker._type_complexity(FunctionType([IntType], IntType)) == 3

    sources = [
        ("fn id[T](x: T) -> T { return x }\nlet pair = id((1, true))", 1),
        ("fn fixed[T](x: T) -> unit { fixed[list[int]]([1]) }\nfixed(1)", 2),
        ("fn fixed[T](x: T) -> unit { fixed([1]) }\nfixed(1)", 2),
        ("fn a[T](x: T) -> unit { b[list[int]]([1]) }\nfn b[T](x: T) -> unit { a[list[int]]([1]) }\na(1)", 3),
    ]
    for source, expected in sources:
        ast = Parser().parse(source)
        checker = TypeChecker()
        checker.check(ast)
        assert len(checker.monomorphized_fns) == expected

    checker._monomorphize_depth = 64
    template = checker.generic_fn_templates["a"]
    assert checker._monomorphize_fn(template, [IntType], 1, 1) is not None

    for filename in ["generic_pair.wysna", "generic_tree_recursive.wysna"]:
        path = os.path.join("tests", "runtime", filename)
        with open(path, encoding="utf-8") as stream:
            ast = Parser(file=path).parse(stream.read())
        checker = TypeChecker(file=path)
        checker.check(ast)
        hir = HirBuilder(checker.interner, file_path=path).lower(ast)
        for generator, unit in [(CCodeGen(file=path), ast), (HirCCodeGen(checker.interner, file_path=path), hir)]:
            output = generator.generate(unit)
            assert generator.telemetry.generated_c_lines == len(output.splitlines())
            assert generator.telemetry.generated_c_bytes == len(output.encode("utf-8"))

    for source in [
        "fn expand[T](x: T) { expand([x]) }\nexpand(1)",
        "fn a[T](x: T) { b([x]) }\nfn b[T](x: T) { a([x]) }\na(1)",
    ]:
        try:
            TypeChecker().check(Parser().parse(source))
        except TypeError as error:
            assert error.code == "W2011"
            assert "depth limit exceeded" in str(error)
        else:
            raise AssertionError("Unbounded instantiation must exhaust the depth budget")
    print("  [PASS] Compound types, finite recursion, cache boundary, immutable keys, and C metrics")


def test_run_stats_preserves_execution():
    with tempfile.TemporaryDirectory() as directory:
        source = os.path.join(directory, "stats.wysna")
        with open(source, "w", encoding="utf-8") as stream:
            stream.write('print("executed")\n')
        for flag in ["--stats", "--metrics"]:
            result = subprocess.run([sys.executable, "main.py", "run", source, flag],
                                    capture_output=True, text=True, timeout=20)
            assert result.returncode == 0, result.stderr
            assert result.stdout == "executed\n", result.stdout
            assert "monomorphization" in json.loads(result.stderr)["telemetry"]
        checked = subprocess.run([sys.executable, "main.py", "check", source, "--stats"],
                                 capture_output=True, text=True, timeout=20)
        assert checked.returncode == 0, checked.stderr
        assert json.loads(checked.stdout)["status"] == "ok"
    print("  [PASS] Run stats preserve program stdout; check stats remain JSON")


def run_generic_hardening_tests() -> bool:
    print("==================================================")
    print("Generic Hardening & Code Bloat Metrics Conformance")
    print("==================================================")
    try:
        test_generic_instance_key()
        test_canonical_deduplication_telemetry()
        test_unbounded_instantiation_budget()
        test_direct_struct_recursion_cycle_detection()
        test_cli_stats_output()
        test_hardening_regressions()
        test_run_stats_preserves_execution()
        from tests.unit.test_compiler_boundaries import test_compiler_boundaries
        test_compiler_boundaries()
        from tests.unit.test_dictionary_key_semantics import run_dictionary_key_tests
        run_dictionary_key_tests()
        from tests.unit.test_f03_error_unions import run_error_union_tests
        run_error_union_tests()
        from tests.unit.test_phase_a import run_phase_a_tests
        run_phase_a_tests()
        from tests.unit.test_ownership_control_flow import run_ownership_tests
        run_ownership_tests()
        from tests.unit.test_f03_ownership import run_f03_ownership_tests
        run_f03_ownership_tests()
        from tests.unit.test_weak_references import run_weak_tests
        run_weak_tests()
        return True
    except Exception as e:
        print(f"  [FAIL] Generic Hardening Test Failed: {e}")
        return False


if __name__ == "__main__":
    if not run_generic_hardening_tests():
        sys.exit(1)
    print("\n[OK] Generic Hardening Conformance: 100% PASS.")
    sys.exit(0)
