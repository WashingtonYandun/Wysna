#!/usr/bin/env python3
"""Build and run the Wysna measurement importer with explicit persistent storage."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common.Platform import cache_lock, compiler_command, executable_name
from compiler.Bootstrap import SELFHOST_BOOTSTRAP_INPUTS

PILOT = ROOT / 'examples/ffi/sqlite'


def build(output=None, backend='hir'):
    compiler = subprocess.run(compiler_command() + ['--version'], capture_output=True,
                              text=True, check=True, timeout=30).stdout
    inputs = list(SELFHOST_BOOTSTRAP_INPUTS) + [str(path.relative_to(ROOT)) for path in
        (PILOT / 'import_csv.wysna', PILOT / 'adapter.c', PILOT / 'adapter.h')]
    digest = hashlib.sha256(json.dumps([compiler, backend, compiler_command(), os.name]).encode())
    for relative in sorted(set(inputs)):
        digest.update(relative.encode())
        digest.update((ROOT / relative).read_bytes())
    identity = digest.hexdigest()
    binary = Path(executable_name(str(output))) if output else ROOT / '.wysna_cache/measurement-app' / identity[:16] / executable_name('measurement-ingest')
    binary = binary.resolve()
    binary.parent.mkdir(parents=True, exist_ok=True)
    with cache_lock(binary.parent / 'build.lock'):
        if output or not binary.exists():
            command = [sys.executable, str(ROOT / 'main.py'), 'build', str(PILOT / 'import_csv.wysna'),
                       '--backend=' + backend, '--link-lib', str(PILOT / 'adapter.c'),
                       '--link-lib', 'sqlite3', '-o', str(binary)]
            result = subprocess.run(command, capture_output=True, text=True, timeout=180)
            if result.returncode:
                raise RuntimeError(result.stderr)
            binary.with_suffix(binary.suffix + '.build.json').write_text(json.dumps({
                'source_sha256': identity, 'backend': backend, 'compiler': compiler,
                'command': command, 'runtime_abi': 2}, indent=2))
    return binary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database', type=Path, help='SQLite database path; required for data commands')
    parser.add_argument('--backend', choices=('hir', 'ast', 'selfhost'), default='hir')
    parser.add_argument('--json', action='store_true')
    commands = parser.add_subparsers(dest='action', required=True)
    builder = commands.add_parser('build')
    builder.add_argument('--output', type=Path, required=True)
    for action in ('import', 'check'):
        commands.add_parser(action).add_argument('csv', type=Path)
    commands.add_parser('status')
    arguments = parser.parse_args()
    if arguments.action != 'build' and arguments.database is None:
        parser.error('--database is required for data commands')
    if arguments.action in ('import', 'check'):
        if not arguments.csv.is_file() or arguments.csv.stat().st_size > 8388608:
            parser.error('CSV must be a regular file of at most 8 MiB')
    binary = build(getattr(arguments, 'output', None), arguments.backend)
    if arguments.action == 'build':
        print(binary)
        return 0
    native = [str(binary)]
    if arguments.action == 'status':
        native.append('--status')
    else:
        if arguments.action == 'check':
            native.append('--check')
        native.append(str(arguments.csv.resolve()))
    environment = dict(os.environ, WYSNA_SQLITE_DB=str(arguments.database.resolve()), WYSNA_MEM_CHECK='1')
    result = subprocess.run(native, capture_output=True, text=True, env=environment, timeout=120)
    lines = [line for line in result.stdout.splitlines() if not line.startswith('resources ')]
    if arguments.json:
        print(json.dumps({'action': arguments.action, 'database': str(arguments.database.resolve()),
                          'exit_code': result.returncode, 'messages': lines,
                          'diagnostic': result.stderr.strip()}, indent=2))
    else:
        print('\n'.join(lines))
        if result.stderr:
            print(result.stderr, end='', file=sys.stderr)
    return result.returncode


if __name__ == '__main__':
    try:
        raise SystemExit(main())
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(2)
