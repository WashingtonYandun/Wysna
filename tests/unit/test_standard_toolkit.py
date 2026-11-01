"""Public standard toolkit, bounded binary resources and cross-engine semantics."""
import json
import os
import random
from pathlib import Path
import subprocess
import sys
import tempfile
from decimal import Decimal

from main import ensure_selfhost_compiler_binary
from common.Platform import compiler_command, executable_name
from tests.engine_harness import run_engine, compare
from runtime import HostAbi

ROOT = Path(__file__).resolve().parents[2]


def _reject_json_constant(value):
    raise ValueError(f'non-standard JSON constant: {value}')


def _last_json_member_wins(pairs):
    result = {}
    for key, value in pairs:
        if _contains_surrogate(key) or _contains_surrogate(value):
            raise ValueError('unpaired surrogate is outside the Wysna JSON string contract')
        result[key] = value
    return result


def _contains_surrogate(value):
    if isinstance(value, str):
        return any(0xD800 <= ord(character) <= 0xDFFF for character in value)
    if isinstance(value, list):
        return any(_contains_surrogate(item) for item in value)
    if isinstance(value, dict):
        return any(_contains_surrogate(key) or _contains_surrogate(item)
                   for key, item in value.items())
    return False


def _json_reference_load(source):
    value = json.loads(source, parse_int=Decimal, parse_float=Decimal,
                       parse_constant=_reject_json_constant,
                       object_pairs_hook=_last_json_member_wins)
    if _contains_surrogate(value):
        raise ValueError('unpaired surrogate is outside the Wysna JSON string contract')
    return value


def _json_depth_and_node_count(value):
    if isinstance(value, list):
        child_metrics = [_json_depth_and_node_count(item) for item in value]
        nested_depths = [depth for depth, _ in child_metrics]
        nested_nodes = [count for _, count in child_metrics]
        depth = 1 + max(nested_depths, default=0)
        nodes = 1 + sum(nested_nodes)
        return depth, nodes
    if isinstance(value, dict):
        child_metrics = [_json_depth_and_node_count(item) for item in value.values()]
        nested_depths = [depth for depth, _ in child_metrics]
        nested_nodes = [count for _, count in child_metrics]
        depth = 1 + max(nested_depths, default=0)
        nodes = 1 + sum(nested_nodes)
        return depth, nodes
    return 0, 1


def _json_reference_accepts(source):
    try:
        value = _json_reference_load(source)
    except (ValueError, RecursionError):
        return False, None
    depth, nodes = _json_depth_and_node_count(value)
    within_limits = (len(source.encode('utf-8')) <= 1048576
                     and depth <= 32
                     and nodes <= 100000)
    return within_limits, value if within_limits else None


def _typed_json_equal(left, right):
    if type(left) is not type(right):
        return False
    if isinstance(left, list):
        return (len(left) == len(right)
                and all(_typed_json_equal(a, b) for a, b in zip(left, right)))
    if isinstance(left, dict):
        return (left.keys() == right.keys()
                and all(_typed_json_equal(left[key], right[key]) for key in left))
    return left == right


def _wysna_string_literal(value):
    parts = ['"']
    for character in value:
        codepoint = ord(character)
        if character == '"':
            parts.append('\\"')
        elif character == '\\':
            parts.append('\\\\')
        elif character == '\n':
            parts.append('\\n')
        elif character == '\r':
            parts.append('\\r')
        elif character == '\t':
            parts.append('\\t')
        elif character == '\0':
            parts.append('\\0')
        elif codepoint < 32:
            parts.append('\\u{' + format(codepoint, 'x') + '}')
        else:
            parts.append(character)
    parts.append('"')
    return ''.join(parts)


def _random_json_value(generator, depth=0):
    scalar_values = [None, True, False, 0, -17, 2**63 + 5, -0.0, 1.25e-7,
                     'plain', 'façade', '😀', 'quote"slash\\', 'line\nnext', '\u0001']
    if depth >= 3:
        return generator.choice(scalar_values)
    kind = generator.randrange(5)
    if kind == 0:
        return generator.choice(scalar_values)
    if kind == 1:
        item_count = generator.randrange(4)
        return [_random_json_value(generator, depth + 1) for _ in range(item_count)]
    if kind == 2:
        field_count = generator.randrange(4)
        fields = {}
        for index in range(field_count):
            key = generator.choice(['name', 'façade', '😀', f'key-{index}'])
            fields[key] = _random_json_value(generator, depth + 1)
        return fields
    return generator.choice(scalar_values)


def test_toolkit_semantics_across_four_engines():
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    source = (ROOT / 'tests/stdlib/toolkit.wysna').read_text(encoding='utf-8')
    expected = (ROOT / 'tests/stdlib/toolkit.expected').read_text(encoding='utf-8')
    with tempfile.TemporaryDirectory() as directory:
        results = [run_engine(engine, source, 'toolkit', selfhost_bin, directory,
                   sanitize=os.environ.get('WYSNA_SANITIZE') == '1') for engine in ('interpreter', 'ast', 'hir', 'selfhost')]
        ok, detail = compare(results, expected)
        assert ok, detail


def test_json_parser_and_serializer_across_four_engines():
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    source = (ROOT / 'tests/stdlib/json.wysna').read_text(encoding='utf-8')
    expected = (ROOT / 'tests/stdlib/json.expected').read_text(encoding='utf-8')
    with tempfile.TemporaryDirectory() as directory:
        results = [run_engine(engine, source, 'stdlib-json', selfhost_bin, directory,
                   sanitize=os.environ.get('WYSNA_SANITIZE') == '1')
                   for engine in ('interpreter', 'ast', 'hir', 'selfhost')]
        ok, detail = compare(results, expected)
        assert ok, detail


def test_json_differential_against_python_across_four_engines():
    generator = random.Random(0x5759534E)
    valid_values = [_random_json_value(generator) for _ in range(40)]
    cases = [
        'null', 'true', 'false', '0', '-0', '-0.0', '1e+2', '-2.50E-1',
        '[]', '{}', '[0,true,null,"x"]',
        '{"b":1,"a":[2.0,{"ok":false}]}',
        '{"same":1,"same":2}',
        '"quotes \\" and slash \\\\"',
        '"line\\nnext\\ttab"', '"façade 😀"',
        '"\\uD83D\\uDE00"',
        '"\\uD800"',
        '', ' ', '[1,]', '{"a":}', '[,1]',
        '01', '-01', '-', '+1', '.5', '1.', '1e', '1e+', '--1',
        'NaN', 'Infinity', '"\\x"', '"line\nbreak"', '"control\x01"',
        '[' * 33 + 'null' + ']' * 33,
    ]
    cases.extend(json.dumps(value, ensure_ascii=False, separators=(',', ':'))
                 for value in valid_values)

    source_lines = [
        'import "std/json"',
        'fn inspect_case(index: int, source: str) -> unit {',
        '    let value = json.parse(source) else error {',
        '        print(index, "ERR")',
        '        return',
        '    }',
        '    let encoded = json.stringify(value) else error {',
        '        print(index, "STRINGIFY_ERR")',
        '        return',
        '    }',
        '    print(index, "OK", encoded)',
        '}',
        'fn main() -> unit {',
    ]
    for index, case in enumerate(cases):
        source_lines.append(f'    inspect_case({index}, {_wysna_string_literal(case)})')
    source_lines.extend(['}', 'main()', ''])
    source = '\n'.join(source_lines)
    expected_values = [_json_reference_accepts(case) for case in cases]
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))

    with tempfile.TemporaryDirectory(prefix='wysna-json-differential-') as temporary:
        for engine in ('interpreter', 'ast', 'hir', 'selfhost'):
            result = run_engine(engine, source, 'json-differential', selfhost_bin,
                                Path(temporary) / engine,
                                sanitize=os.environ.get('WYSNA_SANITIZE') == '1')
            assert (result.exit_code, result.stderr) == (0, ''), (
                f'{engine} failed: exit={result.exit_code}, stderr={result.stderr!r}'
            )
            output_lines = result.stdout.splitlines()
            assert len(output_lines) == len(cases), (
                f'{engine} emitted {len(output_lines)} rows for {len(cases)} cases: '
                f'{result.stdout!r}'
            )
            for index, (line, expected) in enumerate(zip(output_lines, expected_values)):
                fields = line.split(' ', 2)
                assert len(fields) >= 2 and int(fields[0]) == index, (
                    f'{engine} produced an invalid row for case {index}: {line!r}'
                )
                if not expected[0]:
                    assert fields[1] == 'ERR', (
                        f'{engine} accepted JSON rejected by the reference/policy: '
                        f'{cases[index]!r} -> {line!r}'
                    )
                    continue
                assert len(fields) == 3 and fields[1] == 'OK', (
                    f'{engine} rejected valid JSON case {index}: {cases[index]!r} -> {line!r}'
                )
                actual_value = _json_reference_load(fields[2])
                assert _typed_json_equal(actual_value, expected[1]), (
                    f'{engine} decoded JSON differently for case {index}: '
                    f'{cases[index]!r} -> {fields[2]!r}'
                )


def test_streaming_files_and_filesystem_across_four_engines():
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    with tempfile.TemporaryDirectory(prefix='w toolkit é ') as temporary:
        directory = Path(temporary)
        path = directory / 'data é.bin'
        moved = directory / 'renamed é.bin'
        text = '''import "std/buffer"
import "std/files"
import "std/fs"
import "std/path"
fn main() -> unit ! files.FileError | fs.FsError | buffer.BufferError {
    let name = PATH
    let data = buffer.from_text("a\\0é\\r\\n")
    let output = files.open(name, 1)?
    output.write(data)?
    data.close()
    let c1 = output.close()
    let c2 = output.close()
    let sz = fs.size(name)?
    print(c1, c2, sz)
    let input = files.open(name, 0)?
    let mut total = 0
    while true {
        let chunk = input.read(2)?
        let count = chunk.size()
        total = total + count
        chunk.close()
        if count == 0 { break }
    }
    print(total, input.close())
    input.read(2) else error { print(error.operation, error.code > 0, error.domain > 0) return }
    return
}
main()
fs.rename(PATH, MOVED) else error { exit(1) }
print(fs.exists(PATH) else error { false }, fs.exists(MOVED) else error { false })
fs.remove(MOVED) else error { exit(1) }
print(path.join("root", "child"), path.basename("C:/é/file.wysna"), path.dirname("C:/file.wysna"))
'''.replace('PATH', json.dumps(str(path), ensure_ascii=False)).replace('MOVED', json.dumps(str(moved), ensure_ascii=False))
        expected = '0 0 6\n6 0\nread true true\nfalse true\nroot/child file.wysna C:/\n'
        for engine in ('interpreter', 'ast', 'hir', 'selfhost'):
            result = run_engine(engine, text, 'streaming', selfhost_bin, directory / engine,
                                sanitize=os.environ.get('WYSNA_SANITIZE') == '1')
            assert (result.exit_code, result.stdout, result.stderr) == (0, expected, ''), (
                f"Engine {engine} failed: exit={result.exit_code}, stdout={repr(result.stdout)}, stderr={repr(result.stderr)}"
            )
            assert not path.exists() and not moved.exists()


def test_filesystem_missing_size_replaces_previous_error_across_four_engines():
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    with tempfile.TemporaryDirectory(prefix='wysna missing path ') as temporary:
        directory = Path(temporary)
        missing = directory / 'missing.txt'
        source = '''import "std/fs"
fs.size("") else error { print(error.operation, error.code, error.domain) 0 }
fs.size(PATH) else error { print(error.operation, error.code, error.domain) 0 }
'''.replace('PATH', json.dumps(str(missing), ensure_ascii=False))
        results = []
        for engine in ('interpreter', 'ast', 'hir', 'selfhost'):
            result = run_engine(engine, source, 'missing_size', selfhost_bin, directory / engine)
            assert result.exit_code == 0 and result.stderr == '', (
                f'{engine} failed: exit={result.exit_code}, stderr={result.stderr!r}'
            )
            lines = result.stdout.splitlines()
            assert len(lines) == 2, f'{engine} produced unexpected output: {result.stdout!r}'
            first = lines[0].split()
            second = lines[1].split()
            assert first[0] == second[0] == 'size'
            assert int(first[1]) > 0 and int(first[2]) > 0
            assert int(second[1]) > 0 and int(second[2]) > 0
            assert second[1:] != first[1:], f'{engine} retained the previous error'
            results.append((engine, lines))
        assert all(lines == results[0][1] for _, lines in results), results


def test_filesystem_rename_replaces_existing_file_across_four_engines():
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    with tempfile.TemporaryDirectory(prefix='wysna rename ') as temporary:
        directory = Path(temporary)
        for engine in ('interpreter', 'ast', 'hir', 'selfhost'):
            source_path = directory / f'{engine}-source.txt'
            destination_path = directory / f'{engine}-destination.txt'
            target_directory = directory / f'{engine}-directory'
            source_path.write_text('new contents', encoding='utf-8')
            destination_path.write_text('old contents', encoding='utf-8')
            target_directory.mkdir()
            source = '''import "std/fs"
fs.rename(SOURCE, DESTINATION) else error { print("unexpected", error.operation) exit(1) }
fs.rename(SOURCE, DESTINATION) else error { print(error.operation, error.code > 0, error.domain > 0) }
fs.rename(DESTINATION, DIRECTORY) else error { print(error.operation, error.code > 0, error.domain > 0) }
'''.replace('SOURCE', json.dumps(str(source_path), ensure_ascii=False))
            source = source.replace('DESTINATION', json.dumps(str(destination_path), ensure_ascii=False))
            source = source.replace('DIRECTORY', json.dumps(str(target_directory), ensure_ascii=False))
            result = run_engine(engine, source, 'rename_replace', selfhost_bin, directory / engine)
            assert (result.exit_code, result.stdout, result.stderr) == (
                0, 'rename true true\nrename true true\n', ''
            ), f'{engine} failed: {result.exit_code}, {result.stdout!r}, {result.stderr!r}'
            assert not source_path.exists()
            assert destination_path.read_text(encoding='utf-8') == 'new contents'
            assert target_directory.is_dir()


def test_host_handles_reject_stale_wrong_kind_and_oversized_requests():
    buffer = HostAbi.FUNCTIONS['wysna_host_buffer_new']()
    assert HostAbi.buffer_append(buffer, 256) == -1
    assert HostAbi.file_read(buffer, buffer, 1) == -1
    assert HostAbi.file_read(0, buffer, 16777217) == -1
    assert HostAbi.close(buffer) == 0
    fresh = HostAbi.FUNCTIONS['wysna_host_buffer_new']()
    assert fresh != buffer
    assert HostAbi.buffer_append(buffer, 1) == -1
    assert HostAbi.buffer_size(fresh) == 0
    HostAbi.close(fresh)
    code = '''#include "runtime/wysna_runtime.h"
#include <assert.h>
int main(void) {
    void* buffer = wysna_host_buffer_new();
    assert(wysna_host_buffer_append(buffer, 256) == -1);
    assert(wysna_host_file_read(buffer, buffer, 1) == -1);
    assert(wysna_host_file_read(NULL, buffer, 16777217) == -1);
    assert(wysna_host_close(buffer) == 0);
    void* fresh = wysna_host_buffer_new();
    assert(fresh != buffer && wysna_host_buffer_append(buffer, 1) == -1);
    assert(wysna_host_buffer_size(fresh) == 0);
    assert(wysna_host_close(fresh) == 0 && wysna_host_entries == NULL);
    assert(sizeof(WVal) == 32);
    return 0;
}
'''
    with tempfile.TemporaryDirectory() as temporary:
        source = Path(temporary) / 'handles.c'
        binary = Path(temporary) / executable_name('handles')
        source.write_text(code, encoding='utf-8')
        subprocess.run(compiler_command() + ['-std=c99'] + ([] if os.name == 'nt' else ['-fsanitize=address,undefined']) + ['-I' + str(ROOT),
                       str(source), '-o', str(binary)], check=True, capture_output=True, timeout=120)
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and not result.stderr, result


def test_large_file_processing_keeps_buffer_bounded():
    # The input is 2 MiB; both implementations reuse a 4 KiB compact buffer.
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        data_path = directory / 'input.bin'
        data_path.write_bytes(bytes(range(256)) * 8192)
        handle = HostAbi.allocate('buffer', bytearray(str(data_path).encode()))
        file = HostAbi.file_open(handle, 0)
        buffer = HostAbi.FUNCTIONS['wysna_host_buffer_new']()
        total = 0
        while True:
            count = HostAbi.file_read(file, buffer, 4096)
            assert 0 <= count <= 4096
            assert len(HostAbi.find(buffer, 'buffer')) <= 4096
            total += count
            if not count:
                break
        assert total == data_path.stat().st_size
        for value in (file, buffer, handle):
            HostAbi.close(value)
        source = directory / 'bounded.c'
        binary = directory / executable_name('bounded')
        source.write_text('''#include "runtime/wysna_runtime.h"
#include <assert.h>
int main(int argc, char** argv) {
    (void)argc;
    void* path = wysna_host_buffer_new();
    for (const unsigned char* p = (const unsigned char*)argv[1]; *p; p++) assert(wysna_host_buffer_append(path, *p) == 0);
    void* file = wysna_host_file_open(path, 0);
    void* buffer = wysna_host_buffer_new();
    int64_t total = 0, count;
    do {
        count = wysna_host_file_read(file, buffer, 4096);
        assert(count >= 0 && count <= 4096);
        assert(wysna_host_find(buffer, 1)->capacity == 4096);
        total += count;
    } while (count);
    assert(total == 2097152);
    wysna_host_close(file); wysna_host_close(buffer); wysna_host_close(path);
    assert(wysna_host_entries == NULL);
    return 0;
}
''', encoding='utf-8')
        subprocess.run(compiler_command() + ['-std=c99', '-O2', '-I' + str(ROOT), str(source), '-o', str(binary)],
                       check=True, capture_output=True, timeout=120)
        subprocess.run([str(binary), str(data_path)], check=True, capture_output=True, timeout=30)


def run_toolkit_tests():
    test_toolkit_semantics_across_four_engines()
    test_json_parser_and_serializer_across_four_engines()
    test_json_differential_against_python_across_four_engines()
    test_streaming_files_and_filesystem_across_four_engines()
    test_filesystem_missing_size_replaces_previous_error_across_four_engines()
    test_filesystem_rename_replaces_existing_file_across_four_engines()
    test_host_handles_reject_stale_wrong_kind_and_oversized_requests()
    test_large_file_processing_keeps_buffer_bounded()
    test_standard_file_cleanup_runs_on_runtime_panic()
    test_resource_duplicate_owner_is_rejected_without_disposal()
    return True




def test_standard_file_cleanup_runs_on_runtime_panic():
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        for backend in ('interpreter', 'ast', 'hir', 'selfhost'):
            output = directory / (backend + '.bin')
            source = directory / (backend + '.wysna')
            source.write_text('import "std/buffer"\nimport "std/files"\nfn main() {\n'
                f'let file = files.open({json.dumps(str(output))}, 1) else error {{ exit(2) return }}\n'
                'let data = buffer.from_text("flushed")\n'
                'file.write(data) else error { exit(2) }\nprint(1 / 0)\n}\n', encoding='utf-8')
            if backend == 'interpreter':
                command = [sys.executable, str(ROOT / 'main.py'), 'run', str(source)]
            else:
                binary = directory / executable_name(backend)
                if backend == 'ast':
                    build_command = [sys.executable, str(ROOT / 'tests/emit_ast_oracle.py'),
                                     str(source), str(directory / 'oracle.c'), '--build', str(binary)]
                else:
                    build_command = [sys.executable, str(ROOT / 'main.py'), 'build']
                    if backend == 'selfhost':
                        build_command.append('--engine=compiler')
                    build_command += [str(source), '-o', str(binary)]
                built = subprocess.run(build_command, capture_output=True, text=True, timeout=120)
                assert built.returncode == 0, built.stderr
                command = [str(binary)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30)
            assert result.returncode == 1 and 'W4001' in result.stderr, result
            assert output.read_bytes() == b'flushed'


def test_resource_duplicate_owner_is_rejected_without_disposal():
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        source = directory / 'owners.wysna'
        source.write_text('''import "std/resources"
extern "C" fn audit_open() -> opaque
extern "C" fn audit_dispose(handle: opaque) -> int32
extern "C" fn audit_live() -> int32
fn main() {
let handle = audit_open()
let first = resources.acquire(handle, |value: opaque| -> int { audit_dispose(value) }) else error { exit(2) return }
let second = new resources.Resource(handle, |value: opaque| -> int { audit_dispose(value) })
second.borrow() else error { print(error.message) }
print(second.close(), audit_live(), first.close(), audit_live(), first.close())
}
'''.replace('second.borrow() else error { print(error.message) }',
            'let rejected = resources.acquire(handle, |value: opaque| -> int { audit_dispose(value) }) else error { print(error.message)\nfirst }'), encoding='utf-8')
        adapter = ROOT / 'tests/ffi/resource_adapter.c'
        shared = directory / ('adapter.dll' if os.name == 'nt' else 'adapter.dylib' if sys.platform == 'darwin' else 'adapter.so')
        subprocess.run(compiler_command() + ['-std=c99', '-dynamiclib' if sys.platform == 'darwin' else '-shared',
                       str(adapter), '-o', str(shared)], check=True, capture_output=True, timeout=120)
        expected = 'resource already owned or released\n-1 1 0 0 0\n'
        for backend in ('interpreter', 'ast', 'hir', 'selfhost'):
            if backend == 'interpreter':
                command = [sys.executable, str(ROOT / 'main.py'), 'run', str(source), '--link-lib', str(shared)]
            else:
                binary = directory / executable_name(backend)
                if backend == 'ast':
                    build_command = [sys.executable, str(ROOT / 'tests/emit_ast_oracle.py'),
                                     str(source), str(directory / 'owners-oracle.c'), '--build', str(binary),
                                     '--link-lib', str(adapter)]
                else:
                    build_command = [sys.executable, str(ROOT / 'main.py'), 'build']
                    if backend == 'selfhost':
                        build_command.append('--engine=compiler')
                    build_command += [str(source), '--link-lib', str(adapter), '-o', str(binary)]
                built = subprocess.run(build_command, capture_output=True, text=True, timeout=120)
                assert built.returncode == 0, built.stderr
                command = [str(binary)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=30, env=dict(os.environ, WYSNA_MEM_CHECK='1'))
            assert (result.returncode, result.stdout, result.stderr) == (0, expected, ''), result


if __name__ == '__main__':
    run_toolkit_tests()
