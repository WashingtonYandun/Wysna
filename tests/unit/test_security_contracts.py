"""Contracts and worker traps remain enforced in each independent execution engine."""
import os
from pathlib import Path
import subprocess
import sys

import pytest

from compiler.CompilerService import CompilerService
from error.Error import WysnaError
from tests.engine_harness import ENGINES, compare, run_engine
from tests.unit.test_worker_safety import native_worker_compiler, native_check

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = [
    'tests/runtime/worker_value_isolation.wysna',
    'tests/regressions/struct_field_contract_scope.wysna',
    'tests/regressions/struct_field_contract_trap.wysna',
    'tests/regressions/worker_contract_main_trap.wysna',
    'tests/regressions/worker_peer_trap.wysna',
]


@pytest.mark.parametrize('engine', ENGINES)
@pytest.mark.parametrize('fixture', FIXTURES)
def test_security_runtime_contracts(engine, fixture, tmp_path, native_worker_compiler):
    path = ROOT / fixture
    result = run_engine(engine, path.read_text(), fixture, native_worker_compiler, tmp_path, run_timeout=5)
    valid, reason = compare([result], path.with_suffix('.expected').read_text())
    assert valid, reason


@pytest.mark.parametrize('fixture', FIXTURES[-2:])
def test_reference_fatal_worker_shutdown_is_bounded(fixture):
    result = subprocess.run([sys.executable, str(ROOT / 'main.py'), 'run', str(ROOT / fixture)],
        cwd=ROOT, capture_output=True, text=True, timeout=8)
    assert result.returncode == 1, result.stderr
    assert ('[W4005]' if 'main_trap' in fixture else '[W4001]') in result.stderr


@pytest.mark.parametrize('source', [
    'struct Invalid { value: int requires value + 1 }',
    'struct Invalid[T] { value: T requires 1 }',
])
def test_unused_field_predicates_are_checked(source, tmp_path, native_worker_compiler):
    path = tmp_path / 'invalid.wysna'
    path.write_text(source)
    with pytest.raises(WysnaError) as caught:
        CompilerService().check(source, file=str(path))
    assert caught.value.code == 'W2001'
    result, payload = native_check(native_worker_compiler, path)
    assert result.returncode == 1 and payload['diagnostics'][0]['code'] == 'W2001', payload


@pytest.mark.parametrize('engine', ENGINES)
def test_imported_field_contracts_have_declaration_scope_and_evaluate_once(engine, tmp_path, native_worker_compiler):
    module = tmp_path / 'data.wysna'
    module.write_text('let value = -100\nlet limit = 10\npub struct Item { value: int requires value > 0 requires value < limit }\n'
                      'pub struct Box[T] { items: list[T] requires len(items) > 0 }\n')
    source = f'''import "{module.as_posix()}" as data
fn next_value() -> int {{ print("evaluated") 3 }}
fn make() -> data.Item {{
    let limit = -1
    data.Item {{ value: next_value() }}
}}
let first = make()
let second = data.Item {{ ..first, value: 4 }}
let box = data.Box[int] {{ items: [7] }}
print(first.value, second.value, box.items[0])
'''
    result = run_engine(engine, source, 'imported-field-contract', native_worker_compiler, tmp_path)
    valid, reason = compare([result], 'evaluated\n3 4 7\n')
    assert valid, reason


@pytest.mark.parametrize('engine', ENGINES)
@pytest.mark.parametrize('source', [
    'fn f(x: int) -> int requires x > 0 { x }\nprint(f(-1))',
    'fn f(x: int) -> int requires x > 0,\n x < 10 { x }\nprint(f(11))',
    'fn f(x: int) -> int ensures result > 0 { return x }\nprint(f(-1))',
    'fn f(x: int) -> int ensures result > 0, result < 10 { x }\nprint(f(11))',
    'fn f(x: int) -> int ensures result > 0 { x }\nprint(f(-1))',
    'struct Box[T] { items: list[T] requires len(items) > 0 }\nlet b = Box[int] { items: [] }\nprint(len(b.items))',
    'struct Positive { value: int requires value > 0 }\nlet good = Positive { value: 1 }\nlet bad = Positive { ..good, value: -1 }\nprint(bad.value)',
])
def test_negative_contracts_trap_in_all_engines(engine, source, tmp_path, native_worker_compiler):
    result = run_engine(engine, source, 'negative-contract', native_worker_compiler, tmp_path)
    valid, reason = compare([result], '# RUNTIME_PANIC: W4005\n')
    assert valid, reason


@pytest.mark.parametrize('engine', ENGINES)
def test_intrinsic_identity_cannot_be_spoofed_by_a_module_filename(engine, tmp_path, native_worker_compiler):
    directory = tmp_path / 'fake_stdlib'
    directory.mkdir()
    module = directory / 'algorithms.wysna'
    module.write_text('pub fn parallel_map[T,R](xs:list[T], f:fn(T)->R)->list[R] { [] }\n')
    source = f'import "{module.as_posix()}" as fake\nprint(len(fake.parallel_map[int,int]([1], |x:int| => x)))\n'
    result = run_engine(engine, source, 'fake-intrinsic', native_worker_compiler, tmp_path)
    valid, reason = compare([result], '0\n')
    assert valid, reason


@pytest.mark.parametrize('engine', ENGINES)
def test_typed_worker_errors_are_values_and_do_not_abort_peers(engine, tmp_path, native_worker_compiler):
    source = '''import "std/algorithms" as alg
enum Fault { Rejected }
fn work(x: int) -> int ! Fault {
    if x < 0 { return Err(Fault.Rejected) }
    x + 1
}
let results = alg.parallel_map([1, -1], work)
let first = results[0] else err { -1 }
let second = results[1] else err { 42 }
print(first, second)
'''
    result = run_engine(engine, source, 'typed-worker-error', native_worker_compiler, tmp_path)
    valid, reason = compare([result], '2 42\n')
    assert valid, reason


@pytest.mark.parametrize('engine', ENGINES)
def test_inferred_lambda_return_and_struct_self_contract(engine, tmp_path, native_worker_compiler):
    source = '''struct Positive { value: int requires self.value > 0 }
let callback = || { return Positive { value: 1 } }
print(callback().value)
'''
    result = run_engine(engine, source, 'inferred-lambda-contract', native_worker_compiler, tmp_path)
    valid, reason = compare([result], '1\n')
    assert valid, reason


@pytest.mark.parametrize('engine', ENGINES)
@pytest.mark.parametrize('source,expected', [
    ('fn f() -> unit ensures false {}\nf()', '# RUNTIME_PANIC: W4005\n'),
    ('fn f() -> int ! str ensures result > 0 { Ok(-1) }\nprint(f() else error { 0 })', '# RUNTIME_PANIC: W4005\n'),
    ('fn f() -> int ! str ensures result > 0 { return Ok(-1) }\nprint(f() else error { 0 })', '# RUNTIME_PANIC: W4005\n'),
    ('fn f() -> int ! str ensures result > 0 { Err("typed") }\nprint(f() else error { 42 })', '42\n'),
    ('fn f() -> int ! str ensures result > 0 { return Err("typed") }\nprint(f() else error { 42 })', '42\n'),
    ('fn f() -> unit ! str ensures false { Err("typed") }\nf() else error { print(error) }', 'typed\n'),
    ('fn f() -> unit ! str ensures false {}\nf() else error { print(error) }', '# RUNTIME_PANIC: W4005\n'),
    ('fn next() -> int { print("once") 2 }\nfn f() -> int ensures result == 2 { next() }\nprint(f())', 'once\n2\n'),
])
def test_postconditions_only_check_successful_results(engine, source, expected, tmp_path, native_worker_compiler):
    result = run_engine(engine, source, 'postcondition', native_worker_compiler, tmp_path)
    valid, reason = compare([result], expected)
    assert valid, reason


@pytest.mark.parametrize('engine', ENGINES)
def test_field_requires_runs_on_construction_and_spread(engine, tmp_path, native_worker_compiler):
    source = """fn check(value: int) -> bool { print(value) value > 0 }
struct Checked { value: int requires check(value) }
let mut item = Checked { value: 1 }
item.value = -1
print("assigned", item.value)
let next = Checked { ..item, value: 2 }
print("built", next.value)
"""
    result = run_engine(engine, source, 'field-requires-construction', native_worker_compiler, tmp_path)
    valid, reason = compare([result], '1\nassigned -1\n2\nbuilt 2\n')
    assert valid, reason


@pytest.mark.parametrize('engine', ['seed', 'selfhost'])
def test_schema_reports_partial_contract_projection(engine, tmp_path, native_worker_compiler):
    import json

    path = tmp_path / 'transfer.wysna'
    path.write_text('pub fn withdraw(balance: int, amount: int) -> int\n'
                    '    requires amount > 0,\n'
                    '    balance >= amount\n'
                    '    ensures result == balance - amount\n'
                    '{ balance - amount }\n', encoding='utf-8')
    command = ([sys.executable, str(ROOT / 'main.py'), 'schema', '--engine=seed'] if engine == 'seed'
               else [str(native_worker_compiler), '--stdlib-root', str(ROOT / 'stdlib'), 'schema'])
    result = subprocess.run(command + [str(path)], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    tool = json.loads(result.stdout)['tools'][0]
    assert tool['contract_projection'] == 'representable_subset'
    properties = tool['input_schema']['properties']
    assert properties['amount']['exclusiveMinimum'] == 0
    assert 'minimum' not in properties['balance'] and 'exclusiveMinimum' not in properties['balance']
    assert [''.join(predicate.split()) for predicate in tool['requires']] == ['amount>0', 'balance>=amount']
    assert [''.join(predicate.split()) for predicate in tool['ensures']] == ['result==balance-amount']


@pytest.mark.parametrize('engine', ENGINES)
def test_postcondition_reads_parameters_and_globals_in_declaration_scope(engine, tmp_path, native_worker_compiler):
    source = """let limit = 5
fn f(input: int) -> int ensures result == input and result < limit {
    if true {
        let input = 2
        let limit = -1
        return 1
    }
    1
}
print(f(1))
"""
    result = run_engine(engine, source, 'postcondition-declaration-scope', native_worker_compiler, tmp_path)
    valid, reason = compare([result], '1\n')
    assert valid, reason


@pytest.mark.parametrize('engine', ENGINES)
def test_method_postcondition_traps_on_invalid_result(engine, tmp_path, native_worker_compiler):
    source = """class Box {
    fn init() {}
    pub fn value(input: int) -> int ensures result > input { input }
}
print(Box().value(1))
"""
    result = run_engine(engine, source, 'method-postcondition', native_worker_compiler, tmp_path)
    valid, reason = compare([result], '# RUNTIME_PANIC: W4005\n')
    assert valid, reason
