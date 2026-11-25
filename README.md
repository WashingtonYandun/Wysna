<p align="center">
  <img src="assets/wawi.jpg" alt="Wawi, the Wysna mascot" width="112" />
</p>

# Wysna

**A compiled, Agent-First systems programming language that developers and coding agents can maintain together.**

Wysna is statically typed and compiles to native executables via C99 with an 8-byte NaN-boxed `WVal` ABI, atomic ARC with Copy-on-Write value semantics, stackless CPS `async`/`await`, typed recoverable error channels (`T ! E`), and verifiable contracts (`requires` / `ensures`). The compiler exposes deterministic structured diagnostics, a compile-time semantic graph, bounded context inspection, and guarded multi-file patching so AI coding agents can analyze, edit, and test projects safely without guessing.

**[Official Website & Handbook](https://wysna.wysofia.com/)** · [Downloads](https://github.com/WashingtonYandun/Wysna/releases) · [First project](#first-project) · [Agent-First tooling](#agent-first-tooling) · [Documentation](docs/README.md)

[![CI on main](https://github.com/WashingtonYandun/Wysna/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/WashingtonYandun/Wysna/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/WashingtonYandun/Wysna)](https://github.com/WashingtonYandun/Wysna/releases/latest)
[![Apache 2.0 license](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

## Installation

Visit the official site at **[wysna.wysofia.com](https://wysna.wysofia.com/)** for guides and installation instructions, or download release archives directly from [GitHub Releases](https://github.com/WashingtonYandun/Wysna/releases). The native SDK and agent workflows documented below target **Wysna 0.9.5 or later** (if upgrading from 0.8, follow the [0.8 → 0.9 migration guide](docs/MIGRATING_0_8_TO_0_9.md)).

| Distribution | Use it for | Platforms |
| --- | --- | --- |
| [Native SDK](docs/NATIVE_SDK.md) | Native C99 projects, lockfiles, tests, and Agent-First tools (`graph`, `inspect`, `patch`, `mcp`, `lsp`). Bundles the self-hosted compiler, Zig, and stdlib with zero Python runtime dependency. | Windows x64, macOS arm64, Linux x64 |
| [Seed](docs/SEED_DISTRIBUTION.md) | WebAssembly, WASI (`wasm32-wasip1`), browser libraries, experimental direct LLVM compilation, and reference engine/REPL. Installers bundle CPython and Zig. | Windows x64, macOS arm64, Linux x64 |

To install the Native SDK manually, verify the archive against `SHA256SUMS`, extract it, and add its `bin` directory to your `PATH` (or run `open-terminal.cmd` on Windows). Confirm with `wysna --version`.

## First project

Save this program as `quote.wysna`. Contracts verify a positive quantity and nonnegative unit price (in integer cents):

```wysna
pub fn quote_total(quantity: int, unit_price_cents: int) -> int
    requires quantity > 0, unit_price_cents >= 0
    ensures result == quantity * unit_price_cents
{
    return quantity * unit_price_cents
}

print(quote_total(3, 1299))
```

```sh
wysna check quote.wysna
wysna run quote.wysna
wysna build quote.wysna -o quote
```

The program prints `3897`, and the compiled binary runs standalone without Python.

Initialize and verify a manifest-backed project (`wysna.toml` + deterministic SHA-256 `wysna.lock`):

```sh
wysna project new my-app
cd my-app
wysna project lock
wysna project test --frozen
```

See [Wysna Projects](docs/PROJECTS.md) for manifest configuration, offline SHA-256 package archives (`wysna packages`), C FFI linking, and WASI/browser targets.

## Agent-First tooling

Wysna treats AI coding agents and IDE extensions as first-class compiler clients through `--output-format=json-v1` (validated against [schemas/wysna-tool-result.v1.schema.json](schemas/wysna-tool-result.v1.schema.json)) and an in-memory stdio MCP daemon (`wysna mcp`).

Trace cross-target impact in a multi-target project such as the [quote example](examples/shared-browser-app/README.md):

```sh
wysna graph --manifest examples/shared-browser-app/wysna.toml --symbol quote_validation_code --rel impact
```

Every `graph` / `inspect` response identifies symbols by canonical declaration identity (returning `[W1005]` candidates on ambiguity), tracks `call_resolution` and `analysis_boundaries` (`virtual-dispatch`, `indirect-call`, `c-ffi`), and enforces byte budgets with resumable handles (`--max-bytes`, `omitted[].ref`).

| Capability | CLI / MCP Interface | Highlights in 0.9.5 |
| --- | --- | --- |
| **Semantic graph & impact** | `graph`, `wysna_graph` | Declaration-level disambiguation (`W1005`), callers/callees/usages/implements/impact, uncertainty & boundary flags |
| **Bounded & batch inspect** | `inspect`, `wysna_inspect` | Single or batch (`--symbols a,b` / `symbols: [...]`) inspection sharing `--max-bytes`, Seed/Selfhost parity |
| **Structured diagnostics** | `check`, `project check`, `wysna_check`, `wysna_project_check` | Selfhost multi-error recovery (up to 100 diagnostics with cascade suppression) + typed `data` and `related` spans |
| **Atomic semantic patching** | `patch`, `wysna_patch` | Memory dry-run by default; single-symbol `--expect-graph-hash` or multi-file `--manifest --edits` (`source`, `symbol`, `hunks`, `create`, `delete`) with `--expect-plan-hash` and rollback |
| **Project tests & isolation** | `project test`, `wysna_project_test` | Frozen test plans, `--list` discovery, isolated temp fixtures, and opt-in execution over MCP (`--allow-exec`) |
| **Schemas, auto-fix & LSP** | `schema`, `fix`, `explain`, `api`, `lsp`, `skills` | Draft 2020-12 `@AgentTool` schemas, mechanical `W3001`/`W1001` autofixes, and [VS Code extension](editors/vscode-wysna/README.md) |

Read the [AI Agent Guide](docs/AI_AGENT_GUIDE.md) and [agent benchmark harness](bench/ai_eval/README.md) for the complete protocol, deterministic quotas (`--max-steps`, `--max-heap-mb`), and effect trace replay (`--record-trace`, `--replay-trace`).

## Examples

| Project | What it demonstrates |
| --- | --- |
| [Operations audit](examples/wysofia-ops/README.md) | Validated JSON event auditing with contracts, `T ! E`, and optional SQLite storage built with the Native SDK. |
| [Measurement sync](examples/automation-sync/README.md) | Bounded remote/file batch ingestion into SQLite inside an explicit transaction. |
| [Quote application](examples/shared-browser-app/README.md) | Shared pricing and validation logic across native CLI, service, and WebAssembly targets. |

## Architecture and current scope

- **Dual-engine parity & native C99 backend:** Seed is the Python reference oracle; the Self-Hosted compiler (`selfhost/`) powers the standalone Native SDK through a verified AST → HIR → MIR → C99 pipeline with C99-accelerated JSON handling, loop hoisting/borrowing, and dead-code elimination. Direct LLVM emission remains experimental. See [Compiler Architecture](docs/COMPILER_ARCHITECTURE.md) and [Engine Parity Matrix](docs/ENGINE_PARITY_MATRIX.md).
- **Safety & concurrency:** Static types, custom typed static metadata attributes (`struct` schemas), algebraic pattern matching with exhaustiveness checks, atomic ARC + Copy-on-Write (`Weak[C]`), isolated-ownership parallel workers (`std/algorithms`, `std/async.spawn`), and `--sandbox` / OS-boundary controls. See [Scope](docs/SCOPE.md), [Known Limits](docs/KNOWN_LIMITS.md), [Effects](docs/EFFECTS.md), and [Security Policy](SECURITY.md).

## Documentation and community

- **Web & Handbook:** [https://wysna.wysofia.com/](https://wysna.wysofia.com/)
- **Language specs:** [Syntax reference](WYSNA_SINTAXIS.md) · [Language specification](SPEC.md) · [Standard library](docs/STDLIB.md)
- **Guides & releases:** [Documentation index](docs/README.md) · [Changelog](CHANGELOG.md) · [0.9.5 Release Notes](docs/release-notes/v0.9.5.md)
- **Contributing:** [Contributing](CONTRIBUTING.md) · [Code of conduct](CODE_OF_CONDUCT.md)

Wysna is licensed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution.
