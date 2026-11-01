"""Regression contracts for call identity, normal exits, and composed effects."""
import os
from pathlib import Path
import tempfile
from checker.TypeChecker import TypeChecker
from error.Error import WysnaError
from hir.HirBuilder import HirBuilder
from hir.HirCCodeGen import HirCCodeGen
from hir.HirNodes import CollectionCallTarget, HirConstInt
from main import ensure_selfhost_compiler_binary, SELFHOST_BOOTSTRAP_INPUTS
from parser.Parser import Parser
from tests.engine_harness import verify
from tests.fuzz.semantic_properties import generate_properties

ROOT = Path(__file__).resolve().parents[2]


def checked(source):
    ast = Parser().parse(source)
    checker = TypeChecker()
    checker.check(ast)
    return HirBuilder(checker.interner).lower(ast), checker.interner


def rejected(module, interner):
    try:
        HirCCodeGen(interner).generate(module)
    except WysnaError as error:
        assert error.code == 'W9999', error
    else:
        raise AssertionError('Malformed HIR reached C emission')


def test_verifier_rejects_wrong_call_identity():
    module, interner = checked('class C { public fn append(x: int) -> int { return x } }\nprint(C().append(7))')
    call = module.top_level_stmts[0].expr.args[0]
    call.target = CollectionCallTarget('list', 'append')
    rejected(module, interner)
    module, interner = checked('class C { public fn answer() -> int { return 7 } }\nprint(C().answer())')
    call = module.top_level_stmts[0].expr.args[0]
    call.target.slot = 999
    rejected(module, interner)


def test_verifier_rejects_implicit_unit_in_value_function():
    module, interner = checked('fn choose(x: int) -> list[int] { match x { 1 => { return [7] } }\nreturn [8] }')
    module.functions[0].body.statements.pop()
    rejected(module, interner)


def test_verifier_rejects_non_addressable_assignment():
    module, interner = checked('let mut xs = [1]\nprint((xs[0] = 2))')
    assignment = module.top_level_stmts[1].expr.args[0]
    assignment.target = HirConstInt(7, assignment.type_id, assignment.span)
    rejected(module, interner)


def test_bootstrap_tracks_extracted_semantics():
    prefix = 'src/' if (ROOT / 'src').exists() else ''
    required = {f'{prefix}checker/ConstructorFlow.py', f'{prefix}checker/ClosureConversion.py',
                f'{prefix}checker/PatternCoverage.py', f'{prefix}common/ControlFlow.py',
                f'{prefix}compiler/CompilationSession.py', f'{prefix}codegen/VirtualDispatch.py'}
    assert required <= set(SELFHOST_BOOTSTRAP_INPUTS)


def test_seeded_semantic_properties():
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    with tempfile.TemporaryDirectory(prefix='wysna-semantic-properties-') as temporary:
        for seed in (17, 29, 37, 51, 63, 101, 202, 303):
            source, expected = generate_properties(seed)
            ok, detail = verify(source, f'audit_properties_{seed}.wysna', selfhost_bin, temporary,
                                expected=expected, strict=True, sanitize=os.environ.get('WYSNA_SANITIZE') == '1')
            assert ok, f'seed {seed}: {detail}\n{source}'


def run_audit_tests():
    test_verifier_rejects_wrong_call_identity()
    test_verifier_rejects_implicit_unit_in_value_function()
    test_verifier_rejects_non_addressable_assignment()
    test_bootstrap_tracks_extracted_semantics()
    test_seeded_semantic_properties()
    print('Audit regression and independent semantic property contracts passed')
    return True


if __name__ == '__main__':
    run_audit_tests()
