"""Token attribution, prompt-cache cost modeling, and pre-registered gate math."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any, Dict, Iterable, List, Mapping, Sequence


PROMPT_ATTRIBUTION_METHOD = (
    "estimate: character share per prompt source, scaled to provider-reported prompt_tokens"
)
PROMPT_ATTRIBUTION_SOURCES = ("system", "task", "tool_results", "prior_assistant")

GATE_REDUCTION_25K_PCT = 30.0
GATE_REDUCTION_100K_PCT = 45.0


@dataclass(frozen=True)
class CacheCostConfig:
    """Relative weighting for prompt-cached multi-turn runs (no vendor dollar prices)."""

    cache_read_multiplier: float = 0.1
    cache_write_multiplier: float = 1.25
    input_weight: float = 1.0
    output_weight: float = 1.0


def attribute_prompt_tokens(prompt_tokens: int, chars: Mapping[str, int]) -> Dict[str, int]:
    """Split provider prompt tokens proportionally by character share using largest remainder."""
    total_chars = sum(max(0, int(v)) for v in chars.values())
    if prompt_tokens <= 0 or total_chars <= 0:
        return {name: 0 for name in chars}
    exact = {name: prompt_tokens * max(0, int(v)) / total_chars for name, v in chars.items()}
    parts = {name: int(value) for name, value in exact.items()}
    leftover = prompt_tokens - sum(parts.values())
    for name in sorted(exact, key=lambda key: (exact[key] - parts[key], key), reverse=True)[:leftover]:
        parts[name] += 1
    return parts


def compute_turn_effective_tokens(
    prompt_tokens: int,
    completion_tokens: int,
    prev_prompt_tokens: int,
    config: CacheCostConfig | None = None,
) -> float:
    """Compute cache-aware effective relative token units for one turn.

    Assumes longest shared prefix up to ``prev_prompt_tokens`` hits cache at
    ``cache_read_multiplier`` and newly appended prompt tokens pay ``cache_write_multiplier``.
    """
    cfg = config or CacheCostConfig()
    if prev_prompt_tokens <= 0:
        cached_read = 0
        cache_write = max(0, prompt_tokens)
    else:
        cached_read = min(max(0, prev_prompt_tokens), max(0, prompt_tokens))
        cache_write = max(0, prompt_tokens - cached_read)
    eff_input = cfg.input_weight * (
        cached_read * cfg.cache_read_multiplier + cache_write * cfg.cache_write_multiplier
    )
    eff_output = cfg.output_weight * max(0, completion_tokens)
    return round(eff_input + eff_output, 4)


def summarize_condition_runs(runs: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    """Aggregate raw tokens, cache-aware effective tokens, turns, wall time, and pass rate."""
    if not runs:
        return {
            "runs": 0,
            "passed": 0,
            "success_rate": 0.0,
            "median_raw_tokens": 0.0,
            "median_effective_tokens": 0.0,
            "median_turns": 0.0,
            "median_wall_time_ms": 0.0,
            "total_raw_tokens": 0,
            "total_effective_tokens": 0.0,
            "total_tool_output_bytes": 0,
            "tool_calls_by_name": {},
        }
    raw_list = [int(r["total_tokens"]) for r in runs]
    eff_list = [float(r["effective_tokens"]) for r in runs]
    turns_list = [int(r["turns"]) for r in runs]
    wall_list = [float(r["wall_time_ms"]) for r in runs]
    passed = sum(1 for r in runs if r.get("passed"))
    tool_counts: Dict[str, int] = {}
    for run in runs:
        for tool_name, count in (run.get("tool_calls_by_name") or {}).items():
            tool_counts[tool_name] = tool_counts.get(tool_name, 0) + int(count)
    return {
        "runs": len(runs),
        "passed": passed,
        "success_rate": round(passed / len(runs), 4),
        "median_raw_tokens": round(float(median(raw_list)), 2),
        "median_effective_tokens": round(float(median(eff_list)), 2),
        "median_turns": round(float(median(turns_list)), 2),
        "median_wall_time_ms": round(float(median(wall_list)), 2),
        "total_raw_tokens": sum(raw_list),
        "total_effective_tokens": round(sum(eff_list), 2),
        "total_tool_output_bytes": sum(int(r.get("total_tool_output_bytes", 0)) for r in runs),
        "tool_calls_by_name": dict(sorted(tool_counts.items())),
    }


def required_reduction_pct(loc: int) -> float:
    """Return pre-registered median token reduction threshold for codebase LOC."""
    return GATE_REDUCTION_100K_PCT if loc >= 100_000 else GATE_REDUCTION_25K_PCT


def evaluate_decision_gate(
    loc: int,
    condition_a_runs: Sequence[Mapping[str, Any]],
    condition_b_runs: Sequence[Mapping[str, Any]],
    *,
    provider_is_mock: bool,
    use_cache_effective: bool = False,
) -> Dict[str, Any]:
    """Evaluate Condition B (Wysna tooling) against Condition A under pre-registered rules."""
    summary_a = summarize_condition_runs(condition_a_runs)
    summary_b = summarize_condition_runs(condition_b_runs)
    threshold_pct = required_reduction_pct(loc)
    scale_band = "100k+" if loc >= 100_000 else ("25k+" if loc >= 25_000 else "sub_25k_smoke")

    metric_key = "median_effective_tokens" if use_cache_effective else "median_raw_tokens"
    a_med = float(summary_a[metric_key])
    b_med = float(summary_b[metric_key])
    reduction_pct = ((a_med - b_med) / a_med) * 100.0 if a_med > 0 else 0.0

    a_succ = float(summary_a["success_rate"])
    b_succ = float(summary_b["success_rate"])

    if provider_is_mock:
        decision = "invalid_mock"
        passed = False
    elif b_succ < a_succ:
        decision = "fail_success_regression"
        passed = False
    elif reduction_pct < threshold_pct:
        decision = "fail_token_threshold"
        passed = False
    else:
        decision = "pass"
        passed = True

    return {
        "decision": decision,
        "passed": passed,
        "provider_is_mock": provider_is_mock,
        "loc": loc,
        "scale_band": scale_band,
        "metric": metric_key,
        "required_reduction_pct": threshold_pct,
        "observed_reduction_pct": round(reduction_pct, 2),
        "condition_a_success_rate": a_succ,
        "condition_b_success_rate": b_succ,
        "condition_a_median_tokens": a_med,
        "condition_b_median_tokens": b_med,
    }
