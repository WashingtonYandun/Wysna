"""Host failures must remain actionable outside a writable source checkout."""
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
        for kind in ('valid', 'stale', 'duplicate', 'escape'):
            wheel = directory / (kind + '.whl')
            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                with zipfile.ZipFile(wheel, 'w') as packed:
                    packed.writestr('main.py', 'print("stale")\n' if kind == 'stale' else 'print("current")\n')
                    if kind == 'duplicate':
                        packed.writestr('main.py', 'print("current")\n')
                    if kind == 'escape':
                        packed.writestr('../outside.py', 'unsafe')
            output = directory / (kind + '-output')
            if kind == 'valid':
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
    text_paths = sorted(path.relative_to(root).as_posix() for path in paths if b'\n' in path.read_bytes())
    result = subprocess.run(['git','check-attr','eol','--',*text_paths],cwd=root,
                            capture_output=True,text=True,check=True,timeout=30)
    unnormalized = [line for line in result.stdout.splitlines() if not line.endswith(': eol: lf')]
    assert not unnormalized, 'Packaged source bytes change on Windows checkout: ' + repr(unnormalized)


if __name__ == '__main__':
    test_compiler_configuration_and_unavailable_command()
    test_unavailable_compiler_is_structured_and_check_remains_available()
    test_missing_selfhost_sources_fail_before_any_cache_write()
    test_benchmark_checkout_requirement_precedes_bootstrap()
    test_seed_wheel_validation_is_shared_and_precedes_any_extraction()
    test_every_packaged_text_input_has_portable_checkout_bytes()
    print('Distribution: compiler configuration, structured host errors and source-only commands passed')
