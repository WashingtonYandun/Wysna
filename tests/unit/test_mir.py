"""Independent MIR rejection oracles and allocation-free range behavior."""
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import os
import subprocess
import sys
import tempfile
from checker.TypeChecker import TypeChecker
from checker.TypeInterner import TYPE_BOOL, TYPE_INT
from error.Error import WysnaError
from hir.HirBuilder import HirBuilder
from hir.HirCCodeGen import HirCCodeGen
from mir.Lowering import Lowering
from mir.Nodes import Operation, Terminator
from mir.Verifier import Verifier
from parser.Parser import Parser
from main import ensure_selfhost_compiler_binary
from tests.engine_harness import verify

ROOT = Path(__file__).resolve().parents[2]


def lower(source, optimize=True):
    ast = Parser(file='mir-contract.wysna').parse(source)
    checker = TypeChecker(file='mir-contract.wysna')
    checker.check(ast)
    hir = HirBuilder(checker.interner, file_path='mir-contract.wysna').lower(ast)
    return Lowering(checker.interner, 'mir-contract.wysna').lower(hir, optimize=optimize), checker.interner


def operations(module):
    return [operation for function in module.functions for block in function.blocks for operation in block.operations]


def rejects(module, interner):
    try:
        Verifier(interner).verify(module)
    except WysnaError as error:
        assert error.code == 'W9999' and error.stage == 'mir-verification', error
    else:
        raise AssertionError('Malformed MIR was accepted')


def test_cfg_and_ownership_rejections():
    original, interner = lower('fn choose(flag: bool) -> str { if flag { return "yes" } else { return "no" } }\nprint(choose(true))')
    broken = deepcopy(original)
    broken.functions[0].blocks[0].terminator = None
    rejects(broken, interner)
    broken = deepcopy(original)
    broken.functions[0].blocks[0].terminator = Terminator('goto', (999,))
    rejects(broken, interner)
    broken = deepcopy(original)
    block = next(block for block in broken.functions[0].blocks if block.terminator.kind == 'return')
    block.operations = [operation for operation in block.operations if operation.opcode != 'release']
    rejects(broken, interner)
    broken = deepcopy(original)
    function = broken.functions[0]
    block = function.blocks[0]
    parameter = function.params[0]
    block.operations.insert(0, Operation('release', args=(parameter,)))
    rejects(broken, interner)
    broken = deepcopy(original)
    block = broken.functions[0].blocks[0]
    cloned = next(operation for operation in block.operations if operation.opcode == 'clone')
    index = block.operations.index(cloned)
    block.operations.insert(index + 1, Operation('release', args=(cloned.result,)))
    rejects(broken, interner)
    broken = deepcopy(original)
    call = next(operation for operation in operations(broken) if operation.opcode == 'call')
    call.data['name'] = 'wysna_missing'
    rejects(broken, interner)
    broken = deepcopy(original)
    function = broken.functions[0]
    parameter = function.params[0]
    function.slots[parameter] = replace(function.slots[parameter], storage='integer')
    rejects(broken, interner)
    broken = deepcopy(original)
    call = next(operation for operation in operations(broken) if operation.opcode == 'runtime')
    call.result = None
    rejects(broken, interner)


def test_physical_layout_and_runtime_rejections():
    original, interner = lower('struct Pair { a: int, b: int }\nlet p = Pair { b: 2, a: 1 }\nprint(p.a)')
    broken = deepcopy(original)
    field = next(operation for operation in operations(broken) if operation.opcode == 'load_field')
    field.data['index'] = 999
    rejects(broken, interner)
    broken = deepcopy(original)
    constant = next(operation for operation in operations(broken) if operation.opcode == 'const' and operation.data['value'] == 2)
    constant.data['value'] = 'wrong type'
    rejects(broken, interner)
    broken = deepcopy(original)
    call = next(operation for operation in operations(broken) if operation.opcode == 'runtime')
    call.data['name'] = 'system'
    rejects(broken, interner)
    broken = deepcopy(original)
    call = next(operation for operation in operations(broken) if operation.opcode == 'runtime')
    call.effect = 'pure'
    rejects(broken, interner)


def test_error_narrowing_requires_successful_type_test():
    source = '''struct A { value: int }
struct B { text: str }
fn fail() -> int ! A | B { return Err(A { value: 7 }) }
print(fail() else error { match error { A(detail) => detail.value, B(_) => 0 } })'''
    original, interner = lower(source)
    broken = deepcopy(original)
    operation = next(operation for operation in operations(broken) if operation.opcode == 'narrow')
    operation.data['witness'] = operation.args[0]
    rejects(broken, interner)
    broken = deepcopy(original)
    for function in broken.functions:
        for block in function.blocks:
            if block.terminator.kind == 'branch' and any(operation.opcode == 'test' and operation.data['kind'] == 'error_type' for operation in block.operations):
                block.terminator.targets = tuple(reversed(block.terminator.targets))
                rejects(broken, interner)
                return
    raise AssertionError('Missing narrowing branch in the test oracle')


def test_range_iteration_and_limits_across_engines():
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    with tempfile.TemporaryDirectory(prefix='wysna-mir-contracts-') as directory:
        for name in ('mir_range_iteration', 'mir_range_limit', 'mir_range_overflow'):
            path = ROOT / 'tests/runtime' / (name + '.wysna')
            ok, detail = verify(path.read_text(), str(path.relative_to(ROOT)), selfhost_bin, directory,
                                expected=path.with_suffix('.expected').read_text(), strict=True,
                                sanitize=os.environ.get('WYSNA_SANITIZE') == '1')
            assert ok, detail


def test_mir_inspection_and_default_backend():
    with tempfile.TemporaryDirectory(prefix='wysna-mir-cli-') as directory:
        path = Path(directory) / 'source.wysna'
        path.write_text('for index in 0..3 { print(index) }')
        result = subprocess.run([sys.executable, str(ROOT / 'main.py'), 'mir', str(path)],
                                capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
        import json
        data = json.loads(result.stdout)
        assert any(operation['opcode'] == 'range_count' for function in data['functions']
                   for block in function['blocks'] for operation in block['operations'])
        ast = Parser().parse(path.read_text())
        checker = TypeChecker()
        checker.check(ast)
        generator = HirCCodeGen(checker.interner)
        generator.generate(HirBuilder(checker.interner).lower(ast))
        assert generator.mir is not None
        assert all(block.terminator is not None for function in generator.mir.functions for block in function.blocks)


def test_bootstrap_cache_serializes_processes():
    with tempfile.TemporaryDirectory(prefix='wysna-bootstrap-lock-') as directory:
        (Path(directory) / 'selfhost').mkdir()
        (Path(directory) / 'selfhost' / 'compiler.wysna').write_text('print(0)\n')
        script = """import sys, time
from pathlib import Path
import compiler.Bootstrap as bootstrap
root = sys.argv[1]
def rebuild(path):
    marker = Path(path) / 'active'
    assert not marker.exists(), 'concurrent bootstrap rebuild'
    marker.write_text('active')
    time.sleep(0.15)
    marker.unlink()
    return 'ok'
bootstrap._ensure_selfhost_compiler_binary_locked = rebuild
print(bootstrap.ensure_selfhost_compiler_binary(root))
"""
        children = [subprocess.Popen([sys.executable, '-c', script, directory], cwd=ROOT,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for _ in range(2)]
        results = [(child, child.communicate(timeout=30)) for child in children]
        for child, (output, error) in results:
            assert child.returncode == 0 and output == 'ok\n', error


def test_result_payload_requires_a_current_channel_proof():
    source = '''fn value(flag: bool) -> str ! str {
        if flag { return "ok" }
        Err("bad")
    }
    fn read(flag: bool) -> str { value(flag) else error { error } }
    print(read(false))'''
    original, interner = lower(source, optimize=False)
    for mode in ('swap', 'redefine', 'stale'):
        broken = deepcopy(original)
        function = next(fn for fn in broken.functions if fn.name == 'wysna_fn_read')
        guard_block = next(block for block in function.blocks if any(op.opcode == 'test' for op in block.operations))
        guard = next(op for op in guard_block.operations if op.opcode == 'test')
        if mode == 'swap':
            term = guard_block.terminator
            term.targets = tuple(reversed(term.targets))
        elif mode == 'redefine':
            guard_block.operations.append(deepcopy(guard))
        else:
            arm = next(block for block in function.blocks if any(op.data.get('name') == 'wysna_unwrap_err' for op in block.operations))
            value = guard.args[0]
            copy = len(function.slots)
            function.slots.append(replace(function.slots[value], id=copy, name='proof-copy'))
            arm.operations[:0] = [Operation('clone', copy, (value,)), Operation('store', value, (copy,), moves=(0,))]
        rejects(broken, interner)


def run_mir_tests():
    test_cfg_and_ownership_rejections()
    test_physical_layout_and_runtime_rejections()
    test_error_narrowing_requires_successful_type_test()
    test_result_payload_requires_a_current_channel_proof()
    test_range_iteration_and_limits_across_engines()
    test_mir_inspection_and_default_backend()
    test_bootstrap_cache_serializes_processes()
    from tests.unit.test_mir_ownership import run_ownership_optimization_tests
    run_ownership_optimization_tests()
    print('MIR CFG, ownership, layout, narrowing and range contracts passed')
    return True


if __name__ == '__main__':
    run_mir_tests()
