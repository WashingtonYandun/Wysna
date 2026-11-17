"""Immediate process exit and normal leak verification have distinct lifetimes."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from common.Platform import compiler_command, executable_name

ROOT = Path(__file__).resolve().parents[2]


def test_explicit_exit_preserves_status_without_disabling_normal_leak_detection():
    source = r'''
#include "runtime/wysna_runtime.h"
int main(int argc, char **argv) {
    wysna_init_args(argc, argv);
    WVal live = wysna_copy_str("active-frame-value");
    if (argc > 1) wysna_builtin_exit(wysna_int(7));
    (void)live;
    wysna_runtime_cleanup();
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix='wysna-exit-runtime-') as temporary:
        directory = Path(temporary)
        program = directory / 'exit.c'
        executable = directory / executable_name('exit')
        program.write_text(source, encoding='utf-8')
        build = subprocess.run(compiler_command() + ['-std=c99', '-I' + str(ROOT), str(program),
                               '-o', str(executable)], capture_output=True, text=True, timeout=60)
        assert build.returncode == 0, build.stderr
        environment = dict(os.environ, WYSNA_MEM_CHECK='1')
        immediate = subprocess.run([str(executable), 'exit'], env=environment, capture_output=True, text=True, timeout=10)
        assert immediate.returncode == 7, immediate.stderr
        assert 'Memory leak detected' not in immediate.stderr
        normal = subprocess.run([str(executable)], env=environment, capture_output=True, text=True, timeout=10)
        assert normal.returncode == 1
        assert 'Memory leak detected at exit' in normal.stderr


def test_panic_and_exit_under_sanitizers_preserve_exit_without_lsan_false_positives():
    if os.name == 'nt':
        return
    source = r'''
#include "runtime/wysna_runtime.h"
#include <string.h>
int main(int argc, char **argv) {
    wysna_init_args(argc, argv);
    WVal items[2] = {
        wysna_copy_str("heap-string-one-beyond-sso"),
        wysna_copy_str("heap-string-two-beyond-sso")
    };
    WVal live = wysna_make_list(2, items);
    wysna_release(items[0]);
    wysna_release(items[1]);
    if (argc > 1 && strcmp(argv[1], "exit") == 0) {
        printf("flushed-before-exit\n");
        wysna_builtin_exit(wysna_int(7));
    }
    if (argc > 1 && strcmp(argv[1], "fail") == 0) {
        printf("flushed-before-fail\n");
        wysna_runtime_fail("W4003", "synthetic panic with live NaN-boxed list");
    }
    if (argc > 1 && strcmp(argv[1], "clean") == 0) {
        wysna_release(live);
        wysna_runtime_cleanup();
        return 0;
    }
    (void)live;
    wysna_runtime_cleanup();
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix='wysna-exit-asan-') as temporary:
        directory = Path(temporary)
        program = directory / 'exit_asan.c'
        executable = directory / executable_name('exit_asan')
        program.write_text(source, encoding='utf-8')
        command = (
            compiler_command()
            + ['-std=c99', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
               '-I' + str(ROOT), str(program), '-lm', '-o', str(executable)]
        )
        build = subprocess.run(command, capture_output=True, text=True, timeout=60)
        assert build.returncode == 0, build.stderr
        environment = dict(
            os.environ,
            WYSNA_MEM_CHECK='1',
            UBSAN_OPTIONS='halt_on_error=1',
            ASAN_OPTIONS='detect_leaks=' + ('1' if sys.platform.startswith('linux') else '0'),
        )
        for mode, expected_code, expected_out, expected_err in (
            ('exit', 7, 'flushed-before-exit\n', ''),
            ('fail', 1, 'flushed-before-fail\n', '[W4003] RuntimeError: synthetic panic with live NaN-boxed list'),
            ('clean', 0, '', ''),
        ):
            result = subprocess.run([str(executable), mode], env=environment, capture_output=True, text=True, timeout=15)
            assert result.returncode == expected_code, (mode, result)
            assert result.stdout == expected_out, (mode, result)
            if expected_err:
                assert expected_err in result.stderr, (mode, result)
            else:
                assert result.stderr == '', (mode, result)
            assert 'AddressSanitizer' not in result.stderr, (mode, result.stderr)
            assert 'LeakSanitizer' not in result.stderr, (mode, result.stderr)
            assert 'UndefinedBehaviorSanitizer' not in result.stderr, (mode, result.stderr)

        leaked = subprocess.run([str(executable), 'leak'], env=environment, capture_output=True, text=True, timeout=15)
        assert leaked.returncode != 0, leaked
        assert 'Memory leak detected at exit' in leaked.stderr, leaked.stderr
