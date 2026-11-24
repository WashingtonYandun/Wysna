"""Cross-feature programs must agree on generic error identity and cleanup."""

import os
from pathlib import Path
import subprocess

import pytest

from compiler.Bootstrap import ensure_selfhost_compiler_binary
from compiler.CompilerService import CompilerService
from error.Error import WysnaError
from tests.engine_harness import ENGINES, run_engine

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / 'tests/fixtures/language_combinations'
CASES = [('reexports/main.wysna', 'REEXPORT 10\n'),
         ('generic_methods.wysna', 'METHOD 42 7 2\n'),
         ('async_errors.wysna', 'ASYNC_OK 7\nASYNC_ERROR 2 second\n')]


@pytest.fixture(scope='module')
def native_compiler():
    return ensure_selfhost_compiler_binary(str(ROOT))


def program_source(name):
    path = FIXTURES / name
    source = path.read_text()
    return source.replace('"facade.wysna"', '"' + (path.parent / 'facade.wysna').as_posix() + '"')


@pytest.mark.parametrize('engine', ENGINES)
@pytest.mark.parametrize('name, expected', CASES)
def test_generic_error_combinations_across_engines(engine, name, expected, native_compiler, tmp_path):
    result = run_engine(engine, program_source(name), name, native_compiler, tmp_path)
    assert result.exit_code == 0, result.stderr
    assert not result.infrastructure_error, result.infrastructure_error
    assert result.stdout == expected


@pytest.mark.skipif(os.name == 'nt', reason='POSIX gates provide ASan/UBSan coverage')
@pytest.mark.parametrize('name, expected', CASES[1:] + [('borrowed_paths.wysna', 'borrowed paths released\n')])
def test_managed_payloads_and_borrowed_paths_with_sanitizers(name, expected, native_compiler, tmp_path):
    result = run_engine('selfhost', program_source(name), name, native_compiler, tmp_path, sanitize=True)
    assert result.exit_code == 0, result.stderr
    assert not result.infrastructure_error, result.infrastructure_error
    assert result.stdout == expected


@pytest.mark.parametrize('name, source, code', [
    ('async', '''struct Failure[T] { detail: T }
async fn fail() -> int ! Failure[str] { Err(Failure[str] { detail: "bad" }) }
async fn wrong() -> int ! Failure[int] | str { Ok(await fail()?) }
fn main() -> unit { let value = await wrong() else error { 0 } print(value) }
''', 'W2006'),
    ('reexport-exhaustiveness', '''import "facade.wysna" as api
fn wrong() -> int {
    api.errors.fail() else issue { match issue { api.errors.Failure[int](error) => error.detail } }
}
''', 'W2003'),
    ('reexport-membership', '''import "facade.wysna" as api
fn wrong() -> int {
    api.errors.fail() else issue { match issue { api.errors.Failure[bool](_) => 0, _ => 1 } }
}
''', 'W2001'),
    ('generic-method', '''struct Failure[T] { detail: T }
class Factory[T] {
    pub value: T
    fn init(value: T) { this.value = value }
    pub fn reject[E](payload: E) -> T ! Failure[E] | str { Err(Failure[E] { detail: payload }) }
}
fn wrong() -> int ! Failure[str] | str {
    let factory = Factory[int](0)
    factory.reject(10)?
}
''', 'W2006'),
])
def test_incompatible_generic_errors_are_rejected(name, source, code, native_compiler, tmp_path):
    source = source.replace('"facade.wysna"', '"' + (FIXTURES / 'reexports/facade.wysna').as_posix() + '"')
    with pytest.raises(WysnaError) as rejected:
        CompilerService(project_root=ROOT).check(source, file=name + '-invalid.wysna')
    assert rejected.value.code == code
    path = tmp_path / (name + '-invalid.wysna')
    path.write_text(source)
    result = subprocess.run([native_compiler, '--stdlib-root', str(ROOT / 'stdlib'), '--check', str(path)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert f'[{code}]' in result.stderr
    assert '[W9999]' not in result.stderr
