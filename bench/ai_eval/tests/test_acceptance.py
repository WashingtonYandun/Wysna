"""Canonical effectiveness gate and honest handling of mock evidence."""

from bench.ai_eval.acceptance import evaluate_acceptance


def test_mock_cannot_satisfy_effectiveness_gate():
    assert evaluate_acceptance({"provider_name": "mock"})["state"] == "not_measured_with_real_model"


def test_full_real_corpus_gate_requires_functional_success_not_only_typechecking():
    summary = {"provider_name": "openai", "total_tasks": 30, "supported_tasks": 30,
               "raw_pass_at_1_rate": 0.90, "pass_at_1_rate": 29 / 30,
               "post_autofix_first_check_pass_rate": 1.0,
               "results": [{"pass_at_attempt": 1}] * 29 + [{"pass_at_attempt": None}]}
    assert evaluate_acceptance(summary)["state"] == "met"
    summary["pass_at_1_rate"] = 28 / 30
    assert evaluate_acceptance(summary)["state"] == "not_met"
    summary["pass_at_1_rate"] = 1.0
    summary["total_tasks"] = summary["supported_tasks"] = 1
    assert evaluate_acceptance(summary)["state"] == "not_met"
