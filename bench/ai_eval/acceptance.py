"""Canonical authoring thresholds shared by the evaluator and its documentation."""

from statistics import median

RAW_PASS_AT_1_MIN = 0.90
POST_AUTOFIX_FUNCTIONAL_PASS_AT_1_MIN = 0.95
MEDIAN_REPAIR_SUBMISSIONS_MAX = 1.5
REQUIRED_TASKS = 30


def evaluate_acceptance(summary: dict) -> dict:
    """Mock results validate the harness and cannot establish real-model effectiveness."""
    thresholds = {
        "raw_pass_at_1_rate_min": RAW_PASS_AT_1_MIN,
        "pass_at_1_rate_min": POST_AUTOFIX_FUNCTIONAL_PASS_AT_1_MIN,
        "median_repair_submissions_max": MEDIAN_REPAIR_SUBMISSIONS_MAX,
        "required_tasks": REQUIRED_TASKS,
    }
    if summary["provider_name"] == "mock":
        return {"state": "not_measured_with_real_model", "thresholds": thresholds}
    repair_counts = [result["pass_at_attempt"] - 1 for result in summary["results"]
                     if result.get("pass_at_attempt") is not None]
    repair_median = median(repair_counts) if repair_counts else None
    checks = {
        "full_corpus": summary["total_tasks"] == summary["supported_tasks"] == REQUIRED_TASKS,
        "raw_pass_at_1": summary["raw_pass_at_1_rate"] >= RAW_PASS_AT_1_MIN,
        "post_autofix_functional_pass_at_1": summary["pass_at_1_rate"] >= POST_AUTOFIX_FUNCTIONAL_PASS_AT_1_MIN,
        "median_repairs": repair_median is not None and repair_median <= MEDIAN_REPAIR_SUBMISSIONS_MAX,
    }
    return {"state": "met" if all(checks.values()) else "not_met", "thresholds": thresholds,
            "checks": checks, "median_repair_submissions_among_resolved": repair_median,
            "resolved_tasks": len(repair_counts)}
