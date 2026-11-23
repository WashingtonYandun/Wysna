"""A frozen v0.8 project upgrades explicitly without silent lock mutation."""
import json
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from common.Version import WYSNA_VERSION
from tests.unit.test_ops_a01_workflow import native_driver

ROOT = Path(__file__).resolve().parents[2]
LEGACY = ROOT / 'tests/fixtures/language_combinations/migration_v08'


@pytest.mark.parametrize('engine', ['seed', 'native'])
@pytest.mark.parametrize('line_ending', ['lf', 'crlf'])
def test_v08_manifest_and_frozen_lock_require_explicit_upgrade(tmp_path, native_driver, engine, line_ending):
    project = tmp_path / 'project'
    shutil.copytree(LEGACY, project)
    command = [sys.executable, str(ROOT / 'main.py')] if engine == 'seed' else [str(native_driver)]
    environment = dict(os.environ, WYSNA_DELEGATE_NATIVE='0',
                       WYSNA_CACHE_HOME=str(tmp_path / 'cache'),
                       ZIG_GLOBAL_CACHE_DIR=str(tmp_path / 'zig-global'),
                       ZIG_LOCAL_CACHE_DIR=str(tmp_path / 'zig-local'))

    def run(*arguments):
        result = subprocess.run([*command, 'project', *arguments, '--output-format=json-v1'],
            cwd=project, env=environment, capture_output=True, text=True, timeout=120)
        return result, json.loads(result.stdout)

    manifest = project / 'wysna.toml'
    lock = project / 'wysna.lock'
    if line_ending == 'crlf':
        legacy_bytes = manifest.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n')
        manifest.write_bytes(legacy_bytes)
        legacy_lock = json.loads(lock.read_bytes())
        legacy_lock['root']['manifest_sha256'] = hashlib.sha256(legacy_bytes).hexdigest()
        lock.write_bytes((json.dumps(legacy_lock, indent=2) + '\n').encode('utf-8'))
    original_manifest, original_lock = manifest.read_bytes(), lock.read_bytes()
    result, payload = run('check', '--frozen')
    assert result.returncode == 2, payload
    assert payload['diagnostics'][0]['code'] == 'W5002', payload
    assert manifest.read_bytes() == original_manifest
    assert lock.read_bytes() == original_lock

    language = '.'.join(WYSNA_VERSION.split('.')[:2])
    manifest.write_bytes(original_manifest.decode('utf-8').replace('\r\n', '\n').replace('language = "0.8"',
        f'language = "{language}"').replace('compiler = "0.8.0"',
        f'compiler = "{WYSNA_VERSION}"').encode('utf-8'))
    assert b'\r\r\n' not in manifest.read_bytes()
    result, payload = run('check', '--frozen')
    assert result.returncode == 2, payload
    assert 'stale' in payload['diagnostics'][0]['message'].lower(), payload
    assert lock.read_bytes() == original_lock

    result, payload = run('lock')
    assert result.returncode == 0, payload
    locked = lock.read_bytes()
    assert json.loads(locked)['compiler_version'] == WYSNA_VERSION
    assert json.loads(locked)['language_version'] == language
    result, payload = run('test', '--frozen', '--list')
    assert result.returncode == 0, payload
    assert lock.read_bytes() == locked
    assert payload['result']['cases'][0]['id'] == 'test:example'
    if engine == 'seed':
        result, payload = run('test', '--frozen')
        assert result.returncode == 0, payload
        result = subprocess.run([*command, 'project', 'run', '--frozen'], cwd=project,
            env=environment, capture_output=True, text=True, timeout=120)
        assert result.returncode == 0, result.stdout + result.stderr
        assert result.stdout.strip() == 'Hello, Wysna!'
    # Native execution uses the installed SDK acceptance gate with real bundled Zig.
