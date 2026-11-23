"""A worker must never overwrite another task's diagnostic source position."""
import os
from pathlib import Path
import subprocess

from common.Platform import compiler_command, executable_name

ROOT = Path(__file__).resolve().parents[2]


def test_native_workers_keep_independent_diagnostic_positions(tmp_path):
    source = tmp_path / 'thread_positions.c'
    source.write_text(r'''
#include "runtime/wysna_runtime.h"
#include <assert.h>
static int32_t arrived = 0;
static int32_t failures = 0;
static void verify_position(int32_t line) {
    wysna_runtime_push_frame("worker", "worker.wysna", line);
    wysna_runtime_set_position("worker.wysna", line, 3);
    wysna_atomic_inc_i32(&arrived);
    while (wysna_atomic_load_i32(&arrived) < 2) {}
    if (wysna_g_source_line != line || wysna_g_source_col != 3)
        wysna_atomic_inc_i32(&failures);
    if (wysna_thread_diagnostics.call_depth != 1 ||
        wysna_thread_diagnostics.call_stack_line[0] != line)
        wysna_atomic_inc_i32(&failures);
    wysna_runtime_pop_frame();
}
#ifdef _WIN32
static DWORD WINAPI worker(LPVOID raw) { verify_position((int32_t)(intptr_t)raw); return 0; }
#else
static void* worker(void* raw) { verify_position((int32_t)(intptr_t)raw); return NULL; }
#endif
int main(int argc, char** argv) {
    wysna_init_args(argc, argv);
    wysna_runtime_set_position("main.wysna", 99, 7);
#ifdef _WIN32
    HANDLE first = CreateThread(NULL, 0, worker, (void*)(intptr_t)11, 0, NULL);
    HANDLE second = CreateThread(NULL, 0, worker, (void*)(intptr_t)22, 0, NULL);
    assert(first && second);
    WaitForSingleObject(first, INFINITE); WaitForSingleObject(second, INFINITE);
    CloseHandle(first); CloseHandle(second);
#else
    pthread_t first, second;
    assert(pthread_create(&first, NULL, worker, (void*)(intptr_t)11) == 0);
    assert(pthread_create(&second, NULL, worker, (void*)(intptr_t)22) == 0);
    pthread_join(first, NULL); pthread_join(second, NULL);
#endif
    assert(wysna_atomic_load_i32(&failures) == 0);
    assert(wysna_g_source_line == 99 && wysna_g_source_col == 7);
    assert(strcmp(wysna_g_source_file, "main.wysna") == 0);
    wysna_runtime_cleanup();
    return 0;
}
''', encoding='utf-8')
    binary = tmp_path / executable_name('thread_positions')
    flags = [] if os.name == 'nt' else ['-pthread', '-lm']
    build = subprocess.run([*compiler_command(), '-std=c99', '-O1', f'-I{ROOT}',
                            str(source), '-o', str(binary), *flags],
                           capture_output=True, text=True, timeout=60)
    assert build.returncode == 0, build.stderr
    run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
    assert run.returncode == 0, run.stderr
