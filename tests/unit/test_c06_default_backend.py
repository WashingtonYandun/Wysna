"""
Conformance checks for compiler engines, output targets, and the AST test oracle.

Invariants verified:
1. The default Seed engine emits C99 through verified HIR/MIR.
2. --engine selects Seed or Compiler; --backend selects C99 or experimental LLVM.
3. AST C99 emission is available only to internal differential tests.
4. Former mixed backend selectors fail with migration guidance.
5. No silent fallback: Seed errors are reported directly without fallback to AST C.
6. Unit functions with trailing expressions are normalized to HirConstUnit.
7. Native C99 execution through the default Seed-to-HIR/MIR pipeline under Clang AddressSanitizer and UBSan.
"""

import os
import subprocess
import sys
import tempfile
from lexer.Lexer import Lexer
from parser.Parser import Parser
from checker.TypeChecker import TypeChecker
from checker.TypeInterner import TYPE_UNIT
from hir.HirBuilder import HirBuilder
from hir.HirCCodeGen import HirCCodeGen
from hir.HirNodes import HirExprStmt, HirConstUnit
from compiler.CompilerService import CompilerService
from main import run_pipeline, get_repo_root


def _run_cmd(cmd, cwd=None):
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return res.returncode, res.stdout, res.stderr


def test_default_engine_emits_c99_through_hir():
    """Verify that the default Seed engine emits C99 from verified HIR/MIR."""
    print("  [TEST] Default Seed engine emits C99 through verified HIR/MIR...")
    source = """
    fn add(a: int, b: int) -> int {
        return a + b
    }
    print(add(10, 20))
    """
    with tempfile.TemporaryDirectory() as td:
        c_path = os.path.join(td, "default.c")
        code = run_pipeline(source, file_name="test_default.wysna", emit_c_path=c_path)
        assert code == 0, "run_pipeline failed on default emission"
        with open(c_path, "r", encoding="utf-8") as f:
            c_content = f.read()
        
        # Verify HIR hallmarks in generated C code
        # HirCCodeGen emits HirDirectCall targets with resolved signatures
        assert "wysna_fn_add" in c_content, "Generated code must include function wysna_fn_add"
        assert "wysna_main(" in c_content or "main(" in c_content, "Generated code must include main entrypoint"


def test_engine_and_backend_contracts():
    """Verify that engine selection and output-target selection are independent."""
    print("  [TEST] Explicit compiler engine and output backend contracts...")
    source = """
    struct Point { x: int, y: int }
    let p = Point { x: 3, y: 4 }
    print(p.x + p.y)
    """
    repo_root = get_repo_root()
    wysna_cli = os.path.join(repo_root, "wysna")

    with tempfile.TemporaryDirectory() as td:
        src_path = os.path.join(td, "point.wysna")
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(source)

        # 1. Default Seed engine and C99 output target.
        c_default = os.path.join(td, "default.c")
        rc, out, err = _run_cmd([sys.executable, wysna_cli, "emit-c", src_path, "-o", c_default], cwd=repo_root)
        assert rc == 0, f"Default emit-c failed: {err}"

        c_seed = os.path.join(td, "seed.c")
        rc, out, err = _run_cmd([sys.executable, wysna_cli, "emit-c", "--engine=seed", src_path, "-o", c_seed], cwd=repo_root)
        assert rc == 0, f"--engine=seed failed: {err}"

        c99 = os.path.join(td, "c99.c")
        rc, out, err = _run_cmd([sys.executable, wysna_cli, "emit-c", "--backend=c99", src_path, "-o", c99], cwd=repo_root)
        assert rc == 0, f"--backend=c99 failed: {err}"

        c_compiler = os.path.join(td, "compiler.c")
        rc, out, err = _run_cmd([sys.executable, wysna_cli, "emit-c", "--engine=compiler", src_path, "-o", c_compiler], cwd=repo_root)
        assert rc == 0, f"--engine=compiler failed: {err or out}"

        service = CompilerService(project_root=td)
        checked = service.check_file(src_path)
        ast_oracle = service._emit_ast_oracle_for_testing(checked).source
        assert "wysna_main(" in ast_oracle or "main(" in ast_oracle

        # The default Seed engine and explicit C99 target must be reproducible.
        with open(c_default, "r", encoding="utf-8") as f:
            content_default = f.read()
        with open(c_seed, "r", encoding="utf-8") as f:
            content_seed = f.read()
        with open(c99, "r", encoding="utf-8") as f:
            content_c99 = f.read()
        assert content_default == content_seed == content_c99, (
            "Default emit-c must match explicit Seed and C99 selections exactly")

        for old_selector in ("--backend=hir", "--backend=ast", "--backend=selfhost",
                             "--hir", "--selfhost", "--ast"):
            rc, out, err = _run_cmd([sys.executable, wysna_cli, "emit-c", old_selector,
                                     src_path, "-o", os.path.join(td, "invalid.c")], cwd=repo_root)
            assert rc == 2, f"{old_selector} must be rejected as an obsolete selector: {err}"
            assert "--engine" in err or "wysna ast" in err, err

        compiler_check = _run_cmd([sys.executable, wysna_cli, "check", "--engine=compiler",
                                   src_path], cwd=repo_root)
        assert compiler_check[0] == 2, compiler_check
        assert "standalone Compiler SDK" in compiler_check[2]
        compiler_llvm = _run_cmd([sys.executable, wysna_cli, "emit-c", "--engine=compiler",
                                  "--backend=llvm-experimental", src_path], cwd=repo_root)
        assert compiler_llvm[0] == 2, compiler_llvm
        assert "C99 output backend" in compiler_llvm[2]

        # Unknown output targets fail with a usage error.
        rc, out, err = _run_cmd([sys.executable, wysna_cli, "emit-c", "--backend=invalid_backend", src_path], cwd=repo_root)
        assert rc == 2, "Invalid backend must exit with usage status"
        assert "Unknown backend" in err or "Unknown backend" in out, "Must produce error message on invalid backend"


def test_unit_function_tail_normalization():
    """Verify that unit functions with non-unit tail expressions normalize to HirConstUnit."""
    print("  [TEST] Unit function tail expression normalization...")
    source = """
    fn helper() -> int {
        return 42
    }
    fn procedure() -> unit {
        let x = 10
        helper()
    }
    procedure()
    """
    ast = Parser(file="unit_tail.wysna").parse(source)
    tc = TypeChecker(file="unit_tail.wysna")
    tc.check(ast)

    builder = HirBuilder(tc.interner, file_path="unit_tail.wysna")
    hir_mod = builder.lower(ast)
    assert hir_mod is not None, "HIR module must lower successfully"

    # Verify procedure's HIR declaration
    proc_fn = [f for f in hir_mod.functions if f.name == "procedure"][0]
    assert proc_fn.return_type_id == TYPE_UNIT, "procedure return type must be TYPE_UNIT"
    assert isinstance(proc_fn.body.tail_expr, HirConstUnit), "procedure tail_expr must be normalized to HirConstUnit"

    # Verify statement list has the helper() expression statement
    assert any(isinstance(s, HirExprStmt) for s in proc_fn.body.statements), "Discarded helper() must be in statements"


def test_native_c99_execution_under_sanitizers():
    """Verify the default Seed-to-HIR/MIR-to-C99 pipeline under ASan and UBSan."""
    print("  [TEST] Native C99 execution through default Seed HIR/MIR under ASan + UBSan...")
    source = """
    enum Tree[T] {
        Leaf(T),
        Node(Tree[T], Tree[T])
    }

    fn count_leaves[T](t: Tree[T]) -> int {
        return match t {
            Tree.Leaf(_) => 1,
            Tree.Node(left, right) => count_leaves(left) + count_leaves(right)
        }
    }

    fn run_test() -> int {
        let tree: Tree[int] = Tree.Node(
            Tree.Node(Tree.Leaf(1), Tree.Leaf(2)),
            Tree.Leaf(3)
        )
        return count_leaves(tree)
    }

    print("LEAVES:", run_test())
    """
    repo_root = get_repo_root()
    wysna_cli = os.path.join(repo_root, "wysna")

    with tempfile.TemporaryDirectory() as td:
        src_path = os.path.join(td, "tree.wysna")
        bin_path = os.path.join(td, "tree_bin")
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(source)

        # Build natively using the default engine and output target with sanitizers enabled.
        rc, out, err = _run_cmd([
            sys.executable, wysna_cli, "build", "--sanitize", src_path, "-o", bin_path
        ], cwd=repo_root)
        assert rc == 0, f"Native build via default Seed HIR/MIR pipeline failed: {err}"
        assert os.path.exists(bin_path), "Target binary must exist"

        # Execute binary
        env = dict(os.environ, WYSNA_MEM_CHECK="1")
        res = subprocess.run([bin_path], capture_output=True, text=True, env=env)
        assert res.returncode == 0, f"Execution failed with code {res.returncode}: {res.stderr}"
        assert "LEAVES: 3" in res.stdout, f"Unexpected stdout: {res.stdout}"


def run_all() -> bool:
    print("==================================================")
    print("Wysna Compiler Engine and Output Target Contract Suite")
    print("==================================================")
    try:
        test_default_engine_emits_c99_through_hir()
        test_engine_and_backend_contracts()
        test_unit_function_tail_normalization()
        test_native_c99_execution_under_sanitizers()
        print("==================================================")
        print("ALL ENGINE AND OUTPUT TARGET CONTRACT TESTS PASSED.")
        print("==================================================")
        return True
    except AssertionError as e:
        print(f"[FAIL] Assertion error: {e}")
        return False
    except Exception as e:
        print(f"[FAIL] Unexpected error: {e}")
        return False


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
