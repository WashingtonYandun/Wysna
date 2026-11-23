"""Native worker graphs, fallback semantics, and fatal shutdown boundaries."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope='module')
def worker_probe(tmp_path_factory):
    compiler = shutil.which(os.environ.get('CC', 'clang')) or shutil.which('cc')
    if compiler is None:
        pytest.skip('A C99 compiler is required')
    executable = tmp_path_factory.mktemp('worker-transfer') / ('probe.exe' if os.name == 'nt' else 'probe')
    flags = ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if os.environ.get('WYSNA_SANITIZE') == '1' else []
    result = subprocess.run([compiler, '-std=c99', '-O1', *flags, '-I' + str(ROOT),
        str(ROOT / 'tests/runtime/worker_transfer.c'), '-o', str(executable)], capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    return executable


@pytest.mark.parametrize('workers', ['1', '4'])
def test_worker_graph_isolation_in_parallel_and_serial_fallback(worker_probe, workers):
    environment = dict(os.environ, WYSNA_MEM_CHECK='1', WYSNA_PARALLEL_THREADS=workers)
    result = subprocess.run([str(worker_probe)], env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'worker graphs isolated\n'


@pytest.mark.parametrize('kind', ['object', 'pointer', 'weak', 'nested', 'result'])
def test_worker_transfer_rejects_identity_and_opaque_values(worker_probe, kind):
    result = subprocess.run([str(worker_probe), kind], capture_output=True, text=True, timeout=10)
    assert result.returncode == 1, result.stderr
    assert '[W4003]' in result.stderr and 'Worker transfer rejects' in result.stderr


def test_worker_transfer_bounds_graph_depth(worker_probe):
    result = subprocess.run([str(worker_probe), 'depth'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 1, result.stderr
    assert '[W4003]' in result.stderr and '256' in result.stderr


def test_worker_trap_does_not_join_its_own_thread(worker_probe):
    result = subprocess.run([str(worker_probe), 'trap'], capture_output=True, text=True, timeout=10)
    assert result.returncode == 1, result.stderr
    assert '[W4003]' in result.stderr and 'worker trap exits without self-join' in result.stderr


def test_main_trap_does_not_wait_for_active_workers(worker_probe):
    result = subprocess.run([str(worker_probe), 'main-trap'], capture_output=True, text=True, timeout=5)
    assert result.returncode == 1, result.stderr
    assert '[W4003]' in result.stderr and 'main trap does not wait for workers' in result.stderr


def test_task_group_reaches_two_workers_and_joins_both(worker_probe):
    environment = dict(os.environ, WYSNA_MEM_CHECK='1', WYSNA_PARALLEL_THREADS='4')
    result = subprocess.run([str(worker_probe), 'task-group'], env=environment,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert result.stdout == 'task group bounded and joined\n'


@pytest.mark.parametrize('mode,message', [('simultaneous-traps', 'one complete worker diagnostic'),
                                        ('simultaneous-conversions', 'Invalid int conversion')])
def test_simultaneous_worker_traps_report_one_complete_diagnostic(worker_probe, mode, message):
    environment = dict(os.environ, WYSNA_PARALLEL_THREADS='4')
    result = subprocess.run([str(worker_probe), mode], env=environment,
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 1, result.stderr
    assert result.stderr.count('[W4003]') == 1, result.stderr
    assert message + '\n' in result.stderr
