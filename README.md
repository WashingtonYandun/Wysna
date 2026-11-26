<p align="center">
  <img src="assets/wawi.jpg" alt="Wawi, the Wysna mascot" width="112" />
</p>

# Wysna

**A compiled programming language built for AI agents.**

Wysna is statically typed and compiles to native executables through C99. It targets automation workflows, data processing, and logic shared between CLI apps, services, and the browser. Its compiler lets agents query code by symbol or byte span, read structured diagnostics, and verify edits before writing them to disk.

[Official website](https://wysna.wysofia.com/) · [Releases](https://github.com/WashingtonYandun/Wysna/releases/latest) · [First project](#first-project) · [Documentation](docs/README.md)

[![CI on main](https://github.com/WashingtonYandun/Wysna/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/WashingtonYandun/Wysna/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/WashingtonYandun/Wysna)](https://github.com/WashingtonYandun/Wysna/releases/latest)
[![Apache 2.0 license](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

## Installation

Install the **Native SDK** (self-hosted compiler, bundled Zig C toolchain, `project`, `test`, `mcp`, and `lsp` — zero Python required) in one command with automatic SHA-256 verification and `PATH` setup:

**Windows (PowerShell):**

```powershell
irm https://wysna.wysofia.com/install.ps1 | iex
```

**macOS (Apple Silicon) & Linux (x64):**

```sh
curl -fsSL https://wysna.wysofia.com/install.sh | sh
```

**Windows (Scoop) or Graphical Installers:**

```powershell
scoop install https://wysna.wysofia.com/scoop/wysna.json
```

Or download standalone graphical installers (`.exe`, `.pkg`, `.deb`, `.rpm`) and offline archives from [wysna.wysofia.com/download](https://wysna.wysofia.com/download/) or [GitHub Releases](https://github.com/WashingtonYandun/Wysna/releases).

- **[Native SDK](docs/NATIVE_SDK.md):** Default distribution above; builds and tests native apps, manages local packages, and exposes AI agent tooling without Python.
- **[Seed](docs/SEED_DISTRIBUTION.md):** Reference interpreter, interactive REPL, WASI, and browser targets with a bundled private Python runtime (`irm ... | iex` with `$env:WYSNA_DIST="seed"`, or `.exe` / `.pkg` / `.deb` / `.rpm`).

Confirm installation in any new terminal with `wysna --version`.

The examples in this README target **Wysna 1.1.0**.

<a id="first-project"></a>

## First project

Save the program as `quote.wysna`. Its contract requires a positive quantity and a non-negative unit price (expressed in cents).

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

The program prints `3897`. To scaffold a project with a manifest, lockfile, and starter test, run:

```sh
wysna project new my-app
cd my-app
wysna project lock
wysna project check --frozen
wysna project test --frozen
```

<a id="project-impact"></a>

## Understanding and changing a project

Before editing a function, inspect its dependencies with `graph` and retrieve only the declaration or span you need with `inspect --span`. From a repository checkout, this query shows which project entry points share the quote validation helper:

```sh
wysna graph --manifest examples/shared-browser-app/wysna.toml --symbol quote_validation_code --rel impact
```

The output returns dependency chains alongside any boundaries static analysis could not resolve. An agent can preview and apply multi-file edits verified by tests using `patch --test` (backed by `plan_hash` guards and content-addressed native test binary caching), read structured `json-v1` diagnostics pointing directly to candidate fixes, batch reads under a shared context budget, and connect via MCP or the VS Code extension. See the [AI agent workflow guide](docs/AI_AGENT_GUIDE.md).

To enforce architectural boundaries, assign roles to classes via typed attributes and forbid direct cross-layer dependencies in `wysna.toml`. Both `project check` and `patch` validate those rules: `W6101` reports a confirmed violation, and `W6102` flags inconclusive evidence. See [Architecture contracts](docs/ARCHITECTURE_CONTRACTS.md).

## Projects and packages

`wysna.toml` declares targets, dependencies, and test suites. The native SDK packs local archives, pins them by SHA-256, and restores them offline from a cache. Use `wysna.lock` with `--frozen` to verify deterministic resolution. See [Projects](docs/PROJECTS.md).

- [Operations audit](examples/wysofia-ops/README.md) validates JSON events and builds reports, with an optional SQLite target.
- [Automation sync](examples/automation-sync/README.md) fetches a bounded batch and imports records into SQLite inside a transaction.
- [Shared quote app](examples/shared-browser-app/README.md) shares a validation rule across CLI, server, and browser targets.
- [Architecture contracts](examples/architecture/wysna.toml) checks direct dependency constraints across controller, service, and repository layers.

## Language status

Wysna is pre-1.0. It includes contracts, typed error unions, pattern matching, generics, and `async`/`await`. The native SDK ships the Selfhost compiler written in Wysna with C99 as its primary backend, while Seed maintains the reference interpreter. Review platform scope and capability limits before selecting a deployment target: [Architecture](docs/COMPILER_ARCHITECTURE.md) · [Engine support](docs/ENGINE_PARITY_MATRIX.md) · [Known limits](docs/KNOWN_LIMITS.md) · [Effects](docs/EFFECTS.md).

## Documentation and contributing

- [Web handbook](https://wysna.wysofia.com/) · [Syntax reference](WYSNA_SINTAXIS.md) · [Specification](SPEC.md) · [Standard library](docs/STDLIB.md)
- [AI agent guide](docs/AI_AGENT_GUIDE.md) · [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md) · [Security](SECURITY.md) · [Code of conduct](CODE_OF_CONDUCT.md)

Wysna is distributed under the [Apache 2.0 license](LICENSE). See [NOTICE](NOTICE) for attributions.
