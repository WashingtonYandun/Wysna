<p align="center">
  <img src="assets/wawi.jpg" alt="Wawi, the Wysna mascot" width="112" />
</p>

# Wysna

**A compiled language for automation and tools that developers and coding agents can maintain together.**

Wysna is statically typed and builds native executables through C99. Use it to process JSON, validate data before saving it, or share a business rule between a command-line tool and the browser. The compiler provides structured diagnostics and a semantic graph that editors and agents can query to see how a change affects the project.

[Downloads](https://github.com/WashingtonYandun/Wysna/releases) · [First project](#first-project) · [Project impact](#project-impact) · [Documentation](docs/README.md)

[![CI on main](https://github.com/WashingtonYandun/Wysna/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/WashingtonYandun/Wysna/actions/workflows/ci.yml)
[![Latest release](https://img.shields.io/github/v/release/WashingtonYandun/Wysna)](https://github.com/WashingtonYandun/Wysna/releases/latest)
[![Apache 2.0 license](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

## Installation

Selecciona tu distribución en [GitHub Releases](https://github.com/WashingtonYandun/Wysna/releases). Los flujos mostrados aquí requieren **Wysna 0.9.0 o posterior**. Para proyectos existentes, sigue la [guía de migración 0.8 → 0.9](docs/MIGRATING_0_8_TO_0_9.md).

| Distribution | Use it for | Platforms |
| --- | --- | --- |
| [Native SDK](docs/NATIVE_SDK.md) | C99 projects, tests, and agent tools. It includes the self-hosted compiler, Zig, and the standard library. It runs without Python. | Windows x64, macOS arm64, Linux x64 |
| [Seed](docs/SEED_DISTRIBUTION.md) | WebAssembly, WASI, direct LLVM compilation, and the reference interpreter and REPL. Installers include CPython and Zig. | Windows x64, macOS arm64, Linux x64 |

To use the native SDK, verify the archive against `SHA256SUMS`, extract it, and add its `bin` directory to your PATH. On Windows, you can also open `open-terminal.cmd` from the extracted folder. Confirm the installation with `wysna --version`.

## First project

Save this program as `quote.wysna`. The contract requires a positive quantity and a nonnegative price. The calculation represents prices in integer cents.

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

The program prints `3897`. The resulting executable runs without Python. If your program links against external libraries, follow your platform's rules for distributing them.

Create a project with a manifest and an initial test:

```sh
wysna project new my-app
cd my-app
wysna project lock
wysna project test --frozen
```

The native SDK generates and verifies lockfiles for projects with or without local package dependencies. See the [project guide](docs/PROJECTS.md) for manifests, local dependencies, and build targets.

## Project impact

The semantic graph traces relationships in code checked by the compiler. In the [quote example](examples/shared-browser-app/README.md), the CLI, service, and browser share a pricing rule. From a checkout of this repository, run this query to see which project entries to retest when that rule changes:

```sh
wysna graph --manifest examples/shared-browser-app/wysna.toml --symbol quote_validation_code --rel impact
```

Each affected entry comes with a chain of relationships that leads to the changed symbol, including references to functions passed as values. The report marks uncertain entries when the compiler cannot resolve an indirect call or sees a boundary such as JavaScript or linked C code. Run **Wysna: Show Project Impact** in VS Code, or call `wysna_graph` over MCP for the same report.

| Task | Tool |
| --- | --- |
| Find dependent code and inspect a focused excerpt | `graph` and `inspect --with-deps` |
| Read structured compiler diagnostics | `check --output-format=json-v1` |
| Edit a declaration against an expected graph revision | `patch --expect-graph-hash` |
| Inspect effects and describe tool inputs | `explain` and `schema` |
| Connect an editor or a coding agent | `lsp`, `mcp`, and the [VS Code extension](editors/vscode-wysna/README.md) |

The [agent guide](docs/AI_AGENT_GUIDE.md) covers the workflow and output formats. The [evaluation tools](bench/ai_eval/README.md) compare model-selected graph queries with source reads and measure test impact against observed changes in real projects.

## Examples

| Project | What it demonstrates |
| --- | --- |
| [Operations audit](examples/wysofia-ops/README.md) | Validate JSON events and produce an audit report, with an optional SQLite target. |
| [Measurement sync](examples/automation-sync/README.md) | Fetch a bounded batch and import it into SQLite in a transaction. |
| [Quote application](examples/shared-browser-app/README.md) | Share validation and pricing logic across a CLI, a service, and WebAssembly. |

## Status and architecture

Wysna is pre-1.0. C99 is the primary native target, with verified intermediate representations before C generation. Seed is the Python reference implementation. The native SDK uses the self-hosted compiler written in Wysna. The [compiler architecture](docs/COMPILER_ARCHITECTURE.md) and [conformance matrix](docs/ENGINE_PARITY_MATRIX.md) describe the engines and the behavior covered by their tests.

Wysna supports contracts, typed errors, pattern matching, generics, and `async`/`await`. The runtime uses reference counting and copy-on-write collections. The direct LLVM backend is experimental. See the [supported scope](docs/SCOPE.md) and [known limits](docs/KNOWN_LIMITS.md).

Native executables inherit the permissions of their process. See the [effect model](docs/EFFECTS.md) and [security policy](SECURITY.md) for the boundaries of effect analysis and enforcement, including calls into external C code.

## Documentation and contribution

- [Syntax reference](WYSNA_SINTAXIS.md) · [Language specification](SPEC.md) · [Standard library](docs/STDLIB.md)
- [Documentation index](docs/README.md) · [Changelog](CHANGELOG.md)
- [Contributing](CONTRIBUTING.md) · [Code of conduct](CODE_OF_CONDUCT.md)

Wysna is distributed under the [Apache License 2.0](LICENSE). See [NOTICE](NOTICE) for attribution.
