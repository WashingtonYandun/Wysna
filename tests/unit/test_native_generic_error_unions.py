"""Concrete generic error alternatives must keep identity and ownership in native Wysna."""

from pathlib import Path
import subprocess
import os

import pytest

from compiler.Bootstrap import ensure_selfhost_compiler_binary
from compiler.CompilerService import CompilerService
from common.Platform import executable_name
from error.Error import WysnaError
from tests.engine_harness import ENGINES, run_engine

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / 'tests/fixtures/generic_error_union_members.wysna'
EXPECTED = ('VALUE 7\nBOX_INT 404\nBOX_STR note\nBOX_LIST 2 b\nENUM_INT 12\n'
            'ENUM_STR tag\nCLASS ref\nEMPTY_STR\nEMPTY_INT\nRECOVERED 32\n')


@pytest.fixture(scope='module')
def native_compiler():
    return ensure_selfhost_compiler_binary(str(ROOT))


@pytest.mark.parametrize('engine', ENGINES)
def test_concrete_generic_error_payloads_and_propagation(engine, native_compiler, tmp_path):
    result = run_engine(engine, FIXTURE.read_text(), 'generic_error_unions', native_compiler,
                        tmp_path / engine)
    assert result.exit_code == 0, result.stderr
    assert not result.infrastructure_error, result.infrastructure_error
    assert result.stdout == EXPECTED


@pytest.mark.parametrize('source, code', [
    ('''struct Failure[T] { detail: T }
fn fail() -> int ! Failure[int] | Failure[str] { Err(Failure[int] { detail: 1 }) }
fn recover() -> int { fail() else error { match error { Failure[int](value) => value.detail } } }
''', 'W2003'),
    ('''struct Failure[T] { detail: T }
fn fail() -> int ! Failure[int] | str { Err(Failure[int] { detail: 1 }) }
fn recover() -> int { fail() else error { match error { Failure[bool](_) => 1, _ => 0 } } }
''', 'W2001'),
    ('''struct Failure[T] { detail: T }
fn fail() -> int ! Failure[str] { Err(Failure[str] { detail: "bad" }) }
fn recover() -> int ! Failure[int] | str { fail()? }
''', 'W2006'),
    ('''struct Failure[T] { detail: T }
fn fail[T](value: T) -> int ! Failure[T] | Failure[int] { Err(Failure[T] { detail: value }) }
fn main() -> unit { let recovered = fail(7) else error { 0 } print(recovered) }
''', 'W2001'),
])
def test_generic_error_union_rejections_agree_with_seed(source, code, native_compiler, tmp_path):
    with pytest.raises(WysnaError) as rejected:
        CompilerService(project_root=ROOT).check(source, file='generic-invalid.wysna')
    assert rejected.value.code == code
    path = tmp_path / 'invalid.wysna'
    path.write_text(source)
    result = subprocess.run([native_compiler, '--stdlib-root', str(ROOT / 'stdlib'), '--check', str(path)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode != 0
    assert f'[{code}]' in result.stderr
    assert '[W9999]' not in result.stderr


@pytest.mark.skipif(os.name == "nt", reason="ASan/UBSan coverage is provided by the POSIX sanitizer gates")
def test_managed_generic_error_payloads_release_with_sanitizers(native_compiler, tmp_path):
    source = FIXTURE.read_text().replace('for mode in 0..9', 'for iteration in 0..20 {\n    for mode in 0..9')
    source = source.replace('    let recovered = wrapped(32)', '    }\n    let recovered = wrapped(32)')
    result = run_engine('selfhost', source, 'generic_error_ownership', native_compiler,
                        tmp_path, sanitize=True)
    assert result.exit_code == 0, result.stderr
    assert not result.infrastructure_error, result.infrastructure_error
    assert result.stdout == EXPECTED.rsplit('RECOVERED', 1)[0] * 20 + 'RECOVERED 32\n'


def test_imported_generic_error_type_patterns(native_compiler, tmp_path):
    library = tmp_path / 'errors.wysna'
    library.write_text('''pub struct Failure[T] { detail: T }
pub fn fail(flag: bool) -> str ! Failure[int] | Failure[str] {
    if flag { return Err(Failure[int] { detail: 42 }) }
    return Err(Failure[str] { detail: "invalid" })
}
''')
    source = tmp_path / 'main.wysna'
    source.write_text('''import "errors.wysna" as errors
for flag in [true, false] {
    let recovered = errors.fail(flag) else issue {
        match issue {
            errors.Failure[int](error) => str(error.detail),
            errors.Failure[str](error) => error.detail,
        }
    }
    print(recovered)
}
''')
    c_file = tmp_path / 'program.c'
    process = subprocess.run([native_compiler, '--stdlib-root', str(ROOT / 'stdlib'), str(source), str(c_file)],
                             capture_output=True, text=True, timeout=60)
    assert process.returncode == 0, process.stderr
    from compiler.NativeToolchain import NativeToolchain
    binary = tmp_path / executable_name('program')
    NativeToolchain(ROOT).build(c_file.read_text(), binary)
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == '42\ninvalid\n'
