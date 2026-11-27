#!/usr/bin/env python3
"""CLI runner for the Wysna Agent-Scale Benchmark."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[2]
SRC_DIR = REPO_ROOT / "src"
for candidate in (str(REPO_ROOT), str(SRC_DIR)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from bench.agent_scale import SCHEMA_VERSION
from bench.agent_scale.agent import DEFAULT_MAX_OUTPUT_BYTES, MAX_TOOL_TURNS_DEFAULT, run_agent_on_workspace
from bench.agent_scale.generate import generate_project
from bench.agent_scale.metrics import CacheCostConfig, evaluate_decision_gate, summarize_condition_runs
from bench.agent_scale.tasks import ALL_TASKS, prepare_task_workspace
from bench.ai_eval.providers import (
    AnthropicProvider,
    GeminiProvider,
    LlmProvider,
    OpenAICompatibleProvider,
)


class ScaleMockProvider(LlmProvider):
    """Deterministic multi-turn mock provider emitting valid tool_call blocks for smoke tests."""

    token_counting_method = "mock_word_split"

    def __init__(self) -> None:
        self._turn = 0

    def generate(self, system_prompt: str, user_prompt: str, history: Optional[List[Dict[str, str]]] = None):
        self._turn += 1
        prompt_tokens = len(system_prompt.split()) + len(user_prompt.split()) + sum(
            len(item.get("content", "").split()) for item in (history or [])
        )
        if not history:
            resp = '```tool_call\n{"tool": "list_dir", "args": {"path": "src"}}\n```'
        else:
            resp = '```tool_call\n{"tool": "done", "args": {"summary": "mock smoke complete"}}\n```'
        return resp, {"prompt_tokens": prompt_tokens, "completion_tokens": len(resp.split())}, 1.0


def _build_provider(args: argparse.Namespace) -> tuple[LlmProvider, str, bool]:
    if args.provider == "mock":
        return ScaleMockProvider(), args.model or "mock-deterministic", True
    if args.provider == "anthropic":
        model = args.model or "claude-haiku-5-5"
        return AnthropicProvider(model=model), model, False
    if args.provider == "gemini":
        model = args.model or "gemini-2.5-flash"
        return GeminiProvider(model=model), model, False
    if args.provider == "openai":
        model = args.model or "local-model"
        return OpenAICompatibleProvider(base_url=args.base_url, model=model), model, False
    raise ValueError(f"Unsupported provider: {args.provider}")


def run_scale_benchmark(
    *,
    loc: int,
    seed: int,
    conditions: List[str],
    task_ids: List[str],
    repetitions: int,
    provider_name: str,
    model_name: str,
    provider: LlmProvider,
    provider_is_mock: bool,
    wysna_bin: Optional[str] = None,
    max_turns: int = MAX_TOOL_TURNS_DEFAULT,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    cache_config: Optional[CacheCostConfig] = None,
    use_cache_effective: bool = False,
) -> Dict[str, Any]:
    """Generate template project, run paired conditions across tasks, and evaluate gate."""
    with tempfile.TemporaryDirectory(prefix="wysna-agent-scale-") as tmpdir:
        tmp_path = Path(tmpdir)
        template_dir = tmp_path / "template"
        project_meta = generate_project(template_dir, target_loc=loc, seed=seed)

        runs: List[Dict[str, Any]] = []
        for rep in range(repetitions):
            for task_id in task_ids:
                if task_id not in ALL_TASKS:
                    raise ValueError(f"Unknown task ID: {task_id!r}")
                task = ALL_TASKS[task_id]()
                for condition in conditions:
                    ws = prepare_task_workspace(template_dir, task, work_root=tmp_path)
                    try:
                        run_data = run_agent_on_workspace(
                            ws,
                            task_id,
                            task.instruction,
                            condition,
                            provider,
                            max_turns=max_turns,
                            max_output_bytes=max_output_bytes,
                            repo_root=REPO_ROOT,
                            wysna_bin=wysna_bin,
                            cache_config=cache_config,
                        )
                        passed, details = task.oracle(ws, REPO_ROOT, wysna_bin)
                        run_data["repetition"] = rep
                        run_data["passed"] = passed
                        run_data["oracle_details"] = details
                        runs.append(run_data)
                    finally:
                        shutil.rmtree(ws.parent, ignore_errors=True)

        by_cond: Dict[str, Any] = {}
        for cond in conditions:
            by_cond[cond] = summarize_condition_runs([r for r in runs if r["condition"] == cond])

        cond_a = [r for r in runs if r["condition"] == "A_baseline"]
        cond_b = [r for r in runs if r["condition"] == "B_wysna"]
        gate = evaluate_decision_gate(
            project_meta["actual_nonblank_loc"],
            cond_a,
            cond_b,
            provider_is_mock=provider_is_mock,
            use_cache_effective=use_cache_effective,
        )

        return {
            "schema_version": SCHEMA_VERSION,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            "provider": provider_name,
            "model": model_name,
            "provider_is_mock": provider_is_mock,
            "token_counting_method": getattr(provider, "token_counting_method", "unknown"),
            "config": {
                "target_loc": loc,
                "seed": seed,
                "conditions": conditions,
                "tasks": task_ids,
                "repetitions": repetitions,
                "max_turns": max_turns,
                "max_output_bytes": max_output_bytes,
                "wysna_bin": str(Path(wysna_bin).resolve()) if wysna_bin else None,
                "cache_model": {
                    "cache_read_multiplier": (cache_config or CacheCostConfig()).cache_read_multiplier,
                    "cache_write_multiplier": (cache_config or CacheCostConfig()).cache_write_multiplier,
                    "input_weight": (cache_config or CacheCostConfig()).input_weight,
                    "output_weight": (cache_config or CacheCostConfig()).output_weight,
                },
            },
            "project_meta": {
                "target_loc": project_meta["target_loc"],
                "actual_nonblank_loc": project_meta["actual_nonblank_loc"],
                "file_count": project_meta["file_count"],
                "tree_sha256": project_meta["tree_sha256"],
            },
            "summary_by_condition": by_cond,
            "paired_comparison": {
                "condition_a": "A_baseline",
                "condition_b": "B_wysna",
                "median_raw_token_reduction_pct": (
                    round(((by_cond["A_baseline"]["median_raw_tokens"] - by_cond["B_wysna"]["median_raw_tokens"])
                           / by_cond["A_baseline"]["median_raw_tokens"]) * 100.0, 2)
                    if "A_baseline" in by_cond and "B_wysna" in by_cond and by_cond["A_baseline"]["median_raw_tokens"] > 0
                    else 0.0
                ),
                "median_effective_token_reduction_pct": (
                    round(((by_cond["A_baseline"]["median_effective_tokens"] - by_cond["B_wysna"]["median_effective_tokens"])
                           / by_cond["A_baseline"]["median_effective_tokens"]) * 100.0, 2)
                    if "A_baseline" in by_cond and "B_wysna" in by_cond and by_cond["A_baseline"]["median_effective_tokens"] > 0
                    else 0.0
                ),
            },
            "decision_gate": gate,
            "runs": runs,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the Wysna Agent-Scale Benchmark.")
    parser.add_argument("--loc", type=int, default=25000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--conditions", nargs="+", default=["A_baseline", "B_wysna"])
    parser.add_argument("--tasks", nargs="+", default=["T1", "T2", "T3", "T4"])
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--provider", choices=["mock", "anthropic", "openai", "gemini"], default="mock")
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--base-url", type=str, default="http://localhost:1234/v1")
    parser.add_argument("--wysna-bin", type=str, default=None)
    parser.add_argument("--max-turns", type=int, default=MAX_TOOL_TURNS_DEFAULT)
    parser.add_argument("--max-output-bytes", type=int, default=DEFAULT_MAX_OUTPUT_BYTES)
    parser.add_argument("--cache-read-multiplier", type=float, default=0.1)
    parser.add_argument("--cache-write-multiplier", type=float, default=1.25)
    parser.add_argument("--use-cache-effective", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    provider, model_name, is_mock = _build_provider(args)
    cache_cfg = CacheCostConfig(
        cache_read_multiplier=args.cache_read_multiplier,
        cache_write_multiplier=args.cache_write_multiplier,
    )
    report = run_scale_benchmark(
        loc=args.loc,
        seed=args.seed,
        conditions=args.conditions,
        task_ids=args.tasks,
        repetitions=args.repetitions,
        provider_name=args.provider,
        model_name=model_name,
        provider=provider,
        provider_is_mock=is_mock,
        wysna_bin=args.wysna_bin,
        max_turns=args.max_turns,
        max_output_bytes=args.max_output_bytes,
        cache_config=cache_cfg,
        use_cache_effective=args.use_cache_effective,
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "loc": report["project_meta"]["actual_nonblank_loc"],
        "decision_gate": report["decision_gate"],
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
