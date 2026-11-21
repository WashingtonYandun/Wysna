#!/usr/bin/env python3
"""CLI runner for Wysna AI Authoring and Repair Benchmark.

Usage:
  python3 bench/ai_eval/run_eval.py [--provider=mock|gemini|openai] [--model=...] [--task=TASK-01] [--output=results.json]
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

# Load both the benchmark package and the compiler from the source checkout.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path[:0] = [REPO_ROOT, os.path.join(REPO_ROOT, "src")]

from bench.ai_eval.harness import EvaluationHarness
from bench.ai_eval.models import Task
from bench.ai_eval.providers import GeminiProvider, MockProvider, OpenAICompatibleProvider
from bench.ai_eval.checkpoint import load_checkpoint, save_checkpoint


def _checkpoint_config(args, model_name: str, tasks_file: Path) -> dict:
    """Identify the task corpus, compiler source, and provider settings for safe resume."""
    revision = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.strip()
    diff = subprocess.run(
        ["git", "diff", "HEAD", "--binary"], cwd=REPO_ROOT, capture_output=True, check=True,
    ).stdout
    untracked = subprocess.run(
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=REPO_ROOT, capture_output=True, check=True,
    ).stdout
    output_paths = [
        path.resolve() for path in (args.checkpoint, Path(args.output) if args.output else None)
        if path is not None
    ]
    source_hash = hashlib.sha256(diff)
    for relative_name in untracked.split(b"\0"):
        if not relative_name:
            continue
        source_path = Path(REPO_ROOT) / os.fsdecode(relative_name)
        resolved_path = source_path.resolve()
        results_dir = (Path(REPO_ROOT) / "bench" / "ai_eval" / "results").resolve()
        if resolved_path == results_dir or results_dir in resolved_path.parents:
            continue
        if any(
            resolved_path == output_path
            or (
                resolved_path.parent == output_path.parent
                and resolved_path.name.startswith(f".{output_path.name}.")
                and resolved_path.name.endswith(".tmp")
            )
            for output_path in output_paths
        ):
            continue
        file_bytes = source_path.read_bytes()
        source_hash.update(len(relative_name).to_bytes(8, "big"))
        source_hash.update(relative_name)
        source_hash.update(len(file_bytes).to_bytes(8, "big"))
        source_hash.update(file_bytes)
    source_digest = source_hash.hexdigest()
    endpoint_digest = hashlib.sha256(args.base_url.encode("utf-8")).hexdigest()
    return {
        "compiler_commit": revision,
        "compiler_source_sha256": source_digest,
        "tasks_sha256": hashlib.sha256(tasks_file.read_bytes()).hexdigest(),
        "provider": args.provider,
        "model": model_name,
        "endpoint_sha256": endpoint_digest if args.provider == "openai" else None,
        "task_filter": args.task,
        "max_repair_cycles": args.max_repair_cycles,
    }


def format_markdown_table(summary: dict) -> str:
    denom = summary["supported_tasks"]
    lines = [
        f"# Wysna AI Evaluation Report · {summary['model_name']} ({summary['provider_name']})",
        "",
        f"- **Schema Version:** `{summary.get('schema_version', 'wysna.ai-eval/1')}`",
        f"- **Timestamp:** {summary['timestamp']}",
        f"- **Compiler Version:** {summary['compiler_version']} (commit `{summary['compiler_commit']}`)",
        f"- **Total Tasks:** {summary['total_tasks']} (Supported: {denom}, Blocked: {summary['blocked_tasks']})",
        f"- **First Static Check (Raw -> Post-Autofix):** {summary.get('raw_first_check_pass_count', 0)}/{denom} -> {summary.get('post_autofix_first_check_pass_count', 0)}/{denom}",
        f"- **Raw Pass@1 (No Autofix):** {summary.get('raw_pass_at_1_count', 0)}/{denom} ({summary.get('raw_pass_at_1_rate', 0.0) * 100:.1f}%)",
        f"- **Pass@1 (Incl. Autofix):** {summary['pass_at_1_count']}/{denom} ({summary['pass_at_1_rate'] * 100:.1f}%)",
        f"- **Pass@3 (<=3 Turns):** {summary['pass_at_3_count']}/{denom} ({summary['pass_at_3_rate'] * 100:.1f}%)",
        f"- **Resolved (Within Budget <= {summary.get('max_attempts', 4)} Turns):** {summary.get('resolved_count', summary['pass_at_3_count'])}/{denom} ({summary.get('resolved_rate', summary['pass_at_3_rate']) * 100:.1f}%)",
        f"- **Avg Wall Time:** {summary.get('avg_wall_time_ms', summary['avg_latency_ms']):.1f} ms | **Avg Provider Latency:** {summary.get('avg_provider_latency_ms', summary['avg_latency_ms']):.1f} ms | **Avg Turns:** {summary.get('avg_model_turns', 0.0):.2f} | **Total Tokens ({summary.get('token_counting_method', 'unknown')}):** {summary['total_tokens']}",
        "",
        "## Error Distribution",
        "",
        "| Error Category | Count |",
        "| :--- | :---: |",
    ]
    if summary["error_distribution"]:
        for cat, cnt in summary["error_distribution"].items():
            lines.append(f"| `{cat}` | {cnt} |")
    else:
        lines.append("| *None (All passed)* | 0 |")

    lines.extend([
        "",
        "## Detailed Task Breakdown",
        "",
        "| Task ID | Domain | Title | Status | Attempt | Turns | Wall (ms) | Tokens | Error |",
        "| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |",
    ])

    for res in summary["results"]:
        status_badge = "✅ PASS" if res["status"] == "pass" else ("⛔ BLOCKED" if res["status"] == "blocked" else "❌ FAIL")
        attempt_str = str(res["pass_at_attempt"]) if res["pass_at_attempt"] else "-"
        turns_str = str(res.get("model_turns", len(res.get("attempts", []))))
        wall_ms = res.get("wall_time_ms", res["total_latency_ms"])
        err_str = res["final_error_category"] or (res.get("blocked_reason", "-") if res["status"] == "blocked" else "-")
        lines.append(
            f"| `{res['task_id']}` | {res['domain']} | {res['title']} | {status_badge} | {attempt_str} | {turns_str} | {wall_ms} | {res['total_tokens']} | {err_str} |"
        )

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Wysna AI Authoring and Repair Benchmark Harness")
    parser.add_argument(
        "--provider",
        choices=["mock", "gemini", "openai"],
        default="mock",
        help="LLM provider (default: mock for dry-run/CI)",
    )
    parser.add_argument("--model", type=str, default=None, help="Model name identifier")
    parser.add_argument(
        "--tasks-file",
        type=str,
        default=os.path.join(os.path.dirname(__file__), "tasks.json"),
        help="Path to tasks.json corpus file",
    )
    parser.add_argument("--task", type=str, default=None, help="Filter for single task ID or domain")
    parser.add_argument("--output", type=str, default=None, help="Path to write JSON results file")
    parser.add_argument("--checkpoint", type=Path, default=None,
                        help="Atomically save each completed task and resume matching runs")
    parser.add_argument("--max-repair-cycles", type=int, default=3,
                        help="Maximum diagnostic repair submissions per task")
    parser.add_argument("--base-url", type=str, default="http://localhost:1234/v1", help="OpenAI-compatible base URL")
    parser.add_argument("--api-key", type=str, default=None, help="API key for provider")

    args = parser.parse_args()

    # Load tasks
    tasks_file = Path(args.tasks_file).resolve()
    with tasks_file.open("r", encoding="utf-8") as f:
        corpus_data = json.load(f)

    tasks = [Task.from_dict(t) for t in corpus_data.get("tasks", [])]

    # Instantiate provider
    if args.provider == "mock":
        model_name = args.model or "mock-deterministic"
        provider = MockProvider()
    elif args.provider == "gemini":
        model_name = args.model or "gemini-flash-lite-latest"
        provider = GeminiProvider(api_key=args.api_key, model=model_name)
    elif args.provider == "openai":
        model_name = args.model or "local-model"
        provider = OpenAICompatibleProvider(
            base_url=args.base_url,
            api_key=args.api_key,
            model=model_name,
        )
    else:
        print(f"Unknown provider: {args.provider}", file=sys.stderr)
        return 1

    if args.max_repair_cycles < 0:
        parser.error("--max-repair-cycles must be non-negative")
    if args.checkpoint and args.output and args.checkpoint.resolve() == Path(args.output).resolve():
        parser.error("--checkpoint and --output must use different files")

    selected_tasks = [
        task for task in tasks
        if not args.task or task.id == args.task or args.task in task.domain
    ]
    completed_results = []
    checkpoint_config = None
    if args.checkpoint:
        checkpoint_config = _checkpoint_config(args, model_name, tasks_file)
        selected_ids = {task.id for task in selected_tasks}
        completed_results = load_checkpoint(args.checkpoint, checkpoint_config, selected_ids)
        save_checkpoint(args.checkpoint, checkpoint_config, completed_results)
        print(f"Checkpoint: {len(completed_results)}/{len(selected_tasks)} tasks already complete")

    harness = EvaluationHarness(repo_root=REPO_ROOT, max_repair_cycles=args.max_repair_cycles)
    print(f"=== Starting Wysna AI Benchmark ({len(tasks)} tasks loaded) ===")
    print(f"Provider: {args.provider} | Model: {model_name}")

    summary = harness.run_benchmark(
        tasks=tasks,
        provider=provider,
        provider_name=args.provider,
        model_name=model_name,
        task_filter=args.task,
        completed_results=completed_results,
        on_task_complete=(
            lambda _result, results: save_checkpoint(args.checkpoint, checkpoint_config, results)
        ) if args.checkpoint else None,
    )

    summary_dict = summary.to_dict()
    md_output = format_markdown_table(summary_dict)
    print("\n" + md_output)

    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(summary_dict, f, indent=2)
        print(f"\n[+] Results successfully saved to {args.output}")

    return 0


if __name__ == "__main__":
    # Redirected Windows streams may not represent every character in a task report.
    sys.stdout.reconfigure(errors="backslashreplace")
    sys.exit(main())
