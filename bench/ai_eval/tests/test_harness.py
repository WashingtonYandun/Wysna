"""Unit tests for the AI Evaluation Harness (bench/ai_eval).

Validates code block extraction, diagnostic error classification,
oracle execution, multi-attempt repair cycles, and metrics calculation.
"""

import os
import sys
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
    assert len(res.attempts) == 1
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
    assert len(mock.call_history) == 1


def test_token_density_and_self_healing_audit(tmp_path):
    density = run_token_density_audit()
    pipeline = density["fallible_config_pipeline"]
    assert pipeline["wysna"]["bpe_tokens"] < pipeline["go"]["bpe_tokens"]
    assert pipeline["wysna"]["bpe_tokens"] < pipeline["rust"]["bpe_tokens"]

    healing = run_self_healing_efficiency_audit(str(tmp_path))
    assert healing["initial_errors_detected_in_one_pass"] == 2
    assert healing["auto_repaired_without_llm"] is True
    assert healing["llm_repair_turns_required"] == 0

