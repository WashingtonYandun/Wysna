"""Adversarial closure contracts for the principal compiler audit."""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from error.Error import ParserErrors
from main import ensure_selfhost_compiler_binary
from mir.Verifier import RUNTIME, RUNTIME_ARITIES, Verifier
from parser.Parser import Parser
from tests.engine_harness import verify
from tests.unit.test_mir import lower, operations, rejects

ROOT = Path(__file__).resolve().parents[2]


def test_principal_regressions_across_engines():
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    with tempfile.TemporaryDirectory(prefix='wysna-readiness-') as directory:
        for path in sorted((ROOT / 'tests/runtime').glob('readiness_*.wysna')):
            ok, detail = verify(path.read_text(), str(path.relative_to(ROOT)), selfhost_bin, directory,
                                expected=path.with_suffix('.expected').read_text(), strict=True,
                                sanitize=os.environ.get('WYSNA_SANITIZE') == '1')
            assert ok, detail
        for path in sorted((ROOT / 'tests/types/invalid').glob('readiness_*.wysna')):
            ok, detail = verify(path.read_text(), str(path.relative_to(ROOT)), selfhost_bin, directory,
                                strict=True, expect_rejection=True)
            assert ok, detail


RUNTIME_CASES = [
    ('print(chr(65))', 'wysna_builtin_chr'),
    ('print(ord("a"))', 'wysna_builtin_ord'),
    ('print(bytes("abc"))', 'wysna_builtin_bytes'),
    ('print(args())', 'wysna_builtin_args'),
    ('print(slice("abc", 0, 1))', 'wysna_builtin_slice'),
    ('print(enumerate([1]))', 'wysna_builtin_enumerate'),
    ('print(dict_items({1: "a"}))', 'wysna_builtin_dict_items'),
    ('print(read_file("missing"))', 'wysna_builtin_read_file'),
    ('print(write_file("missing", "text"))', 'wysna_builtin_write_file'),
    ('class C {}\nprint(weak(C()))', 'wysna_builtin_weak_owned'),
    ('class C {}\nprint(upgrade(weak(C())))', 'wysna_builtin_upgrade'),
    ('fn f() -> int ! str { Err("bad") }\nprint(f())', 'wysna_err'),
    ('fn f() -> int ! str { Ok(1) }\nprint(f())', 'wysna_ok'),
    ('let x: int? = 1\nprint(x ?? 0)', 'wysna_unwrap_optional'),
    ('enum E { A(int) }\nprint(E.A(1))', 'wysna_enum_payload'),
]
def test_runtime_result_signatures_reject_corrupted_ir():
    for source, helper in RUNTIME_CASES:
        original, interner = lower(source)
        broken = deepcopy(original)
        operation = next(item for item in operations(broken) if item.opcode == 'runtime' and item.data['name'] == helper)
        function = next(fn for fn in broken.functions if operation in [op for block in fn.blocks for op in block.operations])
        slot = function.slots[operation.result]
        wrong = interner.mk_list(slot.type_id)
        function.slots[slot.id] = replace(slot, type_id=wrong, storage='value')
        slots = {item.id: item for item in broken.globals + function.slots}
        try:
            Verifier(interner).runtime_types(operation, [slots[index] for index in operation.args], slots[slot.id])
        except Exception as error:
            assert 'runtime operand/result types mismatch: ' + helper in str(error), error
        else:
            raise AssertionError('The runtime signature itself accepted an invalid result')
        rejects(broken, interner)


def test_runtime_helper_substitution_and_exhaustive_registry():
    assert set(RUNTIME_ARITIES) == RUNTIME
    module, interner = lower('print(int(65))')
    operation = next(item for item in operations(module) if item.opcode == 'runtime' and item.data['name'] == 'wysna_builtin_int')
    operation.data['name'] = 'wysna_builtin_chr'
    rejects(module, interner)
    module, interner = lower('print(Ok(1) == Err(1))')
    operation = next(item for item in operations(module) if item.opcode == 'runtime' and item.data['name'] == 'wysna_equal')
    function = next(fn for fn in module.functions if operation in [op for block in fn.blocks for op in block.operations])
    from checker.TypeInterner import TYPE_INT, TYPE_STR
    for index, type_id in zip(operation.args, (TYPE_INT, TYPE_STR)):
        function.slots[index] = replace(function.slots[index], type_id=type_id)
    slots = {item.id: item for item in function.slots}
    try:
        Verifier(interner).runtime_types(operation, [slots[index] for index in operation.args], slots[operation.result])
    except Exception as error:
        assert 'runtime operand/result types mismatch: wysna_equal' in str(error), error
    else:
        raise AssertionError('Equality accepted incompatible canonical operands')
    function_type = interner.mk_function((), TYPE_INT)
    assert not interner.equality_compatible(function_type, function_type)
    nested_function = interner.mk_list(function_type)
    assert not interner.equality_compatible(nested_function, nested_function)


def test_recovery_reports_multiple_errors_without_returning_a_partial_program():
    source = 'fn broken() {\nlet first =\nlet second =\nlet valid = 1\n}\nlet final =\n'
    try:
        Parser().parse(source, recover=True)
    except ParserErrors as failure:
        diagnostics = failure.to_dict()['diagnostics']
    else:
        raise AssertionError('Recovered syntax errors must prevent execution')
    assert len(diagnostics) >= 3
    assert [error['line'] for error in diagnostics] == sorted(error['line'] for error in diagnostics)
    with tempfile.TemporaryDirectory(prefix='wysna-diagnostics-') as directory:
        path = Path(directory) / 'broken.wysna'
        path.write_text(source)
        result = subprocess.run([sys.executable, str(ROOT / 'main.py'), 'check', '--json', str(path)],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 1
        assert len(json.loads(result.stdout)['diagnostics']) >= 3




def test_compositional_patterns_and_closures():
    from tests.fuzz.readiness_properties import generate_readiness_case
    from tests.formatter_contract import verify_formatting
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    with tempfile.TemporaryDirectory(prefix='wysna-readiness-properties-') as directory:
        for seed in (0, 1, 3, 8, 13, 21, 34, 55):
            source, expected = generate_readiness_case(seed)
            formatted = verify_formatting(source)
            for variant, text in (('original', source), ('formatted', formatted)):
                ok, detail = verify(text, f'readiness_{seed}_{variant}.wysna', selfhost_bin, directory,
                                    expected=expected, strict=True,
                                    sanitize=os.environ.get('WYSNA_SANITIZE') == '1')
                assert ok, detail


def run_readiness_tests():
    test_principal_regressions_across_engines()
    test_runtime_result_signatures_reject_corrupted_ir()
    test_runtime_helper_substitution_and_exhaustive_registry()
    test_recovery_reports_multiple_errors_without_returning_a_partial_program()
    test_pretokenized_parser_does_not_lex_twice_without_patch()
    test_compositional_patterns_and_closures()
    test_scoped_foreign_resources()
    test_canonical_enum_payload_metadata_survives_roundtrip()
    test_lsp_publishes_recovered_syntax_diagnostics()
    test_native_build_reports_independent_compile_and_link_phases()
    test_parser_recovery_has_a_flat_diagnostic_budget()
    test_pilot_package_rejects_unverified_sqlite_sources()
    print('Principal audit: patterns, closures, cyclic formatting, MIR signatures and recovery passed')
    return True


def test_pilot_package_rejects_unverified_sqlite_sources():
    from tools.package_measurement_pilot import sqlite_archive
    with tempfile.TemporaryDirectory(prefix='wysna-package-integrity-') as directory:
        archive = Path(directory) / 'sqlite.zip'
        archive.write_bytes(b'not the pinned SQLite source archive')
        try:
            sqlite_archive(archive)
        except ValueError as failure:
            assert 'pinned official SHA3-256' in str(failure)
        else:
            raise AssertionError('The package builder accepted unverified C source')


def test_pretokenized_parser_does_not_lex_twice_without_patch():
    from unittest.mock import patch
    from lexer.Lexer import Lexer
    source = 'print(1)'
    tokens = Lexer(source).tokenize()
    with patch.object(Lexer, 'tokenize', side_effect=AssertionError('duplicate lexing')):
        assert Parser().parse(source, tokens=tokens).body




def test_scoped_foreign_resources():
    from common.Platform import compiler_command, executable_name
    expected = ('Ok(9) 0 0\n0 0\nresource is closed\n'
                'Err(ResourceError { message: "body failed" }) 0 0\n0 0\nresource is closed\n'
                'Err(ResourceError { message: "body failed" }) 5 1\n0 0\nresource is closed\n')
    with tempfile.TemporaryDirectory(prefix='wysna-scoped-resource-') as directory:
        suffix = '.dll' if os.name == 'nt' else '.dylib' if sys.platform == 'darwin' else '.so'
        shared = Path(directory) / ('adapter' + suffix)
        subprocess.run(compiler_command() + ['-std=c99', '-dynamiclib' if sys.platform == 'darwin' else '-shared',
            str(ROOT / 'tests/ffi/resource_adapter.c'), '-o', str(shared)], check=True, capture_output=True, timeout=60)
        interpreted = subprocess.run([sys.executable, str(ROOT / 'main.py'), 'run',
            str(ROOT / 'tests/ffi/resources.wysna'), '--link-lib', str(shared)], capture_output=True, text=True, timeout=30)
        assert (interpreted.returncode, interpreted.stdout, interpreted.stderr) == (0, expected, ''), interpreted
        for backend in ('ast', 'hir', 'selfhost'):
            binary = Path(directory) / executable_name(backend)
            flags = ['--sanitize'] if os.environ.get('WYSNA_SANITIZE') == '1' else []
            built = subprocess.run([sys.executable, str(ROOT / 'main.py'), 'build',
                str(ROOT / 'tests/ffi/resources.wysna'), '--backend=' + backend,
                '--link-lib', str(ROOT / 'tests/ffi/resource_adapter.c'), '-o', str(binary)] + flags,
                capture_output=True, text=True, timeout=120)
            assert built.returncode == 0, built.stderr
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30,
                env=dict(os.environ, WYSNA_MEM_CHECK='1', ASAN_OPTIONS='detect_leaks=' + ('1' if sys.platform.startswith('linux') else '0'), UBSAN_OPTIONS='halt_on_error=1'))
            assert (result.returncode, result.stdout, result.stderr) == (0, expected, ''), result




def test_canonical_enum_payload_metadata_survives_roundtrip():
    from checker.TypeChecker import TypeChecker
    checker = TypeChecker()
    ast = Parser().parse('enum Event { Data(tuple[bool, int]), Empty }\nfn f(x: tuple[Event, bool]) {}')
    checker.check(ast)
    event = checker.global_scope.lookup_type('Event')
    restored = checker.interner.to_type(checker.interner.intern(event))
    assert restored.variant_payloads['Data'][0].elements == event.variant_payloads['Data'][0].elements


def test_lsp_publishes_recovered_syntax_diagnostics():
    from tooling.LanguageServer import LanguageServer
    messages = []
    server = LanguageServer(messages.append)
    uri = (ROOT / 'readiness-diagnostics.wysna').as_uri()
    server.update(uri, 'let first =\nlet second =\nlet valid = 1', 1)
    diagnostics = messages[-1]['params']['diagnostics']
    assert len(diagnostics) == 2 and all(item['source'] == 'Wysna' for item in diagnostics)
    assert uri not in server.checked


def test_native_build_reports_independent_compile_and_link_phases():
    with tempfile.TemporaryDirectory(prefix='wysna-build-phases-') as directory:
        binary = Path(directory) / 'program'
        result = subprocess.run([sys.executable, str(ROOT / 'main.py'), 'build', '--stats',
            str(ROOT / 'examples/01_hello_world.wysna'), '-o', str(binary)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
        timings = json.loads(result.stderr)['telemetry']['timing_ms']
        assert timings['c_compile'] > 0 and timings['link'] > 0 and timings['mir'] >= 0 and timings['c_emit'] >= 0
        assert timings['hir'] >= 0 and timings['codegen'] >= timings['mir'] + timings['c_emit'] - 0.1
        assert timings['total'] >= timings['c_compile'] + timings['link'] - 0.1




def test_parser_recovery_has_a_flat_diagnostic_budget():
    source = 'fn broken() {\n' + 'let x =\n' * 150 + '}\n'
    try:
        Parser().parse(source, recover=True)
    except ParserErrors as error:
        assert len(error.diagnostics) == 100
        assert all(not isinstance(item, ParserErrors) for item in error.diagnostics)
    else:
        raise AssertionError('Expected the parser diagnostic budget')


if __name__ == '__main__':
    run_readiness_tests()
