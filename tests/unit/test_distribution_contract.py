"""Host failures must remain actionable outside a writable source checkout."""
import json
from contextlib import redirect_stderr, redirect_stdout
import io
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

import main
from common.Platform import compiler_command, executable_name
from error.Error import ToolchainError, WysnaError


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


if __name__ == '__main__':
    test_compiler_configuration_and_unavailable_command()
    test_unavailable_compiler_is_structured_and_check_remains_available()
    test_missing_selfhost_sources_fail_before_any_cache_write()
    test_benchmark_checkout_requirement_precedes_bootstrap()
    print('Distribution: compiler configuration, structured host errors and source-only commands passed')
