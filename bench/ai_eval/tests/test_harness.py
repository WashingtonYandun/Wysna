"""Unit tests for the AI Evaluation Harness (bench/ai_eval).

Validates code block extraction, diagnostic error classification,
oracle execution, multi-attempt repair cycles, and metrics calculation.
"""

import os
import sys
import json
from pathlib import Path
import subprocess
import pytest

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from bench.ai_eval.harness import EvaluationHarness
from bench.ai_eval.models import Task, TaskTestCase
from bench.ai_eval.providers import MockProvider
from bench.ai_eval.token_audit import (
    run_self_healing_efficiency_audit,
    run_token_density_audit,
)


def test_runner_from_source_checkout(tmp_path):
    runner = Path(REPO_ROOT) / "bench" / "ai_eval" / "run_eval.py"
    report = tmp_path / "results.json"
    process = subprocess.run(
        [sys.executable, "-I", str(runner), "--provider=mock", "--task=TASK-01",
         "--output", str(report)],
        cwd=tmp_path, capture_output=True, text=True, timeout=60,
    )
    assert process.returncode == 0, process.stderr
    result = json.loads(report.read_text(encoding="utf-8"))
    assert [task["task_id"] for task in result["results"]] == ["TASK-01"]
    assert result["results"][0]["attempts"][0]["check_passed"]


def test_code_block_extraction():
    harness = EvaluationHarness(repo_root=REPO_ROOT)

    # 1. Single markdown block
    text1 = "Here is the code:\n```wysna\nfn main() -> unit { print(\"Hi\") }\n```"
    files1 = harness.extract_code_blocks(text1)
    assert "main.wysna" in files1
    assert "fn main" in files1["main.wysna"]

    # 2. Multi-file markdown blocks
    text2 = (
        "Here are two modules:\n"
        "### helper.wysna\n```wysna\npub fn inc(x: int) -> int { return x + 1 }\n```\n"
        "### main.wysna\n```wysna\nimport \"helper.wysna\" as h\nfn main() -> unit { print(h.inc(1)) }\n```"
    )
    files2 = harness.extract_code_blocks(text2)
    assert "helper.wysna" in files2
    assert "main.wysna" in files2
    assert "pub fn inc" in files2["helper.wysna"]


def test_error_classification():
    harness = EvaluationHarness(repo_root=REPO_ROOT)

    assert harness.classify_error("ParserError: unexpected token", [{"code": "W0001"}]) == "syntax"
    assert harness.classify_error("TypeError: cannot assign str to int", [{"code": "W2001"}]) == "type"
    assert harness.classify_error("Circular import detected", [{"code": "W1003"}]) == "circular_dependency"
    assert harness.classify_error("Undefined identifier 'foo'", []) == "api_hallucination"
    assert harness.classify_error("Traceback (most recent call last):", []) == "runtime_panic"
    assert harness.classify_error("", []) == "oracle_mismatch"


def test_task_pass_at_first_attempt():
    harness = EvaluationHarness(repo_root=REPO_ROOT)

    task = Task(
        id="TEST-01",
        domain="cli_scripting",
        title="Test Hello World",
        status="supported",
        instruction="Print Hello World",
        test_cases=[
            TaskTestCase(
                name="basic",
                expected_stdout="Hello World\n",
                expected_exit=0,
            )
        ],
    )

    mock = MockProvider({
        "TEST-01": [
            "```wysna\nfn main() -> unit {\n    print(\"Hello World\")\n}\n```"
        ]
    })

    res = harness.evaluate_task(task, mock)
    assert res.status == "pass"
    assert res.pass_at_attempt == 1
    assert res.raw_pass_at_1 is True
    assert res.raw_first_check_passed is True
    assert res.post_autofix_first_check_passed is True
    assert res.first_attempt_autofix_applied is False
    assert res.first_attempt_oracle_passed is True
    assert res.model_turns == 1
    assert res.wall_time_ms >= res.provider_latency_ms >= 0.0
    assert len(res.attempts) == 1
    assert res.attempts[0].raw_check_passed is True
    assert res.attempts[0].post_autofix_check_passed is True
    assert res.attempts[0].autofix_applied is False
    assert res.attempts[0].check_passed is True
    assert res.attempts[0].oracle_passed is True


def test_task_repair_loop_success():
    harness = EvaluationHarness(repo_root=REPO_ROOT)

    task = Task(
        id="TEST-02",
        domain="cli_scripting",
        title="Test Repair Loop",
        status="supported",
        instruction="Print 42",
        test_cases=[
            TaskTestCase(
                name="check_42",
                expected_stdout="42\n",
                expected_exit=0,
            )
        ],
    )

    # Attempt 0: produces wrong output ("99") -> oracle fails
    # Attempt 1: fixes it and produces "42" -> oracle passes
    mock = MockProvider({
        "TEST-02": [
            "```wysna\nfn main() -> unit {\n    print(\"99\")\n}\n```",
            "```wysna\nfn main() -> unit {\n    print(\"42\")\n}\n```",
        ]
    })

    res = harness.evaluate_task(task, mock)
    assert res.status == "pass"
    assert res.pass_at_attempt == 2  # Passed on attempt index 1 (second turn)
    assert len(res.attempts) == 2
    assert res.attempts[0].oracle_passed is False
    assert res.attempts[1].oracle_passed is True


def test_blocked_task_handling():
    harness = EvaluationHarness(repo_root=REPO_ROOT)

    task = Task(
        id="TEST-BLOCKED",
        domain="system_sqlite",
        title="Blocked SQLite Task",
        status="blocked",
        blocked_reason="Requires native SQLite driver in Phase 1",
        instruction="Do something blocked",
    )

    mock = MockProvider()
    res = harness.evaluate_task(task, mock)
    assert res.status == "blocked"
    assert res.blocked_reason == "Requires native SQLite driver in Phase 1"
    assert len(res.attempts) == 0


def test_auto_fix_heals_typo_without_spending_llm_turn():
    harness = EvaluationHarness(repo_root=REPO_ROOT)

    task = Task(
        id="TEST-AUTOFIX",
        domain="cli_scripting",
        title="Test Zero-Turn Auto-Fix",
        status="supported",
        instruction="Print user name",
        test_cases=[
            TaskTestCase(
                name="user_name",
                expected_stdout="Ada\n",
                expected_exit=0,
            )
        ],
    )

    # Attempt 0 has field typo `u.nme` which `wysna fix` repairs deterministically
    mock = MockProvider({
        "TEST-AUTOFIX": [
            "```wysna\nstruct User { name: str }\nfn main() -> unit {\n    let u = User { name: \"Ada\" }\n    print(u.nme)\n}\n```"
        ]
    })

    res = harness.evaluate_task(task, mock)
    assert res.status == "pass"
    assert res.pass_at_attempt == 1
    assert res.raw_first_check_passed is False
    assert res.post_autofix_first_check_passed is True
    assert res.first_attempt_autofix_applied is True
    assert res.first_attempt_oracle_passed is True
    assert res.raw_pass_at_1 is False
    assert res.model_turns == 1
    assert res.attempts[0].raw_check_passed is False
    assert res.attempts[0].post_autofix_check_passed is True
    assert res.attempts[0].autofix_applied is True
    assert len(mock.call_history) == 1


def test_attributable_metrics_separate_raw_autofix_repair_limits_and_wrong_oracle():
    harness = EvaluationHarness(repo_root=REPO_ROOT, max_repair_cycles=3)

    def _task(task_id: str, expected_stdout: str = "ok\n", status: str = "supported") -> Task:
        return Task(
            id=task_id,
            domain="cli_scripting",
            title=task_id,
            status=status,
            blocked_reason="excluded" if status == "blocked" else None,
            instruction=f"Solve {task_id}",
            test_cases=[
                TaskTestCase(name="check", expected_stdout=expected_stdout, expected_exit=0)
            ] if status == "supported" else [],
        )

    good = "```wysna\nfn main() -> unit {\n    print(\"ok\")\n}\n```"
    bad_syntax = "```wysna\nfn main( -> unit {}\n```"
    wrong_output = "```wysna\nfn main() -> unit {\n    print(\"wrong\")\n}\n```"
    autofix_code = (
        "```wysna\nstruct User { name: str }\n"
        "fn main() -> unit {\n    let u = User { name: \"ok\" }\n    print(u.nme)\n}\n```"
    )

    mock = MockProvider({
        "TASK-DIRECT": [good],
        "TASK-AUTOFIX": [autofix_code],
        # Succeeds only on the 4th submission (3rd repair): must count in resolved, NOT pass_at_3.
        "TASK-FOURTH": [bad_syntax, bad_syntax, wrong_output, good],
        # Compiles cleanly on turn 1, but output stays wrong across all 4 submissions.
        "TASK-WRONG-OUT": [wrong_output, wrong_output, wrong_output, wrong_output],
    })

    tasks = [
        _task("TASK-DIRECT"),
        _task("TASK-AUTOFIX"),
        _task("TASK-FOURTH"),
        _task("TASK-WRONG-OUT"),
        _task("TASK-BLOCKED", status="blocked"),
    ]
    summary = harness.run_benchmark(tasks, mock, provider_name="mock", model_name="mock-v2")
    payload = summary.to_dict()

    assert payload["schema_version"] == "wysna.ai-eval/2"
    assert payload["token_counting_method"] == "estimated_whitespace"
    assert payload["total_tasks"] == 5
    assert payload["supported_tasks"] == 4
    assert payload["blocked_tasks"] == 1
    assert payload["denominators"] == {
        "total_tasks": 5,
        "supported_tasks": 4,
        "blocked_tasks": 1,
        "pass_at_1_max_attempts": 1,
        "pass_at_3_max_attempts": 3,
        "budget_max_attempts": 4,
    }
    assert payload["raw_first_check_pass_count"] == 2  # DIRECT + WRONG-OUT
    assert payload["post_autofix_first_check_pass_count"] == 3  # DIRECT + AUTOFIX + WRONG-OUT
    assert payload["raw_pass_at_1_count"] == 1  # DIRECT only
    assert payload["autofix_pass_at_1_count"] == 1  # AUTOFIX only
    assert payload["pass_at_1_count"] == 2
    assert payload["pass_at_3_count"] == 2  # TASK-FOURTH passed on turn 4, excluded from pass_at_3
    assert payload["resolved_count"] == 3
    assert payload["error_distribution"] == {"oracle_mismatch": 1}
    assert payload["avg_wall_time_ms"] >= payload["avg_provider_latency_ms"] >= 0.0
    assert payload["total_model_turns"] == 10  # 1 + 1 + 4 + 4

    fourth = next(r for r in payload["results"] if r["task_id"] == "TASK-FOURTH")
    assert fourth["status"] == "pass"
    assert fourth["pass_at_attempt"] == 4
    assert fourth["model_turns"] == 4
    assert fourth["final_error_category"] is None

    wrong = next(r for r in payload["results"] if r["task_id"] == "TASK-WRONG-OUT")
    assert wrong["status"] == "fail"
    assert wrong["raw_first_check_passed"] is True
    assert wrong["first_attempt_oracle_passed"] is False
    assert wrong["final_error_category"] == "oracle_mismatch"


def test_harness_rejects_path_traversal_and_symlink_escape(tmp_path):
    outside_target = tmp_path / "escaped.wysna"
    harness = EvaluationHarness(repo_root=REPO_ROOT, max_repair_cycles=0)
    task = Task(
        id="TASK-ESCAPE",
        domain="cli_scripting",
        title="Path Escape Prevention",
        status="supported",
        instruction="Output only files inside workspace",
        test_cases=[TaskTestCase(name="ok", expected_stdout="ok\n", expected_exit=0)],
    )

    mock = MockProvider({
        "TASK-ESCAPE": [
            f"### ../escaped.wysna\n```wysna\nfn main() -> unit {{}}\n```\n"
            f"### {outside_target.as_posix()}\n```wysna\nfn main() -> unit {{}}\n```"
        ]
    })
    res = harness.evaluate_task(task, mock)
    assert res.status == "fail"
    assert res.final_error_category == "unsafe_path"
    assert not outside_target.exists()

    with pytest.raises(ValueError, match="escapes workspace"):
        harness._resolve_confined_path(str(tmp_path), "/etc/passwd")
    link_parent = tmp_path / "outside_dir"
    link_parent.mkdir()
    ws = tmp_path / "ws"
    ws.mkdir()
    if os.name != "nt":
        (ws / "link").symlink_to(link_parent, target_is_directory=True)
        with pytest.raises(ValueError, match="escapes workspace"):
            harness._resolve_confined_path(str(ws), "link/pwn.wysna")


def test_token_density_and_self_healing_audit(tmp_path):
    density = run_token_density_audit()
    pipeline = density["fallible_config_pipeline"]
    assert pipeline["wysna"]["bpe_tokens"] < pipeline["go"]["bpe_tokens"]
    assert pipeline["wysna"]["bpe_tokens"] < pipeline["rust"]["bpe_tokens"]

    healing = run_self_healing_efficiency_audit(str(tmp_path))
    assert healing["initial_errors_detected_in_one_pass"] == 2
    assert healing["auto_repaired_without_llm"] is True
    assert healing["llm_repair_turns_required"] == 0


def test_system_prompt_matches_v1_grammar():
    from bench.ai_eval.harness import SYSTEM_PROMPT

    assert "use `public`/`private`" not in SYSTEM_PROMPT
    assert "`pub` and `priv`" in SYSTEM_PROMPT
    for token in ("`u8`", "`None`", "`_ =>`", "`Name { field: new_val, ..base }`"):
        assert token in SYSTEM_PROMPT, token

