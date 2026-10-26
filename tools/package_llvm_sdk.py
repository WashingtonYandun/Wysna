#!/usr/bin/env python3
"""Package a relocatable, offline Seed LLVM SDK with verified native tools."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common.Version import WYSNA_VERSION
from compiler.LlvmSdk import PLATFORMS, host_platform, query_layout, run_tool
from tools.package_components import download, extract, sha256, unpack_seed
from tools.ci_conformance import source_fingerprint
from tooling.ToolResult import tool_result, diagnostic_record

LICENSE = {'url': 'https://raw.githubusercontent.com/llvm/llvm-project/llvmorg-20.1.8/llvm/LICENSE.TXT',
           'sha256': '8d85c1057d742e597985c7d4e6320b015a9139385cff4cbae06ffc0ebe89afee'}


def extract_llvm_tools(archive, destination):
    """Select tools/licenses with bounded writes; reject escaping paths and links."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    total = count = 0
    links = []
    with tarfile.open(archive, 'r|xz') as packed:
        for member in packed:
            count += 1
            path = PurePosixPath(member.name)
            if (path.is_absolute() or PureWindowsPath(member.name).drive or '..' in path.parts or '\\' in member.name):
                raise ValueError('Unsafe LLVM archive member: ' + member.name)
            if count > 200000:
                raise ValueError('LLVM archive has too many members')
            if len(path.parts) < 2:
                continue
            relative = PurePosixPath(*path.parts[1:])
            selected = (relative.as_posix() in ('bin/opt', 'bin/llc', 'bin/dsymutil')
                        or len(relative.parts) == 2 and relative.parts[0] == 'lib' and relative.name.startswith('libLLVM')
                        or 'LICENSE' in relative.name.upper())
            if not selected or member.isdir():
                continue
            target = destination / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.isfile():
                total += member.size
                if total > 2 * 1024 ** 3:
                    raise ValueError('Selected LLVM tools exceed their SDK size budget')
                with packed.extractfile(member) as source, target.open('wb') as output:
                    shutil.copyfileobj(source, output)
                target.chmod(member.mode & 0o755)
            elif member.issym():
                linked = target.parent / member.linkname
                if not linked.resolve().is_relative_to(destination.resolve()):
                    raise ValueError('Unsafe LLVM archive symlink')
                links.append((target, member.linkname))
            else:
                raise ValueError('Unsupported selected LLVM archive member: ' + member.name)
    for target, linked in links:
        target.symlink_to(linked)
    for name in ('opt', 'llc'):
        if not (destination / 'bin' / name).is_file():
            raise ValueError('LLVM archive is missing ' + name)


def verified_archive(component, cache, supplied):
    if supplied:
        supplied = Path(supplied).resolve()
        if sha256(supplied) != component['sha256']:
            raise ValueError('Supplied archive checksum does not match pinned component')
        return supplied
    return download(component, cache)


def assemble(output, cache, wheel, *, llvm_archive=None, zig_archive=None, source_commit=None):
    output, cache, wheel = Path(output).resolve(), Path(cache).resolve(), Path(wheel).resolve()
    if output.exists():
        raise ValueError('Choose a new SDK directory; existing installations are never overwritten')
    host = host_platform()
    if host not in PLATFORMS:
        raise ValueError('LLVM SDK is not pinned for this host')
    policy = PLATFORMS[host]
    cache.mkdir(parents=True, exist_ok=True)
    llvm = verified_archive(policy['llvm'], cache, llvm_archive)
    zig = verified_archive(policy['zig'], cache, zig_archive)
    license_path = download(LICENSE, cache)
    extracted = cache / ('llvm-tools-' + policy['llvm']['sha256'])
    index = extracted / 'extraction.json'
    valid = False
    if index.is_file():
        try:
            record = json.loads(index.read_text())
            valid = record['archive_sha256'] == policy['llvm']['sha256'] and all(sha256(extracted / path) == digest for path, digest in record['files'].items())
        except (OSError, ValueError, KeyError):
            valid = False
    if not valid:
        with tempfile.TemporaryDirectory(prefix='llvm-extract-', dir=cache) as temporary:
            pending = Path(temporary) / 'tools'
            extract_llvm_tools(llvm, pending)
            files = {path.relative_to(pending).as_posix(): sha256(path) for path in pending.rglob('*') if path.is_file()}
            (pending / 'extraction.json').write_text(json.dumps({'archive_sha256': policy['llvm']['sha256'], 'files': files}))
            if extracted.exists():
                shutil.rmtree(extracted)
            pending.replace(extracted)
    before = source_fingerprint()
    commit = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip()
    if source_commit and source_commit != commit:
        raise ValueError('Requested source commit does not match checkout')
    dirty = bool(subprocess.run(['git', 'status', '--porcelain'], cwd=ROOT, check=True, capture_output=True, text=True).stdout.strip())
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='wysna-llvm-package-', dir=output.parent) as temporary:
        temporary = Path(temporary)
        stage = temporary / 'sdk'
        stage.mkdir()
        unpack_seed(wheel, stage / 'seed')
        shutil.copytree(extracted, stage / 'toolchain/llvm', symlinks=True, ignore=shutil.ignore_patterns('extraction.json'))
        extract(zig, temporary / 'zig')
        roots = list((temporary / 'zig').iterdir())
        if len(roots) != 1:
            raise ValueError('Zig archive must contain one toolchain root')
        shutil.move(str(roots[0]), stage / 'toolchain/zig')
        shutil.copy2(license_path, stage / 'toolchain/llvm/LICENSE.TXT')
        shutil.copy2(ROOT / 'LICENSE', stage / 'LICENSE')
        launcher_source = (ROOT / 'packaging/llvm/launcher.py').read_text(encoding='utf-8')
        template = tool_result('sdk-bootstrap', 'toolchain_error', 3,
                               diagnostics=[diagnostic_record('W5001', 'llvm-sdk', '')],
                               invocation_id='bootstrap-template',
                               target={'profile': 'llvm-native-v1', 'triple': policy['triple']})
        marker = 'BOOTSTRAP_ERROR_TEMPLATE = None'
        if launcher_source.count(marker) != 1:
            raise ValueError('SDK bootstrap result marker is missing or ambiguous')
        (stage / 'launcher.py').write_text(launcher_source.replace(marker, 'BOOTSTRAP_ERROR_TEMPLATE = ' + repr(template)), encoding='utf-8')
        (stage / 'bin').mkdir()
        launcher = stage / 'bin/wysna'
        launcher.write_text('''#!/bin/sh
exec "${WYSNA_PYTHON:-python3}" -I -c 'import runpy,sys; from pathlib import Path; sys.dont_write_bytecode=True; root=Path(sys.argv.pop(1)).resolve().parent.parent; runpy.run_path(str(root/"launcher.py"),run_name="__main__")' "$0" "$@"
''', encoding='utf-8')
        launcher.chmod(0o755)
        (stage / 'THIRD_PARTY.txt').write_text('LLVM 20.1.8: Apache-2.0 WITH LLVM-exception; see toolchain/llvm/LICENSE.TXT.\n'
                                                'Zig 0.15.2 and bundled libraries: see toolchain/zig/LICENSE and notices in that tree.\n'
                                                'Python 3.10+ is a host requirement of Wysna Seed and is not bundled.\n', encoding='utf-8')
        layout = query_layout(stage / 'toolchain/llvm/bin/opt', policy['triple'])
        files = {path.relative_to(stage).as_posix(): sha256(path) for path in sorted(stage.rglob('*')) if path.is_file()}
        manifest = {'schema': 'wysna.llvm-sdk/1', 'version': WYSNA_VERSION, 'platform': host,
                    'profile': 'llvm-native-v1', 'implementation': 'seed', 'python_requirement': '>=3.10',
                    'host_requirement': 'macOS 14+' if host.endswith('macos') else 'Linux x64; acceptance on Ubuntu 24.04',
                    'llvm_version': '20.1.8', 'runtime_abi': 2, 'triple': policy['triple'], 'data_layout': layout,
                    'zig_target': policy['zig_target'], 'components': {key: policy[key] for key in ('llvm', 'zig')},
                    'source_commit': commit, 'source_dirty': dirty, 'source_provenance': before,
                    'seed_wheel_sha256': sha256(wheel), 'files': files,
                    'installed_bytes': sum(path.stat().st_size for path in stage.rglob('*') if path.is_file())}
        if before != source_fingerprint():
            raise ValueError('SDK source inputs changed while packaging; retry with a frozen tree')
        (stage / 'llvm-sdk.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
        run_tool([sys.executable, '-I', stage / 'launcher.py', 'version'])
        stage.replace(output)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--cache', type=Path, default=ROOT / '.wysna_cache/llvm-downloads')
    parser.add_argument('--llvm-archive', type=Path)
    parser.add_argument('--zig-archive', type=Path)
    parser.add_argument('--source-commit')
    parser.add_argument('--archive', action='store_true')
    args = parser.parse_args()
    record = assemble(args.output, args.cache, args.wheel, llvm_archive=args.llvm_archive,
                      zig_archive=args.zig_archive, source_commit=args.source_commit)
    if args.archive:
        archive = shutil.make_archive(str(args.output.resolve()), 'gztar', root_dir=args.output.parent, base_dir=args.output.name)
    else:
        archive = None
    print(json.dumps({'sdk': str(args.output), 'archive': archive, 'installed_bytes': record['installed_bytes']}))


if __name__ == '__main__':
    main()
