"""B03 language, ownership and isolated strong-cycle acceptance gates."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from parser.Parser import Parser
from checker.TypeChecker import TypeChecker
from error.Error import WysnaError
from hir.HirBuilder import HirBuilder
from hir.HirCCodeGen import HirCCodeGen
from tests.unit.test_weak_control_block import run_control_block_tests


def run_weak_tests():
    run_control_block_tests()
    invalid = ['let value = weak(1)', 'let value = weak(none)',
               'let value = upgrade(1)', 'let value = weak()',
               'let value = upgrade(none)',
               'class Node {}\nlet value: Weak[int] = weak(Node())',
               'class Node {}\nlet value: Weak[Node, Node] = weak(Node())',
               'class Node { public id: int }\nlet value = weak(Node())\nprint(value.id)',
               'struct Record { value: int }\nlet value = weak(Record { value: 1 })',
               'class Weak {}',
               'class Base {}\nclass Child extends Base {}\nlet value: Weak[Base] = weak(Child())']
    for source in invalid:
        try:
            TypeChecker().check(Parser().parse(source))
        except WysnaError as error:
            assert error.code.startswith('W'), str(error)
        else:
            raise AssertionError('Invalid weak operation was accepted: ' + source)
    generic = 'class Node {}\nfn identity[T](value: T) -> T { return value }\nlet node = Node()\nlet handle = identity(weak(node))'
    generic_ast = Parser().parse(generic)
    generic_checker = TypeChecker()
    generic_checker.check(generic_ast)
    HirBuilder(generic_checker.interner).lower(generic_ast)
    from checker.TypeInterner import TYPE_INT, TYPE_ERROR
    assert generic_checker.interner.mk_weak(TYPE_INT) == TYPE_ERROR
    source = 'class Node {}\nlet object = Node()\nlet handle = weak(object)'
    ast = Parser().parse(source)
    checker = TypeChecker()
    checker.check(ast)
    hir = HirBuilder(checker.interner).lower(ast)
    call = hir.top_level_stmts[-1].init_expr
    call.type_id = checker.interner.mk_optional(call.type_id)
    try:
        HirCCodeGen(checker.interner).generate(hir)
    except WysnaError as error:
        assert error.code == 'W9999', str(error)
    else:
        raise AssertionError('Malformed weak builtin result reached C emission')
    root = Path(__file__).resolve().parents[2]
    env = dict(os.environ, WYSNA_MEM_CHECK='1')
    env['ASAN_OPTIONS'] = 'detect_leaks=1' if sys.platform.startswith('linux') else 'detect_leaks=0'
    with tempfile.TemporaryDirectory(prefix='wysna-b03-') as directory:
        for name, expected_leak in [('weak_references', False), ('class_cycle_limitation', False),
                                    ('strong_cycle', True)]:
            folder = 'memory' if expected_leak else 'runtime'
            source = root / 'tests' / folder / (name + '.wysna')
            expected = '' if expected_leak else source.with_suffix('.expected').read_text()
            if not expected_leak:
                interpreted = subprocess.run([sys.executable, 'main.py', 'run', str(source)],
                                             cwd=root, capture_output=True, text=True, timeout=30)
                assert interpreted.returncode == 0, interpreted.stderr
                assert interpreted.stdout == expected, interpreted.stdout
            for backend in ['ast', 'hir', 'selfhost']:
                c_path = Path(directory) / (name + backend + '.c')
                binary = Path(directory) / (name + backend)
                if backend == 'selfhost':
                    from main import ensure_selfhost_compiler_binary
                    selfhost_bin = ensure_selfhost_compiler_binary(str(root))
                    command = [selfhost_bin, str(source), str(c_path)]
                elif backend == 'ast':
                    command = [sys.executable, str(root / 'tests/emit_ast_oracle.py'),
                               str(source), str(c_path)]
                else:
                    command = [sys.executable, 'main.py', 'emit-c']
                    command += [str(source), '-o', str(c_path)]
                emitted = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=60)
                assert emitted.returncode == 0, (name, backend, emitted.stderr)
                built = subprocess.run([os.environ.get('CC', 'cc'), '-std=c99', '-g',
                                        '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                                        '-I' + str(root), str(c_path), '-o', str(binary)],
                                       capture_output=True, text=True, timeout=60)
                assert built.returncode == 0, (name, backend, built.stderr)
                process_env = dict(env)
                if expected_leak and sys.platform.startswith('linux'):
                    # The cycle is unreachable after create_cycle returns. Exclude stale
                    # machine roots so conservative LSan scanning is deterministic.
                    process_env['LSAN_OPTIONS'] = 'use_stacks=0:use_registers=0'
                result = subprocess.run([str(binary)], cwd=root, env=process_env,
                                        capture_output=True, text=True, timeout=30)
                if expected_leak:
                    assert result.returncode != 0 and 'Memory leak detected at exit' in result.stderr, result.stderr
                    assert 'ERROR: AddressSanitizer' not in result.stderr, result.stderr
                    assert 'runtime error:' not in result.stderr, result.stderr
                    if sys.platform.startswith('linux'):
                        assert 'LeakSanitizer: detected memory leaks' in result.stderr, result.stderr
                    opt_out_env = dict(process_env)
                    opt_out_env.pop('WYSNA_MEM_CHECK', None)
                    opt_out_env['ASAN_OPTIONS'] = 'detect_leaks=0'
                    quiet = subprocess.run([str(binary)], cwd=root, env=opt_out_env,
                                            capture_output=True, text=True, timeout=30)
                    assert quiet.returncode == 0, quiet.stderr
                    assert quiet.stderr == '', quiet.stderr
                else:
                    assert result.returncode == 0, (name, backend, result.stderr)
                    assert result.stdout == expected, (name, backend, result.stdout)
                print(f'  [PASS] B03 {backend}/{name}: ' + ('expected isolated leak' if expected_leak else 'zero leaks'))


if __name__ == '__main__':
    run_weak_tests()
