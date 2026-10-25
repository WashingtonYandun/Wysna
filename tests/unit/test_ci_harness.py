"""Regression contracts for CI failures, coverage and bounded processes."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from tests import engine_harness as harness


def run_ci_harness_tests():
    from tests.unit.test_conformance_runner import test_conformance_runner_contracts
    test_conformance_runner_contracts()
    from tests.unit.test_ci_workflow import test_ci_workflow_contracts
    test_ci_workflow_contracts()
    assert harness.diagnostic('[W4001] runtime error') == 'W4001'
    assert not harness.diagnostic('[W123456] invented error')
    reference = harness.EngineResult('interpreter', stdout='expected\n')
    assert harness.compare([reference, harness.EngineResult('hir', stdout='different\n')])[0] is False
    assert harness.compare([reference, harness.EngineResult('ast', stdout='expected\n', exit_code=1)], 'expected\n')[0] is False
    assert harness.compare([reference, harness.EngineResult('selfhost', stdout='expected\n', infrastructure_error='sanitizer failure')])[0] is False
    assert not harness.unsupported_reason('new_generic_named_core_fixture.wysna', 'selfhost')
    assert not harness.unsupported_reason('tests/runtime/generic_function.wysna', 'selfhost')
    assert not harness.unsupported_reason('tests/modules/checked_session/main.wysna', 'selfhost')
    assert not harness.unsupported_reason('tests/runtime/generic_function.wysna', 'hir')
    with tempfile.TemporaryDirectory() as temporary:
        directory = Path(temporary)
        timed = harness.execute([sys.executable, '-c', 'while True: pass'], directory, 'timeout', 0.1)
        assert timed['returncode'] == 124 and timed['timeout'] == 0.1
        started = time.monotonic()
        parent = 'import subprocess, sys; subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"]); print("child started", flush=True); exec("while True: pass")'
        tree = harness.execute([sys.executable, '-c', parent], directory, 'process-tree', 0.2)
        assert tree['returncode'] == 124 and tree['termination_returncode'] < 0
        assert time.monotonic() - started < 3, 'Compiler descendant survived the timeout'
        signaled = harness.execute([sys.executable, '-c', 'import os, signal; os.kill(os.getpid(), signal.SIGTERM)'], directory, 'signal', 10)
        assert signaled['returncode'] == -15
        assert json.loads((directory / 'signal.json').read_text())['command']
        original = harness.run_engine
        previous = os.environ.get('WYSNA_CI_ARTIFACTS')
        os.environ['WYSNA_CI_ARTIFACTS'] = str(directory / 'artifacts')
        try:
            harness.run_engine = lambda engine, *args: harness.EngineResult(engine, stdout='ok\n')
            assert harness.verify('', 'tests/runtime/generic_function.wysna', '', directory, 'ok\n')[0]
            assert harness.verify('', 'tests/runtime/generic_function.wysna', '', directory, 'ok\n', strict=True)[0]
            feature = harness.feature_for('tests/runtime/generic_function.wysna')
            old_reason = harness.PROFILES['features'][feature].get('selfhost')
            harness.PROFILES['features'][feature]['selfhost'] = 'synthetic unsupported row for harness test'
            try:
                assert not harness.verify('', 'tests/runtime/generic_function.wysna', '', directory, 'ok\n', strict=True)[0]
            finally:
                if old_reason is None:
                    harness.PROFILES['features'][feature].pop('selfhost', None)
                else:
                    harness.PROFILES['features'][feature]['selfhost'] = old_reason
            assert not harness.verify('', 'invalid.wysna', '', directory, expect_rejection=True)[0]
        finally:
            harness.run_engine = original
            if previous is None:
                os.environ.pop('WYSNA_CI_ARTIFACTS')
            else:
                os.environ['WYSNA_CI_ARTIFACTS'] = previous
    with tempfile.TemporaryDirectory() as temporary:
        original_execute = harness.execute
        commands = []
        def simulate(command, directory, phase, timeout, env=None):
            commands.append(command)
            return {'returncode': 0, 'stdout': '',
                    'stderr': 'ERROR: AddressSanitizer: heap-use-after-free' if phase == 'run' else ''}
        try:
            harness.execute = simulate
            result = harness.run_engine('selfhost', '', 'probe.wysna', '/standalone-selfhost', temporary, True)
            assert commands[0][0] == '/standalone-selfhost'
            assert result.infrastructure_error == 'memory or sanitizer failure'
            assert not harness.compare([result], '')[0]
        finally:
            harness.execute = original_execute
    print('CI harness: output, exit status, sanitizer failure, timeout, signal, direct rejection and release coverage contracts passed')


if __name__ == '__main__':
    run_ci_harness_tests()
