"""Task definitions and real-execution hidden oracles for bench/agent_scale."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
from typing import Callable, Dict, List, Optional, Tuple

from bench.agent_scale.generate import DEFAULT_REPO_ROOT, build_compiler_command, validate_project


@dataclass
class ScaleTask:
    task_id: str
    title: str
    kind: str
    instruction: str
    expected_files: Tuple[str, ...]
    setup_workspace: Callable[[Path], None]
    oracle: Callable[[Path, Path, Optional[str]], Tuple[bool, str]]


def _run_probe(
    workspace_dir: Path,
    probe_code: str,
    repo_root: Path,
    wysna_bin: Optional[str],
    timeout_s: int = 120,
) -> Tuple[int, str, str]:
    probe_file = workspace_dir / "src" / "_oracle_probe_tmp.wysna"
    try:
        probe_file.write_text(probe_code, encoding="utf-8")
        cmd = build_compiler_command(repo_root, wysna_bin) + ["run", str(probe_file)]
        proc = subprocess.run(cmd, cwd=workspace_dir, capture_output=True, text=True, timeout=timeout_s)
        return proc.returncode, proc.stdout, proc.stderr
    finally:
        if probe_file.exists():
            probe_file.unlink()


def _check_and_probe(
    workspace_dir: Path,
    probe_code: str,
    expected_stdout: str,
    repo_root: Path,
    wysna_bin: Optional[str],
) -> Tuple[bool, str]:
    ok, _, check_out = validate_project(workspace_dir, repo_root=repo_root, wysna_bin=wysna_bin)
    if not ok:
        return False, f"project check failed:\n{check_out[:1200]}"
    rc, stdout, stderr = _run_probe(workspace_dir, probe_code, repo_root, wysna_bin)
    if rc != 0:
        return False, f"oracle probe exited {rc}: {stderr[:800]}"
    if stdout.strip() != expected_stdout.strip():
        return False, f"stdout mismatch: expected {expected_stdout.strip()!r}, got {stdout.strip()!r}"
    return True, "ok"


def _strip_comments(source: str) -> str:
    return "\n".join(line.split("#", 1)[0] for line in source.splitlines())


def make_task_t1() -> ScaleTask:
    instruction = (
        "Locate the membership tier discount pricing function in this repository (do not assume filenames) "
        "and update membership tier 2 so it grants a 25% discount instead of 15% on positive integer "
        "subtotals (using integer division). Leave tiers 0 and 1 unchanged."
    )
    probe = (
        'import "pricing.wysna" as pricing\n'
        'fn main() -> unit {\n'
        '    let d0 = pricing.apply_tier_discount(1000, 0)\n'
        '    let d1 = pricing.apply_tier_discount(1000, 1)\n'
        '    let d2 = pricing.apply_tier_discount(1000, 2)\n'
        '    let d_neg = pricing.apply_tier_discount(-50, 2)\n'
        '    print(f"{d0}:{d1}:{d2}:{d_neg}")\n'
        '}\n'
        'main()\n'
    )

    def oracle(ws: Path, repo_root: Path, wysna_bin: Optional[str]) -> Tuple[bool, str]:
        return _check_and_probe(ws, probe, "1000:900:750:0", repo_root, wysna_bin)

    return ScaleTask(
        task_id="T1",
        title="Locate tier discount rule without symbol hints",
        kind="locate_and_change",
        instruction=instruction,
        expected_files=("src/pricing.wysna",),
        setup_workspace=lambda _ws: None,
        oracle=oracle,
    )


def make_task_t2() -> ScaleTask:
    instruction = (
        "Add a field `priority_code: int` to struct `LineItem` in `src/billing.wysna`, update every "
        "`LineItem { ... }` construction site across the codebase to pass `priority_code: 1` by default, "
        "and update `summarize_item_line` to return `f\"{item.sku}:{item.quantity}:{item.unit_cents}:{item.priority_code}\"`."
    )
    probe = (
        'import "billing.wysna" as billing\n'
        'import "shipping.wysna" as shipping\n'
        'import "audit.wysna" as audit\n'
        'fn main() -> unit {\n'
        '    let item = billing.LineItem { sku: "SKU-9", quantity: 3, unit_cents: 400, priority_code: 7 }\n'
        '    let std = billing.make_standard_item("STD", 1, 200)\n'
        '    let s = billing.summarize_item_line(item)\n'
        '    let s_std = billing.summarize_item_line(std)\n'
        '    let ship = shipping.dispatch_units(10, 2, 150)\n'
        '    let snap = audit.audit_order_snapshot(4, 5, 1)\n'
        '    print(f"{s}|{s_std}|ship={ship > 0}|{snap}")\n'
        '}\n'
        'main()\n'
    )

    def oracle(ws: Path, repo_root: Path, wysna_bin: Optional[str]) -> Tuple[bool, str]:
        expected = "SKU-9:3:400:7|STD:1:200:1|ship=true|u=4:active=true:after=4:AUD-1:1:100:1"
        return _check_and_probe(ws, probe, expected, repo_root, wysna_bin)

    return ScaleTask(
        task_id="T2",
        title="Propagate new struct field across dependent modules",
        kind="cross_module_field",
        instruction=instruction,
        expected_files=("src/billing.wysna", "src/shipping.wysna", "src/audit.wysna"),
        setup_workspace=lambda _ws: None,
        oracle=oracle,
    )


def make_task_t3() -> ScaleTask:
    instruction = (
        "The project currently fails static typechecking because of a cross-module type mismatch "
        "affecting invoice tax computation. Diagnose the root cause (not just the call-site symptom) "
        "and fix it cleanly so `project check` passes and integer basis-point tax computation works."
    )
    probe = (
        'import "pricing.wysna" as pricing\n'
        'import "billing.wysna" as billing\n'
        'fn main() -> unit {\n'
        '    let tax = pricing.compute_tax_cents(10000, 1200)\n'
        '    let total = billing.compute_invoice_total(10000, 1200)\n'
        '    print(f"{tax}:{total}")\n'
        '}\n'
        'main()\n'
    )

    def setup_workspace(ws: Path) -> None:
        # Mutate pricing.compute_tax_cents so pricing.wysna stays valid in isolation while billing.wysna fails W2001.
        pricing_path = ws / "src" / "pricing.wysna"
        text = pricing_path.read_text(encoding="utf-8")
        old_fn = (
            "pub fn compute_tax_cents(net_cents: int, rate_bps: int) -> int {\n"
            "    if net_cents <= 0 or rate_bps <= 0 {\n"
            "        return 0\n"
            "    }\n"
            "    return (net_cents * rate_bps) / 10000\n"
            "}"
        )
        broken_fn = (
            "pub fn compute_tax_cents(net_cents: int, rate_label: str) -> int {\n"
            '    if net_cents <= 0 or rate_label == "" {\n'
            "        return 0\n"
            "    }\n"
            '    if rate_label == "std" {\n'
            "        return (net_cents * 1200) / 10000\n"
            "    }\n"
            "    return 0\n"
            "}"
        )
        if old_fn not in text:
            raise ValueError("T3 setup anchor compute_tax_cents not found in src/pricing.wysna")
        pricing_path.write_text(text.replace(old_fn, broken_fn, 1), encoding="utf-8")

    def oracle(ws: Path, repo_root: Path, wysna_bin: Optional[str]) -> Tuple[bool, str]:
        return _check_and_probe(ws, probe, "1200:11200", repo_root, wysna_bin)

    return ScaleTask(
        task_id="T3",
        title="Fix cross-module type error diagnosed at caller",
        kind="fix_cross_module_type_error",
        instruction=instruction,
        expected_files=("src/pricing.wysna",),
        setup_workspace=setup_workspace,
        oracle=oracle,
    )


def make_task_t4() -> ScaleTask:
    instruction = (
        "Rename public function `reserve_stock_units` in `src/inventory.wysna` to "
        "`allocate_stock_units` and update all references across every module in `src/` without "
        "changing runtime behavior."
    )
    probe = (
        'import "inventory.wysna" as inventory\n'
        'import "shipping.wysna" as shipping\n'
        'import "audit.wysna" as audit\n'
        'fn main() -> unit {\n'
        '    let rem = inventory.allocate_stock_units(10, 4) else _err { -1 }\n'
        '    let bad = inventory.allocate_stock_units(2, 5) else _err { -1 }\n'
        '    let ship = shipping.dispatch_units(10, 2, 100)\n'
        '    let snap = audit.audit_order_snapshot(3, 8, 2)\n'
        '    print(f"{rem}:{bad}:ship={ship > 200}:{snap}")\n'
        '}\n'
        'main()\n'
    )

    def oracle(ws: Path, repo_root: Path, wysna_bin: Optional[str]) -> Tuple[bool, str]:
        for wysna_file in sorted((ws / "src").rglob("*.wysna")):
            code_only = _strip_comments(wysna_file.read_text(encoding="utf-8"))
            if re.search(r"\breserve_stock_units\b", code_only):
                return False, f"stale reference to reserve_stock_units in {wysna_file.relative_to(ws)}"
        expected = "6:-1:ship=true:u=3:active=true:after=6:AUD-1:2:100"
        return _check_and_probe(ws, probe, expected, repo_root, wysna_bin)

    return ScaleTask(
        task_id="T4",
        title="Rename public function across all dependent modules",
        kind="rename_symbol",
        instruction=instruction,
        expected_files=("src/inventory.wysna", "src/shipping.wysna", "src/audit.wysna", "src/main.wysna"),
        setup_workspace=lambda _ws: None,
        oracle=oracle,
    )


ALL_TASKS: Dict[str, Callable[[], ScaleTask]] = {
    "T1": make_task_t1,
    "T2": make_task_t2,
    "T3": make_task_t3,
    "T4": make_task_t4,
}


def prepare_task_workspace(template_dir: Path, task: ScaleTask, work_root: Optional[Path] = None) -> Path:
    """Copy a generated template project into a fresh isolated workspace and apply task setup."""
    base = Path(tempfile.mkdtemp(prefix=f"wysna-scale-{task.task_id}-", dir=work_root))
    ws = base / "project"
    shutil.copytree(template_dir, ws)
    task.setup_workspace(ws)
    return ws
