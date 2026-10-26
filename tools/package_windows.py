#!/usr/bin/env python3
"""Assemble an offline Windows Wysna distribution from a wheel and pinned runtimes."""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools.package_components import download, extract, sha256, unpack_seed
COMPONENTS = {
    'python': {
        'version': '3.14.8',
        'url': 'https://www.python.org/ftp/python/3.14.8/python-3.14.8-embed-amd64.zip',
        'sha256': 'a93abe456ab01bd96d7a085b3cdb6566b3063f4241360d114142fbdb07f0a310',
    },
    'zig': {
        'version': '0.15.2',
        'url': 'https://ziglang.org/download/0.15.2/zig-x86_64-windows-0.15.2.zip',
        'sha256': '3a0ed1e8799a2f8ce2a6e6290a9ff22e6906f8227865911fb7ddedc3cc14cb0c',
    },
}


def assemble(wheel, output, cache, launcher_compiler=None, source_commit=None, *, entrypoint='main.py'):
    wheel, output, cache = Path(wheel).resolve(), Path(output).resolve(), Path(cache).resolve()
    if output.exists():
        raise ValueError('Choose a new output directory; existing files are never replaced')
    cache.mkdir(parents=True, exist_ok=True)
    archives = {name: download(component, cache) for name, component in COMPONENTS.items()}
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='wysna-windows-package-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'bundle'
        unpack_seed(wheel, stage)
        for required in ('main.py', 'common/Version.py', 'runtime/wysna_runtime.h', 'stdlib/text.wysna'):
            if not (stage / required).is_file():
                raise ValueError('Wheel is missing ' + required)
        metadata = list(stage.glob('*.dist-info/METADATA'))
        if len(metadata) != 1:
            raise ValueError('Expected exactly one wheel metadata record')
        from email.parser import Parser
        version = Parser().parsestr(metadata[0].read_text(encoding='utf-8'))['Version']
        extract(archives['python'], stage / 'python')
        (stage / 'python/python314._pth').write_text('python314.zip\n.\n..\n', encoding='utf-8')
        extract(archives['zig'], stage / 'toolchain')
        (stage / 'toolchain/zig-x86_64-windows-0.15.2').rename(stage / 'toolchain/zig')
        compiler = Path(launcher_compiler).resolve() if launcher_compiler else stage / 'toolchain/zig/zig.exe'
        if entrypoint not in ('main.py', 'wysna_entry.py'):
            raise ValueError('Unknown installed entrypoint')
        subprocess.run([str(compiler), 'cc', '-target', 'x86_64-windows-gnu', '-mcpu=baseline',
                        '-DWYSNA_ENTRYPOINT=L"' + entrypoint + '"',
                        '-O2', '-municode', str(ROOT / 'packaging/windows/launcher.c'),
                        '-o', str(stage / 'wysna.exe')], check=True, timeout=300)
        shutil.copy2(ROOT / 'LICENSE', stage / 'LICENSE')
        shutil.copy2(ROOT / 'packaging/windows/open-terminal.cmd', stage / 'open-terminal.cmd')
        (stage / 'THIRD_PARTY.txt').write_text(
            'This distribution includes CPython under the PSF license (python/LICENSE.txt)\n'
            'and Zig/LLVM and their bundled libraries (toolchain/zig/LICENSE and '
            'toolchain/zig/lib).\n'
            'The complete bundled toolchain is retained, including its license notices.\n', encoding='utf-8')
        manifest = {'version': version, 'profile': 'wysna', 'platform': 'windows-x64',
                    'source_commit': source_commit, 'components': COMPONENTS,
                    'wheel': {'name': wheel.name, 'sha256': sha256(wheel)},
                    'source_only_commands': ['test', 'bench', '--backend=selfhost']}
        (stage / 'windows-bundle.json').write_text(json.dumps(manifest, indent=2) + '\n', encoding='utf-8')
        stage.rename(output)
    print(json.dumps({'bundle': str(output), 'version': version, 'components': COMPONENTS}, indent=2))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cache', type=Path, default=ROOT / '.wysna_cache/windows-downloads')
    parser.add_argument('--launcher-compiler', type=Path)
    parser.add_argument('--source-commit')
    args = parser.parse_args()
    assemble(args.wheel, args.output, args.cache, args.launcher_compiler, args.source_commit)


if __name__ == '__main__':
    main()
