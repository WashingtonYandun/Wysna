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


def test_json_to_int_and_error_cleanup_across_four_engines():
    source = '''import "std/json"

fn main() -> unit {
    let cases = [
        "0", "-0", "42", "-9223372036854775808", "9223372036854775807",
        "1.5", "1e3", "9223372036854775808", "-9223372036854775809",
        "01", "true", "invalid", " 42 "
    ]
    for raw in cases {
        let n = json.to_int(json.Value.Number(raw)) else err {
            print("ERR", raw, err.offset, err.message)
            continue
        }
        print("OK", raw, n)
    }
    let bad_docs = [
        "[1, 2, [3, invalid]]",
        "{\\"a\\": 1, \\"b\\": {\\"c\\": [true, \\"unterminated",
        "{\\"esc\\": \\"\\\\uD83D\\\\uDE00\\", \\"bad\\": \\"\\\\uD800\\"}",
        "[1, 2, 3]"
    ]
    for doc in bad_docs {
        let _v = json.parse(doc) else err {
            print("PARSE_ERR", err.offset, err.message)
            continue
        }
        print("PARSE_OK", doc)
    }
    let obj = json.parse("{\\"s\\":\\"hi\\",\\"n\\":123,\\"f\\":1.5,\\"b\\":true}") else err {
        print("OBJ_ERR", err.message)
        return
    }
    print(
        json.get_str(obj, "s") else _e { "?" },
        json.get_int(obj, "n") else _e { -1 },
        json.get_float(obj, "f") else _e { -1.0 },
        json.get_bool(obj, "b") else _e { false },
    )
}
main()
'''
    expected = (
        "OK 0 0\n"
        "OK -0 0\n"
        "OK 42 42\n"
        "OK -9223372036854775808 -9223372036854775808\n"
        "OK 9223372036854775807 9223372036854775807\n"
        "ERR 1.5 1 expected JSON integer, found JSON number with a fractional or exponent part\n"
        "ERR 1e3 1 expected JSON integer, found JSON number with a fractional or exponent part\n"
        "ERR 9223372036854775808 18 integer overflow\n"
        "ERR -9223372036854775809 19 integer overflow\n"
        "ERR 01 1 leading zero in number\n"
        "ERR true 0 number value must contain one JSON number\n"
        "ERR invalid 0 expected JSON value\n"
        "OK  42  42\n"
        "PARSE_ERR 11 expected JSON value\n"
        "PARSE_ERR 40 unterminated string\n"
        "PARSE_ERR 39 high surrogate must be followed by a low surrogate\n"
        "PARSE_OK [1, 2, 3]\n"
        "hi 123 1.5 true\n"
    )
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    with tempfile.TemporaryDirectory() as directory:
        results = [run_engine(engine, source, 'json-to-int-cleanup', selfhost_bin, directory,
                              sanitize=(os.environ.get('WYSNA_SANITIZE') == '1' or engine == 'hir'))
                   for engine in ('interpreter', 'ast', 'hir', 'selfhost')]
        ok, detail = compare(results, expected)
        assert ok, detail


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


def test_exclusive_file_creation_fails_when_destination_exists_across_four_engines():
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    sanitize = os.environ.get('WYSNA_SANITIZE') == '1'
    with tempfile.TemporaryDirectory(prefix='wysna exclusive é ') as temporary:
        directory = Path(temporary)
        for engine in ('interpreter', 'ast', 'hir', 'selfhost'):
            target = directory / f'{engine}-target.txt'
            missing = directory / 'missing-parent' / 'file.txt'
            source = '''import "std/buffer"
import "std/files"
fn main() -> unit ! files.FileError {
    let created = files.create_exclusive(TARGET_PATH)?
    let data = buffer.from_text("first")
    created.write(data)?
    data.close()
    print("CREATED", created.close())
    files.create_exclusive(TARGET_PATH) else error {
        print("EXISTS", error.operation, error.code > 0, error.domain > 0)
        return
    }
    print("UNEXPECTED-OVERWRITE")
}
fn check_missing() -> unit {
    files.open(MISSING_PATH, 3) else error {
        print("MISSING", error.operation, error.code > 0, error.domain > 0)
        return
    }
    print("UNEXPECTED-MISSING")
}
main() else error { print("ERR", error.operation) exit(1) }
check_missing()
'''.replace('TARGET_PATH', json.dumps(str(target), ensure_ascii=False)).replace('MISSING_PATH', json.dumps(str(missing), ensure_ascii=False))
            result = run_engine(engine, source, 'exclusive_create', selfhost_bin, directory / engine,
                                sanitize=sanitize)
            assert (result.exit_code, result.stdout, result.stderr) == (
                0,
                'CREATED 0\nEXISTS open true true\nMISSING open true true\n',
                '',
            ), f'{engine} failed: {result.exit_code}, {result.stdout!r}, {result.stderr!r}'
            assert target.read_text(encoding='utf-8') == 'first'


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
    assert(sizeof(WVal) == 8);
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
    test_exclusive_file_creation_fails_when_destination_exists_across_four_engines()
    test_host_handles_reject_stale_wrong_kind_and_oversized_requests()
    test_large_file_processing_keeps_buffer_bounded()
    test_standard_file_cleanup_runs_on_runtime_panic()
    test_resource_duplicate_owner_is_rejected_without_disposal()
    test_stdin_io_decodes_valid_empty_and_invalid_utf8_across_four_engines()
    test_str_methods_and_dict_remove_across_four_engines()
    test_directory_listing_file_kinds_and_path_ext_across_four_engines()
    test_process_capture_stdout_stderr_and_bounds_across_four_engines()
    test_csv_parse_records_and_stringify_across_four_engines()
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
let second = resources.Resource(handle, |value: opaque| -> int { audit_dispose(value) })
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


def test_stdin_io_decodes_valid_empty_and_invalid_utf8_across_four_engines():
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    source = '''import "std/io"
fn main() -> unit {
    let first = io.read_line() else error {
        print("LINE-ERR", error.message)
        return
    }
    print("LINE", first.eof, len(first.text), first.text)
    let rest = io.read_all() else error {
        print("ALL-ERR", error.message)
        return
    }
    print("ALL", len(rest), rest)
}
'''
    cases = (
        (b'', b'LINE true 0 \nALL 0 \n'),
        ('café\n🚀'.encode('utf-8'), 'LINE false 4 café\nALL 1 🚀\n'.encode('utf-8')),
        (b'\xff\n', b'LINE-ERR stdin is not valid UTF-8\n'),
        (b'ok\n\xff', 'LINE false 2 ok\nALL-ERR stdin is not valid UTF-8\n'.encode('utf-8')),
    )
    sanitize = os.environ.get('WYSNA_SANITIZE') == '1'
    extra_cc = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if (sanitize and os.name != 'nt') else []
    run_env = dict(
        os.environ,
        WYSNA_MEM_CHECK='1',
        ASAN_OPTIONS='detect_leaks=' + ('1' if sys.platform.startswith('linux') else '0'),
        UBSAN_OPTIONS='halt_on_error=1',
    )
    with tempfile.TemporaryDirectory(prefix='wysna-stdin-io-') as temporary:
        directory = Path(temporary)
        source_path = directory / 'stdin_io.wysna'
        source_path.write_text(source, encoding='utf-8')
        for engine in ('interpreter', 'ast', 'hir', 'selfhost'):
            if engine == 'interpreter':
                command = [sys.executable, str(ROOT / 'main.py'), 'run', str(source_path)]
            else:
                binary = directory / executable_name(engine)
                if engine == 'ast':
                    c_path = directory / 'ast.c'
                    emit = subprocess.run([sys.executable, str(ROOT / 'tests/emit_ast_oracle.py'),
                                           str(source_path), str(c_path)],
                                          capture_output=True, text=True, timeout=120)
                    assert emit.returncode == 0, emit.stderr
                    subprocess.run(compiler_command() + ['-std=c99', '-O2', '-I' + str(ROOT),
                                   str(c_path), '-o', str(binary)] + extra_cc,
                                   check=True, capture_output=True, timeout=120)
                else:
                    build_cmd = [sys.executable, str(ROOT / 'main.py'), 'build']
                    if engine == 'selfhost':
                        build_cmd.append('--engine=compiler')
                    elif extra_cc:
                        build_cmd.append('--sanitize')
                    build_cmd += [str(source_path), '-o', str(binary)]
                    built = subprocess.run(build_cmd, capture_output=True, text=True, timeout=120)
                    assert built.returncode == 0, built.stderr
                    if engine == 'selfhost' and extra_cc:
                        c_path = directory / 'selfhost.c'
                        emit = subprocess.run([selfhost_bin, '--stdlib-root', str(ROOT / 'stdlib'),
                                               str(source_path), str(c_path)],
                                              capture_output=True, text=True, timeout=120)
                        assert emit.returncode == 0, emit.stderr
                        subprocess.run(compiler_command() + ['-std=c99', '-O2', '-I' + str(ROOT),
                                       str(c_path), '-o', str(binary)] + extra_cc,
                                       check=True, capture_output=True, timeout=120)
                command = [str(binary)]
            for payload, expected in cases:
                result = subprocess.run(command, input=payload, capture_output=True, timeout=30, env=run_env)
                assert (result.returncode, result.stdout.replace(b'\r\n', b'\n'), result.stderr) == (0, expected, b''), (
                    engine, payload, result.returncode, result.stdout, result.stderr
                )


def test_str_methods_and_dict_remove_across_four_engines():
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    source = '''fn main() -> unit {
    let text = "  Wysna Lang  ".trim()
    print(text, text.split(" "), text.to_upper(), text.to_lower())
    print(text.starts_with("Wy"), text.ends_with("ng"), text.replace("Lang", "v0.8"))
    let base = {"a": 1, "b": 2}
    let mut alias = base
    let removed = alias.remove("a")
    let missing = alias.remove("missing")
    let mut cleared = base
    cleared.clear()
    print(base["a"], len(alias), len(cleared))
    match removed {
        Some(v) => print("removed", v),
        None => print("removed-none"),
    }
    match missing {
        Some(v) => print("missing-some", v),
        None => print("missing-none"),
    }
}
'''
    expected = 'Wysna Lang ["Wysna", "Lang"] WYSNA LANG wysna lang\ntrue true Wysna v0.8\n1 1 0\nremoved 1\nmissing-none\n'
    with tempfile.TemporaryDirectory() as directory:
        results = [
            run_engine(engine, source, 'str-dict-methods', selfhost_bin, directory,
                       sanitize=os.environ.get('WYSNA_SANITIZE') == '1')
            for engine in ('interpreter', 'ast', 'hir', 'selfhost')
        ]
        ok, detail = compare(results, expected)
        assert ok, detail


def test_directory_listing_file_kinds_and_path_ext_across_four_engines():
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    with tempfile.TemporaryDirectory() as directory:
        root_dir = Path(directory) / 'workspace'
        root_dir.mkdir()
        (root_dir / 'z.txt').write_text('z', encoding='utf-8')
        (root_dir / 'a.json').write_text('{}', encoding='utf-8')
        (root_dir / '.env').write_text('k=v', encoding='utf-8')
        (root_dir / 'sub.dir').mkdir()
        source = f'''import "std/fs"
import "std/path"
fn main() -> unit {{
    print(path.ext("report.json"), path.ext("a.tar.gz"), path.ext(".env"), path.ext("sub.dir/noext"))
    let is_d = fs.is_dir({json.dumps(str(root_dir))}) else error {{ exit(2) return }}
    let is_f = fs.is_file({json.dumps(str(root_dir / 'a.json'))}) else error {{ exit(2) return }}
    let d_as_f = fs.is_file({json.dumps(str(root_dir))}) else error {{ exit(2) return }}
    let f_as_d = fs.is_dir({json.dumps(str(root_dir / 'a.json'))}) else error {{ exit(2) return }}
    let missing = fs.is_dir({json.dumps(str(root_dir / 'missing'))}) else error {{ exit(2) return }}
    print(is_d, is_f, d_as_f, f_as_d, missing)
    let names = fs.list_dir({json.dumps(str(root_dir))}) else error {{ exit(2) return }}
    print(names)
    match fs.list_dir({json.dumps(str(root_dir / 'a.json'))}) {{
        Ok(items) => print("UNEXPECTED", len(items)),
        Err(err) => print("LIST-ERR", err.operation),
    }}
}}
'''
        expected = '.json .gz  \ntrue true false false false\n[".env", "a.json", "sub.dir", "z.txt"]\nLIST-ERR list_dir\n'
        results = [
            run_engine(engine, source, 'fs-path-ext', selfhost_bin, directory,
                       sanitize=os.environ.get('WYSNA_SANITIZE') == '1')
            for engine in ('interpreter', 'ast', 'hir', 'selfhost')
        ]
        ok, detail = compare(results, expected)
        assert ok, detail


def test_process_capture_stdout_stderr_and_bounds_across_four_engines():
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    source = f'''import "std/process"
fn main() -> unit {{
    let out = process.capture({json.dumps(sys.executable)}, ["-c", "import sys; print(123); sys.stderr.write(str(456)); sys.exit(7)"], 5000, 4096) else error {{
        exit(2)
        return
    }}
    print(out.status, out.stdout.trim(), out.stderr.trim())
    match process.capture({json.dumps(sys.executable)}, ["-c", "print(str(9) * 128)"], 5000, 32) {{
        Ok(_) => print("UNEXPECTED-OVERFLOW"),
        Err(err) => print("OVERFLOW", err.operation),
    }}
    match process.capture({json.dumps(sys.executable)}, ["-c", "import time; time.sleep(0.5)"], 60, 4096) {{
        Ok(_) => print("UNEXPECTED-TIMEOUT"),
        Err(err) => print("TIMEOUT", err.operation),
    }}
}}
'''
    expected = '7 123 456\nOVERFLOW capture\nTIMEOUT capture\n'
    with tempfile.TemporaryDirectory() as directory:
        results = [
            run_engine(engine, source, 'process-capture', selfhost_bin, directory,
                       sanitize=os.environ.get('WYSNA_SANITIZE') == '1')
            for engine in ('interpreter', 'ast', 'hir', 'selfhost')
        ]
        ok, detail = compare(results, expected)
        assert ok, detail


def test_csv_parse_records_and_stringify_across_four_engines():
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    source = '''import "std/csv"
fn main() -> unit {
    let raw = "name,role,note\\r\\n\\"Ada, L.\\",\\"Chief \\"\\"AI\\"\\" Lead\\",\\"line1\\nline2\\"\\nWasho,Eng,ok\\n"
    let recs = csv.parse_records(raw) else error { exit(2) return }
    print(len(recs), recs[0]["name"], recs[0]["role"], recs[1]["name"])
    let ser = csv.stringify([["a,b", "x\\"y"], ["plain", "1"]]) else error { exit(2) return }
    print(ser)
    match csv.parse("\\"unterminated") {
        Ok(_) => print("UNEXPECTED"),
        Err(err) => print("CSV-ERR", err.line, err.column),
    }
}
'''
    expected = '2 Ada, L. Chief "AI" Lead Washo\n"a,b","x""y"\nplain,1\nCSV-ERR 1 1\n'
    with tempfile.TemporaryDirectory() as directory:
        results = [
            run_engine(engine, source, 'csv-codec', selfhost_bin, directory,
                       sanitize=os.environ.get('WYSNA_SANITIZE') == '1')
            for engine in ('interpreter', 'ast', 'hir', 'selfhost')
        ]
        ok, detail = compare(results, expected)
        assert ok, detail


def test_json_extractors_quoting_and_key_cache_across_four_engines():
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    source = '''import "std/json"
fn main() -> unit {
    let payload = "[{\\"categoryName\\":\\"alpha\\",\\"long_field_name\\":true,\\"float_metric\\":-12.5e1,\\"score\\":42,\\"short\\":\\"ok\\"},{\\"categoryName\\":\\"beta\\",\\"long_field_name\\":false,\\"float_metric\\":0.25,\\"score\\":-7,\\"short\\":\\"\\\\n\\"}]"
    let rows = json.parse(payload) else err { print("PARSE-FAIL", err.message) return }
    let items = json.to_array(rows) else err { print("ARR-FAIL", err.message) return }
    for item in items {
        let s = json.get_str(item, "categoryName") else _e { "err" }
        let b = json.get_bool(item, "long_field_name") else _e { false }
        let f = json.get_float(item, "float_metric") else _e { -999.0 }
        let i = json.get_int(item, "score") else _e { -999 }
        let sh = json.get_str(item, "short") else _e { "err" }
        print(s, b, f, i, len(sh))
    }
    let tricky = json.object({
        "long_key_exceeding_sixty_four_bytes_for_cache_fallback_verification_12345": json.from_int(9),
        "bad_int": json.Value.Number("01"),
        "bad_float": json.Value.Number(".5"),
        "overflow_float": json.Value.Number("1e500"),
        "wrong_bool": json.from_str("true"),
    })
    json.get_int(tricky, "bad_int") else err { print("BAD-INT", err.offset, err.message) 0 }
    json.get_float(tricky, "bad_float") else err { print("BAD-FLOAT", err.offset, err.message) 0.0 }
    let ov = json.get_float(tricky, "overflow_float") else err {
        print("OVR-ERR", err.message)
        -1.0
    }
    print("OVR-FLOAT", ov > 1000000.0)
    json.get_bool(tricky, "wrong_bool") else err { print("BAD-BOOL", err.offset, err.message) false }
    json.get_str(tricky, "missing_k") else err { print("MISS-STR", err.offset, err.message) "" }
    json.get_int(json.from_int(1), "k") else err { print("NOT-OBJ", err.offset, err.message) 0 }
    let mut over_1mib_digits = "1"
    for _step in 0..21 {
        over_1mib_digits = over_1mib_digits + over_1mib_digits
    }
    let big_obj = json.object({"huge": json.Value.Number(over_1mib_digits)})
    json.get_int(big_obj, "huge") else err { print("BIG-INT", err.message) 0 }
    json.get_float(big_obj, "huge") else err { print("BIG-FLOAT", err.message) 0.0 }
    let ctrl = "" + chr(0) + chr(8) + chr(9) + chr(10) + chr(12) + chr(13) + "\\"" + "\\\\" + "café"
    let quoted = json.stringify(json.from_str(ctrl)) else err { print("STR-FAIL", err.message) "" }
    print(quoted)
    let roundtrip = json.stringify(json.object({"plain": json.from_str(""), "x": json.from_str("hi")})) else err { "" }
    print(roundtrip)
}
main()
'''
    expected = (
        "alpha true -125.0 42 2\n"
        "beta false 0.25 -7 1\n"
        "BAD-INT 1 bad_int: leading zero in number\n"
        "BAD-FLOAT 0 bad_float: expected JSON value\n"
        "OVR-FLOAT true\n"
        "BAD-BOOL 0 wrong_bool: expected JSON boolean, found JSON string\n"
        "MISS-STR 0 missing_k: missing required JSON field\n"
        "NOT-OBJ 0 k: expected JSON object, found JSON number\n"
        "BIG-INT huge: maximum JSON input byte limit exceeded\n"
        "BIG-FLOAT huge: maximum JSON input byte limit exceeded\n"
        "\"\\u0000\\b\\t\\n\\f\\r\\\"\\\\café\"\n"
        "{\"plain\":\"\",\"x\":\"hi\"}\n"
    )
    with tempfile.TemporaryDirectory() as directory:
        results = [
            run_engine(engine, source, 'json-opt2-matrix', selfhost_bin, directory,
                       sanitize=(os.environ.get('WYSNA_SANITIZE') == '1' or engine == 'hir'))
            for engine in ('interpreter', 'ast', 'hir', 'selfhost')
        ]
        ok, detail = compare(results, expected)
        assert ok, detail


def test_algorithms_sort_sizes_edge_cases_and_stability_across_four_engines():
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    source = '''import "std/algorithms" as alg
struct Pair { key: int, seq: int }
fn is_sorted(xs: list[int]) -> bool {
    let mut i = 1
    while i < len(xs) {
        if xs[i] < xs[i - 1] { return false }
        i = i + 1
    }
    return true
}
fn main() -> unit {
    for n in [0, 1, 2, 15, 16, 17, 64, 100] {
        let mut rev: list[int] = []
        let mut fwd: list[int] = []
        let mut same: list[int] = []
        let mut k = 0
        while k < n {
            rev.append(n - 1 - k)
            fwd.append(k)
            same.append(7)
            k = k + 1
        }
        let s_rev = alg.sort[int](rev, |a: int, b: int| { a < b })
        let s_fwd = alg.sort[int](fwd, |a: int, b: int| { a < b })
        let s_same = alg.sort[int](same, |a: int, b: int| { a < b })
        if len(s_rev) != n or not is_sorted(s_rev) or not is_sorted(s_fwd) or not is_sorted(s_same) {
            print("FAIL-SIZE", n)
            return
        }
        if n > 0 and (s_rev[0] != 0 or s_rev[n - 1] != n - 1) {
            print("FAIL-ENDS", n)
            return
        }
    }
    let mut items: list[Pair] = []
    for idx in 0..33 {
        items.append(Pair { key: 5 - (idx % 6), seq: idx })
    }
    let ordered = alg.sort[Pair](items, |left: Pair, right: Pair| { left.key < right.key })
    let mut stable = len(ordered) == 33
    for i in 1..len(ordered) {
        let prev = ordered[i - 1]
        let cur = ordered[i]
        if cur.key < prev.key or (cur.key == prev.key and cur.seq <= prev.seq) {
            stable = false
        }
    }
    print("SORT-OK", stable, ordered[0].key, ordered[0].seq, ordered[32].key, ordered[32].seq)
}
main()
'''
    expected = 'SORT-OK true 0 5 5 30\n'
    with tempfile.TemporaryDirectory() as directory:
        results = [
            run_engine(engine, source, 'algorithms-sort-matrix', selfhost_bin, directory,
                       sanitize=os.environ.get('WYSNA_SANITIZE') == '1')
            for engine in ('interpreter', 'ast', 'hir', 'selfhost')
        ]
        ok, detail = compare(results, expected)
        assert ok, detail


if __name__ == '__main__':
    run_toolkit_tests()
