"""
test_c04_adt_layout.py - Comprehensive verification for Milestone C04:
Layout Graph, Finite Size Verification, and Recursive ADTs.

Verifies:
1. LayoutGraph construction and Tarjan's SCC on INLINE subgraph:
   - Self-referencing struct cycle detection (Node -> Node) with [W2011].
   - Mutual struct cycle detection (CycleA -> CycleB -> CycleA) with [W2011].
   - Multi-node cycle detection (A -> B -> C -> A) with [W2011].
   - Inline tuple struct cycle detection (struct A { t: tuple[int, A] }) with [W2011].
   - Indirection edges (enums, classes, Option, Result) do NOT trigger inline layout cycles.
2. Recursive ADTs (enums with boxed variant payloads):
   - Generic binary tree ADT (enum Tree[T] { Leaf(T), Node(Tree[T], Tree[T]) }) without Box[T].
   - Generic linked list ADT (enum List[T] { Nil, Cons(T, List[T]) }).
   - Non-generic recursive enum (enum IntList { Empty, Cell(int, IntList) }).
3. TypeInterner variant payload metadata:
   - Preserves variant payload TypeIds in EnumDef.variant_payloads.
4. Pattern matching with guards:
   - Guards do NOT contribute unconditional exhaustiveness (raises W2003 if unconditional arm is missing).
   - Guards evaluate and fall through cleanly at runtime.
5. Native C99 execution through the AST oracle and verified HIR/MIR production path with AddressSanitizer (0 leaks).
"""

import os
import sys
import subprocess
import tempfile
from parser.Parser import Parser
from checker.TypeChecker import TypeChecker
from checker.TypeInterner import TypeInterner, TYPE_INT, TYPE_STR
from checker.LayoutGraph import LayoutGraph, EdgeKind, build_layout_graph_from_declarations
from codegen.CCodeGen import CCodeGen
from hir.HirBuilder import HirBuilder
from hir.HirCCodeGen import HirCCodeGen
from error.Error import TypeError


def test_layout_graph_scc_inline_vs_indirect():
    """Verify Tarjan's SCC isolates inline edges from indirect edges."""
    # 1. Graph with only indirect edges (e.g. enums, pointers)
    g1 = LayoutGraph()
    g1.add_edge("Tree", "Tree", EdgeKind.INDIRECT, "left")
    g1.add_edge("Tree", "Tree", EdgeKind.INDIRECT, "right")
    g1.add_edge("Node", "Option", EdgeKind.INDIRECT, "next")
    sccs1 = g1.find_inline_sccs()
    assert len(sccs1) == 0, f"Expected 0 inline SCCs, got {sccs1}"

    # 2. Self-loop on inline edge
    g2 = LayoutGraph()
    g2.add_edge("BadStruct", "BadStruct", EdgeKind.INLINE, "self")
    sccs2 = g2.find_inline_sccs()
    assert len(sccs2) == 1 and sccs2[0] == ["BadStruct"], f"Expected [['BadStruct']], got {sccs2}"
    path, edge = g2.find_inline_cycle_path(sccs2[0])
    assert path == ["BadStruct", "BadStruct"], f"Unexpected cycle path: {path}"

    # 3. Mutual inline cycle
    g3 = LayoutGraph()
    g3.add_edge("A", "B", EdgeKind.INLINE, "b")
    g3.add_edge("B", "A", EdgeKind.INLINE, "a")
    sccs3 = g3.find_inline_sccs()
    assert len(sccs3) == 1 and set(sccs3[0]) == {"A", "B"}, f"Expected SCC with A and B, got {sccs3}"
    path3, edge3 = g3.find_inline_cycle_path(sccs3[0])
    assert path3[0] == path3[-1] and len(path3) == 3, f"Unexpected cycle path: {path3}"

    # 4. Multi-node inline cycle A -> B -> C -> A
    g4 = LayoutGraph()
    g4.add_edge("A", "B", EdgeKind.INLINE, "b")
    g4.add_edge("B", "C", EdgeKind.INLINE, "c")
    g4.add_edge("C", "A", EdgeKind.INLINE, "a")
    sccs4 = g4.find_inline_sccs()
    assert len(sccs4) == 1 and set(sccs4[0]) == {"A", "B", "C"}
    path4, edge4 = g4.find_inline_cycle_path(sccs4[0])
    assert path4[0] == path4[-1] and len(path4) == 4

    print("  [PASS] LayoutGraph SCC correctly separates inline and indirect edges")


def test_reject_infinite_layout_cycles():
    """Verify that typechecker rejects infinite struct layout cycles with W2011."""
    # Direct self cycle
    code_self = """
    struct BadSelf {
        val: int,
        next: BadSelf
    }
    """
    try:
        ast = Parser(file="test_self.wysna").parse(code_self)
        TypeChecker(file="test_self.wysna").check(ast)
        assert False, "Expected TypeError for direct self-cycle"
    except TypeError as e:
        assert e.code == "W2011", f"Expected W2011, got {e.code}"
        assert "BadSelf" in e.message

    # Mutual cycle
    code_mutual = """
    struct A {
        val: int,
        b: B
    }
    struct B {
        val: int,
        a: A
    }
    """
    try:
        ast = Parser(file="test_mutual.wysna").parse(code_mutual)
        TypeChecker(file="test_mutual.wysna").check(ast)
        assert False, "Expected TypeError for mutual struct cycle"
    except TypeError as e:
        assert e.code == "W2011", f"Expected W2011, got {e.code}"
        assert "cycle" in e.message

    # Inline tuple struct cycle
    code_tuple = """
    struct RecTuple {
        val: int,
        tup: tuple[int, RecTuple]
    }
    """
    try:
        ast = Parser(file="test_tuple.wysna").parse(code_tuple)
        TypeChecker(file="test_tuple.wysna").check(ast)
        assert False, "Expected TypeError for tuple struct cycle"
    except TypeError as e:
        assert e.code == "W2011", f"Expected W2011, got {e.code}"
        assert "RecTuple" in e.message

    print("  [PASS] Rejection of direct, mutual, and tuple struct layout cycles with [W2011]")


def test_type_interner_enum_payloads():
    """Verify TypeInterner preserves variant payload metadata in EnumDef."""
    interner = TypeInterner()
    int_tid = TYPE_INT
    str_tid = TYPE_STR

    enum_tid = interner.mk_enum(
        "Message",
        variants=["Quit", "Write", "Move"],
        variant_payloads={
            "Quit": [],
            "Write": [str_tid],
            "Move": [int_tid, int_tid]
        }
    )
    assert enum_tid > 0
    assert "Message" in interner.enum_defs
    edef = interner.enum_defs["Message"]
    assert edef.variants == ["Quit", "Write", "Move"]
    assert edef.variant_payloads["Quit"] == []
    assert edef.variant_payloads["Write"] == [str_tid]
    assert edef.variant_payloads["Move"] == [int_tid, int_tid]

    # Verify lookup via key returns the existing definition
    enum_tid2 = interner.mk_enum("Message")
    assert enum_tid2 == enum_tid
    assert interner.enum_defs["Message"].variant_payloads["Write"] == [str_tid]

    print("  [PASS] TypeInterner preserves enum variant payloads in EnumDef")


def test_match_guards_and_exhaustiveness():
    """Verify that match guards do not contribute unconditional coverage."""
    # Missing unconditional fallback -> must raise W2003
    code_missing_unconditional = """
    enum Status {
        Active(int),
        Inactive
    }

    fn check_status(s: Status) -> int {
        match s {
            Status.Active(val) if val > 10 => val,
            Status.Inactive => 0
        }
    }
    """
    try:
        ast = Parser(file="test_guard.wysna").parse(code_missing_unconditional)
        TypeChecker(file="test_guard.wysna").check(ast)
        assert False, "Expected W2003 for non-exhaustive match with guarded arm"
    except TypeError as e:
        assert e.code == "W2003", f"Expected W2003, got {e.code}"

    # Exhaustive when an unconditional arm exists
    code_exhaustive = """
    enum Status {
        Active(int),
        Inactive
    }

    fn check_status(s: Status) -> int {
        match s {
            Status.Active(val) if val > 10 => val * 2,
            Status.Active(val) => val,
            Status.Inactive => 0
        }
    }

    let s1 = Status.Active(15)
    let s2 = Status.Active(5)
    let s3 = Status.Inactive
    """
    ast = Parser(file="test_guard_ok.wysna").parse(code_exhaustive)
    checker = TypeChecker(file="test_guard_ok.wysna")
    checker.check(ast)

    print("  [PASS] Match guards do not bypass exhaustiveness checking (W2003 enforced)")


def test_native_c99_recursive_adts():
    """Verify recursive ADTs through the AST oracle and verified HIR/MIR path under ASan."""
    code = """
    enum Tree[T] {
        Leaf(T),
        Node(Tree[T], Tree[T])
    }

    enum List[T] {
        Nil,
        Cons(T, List[T])
    }

    enum IntList {
        Empty,
        Cell(int, IntList)
    }

    fn sum_tree(t: Tree[int]) -> int {
        match t {
            Tree.Leaf(val) if val < 0 => 0,
            Tree.Leaf(val) => val,
            Tree.Node(left, right) => sum_tree(left) + sum_tree(right)
        }
    }

    fn length[T](l: List[T]) -> int {
        match l {
            List.Nil => 0,
            List.Cons(head, tail) => 1 + length(tail)
        }
    }

    fn sum_int_list(l: IntList) -> int {
        match l {
            IntList.Empty => 0,
            IntList.Cell(head, tail) => head + sum_int_list(tail)
        }
    }

    fn main() -> int {
        let tree: Tree[int] = Tree.Node(
            Tree.Leaf(10),
            Tree.Node(Tree.Leaf(20), Tree.Leaf(30))
        )
        print(sum_tree(tree))

        let list: List[int] = List.Cons(1, List.Cons(2, List.Cons(3, List.Nil)))
        print(length(list))

        let ilist: IntList = IntList.Cell(100, IntList.Cell(200, IntList.Empty))
        print(sum_int_list(ilist))
        return 0
    }

    main()
    """

    ast = Parser(file="recursive_adts.wysna").parse(code)
    checker = TypeChecker(file="recursive_adts.wysna")
    checker.check(ast)

    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    with tempfile.TemporaryDirectory() as tmpdir:
        # 1. AST Backend
        c_ast = CCodeGen(file="recursive_adts.wysna").generate(ast)
        c_path_ast = os.path.join(tmpdir, "adt_ast.c")
        bin_path_ast = os.path.join(tmpdir, "adt_ast")
        with open(c_path_ast, "w", encoding="utf-8") as f:
            f.write(c_ast)

        comp_ast = subprocess.run(
            [os.environ.get("CC", "cc"), "-std=c99", "-fsanitize=address,undefined", f"-I{repo_root}", c_path_ast, "-o", bin_path_ast],
            capture_output=True, text=True, timeout=60
        )
        assert comp_ast.returncode == 0, f"AST compilation failed:\n{comp_ast.stderr}"
        run_ast = subprocess.run([bin_path_ast], capture_output=True, text=True, timeout=30)
        assert run_ast.returncode == 0, f"AST run failed:\n{run_ast.stderr}"
        expected_output = "60\n3\n300\n"
        assert run_ast.stdout == expected_output, f"AST output mismatch: {run_ast.stdout!r} vs {expected_output!r}"

        # 2. HIR Backend
        builder = HirBuilder(checker.interner)
        hir_mod = builder.lower(ast)
        c_hir = HirCCodeGen(checker.interner).generate(hir_mod)
        c_path_hir = os.path.join(tmpdir, "adt_hir.c")
        bin_path_hir = os.path.join(tmpdir, "adt_hir")
        with open(c_path_hir, "w", encoding="utf-8") as f:
            f.write(c_hir)

        comp_hir = subprocess.run(
            [os.environ.get("CC", "cc"), "-std=c99", "-fsanitize=address,undefined", f"-I{repo_root}", c_path_hir, "-o", bin_path_hir],
            capture_output=True, text=True, timeout=60
        )
        assert comp_hir.returncode == 0, f"HIR compilation failed:\n{comp_hir.stderr}"
        run_hir = subprocess.run([bin_path_hir], capture_output=True, text=True, timeout=30)
        assert run_hir.returncode == 0, f"HIR run failed:\n{run_hir.stderr}"
        assert run_hir.stdout == expected_output, f"HIR output mismatch: {run_hir.stdout!r} vs {expected_output!r}"

    print("  [PASS] Native C99 execution through AST oracle and verified HIR/MIR with AddressSanitizer")


def run_all() -> bool:
    print("==================================================")
    print("Wysna C04 Recursive ADTs & Layout Graph Suite")
    print("==================================================")
    try:
        test_layout_graph_scc_inline_vs_indirect()
        test_reject_infinite_layout_cycles()
        test_type_interner_enum_payloads()
        test_match_guards_and_exhaustiveness()
        test_native_c99_recursive_adts()
        print("==================================================")
        print("ALL C04 ADT & LAYOUT GRAPH TESTS PASSED.")
        print("==================================================")
        return True
    except Exception as e:
        print(f"[FAIL] C04 Test Suite Failed: {type(e).__name__}: {e}")
        return False


if __name__ == "__main__":
    if not run_all():
        sys.exit(1)
    sys.exit(0)
