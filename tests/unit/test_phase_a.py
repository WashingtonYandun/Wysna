"""Reproducible Phase A regression evidence."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from checker.TypeChecker import TypeChecker
from checker.TypeInterner import TypeInterner
from checker.WysnaType import IntType, StrType, OptionalType, ResultType, EnumType
from parser.Parser import Parser
from formatter.Formatter import Formatter
from error.Error import WysnaError


def run_phase_a_tests():
    print(f"Phase A platform={sys.platform} compiler={os.environ.get('CC', 'cc')}")
    interner = TypeInterner()
    for name, args, sugar in [
        ('Option', [IntType], OptionalType(IntType)),
        ('Result', [IntType, StrType], ResultType(IntType, StrType)),
    ]:
        enum = EnumType(name + '__probe', [])
        enum.template_name, enum.type_args = name, args
        assert interner.intern(enum) == interner.intern(sugar)
    assert interner.mk_optional(interner.mk_optional(3)) != interner.mk_optional(3)

    source = "fn id[T](x: T) -> T { return x }\nlet sugar: int? = 1\nlet alias: Option[int] = Some(1)\nlet first = id(sugar)\nlet second = id(alias)"
    ast = Parser().parse(source)
    checker = TypeChecker()
    checker.check(ast)
    assert checker.telemetry.specialized_functions == 1


    parser, formatter = Parser(), Formatter()
    for annotation in ['int?', 'int ! str', '(int?) ! str', '(int ! str)?', '(int ! str) ! bool', '(int?)?']:
        source = 'fn signature() -> ' + annotation + ' {}'
        ast = parser.parse(source)
        rendered = formatter.format(ast)
        assert formatter.format(parser.parse(rendered)) == rendered

    invalid = [
        'fn bad(x: (int?) ! str) -> int ! str { let inner = x?\nreturn inner? }',
        'fn bad(x: int ! bool) -> int ! str { return x? }',
        'enum Result[T, E] { Ok(T), Err(E) }',
        'let value: int? = none\nlet ambiguous: (int?)? = value',
        'fn bad(x: Option[Result[int, str]]) -> int { match x { Some(Ok(v)) => v, None => 0 } }',
        'enum Pair[A, B] { Item(A, B) }\nfn bad(x: Pair[bool, bool]) -> int { match x { Item(true, false) => 1, Item(false, true) => 2 } }',
        'fn bad(x: Option[int]) -> int { match x { Some(v) if true => v, None => 0 } }',
    ]
    for source in invalid:
        try:
            TypeChecker().check(parser.parse(source))
        except WysnaError as error:
            assert error.code in ('W2006', 'W1002', 'W2003', 'W2001'), str(error)
        else:
            raise AssertionError('Invalid propagation/reserved builtin identity was accepted')

    root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='wysna-phase-a-') as directory:
        for name in ['strings_ownership', 'generic_error_composition', 'generic_domain_err', 'generic_match_guards']:
            source = root / 'tests/runtime' / (name + '.wysna')
            expected = source.with_suffix('.expected').read_text()
            interpreted = subprocess.run([sys.executable, str(root / 'main.py'), 'run', str(source)],
                                         capture_output=True, text=True, timeout=30)
            assert interpreted.returncode == 0, interpreted.stderr
            assert interpreted.stdout == expected, (name, interpreted.stdout, expected)
            for hir in [False, True]:
                binary = Path(directory) / ('program-hir' if hir else 'program-ast')
                if hir:
                    command = [sys.executable, str(root / 'main.py'), 'build', '--sanitize',
                               str(source), '-o', str(binary)]
                else:
                    command = [sys.executable, str(root / 'tests/emit_ast_oracle.py'),
                               str(source), str(Path(directory) / (name + '-ast.c')),
                               '--build', str(binary), '--sanitize']
                compiled = subprocess.run(command,
                                          capture_output=True, text=True, timeout=60)
                assert compiled.returncode == 0, compiled.stderr
                env = dict(os.environ, WYSNA_MEM_CHECK='1')
                env['ASAN_OPTIONS'] = 'detect_leaks=1' if sys.platform.startswith('linux') else 'detect_leaks=0'
                result = subprocess.run([str(binary)], env=env, capture_output=True, text=True, timeout=30)
                assert result.returncode == 0, (name, result.stderr)
                assert result.stdout == expected, (name, result.stdout, expected)
    print('  [PASS] Phase A identity, grouped types, propagation, strings and ASan/UBSan regressions')


if __name__ == '__main__':
    run_phase_a_tests()
