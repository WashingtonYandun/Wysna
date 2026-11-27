"""Block 3 tests: ai_eval Native SDK integration, diagnostic formatting, and error taxonomy."""

import json
import os
from pathlib import Path
import subprocess
import sys

from bench.ai_eval.harness import EvaluationHarness
from compiler.Bootstrap import ensure_selfhost_compiler_binary

ROOT = Path(__file__).resolve().parents[2]


def test_format_diagnostic_extracts_primary_span_and_help_from_selfhost(tmp_path):
    compiler = ensure_selfhost_compiler_binary(str(ROOT))
    src = tmp_path / "typo.wysna"
    src.write_text('fn main() {\n  let counter = 1\n  print(coutner)\n}\n', encoding="utf-8")

    proc = subprocess.run(
        [compiler, "check", "--output-format=json-v1", str(src)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode != 0
    payload = json.loads(proc.stdout)
    diag = payload["diagnostics"][0]

    formatted = EvaluationHarness.format_diagnostic_for_prompt(diag)
    assert "[W1001]" in formatted
    assert "line 3" in formatted
    assert "help: Did you mean 'counter'?" in formatted

    harness = EvaluationHarness(repo_root=str(ROOT))
    assert harness.classify_error(proc.stderr, payload["diagnostics"]) == "api_hallucination"


def test_classify_error_recognizes_native_w4002_runtime_panic():
    harness = EvaluationHarness(repo_root=str(ROOT))
    stderr = "[W4002] /tmp/main.wysna:3:9 - List index out of bounds (index 5, length 2)\n"
    assert harness.classify_error(stderr, []) == "runtime_panic"


def test_run_eval_and_ab_cohort_mock_cli_smoke(tmp_path):
    eval_out = tmp_path / "eval_mock.json"
    eval_res = subprocess.run(
        [sys.executable, "bench/ai_eval/run_eval.py", "--provider=mock", "--task=TASK-01", "--output", str(eval_out)],
        cwd=ROOT,
        env=dict(os.environ, PYTHONPATH=f"{ROOT / 'src'}:{ROOT}"),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert eval_res.returncode == 0, eval_res.stderr
    assert eval_out.is_file()

    ab_out = tmp_path / "ab_mock.json"
    ab_res = subprocess.run(
        [
            sys.executable,
            "bench/ai_eval/ab_cohort.py",
            "--provider=mock",
            "--suite=agent-first-v1",
            "--repetitions=1",
            "--output",
            str(ab_out),
        ],
        cwd=ROOT,
        env=dict(os.environ, PYTHONPATH=f"{ROOT / 'src'}:{ROOT}"),
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert ab_res.returncode == 0, ab_res.stderr
    assert ab_out.is_file()
