"""Worker entry checks reject unsafe graphs and transitive effects before execution."""
import json
import os
from pathlib import Path
import subprocess

import pytest

from compiler.CompilerService import CompilerService
from error.Error import TypeError

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def native_worker_compiler():
    configured = os.environ.get('WYSNA_TEST_SELFHOST_BIN')
    if configured:
        return configured
    from compiler.Bootstrap import ensure_selfhost_compiler_binary
    return ensure_selfhost_compiler_binary(str(ROOT))


def native_check(compiler, path):
    result = subprocess.run([str(compiler), '--stdlib-root', str(ROOT / 'stdlib'), '--check', str(path),
        '--output-format=json-v1'], cwd=ROOT, capture_output=True, text=True, timeout=60)
    return result, json.loads(result.stdout)


PREFIX = 'import "std/algorithms" as alg\n'


SAFE_CASES = [
    'fn work(x: int) -> int { x + 1 }\nprint(alg.parallel_map([1, 2], work))',
    'let offset = 7\nprint(alg.parallel_map([1, 2], |x: int| => x + offset))',
    'let offset = [7]\nlet work = |x: int| => x + offset[0]\nprint(alg.parallel_map([1, 2], work))',
    'fn inner(x: int) -> int { x * 2 }\nfn outer(x: int) -> int { inner(x) }\nprint(alg.parallel_map([1], outer))',
    'struct Input { value: int requires value > 0 }\nfn work(x: int) -> int { Input { value: x }.value }\nalg.parallel_map([1], work)',
    'fn work(x: int) -> int? { Some(x) }\nprint(alg.parallel_map([1], work))',
]


@pytest.mark.parametrize('source', SAFE_CASES)
def test_known_pure_worker_callbacks_are_accepted(source, tmp_path):
    CompilerService().check(PREFIX + source, file=str(tmp_path / 'main.wysna'))


UNSAFE_CASES = [
    ('fn work(x: int) -> int { match x { 1 => { print(x) x }, _ => x } }\nalg.parallel_map([1], work)', 'W2015'),
    ('fn guard(x: int) -> bool { print(x) true }\nfn work(x: int) -> int requires guard(x) { x }\nalg.parallel_map([1], work)', 'W2015'),
    ('fn guard(x: int) -> bool { print(x) true }\nstruct Checked { value: int requires guard(value) }\nfn work(x: int) -> int { Checked { value: x }.value }\nalg.parallel_map([1], work)', 'W2015'),
    ('let limit = 3\nstruct Checked { value: int requires value < limit }\nalg.parallel_map([1], |x: int| { let captured = limit; Checked { value: x + captured - captured }.value })', 'W2015'),
    ('import "std/async" as tasks\nfn work(x: int) -> int? { tasks._take_task_value[int](x, None) }\nalg.parallel_map([1], work)', 'W2015'),
    ('fn work(x: int) -> int { alg.parallel_map([x], |y: int| => y)[0] }\nalg.parallel_map([1], work)', 'W2015'),
    ('let dispatch: fn(list[int], fn(int) -> int) -> list[int] = alg.parallel_map\ndispatch([1], |x: int| { print(x) x })', 'W2015'),
    ('class Counter { fn init() {} }\nfn work(x: int) -> int { let counter = Counter(); x }\nalg.parallel_map([1], work)', 'W2014'),
    ('let value = 7\nfn work(x: int) -> int { x + value }\nalg.parallel_map([1], work)', 'W2015'),
    ('fn effect(x: int) -> int { print(x) x }\nfn work(x: int) -> int { effect(x) }\nalg.parallel_map([1], work)', 'W2015'),
    ('extern "C" fn foreign(value: int) -> int\nfn work(x: int) -> int { foreign(x) }\nalg.parallel_map([1], work)', 'W2015'),
    ('class Counter { fn init() {} }\nlet counter = Counter()\nalg.parallel_map([1], |x: int| => x + len(str(counter)))', 'W2014'),
    ('class Counter { fn init() {} }\nstruct Data { counter: Counter }\nfn work(data: Data) -> int { 0 }\nalg.parallel_map([Data { counter: Counter() }], work)', 'W2014'),
    ('fn work(x: int) -> int { x }\nlet mut callback: fn(int) -> int = work\nalg.parallel_map([1], callback)', 'W2015'),
    ('fn work(callback: fn(int) -> int) -> list[int] { alg.parallel_map([1], callback) }', 'W2015'),
    ('fn effect(x: int) -> int { print(x) x }\nlet values: list[int] = []\nalg.parallel_map(values, effect)', 'W2015'),
]


@pytest.mark.parametrize('source,code', UNSAFE_CASES)
def test_unsafe_worker_boundaries_have_structured_diagnostics(source, code, tmp_path):
    with pytest.raises(TypeError) as caught:
        CompilerService().check(PREFIX + source, file=str(tmp_path / 'main.wysna'))
    assert caught.value.code == code
    assert caught.value.data['kind'] in ('worker_transfer', 'worker_effect')
    assert caught.value.line > 0


def test_helper_body_edits_recheck_effects_despite_unchanged_interface(tmp_path):
    helper = tmp_path / 'helper.wysna'
    helper.write_text('pub fn work(x: int) -> int { x + 1 }\n')
    source = PREFIX + 'import "helper.wysna" as helper\nprint(alg.parallel_map([1], helper.work))\n'
    main = tmp_path / 'main.wysna'
    service = CompilerService(project_root=tmp_path)
    service.check(source, file=main)
    helper.write_text('pub fn work(x: int) -> int { print(x) x + 1 }\n')
    with pytest.raises(TypeError) as caught:
        service.check(source, file=main)
    assert caught.value.code == 'W2015'


@pytest.mark.parametrize('source', SAFE_CASES)
def test_native_accepts_the_same_closed_callbacks(source, tmp_path, native_worker_compiler):
    path = tmp_path / 'main.wysna'
    path.write_text(PREFIX + source)
    result, payload = native_check(native_worker_compiler, path)
    assert result.returncode == 0, payload


@pytest.mark.parametrize('source,code', UNSAFE_CASES)
def test_native_rejects_the_same_unsafe_boundaries(source, code, tmp_path, native_worker_compiler):
    path = tmp_path / 'main.wysna'
    path.write_text(PREFIX + source)
    result, payload = native_check(native_worker_compiler, path)
    assert result.returncode == 1, payload
    diagnostic = payload['diagnostics'][0]
    assert diagnostic['code'] == code, payload
    assert diagnostic['data']['kind'] in ('worker_transfer', 'worker_effect'), payload
    assert diagnostic['data']['boundary_line'] > 0, payload
