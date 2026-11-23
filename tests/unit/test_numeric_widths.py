"""Checked byte bindings and compact byte lists in the supported engines."""

import os
from pathlib import Path
import subprocess

import pytest

from common.Platform import compiler_command
from tests.engine_harness import ENGINES, ROOT, run_engine
from tests.unit.test_worker_safety import native_worker_compiler


@pytest.mark.parametrize('engine', ENGINES)
def test_bytes_keep_order_and_isolate_aliases(engine, tmp_path, native_worker_compiler):
    source = """
fn checked(value: int) -> u8 {
    match u8(value) {
        Some(byte) => byte,
        None => 0,
    }
}
let mut values: list[u8] = [1, 255]
let alias = values
values[0] = 9
values.append(checked(12))
print(alias)
print(values)
print(slice(values, 1, 3))
print(int(values.pop()))
print(values)
print(u8(-1) == None)
print(u8(256) == None)
"""
    result = run_engine(engine, source, 'byte-ownership', native_worker_compiler, tmp_path)
    assert result.runtime_ok, result.stderr
    assert result.stdout == '[1, 255]\n[9, 255, 12]\n[255, 12]\n12\n[9, 255]\ntrue\ntrue\n'


@pytest.mark.parametrize('engine', ENGINES)
@pytest.mark.parametrize('source,code', [
    ('let limit: u8 = 256', 'W0002'),
    ('let input = 1\nlet limit: u8 = input', 'W2001'),
])
def test_narrowing_never_happens_implicitly(engine, source, code, tmp_path, native_worker_compiler):
    result = run_engine(engine, source, 'byte-rejection', native_worker_compiler, tmp_path)
    assert not result.compile_ok
    assert result.diag_code == code, result.stderr


def test_million_u8_payload_is_one_megabyte(tmp_path: Path):
    executable = tmp_path / ('packed.exe' if os.name == 'nt' else 'packed')
    flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if os.environ.get('WYSNA_SANITIZE') == '1' else []
    compiled = subprocess.run(
        [*compiler_command(), '-std=c99', '-O1', *flags, '-I' + str(ROOT),
         str(ROOT / 'tests/runtime/packed_u8.c'), '-o', str(executable)],
        capture_output=True, text=True, timeout=120,
    )
    assert compiled.returncode == 0, compiled.stderr
    result = subprocess.run([str(executable)], env=dict(os.environ, WYSNA_MEM_CHECK='1'),
                            capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert result.stdout == '1000000 8000000\n'


@pytest.mark.parametrize('producer', ('seed', 'selfhost'))
def test_byte_schema_codecs_validate_external_values(producer, tmp_path, native_worker_compiler):
    import json
    import sys

    declarations = 'pub struct Sample { scalar: u8, bytes: list[u8], optional: u8? }\n'
    source = tmp_path / 'types.wysna'
    source.write_text(declarations, encoding='utf-8')
    command = ([sys.executable, str(ROOT / 'main.py')] if producer == 'seed'
               else [str(native_worker_compiler)])
    schema = subprocess.run([*command, 'schema', '--output-format=json-v1', '--emit-codecs', str(source)],
                            cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert schema.returncode == 0, schema.stderr
    payload = json.loads(schema.stdout)['result']
    properties = payload['$defs']['Sample']['properties']
    bounds = {'type': 'integer', 'minimum': 0, 'maximum': 255}
    assert properties['scalar'] == bounds
    assert properties['bytes']['items'] == bounds
    program = declarations + payload['codecs_source'] + '''
fn accepted() -> unit ! json.JsonError {
    let decoded = from_json_Sample("{\\"scalar\\":255,\\"bytes\\":[0,42],\\"optional\\":17}")?
    print(decoded.scalar, decoded.bytes, decoded.optional)
}
let _ = accepted() else err { print(err.message) }
fn rejected() {
    let _ = from_json_Sample("{\\"scalar\\":256,\\"bytes\\":[],\\"optional\\":null}") else err {
        print(err.message)
        return
    }
}
rejected()
'''
    engine = 'hir' if producer == 'seed' else 'selfhost'
    result = run_engine(engine, program, 'byte-json-schema', native_worker_compiler, tmp_path)
    assert result.runtime_ok, result.stderr
    assert result.stdout == '255 [0, 42] Some(17)\nu8 out of range\n'


@pytest.mark.parametrize('engine', ENGINES)
def test_c_abi_uses_unsigned_bytes(engine, tmp_path, native_worker_compiler):
    import sys

    adapter = tmp_path / 'adapter.c'
    adapter.write_text('#include <stdint.h>\nuint8_t byte_identity(uint8_t value) { return value; }\n',
                       encoding='utf-8')
    program = 'extern "C" fn byte_identity(value: u8) -> u8\nprint(int(byte_identity(255)))\n'
    if engine == 'interpreter':
        suffix = '.dll' if os.name == 'nt' else '.dylib' if sys.platform == 'darwin' else '.so'
        library = tmp_path / ('adapter' + suffix)
        target = '-dynamiclib' if sys.platform == 'darwin' else '-shared'
        compiled = subprocess.run([*compiler_command(), target, str(adapter), '-o', str(library)],
                                  capture_output=True, text=True, timeout=60)
        assert compiled.returncode == 0, compiled.stderr
        source = tmp_path / 'source.wysna'
        source.write_text(program, encoding='utf-8')
        result = subprocess.run([sys.executable, str(ROOT / 'main.py'), 'run', str(source),
                                 '--link-lib', str(library)], capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, result.stderr
    else:
        result = run_engine(engine, program, 'byte-c-abi', native_worker_compiler, tmp_path,
                            extra_link_flags=[str(adapter)])
        assert result.runtime_ok, result.stderr
    assert result.stdout == '255\n'
