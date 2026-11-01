#!/usr/bin/env python3
"""Build a relocatable Seed installation with private Python and a pinned C toolchain."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common.Version import WYSNA_VERSION
from tooling.ToolResult import diagnostic_record, tool_result
from tools.package_components import download, extract, sha256, unpack_seed

PYTHONS = json.loads((ROOT / 'packaging/install/python-components.json').read_text())
ZIGS = json.loads((ROOT / 'packaging/native/components.json').read_text())['zig']


def host_platform():
    system = {'Darwin': 'macos', 'Linux': 'linux', 'Windows': 'windows'}.get(platform.system())
    machine = {'arm64': 'aarch64', 'aarch64': 'aarch64', 'AMD64': 'x86_64', 'x86_64': 'x86_64'}.get(platform.machine())
    value = str(machine) + '-' + str(system)
    if value != 'x86_64-windows' and value not in PYTHONS:
        raise ValueError('No installed distribution is pinned for ' + value)
    return value


def extract_python(archive, destination):
    """Permit only internal Python symlinks, after extracting regular files safely."""
    destination = Path(destination)
    if destination.exists() or destination.is_symlink():
        raise ValueError('Python extraction requires a new directory')
    destination = destination.resolve()
    with tarfile.open(archive, 'r:*') as stream:
        members = stream.getmembers()
        if len(members) > 100000 or sum(member.size for member in members) > 2 * 1024**3:
            raise ValueError('Python archive exceeds package bounds')
        names = set()
        links = set()
        for member in members:
            name = PurePosixPath(member.name)
            if (name.is_absolute() or PureWindowsPath(member.name).drive or '\\' in member.name
                    or '..' in name.parts or not name.parts or name.parts[0] != 'python'
                    or not (member.isfile() or member.isdir() or member.issym())):
                raise ValueError('Unsafe Python archive member: ' + member.name)
            if member.name in names:
                raise ValueError('Duplicate Python archive member')
            names.add(member.name)
            if member.issym():
                links.add(name)
                target = member.linkname
                resolved = PurePosixPath(os.path.normpath(str(name.parent / target)))
                if (PurePosixPath(target).is_absolute() or PureWindowsPath(target).drive or '\\' in target
                        or not resolved.parts or resolved.parts[0] != 'python' or '..' in resolved.parts):
                    raise ValueError('Python symlink escapes its component')
        if any(parent in links for name in names for parent in PurePosixPath(name).parents):
            raise ValueError('Python archive writes through a symlink')
        destination.mkdir(parents=True)
        for member in members:
            path = destination / member.name
            if member.isdir():
                path.mkdir(parents=True, exist_ok=True)
            elif member.isfile():
                path.parent.mkdir(parents=True, exist_ok=True)
                with stream.extractfile(member) as source, path.open('xb') as output:
                    shutil.copyfileobj(source, output)
                path.chmod(member.mode & 0o777)
        for member in members:
            if member.issym():
                path = destination / member.name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.symlink_to(member.linkname)
        for name in links:
            if not (destination / name).resolve().is_relative_to(destination / 'python'):
                raise ValueError('Python symlink chain escapes its component')


def assemble(wheel, output, cache, *, source_commit=None):
    wheel, output, cache = Path(wheel).resolve(), Path(output).resolve(), Path(cache).resolve()
    if output.exists():
        raise ValueError('Package into a new directory; preserve existing installations')
    host = host_platform()
    cache.mkdir(parents=True, exist_ok=True)
    actual = subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip()
    dirty = bool(subprocess.check_output(['git', 'status', '--porcelain'], cwd=ROOT, text=True).strip())
    if source_commit and (actual != source_commit or dirty):
        raise ValueError('Installed distributions require the clean requested source commit')
    components = {}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='wysna-distribution-', dir=output.parent) as temporary:
        temporary = Path(temporary)
        stage = temporary / 'bundle'
        if host == 'x86_64-windows':
            from tools.package_windows import assemble as windows_bundle
            record = windows_bundle(wheel, stage, cache, source_commit=actual, entrypoint='wysna_entry.py')
            components.update(record['components'])
            python = 'python/python.exe'
            compiler = 'toolchain/zig/zig.exe'
            launcher = 'wysna.exe'
            target = 'x86_64-windows-gnu'
        else:
            unpack_seed(wheel, stage)
            python_archive = download(PYTHONS[host], cache)
            extract_python(python_archive, temporary / 'python-component')
            shutil.move(str(temporary / 'python-component/python'), stage / 'python')
            zig_archive = download(ZIGS[host], cache)
            extract(zig_archive, temporary / 'zig-component')
            roots = list((temporary / 'zig-component').iterdir())
            if len(roots) != 1:
                raise ValueError('Expected one Zig component directory')
            (stage / 'toolchain').mkdir()
            shutil.move(str(roots[0]), stage / 'toolchain/zig')
            (stage / 'bin').mkdir()
            python, compiler, launcher = 'python/bin/python3', 'toolchain/zig/zig', 'bin/wysna'
            target = ZIGS[host]['target']
            if host.endswith('linux'):
                # User programs can link declared system libraries; libc is still bundled by Zig.
                target = 'x86_64-linux-gnu.2.28'
            environment = dict(os.environ, ZIG_GLOBAL_CACHE_DIR=str(cache / 'zig-global'), ZIG_LOCAL_CACHE_DIR=str(temporary / 'zig-local'))
            subprocess.run([str(stage / compiler), 'cc', '-target', target, '-mcpu=baseline', '-std=c99', '-O2',
                            str(ROOT / 'packaging/install/launcher.c'), '-o', str(stage / launcher)],
                           env=environment, capture_output=True, text=True, check=True, timeout=300)
            components.update(python=PYTHONS[host], zig=ZIGS[host])
        error = tool_result('bootstrap', 'toolchain_error', 3,
                           diagnostics=[diagnostic_record('W5001', 'configuration', 'Invalid Wysna installation')])
        entry = (ROOT / 'packaging/install/entry.py').read_text().replace('__WYSNA_BOOTSTRAP_ERROR__', repr(error))
        (stage / 'wysna_entry.py').write_text(entry, encoding='utf-8')
        shutil.copy2(ROOT / 'LICENSE', stage / 'LICENSE')
        (stage / 'THIRD_PARTY.txt').write_text('This installation includes CPython and Zig/LLVM under their original licenses.\n'
                                            'Python notices are retained in python; Zig notices in toolchain/zig.\n', encoding='utf-8')
        files = {path.relative_to(stage).as_posix(): sha256(path) for path in sorted(stage.rglob('*')) if path.is_file()}
        record = {'schema': 'wysna.installation/1', 'implementation': 'seed', 'version': WYSNA_VERSION,
                  'platform': host, 'source_commit': actual, 'source_dirty': dirty,
                  'python': python, 'compiler': compiler, 'launcher': launcher, 'target': target,
                  'created_at': datetime.now(timezone.utc).isoformat(), 'components': components,
                  'wheel_sha256': sha256(wheel), 'files': files,
                  'source_only_commands': ['test', 'bench', '--engine=compiler'],
                  'optional_external_dependencies': ['libcurl development files for native HTTP', 'declared C FFI libraries']}
        (stage / 'installation.json').write_text(json.dumps(record, indent=2) + '\n')
        stage.rename(output)
    return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, default=ROOT / '.wysna_cache/installation-downloads')
    parser.add_argument('--source-commit')
    args = parser.parse_args()
    record = assemble(args.wheel, args.output, args.cache, source_commit=args.source_commit)
    print(json.dumps({key: record[key] for key in ('version', 'platform', 'source_commit', 'source_dirty')}))


if __name__ == '__main__':
    main()
