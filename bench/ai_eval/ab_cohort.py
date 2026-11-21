"""Paired A/B cohort harness for Agent-First evaluation (Hito 2 / R7)."""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import difflib
import json
import os
from statistics import median
import subprocess
import sys
import tempfile
from typing import Any, Callable, Dict, List, Optional, Sequence, Tuple

if __name__ == "__main__" and (__package__ is None or __package__ == ""):
    _ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    sys.path[:0] = [_ROOT, os.path.join(_ROOT, "src")]

from bench.ai_eval.harness import EvaluationHarness, SYSTEM_PROMPT
from bench.ai_eval.models import SCHEMA_VERSION, Task, TaskEvaluationResult, TaskTestCase
from bench.ai_eval.providers import GeminiProvider, LlmProvider, MockProvider, OpenAICompatibleProvider

TASK_FAMILIES = ("authoring", "syntax_type_repair", "refactor", "add_capability", "diagnosis")
COHORT_SCHEMA_VERSION = "wysna.ai-eval-ab/1"


@dataclass(frozen=True)
class CohortTask:
    task: Task
    family: str
    allowed_edit_files: Tuple[str, ...]
    invariant_checker: Optional[Callable[[str, TaskEvaluationResult], Optional[str]]] = None
    condition_instructions: Tuple[Tuple[str, str], ...] = ()


def _build_default_cohort_tasks() -> List[CohortTask]:
    """Return canonical tasks spanning all 5 Hito 2 evaluation families."""
    return [
        CohortTask(
            task=Task(
                id="AB-AUTHOR-01",
                domain="cli_scripting",
                title="Author bounded positive integer sum CLI",
                status="supported",
                instruction=(
                    "Write `main.wysna` that parses integer arguments (excluding argv[0]), "
                    "rejects any negative value by printing 'invalid' to stderr and exiting 2, "
                    "and prints the sum otherwise."
                ),
                test_cases=[
                    TaskTestCase(name="valid", args=["4", "5", "6"], expected_stdout="15\n", expected_exit=0),
                    TaskTestCase(name="negative", args=["4", "-1"], expected_exit=2, expected_stderr="invalid\n"),
                ],
            ),
            family="authoring",
            allowed_edit_files=("main.wysna",),
        ),
        CohortTask(
            task=Task(
                id="AB-REPAIR-02",
                domain="cli_scripting",
                title="Repair return type and optional unwrap",
                status="supported",
                instruction="Fix `main.wysna` so `safe_double(maybe: int?) -> int` unwraps optionals and prints 20.",
                fixtures={
                    "main.wysna": (
                        "fn safe_double(maybe: int?) -> int {\n"
                        "    let value = maybe\n"
                        "    return value * 2\n"
                        "}\n"
                        "fn main() -> unit {\n"
                        "    let n: int? = Some(10)\n"
                        "    print(safe_double(n))\n"
                        "}\n"
                        "main()\n"
                    ),
                },
                test_cases=[TaskTestCase(name="double", expected_stdout="20\n", expected_exit=0)],
            ),
            family="syntax_type_repair",
            allowed_edit_files=("main.wysna",),
        ),
        CohortTask(
            task=Task(
                id="AB-REFACTOR-03",
                domain="multi_module",
                title="Refactor shared formatting helper without breaking untouched sibling",
                status="supported",
                instruction="Update `formatter.wysna` `tag(name)` to bracket uppercase names while keeping `helper.wysna` unchanged.",
                fixtures={
                    "formatter.wysna": (
                        "pub fn tag(name: str) -> str {\n"
                        "    return \"[\"+name+\"]\"\n"
                        "}\n"
                    ),
                    "helper.wysna": (
                        "pub fn prefix() -> str {\n"
                        "    return \"item:\"\n"
                        "}\n"
                    ),
                    "main.wysna": (
                        "import \"formatter.wysna\" as fmt\n"
                        "import \"helper.wysna\" as helper\n"
                        "fn main() -> unit {\n"
                        "    print(helper.prefix() + fmt.tag(\"core\"))\n"
                        "}\n"
                        "main()\n"
                    ),
                },
                test_cases=[TaskTestCase(name="refactored", expected_stdout="item:[CORE]\n", expected_exit=0)],
            ),
            family="refactor",
            allowed_edit_files=("formatter.wysna",),
        ),
        CohortTask(
            task=Task(
                id="AB-H2-CAPABILITY-04",
                domain="data_files",
                title="Enforce upper value bound with atomic rollback on persistence ledger",
                status="supported",
                instruction=(
                    "Extend `main.wysna` so values > 1000 are rejected BEFORE writing `ledger.txt`, "
                    "print diagnostic `value_limit_exceeded` to stderr and exit 3 while keeping the output file "
                    "intact on partial invalid batches."
                ),
                fixtures={
                    "batch_invalid.txt": "10\n1500\n",
                    "batch_valid.txt": "10\n200\n",
                    "main.wysna": (
                        'import "std/environment" as environment\n'
                        'import "std/io" as io\n'
                        'import "std/parse" as parse\n'
                        'import "std/text" as text\n'
                        'fn main() -> unit {\n'
                        '    let a = environment.arguments()\n'
                        '    let raw = io.read_text(a[1]) else err { exit(2) return }\n'
                        '    let out_path = a[2]\n'
                        r'    let lines = text.split(text.trim(raw), "\n")' + '\n'
                        '    for item in lines {\n'
                        '        let n = parse.parse_int(text.trim(item)) else err { exit(3) return }\n'
                        '        if n < 0 { exit(3) return }\n'
                        r'        io.write_text(out_path, str(n) + "\n") else err { exit(3) return }' + '\n'
                        '    }\n'
                        '    print("committed")\n'
                        '}\n'
                        'main()\n'
                    ),
                },
                test_cases=[
                    TaskTestCase(
                        name="invalid_batch_must_not_persist_partial_rows",
                        args=["batch_invalid.txt", "ledger.txt"],
                        expected_exit=3,
                        expected_stderr="value_limit_exceeded\n",
                        preserved_files={"ledger.txt": None},
                    ),
                    TaskTestCase(
                        name="invalid_batch_preserves_previous_ledger",
                        args=["batch_invalid.txt", "ledger.txt"],
                        expected_exit=3,
                        expected_stderr="value_limit_exceeded\n",
                        preserved_files={"ledger.txt": "7\r\n42\r\n"},
                    ),
                    TaskTestCase(
                        name="valid_batch_persists",
                        args=["batch_valid.txt", "ledger_ok.txt"],
                        expected_stdout="committed\n",
                        expected_exit=0,
                        file_assertions={"ledger_ok.txt": "10\n200"},
                    ),
                ],
            ),
            family="add_capability",
            allowed_edit_files=("main.wysna",),
        ),
        CohortTask(
            task=Task(
                id="AB-DIAGNOSIS-05",
                domain="cli_scripting",
                title="Diagnose inclusive range boundary defect",
                status="supported",
                instruction="Fix the off-by-one loop bound in `main.wysna` so sum of 1..=5 prints 15.",
                fixtures={
                    "main.wysna": (
                        "fn main() -> unit {\n"
                        "    let mut acc = 0\n"
                        "    for i in 1..5 {\n"
                        "        acc = acc + i\n"
                        "    }\n"
                        "    print(acc)\n"
                        "}\n"
                        "main()\n"
                    ),
                },
                test_cases=[TaskTestCase(name="sum", expected_stdout="15\n", expected_exit=0)],
            ),
            family="diagnosis",
            allowed_edit_files=("main.wysna",),
        ),
        CohortTask(
            task=Task(
                id="AB-MULTIFILE-06",
                domain="multi_module",
                title="Localized rule update in >300 LOC 5-module ledger pipeline",
                status="supported",
                instruction=(
                    "Update `validator.wysna` only so `validate_item` also rejects items whose "
                    "`amount_cents > 50000`, and `reject_reasons` appends `sku + \":amount_exceeds_limit\"` "
                    "for those items while preserving all other checks and untouched sibling modules."
                ),
                fixtures={
                    "input_ledger.txt": "SKU-01,ops,12000\nSKU-02,cloud,75000\nSKU-03,travel,5000\n,ops,300\n",
                    "models.wysna": (
                        "pub struct LineItem {\n"
                        "    sku: str,\n"
                        "    category: str,\n"
                        "    amount_cents: int,\n"
                        "}\n\n"
                        "pub struct LedgerSummary {\n"
                        "    valid_count: int,\n"
                        "    rejected_count: int,\n"
                        "    total_cents: int,\n"
                        "    ops_cents: int,\n"
                        "    cloud_cents: int,\n"
                        "    other_cents: int,\n"
                        "    first_rejection: str,\n"
                        "}\n\n"
                        "pub fn make_item(sku: str, category: str, amount_cents: int) -> LineItem {\n"
                        "    return LineItem { sku: sku, category: category, amount_cents: amount_cents }\n"
                        "}\n\n"
                        "pub fn make_summary(\n"
                        "    valid_count: int,\n"
                        "    rejected_count: int,\n"
                        "    total_cents: int,\n"
                        "    ops_cents: int,\n"
                        "    cloud_cents: int,\n"
                        "    other_cents: int,\n"
                        "    first_rejection: str,\n"
                        ") -> LedgerSummary {\n"
                        "    return LedgerSummary {\n"
                        "        valid_count: valid_count,\n"
                        "        rejected_count: rejected_count,\n"
                        "        total_cents: total_cents,\n"
                        "        ops_cents: ops_cents,\n"
                        "        cloud_cents: cloud_cents,\n"
                        "        other_cents: other_cents,\n"
                        "        first_rejection: first_rejection,\n"
                        "    }\n"
                        "}\n\n"
                        "pub fn format_cents(cents: int) -> str {\n"
                        "    let dollars = cents / 100\n"
                        "    let rem = cents % 100\n"
                        "    if rem < 10 {\n"
                        "        return str(dollars) + \".0\" + str(rem)\n"
                        "    }\n"
                        "    return str(dollars) + \".\" + str(rem)\n"
                        "}\n\n"
                        "pub fn is_priority_category(category: str) -> bool {\n"
                        "    return category == \"ops\" or category == \"cloud\"\n"
                        "}\n\n"
                        "pub fn item_label(item: LineItem) -> str {\n"
                        "    return item.sku + \"[\" + item.category + \"]=\" + format_cents(item.amount_cents)\n"
                        "}\n\n"
                        "pub fn empty_summary() -> LedgerSummary {\n"
                        "    return make_summary(0, 0, 0, 0, 0, 0, \"none\")\n"
                        "}\n\n"
                        "pub fn item_amount_dollars(item: LineItem) -> int {\n"
                        "    return item.amount_cents / 100\n"
                        "}\n\n"
                        "pub fn is_high_value(item: LineItem, threshold: int) -> bool {\n"
                        "    return item.amount_cents >= threshold\n"
                        "}\n\n"
                        "pub fn summary_total_items(summary: LedgerSummary) -> int {\n"
                        "    return summary.valid_count + summary.rejected_count\n"
                        "}\n"
                    ),
                    "parser.wysna": (
                        "import \"std/parse\" as parse\n"
                        "import \"models.wysna\" as models\n\n"
                        "pub fn normalize_category(raw: str) -> str {\n"
                        "    let cleaned = raw.trim().to_lower()\n"
                        "    if cleaned == \"\" {\n"
                        "        return \"other\"\n"
                        "    }\n"
                        "    return cleaned\n"
                        "}\n\n"
                        "pub fn parse_row(line: str) -> models.LineItem ! str {\n"
                        "    let trimmed = line.trim()\n"
                        "    let cols = trimmed.split(\",\")\n"
                        "    if len(cols) != 3 {\n"
                        "        return Err(\"expected 3 columns\")\n"
                        "    }\n"
                        "    let sku = cols[0].trim()\n"
                        "    let cat = normalize_category(cols[1])\n"
                        "    let amount = parse.parse_int(cols[2].trim()) else _err {\n"
                        "        return Err(\"invalid integer amount\")\n"
                        "    }\n"
                        "    return Ok(models.make_item(sku, cat, amount))\n"
                        "}\n\n"
                        "pub fn parse_lines(raw: str) -> list[models.LineItem] ! str {\n"
                        "    let rows = raw.trim().split(\"\\n\")\n"
                        "    let mut out: list[models.LineItem] = []\n"
                        "    for row in rows {\n"
                        "        let t = row.trim()\n"
                        "        if t == \"\" {\n"
                        "            continue\n"
                        "        }\n"
                        "        let parsed = parse_row(t)?\n"
                        "        out.append(parsed)\n"
                        "    }\n"
                        "    return Ok(out)\n"
                        "}\n\n"
                        "pub fn count_raw_rows(raw: str) -> int {\n"
                        "    let rows = raw.trim().split(\"\\n\")\n"
                        "    let mut total = 0\n"
                        "    for row in rows {\n"
                        "        if row.trim() != \"\" {\n"
                        "            total = total + 1\n"
                        "        }\n"
                        "    }\n"
                        "    return total\n"
                        "}\n\n"
                        "pub fn extract_skus(items: list[models.LineItem]) -> list[str] {\n"
                        "    let mut skus: list[str] = []\n"
                        "    for item in items {\n"
                        "        skus.append(item.sku)\n"
                        "    }\n"
                        "    return skus\n"
                        "}\n\n"
                        "pub fn filter_category(items: list[models.LineItem], cat: str) -> list[models.LineItem] {\n"
                        "    let target = normalize_category(cat)\n"
                        "    let mut matched: list[models.LineItem] = []\n"
                        "    for item in items {\n"
                        "        if item.category == target {\n"
                        "            matched.append(item)\n"
                        "        }\n"
                        "    }\n"
                        "    return matched\n"
                        "}\n\n"
                        "pub fn sum_raw_amounts(items: list[models.LineItem]) -> int {\n"
                        "    let mut acc = 0\n"
                        "    for item in items {\n"
                        "        acc = acc + item.amount_cents\n"
                        "    }\n"
                        "    return acc\n"
                        "}\n"
                    ),
                    "validator.wysna": (
                        "import \"models.wysna\" as models\n\n"
                        "pub fn has_nonempty_sku(sku: str) -> bool {\n"
                        "    return sku.trim() != \"\"\n"
                        "}\n\n"
                        "pub fn has_valid_category(category: str) -> bool {\n"
                        "    return category.trim() != \"\"\n"
                        "}\n\n"
                        "pub fn validate_item(item: models.LineItem) -> bool {\n"
                        "    if not has_nonempty_sku(item.sku) {\n"
                        "        return false\n"
                        "    }\n"
                        "    if not has_valid_category(item.category) {\n"
                        "        return false\n"
                        "    }\n"
                        "    if item.amount_cents <= 0 {\n"
                        "        return false\n"
                        "    }\n"
                        "    return true\n"
                        "}\n\n"
                        "pub fn rejection_tag(item: models.LineItem) -> str {\n"
                        "    if not has_nonempty_sku(item.sku) {\n"
                        "        return \"missing_sku\"\n"
                        "    }\n"
                        "    if not has_valid_category(item.category) {\n"
                        "        return item.sku + \":missing_category\"\n"
                        "    }\n"
                        "    if item.amount_cents <= 0 {\n"
                        "        return item.sku + \":non_positive_amount\"\n"
                        "    }\n"
                        "    return \"\"\n"
                        "}\n\n"
                        "pub fn filter_valid(items: list[models.LineItem]) -> list[models.LineItem] {\n"
                        "    let mut out: list[models.LineItem] = []\n"
                        "    for item in items {\n"
                        "        if validate_item(item) {\n"
                        "            out.append(item)\n"
                        "        }\n"
                        "    }\n"
                        "    return out\n"
                        "}\n\n"
                        "pub fn reject_reasons(items: list[models.LineItem]) -> list[str] {\n"
                        "    let mut reasons: list[str] = []\n"
                        "    for item in items {\n"
                        "        if not validate_item(item) {\n"
                        "            reasons.append(rejection_tag(item))\n"
                        "        }\n"
                        "    }\n"
                        "    return reasons\n"
                        "}\n\n"
                        "pub fn count_rejected(items: list[models.LineItem]) -> int {\n"
                        "    let reasons = reject_reasons(items)\n"
                        "    return len(reasons)\n"
                        "}\n\n"
                        "pub fn all_items_valid(items: list[models.LineItem]) -> bool {\n"
                        "    return count_rejected(items) == 0\n"
                        "}\n"
                    ),
                    "reporter.wysna": (
                        "import \"models.wysna\" as models\n\n"
                        "pub fn summarize(valid: list[models.LineItem], rejected: list[str]) -> models.LedgerSummary {\n"
                        "    let mut total_cents = 0\n"
                        "    let mut ops_cents = 0\n"
                        "    let mut cloud_cents = 0\n"
                        "    let mut other_cents = 0\n"
                        "    for item in valid {\n"
                        "        total_cents = total_cents + item.amount_cents\n"
                        "        if item.category == \"ops\" {\n"
                        "            ops_cents = ops_cents + item.amount_cents\n"
                        "        } elif item.category == \"cloud\" {\n"
                        "            cloud_cents = cloud_cents + item.amount_cents\n"
                        "        } else {\n"
                        "            other_cents = other_cents + item.amount_cents\n"
                        "        }\n"
                        "    }\n"
                        "    let first_rej = if len(rejected) > 0 { rejected[0] } else { \"none\" }\n"
                        "    return models.make_summary(\n"
                        "        len(valid),\n"
                        "        len(rejected),\n"
                        "        total_cents,\n"
                        "        ops_cents,\n"
                        "        cloud_cents,\n"
                        "        other_cents,\n"
                        "        first_rej,\n"
                        "    )\n"
                        "}\n\n"
                        "pub fn format_summary_line(summary: models.LedgerSummary) -> str {\n"
                        "    let total_str = models.format_cents(summary.total_cents)\n"
                        "    return \"valid=\" + str(summary.valid_count) + \" rejected=\" + str(summary.rejected_count) + \" total=\" + total_str + \" first=\" + summary.first_rejection\n"
                        "}\n\n"
                        "pub fn category_share(part: int, whole: int) -> int {\n"
                        "    if whole <= 0 {\n"
                        "        return 0\n"
                        "    }\n"
                        "    return (part * 100) / whole\n"
                        "}\n\n"
                        "pub fn format_breakdown(summary: models.LedgerSummary) -> str {\n"
                        "    let ops_pct = category_share(summary.ops_cents, summary.total_cents)\n"
                        "    let cloud_pct = category_share(summary.cloud_cents, summary.total_cents)\n"
                        "    return \"ops=\" + str(ops_pct) + \"% cloud=\" + str(cloud_pct) + \"%\"\n"
                        "}\n\n"
                        "pub fn format_other_breakdown(summary: models.LedgerSummary) -> str {\n"
                        "    let other_pct = category_share(summary.other_cents, summary.total_cents)\n"
                        "    return \"other=\" + str(other_pct) + \"%\"\n"
                        "}\n\n"
                        "pub fn format_full_report(summary: models.LedgerSummary) -> str {\n"
                        "    return format_summary_line(summary) + \"\\n\" + format_breakdown(summary) + \" \" + format_other_breakdown(summary)\n"
                        "}\n"
                    ),
                    "main.wysna": (
                        "import \"std/environment\" as environment\n"
                        "import \"std/io\" as io\n"
                        "import \"parser.wysna\" as parser\n"
                        "import \"validator.wysna\" as validator\n"
                        "import \"reporter.wysna\" as reporter\n\n"
                        "fn run_ledger(input_path: str) -> int {\n"
                        "    let raw = io.read_text(input_path) else _err {\n"
                        "        eprint(\"read_failed\")\n"
                        "        return 2\n"
                        "    }\n"
                        "    let items = parser.parse_lines(raw) else err {\n"
                        "        eprint(err)\n"
                        "        return 3\n"
                        "    }\n"
                        "    let valid = validator.filter_valid(items)\n"
                        "    let rejected = validator.reject_reasons(items)\n"
                        "    let summary = reporter.summarize(valid, rejected)\n"
                        "    print(reporter.format_summary_line(summary))\n"
                        "    print(reporter.format_breakdown(summary))\n"
                        "    return 0\n"
                        "}\n\n"
                        "fn main() -> unit {\n"
                        "    let argv = environment.arguments()\n"
                        "    if len(argv) < 2 {\n"
                        "        eprint(\"usage: ledger <path>\")\n"
                        "        exit(2)\n"
                        "        return\n"
                        "    }\n"
                        "    let code = run_ledger(argv[1])\n"
                        "    if code != 0 {\n"
                        "        exit(code)\n"
                        "    }\n"
                        "}\n"
                        "main()\n"
                    ),
                },
                test_cases=[
                    TaskTestCase(
                        name="ledger_rejects_over_limit_and_missing_sku",
                        args=["input_ledger.txt"],
                        expected_stdout="valid=2 rejected=2 total=170.00 first=SKU-02:amount_exceeds_limit\nops=70% cloud=0%\n",
                        expected_exit=0,
                    ),
                ],
            ),
            family="refactor",
            allowed_edit_files=("validator.wysna",),
        ),
    ]


def build_import_style_ab_tasks() -> List[CohortTask]:
    """Return one task whose A/B conditions differ only by import syntax."""
    common_cases = [
        TaskTestCase(
            name="summary",
            expected_stdout="sum=9\n",
            expected_exit=0,
        ),
    ]
    return [
        CohortTask(
            task=Task(
                id="LANG-R01-IMPORT-STYLE",
                domain="cli_scripting",
                title="Parse and sum two comma-separated integers",
                status="supported",
                instruction=(
                    "Write `main.wysna` to split the string `\"4,5\"`, parse both values as integers, "
                    "sum them, and print exactly `sum=9`. Use the import form specified for this run."
                ),
                test_cases=common_cases,
            ),
            family="authoring",
            allowed_edit_files=("main.wysna",),
            condition_instructions=(
                ("A", 'Use `import "std/parse" as parse` and call `parse.parse_int(...)`.'),
                ("B", 'Use `from "std/parse" use { parse_int }` and call `parse_int(...)`.'),
            ),
        ),
    ]


def _collect_semantic_context(harness: EvaluationHarness, task: Task) -> Tuple[str, int]:
    """Collect read-only Condition B semantic graph & explanation telemetry inside an isolated temp copy."""
    if not task.fixtures:
        return "", 0
    with tempfile.TemporaryDirectory(prefix="wysna-ab-semantic-") as tmpdir:
        for rel_path, content in task.fixtures.items():
            target = harness._resolve_confined_path(tmpdir, rel_path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        entry = task.entrypoint
        if not (os.path.join(tmpdir, entry) and os.path.isfile(os.path.join(tmpdir, entry))):
            return "", 0
        env = os.environ.copy()
        env["PYTHONPATH"] = f"{os.path.join(harness.repo_root, 'src')}{os.pathsep}{harness.repo_root}"
        snippets: List[str] = []
        queries = 0
        for subcmd in (
            ["graph", entry, "--output-format=json-v1"],
            ["inspect", entry, "--output-format=json-v1"],
            ["explain", entry, "--output-format=json-v1"],
        ):
            proc = subprocess.run(
                harness.compiler_cmd + subcmd,
                cwd=tmpdir,
                capture_output=True,
                text=True,
                timeout=harness.timeout_seconds,
                env=env,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                queries += 1
                snippets.append(f"### Semantic Tool ({subcmd[0]})\n```json\n{proc.stdout.strip()[:2500]}\n```")
        return "\n".join(snippets), queries


def _count_collateral_lines(
    harness: EvaluationHarness,
    fixtures: Dict[str, str],
    completion: str,
    entrypoint: str,
    allowed_edit_files: Sequence[str],
) -> Tuple[int, int]:
    """Count modified files and changed lines outside allowed target files."""
    extracted = harness.extract_code_blocks(completion)
    if not extracted:
        extracted = {entrypoint: completion}
    allowed = set(allowed_edit_files)
    collateral_lines = 0
    for fname, new_content in extracted.items():
        if fname in allowed:
            continue
        old_content = fixtures.get(fname, "")
        diff = list(
            difflib.unified_diff(
                old_content.splitlines(),
                new_content.splitlines(),
                lineterm="",
            )
        )
        collateral_lines += sum(
            1 for line in diff if (line.startswith("+") or line.startswith("-")) and not line.startswith(("+++", "---"))
        )
    return len(extracted), collateral_lines


class ConditionHarness(EvaluationHarness):
    """Variant of EvaluationHarness supporting persistence-safe state assertions and Condition A/B context."""

    def __init__(self, condition: str, *args: Any, interactive: bool = False, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.condition = condition
        self.interactive = interactive
        self.tool_turns = 0
        self.read_count = 0
        self.semantic_count = 0

    def workspace_context(self, fixtures):
        if self.interactive:
            return "".join(f"- {name}\n" for name in fixtures)
        return super().workspace_context(fixtures)

    def generate_completion(self, provider, prompt, history, workspace):
        if not self.interactive:
            return super().generate_completion(provider, prompt, history, workspace)
        choices = 'read'
        if self.condition == "B":
            choices += ', graph, inspect'
        tool_prompt = SYSTEM_PROMPT + (
            "\nBefore submitting code you may request up to two read-only queries by replying with only "
            f'one JSON object: {{"tool":"<one of {choices}>","path":"file.wysna","symbol":"symbol name"}}. '
            "The symbol field is required for graph and inspect. Always finish by returning the Wysna code."
        )
        turns = list(history)
        totals = {"prompt_tokens": 0, "completion_tokens": 0}
        total_latency = 0.0
        for remaining in (2, 1, 0):
            instruction = tool_prompt if remaining else SYSTEM_PROMPT + "\nReturn the completed code now."
            completion, tokens, latency = provider.generate(instruction, prompt, turns)
            for key in totals:
                totals[key] += tokens.get(key, 0)
            total_latency += latency
            if not remaining:
                break
            try:
                query = json.loads(completion)
            except (ValueError, RecursionError):
                break
            if not isinstance(query, dict) or "tool" not in query:
                break
            answer = self._query_workspace(workspace, query)
            self.tool_turns += 1
            turns.extend((
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": completion},
            ))
            prompt = "Workspace query result:\n" + answer
        return completion, totals, total_latency

    def _query_workspace(self, workspace, query):
        tool, name, symbol = (query.get(key) for key in ("tool", "path", "symbol"))
        try:
            if not isinstance(name, str) or not name.endswith(".wysna"):
                raise ValueError("expected a workspace .wysna file")
            path = self._resolve_confined_path(workspace, name)
            if tool == "read":
                self.read_count += 1
                with path.open(encoding="utf-8") as source:
                    return source.read(6000)
            if self.condition != "B" or tool not in ("graph", "inspect"):
                raise ValueError("unknown workspace query")
            if not isinstance(symbol, str) or not symbol.strip() or len(symbol) > 256:
                raise ValueError("provide a short symbol name")
            self.semantic_count += 1
            options = ["--rel=impact"] if tool == "graph" else ["--with-deps"]
            env = dict(os.environ, PYTHONPATH=os.pathsep.join((os.path.join(self.repo_root, "src"), self.repo_root)))
            result = subprocess.run(
                self.compiler_cmd + [tool, str(path), "--symbol=" + symbol, "--output-format=json-v1", *options],
                cwd=workspace, capture_output=True, encoding="utf-8", errors="replace", env=env,
                timeout=self.timeout_seconds,
            )
            response = result.stdout or result.stderr
            return response[:6000] + ("\n[additional output omitted]" if len(response) > 6000 else "")
        except (ValueError, OSError, subprocess.TimeoutExpired) as error:
            return f"Query failed: {error}"


def evaluate_expansion_gate(
    paired_runs: Sequence[Dict[str, Any]],
    *,
    min_median_improvement_pct: float = 20.0,
) -> Dict[str, Any]:
    """Apply Hito 2 item 4 decision threshold cleanly over paired Condition A vs B runs."""
    by_condition: Dict[str, List[Dict[str, Any]]] = {"A": [], "B": []}
    by_task: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for run in paired_runs:
        cond = run["condition"]
        by_condition.setdefault(cond, []).append(run)
        by_task.setdefault(run["task_id"], {"A": [], "B": []})[cond].append(run)

    a_runs = by_condition["A"]
    b_runs = by_condition["B"]
    a_pass = sum(1 for r in a_runs if r["passed"])
    b_pass = sum(1 for r in b_runs if r["passed"])
    a_rate = a_pass / len(a_runs) if a_runs else 0.0
    b_rate = b_pass / len(b_runs) if b_runs else 0.0
    a_reg = sum(1 for r in a_runs if r["regression"])
    b_reg = sum(1 for r in b_runs if r["regression"])

    paired_a_times: List[float] = []
    paired_b_times: List[float] = []
    paired_a_tokens: List[int] = []
    paired_b_tokens: List[int] = []
    paired_resolved_tasks: List[str] = []
    per_task_losses: List[Dict[str, Any]] = []

    for task_id, arms in sorted(by_task.items()):
        a_ok = [r for r in arms["A"] if r["passed"]]
        b_ok = [r for r in arms["B"] if r["passed"]]
        if a_ok and b_ok:
            paired_resolved_tasks.append(task_id)
            med_a_time = median(r["wall_time_ms"] for r in a_ok)
            med_b_time = median(r["wall_time_ms"] for r in b_ok)
            med_a_tok = median(r["total_tokens"] for r in a_ok)
            med_b_tok = median(r["total_tokens"] for r in b_ok)
            paired_a_times.append(med_a_time)
            paired_b_times.append(med_b_time)
            paired_a_tokens.append(int(med_a_tok))
            paired_b_tokens.append(int(med_b_tok))
            if med_b_time > med_a_time or med_b_tok > med_a_tok:
                per_task_losses.append({
                    "task_id": task_id,
                    "reason": "slower_or_more_tokens_in_B",
                    "a_median_wall_ms": round(med_a_time, 2),
                    "b_median_wall_ms": round(med_b_time, 2),
                    "a_median_tokens": med_a_tok,
                    "b_median_tokens": med_b_tok,
                })
        elif len(a_ok) > len(b_ok):
            per_task_losses.append({
                "task_id": task_id,
                "reason": "lower_pass_count_in_B",
                "a_passes": len(a_ok),
                "b_passes": len(b_ok),
            })

    wall_reduction_pct = 0.0
    token_reduction_pct = 0.0
    if paired_resolved_tasks:
        a_med_wall = median(paired_a_times)
        b_med_wall = median(paired_b_times)
        a_med_tok = median(paired_a_tokens)
        b_med_tok = median(paired_b_tokens)
        if a_med_wall > 0:
            wall_reduction_pct = ((a_med_wall - b_med_wall) / a_med_wall) * 100.0
        if a_med_tok > 0:
            token_reduction_pct = ((a_med_tok - b_med_tok) / a_med_tok) * 100.0

    if b_rate < a_rate or b_reg > a_reg:
        decision = "reject_expansion"
    elif not paired_resolved_tasks:
        decision = "inconclusive"
    elif wall_reduction_pct >= min_median_improvement_pct or token_reduction_pct >= min_median_improvement_pct:
        decision = "fund_expansion"
    else:
        decision = "hold_expansion_threshold_unmet"

    return {
        "schema_version": COHORT_SCHEMA_VERSION,
        "base_eval_schema_version": SCHEMA_VERSION,
        "decision": decision,
        "min_median_improvement_pct": min_median_improvement_pct,
        "condition_a_runs": len(a_runs),
        "condition_b_runs": len(b_runs),
        "condition_a_success_rate": round(a_rate, 4),
        "condition_b_success_rate": round(b_rate, 4),
        "condition_a_regressions": a_reg,
        "condition_b_regressions": b_reg,
        "paired_resolved_tasks": paired_resolved_tasks,
        "median_wall_time_reduction_pct": round(wall_reduction_pct, 2),
        "median_token_reduction_pct": round(token_reduction_pct, 2),
        "per_task_losses": per_task_losses,
    }


def run_ab_cohort(
    cohort_tasks: Sequence[CohortTask],
    provider_factory: Callable[[str], LlmProvider],
    *,
    repo_root: str,
    repetitions: int = 3,
    max_repair_cycles: int = 3,
    model_name: str = "mock-deterministic",
    provider_name: str = "mock",
    interactive: bool = False,
    comparison_axis: str = "semantic-prefetch",
) -> Dict[str, Any]:
    """Execute alternating blinded A/B repetitions with fresh isolated workspaces per run."""
    runs: List[Dict[str, Any]] = []

    for rep in range(repetitions):
        order = ("A", "B") if rep % 2 == 0 else ("B", "A")
        for cohort_item in cohort_tasks:
            for condition in order:
                harness = ConditionHarness(
                    condition, repo_root=repo_root, max_repair_cycles=max_repair_cycles, interactive=interactive,
                )
                provider = provider_factory(condition)
                task_copy = Task.from_dict(cohort_item.task.to_dict())
                condition_instruction = dict(cohort_item.condition_instructions).get(condition)
                if condition_instruction:
                    task_copy.instruction = f"{task_copy.instruction}\n\n[CONDITION]\n{condition_instruction}"
                semantic_queries = 0
                files_read = len(task_copy.fixtures)
                if condition == "B" and not interactive:
                    sem_note, semantic_queries = _collect_semantic_context(harness, task_copy)
                    if sem_note:
                        task_copy.instruction = f"{task_copy.instruction}\n\n[SEMANTIC_CONTEXT]\n{sem_note}"

                result = harness.evaluate_task(task_copy, provider)
                final_code = result.attempts[-1].code if result.attempts else ""
                files_touched, collateral_lines = _count_collateral_lines(
                    harness,
                    cohort_item.task.fixtures,
                    final_code,
                    cohort_item.task.entrypoint,
                    cohort_item.allowed_edit_files,
                )
                invariant_err = (
                    cohort_item.invariant_checker(final_code, result)
                    if cohort_item.invariant_checker
                    else None
                )
                passed = result.status == "pass" and invariant_err is None
                regression = bool(
                    not passed and any(a.check_passed for a in result.attempts)
                    and any("Persistence invariant" in (a.oracle_failure_details or "") for a in result.attempts)
                )
                runs.append({
                    "repetition": rep,
                    "order": "".join(order),
                    "condition": condition,
                    "task_id": cohort_item.task.id,
                    "family": cohort_item.family,
                    "passed": passed,
                    "regression": regression,
                    "pass_at_attempt": result.pass_at_attempt,
                    "model_turns": result.model_turns + harness.tool_turns,
                    "wall_time_ms": round(result.wall_time_ms, 2),
                    "provider_latency_ms": round(result.provider_latency_ms, 2),
                    "total_tokens": result.total_tokens,
                    "token_counting_method": getattr(provider, "token_counting_method", "unknown"),
                    "files_read_count": harness.read_count if interactive else files_read,
                    "semantic_queries_count": harness.semantic_count if interactive else semantic_queries,
                    "files_touched_count": files_touched,
                    "collateral_lines_changed": collateral_lines,
                    "final_error_category": result.final_error_category or invariant_err,
                })

    summary = evaluate_expansion_gate(runs)
    summary["oracle_revision"] = 2
    summary["provider_name"] = provider_name
    if interactive:
        summary["context_mode"] = "model-selected"
    elif comparison_axis == "import-style":
        summary["context_mode"] = "none"
    else:
        summary["context_mode"] = "prefetched"
    summary["comparison_axis"] = comparison_axis
    summary["model_name"] = model_name
    summary["repetitions"] = repetitions
    summary["families_covered"] = sorted({item.family for item in cohort_tasks})
    summary["runs"] = runs
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Wysna Hito 2 Paired A/B Agent-First Cohort Runner")
    parser.add_argument("--provider", choices=["mock", "gemini", "openai"], default="mock")
    parser.add_argument("--model", type=str, default=None)
    parser.add_argument("--base-url", type=str, default="http://localhost:1234/v1")
    parser.add_argument("--api-key", type=str, default=None)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--max-repair-cycles", type=int, default=3)
    parser.add_argument("--output", type=str, default=None)
    parser.add_argument("--interactive-tools", action="store_true")
    parser.add_argument("--suite", choices=["default", "import-style", "all"], default="default")
    args = parser.parse_args()

    repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    if args.suite == "default":
        tasks = _build_default_cohort_tasks()
    elif args.suite == "import-style":
        tasks = build_import_style_ab_tasks()
    else:
        tasks = _build_default_cohort_tasks() + build_import_style_ab_tasks()

    if args.provider == "mock":
        model_name = args.model or "mock-deterministic"
        factory = lambda _cond: MockProvider()
    elif args.provider == "gemini":
        model_name = args.model or "gemini-flash-lite-latest"
        factory = lambda _cond: GeminiProvider(api_key=args.api_key, model=model_name)
    else:
        model_name = args.model or "local-model"
        factory = lambda _cond: OpenAICompatibleProvider(base_url=args.base_url, api_key=args.api_key, model=model_name)

    report = run_ab_cohort(
        tasks,
        factory,
        repo_root=repo_root,
        repetitions=args.repetitions,
        max_repair_cycles=args.max_repair_cycles,
        model_name=model_name,
        provider_name=args.provider,
        interactive=args.interactive_tools,
        comparison_axis=("import-style" if args.suite == "import-style" else "mixed" if args.suite == "all" else "semantic-prefetch"),
    )
    if args.output:
        os.makedirs(os.path.dirname(os.path.abspath(args.output)), exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as handle:
            json.dump(report, handle, indent=2)
    print(json.dumps({
        "decision": report["decision"],
        "families": report["families_covered"],
        "a_success_rate": report["condition_a_success_rate"],
        "b_success_rate": report["condition_b_success_rate"],
        "paired_resolved": report["paired_resolved_tasks"],
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
