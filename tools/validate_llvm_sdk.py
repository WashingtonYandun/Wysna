#!/usr/bin/env python3
"""Accept an installed LLVM SDK without checkout imports, LLVM bindings or host CC."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

PROGRAM = '''import "std/json" as json
struct Point { x: int, y: int }
fn optional(value: int?) -> int { value ?? 0 }
let original = [20, 21]
let mut changed = original
changed.append(42)
let captured = 1
let add: fn(int) -> int = |value: int| => value + captured
let point = Point { x: optional(20), y: add(21) }
let number = json.number(str(point.x + point.y)) else error { json.null() }
let encoded = json.stringify(json.object({"answer": number})) else error { "error" }
print(original[1], changed[2], encoded)
if len(args()) > 1 { print(args()[1], args()[2]) }
'''


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def snapshot(root):
    return {path.relative_to(root).as_posix(): (digest(path), stat.S_IMODE(path.stat().st_mode))
            for path in root.rglob('*') if path.is_file()}


def validate(sdk, report, source_commit=None, forbid_source=None):
    sdk = Path(sdk).resolve()
    manifest = json.loads((sdk / 'llvm-sdk.json').read_text(encoding='utf-8'))
    if source_commit and (manifest['source_commit'] != source_commit or manifest['source_dirty']):
        raise AssertionError('Installed SDK does not originate from the clean requested commit')
    if forbid_source and sdk.is_relative_to(Path(forbid_source).resolve()):
        raise AssertionError('Consumer SDK must be outside the forbidden checkout')
    records = []
    original_modes = {path: stat.S_IMODE(path.stat().st_mode) for path in sdk.rglob('*') if not path.is_symlink()}
    original_modes[sdk] = stat.S_IMODE(sdk.stat().st_mode)
    try:
        for path, mode in original_modes.items():
            path.chmod(mode & ~0o222)
        before = snapshot(sdk)
        with tempfile.TemporaryDirectory(prefix='wysna LLVM consumer ü ') as temporary:
            root = Path(temporary)
            empty = root / 'empty-path'
            empty.mkdir()
            environment = {**os.environ, 'PATH': str(empty), 'CC': '/missing/host-compiler',
                           'WYSNA_PYTHON': sys.executable, 'PYTHONPATH': '', 'PYTHONNOUSERSITE': '1',
                           'HOME': str(root / 'home'), 'WYSNA_CACHE_HOME': str(root / 'user-cache'),
                           'WYSNA_COMPILER_CACHE_HOME': str(root / 'object-cache'), 'WYSNA_MEM_CHECK': '1'}
            environment.pop('WYSNA_LLVM_SDK', None)
            source = root / 'program ü.wysna'
            source.write_text(PROGRAM, encoding='utf-8')
            link = root / 'wysna ü'
            link.symlink_to(sdk / 'bin/wysna')

            def run(arguments, expected=0, launcher=link):
                command = [str(launcher), *map(str, arguments)]
                result = subprocess.run(command, cwd=root, env=environment, capture_output=True,
                                        text=True, timeout=180)
                records.append({'command': command, 'exit_code': result.returncode,
                                'stdout': result.stdout, 'stderr': result.stderr})
                if result.returncode != expected:
                    raise AssertionError(records[-1])
                return result

            # Prove that the installed Seed resolves only SDK modules and never imports LLVM bindings.
            probe = "import sys,runpy; from pathlib import Path; root=Path(sys.argv.pop(1)); sys.path.insert(0,str(root/'seed')); import compiler.LlvmToolchain as t; assert Path(t.__file__).is_relative_to(root); assert 'llvmlite' not in sys.modules"
            subprocess.run([sys.executable, '-I', '-c', probe, str(sdk)], cwd=root, check=True, env=environment, timeout=30)
            assert 'LLVM 20.1.8' in run(['version']).stdout
            check = run(['check', source, '--output-format=json-v1'])
            assert json.loads(check.stdout)['status'] == 'ok'
            ir = root / 'program.ll'
            run(['emit-llvm', source, '-o', ir])
            assert 'target triple' in ir.read_text() and 'wysna_llvm_construct' in ir.read_text()
            for profile in ('dev', 'release', 'size'):
                binary = root / ('program-' + profile)
                result = run(['build', source, '--profile=' + profile, '-o', binary, '--stats'])
                metadata = json.loads(result.stderr)
                assert metadata['target']['driver'] == 'sdk-tools' and metadata['target']['binding_version'] is None
                execution = subprocess.run([str(binary)], cwd=root, env=environment,
                                           capture_output=True, text=True, timeout=30)
                assert (execution.returncode, execution.stdout, execution.stderr) == (0, '21 42 {"answer":42}\n', ''), execution
            sidecar = root / 'run.json'
            assert run(['run', source, '--report-json', sidecar]).stdout == '21 42 {"answer":42}\n'
            assert json.loads(sidecar.read_text())['status'] == 'ok'
            forwarded = run(['run', source, '--', '--backend=c99', '--output-format=json-v1'])
            assert forwarded.stdout == '21 42 {"answer":42}\n--backend=c99 --output-format=json-v1\n'
            protected = run(['emit-llvm', source, '-o', sdk / 'seed/main.py'], expected=1)
            assert 'W5002' in protected.stderr
            # Missing and corrupt components must fail before even the version command succeeds.
            for component in ('runtime/wysna_llvm_operations.def', 'compiler/LlvmSdk.py',
                              'tooling/ToolResult.py', 'common/Version.py'):
                damaged = sdk / 'seed' / component
                saved = damaged.read_bytes()
                damaged.chmod(0o644)
                try:
                    damaged.write_bytes(saved + b'\0')
                    result = run(['version', '--output-format=json-v1'], expected=3)
                    diagnostic = json.loads(result.stdout)
                    assert diagnostic['schema_version'] == 'wysna.tool-result/1'
                    assert diagnostic['status'] == 'toolchain_error' and not result.stderr
                    assert diagnostic['diagnostics'][0]['code'] == 'W5001'
                finally:
                    damaged.write_bytes(saved)
                    damaged.chmod(before[damaged.relative_to(sdk).as_posix()][1])
            manifest_path = sdk / 'llvm-sdk.json'
            saved_manifest = manifest_path.read_bytes()
            manifest_mode = stat.S_IMODE(manifest_path.stat().st_mode)
            manifest_path.chmod(0o644)
            try:
                for malformed in ('[]', '{}', '{"files": []}'):
                    manifest_path.write_text(malformed)
                    rejected = json.loads(run(['version', '--output-format=json-v1'], expected=3).stdout)
                    assert rejected['diagnostics'][0]['code'] == 'W5001'
            finally:
                manifest_path.write_bytes(saved_manifest)
                manifest_path.chmod(manifest_mode)
            missing = sdk / 'toolchain/llvm/bin/llc'
            directory_mode = stat.S_IMODE(missing.parent.stat().st_mode)
            missing.parent.chmod(directory_mode | 0o200)
            hidden = missing.with_name('llc.saved')
            try:
                missing.rename(hidden)
                assert 'W5001' in run(['version'], expected=3).stderr
            finally:
                hidden.rename(missing)
                missing.parent.chmod(directory_mode)
            # Relocation and an atomic user-managed update of the command symlink.
            relocated = sdk.with_name(sdk.name + ' relocated ü')
            try:
                sdk.rename(relocated)
                link.unlink()
                link.symlink_to(relocated / 'bin/wysna')
                assert 'LLVM 20.1.8' in run(['version']).stdout
                assert run(['run', source]).stdout == '21 42 {"answer":42}\n'
            finally:
                if relocated.exists():
                    relocated.rename(sdk)
        assert snapshot(sdk) == before, 'Installed SDK contents or permissions changed during acceptance'
        result = {'schema': 'wysna.llvm-sdk-acceptance/1', 'status': 'passed', 'source_commit': manifest['source_commit'],
                  'source_dirty': manifest['source_dirty'], 'platform': manifest['platform'],
                  'installed_bytes': manifest['installed_bytes'], 'host_python': sys.version,
                  'host_compiler': 'unavailable', 'host_llvm': 'unavailable on PATH',
                  'read_only_unchanged': True, 'commands': records}
        Path(report).write_text(json.dumps(result, indent=2), encoding='utf-8')
        print('Installed LLVM SDK: offline managed programs, profiles, run reports, relocation, read-only files and corruption rejection passed')
        return result
    except Exception as error:
        Path(report).write_text(json.dumps({'schema': 'wysna.llvm-sdk-acceptance/1', 'status': 'failed',
                                          'error': str(error), 'commands': records}, indent=2), encoding='utf-8')
        raise
    finally:
        for path, mode in sorted(original_modes.items(), key=lambda item: len(item[0].parts)):
            if path.exists():
                path.chmod(mode)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--source-commit')
    parser.add_argument('--forbid-source')
    args = parser.parse_args()
    validate(args.sdk, args.report, args.source_commit, args.forbid_source)


if __name__ == '__main__':
    main()
