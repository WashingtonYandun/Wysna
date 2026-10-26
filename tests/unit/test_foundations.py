"""Executable public contracts and bounded standard-resource lifetime gates."""
import os
from pathlib import Path
import re
import tempfile

from main import ensure_selfhost_compiler_binary
from tests.engine_harness import verify, run_engine

ROOT = Path(__file__).resolve().parents[2]


def test_readme_examples_execute_in_all_engines():
    text = (ROOT / 'README.md').read_text(encoding='utf-8')
    blocks = re.findall(r'```wysna\n(.*?)```', text, re.S)
    assert blocks, 'The public quick start needs an executable Wysna example'
    tutorial = sorted((ROOT / 'examples/language').glob('*.wysna'))
    assert len(tutorial) == 5, 'Preserve the five original language-tour regressions'
    examples = [(f'readme_{index}.wysna', source) for index, source in enumerate(blocks)]
    examples.extend((path.name, path.read_text(encoding='utf-8')) for path in tutorial)
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    with tempfile.TemporaryDirectory(prefix='wysna-doc-contract-') as temporary:
        for name, source in examples:
            ok, detail = verify(source, name, selfhost_bin, temporary, strict=True,
                                sanitize=os.environ.get('WYSNA_SANITIZE') == '1')
            assert ok, detail


def test_standard_resources_are_bounded_in_a_persistent_process():
    source = '''import "std/buffer"
import "std/files"
extern "C" fn wysna_host_active_handles() -> int
extern "C" fn wysna_host_active_bytes() -> int
extern "C" fn wysna_host_buffer_size(handle: opaque) -> int
fn request() -> unit ! files.FileError {
    let data = new buffer.Bytes()
    data.append(255) else error { exit(21) return }
    let alias = data
    let copied = buffer.clone(alias) else error { exit(21) return }
    let file = files.open("README.md", 0)?
    let chunk = file.read(32)?
    if chunk.size() != 32 { exit(22) }
    file.read(0) else error {
        if error.code <= 0 or error.domain <= 0 { exit(23) }
        return
    }
    exit(30)
}
fn closed_alias() {
    let first = buffer.from_text("alias")
    let second = first
    let borrowed = first.borrow()
    first.close()
    if second.size() != -1 { exit(24) }
    if second.close() != 0 { exit(25) }
}
fn escaped() -> buffer.Bytes { return buffer.from_text("retained") }
fn borrowed() -> opaque {
    let value = escaped()
    return value.borrow()
}
fn containers_and_closures() {
    let values = [escaped()]
    let callback = || -> int { values[0].size() }
    if callback() != 8 { exit(28) }
}
fn missing_file() {
    let missing = files.open("wysna-missing-resource-contract/file", 0) else error {
        if error.code <= 0 or error.domain <= 0 { exit(23) }
        return
    }
    exit(31)
}
let baseline = wysna_host_active_handles()
missing_file()
containers_and_closures()
if wysna_host_buffer_size(borrowed()) != -1 { exit(29) }
for count in [10000, 100000] {
    for index in 0..count {
        request() else error { exit(26) }
        if wysna_host_active_handles() != baseline or wysna_host_active_bytes() != 0 { exit(27) }
    }
    print(count, wysna_host_active_handles() - baseline, wysna_host_active_bytes())
}
closed_alias()
print(wysna_host_active_handles() - baseline)
'''
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    with tempfile.TemporaryDirectory(prefix='wysna-persistent-resource-') as temporary:
        ok, detail = verify(source, 'persistent_resources.wysna', selfhost_bin, temporary, strict=True,
                            expected='10000 0 0\n100000 0 0\n0\n', run_timeout=300,
                            sanitize=os.environ.get('WYSNA_SANITIZE') == '1')
        assert ok, detail


def test_fallible_unit_tail_parity():
    source = ROOT / 'tests/runtime/fallible_unit_tails.wysna'
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    with tempfile.TemporaryDirectory(prefix='wysna-unit-result-') as temporary:
        ok, detail = verify(source.read_text(), str(source), selfhost_bin, temporary, strict=True,
                            expected=source.with_suffix('.expected').read_text(),
                            sanitize=os.environ.get('WYSNA_SANITIZE') == '1')
        assert ok, detail


def test_unit_tail_rejects_incompatible_error_channels():
    sources = ['fn failure() -> int ! int { Err(7) }\nfn action() -> unit ! str { failure() }',
               'fn action() -> unit ! str { Err(42) }',
               'fn action() -> unit ! str { let problem = Err(42)\nproblem }',
               'fn failure() -> int ! int { Err(7) }\nfn action() -> unit ! str { if true { failure() } else { 42 } }']
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    with tempfile.TemporaryDirectory(prefix='wysna-unit-error-channel-') as temporary:
        for index, source in enumerate(sources):
            for engine in ('interpreter', 'ast', 'hir', 'selfhost'):
                result = run_engine(engine, source, 'unit_error_channel.wysna', selfhost_bin, Path(temporary) / str(index) / engine)
                assert result.exit_code == 1 and result.diag_code == 'W2004', vars(result)
                assert not result.infrastructure_error, vars(result)


def test_flush_failure_is_recoverable():
    if not Path('/dev/full').exists():
        return
    source = '''import "std/buffer"
import "std/files"
fn write_failure() -> unit ! files.FileError {
    let file = files.open("/dev/full", 1)?
    let bytes = buffer.from_text("write must fail")
    file.write(bytes)?
}
write_failure() else error { print(error.operation == "write", error.code > 0, error.domain > 0) }
'''
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    with tempfile.TemporaryDirectory(prefix='wysna-flush-failure-') as temporary:
        ok, detail = verify(source, 'flush_failure.wysna', selfhost_bin, temporary, strict=True,
                            expected='true true true\n', sanitize=os.environ.get('WYSNA_SANITIZE') == '1')
        assert ok, detail


def run_foundations_tests():
    test_fallible_unit_tail_parity()
    test_unit_tail_rejects_incompatible_error_channels()
    test_flush_failure_is_recoverable()
    test_readme_examples_execute_in_all_engines()
    test_standard_resources_are_bounded_in_a_persistent_process()
    return True


if __name__ == '__main__':
    run_foundations_tests()
