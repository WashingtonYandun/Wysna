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


def test_ab_resume_preserves_completed_condition_and_rejects_changed_config(tmp_path, monkeypatch):
    from bench.ai_eval.ab_cohort import CohortTask, ConditionHarness, run_ab_cohort
    from bench.ai_eval.checkpoint import load_rows, COHORT_CHECKPOINT_SCHEMA

    task = _task("PAIR-1")
    cohort = [CohortTask(task, "authoring", ("main.wysna",))]
    path = tmp_path / "ab.checkpoint.json"
    config = {"model": "mock", "revision": "fixture"}
    evaluate = Mock(side_effect=[_result(task), RuntimeError("interrupted")])
    monkeypatch.setattr(ConditionHarness, "evaluate_task", evaluate)
    with pytest.raises(RuntimeError, match="interrupted"):
        run_ab_cohort(cohort, lambda _condition: MockProvider(), repo_root=str(REPO_ROOT),
                      repetitions=1, checkpoint_path=path, checkpoint_config=config)
    saved = load_rows(path, config, COHORT_CHECKPOINT_SCHEMA)
    assert len(saved) == 1 and saved[0]["condition"] == "A"
    assert saved[0]["task_result"]["task_id"] == task.id
    evaluate = Mock(return_value=_result(task))
    monkeypatch.setattr(ConditionHarness, "evaluate_task", evaluate)
    report = run_ab_cohort(cohort, lambda _condition: MockProvider(), repo_root=str(REPO_ROOT),
                           repetitions=1, checkpoint_path=path, checkpoint_config=config)
    assert [row["condition"] for row in report["runs"]] == ["A", "B"]
    evaluate.assert_called_once()
    with pytest.raises(ValueError, match="configuration differs"):
        run_ab_cohort(cohort, lambda _condition: MockProvider(), repo_root=str(REPO_ROOT),
                      repetitions=1, checkpoint_path=path, checkpoint_config={"model": "changed"})


def test_failed_atomic_replace_preserves_previous_checkpoint(tmp_path, monkeypatch):
    from bench.ai_eval import checkpoint as module
    path = tmp_path / "safe.json"
    config = {"model": "mock"}
    first = _result(_task("TASK-1"))
    save_checkpoint(path, config, [first])
    previous = path.read_bytes()
    monkeypatch.setattr(module.os, "replace", Mock(side_effect=OSError("disk failure")))
    with pytest.raises(OSError, match="disk failure"):
        save_checkpoint(path, config, [first, _result(_task("TASK-2"))])
    assert path.read_bytes() == previous
    assert list(tmp_path.iterdir()) == [path]


def test_source_fingerprint_ignores_reports_but_detects_changes(tmp_path):
    import subprocess
    from bench.ai_eval.checkpoint import run_config

    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    subprocess.run(["git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
                    "commit", "--allow-empty", "-qm", "Initialize fixture"], cwd=tmp_path, check=True)
    arguments = {"provider": "mock", "model": "fixture", "base_url": "", "corpus": [], "parameters": {}}
    original = run_config(tmp_path, **arguments)
    results = tmp_path / "bench/ai_eval/results"
    results.mkdir(parents=True)
    (results / "generated.json").write_text("{}")
    assert run_config(tmp_path, **arguments) == original
    (tmp_path / "source.wysna").write_text("print(1)")
    assert run_config(tmp_path, **arguments) != original
