"""Native adapter execution, phase failures and ownership of temporary artifacts."""
from dataclasses import FrozenInstanceError
import os
from pathlib import Path
import subprocess
import tempfile
from unittest import TestCase
from unittest.mock import patch

from compiler.NativeToolchain import NativeCompilationError, NativeToolchain
from error.Error import ToolchainError


ROOT = Path(__file__).resolve().parents[2]
expect_error = TestCase().assertRaises


def test_native_build_runs_explicit_c_adapter():
    with tempfile.TemporaryDirectory(prefix='wysna toolchain test ') as temporary:
        directory = Path(temporary)
        adapter = directory / 'user adapter.c'
        adapter.write_text('#include <stdint.h>\nint64_t twice(int64_t value) { return value * 2; }\n',
                           encoding='utf-8')
        source = '''#include <stdint.h>
#include <inttypes.h>
#include <stdio.h>
extern int64_t twice(int64_t value);
int main(void) { printf("%" PRId64 "\\n", twice(21)); return 0; }
'''
        result = NativeToolchain(ROOT).build(source, directory / 'program', link_libraries=(adapter,))
        execution = subprocess.run([result.output_path], capture_output=True, text=True, timeout=30)
        assert execution.returncode == 0, execution.stderr
        assert execution.stdout == '42\n'
        assert result.c_compile_ms > 0 and result.link_ms > 0
        assert adapter.is_file()
        with expect_error(FrozenInstanceError):
            result.output_path = 'changed'


def test_http_native_build_resolves_and_links_libcurl_only_on_demand():
    commands = []
    compiled_sources = []
    curl_outputs = {
        '--checkfor': subprocess.CompletedProcess([], 0, stdout='', stderr=''),
        '--cflags': subprocess.CompletedProcess([], 0, stdout='-I/curl/include', stderr=''),
        '--libs': subprocess.CompletedProcess([], 0, stdout='-L/curl/lib -lcurl', stderr=''),
    }

    def run(command, **options):
        if command[0] == '/curl-config':
            assert options == {'capture_output': True, 'text': True, 'timeout': 10}
            return curl_outputs[command[1]]
        commands.append(command)
        assert options == {'capture_output': True, 'text': True, 'timeout': 120}
        if '-c' in command:
            compiled_sources.append(Path(command[command.index('-c') + 1]).read_text(encoding='utf-8'))
            Path(command[command.index('-o') + 1]).write_bytes(b'object')
        return subprocess.CompletedProcess(command, 0, stdout='', stderr='')

    source = '#include "runtime/wysna_runtime.h"\nextern int64_t wysna_host_http_request(void);\n'
    with tempfile.TemporaryDirectory(prefix='wysna-http-native-') as temporary:
        with patch('compiler.NativeToolchain.shutil.which', return_value='/curl-config'), \
                patch('compiler.NativeToolchain.compiler_command', return_value=['fake-cc']), \
                patch('compiler.NativeToolchain.subprocess.run', side_effect=run):
            NativeToolchain(ROOT).build(source, Path(temporary) / 'http-app')

    assert len(commands) == 2
    compile_command, link_command = commands
    assert '-I/curl/include' in compile_command
    assert '-L/curl/lib' in link_command and '-lcurl' in link_command
    assert compiled_sources[0].startswith('#define WYSNA_HAS_HTTP 1\n')


def test_http_native_build_rejects_old_libcurl():
    version_failure = subprocess.CompletedProcess(
        ['/curl-config', '--checkfor', '8.3.0'], 1,
        stdout='libcurl 8.2.1 is older than 8.3.0', stderr='',
    )
    with patch('compiler.NativeToolchain.shutil.which', return_value='/curl-config'), \
            patch('compiler.NativeToolchain.subprocess.run', return_value=version_failure):
        with expect_error(NativeCompilationError) as raised:
            NativeToolchain(ROOT).build(
                'extern int64_t wysna_host_http_request(void);', 'unused-output')
    assert '8.3.0' in raised.exception.stderr


def _check_native_failure_cleanup(failure_stage):
    commands = []
    owned_directories = set()
    with tempfile.TemporaryDirectory(prefix='wysna-toolchain-inputs-') as temporary:
        directory = Path(temporary)
        adapter = directory / 'adapter.c'
        adapter.write_text('/* caller owned */', encoding='utf-8')
        archive = directory / 'library.a'
        archive.write_bytes(b'caller owned archive')
        output = directory / 'existing-output'
        output.write_bytes(b'previous output')

        def run(command, **options):
            commands.append(command)
            assert options == {'capture_output': True, 'text': True, 'timeout': 120}
            assert command[:2] == ['fake-cc', '--configured-flag']
            assert '-fsanitize=address,undefined' in command
            assert '-fno-omit-frame-pointer' in command
            compiling = '-c' in command
            generated = Path(command[command.index('-o') + 1])
            if compiling:
                owned_directories.add(generated.parent)
                generated.write_bytes(b'partial temporary object')
            stage = 'program' if len(commands) == 1 else 'adapter' if compiling else 'link'
            if failure_stage == 'timeout' and stage == 'adapter':
                raise subprocess.TimeoutExpired(command, 120)
            if failure_stage == 'os_error' and stage == 'adapter':
                raise OSError('process launch failed')
            return subprocess.CompletedProcess(command, 9 if stage == failure_stage else 0,
                                               stdout='', stderr='native failure')

        expected = subprocess.TimeoutExpired if failure_stage == 'timeout' else (
            OSError if failure_stage == 'os_error' else NativeCompilationError
        )
        with patch('compiler.NativeToolchain.compiler_command', return_value=['fake-cc', '--configured-flag']), \
                patch('compiler.NativeToolchain.subprocess.run', side_effect=run):
            with expect_error(expected) as raised:
                NativeToolchain(ROOT).build('int main(void) { return 0; }', output,
                                             sanitize=True, link_libraries=(adapter, archive, 'm'))
        if isinstance(raised.exception, NativeCompilationError):
            error = raised.exception
            assert error.returncode == 9 and error.stderr == 'native failure'
            assert error.phase == ('link' if failure_stage == 'link' else 'c_compile')
            assert error.label == ('C Linker Error' if failure_stage == 'link' else 'C Compiler Error')
        assert owned_directories and all(not path.exists() for path in owned_directories)
        assert adapter.read_text(encoding='utf-8') == '/* caller owned */'
        assert archive.read_bytes() == b'caller owned archive'
        assert output.read_bytes() == b'previous output'
        if failure_stage == 'link':
            linked = commands[-1]
            assert str(archive) in linked and '-lm' in linked
            assert str(adapter) not in linked


def test_native_failure_cleans_only_owned_temporary_files():
    for failure_stage in ('program', 'adapter', 'link', 'timeout', 'os_error'):
        _check_native_failure_cleanup(failure_stage)


def test_unavailable_native_compiler_keeps_structured_error():
    failure = ToolchainError('missing configured compiler')
    with patch('compiler.NativeToolchain.compiler_command', side_effect=failure):
        with expect_error(ToolchainError) as raised:
            NativeToolchain(ROOT).build('int main(void) { return 0; }', 'unused-output')
    assert raised.exception is failure and failure.code == 'W5001'


def _check_selfhost_emission(outcome):
    directories = []
    with tempfile.TemporaryDirectory(prefix='wysna selfhost inputs ') as temporary:
        directory = Path(temporary)
        source = directory / 'input.wysna'
        source.write_text('print(42)', encoding='utf-8')
        compiler = directory / 'compiler with spaces'

        def run(command, **options):
            assert command[:2] == [str(compiler), os.path.abspath(source)]
            assert options == {'capture_output': True, 'text': True,
                               'cwd': str(directory), 'timeout': 120}
            emitted = Path(command[2])
            directories.append(emitted.parent)
            emitted.write_text('/* emitted C */', encoding='utf-8')
            if outcome == 'timeout':
                raise subprocess.TimeoutExpired(command, 120)
            return subprocess.CompletedProcess(command, 0 if outcome == 'ok' else 7,
                                               stdout='', stderr='selfhost failure')

        with patch('compiler.NativeToolchain.subprocess.run', side_effect=run):
            if outcome == 'ok':
                result = NativeToolchain(ROOT).emit_selfhost(compiler, source)
                assert result.source == '/* emitted C */' and result.codegen_ms >= 0
                with expect_error(FrozenInstanceError):
                    result.source = 'changed'
            else:
                expected = subprocess.TimeoutExpired if outcome == 'timeout' else NativeCompilationError
                with expect_error(expected) as raised:
                    NativeToolchain(ROOT).emit_selfhost(compiler, source)
                if outcome == 'failure':
                    error = raised.exception
                    assert (error.phase, error.label, error.stderr, error.returncode) == (
                        'selfhost', 'Wysna Compiler Error', 'selfhost failure', 7,
                    )
        assert directories and all(not path.exists() for path in directories)
        assert source.read_text(encoding='utf-8') == 'print(42)'


def test_selfhost_emission_preserves_input_cwd_and_cleans_output():
    for outcome in ('ok', 'failure', 'timeout'):
        _check_selfhost_emission(outcome)


def run_native_toolchain_tests():
    test_native_build_runs_explicit_c_adapter()
    test_http_native_build_resolves_and_links_libcurl_only_on_demand()
    test_http_native_build_rejects_old_libcurl()
    test_native_failure_cleans_only_owned_temporary_files()
    test_unavailable_native_compiler_keeps_structured_error()
    test_selfhost_emission_preserves_input_cwd_and_cleans_output()
    print('Native toolchain: all 12 execution, dependency, cleanup and ABI adapter cases passed')
    return True


if __name__ == '__main__':
    run_native_toolchain_tests()
