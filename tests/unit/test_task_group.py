"""Group outcomes and cancellation across the four C99/Reference engines."""

import pytest

from tests.engine_harness import ENGINES, run_engine
from tests.unit.test_worker_safety import native_worker_compiler


@pytest.mark.parametrize('engine', ENGINES)
def test_group_report_preserves_order_and_typed_errors(engine, tmp_path, native_worker_compiler):
    source = """
import "std/async" as tasks
fn convert(value: int) -> int ! str {
    if value == 1 { return Err("failed") }
    return value * 2
}
let stopped = tasks.run_group([0, 1, 2], 1, -1, true, convert) else error { [] }
let completed = tasks.run_group([0, 1, 2], 2, -1, false, convert) else error { [] }
let expired = tasks.run_group([0, 1], 2, 0, false, convert) else error { [] }
let invalid = tasks.run_group([0, 1], 0, -1, false, convert)
print(stopped)
print(completed)
print(expired)
print(invalid)
"""
    result = run_engine(engine, source, 'group-outcomes', native_worker_compiler, tmp_path)
    assert result.runtime_ok, result.stderr
    assert result.stdout == (
        '[TaskOutcome.Completed(0), TaskOutcome.Failed("failed"), TaskOutcome.Cancelled]\n'
        '[TaskOutcome.Completed(0), TaskOutcome.Failed("failed"), TaskOutcome.Completed(4)]\n'
        '[TaskOutcome.DeadlineExceeded, TaskOutcome.DeadlineExceeded]\n'
        'Err(TaskGroupError.InvalidConcurrency)\n'
    )


@pytest.mark.parametrize('engine', ENGINES)
def test_group_cancels_running_cooperative_work(engine, tmp_path, native_worker_compiler):
    source = """
import "std/async" as tasks
fn wait_for_deadline(value: int) -> int ! str {
    while not tasks.group_cancelled() { }
    return value
}
fn stop(value: int) -> int ! str {
    let _ = tasks.cancel_group()
    return value
}
print(tasks.group_cancelled())
let outcomes = tasks.run_group([1], 1, 100, false, wait_for_deadline) else error { [] }
print(outcomes)
print(tasks.group_cancelled())
print(tasks.cancel_group())
let cancelled = tasks.run_group([1, 2], 1, -1, false, stop) else error { [] }
print(cancelled)
"""
    result = run_engine(engine, source, 'group-deadline', native_worker_compiler, tmp_path, run_timeout=5)
    assert result.runtime_ok, result.stderr
    assert result.stdout == (
        'false\n[TaskOutcome.DeadlineExceeded]\nfalse\nfalse\n'
        '[TaskOutcome.Cancelled, TaskOutcome.Cancelled]\n'
    )


@pytest.mark.parametrize('engine', ENGINES)
def test_group_rejects_effectful_callbacks(engine, tmp_path, native_worker_compiler):
    source = """
import "std/async" as tasks
fn effectful(value: int) -> int ! str {
    print(value)
    return value
}
let outcomes = tasks.run_group([1], 2, -1, false, effectful)
"""
    result = run_engine(engine, source, 'group-worker-safety', native_worker_compiler, tmp_path)
    assert not result.compile_ok
    assert result.diag_code == 'W2015', result.stderr
