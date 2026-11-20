"""Independent N/M scaling, multiple aliases and repeated writes for B02."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from parser.Parser import Parser
from checker.TypeChecker import TypeChecker
from error.Error import WysnaError
from runtime.Interpreter import Interpreter
from runtime.Values import cow_telemetry
from codegen.CCodeGen import CCodeGen
from hir.HirBuilder import HirBuilder
from hir.HirCCodeGen import HirCCodeGen
from main import ensure_selfhost_compiler_binary
from tests.unit.test_cow_telemetry import parse_checkpoints


LOGICAL_KEYS = ('detach_list', 'detach_dict', 'detach_struct', 'copied_slots')


def logical_metrics(checkpoints):
    return {label: {key: values[key] for key in LOGICAL_KEYS}
            for label, values in checkpoints.items()}


def matrix_program():
    functions, calls, expected, dimensions = [], [], [], []
    for rows, columns in [(2, 3), (2, 17), (7, 3), (7, 17)]:
        name = f'matrix_{rows}_{columns}'
        dimensions.append((name, rows, columns))
        row_literals = ['[' + ', '.join(['0'] * columns) + ']' for _ in range(rows)]
        lines = [f'fn {name}() -> unit {{',
                 'let mut matrix = [' + ', '.join(row_literals) + ']',
                 'let snap0 = matrix', 'let snap1 = matrix']
        phases = [('baseline', ''), ('first', 'matrix[0][0] = 1'),
                  ('second', 'matrix[0][1] = 2'), ('third', 'matrix[1][0] = 3'),
                  ('fourth', 'matrix[1][1] = 4'), ('renew', 'let middle = matrix'),
                  ('fifth', 'matrix[0][0] = 5'), ('sixth', 'matrix[0][1] = 6')]
        for phase, statement in phases:
            if statement:
                lines.append(statement)
            lines.append(f'eprint("__COW_CHECKPOINT__:{name}/{phase}")')
        lines += [f'print({rows}, {columns}, matrix[0][0], matrix[0][1], '
                  'matrix[1][0], matrix[1][1], snap0[0][0], snap1[0][0], middle[0][0])', '}']
        functions.append('\n'.join(lines))
        calls.append(name + '()')
        expected.append(f'{rows} {columns} 5 6 3 4 0 0 1\n')
    functions.append('fn constructor_retention() -> unit {\n    let mut nested = [[1, 2]]\n    eprint("__COW_CHECKPOINT__:retention/before")\n    nested[0][0] = 9\n    eprint("__COW_CHECKPOINT__:retention/after")\n    print(nested[0][0])\n}')
    calls.append('constructor_retention()')
    expected.append('9\n')
    return '\n'.join(functions + calls), ''.join(expected), dimensions


def validate_scaling(checkpoints, dimensions):
    phases = ['baseline', 'first', 'second', 'third', 'fourth', 'renew', 'fifth', 'sixth']
    labels = {f'{name}/{phase}' for name, _, _ in dimensions for phase in phases}
    assert set(checkpoints) == labels | {'retention/before', 'retention/after'}
    for name, rows, columns in dimensions:
        previous = checkpoints[f'{name}/baseline']
        for phase, detaches, slots in [('first', 2, rows + columns), ('second', 0, 0),
                                      ('third', 1, columns), ('fourth', 0, 0),
                                      ('renew', 0, 0), ('fifth', 2, rows + columns),
                                      ('sixth', 0, 0)]:
            current = checkpoints[f'{name}/{phase}']
            assert current['detach_list'] - previous['detach_list'] == detaches, (name, phase)
            assert current['copied_slots'] - previous['copied_slots'] == slots, (name, phase)
            assert current['detach_dict'] == previous['detach_dict']
            assert current['detach_struct'] == previous['detach_struct']
            if 'allocs' in current:
                assert current['allocs'] - previous['allocs'] == 2 * detaches, (name, phase)
                for field in ['copied_bytes', 'allocated_bytes']:
                    delta = current[field] - previous[field]
                    assert delta > 0 if detaches else delta == 0, (name, phase, field)
            previous = current


def run_scaling_tests():
    root = Path(__file__).resolve().parents[2]
    source, expected, dimensions = matrix_program()
    ast = Parser().parse(source)
    checker = TypeChecker()
    checker.check(ast)
    for invalid in ['let value = [1]\nvalue[0] = 2',
                    'let mut pair = (1, 2)\npair[0] = 3']:
        try:
            TypeChecker().check(Parser().parse(invalid))
        except WysnaError:
            pass
        else:
            raise AssertionError('Immutable/tuple mutation was accepted')
    output, errors = io.StringIO(), io.StringIO()
    cow_telemetry.reset()
    previous = os.environ.get('WYSNA_COW_METRICS')
    os.environ['WYSNA_COW_METRICS'] = '1'
    try:
        with contextlib.redirect_stderr(errors):
            Interpreter(stdout=output).interpret(ast)
    finally:
        if previous is None:
            os.environ.pop('WYSNA_COW_METRICS', None)
        else:
            os.environ['WYSNA_COW_METRICS'] = previous
    assert output.getvalue() == expected
    reference = parse_checkpoints(errors.getvalue())
    validate_scaling(reference, dimensions)
    assert reference['retention/after'] == reference['retention/before']
    hir = HirBuilder(checker.interner).lower(ast)
    with tempfile.TemporaryDirectory(prefix='wysna-cow-scaling-') as directory:
        wysna_path = Path(directory) / 'scaling.wysna'
        wysna_path.write_text(source)
        native_reference = None
        for backend in ['ast', 'hir', 'selfhost']:
            c_path, binary = Path(directory) / (backend + '.c'), Path(directory) / backend
            if backend == 'selfhost':
                selfhost_bin = ensure_selfhost_compiler_binary(str(root))
                subprocess.run([selfhost_bin, str(wysna_path), str(c_path)], cwd=root,
                               check=True, capture_output=True, text=True, timeout=60)
            else:
                code = (CCodeGen().generate(ast) if backend == 'ast'
                        else HirCCodeGen(checker.interner).generate(hir))
                c_path.write_text(code)
            subprocess.run([os.environ.get('CC', 'cc'), '-std=c99', '-fsanitize=address,undefined',
                            '-fno-omit-frame-pointer', '-I' + str(root), str(c_path), '-o', str(binary)],
                           check=True, capture_output=True, text=True, timeout=60)
            env = dict(os.environ, WYSNA_MEM_CHECK='1', WYSNA_COW_METRICS='1')
            env['ASAN_OPTIONS'] = 'detect_leaks=1' if sys.platform.startswith('linux') else 'detect_leaks=0'
            result = subprocess.run([str(binary)], env=env, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, (backend, result.stderr)
            assert result.stdout == expected, (backend, result.stdout)
            metrics = parse_checkpoints(result.stderr)
            validate_scaling(metrics, dimensions)
            before, after = metrics['retention/before'], metrics['retention/after']
            assert after['detach_list'] - before['detach_list'] == (1 if backend == 'ast' else 0)
            assert after['copied_slots'] - before['copied_slots'] == (2 if backend == 'ast' else 0)
            comparable = {label: value for label, value in logical_metrics(metrics).items()
                          if not label.startswith('retention/')}
            expected_metrics = {label: value for label, value in reference.items()
                                if not label.startswith('retention/')}
            assert comparable == expected_metrics, backend
            if native_reference is None:
                native_reference = metrics
            shared_metrics = {label: values for label, values in metrics.items()
                              if not label.startswith('retention/')}
            shared_reference = {label: values for label, values in native_reference.items()
                                if not label.startswith('retention/')}
            assert shared_metrics == shared_reference, backend
    print('  [PASS] B02 constructor retention cost: AST 1 detach/2 slots; MIR and interpreter 0')
    print('  [PASS] B02 independent N/M matrix sizes, three aliases and repeated writes on four engines')


if __name__ == '__main__':
    run_scaling_tests()
