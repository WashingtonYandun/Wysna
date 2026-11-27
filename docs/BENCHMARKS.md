# Wysna Benchmarks (v1.1.0)

This document records empirical performance, binary footprint, toolchain diagnostic latency, and live AI coding evaluations for **Wysna 1.1.0 (`native-core-v1`, `-O2`)** at commit `c20a640`.

---

## 1. Test Environment & Toolchains

- **Hardware / OS:** Apple M3 (`Mac15,12`, 8 CPUs, 8 GiB RAM) · macOS `27.2.1` (`26B131`)
- **Wysna:** `Wysna 1.1.0 native SDK` (`c20a6402d9777d3d56f63baa2cd2c19689385cfe`, `-O2`, clean tree)
- **Rust:** `rustc 1.100.0 (0ef1bc386 2026-11-09) (Homebrew)`
- **Go:** `go version go1.27.4 darwin/arm64`
- **Python:** `Python 3.14.7`
- **JavaScript:** Node `v26.8.2`
- **Java:** `openjdk 25.0.4.1 2026-08-18 LTS` (`jackson-core-2.21.5.jar` for JSON)
- **Live AI Provider:** Anthropic `claude-haiku-5-5` (zero mocks)

---

## 2. Six-Language Runtime & Memory (30 samples)

Measured as fresh OS processes in rotated serial order (`bench/cross_language/run.py`):

| Benchmark | Wysna | Rust | Go | Python | Node JS | Java (HotSpot) |
|---|---|---|---|---|---|---|
| `startup` | **2.394 ms** (p95 `2.777`, **1.6 MiB**) | 2.859 ms (p95 `3.056`, 1.7 MiB) | 3.409 ms (p95 `3.821`, 4.0 MiB) | 17.365 ms (p95 `18.161`, 14.0 MiB) | 28.167 ms (p95 `30.164`, 51.5 MiB) | 20.340 ms (p95 `21.252`, 39.4 MiB) |
| `ledger_1000` | **2.542 ms** (p95 `2.967`, **2.0 MiB**) | 2.682 ms (p95 `2.978`, 2.0 MiB) | 3.572 ms (p95 `3.720`, 4.5 MiB) | 17.849 ms (p95 `18.456`, 14.3 MiB) | 29.249 ms (p95 `30.847`, 53.8 MiB) | 27.157 ms (p95 `28.125`, 41.5 MiB) |
| `json_1000` | 2.884 ms (p95 `3.310`, 2.2 MiB) | **2.579 ms** (p95 `2.805`, **2.0 MiB**) | 3.890 ms (p95 `4.119`, 5.0 MiB) | 22.376 ms (p95 `22.943`, 16.1 MiB) | 29.301 ms (p95 `30.814`, 52.3 MiB) | 58.173 ms (p95 `59.119`, 51.6 MiB) |
| `ledger_20000` | 5.150 ms (p95 `5.493`, 5.9 MiB) | **4.711 ms** (p95 `4.994`, **3.9 MiB**) | 5.665 ms (p95 `5.988`, 8.7 MiB) | 22.989 ms (p95 `23.750`, 20.1 MiB) | 35.972 ms (p95 `37.032`, 68.8 MiB) | 38.785 ms (p95 `39.721`, 49.1 MiB) |
| `json_10000` | 6.172 ms (p95 `6.495`, 5.3 MiB) | **3.699 ms** (p95 `3.981`, **2.5 MiB**) | 6.958 ms (p95 `7.317`, 6.4 MiB) | 26.548 ms (p95 `27.825`, 18.9 MiB) | 30.516 ms (p95 `32.330`, 58.6 MiB) | 67.064 ms (p95 `71.344`, 55.7 MiB) |

### Binary Size, Build Latency & Static JSON Diagnostics

| Metric | Wysna | Rust | Go | Python | Node JS | Java |
|---|---|---|---|---|---|---|
| Binary size (`startup` / `json` / `ledger`) | **90.6 KB** / **165.8 KB** / **130.2 KB** | 435.8 KB / 538.4 KB / 456.9 KB | 2.43 MB / 3.78 MB / 2.43 MB | N/A | N/A | `.class` (407 B / 5.5 KB / 5.9 KB) |
| Cold `json` release build | 1,641.38 ms | 3,763.50 ms | 2,559.82 ms | N/A | N/A | **435.25 ms** (bytecode) |
| Edited CLI rebuild/check p50 (5 samples) | **101.15 ms** (min `93.15 ms`, 61.9 MB RSS) | 114.10 ms (89.6 MB RSS) | 103.29 ms (68.2 MB RSS) | 35.13 ms (`py_compile`, 20.9 MB) | 27.83 ms (`--check`, 52.8 MB) | 214.34 ms (91.1 MB RSS) |
| JSON type diagnostic (`let value: int = "wrong"`) | **18.636 ms** (`check --output-format=json-v1`, **4.15 MB** RSS, exit `1`) | 50.844 ms (`--emit=metadata --error-format=json`, 82.49 MB, exit `1`) | 39.009 ms (`go build -json`, 24.31 MB, exit `1`) | 36.262 ms (`py_compile`, 20.92 MB, exit `0`, syntax-only) | 28.603 ms (`--check`, 52.48 MB, exit `0`, syntax-only) | 203.456 ms (`javac -XDrawDiagnostics`, 90.78 MB, exit `1`) |

---

## 3. `wysna run` Cold vs Warm `run-cache` (Single-File & Multi-File)

Compares cold compilation + execution against the warm median of 5 consecutive runs with transitive local-import hashing:

| Workload | Runner | Cold (ms) | Warm Median (ms) | Notes |
|---|---|---|---|---|
| `startup` (single-file) | `wysna run --profile=release` | 216.70 | **16.68** | Native binary cache |
| `startup` (single-file) | `wysna run --profile=dev` | 3,583.21 | **18.99** | Dev binary cache |
| `startup` (single-file) | `go run` | 2,036.20 | 46.78 | Go build cache |
| `startup` (single-file) | `python3` / `node` | 16.35 / 33.70 | 16.28 / 28.39 | Interpreters / JIT |
| `ledger` (`main.wysna` + `ledger.wysna`) | `wysna run --profile=release` | 368.48 | **18.61** | Transitive `run-cache` (`2` entries); invalidates on imported edit |
| `ledger` (`main.wysna` + `ledger.wysna`) | `wysna run --profile=dev` | 3,688.03 | **18.84** | Transitive `run-cache` |
| `ledger` (`main.wysna` + `ledger.wysna`) | `go run` | 227.23 | 46.84 | `2.52x` slower warm than `wysna` |
| `ledger` (`main.wysna` + `ledger.wysna`) | `python3` / `node` | 17.58 / 29.08 | 16.26 / 29.57 | Interpreters / JIT |

---

## 4. Wysna AI Evaluation Evolution (30-Task Corpus, `claude-haiku-5-5`)

Protocol: `bench/ai_eval/run_eval.py --provider=anthropic --model=claude-haiku-5-5 --max-repair-cycles=3` (up to 4 total attempts: initial generation + 3 diagnostic repair turns).

| Metric | Baseline (`a9d4783`) | After Diagnostics & Fix Improvements (`c20a640`) | Delta |
|---|---|---|---|
| **Resolved (`<= 4` attempts)** | 27 / 30 (`90.0%`) | **30 / 30 (`100.0%`)** | **+3 tasks (+10.0 pp)** |
| **pass@1** | 13 / 30 (`43.3%`) | **16 / 30 (`53.3%`)** | **+3 (+10.0 pp)** |
| **pass@3** | 25 / 30 (`83.3%`) | **28 / 30 (`93.3%`)** | **+3 (+10.0 pp)** |
| **Total model turns (avg)** | 59 (`1.97`) | **50 (`1.67`)** | **-9 (-15.3%)** |
| **Average wall latency** | 11,492.66 ms | **9,075.07 ms** | **-2,417.59 ms (-21.0%)** |
| **Average provider latency** | 10,927.96 ms | **8,654.72 ms** | **-2,273.24 ms (-20.8%)** |
| **Total tokens** | 228,142 (`154,207` p / `73,935` c) | **202,682 (`143,911` p / `58,771` c)** | **-25,460 (-11.2%)** |

Per-domain progression (`pass@1` / `pass@3` / `resolved`):
- `cli_scripting` (12): `5 -> 8` `pass@1`, `12/12` `pass@3`, `12/12` `resolved`
- `data_files` (8): `3 -> 3` `pass@1`, `6 -> 7` `pass@3`, `7/8 -> 8/8` `resolved`
- `multi_module` (6): `4 -> 4` `pass@1`, `4 -> 6` `pass@3`, `4/6 -> 6/6` `resolved`
- `system_sqlite` (4): `1/3/4 -> 1/3/4`

---

## 5. Cross-Language AI Authoring Snapshot (8 Tasks `XL-01`..`XL-08`, `claude-haiku-5-5`)

| Language | pass@1 | Resolved | Total Turns | Total Tokens (`prompt / completion`) | Total LLM | Total Compile | Total Exec | Median LOC (bytes) |
|---|---|---|---|---|---|---|---|---|
| **Python** | **8 / 8** | **8 / 8** | 8 | 5,519 (`1,863 / 3,656`) | 17,589.46 ms | 342.02 ms | 172.85 ms | 19.0 (`528.0 B`) |
| **Go** | **8 / 8** | **8 / 8** | 8 | 6,878 (`1,880 / 4,998`) | 22,153.39 ms | 1,801.03 ms | 1,252.30 ms | 24.0 (`569.5 B`) |
| **Wysna** | 7 / 8 | 7 / 8 | 11 | 45,550 (`31,709 / 13,841`) | 60,144.65 ms | **1,468.69 ms** | **857.05 ms** | 28 (`856 B`) |
| **Rust** | 7 / 8 | 7 / 8 | 11 | 34,379 (`12,735 / 21,644`) | 78,918.31 ms | 2,317.48 ms | 1,010.83 ms | 18 (`541 B`) |

### Methodology & Honest Caveats
1. **Prompt-token asymmetry:** Go, Python, and Rust are present in LLM pretraining weights, so minimal instructions suffice (`1,863`–`1,880` prompt tokens for Go/Python across 8 tasks). Wysna injects `SYSTEM_PROMPT` (`bench/ai_eval/harness.py`) to teach grammar and stdlib signatures out-of-distribution (`31,709` prompt tokens total).
2. **Stdlib scope & `XL-07` failure analysis:** `XL-07` required parsing and filtering JSON arrays without external package managers. Go and Python passed `8/8` via built-in stdlib JSON. Rust (`7/8`) lacks JSON in `std`, so the model wrote a recursive-descent parser from scratch and hit the 4,096 output-token limit (`unclosed delimiter`). Wysna (`7/8`) passed `XL-01`..`XL-06` and `XL-08` on `pass@1`, compiling faster than `rustc` (`1,468.69 ms` vs `2,317.48 ms`), and failed `XL-07` due to unqualified `JsonError` after `import "std/json" as json` and applying `??` to `str ! JsonError`. Those findings directly drove the `W1001`/`W2006` diagnostic, autofix, and prompt updates following `c20a640`.
3. **Hardware scope:** Results reflect Apple M3 (`8 GiB` RAM, macOS) with synthetic benchmark fixtures and clean-process latency; long-running server steady-state throughput differs from CLI startup-inclusive timings.

---

## 6. Reproducing Locally

```sh
# 1. Six-language runtime, build, and diagnostic latency benchmark
python3 bench/cross_language/run.py \
  --wysna /path/to/wysna-native-sdk/bin/wysna \
  --jackson /path/to/jackson-core-2.21.5.jar \
  --output bench/cross_language/results-local.json

# 2. 30-task AI authoring & repair benchmark (requires real provider credentials in env)
python3 bench/ai_eval/run_eval.py \
  --compiler /path/to/wysna-native-sdk/bin/wysna \
  --provider anthropic \
  --model claude-haiku-5-5 \
  --max-repair-cycles 3 \
  --output /tmp/wysna-ai-eval.json
```
