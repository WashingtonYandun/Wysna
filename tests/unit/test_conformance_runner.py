"""The complete conformance runner must preserve failures and fixture counts."""
from contextlib import ExitStack, redirect_stdout
import io
import os
from pathlib import Path
from types import SimpleNamespace
import tempfile
from unittest.mock import patch


def _run_fixture(*, native_failure=False, missing_expected=False, include_support=False):
    import run_conformance as runner

    with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
        root = Path(temporary)
        (root / 'selfhost').mkdir()
        (root / 'selfhost/compiler.wysna').write_text('fn main() {}\n', encoding='utf-8')
        (root / 'tests/types/valid').mkdir(parents=True)
        (root / 'tests/types/valid/entry.wysna').write_text('fn main() {}\n', encoding='utf-8')
        (root / 'examples').mkdir()
        (root / 'examples/showcase.wysna').write_text('fn main() { print("ok") }\n', encoding='utf-8')
        if not missing_expected:
            (root / 'examples/showcase.expected').write_text('ok\n', encoding='utf-8')
        if include_support:
            (root / 'examples/http_json_client.wysna').write_text('fn main() {}\n', encoding='utf-8')
        stack.enter_context(patch.object(runner, '__file__', str(root / 'run_conformance.py')))
        stack.enter_context(patch('tests.formatter_contract.formatter_paths', return_value=[]))
        stack.enter_context(patch.object(runner, 'sha256_file', return_value='same-stage-hash'))
        for name in vars(runner):
            if name.startswith('run_') and name not in ('run_suite',):
                stack.enter_context(patch.object(runner, name, return_value=True))
        stack.enter_context(patch.object(runner, 'test_split_conformance', return_value=True))
        for module, name in (
            ('test_d04_semantic_fuzzing', 'run_all'),
            ('test_d05_telemetry_benchmarks', 'run_all'),
            ('test_expansion_contracts', 'run_expansion_tests'),
            ('test_audit_remediation', 'run_audit_tests'),
            ('test_mir', 'run_mir_tests'),
            ('test_audit_completion', 'run_completion_tests'),
            ('test_audit_readiness', 'run_readiness_tests'),
            ('test_audit_findings', 'run_findings_tests'),
            ('test_standard_toolkit', 'run_toolkit_tests'),
            ('test_foundations', 'run_foundations_tests'),
        ):
            stack.enter_context(patch('tests.unit.' + module + '.' + name, return_value=True))

        def execute(command, **options):
            if native_failure and command[0].endswith('ex_showcase'):
                return SimpleNamespace(returncode=1, stdout='', stderr='synthetic native failure')
            return SimpleNamespace(returncode=0, stdout='ok\n', stderr='')

        stack.enter_context(patch.object(runner.subprocess, 'run', side_effect=execute))
        output = io.StringIO()
        original_directory = os.getcwd()
        try:
            with redirect_stdout(output):
                runner.run_suite()
        except SystemExit as result:
            assert os.getcwd() == original_directory, 'Runner did not restore its caller directory'
            return result.code, output.getvalue()
        raise AssertionError('Runner did not report an exit status')


def test_conformance_runner_contracts():
    status, output = _run_fixture(include_support=True)
    assert status == 0, output
    assert 'Examples E2E Suite:  1/1' in output, output
    assert '[BOOTSTRAP/SELFHOST FAIL]' not in output, output
    for options in ({'native_failure': True}, {'missing_expected': True}):
        status, output = _run_fixture(**options)
        assert status == 1, output
        assert 'Examples E2E Suite:  0/1' in output, output
        assert 'FAILURES DETECTED.' in output, output
        assert 'PASSED (100%)' not in output, output


if __name__ == '__main__':
    test_conformance_runner_contracts()
    print('Conformance runner: support classification, native failure and missing expectation contracts passed')
