"""Host failures must remain actionable outside a writable source checkout."""
import base64
import csv
import hashlib
import json
from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import warnings
import zipfile
from unittest.mock import patch

import main
from common.Platform import compiler_command, executable_name
from error.Error import ToolchainError, WysnaError
from tools.package_components import unpack_seed
from project.Manifest import tomllib


def test_compiler_configuration_and_unavailable_command():
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        for configured in (str(directory / 'missing compiler'), '   ', '"unterminated'):
            with patch.dict(os.environ, CC=configured):
                try:
                    compiler_command()
                except ToolchainError as error:
                    assert error.code == 'W5001' and error.stage == 'toolchain'
                else:
                    raise AssertionError('Unavailable or malformed C compiler accepted')
        compiler = directory / executable_name('compiler with spaces')
        compiler.write_text('')
        compiler.chmod(0o755)
        with patch.dict(os.environ, CC=str(compiler)):
            assert compiler_command() == [str(compiler)]


def test_unavailable_compiler_is_structured_and_check_remains_available():
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        with patch.dict(os.environ, CC=str(directory / 'missing compiler')):
            with redirect_stdout(io.StringIO()):
                assert main.run_pipeline('print(7)', check_only=True) == 0
            output = io.StringIO()
            with redirect_stdout(output):
                assert main.run_pipeline('print(7)', build_bin_path=str(directory / 'program'), json_diagnostics=True) == 1
            diagnostic = json.loads(output.getvalue())
            assert diagnostic['code'] == 'W5001' and diagnostic['stage'] == 'toolchain'
            assert 'Install a C99 toolchain or set CC' in diagnostic['message']


def test_missing_selfhost_sources_fail_before_any_cache_write():
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        try:
            main.ensure_selfhost_compiler_binary(str(directory))
        except WysnaError as error:
            assert error.code == 'W2013'
        else:
            raise AssertionError('Missing selfhost sources accepted')
        assert not (directory / '.wysna_cache').exists()


def test_benchmark_checkout_requirement_precedes_bootstrap():
    with tempfile.TemporaryDirectory() as temporary:
        def forbidden_bootstrap(*args):
            raise AssertionError('Benchmark capability must be checked before bootstrap')
        output = io.StringIO()
        with patch.object(sys, 'argv', ['wysna', 'bench']), patch('tooling.CLI.get_repo_root', return_value=temporary):
            with patch('tooling.CLI.ensure_selfhost_compiler_binary', forbidden_bootstrap), redirect_stderr(output):
                try:
                    main.main()
                except SystemExit as error:
                    assert error.code == 1
                else:
                    raise AssertionError('Source-only benchmark accepted without checkout')
        assert output.getvalue() == 'wysna bench requires a source checkout\n'


def test_seed_wheel_validation_is_shared_and_precedes_any_extraction():
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        source = directory / 'source'
        source.mkdir()
        (source / 'main.py').write_text('print("current")\n', newline='\n')
        (source / 'README.md').write_text('current documentation: programación\n', encoding='utf-8', newline='\n')
        (source / 'LICENSE').write_text('license\n', newline='\n')
        (source / 'pyproject.toml').write_text('''[project]
name = "wysna"
version = "0.5.0"
readme = "README.md"
license = {file = "LICENSE"}
requires-python = ">=3.10"
[project.optional-dependencies]
llvm = ["llvmlite==0.46.0"]
[project.scripts]
wysna = "main:main"
[tool.setuptools]
py-modules = ["main"]
packages = []
[tool.setuptools.package-data]
''')
        for kind in ('valid', 'legacy_metadata', 'stale', 'duplicate', 'escape', 'unexpected', 'missing',
                     'stale_metadata', 'bad_record', 'entrypoint', 'extra_entrypoint',
                     'python_requirement', 'extra_dependency', 'wheel_tag', 'top_level',
                     'unexpected_header', 'duplicate_name', 'unexpected_platform'):
            wheel = directory / (kind + '.whl')
            metadata = 'wysna-0.5.0.dist-info/'
            files = {
                'main.py': b'print("stale")\n' if kind == 'stale' else b'print("current")\n',
                metadata + 'METADATA': (('Metadata-Version: 2.1\n' if kind == 'legacy_metadata'
                                         else 'Metadata-Version: 2.4\n') + 'Name: wysna\n'
                    + ('Name: other\n' if kind == 'duplicate_name' else '') + 'Version: 0.5.0\n'
                    'License: license\nLicense-File: LICENSE\nDynamic: license-file\n'
                    'Description-Content-Type: text/markdown\nRequires-Python: '
                    + ('>=3.5' if kind == 'python_requirement' else '>=3.10')
                    + '\nProvides-Extra: llvm\nRequires-Dist: '
                    + ('llvmlite (==0.46.0) ; extra == "llvm"\n' if kind == 'legacy_metadata'
                       else 'llvmlite==0.46.0; extra == "llvm"\n')
                    + ('Platform: UNKNOWN\n' if kind == 'legacy_metadata' else '')
                    + ('Platform: malicious\n' if kind == 'unexpected_platform' else '')
                    + ('Requires-Dist: malicious==1\n' if kind == 'extra_dependency' else '')
                    + ('Requires-External: unknown\n' if kind == 'unexpected_header' else '')
                    + '\n' + ('stale documentation\n' if kind == 'stale_metadata'
                               else 'current documentation: programación\n')
                    + ('\n\n' if kind == 'legacy_metadata' else '')).encode('utf-8'),
                metadata + 'WHEEL': ('Wheel-Version: 1.0\nGenerator: setuptools (84.0.0)\n'
                    'Root-Is-Purelib: true\nTag: ' + ('cp314-none-any' if kind == 'wheel_tag' else 'py3-none-any')
                    + '\n\n').encode(),
                metadata + 'top_level.txt': b'wrong\n' if kind == 'top_level' else b'main\n',
                metadata + 'entry_points.txt': b'[console_scripts]\nwysna = ' + (
                    b'wrong:main\n' if kind == 'entrypoint' else b'main:main\n') + (
                    b'[other]\nplugin = unexpected:main\n' if kind == 'extra_entrypoint' else b''),
                metadata + 'licenses/LICENSE': b'license\n',
            }
            if kind == 'missing':
                del files['main.py']
            if kind == 'unexpected':
                files['runtime/rogue.py'] = b'unexpected\n'
            record = io.StringIO(newline='')
            writer = csv.writer(record, lineterminator='\n')
            for name, data in files.items():
                digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()
                writer.writerow((name, 'sha256=' + digest, str(len(data))))
            writer.writerow((metadata + 'RECORD', '', ''))
            files[metadata + 'RECORD'] = record.getvalue().encode()
            if kind == 'bad_record':
                files[metadata + 'WHEEL'] += b'changed after RECORD\n'
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                with zipfile.ZipFile(wheel, 'w') as packed:
                    for name, data in files.items():
                        packed.writestr(name, data)
                    if kind == 'duplicate':
                        packed.writestr('main.py', 'print("current")\n')
                    if kind == 'escape':
                        packed.writestr('../outside.py', 'unsafe')
            output = directory / (kind + '-output')
            if kind in ('valid', 'legacy_metadata'):
                unpack_seed(wheel, output, source)
                assert (output / 'main.py').read_bytes() == (source / 'main.py').read_bytes()
            else:
                try:
                    unpack_seed(wheel, output, source)
                except ValueError:
                    pass
                else:
                    raise AssertionError('Unsafe or stale wheel accepted: ' + kind)
                assert not output.exists()
        assert not (directory / 'outside.py').exists()


def test_every_packaged_text_input_has_portable_checkout_bytes():
    root = Path(__file__).resolve().parents[2]
    configuration = tomllib.loads((root / 'pyproject.toml').read_text(encoding='utf-8'))['tool']['setuptools']
    paths = {root / (name + '.py') for name in configuration['py-modules']}
    for name in configuration['packages']:
        paths.update((root / name.replace('.', '/')).glob('*.py'))
    for name, patterns in configuration['package-data'].items():
        for pattern in patterns:
            paths.update((root / name.replace('.', '/')).glob(pattern))
    # The installer also compares the license embedded in the wheel with the
    # checkout, so its bytes need the same cross-platform guarantee.
    paths.add(root / 'LICENSE')
    text_paths = sorted(path.relative_to(root).as_posix() for path in paths if b'\n' in path.read_bytes())
    result = subprocess.run(['git','check-attr','eol','--',*text_paths],cwd=root,
                            capture_output=True,text=True,check=True,timeout=30)
    unnormalized = [line for line in result.stdout.splitlines() if not line.endswith(': eol: lf')]
    assert not unnormalized, 'Packaged source bytes change on Windows checkout: ' + repr(unnormalized)


def test_root_license_checkout_stays_lf_with_autocrlf():
    root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='wysna-license-checkout-') as temporary:
        checkout = Path(temporary)
        for name in ('.gitattributes', 'LICENSE'):
            (checkout / name).write_bytes((root / name).read_bytes())
        subprocess.run(['git', 'init', '-q'], cwd=checkout, check=True, capture_output=True)
        subprocess.run(['git', '-c', 'core.autocrlf=true', 'add', '--', '.gitattributes', 'LICENSE'],
                       cwd=checkout, check=True, capture_output=True)
        (checkout / 'LICENSE').unlink()
        subprocess.run(['git', '-c', 'core.autocrlf=true', 'checkout', '--', 'LICENSE'],
                       cwd=checkout, check=True, capture_output=True)
        assert (checkout / 'LICENSE').read_bytes() == (root / 'LICENSE').read_bytes()


if __name__ == '__main__':
    test_compiler_configuration_and_unavailable_command()
    test_unavailable_compiler_is_structured_and_check_remains_available()
    test_missing_selfhost_sources_fail_before_any_cache_write()
    test_benchmark_checkout_requirement_precedes_bootstrap()
    test_seed_wheel_validation_is_shared_and_precedes_any_extraction()
    test_every_packaged_text_input_has_portable_checkout_bytes()
    print('Distribution: compiler configuration, structured host errors and source-only commands passed')
