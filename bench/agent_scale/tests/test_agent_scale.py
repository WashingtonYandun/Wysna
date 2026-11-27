"""Unit and smoke tests for bench/agent_scale."""

from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SRC_DIR = REPO_ROOT / "src"
for candidate in (str(REPO_ROOT), str(SRC_DIR)):
    if candidate not in sys.path:
        sys.path.insert(0, candidate)

from bench.agent_scale.agent import resolve_confined_path, truncate_output
from bench.agent_scale.generate import generate_project, validate_project
from bench.agent_scale.metrics import (
    GATE_REDUCTION_25K_PCT,
    GATE_REDUCTION_100K_PCT,
    CacheCostConfig,
    attribute_prompt_tokens,
    compute_turn_effective_tokens,
    evaluate_decision_gate,
)
from bench.agent_scale.tasks import ALL_TASKS, prepare_task_workspace


def test_generator_determinism_loc_tolerance_and_seed_check(tmp_path):
    p1 = tmp_path / "gen1"
    p2 = tmp_path / "gen2"
    m1 = generate_project(p1, target_loc=1500, seed=77)
    m2 = generate_project(p2, target_loc=1500, seed=77)

    assert m1["tree_sha256"] == m2["tree_sha256"]
    assert m1["actual_nonblank_loc"] == m2["actual_nonblank_loc"]
    assert abs(m1["actual_nonblank_loc"] - 1500) <= 1500 * 0.05

    assert m1["anchors"]["T1"]["symbol"] in m1["symbols"]
    assert m1["anchors"]["T2"]["symbol"] in m1["symbols"]
    assert m1["anchors"]["T3"]["definition_symbol"] in m1["symbols"]
    assert m1["anchors"]["T4"]["old_symbol"] in m1["symbols"]
    assert "billing.wysna" in m1["reverse_deps"]["pricing.wysna"]
    assert "shipping.wysna" in m1["reverse_deps"]["inventory.wysna"]

    ok, _ms, out = validate_project(p1, repo_root=REPO_ROOT)
    assert ok, out


def test_all_four_oracles_fail_initially_and_pass_on_reference_solutions(tmp_path):
    template = tmp_path / "template"
    generate_project(template, target_loc=1200, seed=11)

    # T1: locate and change tier 2 discount from 15% (85/100) to 25% (75/100)
    t1 = ALL_TASKS["T1"]()
    ws1 = prepare_task_workspace(template, t1, work_root=tmp_path)
    ok_before, _ = t1.oracle(ws1, REPO_ROOT, None)
    assert not ok_before
    pricing = ws1 / "src" / "pricing.wysna"
    pricing.write_text(
        pricing.read_text(encoding="utf-8").replace("(subtotal_cents * 85) / 100", "(subtotal_cents * 75) / 100", 1),
        encoding="utf-8",
    )
    ok_after, details1 = t1.oracle(ws1, REPO_ROOT, None)
    assert ok_after, details1

    # T2: cross-module struct field priority_code on LineItem
    t2 = ALL_TASKS["T2"]()
    ws2 = prepare_task_workspace(template, t2, work_root=tmp_path)
    ok_before, _ = t2.oracle(ws2, REPO_ROOT, None)
    assert not ok_before
    billing_p = ws2 / "src" / "billing.wysna"
    billing_t = billing_p.read_text(encoding="utf-8")
    billing_t = billing_t.replace(
        "    unit_cents: int,\n}",
        "    unit_cents: int,\n    priority_code: int,\n}",
        1,
    )
    billing_t = billing_t.replace(
        'f"{item.sku}:{item.quantity}:{item.unit_cents}"',
        'f"{item.sku}:{item.quantity}:{item.unit_cents}:{item.priority_code}"',
        1,
    )
    billing_t = billing_t.replace(
        "LineItem { sku: sku, quantity: quantity, unit_cents: unit_cents }",
        "LineItem { sku: sku, quantity: quantity, unit_cents: unit_cents, priority_code: 1 }",
        1,
    )
    billing_p.write_text(billing_t, encoding="utf-8")
    ship_p = ws2 / "src" / "shipping.wysna"
    ship_p.write_text(
        ship_p.read_text(encoding="utf-8").replace(
            'LineItem { sku: "SHIP-DEFAULT", quantity: qty, unit_cents: unit_cents }',
            'LineItem { sku: "SHIP-DEFAULT", quantity: qty, unit_cents: unit_cents, priority_code: 1 }',
            1,
        ),
        encoding="utf-8",
    )
    audit_p = ws2 / "src" / "audit.wysna"
    audit_p.write_text(
        audit_p.read_text(encoding="utf-8").replace(
            'LineItem { sku: "AUD-1", quantity: qty, unit_cents: 100 }',
            'LineItem { sku: "AUD-1", quantity: qty, unit_cents: 100, priority_code: 1 }',
            1,
        ),
        encoding="utf-8",
    )
    ok_after, details2 = t2.oracle(ws2, REPO_ROOT, None)
    assert ok_after, details2

    # T3: fix cross-module injected type error in pricing.compute_tax_cents
    t3 = ALL_TASKS["T3"]()
    ws3 = prepare_task_workspace(template, t3, work_root=tmp_path)
    ok_before, _ = t3.oracle(ws3, REPO_ROOT, None)
    assert not ok_before
    # Copy clean pricing.wysna from untouched template to simulate reference fix.
    (ws3 / "src" / "pricing.wysna").write_text(
        (template / "src" / "pricing.wysna").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    ok_after, details3 = t3.oracle(ws3, REPO_ROOT, None)
    assert ok_after, details3

    # T4: rename reserve_stock_units -> allocate_stock_units across src/
    t4 = ALL_TASKS["T4"]()
    ws4 = prepare_task_workspace(template, t4, work_root=tmp_path)
    ok_before, _ = t4.oracle(ws4, REPO_ROOT, None)
    assert not ok_before
    for fname in ("inventory.wysna", "shipping.wysna", "audit.wysna", "main.wysna"):
        fpath = ws4 / "src" / fname
        fpath.write_text(
            fpath.read_text(encoding="utf-8").replace("reserve_stock_units", "allocate_stock_units"),
            encoding="utf-8",
        )
    ok_after, details4 = t4.oracle(ws4, REPO_ROOT, None)
    assert ok_after, details4


def test_sandbox_confinement_and_output_truncation(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "secret.txt"
    outside.write_text("top-secret", encoding="utf-8")

    with pytest.raises(ValueError):
        resolve_confined_path(ws, "../secret.txt")
    with pytest.raises(ValueError):
        resolve_confined_path(ws, "/etc/passwd")

    link = ws / "escape_link"
    os.symlink(outside, link)
    with pytest.raises(ValueError, match="Symlink escapes"):
        resolve_confined_path(ws, "escape_link")

    # Truncation keeps valid UTF-8 and marks omitted byte count
    payload = "é" * 50  # 100 UTF-8 bytes
    capped = truncate_output(payload, max_bytes=21)
    assert capped.endswith("\n[truncated 79 bytes]")


def test_metrics_cache_math_and_decision_gate():
    parts = attribute_prompt_tokens(100, {"system": 250, "task": 150, "tool_results": 400, "prior_assistant": 200})
    assert sum(parts.values()) == 100
    assert parts["tool_results"] == 40

    cfg = CacheCostConfig(cache_read_multiplier=0.1, cache_write_multiplier=1.25)
    t0 = compute_turn_effective_tokens(1000, 100, prev_prompt_tokens=0, config=cfg)
    t1 = compute_turn_effective_tokens(1500, 100, prev_prompt_tokens=1000, config=cfg)
    assert t0 == 1350.0  # 1000 * 1.25 + 100
    assert t1 == 825.0   # 1000 * 0.1 + 500 * 1.25 + 100

    assert GATE_REDUCTION_25K_PCT == 30.0
    assert GATE_REDUCTION_100K_PCT == 45.0

    a_ok = [{"passed": True, "total_tokens": 10000, "effective_tokens": 5000.0, "turns": 5, "wall_time_ms": 100.0}]
    b_win = [{"passed": True, "total_tokens": 6000, "effective_tokens": 3000.0, "turns": 4, "wall_time_ms": 80.0}]
    b_small_win = [{"passed": True, "total_tokens": 8500, "effective_tokens": 4500.0, "turns": 4, "wall_time_ms": 80.0}]
    b_fail = [{"passed": False, "total_tokens": 4000, "effective_tokens": 2000.0, "turns": 4, "wall_time_ms": 80.0}]

    assert evaluate_decision_gate(25000, a_ok, b_win, provider_is_mock=True)["decision"] == "invalid_mock"
    assert evaluate_decision_gate(25000, a_ok, b_win, provider_is_mock=False)["decision"] == "pass"
    assert evaluate_decision_gate(25000, a_ok, b_small_win, provider_is_mock=False)["decision"] == "fail_token_threshold"
    assert evaluate_decision_gate(100000, a_ok, b_win, provider_is_mock=False)["decision"] == "fail_token_threshold"
    assert evaluate_decision_gate(25000, a_ok, b_fail, provider_is_mock=False)["decision"] == "fail_success_regression"


def test_cli_mock_end_to_end_smoke(tmp_path):
    report_path = tmp_path / "scale_smoke.json"
    proc = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "bench" / "agent_scale" / "run.py"),
            "--loc", "1500",
            "--conditions", "A_baseline", "B_wysna",
            "--tasks", "T1",
            "--repetitions", "1",
            "--provider", "mock",
            "--output", str(report_path),
        ],
        cwd=REPO_ROOT,
        env=dict(os.environ, PYTHONPATH=f"{SRC_DIR}{os.pathsep}{REPO_ROOT}"),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert proc.returncode == 0, proc.stderr + proc.stdout
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data["schema_version"] == "wysna.agent-scale/1"
    assert data["provider_is_mock"] is True
    assert data["decision_gate"]["decision"] == "invalid_mock"
    assert len(data["runs"]) == 2
    assert {r["condition"] for r in data["runs"]} == {"A_baseline", "B_wysna"}
    for run in data["runs"]:
        assert run["finished_reason"] == "done"
        assert run["turns"] == 2
