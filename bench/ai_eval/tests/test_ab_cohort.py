"""Contract tests for the Hito 2 paired A/B cohort harness and persistence oracle."""

import json
import os
from pathlib import Path
import subprocess
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from bench.ai_eval.ab_cohort import (
    COHORT_SCHEMA_VERSION,
    ConditionHarness,
    TASK_FAMILIES,
    _build_default_cohort_tasks,
    build_import_style_ab_tasks,
    evaluate_expansion_gate,
    run_ab_cohort,
)
from bench.ai_eval.providers import MockProvider


def test_default_cohort_covers_five_families():
    tasks = _build_default_cohort_tasks()
    assert {item.family for item in tasks} == set(TASK_FAMILIES)


def test_h2_persistence_oracle_rejects_compiler_valid_partial_write():
    tasks = [item for item in _build_default_cohort_tasks() if item.task.id == "AB-H2-CAPABILITY-04"]

    # Buggy submission compiles cleanly (`wysna check` passes), writes first valid row, then hits > 1000.
    buggy_partial_write = (
        "```wysna\n"
        'import "std/environment" as environment\n'
        'import "std/io" as io\n'
        'import "std/parse" as parse\n'
        'import "std/text" as text\n'
        'fn main() -> unit {\n'
        '    let a = environment.arguments()\n'
        '    let raw = io.read_text(a[1]) else err { exit(2) return }\n'
        '    let out_path = a[2]\n'
        r'    let lines = text.split(text.trim(raw), "\n")' + '\n'
        '    let mut buf = ""\n'
        '    for item in lines {\n'
        '        let n = parse.parse_int(text.trim(item)) else err { exit(3) return }\n'
        '        if n > 1000 { exit(3) return }\n'
        r'        buf = buf + str(n) + "\n"' + '\n'
        '        io.write_text(out_path, text.trim(buf)) else err { exit(3) return }\n'
        '    }\n'
        '    print("committed")\n'
        '}\n'
        'main()\n'
        "```"
    )
    atomic_solution = (
        "```wysna\n"
        'import "std/environment" as environment\n'
        'import "std/io" as io\n'
        'import "std/parse" as parse\n'
        'import "std/text" as text\n'
        'fn main() -> unit {\n'
        '    let a = environment.arguments()\n'
        '    let raw = io.read_text(a[1]) else err { exit(2) return }\n'
        '    let out_path = a[2]\n'
        r'    let lines = text.split(text.trim(raw), "\n")' + '\n'
        '    let mut buf = ""\n'
        '    for item in lines {\n'
        '        let n = parse.parse_int(text.trim(item)) else err { exit(3) return }\n'
        '        if n < 0 or n > 1000 { eprint("value_limit_exceeded") exit(3) return }\n'
        r'        buf = buf + str(n) + "\n"' + '\n'
        '    }\n'
        '    io.write_text(out_path, text.trim(buf)) else err { exit(3) return }\n'
        '    print("committed")\n'
        '}\n'
        'main()\n'
        "```"
    )

    def factory(condition: str) -> MockProvider:
        if condition == "A":
            return MockProvider({"AB-H2-CAPABILITY-04": [buggy_partial_write]})
        return MockProvider({"AB-H2-CAPABILITY-04": [atomic_solution]})

    report = run_ab_cohort(
        tasks,
        factory,
        repo_root=REPO_ROOT,
        repetitions=3,
        max_repair_cycles=0,
    )
    assert report["schema_version"] == COHORT_SCHEMA_VERSION
    assert [r["order"] for r in report["runs"][::2]] == ["AB", "BA", "AB"]

    a_runs = [r for r in report["runs"] if r["condition"] == "A"]
    b_runs = [r for r in report["runs"] if r["condition"] == "B"]
    assert all(not r["passed"] and r["regression"] for r in a_runs), a_runs
    assert all(r["passed"] and not r["regression"] and r["semantic_queries_count"] >= 1 for r in b_runs), b_runs


def test_negative_cases_require_diagnostic_and_preserve_ledger(tmp_path):
    harness = ConditionHarness("A", repo_root=REPO_ROOT)
    task = next(item.task for item in _build_default_cohort_tasks() if item.family == "add_capability")
    cases = [case for case in task.test_cases if case.expected_exit == 3]
    source = tmp_path / "main.wysna"

    source.write_text("exit(3)\n", encoding="utf-8")
    passed, error = harness.run_oracle_tests(str(tmp_path), source.name, cases)
    assert not passed and "stderr" in error

    source.write_text(
        'let arguments = args()\n'
        'write_file(arguments[2], "") else { exit(2) }\n'
        'eprint("value_limit_exceeded")\nexit(3)\n', encoding="utf-8",
    )
    for case in cases:
        passed, error = harness.run_oracle_tests(str(tmp_path), source.name, [case])
        assert not passed and "Persistence invariant" in error, (case.name, error)
    assert len(cases) == 2

    source.write_text('eprint("value_limit_exceeded")\nexit(3)\n', encoding="utf-8")
    assert harness.run_oracle_tests(str(tmp_path), source.name, cases) == (True, None)


def test_collateral_line_tracking_and_decision_gate_thresholds():
    # Verify collateral diff counting against multi-module refactor task.
    refactor_task = [item for item in _build_default_cohort_tasks() if item.task.id == "AB-REFACTOR-03"]
    unscoped_edit = (
        "### formatter.wysna\n```wysna\n"
        "pub fn tag(name: str) -> str {\n    return \"[CORE]\"\n}\n"
        "```\n"
        "### helper.wysna\n```wysna\n"
        "pub fn prefix() -> str {\n    let unused = 1\n    return \"item:\"\n}\n"
        "```"
    )
    report = run_ab_cohort(
        refactor_task,
        lambda _c: MockProvider({"AB-REFACTOR-03": [unscoped_edit]}),
        repo_root=REPO_ROOT,
        repetitions=1,
        max_repair_cycles=0,
    )
    assert all(r["collateral_lines_changed"] >= 1 for r in report["runs"]), report["runs"]

    # Threshold gate: >= 20% token or time improvement with no regression -> fund_expansion
    funded = evaluate_expansion_gate([
        {"condition": "A", "task_id": "T1", "passed": True, "regression": False, "wall_time_ms": 100.0, "total_tokens": 1000},
        {"condition": "B", "task_id": "T1", "passed": True, "regression": False, "wall_time_ms": 70.0, "total_tokens": 750},
    ])
    assert funded["decision"] == "fund_expansion" and funded["median_wall_time_reduction_pct"] == 30.0

    # Threshold unmet (<20%) + slower task in B -> hold_expansion_threshold_unmet with per_task_losses published
    held = evaluate_expansion_gate([
        {"condition": "A", "task_id": "T1", "passed": True, "regression": False, "wall_time_ms": 100.0, "total_tokens": 1000},
        {"condition": "B", "task_id": "T1", "passed": True, "regression": False, "wall_time_ms": 110.0, "total_tokens": 950},
    ])
    assert held["decision"] == "hold_expansion_threshold_unmet" and len(held["per_task_losses"]) == 1

    # Higher regressions in B -> reject_expansion
    rejected = evaluate_expansion_gate([
        {"condition": "A", "task_id": "T1", "passed": True, "regression": False, "wall_time_ms": 100.0, "total_tokens": 1000},
        {"condition": "B", "task_id": "T1", "passed": False, "regression": True, "wall_time_ms": 50.0, "total_tokens": 500},
    ])
    assert rejected["decision"] == "reject_expansion"


def test_ab_cohort_cli_runs_isolated_mock_smoke(tmp_path):
    script = Path(REPO_ROOT) / "bench" / "ai_eval" / "ab_cohort.py"
    out = tmp_path / "ab_report.json"
    proc = subprocess.run(
        [sys.executable, "-I", str(script), "--provider=mock", "--repetitions=1", "--output", str(out)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=90,
    )
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["schema_version"] == COHORT_SCHEMA_VERSION
    assert payload["oracle_revision"] == 2
    assert payload["families_covered"] == sorted(TASK_FAMILIES)
    assert payload["decision"] == "inconclusive"
    assert len(payload["runs"]) == 12  # 6 cohort tasks * 2 conditions


def test_multifile_over_300_loc_task_and_import_style_ab_suite():
    multifile = next(item for item in _build_default_cohort_tasks() if item.task.id == "AB-MULTIFILE-06")
    wysna_files = {k: v for k, v in multifile.task.fixtures.items() if k.endswith(".wysna")}
    assert len(wysna_files) == 5
    total_loc = sum(len(src.splitlines()) for src in wysna_files.values())
    assert total_loc > 300, total_loc

    fixed_validator = (
        "### validator.wysna\n```wysna\n"
        + wysna_files["validator.wysna"]
        .replace(
            "    if item.amount_cents <= 0 {\n        return false\n    }\n",
            "    if item.amount_cents <= 0 or item.amount_cents > 50000 {\n        return false\n    }\n",
        )
        .replace(
            "    if item.amount_cents <= 0 {\n        return item.sku + \":non_positive_amount\"\n    }\n",
            "    if item.amount_cents <= 0 {\n        return item.sku + \":non_positive_amount\"\n    }\n"
            "    if item.amount_cents > 50000 {\n        return item.sku + \":amount_exceeds_limit\"\n    }\n",
        )
        + "```"
    )
    report = run_ab_cohort(
        [multifile],
        lambda _c: MockProvider({"AB-MULTIFILE-06": [fixed_validator]}),
        repo_root=REPO_ROOT,
        repetitions=1,
        max_repair_cycles=0,
    )
    assert all(r["passed"] and r["collateral_lines_changed"] == 0 for r in report["runs"]), report["runs"]

    import_tasks = build_import_style_ab_tasks()
    assert [t.task.id for t in import_tasks] == ["LANG-R01-IMPORT-STYLE"]
    assert dict(import_tasks[0].condition_instructions) == {
        "A": 'Use `import "std/parse" as parse` and call `parse.parse_int(...)`.',
        "B": 'Use `from "std/parse" use { parse_int }` and call `parse_int(...)`.',
    }
    sol_as = (
        '```wysna\nimport "std/parse" as parse\nfn main() {\n'
        '    let parts = "4,5".split(",")\n'
        '    let a = parse.parse_int(parts[0]) else _err { 0 }\n'
        '    let b = parse.parse_int(parts[1]) else _err { 0 }\n'
        '    print("sum=" + str(a + b))\n}\nmain()\n```'
    )
    sol_use = (
        '```wysna\nfrom "std/parse" use { parse_int }\nfn main() {\n'
        '    let parts = "4,5".split(",")\n'
        '    let a = parse_int(parts[0]) else _err { 0 }\n'
        '    let b = parse_int(parts[1]) else _err { 0 }\n'
        '    print("sum=" + str(a + b))\n}\nmain()\n```'
    )
    lang_providers = {}

    def lang_factory(condition):
        solution = sol_as if condition == "A" else sol_use
        provider = MockProvider({"LANG-R01-IMPORT-STYLE": [solution]})
        lang_providers[condition] = provider
        return provider

    lang_report = run_ab_cohort(
        import_tasks,
        lang_factory,
        repo_root=REPO_ROOT,
        repetitions=1,
        max_repair_cycles=0,
        comparison_axis="import-style",
    )
    assert all(r["passed"] for r in lang_report["runs"]), lang_report["runs"]
    assert [r["condition"] for r in lang_report["runs"]] == ["A", "B"]
    assert lang_report["context_mode"] == "none"
    assert lang_report["comparison_axis"] == "import-style"
    assert 'Use `import "std/parse" as parse`' in lang_providers["A"].call_history[0]["user_prompt"]
    assert 'Use `from "std/parse" use { parse_int }`' in lang_providers["B"].call_history[0]["user_prompt"]


def test_interactive_cohort_uses_model_selected_queries_and_counts_extra_turns():
    task = next(item for item in _build_default_cohort_tasks() if item.family == "diagnosis")
    solution = '```wysna\nfn main() -> unit { print(15) }\nmain()\n```'
    providers = {}

    def factory(condition):
        query = {"tool": "read" if condition == "A" else "inspect", "path": "main.wysna"}
        if condition == "B":
            query["symbol"] = "main"
        provider = MockProvider({task.task.id: [json.dumps(query), solution]})
        providers[condition] = provider
        return provider

    report = run_ab_cohort(
        [task], factory, repo_root=REPO_ROOT, repetitions=1, max_repair_cycles=0, interactive=True,
    )
    assert report["context_mode"] == "model-selected"
    a, b = report["runs"]
    assert a["passed"] and b["passed"]
    assert a["model_turns"] == b["model_turns"] == 2
    assert a["files_read_count"] == 1 and b["files_read_count"] == 0
    assert a["semantic_queries_count"] == 0 and b["semantic_queries_count"] == 1
    assert "fn main" not in providers["A"].call_history[0]["user_prompt"]
    assert "fn main" in providers["A"].call_history[1]["user_prompt"]
    assert "wysna.inspect/1" in providers["B"].call_history[1]["user_prompt"]


def test_openai_compatible_provider_retries_429_and_handles_null_content(monkeypatch):
    import io
    import urllib.error
    from bench.ai_eval.providers import OpenAICompatibleProvider

    attempts = {"count": 0}
    seen_headers = []

    class FakeResp:
        def __init__(self, payload):
            self._body = json.dumps(payload).encode("utf-8")

        def read(self):
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    def fake_urlopen(req, context=None, timeout=None):
        attempts["count"] += 1
        seen_headers.append(dict(req.header_items()))
        if attempts["count"] == 1:
            raise urllib.error.HTTPError(
                req.full_url, 429, "Too Many Requests", {"Retry-After": "0"}, io.BytesIO(b'{"error":"rate limit"}')
            )
        return FakeResp({
            "choices": [{"message": {"content": None, "reasoning": "thinking"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 20},
        })

    monkeypatch.setattr("bench.ai_eval.providers.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("bench.ai_eval.providers.time.sleep", lambda _s: None)
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-dummy-token")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    provider = OpenAICompatibleProvider(
        base_url="https://openrouter.ai/api/v1",
        model="nvidia/nemotron-3.5-super-vl:free",
        min_interval_sec=0.0,
    )
    text, tokens, _latency = provider.generate("sys", "usr")
    assert attempts["count"] == 2
    assert text == ""
    assert tokens == {"prompt_tokens": 10, "completion_tokens": 20}
    assert seen_headers[0].get("Http-referer") == "https://github.com/WashingtonYandun/Wysna"
    assert seen_headers[0].get("X-title") == "Wysna AI Eval"


def test_openrouter_rate_limit_is_shared_between_provider_instances(monkeypatch):
    import bench.ai_eval.providers as providers

    current_time = {"value": 0.0}
    delays = []

    def fake_sleep(delay):
        delays.append(delay)
        current_time["value"] += delay

    class FakeResp:
        def read(self):
            return json.dumps({"choices": [{"message": {"content": "ok"}}]}).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setenv("OPENROUTER_API_KEY", "test-dummy-token")
    monkeypatch.setattr(providers, "_openrouter_next_request_at", 0.0)
    monkeypatch.setattr(providers.time, "monotonic", lambda: current_time["value"])
    monkeypatch.setattr(providers.time, "sleep", fake_sleep)
    monkeypatch.setattr(providers.urllib.request, "urlopen", lambda *args, **kwargs: FakeResp())

    first = providers.OpenAICompatibleProvider(base_url="https://openrouter.ai/api/v1")
    second = providers.OpenAICompatibleProvider(base_url="https://openrouter.ai/api/v1")
    first.generate("system", "first")
    second.generate("system", "second")

    assert delays == [3.5]
