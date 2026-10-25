#!/usr/bin/env python3
"""Build and verify a native measurement pilot with SQLite linked into the executable."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sqlite3
import ssl
import subprocess
import sys
import tempfile
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common.Platform import cache_lock, compiler_command, executable_name
from compiler.Bootstrap import SELFHOST_BOOTSTRAP_INPUTS

SQLITE_VERSION = '3.54.1'
SQLITE_ARCHIVE = 'sqlite-amalgamation-3540100'
SQLITE_URL = 'https://www.sqlite.org/2026/' + SQLITE_ARCHIVE + '.zip'
SQLITE_SHA3 = '92281c86e2957f9b9305890f9ac04204caa7b677cc868dc326d942689df28e30'
FIXTURES = {
    'measurements.csv': 'id,value\n1,7\n2,42\n',
    'duplicate.csv': 'id,value\n3,5\n1,9\n',
    'malformed.csv': 'id,value\n3,5\n4,-2\n',
    'overflow.csv': 'id,value\n3,9223372036854775808\n',
    'append.csv': 'id,value\n3,5\n',
}


def execute(command, **kwargs):
    result = subprocess.run(command, capture_output=True, text=True, timeout=300, **kwargs)
    if result.returncode:
        raise RuntimeError(result.stderr or result.stdout or str(command))
    return result


def sqlite_archive(path=None):
    cache = ROOT / '.wysna_cache/pilot-package'
    cache.mkdir(parents=True, exist_ok=True)
    target = Path(path) if path else cache / (SQLITE_ARCHIVE + '.zip')
    with cache_lock(cache / 'sqlite.lock'):
        if not target.exists() and path is None:
            context = ssl.create_default_context()
            if sys.platform == 'darwin' and Path('/etc/ssl/cert.pem').exists():
                context.load_verify_locations('/etc/ssl/cert.pem')
            with urllib.request.urlopen(SQLITE_URL, timeout=60, context=context) as response:
                data = response.read(8 * 1024 * 1024)
            if hashlib.sha3_256(data).hexdigest() != SQLITE_SHA3:
                raise ValueError('SQLite download does not match the pinned official SHA3-256')
            target.write_bytes(data)
        if hashlib.sha3_256(target.read_bytes()).hexdigest() != SQLITE_SHA3:
            raise ValueError('SQLite archive does not match the pinned official SHA3-256')
    return target


def verify_package(directory):
    """Exercise the exported executable, with an independent database oracle."""
    records = []
    with tempfile.TemporaryDirectory(prefix='w pilot with spaces ') as temporary:
        sandbox = Path(temporary)
        copied = sandbox / 'application with spaces'
        shutil.copytree(directory, copied)
        binary = copied / executable_name('measurement-ingest')
        database = sandbox / 'trial database.sqlite'
        environment = dict(os.environ, WYSNA_SQLITE_DB=str(database), WYSNA_MEM_CHECK='1')
        def run(arguments, code, output=None):
            result = subprocess.run([str(binary)] + arguments, env=environment,
                                    capture_output=True, text=True, timeout=60)
            assert result.returncode == code and not result.stderr, result
            if output is not None:
                assert result.stdout == output, result
            records.append({'arguments': arguments, 'exit_code': code, 'stdout': result.stdout})
        def snapshot():
            with closing(sqlite3.connect(database)) as connection:
                return (connection.execute('SELECT id,value FROM measurements ORDER BY id').fetchall(),
                        connection.execute('SELECT row_count,input_bytes FROM imports ORDER BY id').fetchall())
        def csv(name):
            return str(copied / name)
        run(['--version'], 0, '3054001\n')
        run(['--check', csv('measurements.csv')], 0, 'validated 2\nresources 0\n')
        assert snapshot() == ([], [])
        run(['--status'], 0, 'status 0 0 0\nresources 0\n')
        run([csv('measurements.csv')], 0, 'imported 2 2 49\nresources 0\n')
        baseline = snapshot()
        assert baseline == ([(1, 7), (2, 42)], [(2, len(FIXTURES['measurements.csv']))])
        for name, message in (('duplicate.csv', 'database error 19'),
                              ('malformed.csv', 'csv error 3 expected ASCII decimal'),
                              ('overflow.csv', 'csv error 2 integer overflow')):
            run([csv(name)], 1, message + '\nresources 0\n')
            assert snapshot() == baseline, 'Failed batch changed data or journal: ' + name
        run(['--status'], 0, 'status 2 49 1\nresources 0\n')
        run(['--check', csv('append.csv')], 0, 'validated 1\nresources 0\n')
        assert snapshot() == baseline
        run([csv('append.csv')], 0, 'imported 1 3 54\nresources 0\n')
        rows, journal = snapshot()
        assert rows == [(1, 7), (2, 42), (3, 5)] and [row[0] for row in journal] == [2, 1]
        run(['--status'], 0, 'status 3 54 2\nresources 0\n')
    return {'passed': True, 'space_paths': True, 'atomic_rollback': True,
            'dry_run_rollback': True, 'transactional_journal': True, 'commands': records}


def package(output, archive=None):
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    allowed = set(FIXTURES) | {executable_name('measurement-ingest'), 'smoke.json',
                              'manifest.json', 'START_HERE.txt', 'CHECKSUMS.sha256'}
    if any(path.name not in allowed or not path.is_file() for path in output.iterdir()):
        raise ValueError('Output directory contains unrelated files; choose a new directory')
    compiler = compiler_command()
    toolchain = execute(compiler + ['--version']).stdout
    windows = os.name == 'nt'
    flags = ['-std=c99', '-O2'] + (['-fms-runtime-lib=static', '-D_CRT_SECURE_NO_WARNINGS'] if windows else [])
    library = sqlite_archive(archive)
    with tempfile.TemporaryDirectory(prefix='wysna-package-') as temporary:
        workspace = Path(temporary)
        with zipfile.ZipFile(library) as zipped:
            for name in ('sqlite3.c', 'sqlite3.h'):
                (workspace / name).write_bytes(zipped.read(SQLITE_ARCHIVE + '/' + name))
        generated = workspace / 'application.c'
        execute([sys.executable, str(ROOT / 'main.py'), 'emit-c', '--backend=hir',
                 str(ROOT / 'examples/ffi/sqlite/import_csv.wysna'), '-o', str(generated)])
        objects = []
        for index, source in enumerate((generated, ROOT / 'examples/ffi/sqlite/adapter.c', workspace / 'sqlite3.c')):
            object_path = workspace / (str(index) + ('.obj' if windows else '.o'))
            execute(compiler + flags + ['-I' + str(ROOT), '-I' + str(workspace),
                                       '-c', str(source), '-o', str(object_path)])
            objects.append(str(object_path))
        binary = output / executable_name('measurement-ingest')
        extra = ['-lpthread', '-ldl', '-lm'] if sys.platform.startswith('linux') else []
        execute(compiler + flags + objects + extra + ['-o', str(binary)])
    dependencies = []
    if windows:
        compiler_path = shutil.which(compiler[0])
        reader = Path(compiler_path).with_name('llvm-readobj.exe') if compiler_path else None
        if reader is None or not reader.is_file():
            raise RuntimeError('Windows packaging requires llvm-readobj beside clang to verify DLL imports')
        imports = execute([str(reader), '--coff-imports', str(binary)]).stdout
        dependencies = re.findall(r'Name:\s+(\S+\.dll)', imports, flags=re.I)
        if not dependencies:
            raise RuntimeError('No Windows imports found; cannot verify package dependencies')
        forbidden = ('sqlite', 'vcruntime', 'msvcp', 'msvcrt', 'ucrtbase', 'api-ms-win-crt')
        if any(name.lower().startswith(forbidden) for name in dependencies):
            raise RuntimeError('Pilot unexpectedly requires an external SQLite or C runtime DLL: ' + str(dependencies))
    for name, content in FIXTURES.items():
        (output / name).write_bytes(content.encode('ascii'))
    smoke = verify_package(output)
    (output / 'smoke.json').write_text(json.dumps(smoke, indent=2))
    digest = hashlib.sha256()
    for relative in sorted(set(SELFHOST_BOOTSTRAP_INPUTS + ('examples/ffi/sqlite/import_csv.wysna',
                               'examples/ffi/sqlite/adapter.c', 'examples/ffi/sqlite/adapter.h',
                               'tools/package_measurement_pilot.py'))):
        digest.update(relative.encode())
        digest.update((ROOT / relative).read_bytes())
    commit = execute(['git', '-C', str(ROOT), 'rev-parse', 'HEAD']).stdout.strip()
    manifest = {'commit': commit, 'source_sha256': digest.hexdigest(), 'platform': sys.platform,
                'machine': platform.machine(), 'compiler': toolchain, 'flags': flags, 'runtime_abi': 2,
                'sqlite': {'version': SQLITE_VERSION, 'url': SQLITE_URL, 'archive_sha3_256': SQLITE_SHA3,
                           'linked_statically': True},
                'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(), 'smoke_passed': True,
                'imported_dlls': dependencies, 'msvc_runtime_static': windows}
    (output / 'manifest.json').write_text(json.dumps(manifest, indent=2))
    (output / 'START_HERE.txt').write_text('''Measurement Ingestion Pilot

Windows x64: extract the package and open PowerShell in the extracted folder.
The executable includes SQLite and the C runtime; it requires neither Python nor a compiler.
This is an unsigned experimental binary. Administrator privileges are not required.
Use a new database for this trial; do not point it at an existing database.

$env:WYSNA_SQLITE_DB = Join-Path $PWD ("trial-" + [guid]::NewGuid() + ".sqlite")
.\\measurement-ingest.exe --version
.\\measurement-ingest.exe --check measurements.csv
.\\measurement-ingest.exe --status
.\\measurement-ingest.exe measurements.csv
.\\measurement-ingest.exe --status
.\\measurement-ingest.exe duplicate.csv
.\\measurement-ingest.exe --status
.\\measurement-ingest.exe malformed.csv
.\\measurement-ingest.exe --status
.\\measurement-ingest.exe --check append.csv
.\\measurement-ingest.exe --status
.\\measurement-ingest.exe append.csv
.\\measurement-ingest.exe --status

Expected: before the import, status is 0 0 0. After importing, status is 2 49 1.
duplicate.csv and malformed.csv should fail with exit code 1 and preserve status 2 49 1.
Checking append.csv should not change it. After importing append.csv, status is 3 54 2.
resources 0 confirms that owned handles were closed; it does not prove the absence of all leaks.
The third status value confirms the journal entry. Save the created database and output.

Record the Windows model, time to the first import, errors, and confusing steps.
Then try a non-sensitive CSV from one of your own tasks: header id,value, non-negative integers,
unique IDs, at most 100000 rows, and 8 MiB. Select another new database. No data is uploaded.
This trial on one machine does not demonstrate adoption by independent users.

manifest.json identifies the sources, compiler, and binary. CHECKSUMS.sha256 can verify
the files with Get-FileHash -Algorithm SHA256. smoke.json records only the CI smoke test.
SQLite: https://www.sqlite.org/copyright.html. The version and source are pinned in manifest.json.
''', encoding='utf-8')
    checksums = [hashlib.sha256(path.read_bytes()).hexdigest() + '  ' + path.name
                 for path in sorted(output.iterdir()) if path.is_file() and path.name != 'CHECKSUMS.sha256']
    (output / 'CHECKSUMS.sha256').write_text('\n'.join(checksums) + '\n')
    machine = platform.machine().lower().replace('amd64', 'x86_64')
    destination = output.parent / (output.name + '-' + sys.platform + '-' + machine + '.zip')
    with zipfile.ZipFile(destination, 'w', zipfile.ZIP_DEFLATED) as zipped:
        for path in sorted(output.iterdir()):
            if path.is_file():
                zipped.write(path, path.name)
    return destination


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'dist/measurement-pilot')
    parser.add_argument('--sqlite-archive', type=Path)
    arguments = parser.parse_args()
    print(package(arguments.output, arguments.sqlite_archive))


if __name__ == '__main__':
    main()
