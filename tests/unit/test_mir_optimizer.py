"""Unit tests for MIR CFG optimizations, dead block pruning, and trampoline elimination."""
import unittest
from copy import deepcopy

from checker.TypeChecker import TypeChecker
from checker.TypeInterner import TYPE_BOOL, TYPE_INT
from hir.HirBuilder import HirBuilder
from mir.Lowering import Lowering
from mir.Nodes import Operation, Terminator, dump
from mir.Optimizer import (
    eliminate_unreachable_blocks,
    simplify_branches,
    eliminate_empty_trampolines,
    simplify_function_cfg,
    optimize,
)
from mir.Verifier import Verifier
from parser.Parser import Parser


def lower_mir(source: str, optimize_mir: bool = False):
    ast = Parser(file='optimizer-test.wysna').parse(source)
    checker = TypeChecker(file='optimizer-test.wysna')
    checker.check(ast)
    hir = HirBuilder(checker.interner, file_path='optimizer-test.wysna').lower(ast)
    module = Lowering(checker.interner, 'optimizer-test.wysna').lower(hir, optimize=optimize_mir)
    return module, checker.interner


class MirOptimizerTests(unittest.TestCase):
    def test_unreachable_block_elimination(self):
        source = """
fn early_return(x: int) -> int {
    if x > 0 {
        return 1
    } else {
        return 2
    }
}
print(early_return(5))
"""
        module, interner = lower_mir(source, optimize_mir=False)
        fn = module.functions[0]
        # Initially, Lowering created an unreachable join block
        self.assertTrue(any(b.terminator.kind == 'unreachable' for b in fn.blocks))
        initial_block_count = len(fn.blocks)

        changed = eliminate_unreachable_blocks(fn)
        self.assertTrue(changed)
        self.assertLess(len(fn.blocks), initial_block_count)
        # All blocks must be contiguously numbered 0 .. len(blocks)-1
        self.assertEqual([b.id for b in fn.blocks], list(range(len(fn.blocks))))
        # No unreachable blocks remain
        self.assertFalse(any(b.terminator.kind == 'unreachable' for b in fn.blocks))
        # Verifier must pass cleanly
        Verifier(interner).verify(module)

    def test_while_loop_dead_block_pruning(self):
        source = """
fn loop_count(limit: int) -> int {
    let mut i = 0
    while i < limit {
        i = i + 1
    }
    return i
}
print(loop_count(10))
"""
        module, interner = lower_mir(source, optimize_mir=False)
        fn = module.functions[0]
        initial_count = len(fn.blocks)
        self.assertTrue(any(b.terminator.kind == 'unreachable' for b in fn.blocks))

        changed = eliminate_unreachable_blocks(fn)
        self.assertTrue(changed)
        self.assertEqual(len(fn.blocks), initial_count - 1)
        self.assertEqual([b.id for b in fn.blocks], list(range(len(fn.blocks))))
        Verifier(interner).verify(module)

    def test_trampoline_elimination(self):
        source = """
fn print_positive(x: int) {
    if x > 0 {
        print("positive")
    }
}
print_positive(4)
"""
        module, interner = lower_mir(source, optimize_mir=False)
        fn = module.functions[0]
        # In `if` without `else`, an empty trampoline block exists
        has_trampoline = any(
            b.id != 0 and len(b.operations) == 0 and b.terminator and b.terminator.kind == 'goto'
            for b in fn.blocks
        )
        self.assertTrue(has_trampoline)

        changed = eliminate_empty_trampolines(fn)
        self.assertTrue(changed)
        eliminate_unreachable_blocks(fn)

        # After bypassing and pruning, no empty trampolines remain
        self.assertFalse(any(
            b.id != 0 and len(b.operations) == 0 and b.terminator and b.terminator.kind == 'goto'
            for b in fn.blocks
        ))
        Verifier(interner).verify(module)

    def test_constant_branch_folding(self):
        source = """
fn const_branch() -> int {
    let flag = true
    if flag {
        return 100
    } else {
        return 200
    }
}
print(const_branch())
"""
        module, interner = lower_mir(source, optimize_mir=False)
        fn = module.functions[0]
        simplify_branches(fn)
        eliminate_unreachable_blocks(fn)

        # The branch must have been folded, leaving no conditional branch
        self.assertFalse(any(b.terminator.kind == 'branch' for b in fn.blocks))
        Verifier(interner).verify(module)

    def test_identical_targets_folding(self):
        source = """
fn redundant_branch(flag: bool) -> int {
    if flag {
        return 42
    } else {
        return 42
    }
}
print(redundant_branch(true))
"""
        module, interner = lower_mir(source, optimize_mir=False)
        fn = module.functions[0]
        # Manually point branch targets to the same block to test redundant branch folding
        for b in fn.blocks:
            if b.terminator and b.terminator.kind == 'branch':
                b.terminator.targets = (b.terminator.targets[0], b.terminator.targets[0])
                break

        changed = simplify_branches(fn)
        self.assertTrue(changed)
        eliminate_unreachable_blocks(fn)
        self.assertEqual([b.id for b in fn.blocks], list(range(len(fn.blocks))))
        Verifier(interner).verify(module)

    def test_full_pipeline_optimization_and_idempotence(self):
        source = """
let global = "global"
fn read(text: str) -> int { len(text) + ord(text[0]) + len(text) }
fn compute(n: int) -> int {
    let mut sum = 0
    let mut i = 0
    while i < n {
        if i % 2 == 0 {
            sum = sum + i
        }
        i = i + 1
    }
    return sum
}
print(read("wysna"), compute(10))
"""
        module, interner = lower_mir(source, optimize_mir=False)
        optimized = optimize(module, interner)
        first_dump = dump(optimized)

        # Applying optimize again must be idempotent
        second = optimize(optimized, interner)
        self.assertEqual(dump(second), first_dump)
        Verifier(interner).verify(second)


if __name__ == '__main__':
    unittest.main()
