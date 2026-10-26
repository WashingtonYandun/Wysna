#!/usr/bin/env python3
"""Verify and measure atomic CSV imports in C, AST C99, HIR C99 and Wysna Compiler."""
import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import sqlite3
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from compiler.Bootstrap import ensure_selfhost_compiler_binary
from tools.validate_sqlite_ffi import execute

PILOT = ROOT / 'examples/ffi/sqlite'


def snapshot(path):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute('SELECT id, value FROM measurements ORDER BY id').fetchall()


def journal(path):
    with closing(sqlite3.connect(path)) as connection:
        return connection.execute('SELECT row_count, input_bytes FROM imports ORDER BY id').fetchall()


def measured(binary, source, database, sanitize, allocation_profile=False):
    environment = dict(os.environ, WYSNA_SQLITE_DB=str(database), WYSNA_MEM_CHECK='1')
    environment.pop('WYSNA_MEM_STATS', None)
    if allocation_profile:
        environment['WYSNA_MEM_STATS'] = '1'
    if sanitize:
        environment['ASAN_OPTIONS'] = 'detect_leaks=' + ('1' if sys.platform.startswith('linux') else '0')
        environment['UBSAN_OPTIONS'] = 'halt_on_error=1:print_stacktrace=1'
    arguments = [str(argument) for argument in source] if isinstance(source, (list, tuple)) else [str(source)]
    result = execute([sys.executable, str(ROOT / 'tools/measure_process.py'), '--',
                      str(binary)] + arguments, environment)
    record = json.loads(result.stdout)
    if allocation_profile:
        match = re.fullmatch(r'\[WYSNA_MEM_STATS\] alloc=(\d+) free=(\d+) retain=(\d+) release=(\d+) live=(\d+)\n',
                             record['stderr'])
        assert match, record
        record['runtime_memory'] = dict(zip(('alloc', 'free', 'retain', 'release', 'live'),
                                             map(int, match.groups())))
        assert record['runtime_memory']['alloc'] == record['runtime_memory']['free'], record
        assert record['runtime_memory']['live'] == 0, record
    else:
        assert not record['stderr'], record
    assert record['peak_memory_bytes'] > 0, record
    return record


def validate(rows=10000, samples=3, sanitize=False):
    compiler = shlex.split(os.environ.get('CC', 'cc'))
    flags = ['-std=c99', '-O1' if sanitize else '-O2', '-g', '-I' + str(ROOT)]
    if sanitize:
        flags += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    bootstrap_started = time.perf_counter()
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    bootstrap_seconds = time.perf_counter() - bootstrap_started
    report = {'rows': rows, 'samples': samples, 'sanitize': sanitize,
              'platform': sys.platform, 'compiler': execute(compiler + ['--version']).stdout,
              'flags': flags, 'bootstrap_seconds': bootstrap_seconds,
              'memory_method': 'one isolated child ru_maxrss; Darwin bytes, Linux KiB converted to bytes',
              'engines': {}}
    with tempfile.TemporaryDirectory(prefix='wysna-sqlite-import-') as temporary:
        directory = Path(temporary)
        adapter = directory / 'adapter.o'
        execute(compiler + flags + ['-Wall', '-Wextra', '-Werror', '-c', str(PILOT / 'adapter.c'), '-o', str(adapter)])
        csv = directory / 'measurements.csv'
        values = [(index, 4294967296 + index * 10) for index in range(rows)]
        csv.write_bytes(('id,value\r\n' + ''.join(f'{key},{value}\r\n' for key, value in values)).encode())
        total = sum(value for _, value in values)
        for backend in ('c', 'ast', 'hir', 'selfhost'):
            binary = directory / backend
            started = time.perf_counter()
            if backend == 'c':
                source = PILOT / 'import_reference.c'
            else:
                source = directory / (backend + '.c')
                if backend == 'selfhost':
                    execute([selfhost_bin, str(PILOT / 'import_csv.wysna'), str(source)])
                else:
                    execute([sys.executable, str(ROOT / 'main.py'), 'emit-c', '--backend=' + backend,
                             str(PILOT / 'import_csv.wysna'), '-o', str(source)])
            command = compiler + flags + [str(source), str(adapter), '-lsqlite3', '-o', str(binary)]
            execute(command)
            build_seconds = time.perf_counter() - started
            version = measured(binary, "--version", directory / "unused.sqlite", sanitize)
            assert version["exit_code"] == 0, version
            allocation = None
            if backend != 'c':
                profile_database = directory / (backend + '-allocations.sqlite')
                allocation = measured(binary, csv, profile_database, sanitize, allocation_profile=True)
                assert allocation['exit_code'] == 0 and snapshot(profile_database) == values, allocation
            runs = []
            for sample in range(samples + 1):
                database = directory / f'{backend}-{sample}.sqlite'
                record = measured(binary, csv, database, sanitize)
                assert record['exit_code'] == 0, (backend, record)
                assert record['stdout'] == f'imported {rows} {rows} {total}\nresources 0\n', record
                assert snapshot(database) == values, backend
                assert journal(database) == [(rows, csv.stat().st_size)], backend
                if sample:
                    runs.append(record)
            baseline = directory / f'{backend}-baseline.sqlite'
            shutil.copyfile(database, baseline)
            append = directory / 'append.csv'
            append.write_text(f'id,value\n{rows},7')
            checked = measured(binary, ['--check', append], database, sanitize)
            assert checked['exit_code'] == 0 and checked['stdout'] == 'validated 1\nresources 0\n', checked
            assert snapshot(database) == values and journal(database) == [(rows, csv.stat().st_size)], backend
            status = measured(binary, '--status', database, sanitize)
            assert status['exit_code'] == 0 and status['stdout'] == f'status {rows} {total} 1\nresources 0\n', status
            record = measured(binary, append, database, sanitize)
            assert record['exit_code'] == 0 and record['stdout'] == f'imported 1 {rows + 1} {total + 7}\nresources 0\n', record
            assert snapshot(database) == values + [(rows, 7)], backend
            assert journal(database) == [(rows, csv.stat().st_size), (1, append.stat().st_size)], backend
            # The valid first row must also roll back when a later row fails.
            cases = {
                'duplicate_batch': (f'id,value\n{rows},7\n{rows},9\n', 'database error 19'),
                'duplicate_persisted': ('id,value\n0,7\n', 'database error 19'),
                'bad_decimal': (f'id,value\n{rows},7\n{rows + 1},-2\n', 'csv error 3 expected ASCII decimal'),
                'overflow': (f'id,value\n{rows},9223372036854775808\n', 'csv error 2 integer overflow'),
                'sum_overflow': (f'id,value\n{rows},9223372036854775807\n', 'database error 1'),
                'header': ('value,id\n1,2\n', 'csv error 1 expected id,value header'),
                'columns': ('id,value\n1,2,3\n', 'csv error 2 expected two columns'),
                'empty_value': ('id,value\n1,\n', 'csv error 2 empty integer'),
                'nul': ('id,value\n1,2\x00\n', 'csv error 2 expected ASCII decimal'),
                'unicode_digit': ('id,value\n1,１２\n', 'csv error 2 expected ASCII decimal'),
                'no_rows': ('id,value\n', 'csv error 2 no measurements'),
                'row_limit': ('id,value\n' + ''.join(f'{rows + key},0\n' for key in range(100001)),
                              'csv error 100002 row limit exceeded'),
            }
            for name, (content, expected) in cases.items():
                source_csv = directory / (name + '.csv')
                source_csv.write_bytes(content.encode())
                target = directory / f'{backend}-{name}.sqlite'
                shutil.copyfile(baseline, target)
                record = measured(binary, source_csv, target, sanitize)
                assert record['exit_code'] == 1 and record['stdout'] == expected + '\nresources 0\n', (backend, name, record)
                assert snapshot(target) == values, (backend, name, 'failed import changed persisted rows')
                assert journal(target) == [(rows, csv.stat().st_size)], (backend, name, 'failed import changed its journal')
            bad_database = directory / 'missing' / 'database.sqlite'
            record = measured(binary, csv, bad_database, sanitize)
            assert record['exit_code'] == 1 and record['stdout'] == 'database error 14\nresources 0\n', record
            record = measured(binary, directory / 'missing.csv', baseline, sanitize)
            assert record['exit_code'] == 2 and record['stdout'].startswith('io error'), record
            assert snapshot(baseline) == values, backend
            durations = [run['elapsed_seconds'] for run in runs]
            memory = [run['peak_memory_bytes'] for run in runs]
            report['engines'][backend] = {
                'sqlite_version_number': int(version['stdout']),
                'build_seconds': build_seconds, 'run_seconds': durations,
                'median_run_seconds': statistics.median(durations),
                'user_seconds': [run['user_seconds'] for run in runs],
                'system_seconds': [run['system_seconds'] for run in runs],
                'runtime_memory': allocation['runtime_memory'] if allocation else None,
                'peak_memory_bytes': memory, 'median_peak_memory_bytes': statistics.median(memory),
                'binary_bytes': binary.stat().st_size, 'input_bytes': csv.stat().st_size,
                'persisted_rows': rows, 'persisted_total': total, 'live_adapter_resources': 0,
                'dry_run_rollback': True, 'transactional_journal': True, 'status_command': True,
                'failure_cases': list(cases) + ['open', 'missing_file'], 'link_command': command}
    assert len({engine['sqlite_version_number'] for engine in report['engines'].values()}) == 1
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--rows', type=int, default=10000)
    parser.add_argument('--samples', type=int, default=3)
    parser.add_argument('--sanitize', action='store_true')
    parser.add_argument('--report', type=Path, default=ROOT / '.wysna_cache/sqlite-import/report.json')
    arguments = parser.parse_args()
    if not 1 <= arguments.rows <= 100000 or not 1 <= arguments.samples <= 100:
        parser.error('rows must be 1..100000 and samples must be 1..100')
    report = validate(arguments.rows, arguments.samples, arguments.sanitize)
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(json.dumps(report, indent=2))
    print(f'SQLite CSV: C/AST/HIR/direct selfhost passed; {arguments.rows} rows, atomic rollback, persistence, zero live handles')
    print(f'Report: {arguments.report}')


if __name__ == '__main__':
    main()
