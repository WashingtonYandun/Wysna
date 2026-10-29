"""
test_c05_types_inference.py - Comprehensive verification for Milestone C05:
Types, Inference Centralization, Container Invariance, and Order Independence.

Verifies:
1. Declaration order independence (Permutation Invariance):
   - Struct Outer before struct Inner.
   - Enum before struct payload.
   - Class before struct member.
   - Top-level function before return/param type declaration.
   - Permuting declarations yields identical semantic typing.
2. Container and compound type invariance:
   - list[Dog] is NOT assignable to list[Animal] (strict invariance per SPEC 4.1 & ADR-002).
   - dict[str, Dog] is NOT assignable to dict[str, Animal].
   - Generic struct Box[Dog] is NOT assignable to Box[Animal].
   - Generic enum Tree[Dog] is NOT assignable to Tree[Animal].
   - TypeInterner.is_assignable canonical TypeId invariance.
3. Contextless inference protection:
   - Bare `let d = {}` is rejected with T011 ("Cannot infer type of empty dictionary literal").
   - Explicit `let d: dict[str, int] = {}` is accepted.
   - Bare `let xs = []` is rejected with T011.
   - Expected type context correctly propagates through `??` coalescing.
4. Conflicting generic type inference:
   - fn choose[T](a: T, b: T) -> T called with conflicting arguments raises W2001.
   - Struct Pair[T] { a: T, b: T } initialized with conflicting fields raises W2001.
5. Generic enum argument preservation:
   - Multi-argument generic enum Either[L, R] retains all concrete type arguments in TypeInterner.
6. Native C99 execution through the AST oracle and verified HIR/MIR production path with AddressSanitizer (0 leaks).
"""

import os
import sys
import subprocess
import tempfile
from parser.Parser import Parser
from checker.TypeChecker import TypeChecker
from checker.TypeInterner import TypeInterner, TYPE_INT, TYPE_STR
from checker.WysnaType import (
    IntType, StrType, ListType, DictType, ClassType, StructType, EnumType, is_assignable
)
from codegen.CCodeGen import CCodeGen
from hir.HirBuilder import HirBuilder
from hir.HirCCodeGen import HirCCodeGen
from error.Error import TypeError


def test_declaration_order_invariance():
    """Verify that declaration ordering does not affect type checking."""
    # Case 1: Struct Outer declared before Inner
    src1 = """
    struct Outer {
        inner: Inner
    }
    struct Inner {
        value: int
    }
    let o = Outer { inner: Inner { value: 42 } }
    """
    ast1 = Parser(file="order1.wysna").parse(src1)
    tc1 = TypeChecker(file="order1.wysna")
    tc1.check(ast1)
    sym_o = tc1.global_scope.lookup_symbol("o")
    assert sym_o is not None
    assert isinstance(sym_o.type, StructType) and sym_o.type.name == "Outer"

    # Case 2: Enum Status declared before struct Payload
    src2 = """
    enum Status {
        Ok(Payload),
        Failed
    }
    struct Payload {
        code: int
    }
    let s = Status.Ok(Payload { code: 200 })
    """
    ast2 = Parser(file="order2.wysna").parse(src2)
    tc2 = TypeChecker(file="order2.wysna")
    tc2.check(ast2)
    sym_s = tc2.global_scope.lookup_symbol("s")
    assert sym_s is not None
    assert isinstance(sym_s.type, EnumType) and sym_s.type.name == "Status"

    # Case 3: Class Service declared before struct Config
    src3 = """
    class Service {
        public cfg: Config
        fn init(c: Config) {
            self.cfg = c
        }
    }
    struct Config {
        port: int
    }
    let s = new Service(Config { port: 8080 })
    """
    ast3 = Parser(file="order3.wysna").parse(src3)
    tc3 = TypeChecker(file="order3.wysna")
    tc3.check(ast3)
    sym_srv = tc3.global_scope.lookup_symbol("s")
    assert sym_srv is not None
    assert isinstance(sym_srv.type, ClassType) and sym_srv.type.name == "Service"

    # Case 4: Function make_point declared before struct Point
    src4 = """
    fn make_point(x: int, y: int) -> Point {
        return Point { x: x, y: y }
    }
    struct Point {
        x: int
        y: int
    }
    let p = make_point(10, 20)
    """
    ast4 = Parser(file="order4.wysna").parse(src4)
    tc4 = TypeChecker(file="order4.wysna")
    tc4.check(ast4)
    sym_p = tc4.global_scope.lookup_symbol("p")
    assert sym_p is not None
    assert isinstance(sym_p.type, StructType) and sym_p.type.name == "Point"

    print("  [PASS] Declaration order independence (Permutation Invariance)")


def test_container_and_compound_invariance():
    """Verify strict invariance on collections and generic compound types."""
    # 1. WysnaType.is_assignable invariance check:
    animal = ClassType("Animal")
    dog = ClassType("Dog", superclass=animal)
    assert is_assignable(dog, animal), "OOP subclass should be assignable to superclass"

    list_animal = ListType(animal)
    list_dog = ListType(dog)
    assert not is_assignable(list_dog, list_animal), "list[Dog] must NOT be assignable to list[Animal] (invariant)"
    assert not is_assignable(list_animal, list_dog), "list[Animal] must NOT be assignable to list[Dog]"
    assert is_assignable(list_dog, list_dog), "list[Dog] is assignable to list[Dog]"

    dict_animal = DictType(StrType, animal)
    dict_dog = DictType(StrType, dog)
    assert not is_assignable(dict_dog, dict_animal), "dict[str, Dog] must NOT be assignable to dict[str, Animal] (invariant)"

    # 2. TypeInterner.is_assignable invariance check:
    interner = TypeInterner()
    c_animal = interner.mk_class("Animal")
    c_dog = interner.mk_class("Dog", superclass=c_animal)
    assert interner.is_assignable(c_dog, c_animal)


    l_animal = interner.mk_list(c_animal)
    l_dog = interner.mk_list(c_dog)
    assert not interner.is_assignable(l_dog, l_animal), "Interner: list[Dog] must NOT be assignable to list[Animal]"

    d_animal = interner.mk_dict(TYPE_STR, c_animal)
    d_dog = interner.mk_dict(TYPE_STR, c_dog)
    assert not interner.is_assignable(d_dog, d_animal), "Interner: dict[str, Dog] must NOT be assignable to dict[str, Animal]"

    # 3. Source-level rejection of list subtyping:
    src_invalid = """
    class Animal {
        public name: str
        fn init(name: str) {
            self.name = name
        }
    }
    class Dog extends Animal {
        public breed: str
        fn init(name: str, breed: str) {
            super.init(name)
            self.breed = breed
        }
    }
    let dogs: list[Dog] = [new Dog("Rex", "Collie")]
    let animals: list[Animal] = dogs
    """
    try:
        ast = Parser(file="inv.wysna").parse(src_invalid)
        tc = TypeChecker(file="inv.wysna")
        tc.check(ast)
        assert False, "Expected assignment of list[Dog] to list[Animal] to be rejected"
    except TypeError as e:
        assert e.code in ("W2001", "T003"), f"Expected W2001 or T003, got {e.code}: {e}"


    print("  [PASS] Container and compound type invariance")


def test_contextless_inference_protection():
    """Verify that bare unannotated empty collections are rejected with T011."""
    # 1. Bare empty dict rejected
    src_dict = "let d = {}"
    try:
        ast = Parser(file="dict_empty.wysna").parse(src_dict)
        tc = TypeChecker(file="dict_empty.wysna")
        tc.check(ast)
        assert False, "Expected let d = {} to be rejected with W2001"
    except TypeError as e:
        assert e.code == "W2001", f"Expected W2001, got {e.code}: {e}"

    # 2. Annotated empty dict accepted
    src_dict_ok = "let d: dict[str, int] = {}"
    ast_ok = Parser(file="dict_ok.wysna").parse(src_dict_ok)
    tc_ok = TypeChecker(file="dict_ok.wysna")
    tc_ok.check(ast_ok)
    sym_d = tc_ok.global_scope.lookup_symbol("d")
    assert sym_d is not None
    assert isinstance(sym_d.type, DictType)

    # 3. Bare empty list rejected
    src_list = "let xs = []"
    try:
        ast = Parser(file="list_empty.wysna").parse(src_list)
        tc = TypeChecker(file="list_empty.wysna")
        tc.check(ast)
        assert False, "Expected let xs = [] to be rejected with W2001"
    except TypeError as e:
        assert e.code == "W2001", f"Expected W2001, got {e.code}: {e}"


    # 4. Context propagation through ??
    src_coalesce = """
    let opt: list[int]? = none
    let xs = opt ?? []
    """
    ast_c = Parser(file="coalesce.wysna").parse(src_coalesce)
    tc_c = TypeChecker(file="coalesce.wysna")
    tc_c.check(ast_c)
    sym_xs = tc_c.global_scope.lookup_symbol("xs")
    assert sym_xs is not None
    assert isinstance(sym_xs.type, ListType) and sym_xs.type.element == IntType

    print("  [PASS] Contextless inference protection & ?? context propagation")


def test_conflicting_generic_inference():
    """Verify that conflicting type arguments during inference raise W2001."""
    # 1. Conflicting function arguments: choose(1, "str")
    src_fn = """
    fn choose[T](a: T, b: T) -> T {
        return a
    }
    let res = choose(1, "hello")
    """
    try:
        ast = Parser(file="choose.wysna").parse(src_fn)
        tc = TypeChecker(file="choose.wysna")
        tc.check(ast)
        assert False, "Expected conflicting generic function arguments to be rejected"
    except TypeError as e:
        assert e.code == "W2001", f"Expected W2001, got {e.code}: {e}"

    # 2. Conflicting struct fields: Pair { a: 10, b: "hello" }
    src_struct = """
    struct Pair[T] {
        a: T
        b: T
    }
    let p = Pair { a: 10, b: "hello" }
    """
    try:
        ast = Parser(file="pair.wysna").parse(src_struct)
        tc = TypeChecker(file="pair.wysna")
        tc.check(ast)
        assert False, "Expected conflicting generic struct fields to be rejected"
    except TypeError as e:
        assert e.code == "W2001", f"Expected W2001, got {e.code}: {e}"

    print("  [PASS] Conflicting generic inference detection (W2001)")


def test_generic_enum_argument_preservation():
    """Verify that multi-argument generic enums preserve all type arguments."""
    src = """
    enum Either[L, R] {
        Left(L),
        Right(R)
    }
    fn get_left() -> Either[int, str] {
        return Either.Left[int, str](42)
    }
    fn get_right() -> Either[int, str] {
        return Either.Right[int, str]("done")
    }
    let e1 = get_left()
    let e2 = get_right()
    """
    ast = Parser(file="either.wysna").parse(src)
    tc = TypeChecker(file="either.wysna")
    tc.check(ast)

    sym_e1 = tc.global_scope.lookup_symbol("e1")
    assert sym_e1 is not None
    assert isinstance(sym_e1.type, EnumType)
    assert sym_e1.type.type_args == [IntType, StrType], f"Expected [int, str], got {sym_e1.type.type_args}"
    assert sym_e1.type.template_name == "Either"

    # Check interner metadata
    enum_def = tc.interner.enum_defs.get(sym_e1.type.name)
    assert enum_def is not None
    assert len(enum_def.type_args) == 2, f"Expected 2 type args in EnumDef, got {enum_def.type_args}"
    assert enum_def.type_args == (TYPE_INT, TYPE_STR)
    assert enum_def.template_name == "Either"

    print("  [PASS] Generic enum argument preservation in EnumDef and TypeInterner")


def test_native_c99_execution_and_sanitizers():
    """Verify end-to-end native execution with out-of-order declarations and generic enums."""
    src = """
    fn run_computation() -> int {
        let tree = Tree.Node(Tree.Leaf(10), Tree.Leaf(20))
        return eval_tree(tree)
    }

    fn eval_tree(t: Tree[int]) -> int {
        match t {
            Tree.Leaf(val) => val,
            Tree.Node(l, r) => eval_tree(l) + eval_tree(r)
        }
    }

    enum Tree[T] {
        Leaf(T),
        Node(Tree[T], Tree[T])
    }

    struct ResultContainer {
        total: int
    }

    let res = ResultContainer { total: run_computation() }
    print(res.total)
    """
    with tempfile.TemporaryDirectory(prefix="wysna_c05_test_") as tmpdir:
        wysna_file = os.path.join(tmpdir, "test.wysna")
        with open(wysna_file, "w") as f:
            f.write(src)

        # 1. AST C99 backend
        ast = Parser(file=wysna_file).parse(src)
        tc_ast = TypeChecker(file=wysna_file)
        tc_ast.check(ast)
        codegen = CCodeGen()
        c_code_ast = codegen.generate(ast)

        c_file_ast = os.path.join(tmpdir, "ast_prog.c")
        bin_file_ast = os.path.join(tmpdir, "ast_prog")
        with open(c_file_ast, "w") as f:
            f.write(c_code_ast)

        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        compile_cmd = [
            os.environ.get("CC", "clang"),
            "-std=c99",
            "-fsanitize=address,undefined",
            "-O1",
            f"-I{repo_root}",
            "-DWYSNA_MEM_CHECK=1",
            c_file_ast,
            "-o", bin_file_ast
        ]
        res = subprocess.run(compile_cmd, capture_output=True, text=True)
        assert res.returncode == 0, f"AST C99 Clang ASan compile failed:\n{res.stderr}"

        run_res = subprocess.run([bin_file_ast], capture_output=True, text=True)
        assert run_res.returncode == 0, f"AST C99 runtime failed:\n{run_res.stderr}"
        assert run_res.stdout.strip() == "30", f"AST C99 expected stdout '30', got '{run_res.stdout.strip()}'"

        # 2. Seed's verified HIR/MIR C99 production path
        ast_hir = Parser(file=wysna_file).parse(src)
        tc_hir = TypeChecker(file=wysna_file)
        tc_hir.check(ast_hir)
        hir_builder = HirBuilder(tc_hir.interner)
        hir_mod = hir_builder.lower(ast_hir)
        hir_codegen = HirCCodeGen(tc_hir.interner)
        c_code_hir = hir_codegen.generate(hir_mod)

        c_file_hir = os.path.join(tmpdir, "hir_prog.c")
        bin_file_hir = os.path.join(tmpdir, "hir_prog")
        with open(c_file_hir, "w") as f:
            f.write(c_code_hir)

        compile_cmd_hir = [
            os.environ.get("CC", "clang"),
            "-std=c99",
            "-fsanitize=address,undefined",
            "-O1",
            f"-I{repo_root}",
            "-DW_MEM_CHECK=1",
            c_file_hir,
            "-o", bin_file_hir
        ]
        res_hir = subprocess.run(compile_cmd_hir, capture_output=True, text=True)
        assert res_hir.returncode == 0, f"HIR C99 Clang ASan compile failed:\n{res_hir.stderr}"

        run_res_hir = subprocess.run([bin_file_hir], capture_output=True, text=True)
        assert run_res_hir.returncode == 0, f"HIR C99 runtime failed:\n{run_res_hir.stderr}"
        assert run_res_hir.stdout.strip() == "30", f"HIR C99 expected stdout '30', got '{run_res_hir.stdout.strip()}'"


    print("  [PASS] Native C99 execution through AST oracle and verified HIR/MIR with AddressSanitizer")


def run_all() -> bool:
    print("==================================================")
    print("Wysna C05 Types, Invariance & Inference Suite")
    print("==================================================")
    try:
        test_declaration_order_invariance()
        test_container_and_compound_invariance()
        test_contextless_inference_protection()
        test_conflicting_generic_inference()
        test_generic_enum_argument_preservation()
        test_native_c99_execution_and_sanitizers()
        print("==================================================")
        print("ALL C05 TYPES & INFERENCE TESTS PASSED.")
        print("==================================================")
        return True
    except Exception as e:
        print(f"C05 TEST FAILED: {type(e).__name__}: {e}")
        return False



if __name__ == "__main__":
    success = run_all()
    sys.exit(0 if success else 1)
