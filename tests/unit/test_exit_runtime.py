"""Immediate process exit and normal leak verification have distinct lifetimes."""
import os
from pathlib import Path
import subprocess
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
