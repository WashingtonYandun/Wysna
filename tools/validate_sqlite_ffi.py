#!/usr/bin/env python3
"""Build and verify the SQLite FFI pilot against the same workload in C."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import shlex
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / 'examples/ffi/sqlite'


def execute(command, environment=None):
    result = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True,
                            text=True, timeout=120)
    if result.returncode:
        raise RuntimeError(f'{shlex.join(command)} failed ({result.returncode}):\n{result.stderr}')
    return result


def validate(rows=1000, samples=3, sanitize=False, llvm=False):
    compiler = shlex.split(os.environ.get('CC', 'cc'))
    flags = ['-std=c99', '-O1' if sanitize else '-O2', '-g']
    if sanitize:
        flags += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    flags += ['-I' + str(ROOT)]
    report = {'rows': rows, 'samples': samples, 'sanitize': sanitize,
              'platform': sys.platform, 'compiler': execute(compiler + ['--version']).stdout,
              'flags': flags, 'engines': {}, 'peak_memory_bytes': None}
    expected_total = rows * 4294967296 + rows * (rows - 1) // 2 * 10
    with tempfile.TemporaryDirectory(prefix='wysna-sqlite-ffi-') as temporary:
        directory = Path(temporary)
        adapter = directory / 'adapter.o'
        execute(compiler + flags + ['-Wall', '-Wextra', '-Werror', '-c', str(PILOT / 'adapter.c'), '-o', str(adapter)])
        for backend in (('c', 'ast', 'hir', 'llvm-experimental') if llvm else ('c', 'ast', 'hir')):
            binary = directory / backend
            started = time.perf_counter()
            if backend == 'llvm-experimental':
                command = [sys.executable, str(ROOT / 'main.py'), 'build', '--backend=llvm-experimental',
                           str(PILOT / 'measurements.wysna'), '-o', str(binary),
                           '--link-lib', str(adapter), '--link-lib', 'sqlite3'] + (['--sanitize'] if sanitize else [])
                execute(command)
            elif backend == 'c':
                source = PILOT / 'reference.c'
            else:
                source = directory / (backend + '.c')
                if backend == 'ast':
                    command = [sys.executable, str(ROOT / 'tests/emit_ast_oracle.py'),
                               str(PILOT / 'measurements.wysna'), str(source)]
                else:
                    command = [sys.executable, str(ROOT / 'main.py'), 'emit-c',
                               str(PILOT / 'measurements.wysna'), '-o', str(source)]
                execute(command)
            if backend != 'llvm-experimental':
                command = compiler + flags + [str(source), str(adapter), '-lsqlite3', '-o', str(binary)]
                execute(command)
            build_seconds = time.perf_counter() - started
            environment = dict(os.environ, WYSNA_MEM_CHECK='1', WYSNA_SQLITE_ROWS=str(rows))
            if sanitize:
                environment['ASAN_OPTIONS'] = 'detect_leaks=' + ('1' if sys.platform.startswith('linux') else '0')
                environment['UBSAN_OPTIONS'] = 'halt_on_error=1:print_stacktrace=1'
            elapsed = []
            for sample in range(samples + 1):
                database = directory / f'{backend}-{sample}.sqlite'
                environment['WYSNA_SQLITE_DB'] = str(database)
                started = time.perf_counter()
                result = execute([str(binary)], environment)
                duration = time.perf_counter() - started
                if result.stderr:
                    raise AssertionError((backend, result.stderr))
                lines = result.stdout.splitlines()
                assert len(lines) == 3 and int(lines[0]) > 0, (backend, lines)
                assert lines[1] == f'{rows} {expected_total}', (backend, lines)
                assert lines[2] == '19 -1 21 0 0 0 0', (backend, lines)
                with closing(sqlite3.connect(database)) as connection:
                    actual = connection.execute('SELECT count(*), sum(value) FROM measurements').fetchone()
                    assert actual == (rows, expected_total), (backend, actual)
                if sample:
                    elapsed.append(duration)
            for failure, path, requested_rows, expected in [
                ('open', directory / 'missing' / 'database.sqlite', rows,
                 f'open or input failure 14 {rows}\n0 0\n'),
                ('input', directory / f'{backend}-invalid.sqlite', 0,
                 'open or input failure 0 -1\n0 0\n'),
            ]:
                environment.update(WYSNA_SQLITE_DB=str(path), WYSNA_SQLITE_ROWS=str(requested_rows))
                result = execute([str(binary)], environment)
                assert result.stdout == expected and not result.stderr, (backend, failure, result.stdout, result.stderr)
            report['engines'][backend] = {
                'sqlite_version_number': int(lines[0]), 'build_seconds': build_seconds,
                'run_seconds': elapsed, 'median_run_seconds': statistics.median(elapsed),
                'binary_bytes': binary.stat().st_size, 'link_command': command,
                'persisted_rows': rows, 'persisted_total': expected_total,
                'duplicate_status': 19, 'invalid_value_status': -1, 'closed_handle_status': 21,
                'live_adapter_resources': 0, 'failure_cases': ['open', 'input'],
            }
    versions = [engine['sqlite_version_number'] for engine in report['engines'].values()]
    assert len(set(versions)) == 1, versions
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=int, default=1000)
    parser.add_argument('--samples', type=int, default=3)
    parser.add_argument('--sanitize', action='store_true')
    parser.add_argument('--llvm', action='store_true', help='Also qualify the optional LLVM backend')
    parser.add_argument('--report', type=Path, default=ROOT / '.wysna_cache/sqlite-ffi/report.json')
    args = parser.parse_args()
    if not 1 <= args.rows <= 100000 or not 1 <= args.samples <= 100:
        parser.error('rows must be 1..100000 and samples must be 1..100')
    report = validate(args.rows, args.samples, args.sanitize, args.llvm)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2))
    print(f'SQLite FFI: {"/".join(report["engines"])} passed; {args.rows} persisted rows, recoverable errors and zero live adapter resources')
    print(f'Report: {args.report}')


if __name__ == '__main__':
    main()
