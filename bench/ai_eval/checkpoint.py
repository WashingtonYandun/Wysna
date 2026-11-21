"""Atomic per-task checkpoints for resumable AI evaluation runs."""

from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import hashlib
import subprocess
from typing import Any, Dict, List

from bench.ai_eval.models import TaskEvaluationResult


CHECKPOINT_SCHEMA = "wysna.ai-eval-checkpoint/1"
COHORT_CHECKPOINT_SCHEMA = "wysna.ai-eval-ab-checkpoint/1"


def run_config(repo_root: Path, *, provider: str, model: str, base_url: str,
               corpus: Any, parameters: Dict[str, Any], output_paths=()) -> Dict[str, Any]:
    """Fingerprint source and public settings without persisting credentials."""
    repo_root = repo_root.resolve()
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_root,
                              capture_output=True, text=True, check=True).stdout.strip()
    excluded = ("bench/ai_eval/results", "docs/evolution/evidence")
    diff = subprocess.run(["git", "diff", "HEAD", "--binary", "--", ".",
                           *[f":(exclude){name}/**" for name in excluded]], cwd=repo_root,
                          capture_output=True, check=True).stdout
    untracked = subprocess.run(["git", "ls-files", "--others", "--exclude-standard", "-z"],
                              cwd=repo_root, capture_output=True, check=True).stdout
    outputs = [Path(path).resolve() for path in output_paths if path is not None]
    source_hash = hashlib.sha256(diff)
    for relative_name in untracked.split(b"\0"):
        if not relative_name:
            continue
        relative_path = Path(os.fsdecode(relative_name))
        path = (repo_root / relative_path).resolve()
        if any(relative_path.is_relative_to(name) for name in excluded):
            continue
        if any(path == output or (path.parent == output.parent
               and path.name.startswith(f".{output.name}.") and path.name.endswith(".tmp"))
               for output in outputs):
            continue
        content = path.read_bytes()
        source_hash.update(len(relative_name).to_bytes(8, "big"))
        source_hash.update(relative_name)
        source_hash.update(hashlib.sha256(content).digest())
    return {
        "compiler_commit": revision,
        "compiler_source_sha256": source_hash.hexdigest(),
        "tasks_sha256": hashlib.sha256(json.dumps(corpus, sort_keys=True).encode()).hexdigest(),
        "provider": provider,
        "model": model,
        "endpoint_sha256": hashlib.sha256(base_url.rstrip("/").encode()).hexdigest() if provider == "openai" else None,
        "parameters": parameters,
    }


def load_rows(path: Path, config: Dict[str, Any], schema: str) -> List[Dict[str, Any]]:
    if not path.exists():
        return []
    try:
        checkpoint = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"Cannot read benchmark checkpoint {path}: {error}") from error
    if not isinstance(checkpoint, dict) or checkpoint.get("schema_version") != schema:
        raise ValueError("Checkpoint schema is unsupported. Choose a new --checkpoint path.")
    if checkpoint.get("config") != config:
        raise ValueError("Checkpoint configuration differs from this run. Choose a new --checkpoint path.")
    rows = checkpoint.get("results")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError("Checkpoint results must be an array of objects.")
    return rows


def load_checkpoint(
    path: Path,
    config: Dict[str, Any],
    selected_task_ids: set[str],
) -> List[TaskEvaluationResult]:
    """Load completed tasks only when the stored run configuration matches."""
    raw_results = load_rows(path, config, CHECKPOINT_SCHEMA)
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
    save_rows(path, config, [result.to_dict() for result in results], CHECKPOINT_SCHEMA)


def save_rows(path: Path, config: Dict[str, Any], rows: List[Dict[str, Any]], schema: str) -> None:
    """Write through a sibling temporary file so interruption preserves the previous checkpoint."""
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
                "schema_version": schema,
                "config": config,
                "results": rows,
            }, temporary, ensure_ascii=False, indent=2)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_path, path)
    except BaseException:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        raise
