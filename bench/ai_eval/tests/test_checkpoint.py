"""Task checkpoint validation and recovery contracts."""

from pathlib import Path
import sys
from unittest.mock import Mock

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bench.ai_eval.checkpoint import load_checkpoint, save_checkpoint
from bench.ai_eval.harness import EvaluationHarness
from bench.ai_eval.models import Task, TaskEvaluationResult
from bench.ai_eval.providers import MockProvider


def _task(task_id: str) -> Task:
    return Task(
        id=task_id,
        domain="cli_scripting",
        title=task_id,
        status="supported",
        instruction="Return the expected value.",
    )


def _result(task: Task) -> TaskEvaluationResult:
    return TaskEvaluationResult(
        task_id=task.id,
        domain=task.domain,
        title=task.title,
        status="pass",
        pass_at_attempt=1,
        raw_first_check_passed=True,
        post_autofix_first_check_passed=True,
        first_attempt_oracle_passed=True,
        raw_pass_at_1=True,
        model_turns=1,
        provider_latency_ms=10.0,
        wall_time_ms=12.0,
        total_tokens=8,
    )


def test_checkpoint_round_trip_and_configuration_validation(tmp_path):
    checkpoint = tmp_path / "benchmark.checkpoint.json"
    config = {"model": "nvidia/nemotron:free", "source": "fixture"}
    first = _result(_task("TASK-1"))

    save_checkpoint(checkpoint, config, [first])

    loaded = load_checkpoint(checkpoint, config, {"TASK-1", "TASK-2"})
    assert loaded == [first]
    assert list(tmp_path.glob("*.tmp")) == []
    with pytest.raises(ValueError, match="configuration differs"):
        load_checkpoint(checkpoint, {**config, "model": "different-model"}, {"TASK-1"})
    with pytest.raises(ValueError, match="unexpected task IDs"):
        load_checkpoint(checkpoint, config, {"TASK-2"})


def test_benchmark_recovers_after_failure_between_tasks(tmp_path):
    first_task = _task("TASK-1")
    second_task = _task("TASK-2")
    tasks = [first_task, second_task]
    checkpoint = tmp_path / "benchmark.checkpoint.json"
    config = {"model": "mock", "source": "fixture"}
    harness = EvaluationHarness(repo_root=str(REPO_ROOT))
    provider = MockProvider()
    completed = _result(first_task)
    evaluator = Mock(side_effect=[completed, RuntimeError("simulated provider quota response")])
    harness.evaluate_task = evaluator

    def persist(_result, results):
        save_checkpoint(checkpoint, config, results)

    with pytest.raises(RuntimeError, match="simulated provider quota"):
        harness.run_benchmark(tasks, provider, completed_results=[], on_task_complete=persist)

    resumed_results = load_checkpoint(checkpoint, config, {task.id for task in tasks})
    assert [result.task_id for result in resumed_results] == ["TASK-1"]

    resumed_harness = EvaluationHarness(repo_root=str(REPO_ROOT))
    second_result = _result(second_task)
    resumed_evaluator = Mock(return_value=second_result)
    resumed_harness.evaluate_task = resumed_evaluator
    summary = resumed_harness.run_benchmark(
        tasks,
        provider,
        completed_results=resumed_results,
        on_task_complete=persist,
    )

    assert [result.task_id for result in summary.results] == ["TASK-1", "TASK-2"]
    resumed_evaluator.assert_called_once_with(second_task, provider)
