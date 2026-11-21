"""Atomic per-task checkpoints for resumable AI evaluation runs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any, Dict, List

from bench.ai_eval.models import TaskEvaluationResult


CHECKPOINT_SCHEMA = "wysna.ai-eval-checkpoint/1"


def load_checkpoint(
    path: Path,
    config: Dict[str, Any],
    selected_task_ids: set[str],
) -> List[TaskEvaluationResult]:
    """Load completed tasks only when the stored run configuration matches."""
    if not path.exists():
        return []

    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read benchmark checkpoint {path}: {error}") from error

    if not isinstance(checkpoint, dict):
        raise ValueError("Checkpoint must contain a JSON object. Choose a new --checkpoint path.")
    if checkpoint.get("schema_version") != CHECKPOINT_SCHEMA:
        raise ValueError("Checkpoint schema is unsupported. Choose a new --checkpoint path.")
    if checkpoint.get("config") != config:
        raise ValueError("Checkpoint configuration differs from this run. Choose a new --checkpoint path.")

    raw_results = checkpoint.get("results")
    if not isinstance(raw_results, list):
        raise ValueError("Checkpoint results must be a JSON array. Choose a new --checkpoint path.")
    try:
        results = [TaskEvaluationResult.from_dict(item) for item in raw_results]
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("Checkpoint contains a malformed task result. Choose a new --checkpoint path.") from error

    task_ids = [result.task_id for result in results]
    if len(task_ids) != len(set(task_ids)) or not set(task_ids).issubset(selected_task_ids):
        raise ValueError("Checkpoint contains duplicate or unexpected task IDs. Choose a new --checkpoint path.")
    return results


def save_checkpoint(path: Path, config: Dict[str, Any], results: List[TaskEvaluationResult]) -> None:
    """Replace the checkpoint atomically after the completed task result is serialized."""
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_path = Path(temporary.name)
            json.dump({
                "schema_version": CHECKPOINT_SCHEMA,
                "config": config,
                "results": [result.to_dict() for result in results],
            }, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
