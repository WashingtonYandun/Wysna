#!/usr/bin/env python3
"""CLI runner for Wysna AI Authoring and Repair Benchmark.

Usage:
  python3 bench/ai_eval/run_eval.py [--provider=mock|gemini|openai] [--model=...] [--task=TASK-01] [--output=results.json]
"""

import argparse
import json
import os
import sys

# Ensure repository root is on sys.path
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from bench.ai_eval.harness import EvaluationHarness
from bench.ai_eval.models import Task
from bench.ai_eval.providers import GeminiProvider, MockProvider, OpenAICompatibleProvider


def format_markdown_table(summary: dict) -> str:
    lines = [
        f"# Wysna AI Evaluation Report · {summary['model_name']} ({summary['provider_name']})",
        "",
        f"- **Timestamp:** {summary['timestamp']}",
        f"- **Compiler Version:** {summary['compiler_version']} (commit `{summary['compiler_commit']}`)",
        f"- **Total Tasks:** {summary['total_tasks']} (Supported: {summary['supported_tasks']}, Blocked: {summary['blocked_tasks']})",
        f"- **Pass@1 (First Try):** {summary['pass_at_1_count']}/{summary['supported_tasks']} ({summary['pass_at_1_rate'] * 100:.1f}%)",
        f"- **Pass@3 (Repair Gate):** {summary['pass_at_3_count']}/{summary['supported_tasks']} ({summary['pass_at_3_rate'] * 100:.1f}%)",
        f"- **Avg Latency:** {summary['avg_latency_ms']:.1f} ms | **Total Tokens:** {summary['total_tokens']}",
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
        "| Task ID | Domain | Title | Status | Attempt | Latency (ms) | Tokens | Error |",
        "| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :--- |",
    ])

    for res in summary["results"]:
        status_badge = "✅ PASS" if res["status"] == "pass" else ("⛔ BLOCKED" if res["status"] == "blocked" else "❌ FAIL")
        attempt_str = str(res["pass_at_attempt"]) if res["pass_at_attempt"] else "-"
        err_str = res["final_error_category"] or (res.get("blocked_reason", "-") if res["status"] == "blocked" else "-")
        lines.append(
            f"| `{res['task_id']}` | {res['domain']} | {res['title']} | {status_badge} | {attempt_str} | {res['total_latency_ms']} | {res['total_tokens']} | {err_str} |"
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
    parser.add_argument("--base-url", type=str, default="http://localhost:1234/v1", help="OpenAI-compatible base URL")
    parser.add_argument("--api-key", type=str, default=None, help="API key for provider")

    args = parser.parse_args()

    # Load tasks
    with open(args.tasks_file, "r", encoding="utf-8") as f:
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

    harness = EvaluationHarness(repo_root=REPO_ROOT)
    print(f"=== Starting Wysna AI Benchmark ({len(tasks)} tasks loaded) ===")
    print(f"Provider: {args.provider} | Model: {model_name}")

    summary = harness.run_benchmark(
        tasks=tasks,
        provider=provider,
        provider_name=args.provider,
        model_name=model_name,
        task_filter=args.task,
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
    sys.exit(main())
