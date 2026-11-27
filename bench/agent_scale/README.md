# Wysna Agent-Scale Benchmark (`bench/agent_scale`)

Evaluates whether Wysna's semantic tools (`check`, `graph`, `inspect`, `patch`) reduce **end-to-end tokens** and **improve task reliability** compared against plain file/search tools (`list_dir`, `read_file`, `grep`, `write_file`, `apply_edit`) on large multi-module Wysna codebases (`5k`, `25k`, `100k` non-blank LOC).

---

## 1. Motivation & Protocol

Earlier small-repo A/B trials (`ab_default`, `ab_agent_first_v1`) showed semantic prefetching increasing token consumption by +71% to +103% on tiny files. This benchmark tests the large-codebase regime directly before expanding agent tooling:

1. **Deterministic Workspace Generation (`generate.py`):** Produces a seeded, acyclic multi-module Wysna project (`wysna.toml` + domain core + generated submodules wired through `src/registry.wysna` so `project check` validates 100% of LOC), plus `ground_truth.json`.
2. **Paired Evaluation (`agent.py` + `tasks.py`):** Runs each task under Condition `A_baseline` (filesystem + grep only) and Condition `B_wysna` (filesystem + `wysna check|graph|inspect|patch` via `--wysna-bin` or Python Seed), using a uniform text JSON ```tool_call``` protocol with a strict per-tool output byte cap (`8192` bytes by default).
3. **Real-Execution Oracles:** Every task (`T1` locate & change without hints, `T2` cross-module struct field propagation, `T3` cross-module type diagnosis, `T4` project-wide symbol rename) verifies both `project check` and live program execution against hidden probes.

---

## 2. Metrics, Cache Model & Pre-Registered Gate

- **Prompt attribution (`metrics.py`):** Per-turn split across `system`, `task`, `tool_results`, and `prior_assistant` scaled by character share to provider-reported `prompt_tokens` (explicitly marked as an estimate).
- **Cache-aware effective tokens:** Models prefix cache hits on consecutive turns using relative weights (`cache_read_multiplier=0.1`, `cache_write_multiplier=1.25`, `input_weight=1.0`, `output_weight=1.0`) — no vendor dollar prices are hardcoded.
- **Pre-registered Decision Gate (`B_wysna` vs `A_baseline`):**
  - Mock runs are always stamped `"provider_is_mock": true` and forced to `"decision": "invalid_mock"`.
  - Real-provider runs pass **only** when `condition_b_success_rate >= condition_a_success_rate` AND median token reduction meets **`>= 30.0%` at `25k+` LOC** (`>= 45.0%` at `100k+` LOC).

---

## 3. Usage

```sh
# 1. Generate and typecheck a deterministic project
PYTHONPATH=src:. python3 bench/agent_scale/generate.py \
  --output /tmp/scale-25k --loc 25000 --seed 42 --validate

# 2. Dry-run with mock provider (never satisfies decision gate)
PYTHONPATH=src:. python3 bench/agent_scale/run.py \
  --loc 5000 --conditions A_baseline B_wysna --tasks T1 T2 T3 T4 \
  --repetitions 1 --provider mock --output /tmp/scale_mock.json

# 3. Real evaluation (API key read strictly from environment variables)
export ANTHROPIC_API_KEY="..."
PYTHONPATH=src:. python3 bench/agent_scale/run.py \
  --loc 25000 --conditions A_baseline B_wysna --tasks T1 T2 T3 T4 \
  --repetitions 3 --provider anthropic --model claude-haiku-5-5 \
  --wysna-bin /path/to/wysna --output /tmp/scale_25k_haiku.json
```

---

## 4. Honest Limitations & What This Does Not Measure

- **Synthetic scale regularity:** Submodules beyond the core 6 domain modules follow structured templates; they measure symbol/import graph dilution and search noise rather than messy legacy idioms.
- **Text tool protocol vs native function calling:** All models share a plain ```tool_call``` fenced JSON protocol so providers without native tool calling (and cross-provider comparisons) stay symmetric; vendor-native schema caching may differ slightly.
- **Single-language regime:** This suite compares `B_wysna` against `A_baseline` *within Wysna*. Comparing end-to-end scale efficiency against Go/Python repos requires separate multi-language ports (planned only after Condition B passes its internal gate).
