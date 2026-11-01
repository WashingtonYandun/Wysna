"""Token density and self-healing repair efficiency audit for Wysna vs Python/TypeScript/Go/Rust.

Quantifies:
1. Syntactic token economy (subword BPE-approximation, lexical tokens, and boilerplate ratio)
   across canonical agent tasks (fallible I/O + JSON + functional pipelines + nominal structs).
2. Self-healing loop token economy: compares legacy single-error / manual repair against
   Wysna's multi-error recovery (`TypeErrors`) and zero-LLM-turn `wysna fix --apply`.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from tooling.FixCLI import collect_diagnostics, fix_source

_SUBWORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+\.\d+|\d+|==|!=|<=|>=|->|=>|\?\?|[^\sA-Za-z0-9_]")


def estimate_bpe_tokens(source: str) -> int:
    """Deterministic subword token estimator approximating cl100k_base / o200k_base."""
    tokens = _SUBWORD_RE.findall(source)
    count = 0
    for tok in tokens:
        if len(tok) > 10 and tok[0].isalpha():
            count += (len(tok) + 5) // 6
        else:
            count += 1
    return count


CANONICAL_BENCHMARKS: Dict[str, Dict[str, str]] = {
    "fallible_config_pipeline": {
        "wysna": (
            "struct Config { host: str, port: int }\n"
            "pub fn load_ports(path: str, env_port: int?) -> int ! IoError {\n"
            "    let raw = read_file(path)?\n"
            "    let base = env_port ?? 8080\n"
            "    let mut total = 0\n"
            "    for n in [base, len(raw), 9000] {\n"
            "        if n > 1024 { total = total + n + 1 }\n"
            "    }\n"
            "    return Ok(total)\n"
            "}\n"
        ),
        "python": (
            "from dataclasses import dataclass\n"
            "from typing import Optional\n\n"
            "@dataclass(frozen=True)\n"
            "class Config:\n"
            "    host: str\n"
            "    port: int\n\n"
            "def load_ports(path: str, env_port: Optional[int]) -> int:\n"
            "    with open(path, 'r', encoding='utf-8') as f:\n"
            "        raw = f.read()\n"
            "    base = env_port if env_port is not None else 8080\n"
            "    total = 0\n"
            "    for n in [base, len(raw), 9000]:\n"
            "        if n > 1024:\n"
            "            total = total + n + 1\n"
            "    return total\n"
        ),
        "typescript": (
            'import { readFileSync } from "node:fs";\n\n'
            "export interface Config {\n"
            "  readonly host: string;\n"
            "  readonly port: number;\n"
            "}\n\n"
            "export function loadPorts(path: string, envPort: number | null): number {\n"
            '  const raw = readFileSync(path, "utf-8");\n'
            "  const base = envPort ?? 8080;\n"
            "  let total = 0;\n"
            "  for (const n of [base, raw.length, 9000]) {\n"
            "    if (n > 1024) { total = total + n + 1; }\n"
            "  }\n"
            "  return total;\n"
            "}\n"
        ),
        "go": (
            "package main\n\n"
            'import "os"\n\n'
            "type Config struct {\n"
            "    Host string\n"
            "    Port int\n"
            "}\n\n"
            "func LoadPorts(path string, envPort *int) (int, error) {\n"
            "    raw, err := os.ReadFile(path)\n"
            "    if err != nil {\n"
            "        return 0, err\n"
            "    }\n"
            "    base := 8080\n"
            "    if envPort != nil {\n"
            "        base = *envPort\n"
            "    }\n"
            "    nums := []int{base, len(raw), 9000}\n"
            "    total := 0\n"
            "    for _, n := range nums {\n"
            "        if n > 1024 {\n"
            "            total += n + 1\n"
            "        }\n"
            "    }\n"
            "    return total, nil\n"
            "}\n"
        ),
        "rust": (
            "use std::fs;\n"
            "use std::io;\n\n"
            "#[derive(Clone, Debug)]\n"
            "pub struct Config {\n"
            "    pub host: String,\n"
            "    pub port: i64,\n"
            "}\n\n"
            "pub fn load_ports(path: &str, env_port: Option<i64>) -> Result<i64, io::Error> {\n"
            "    let raw = fs::read_to_string(path)?;\n"
            "    let base = env_port.unwrap_or(8080);\n"
            "    let mut total: i64 = 0;\n"
            "    for n in [base, raw.len() as i64, 9000] {\n"
            "        if n > 1024 { total = total + n + 1; }\n"
            "    }\n"
            "    Ok(total)\n"
            "}\n"
        ),
    }
}


def run_token_density_audit() -> Dict[str, Any]:
    """Compare token density across Wysna, Python, TypeScript, Go, and Rust."""
    report: Dict[str, Any] = {}
    for bench_name, implementations in CANONICAL_BENCHMARKS.items():
        rows: Dict[str, Dict[str, Any]] = {}
        wysna_bpe = estimate_bpe_tokens(implementations["wysna"])
        for lang, code in implementations.items():
            bpe = estimate_bpe_tokens(code)
            rows[lang] = {
                "characters": len(code),
                "lines": len(code.strip().splitlines()),
                "bpe_tokens": bpe,
                "relative_to_wysna": round(bpe / max(wysna_bpe, 1), 3),
            }
        report[bench_name] = rows
    return report


def run_self_healing_efficiency_audit(tmp_dir: str) -> Dict[str, Any]:
    """Measure repair turns and token savings from multi-error recovery and `wysna fix`."""
    program = (
        "struct Account { balance: int, owner: str }\n"
        "fn update(a: Account) -> int {\n"
        "    let total = 0\n"
        "    total = 10\n"
        "    let current = a.balanc\n"
        "    return total + current\n"
        "}\n"
        "fn main() {\n"
        "    let acc = Account { balance: 100, owner: \"Ada\" }\n"
        "    print(update(acc))\n"
        "}\n"
    )
    path = f"{tmp_dir}/audit_heal.wysna"
    initial_diags = collect_diagnostics(path, program)
    fixed = fix_source(path, program)

    return {
        "initial_errors_detected_in_one_pass": len(initial_diags),
        "auto_repaired_without_llm": len(fixed["remaining_diagnostics"]) == 0,
        "llm_repair_turns_required": 0 if len(fixed["remaining_diagnostics"]) == 0 else 1,
        "legacy_single_error_llm_turns": 2,
    }
