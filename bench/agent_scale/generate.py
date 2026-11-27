#!/usr/bin/env python3
"""Deterministic generator of large multi-module Wysna projects for scale benchmarks."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import random
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLCHAIN_VERSION = "1.1.0"
LANGUAGE_VERSION = "1.1"


def _count_nonblank_lines(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip())


def _hash_project_tree(root: Path) -> str:
    hasher = hashlib.sha256()
    candidates = [root / "wysna.toml"] + sorted((root / "src").rglob("*.wysna"))
    for path in candidates:
        rel = path.relative_to(root).as_posix()
        hasher.update(rel.encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(path.read_bytes())
        hasher.update(b"\0")
    return hasher.hexdigest()


def _core_modules(seed: int) -> Dict[str, str]:
    rng = random.Random(seed)
    base_fee_cents = rng.choice([250, 300, 350])
    pricing = (
        f'## Deterministic pricing rules, tier discounts, and basis-point tax computation.\n\n'
        f'## Computes discounted subtotal in integer cents based on membership tier.\n'
        f'## Tier 0 has no discount; tier 1 grants 10% off; tier 2 grants 15% off.\n'
        f'pub fn apply_tier_discount(subtotal_cents: int, tier_code: int) -> int {{\n'
        f'    if subtotal_cents <= 0 {{\n'
        f'        return 0\n'
        f'    }}\n'
        f'    if tier_code == 1 {{\n'
        f'        return (subtotal_cents * 90) / 100\n'
        f'    }}\n'
        f'    if tier_code == 2 {{\n'
        f'        return (subtotal_cents * 85) / 100\n'
        f'    }}\n'
        f'    return subtotal_cents\n'
        f'}}\n\n'
        f'## Computes tax in cents using integer basis points (10000 bps = 100%).\n'
        f'pub fn compute_tax_cents(net_cents: int, rate_bps: int) -> int {{\n'
        f'    if net_cents <= 0 or rate_bps <= 0 {{\n'
        f'        return 0\n'
        f'    }}\n'
        f'    return (net_cents * rate_bps) / 10000\n'
        f'}}\n\n'
        f'## Fixed handling fee in cents for outbound orders.\n'
        f'pub fn handling_fee_cents() -> int {{\n'
        f'    return {base_fee_cents}\n'
        f'}}\n'
    )

    users = (
        '## Customer account state and tier resolution helpers.\n\n'
        'pub enum AccountStatus { Active, Suspended, Closed }\n\n'
        'pub struct Customer {\n'
        '    id: int,\n'
        '    tier_code: int,\n'
        '    status: AccountStatus,\n'
        '}\n\n'
        '## Returns true when the customer account may place new orders.\n'
        'pub fn is_active_customer(user: Customer) -> bool {\n'
        '    return match user.status {\n'
        '        AccountStatus.Active => true,\n'
        '        _ => false,\n'
        '    }\n'
        '}\n\n'
        '## Builds a default customer record for testing and seeding.\n'
        'pub fn default_customer(id: int, tier_code: int) -> Customer {\n'
        '    return Customer { id: id, tier_code: tier_code, status: AccountStatus.Active }\n'
        '}\n'
    )

    inventory = (
        '## Warehouse stock validation and atomic reservation helpers.\n\n'
        'pub struct InventoryError {\n'
        '    sku_stock: int,\n'
        '    message: str,\n'
        '}\n\n'
        '## Reserves requested units from stock or fails with InventoryError.\n'
        'pub fn reserve_stock_units(stock: int, requested: int) -> int ! InventoryError {\n'
        '    if requested <= 0 {\n'
        '        return Err(InventoryError { sku_stock: stock, message: "requested must be positive" })\n'
        '    }\n'
        '    if requested > stock {\n'
        '        return Err(InventoryError { sku_stock: stock, message: "insufficient stock" })\n'
        '    }\n'
        '    return Ok(stock - requested)\n'
        '}\n\n'
        '## Computes safety buffer units above a threshold.\n'
        'pub fn safety_buffer(stock: int) -> int {\n'
        '    if stock > 20 {\n'
        '        return stock - 20\n'
        '    }\n'
        '    return 0\n'
        '}\n'
    )

    billing = (
        '## Invoice calculation and billable line item records.\n'
        'import "pricing.wysna" as pricing\n\n'
        'pub struct LineItem {\n'
        '    sku: str,\n'
        '    quantity: int,\n'
        '    unit_cents: int,\n'
        '}\n\n'
        '## Formats a canonical colon-delimited summary for a line item.\n'
        'pub fn summarize_item_line(item: LineItem) -> str {\n'
        '    return f"{item.sku}:{item.quantity}:{item.unit_cents}"\n'
        '}\n\n'
        '## Constructs a standard billable item line.\n'
        'pub fn make_standard_item(sku: str, quantity: int, unit_cents: int) -> LineItem {\n'
        '    return LineItem { sku: sku, quantity: quantity, unit_cents: unit_cents }\n'
        '}\n\n'
        '## Computes invoice total including basis-point tax.\n'
        'pub fn compute_invoice_total(net_cents: int, rate_bps: int) -> int {\n'
        '    let tax = pricing.compute_tax_cents(net_cents, rate_bps)\n'
        '    return net_cents + tax\n'
        '}\n'
    )

    shipping = (
        '## Dispatch orchestration across billing line items and inventory stock.\n'
        'import "billing.wysna" as billing\n'
        'import "inventory.wysna" as inventory\n'
        'import "pricing.wysna" as pricing\n\n'
        '## Prepares dispatch by reserving inventory and summing line value plus handling.\n'
        'pub fn dispatch_units(stock: int, qty: int, unit_cents: int) -> int {\n'
        '    let _remaining = inventory.reserve_stock_units(stock, qty) else _err {\n'
        '        return -1\n'
        '    }\n'
        '    let item = billing.LineItem { sku: "SHIP-DEFAULT", quantity: qty, unit_cents: unit_cents }\n'
        '    return (item.quantity * item.unit_cents) + pricing.handling_fee_cents()\n'
        '}\n'
    )

    audit = (
        '## Cross-module compliance verification record builder.\n'
        'import "billing.wysna" as billing\n'
        'import "inventory.wysna" as inventory\n'
        'import "users.wysna" as users\n\n'
        '## Produces a compact audit stamp covering user status, stock check, and line item.\n'
        'pub fn audit_order_snapshot(user_id: int, stock: int, qty: int) -> str {\n'
        '    let user = users.default_customer(user_id, 1)\n'
        '    let active = users.is_active_customer(user)\n'
        '    let after = inventory.reserve_stock_units(stock, qty) else _err { -1 }\n'
        '    let item = billing.LineItem { sku: "AUD-1", quantity: qty, unit_cents: 100 }\n'
        '    let line = billing.summarize_item_line(item)\n'
        '    return f"u={user.id}:active={active}:after={after}:{line}"\n'
        '}\n'
    )

    return {
        "pricing.wysna": pricing,
        "users.wysna": users,
        "inventory.wysna": inventory,
        "billing.wysna": billing,
        "shipping.wysna": shipping,
        "audit.wysna": audit,
    }


def _render_filler_module(idx: int, fn_count: int, rng: random.Random) -> str:
    domains = ("ledger", "catalog", "routing", "quota", "telemetry", "settlement", "warehouse")
    domain = domains[idx % len(domains)]
    mod_tag = f"mod_{idx:03d}"
    lines: List[str] = [
        f"## Generated {domain} helper module ({mod_tag}) for multi-module scale checks.",
        f'import "pricing.wysna" as pricing',
        "",
        f"pub struct Record_{idx:03d} {{",
        "    id: int,",
        "    weight: int,",
        "    code: int,",
        "}",
        "",
        f"pub enum Mode_{idx:03d} {{ Fast, Standard, Deferred }}",
        "",
    ]
    fn_names: List[str] = []
    for f_idx in range(fn_count):
        fn_name = f"compute_{domain}_{idx:03d}_{f_idx:03d}"
        fn_names.append(fn_name)
        mult = rng.randint(2, 19)
        bias = rng.randint(1, 97)
        lines.extend([
            f"## Evaluates bounded {domain} score #{f_idx} using pricing basis helper.",
            f"pub fn {fn_name}(base: int, tier: int) -> int {{",
            f"    let discounted = pricing.apply_tier_discount(base * {mult} + {bias}, tier)",
            f"    if discounted > 100000 {{",
            f"        return discounted / 2",
            f"    }}",
            f"    return discounted + {f_idx}",
            f"}}",
            "",
        ])
    lines.extend([
        f"## Aggregates checksum across {mod_tag} operations.",
        f"pub fn module_checksum_{idx:03d}() -> int {{",
        f"    let r = Record_{idx:03d} {{ id: {idx + 1}, weight: 3, code: 1 }}",
        f"    let m = Mode_{idx:03d}.Standard",
        f"    let bonus = match m {{",
        f"        Mode_{idx:03d}.Fast => 2,",
        f"        Mode_{idx:03d}.Standard => 1,",
        f"        Mode_{idx:03d}.Deferred => 0,",
        f"    }}",
        f"    return {fn_names[0]}(r.id + r.weight, r.code) + bonus",
        f"}}",
        "",
    ])
    return "\n".join(lines)


def generate_project(output_dir: Path | str, target_loc: int = 25000, seed: int = 42) -> Dict[str, Any]:
    """Generate a deterministic multi-module Wysna project within +/-5% of ``target_loc``."""
    if target_loc < 400:
        raise ValueError(f"target_loc must be >= 400 to include core domain modules, got {target_loc}")
    root = Path(output_dir).resolve()
    src_dir = root / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    # Remove stale filler files if regenerating into an existing directory cleanly.
    for existing in list(src_dir.glob("*.wysna")):
        existing.unlink()

    rng = random.Random(seed)
    core = _core_modules(seed)
    for fname, content in core.items():
        (src_dir / fname).write_text(content, encoding="utf-8")

    # Estimate overhead of main.wysna + registry.wysna to keep total non-blank LOC tight.
    if target_loc <= 3000:
        module_count = 6
    elif target_loc <= 12000:
        module_count = 24
    elif target_loc <= 40000:
        module_count = 80
    else:
        module_count = 160

    core_loc = sum(_count_nonblank_lines(text) for text in core.values())
    hub_overhead = 12 + (module_count * 2) + 25
    filler_budget = max(module_count * 20, target_loc - core_loc - hub_overhead)
    # Each function in _render_filler_module contributes 8 non-blank lines + ~19 lines boilerplate/module.
    boiler_per_mod = 19
    total_fns_needed = max(module_count, round((filler_budget - module_count * boiler_per_mod) / 8.0))
    base_fns = total_fns_needed // module_count
    extra_fns = total_fns_needed % module_count

    filler_files: Dict[str, str] = {}
    for idx in range(module_count):
        fn_count = max(1, base_fns + (1 if idx < extra_fns else 0))
        mod_name = f"mod_{idx:03d}.wysna"
        content = _render_filler_module(idx, fn_count, rng)
        filler_files[mod_name] = content
        (src_dir / mod_name).write_text(content, encoding="utf-8")

    reg_lines = ["## Central module registry ensuring full-project typechecking reachability."]
    for idx in range(module_count):
        reg_lines.append(f'import "mod_{idx:03d}.wysna" as m{idx:03d}')
    reg_lines.extend(["", "## Sums deterministic module checksums across all submodules.", "pub fn total_checksum() -> int {", "    let mut acc = 0"])
    for idx in range(module_count):
        reg_lines.append(f"    acc += m{idx:03d}.module_checksum_{idx:03d}()")
    reg_lines.extend(["    return acc", "}", ""])
    registry_text = "\n".join(reg_lines)
    (src_dir / "registry.wysna").write_text(registry_text, encoding="utf-8")

    main_text = (
        '## Application entrypoint connecting core domain modules and generated submodules.\n'
        'import "pricing.wysna" as pricing\n'
        'import "billing.wysna" as billing\n'
        'import "inventory.wysna" as inventory\n'
        'import "shipping.wysna" as shipping\n'
        'import "audit.wysna" as audit\n'
        'import "registry.wysna" as registry\n\n'
        'fn main() -> unit {\n'
        '    let disc = pricing.apply_tier_discount(1000, 2)\n'
        '    let inv = billing.compute_invoice_total(disc, 1200)\n'
        '    let ship = shipping.dispatch_units(10, 2, 500)\n'
        '    let snap = audit.audit_order_snapshot(7, 5, 1)\n'
        '    let rem = inventory.reserve_stock_units(5, 1) else _err { -1 }\n'
        '    let reg = registry.total_checksum()\n'
        '    print(f"disc={disc} inv={inv} ship={ship} rem={rem} snap={snap} reg={reg}")\n'
        '}\n\n'
        'main()\n'
    )
    (src_dir / "main.wysna").write_text(main_text, encoding="utf-8")

    manifest_text = (
        'manifest_version = 1\n\n'
        '[package]\n'
        'name = "agent-scale-app"\n'
        'version = "0.1.0"\n'
        f'language = "{LANGUAGE_VERSION}"\n\n'
        '[toolchain]\n'
        f'compiler = "{TOOLCHAIN_VERSION}"\n'
        'stdlib = "bundled"\n\n'
        '[sources]\n'
        'root = "src"\n\n'
        '[targets.app]\n'
        'entry = "src/main.wysna"\n'
        'profile = "native-cli"\n'
    )
    (root / "wysna.toml").write_text(manifest_text, encoding="utf-8")

    all_files = sorted(src_dir.glob("*.wysna"))
    actual_loc = sum(_count_nonblank_lines(p.read_text(encoding="utf-8")) for p in all_files)
    symbols, reverse_deps = _extract_ground_truth_graph(all_files)
    tree_sha = _hash_project_tree(root)

    ground_truth = {
        "schema_version": "wysna.agent-scale-project/1",
        "seed": seed,
        "target_loc": target_loc,
        "actual_nonblank_loc": actual_loc,
        "file_count": len(all_files),
        "tree_sha256": tree_sha,
        "symbols": symbols,
        "reverse_deps": reverse_deps,
        "anchors": {
            "T1": {"file": "src/pricing.wysna", "symbol": "apply_tier_discount"},
            "T2": {
                "file": "src/billing.wysna",
                "symbol": "LineItem",
                "summary_fn": "summarize_item_line",
                "dependent_files": ["src/billing.wysna", "src/shipping.wysna", "src/audit.wysna"],
            },
            "T3": {
                "definition_file": "src/pricing.wysna",
                "definition_symbol": "compute_tax_cents",
                "caller_file": "src/billing.wysna",
                "caller_symbol": "compute_invoice_total",
            },
            "T4": {
                "file": "src/inventory.wysna",
                "old_symbol": "reserve_stock_units",
                "new_symbol": "allocate_stock_units",
                "dependent_files": ["src/inventory.wysna", "src/shipping.wysna", "src/audit.wysna", "src/main.wysna"],
            },
        },
    }
    (root / "ground_truth.json").write_text(json.dumps(ground_truth, indent=2) + "\n", encoding="utf-8")
    return ground_truth


def _extract_ground_truth_graph(files: List[Path]) -> Tuple[Dict[str, Dict[str, str]], Dict[str, List[str]]]:
    symbols: Dict[str, Dict[str, str]] = {}
    reverse_deps: Dict[str, List[str]] = {}
    for path in files:
        mod_name = path.name
        reverse_deps.setdefault(mod_name, [])
        lines = path.read_text(encoding="utf-8").splitlines()
        for idx, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith('import "') and '.wysna"' in stripped:
                imported = stripped.split('"', 2)[1]
                reverse_deps.setdefault(imported, [])
                if mod_name not in reverse_deps[imported]:
                    reverse_deps[imported].append(mod_name)
            for prefix, kind in (("pub fn ", "fn"), ("pub struct ", "struct"), ("pub enum ", "enum")):
                if stripped.startswith(prefix):
                    rest = stripped[len(prefix):]
                    sym_name = rest.split("(")[0].split("{")[0].strip()
                    doc = ""
                    if idx > 0 and lines[idx - 1].strip().startswith("##"):
                        doc = lines[idx - 1].strip().lstrip("#").strip()
                    symbols[sym_name] = {
                        "file": f"src/{mod_name}",
                        "module": mod_name,
                        "kind": kind,
                        "doc": doc,
                    }
    for key in reverse_deps:
        reverse_deps[key].sort()
    return symbols, reverse_deps


def build_compiler_command(repo_root: Path = DEFAULT_REPO_ROOT, wysna_bin: Optional[str] = None) -> List[str]:
    if wysna_bin:
        return [str(Path(wysna_bin).resolve())]
    return [sys.executable, str((repo_root / "main.py").resolve())]


def validate_project(
    project_dir: Path | str,
    repo_root: Path = DEFAULT_REPO_ROOT,
    wysna_bin: Optional[str] = None,
    timeout_s: int = 180,
) -> Tuple[bool, float, str]:
    """Run ``project check --manifest wysna.toml`` and return (ok, elapsed_ms, output)."""
    root = Path(project_dir).resolve()
    cmd = build_compiler_command(repo_root, wysna_bin) + [
        "project", "check", "--manifest", str(root / "wysna.toml"), "--output-format=json-v1"
    ]
    start = time.perf_counter()
    proc = subprocess.run(cmd, cwd=repo_root, capture_output=True, text=True, timeout=timeout_s)
    elapsed_ms = (time.perf_counter() - start) * 1000.0
    output = (proc.stdout or "") + (proc.stderr or "")
    return proc.returncode == 0, elapsed_ms, output


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a deterministic multi-module Wysna scale project.")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--loc", type=int, default=25000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--wysna-bin", type=str, default=None)
    args = parser.parse_args()

    t0 = time.perf_counter()
    meta = generate_project(args.output, target_loc=args.loc, seed=args.seed)
    gen_ms = (time.perf_counter() - t0) * 1000.0
    result = {
        "schema_version": meta["schema_version"],
        "seed": meta["seed"],
        "target_loc": meta["target_loc"],
        "actual_nonblank_loc": meta["actual_nonblank_loc"],
        "file_count": meta["file_count"],
        "symbol_count": len(meta["symbols"]),
        "tree_sha256": meta["tree_sha256"],
        "generation_ms": round(gen_ms, 2),
    }
    if args.validate:
        ok, check_ms, out = validate_project(args.output, wysna_bin=args.wysna_bin)
        result["check_passed"] = ok
        result["check_ms"] = round(check_ms, 2)
        if not ok:
            result["check_error"] = out[-2000:]
    print(json.dumps(result, indent=2))
    return 0 if result.get("check_passed", True) else 1


if __name__ == "__main__":
    raise SystemExit(main())
