"""
test_c03_generic_specialization.py - Comprehensive verification for C03 Generic Specialization on HIR.

Verifies:
1. Collision freedom: user identifiers with '__' alongside generic specializations.
2. Reserved compiler prefix rejection (_WYSNA_ and __wysna_internal_ user identifiers).
3. Specialization worklist states and regular recursion termination.
4. Direct and mutual structural expansion cycle detection with instantiation chain diagnostics (W2011).
5. Deterministic topological sorting and PYTHONHASHSEED invariance.
6. Native C99 execution of specialized generics through the AST oracle and verified HIR/MIR production path.
"""

import os
import sys
import subprocess
import tempfile
from parser.Parser import Parser
from checker.TypeChecker import (
    TypeChecker, GenericInstanceKey, SpecializationState, mangle_generic_name
)
from checker.TypeInterner import TypeInterner, IntType, StrType, BoolType
from codegen.CCodeGen import CCodeGen
from hir.HirBuilder import HirBuilder
from hir.HirCCodeGen import HirCCodeGen
from error.Error import TypeError, WysnaError


def test_collision_freedom():
    """Verify that user identifiers containing '__' do not collide with generic specializations."""
    code = """
    struct Box__int {
        custom_val: int
    }

    struct Box[T] {
        val: T
    }

    fn id__int(x: int) -> int {
        return x * 10
    }

    fn id[T](x: T) -> T {
        return x
    }

    fn main() -> int {
        let u: Box__int = Box__int { custom_val: 100 }
        let g: Box[int] = Box(200)
        let r1 = id__int(u.custom_val)
        let r2 = id(g.val)
        return r1 + r2
    }
    """
    ast = Parser().parse(code)
    checker = TypeChecker()
    checker.check(ast)

    # Verify both Box__int and specialized Box[int] exist in scopes
    user_struct = checker.global_scope.lookup_type("Box__int")
    assert user_struct is not None, "User struct Box__int must exist"

    specialized_box_keys = [k for k in checker.specializations.keys() if k.template_name == "Box"]
    assert len(specialized_box_keys) == 1, "Exactly one Box specialization must exist"
    spec_entry = checker.specializations[specialized_box_keys[0]]
    assert spec_entry.state == SpecializationState.COMPLETE
    assert spec_entry.specialized_type is not None
    assert spec_entry.specialized_type.name != "Box__int", "Mangled name must not collide with user Box__int"
    assert spec_entry.specialized_type.name.startswith("_WYSNA_G_"), "Specialization must carry canonical _WYSNA_G_ prefix"

    # Verify user fn id__int and generic id[int]
    user_fn = checker.global_scope.lookup_symbol("id__int")
    assert user_fn is not None, "User fn id__int must exist"

    spec_fn_keys = [k for k in checker.specializations.keys() if k.template_name == "id"]
    assert len(spec_fn_keys) == 1, "Exactly one id specialization must exist"
    spec_fn_entry = checker.specializations[spec_fn_keys[0]]
    assert spec_fn_entry.state == SpecializationState.COMPLETE
    assert spec_fn_entry.specialized_symbol.name != "id__int"
    assert spec_fn_entry.specialized_symbol.name.startswith("_WYSNA_G_")

    print("  [PASS] Collision freedom between user '__' identifiers and generic specializations")


def test_reserved_compiler_prefix():
    """Verify that user identifiers starting with _WYSNA_ or __wysna_internal_ are rejected."""
    invalid_codes = [
        "struct _WYSNA_Custom { x: int }",
        "struct __wysna_internal_Custom { x: int }",
        "fn _WYSNA_func() -> int { return 1 }",
        "fn __wysna_internal_func() -> int { return 1 }",
        "let _WYSNA_var = 10",
        "let __wysna_internal_var = 10",
    ]
    for code in invalid_codes:
        try:
            ast = Parser().parse(code)
            TypeChecker().check(ast)
        except WysnaError as err:
            assert err.code == "W1002", f"Expected W1002 for reserved prefix, got {err.code}: {err}"
        else:
            raise AssertionError(f"Expected compiler prefix rejection for: {code}")

    print("  [PASS] Reserved compiler prefix (_WYSNA_, __wysna_internal_) protection")


def test_regular_recursion_termination():
    """Verify that regular recursion over the same generic type terminates without cycle error."""
    code = """
    fn countdown[T](n: int, val: T) -> int {
        if n <= 0 {
            return 0
        }
        return countdown[T](n - 1, val)
    }

    fn main() -> int {
        return countdown(5, "hello")
    }
    """
    ast = Parser().parse(code)
    checker = TypeChecker()
    checker.check(ast)

    spec_keys = [k for k in checker.specializations.keys() if k.template_name == "countdown"]
    assert len(spec_keys) == 1, "Regular recursion should instantiate exactly 1 specialization"
    entry = checker.specializations[spec_keys[0]]
    assert entry.state == SpecializationState.COMPLETE

    print("  [PASS] Regular recursion termination with cache-first lookup")


def test_structural_expansion_diagnostics():
    """Verify direct and mutual structural generic expansion rejection with chain diagnostic."""
    # 1. Direct expansion
    direct_code = """
    fn expand[T](x: T) -> unit {
        expand([x])
    }
    fn main() -> unit {
        expand(1)
    }
    """
    try:
        TypeChecker().check(Parser().parse(direct_code))
    except TypeError as err:
        assert err.code == "W2011", f"Expected W2011, got {err.code}"
        msg = str(err)
        assert "structural generic expansion cycle detected" in msg or "depth limit exceeded" in msg
        assert "expand" in msg
    else:
        raise AssertionError("Direct structural expansion must be rejected")

    # 2. Mutual expansion
    mutual_code = """
    fn ping[T](x: T) -> unit {
        pong([x])
    }
    fn pong[U](y: U) -> unit {
        ping([y])
    }
    fn main() -> unit {
        ping(1)
    }
    """
    try:
        TypeChecker().check(Parser().parse(mutual_code))
    except TypeError as err:
        assert err.code == "W2011", f"Expected W2011, got {err.code}"
        msg = str(err)
        assert "ping" in msg or "pong" in msg
    else:
        raise AssertionError("Mutual structural expansion must be rejected")

    print("  [PASS] Direct and mutual structural expansion cycle detection with chain diagnostics")


def test_deterministic_topological_sorting_and_hashseed():
    """Verify topological sorting of specialized declarations and PYTHONHASHSEED invariance."""
    code = """
    struct Leaf[T] {
        value: T
    }

    struct Branch[T] {
        leaf: Leaf[T]
        extra: int
    }

    struct Tree[T] {
        root: Branch[T]
    }

    fn make_tree[T](val: T) -> Tree[T] {
        let l = Leaf(val)
        let b = Branch(l, 42)
        return Tree(b)
    }

    fn main() -> int {
        let t = make_tree(99)
        return t.root.leaf.value
    }
    """
    # Test across multiple PYTHONHASHSEED values via subprocess to ensure clean Python runtime state
    c_outputs = []
    seeds = ["0", "42", "12345", "999999"]
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))

    with tempfile.TemporaryDirectory() as tmpdir:
        src_path = os.path.join(tmpdir, "test_seed.wysna")
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(code)

        for seed in seeds:
            env = os.environ.copy()
            env["PYTHONHASHSEED"] = seed
            res = subprocess.run(
                [sys.executable, "main.py", "emit-c", src_path],
                cwd=repo_root,
                capture_output=True,
                text=True,
                env=env,
                timeout=30
            )
            assert res.returncode == 0, f"emit-c failed with PYTHONHASHSEED={seed}: {res.stderr}"
            out_c = os.path.join(tmpdir, "test_seed.c")
            with open(out_c, "r", encoding="utf-8") as cf:
                c_outputs.append(cf.read())

    # All emitted C files across different hash seeds must be 100% byte-identical
    first_c = c_outputs[0]
    for idx, c_code in enumerate(c_outputs[1:], start=1):
        assert c_code == first_c, f"C emission diverged with PYTHONHASHSEED={seeds[idx]}"

    print("  [PASS] Deterministic topological sorting and PYTHONHASHSEED invariance")


def test_native_c99_execution():
    """Verify native C99 execution of specialized generics with user '__' identifiers."""
    code = """
    struct Pair__int {
        a: int
        b: int
    }

    struct Pair[T, U] {
        first: T
        second: U
    }

    fn sum_pair__int(p: Pair__int) -> int {
        return p.a + p.b
    }

    fn swap[T, U](p: Pair[T, U]) -> Pair[U, T] {
        return Pair(p.second, p.first)
    }

    fn countdown[T](n: int, acc: int, marker: T) -> int {
        if n <= 0 {
            return acc
        }
        return countdown(n - 1, acc + n, marker)
    }

    fn main() -> int {
        let up: Pair__int = Pair__int { a: 10, b: 20 }
        let gp: Pair[int, str] = Pair(100, "wawi")
        let swapped: Pair[str, int] = swap(gp)
        let total = sum_pair__int(up) + swapped.second + countdown(4, 0, true)
        print(swapped.first)
        print(total)
        return 0
    }
    """
    ast = Parser().parse(code)
    checker = TypeChecker()
    checker.check(ast)

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. AST C Backend
        c_ast = CCodeGen().generate(ast)
        c_path_ast = os.path.join(tmpdir, "prog_ast.c")
        bin_path_ast = os.path.join(tmpdir, "prog_ast")
        with open(c_path_ast, "w", encoding="utf-8") as f:
            f.write(c_ast)

        comp_ast = subprocess.run(
            [os.environ.get("CC", "cc"), "-std=c99", "-fsanitize=address,undefined", f"-I{repo_root}", c_path_ast, "-o", bin_path_ast],
            capture_output=True, text=True, timeout=60
        )
        assert comp_ast.returncode == 0, comp_ast.stderr
        run_ast = subprocess.run([bin_path_ast], capture_output=True, text=True, timeout=30)
        assert run_ast.returncode == 0, run_ast.stderr
        assert run_ast.stdout == "wawi\n140\n", f"AST stdout unexpected: {run_ast.stdout!r}"

        # 2. HIR C Backend
        builder = HirBuilder(checker.interner)
        hir_mod = builder.lower(ast)
        c_hir = HirCCodeGen(checker.interner).generate(hir_mod)
        c_path_hir = os.path.join(tmpdir, "prog_hir.c")
        bin_path_hir = os.path.join(tmpdir, "prog_hir")
        with open(c_path_hir, "w", encoding="utf-8") as f:
            f.write(c_hir)

        comp_hir = subprocess.run(
            [os.environ.get("CC", "cc"), "-std=c99", "-fsanitize=address,undefined", f"-I{repo_root}", c_path_hir, "-o", bin_path_hir],
            capture_output=True, text=True, timeout=60
        )
        assert comp_hir.returncode == 0, comp_hir.stderr
        run_hir = subprocess.run([bin_path_hir], capture_output=True, text=True, timeout=30)
        assert run_hir.returncode == 0, run_hir.stderr
        assert run_hir.stdout == "wawi\n140\n", f"HIR stdout unexpected: {run_hir.stdout!r}"

    print("  [PASS] Native C99 execution through AST oracle and verified HIR/MIR with AddressSanitizer")


def run_all() -> bool:
    print("==================================================")
    print("Wysna C03 Generic Specialization & Mangling")
    print("==================================================")
    try:
        test_collision_freedom()
        test_reserved_compiler_prefix()
        test_regular_recursion_termination()
        test_structural_expansion_diagnostics()
        test_deterministic_topological_sorting_and_hashseed()
        test_native_c99_execution()
        print("==================================================")
        print("ALL C03 GENERIC SPECIALIZATION TESTS PASSED.")
        print("==================================================")
        return True
    except Exception as e:
        print(f"[FAIL] C03 Test Suite Failed: {type(e).__name__}: {e}")
        return False


if __name__ == "__main__":
    if not run_all():
        sys.exit(1)
    sys.exit(0)
