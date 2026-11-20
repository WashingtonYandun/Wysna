# Wysna Workflow for AI Agents

Use the installed Seed CLI as the project workflow. It provides deterministic
project checks, builds, tests, API discovery and versioned machine output. The
standalone `native-core-v1` SDK provides `check`, `fmt`, `test`, `build`, `run`, `graph`,
`inspect`, `patch`, `explain`, and Stage-3 native `lsp` without Python, while Seed
continues to own project manifests (`wysna.toml`), lockfiles, `fix`, `schema`, `mcp`,
and WebAssembly targets.

## Discover the installed toolchain

Start by reading the commands and target contracts from the compiler you will run:

```sh
wysna capabilities --output-format=json-v1
wysna api std/json --output-format=json-v1
```

The `wysna.capabilities/1` result reports the installed commands and each target's
engine, backend, triple and capabilities. Its `build_requirements` and
`run_requirements` arrays contain objects with `name`, `version` and `provided_by`
fields. Select only a profile returned by this command. A target's presence does
not mean every Wysna compiler implementation supports it; inspect the `engine`
field and use the matching installed toolchain.

## Create, check, build and test a project

```sh
wysna project new hello-agent --output-format=json-v1
wysna project check --manifest hello-agent/wysna.toml --output-format=json-v1
wysna project build --manifest hello-agent/wysna.toml --output-format=json-v1
wysna project run --manifest hello-agent/wysna.toml
wysna project test --manifest hello-agent/wysna.toml --report-json hello-agent/test-report.json
```

Use `wysna api --manifest path/to/wysna.toml --output-format=json-v1` to inspect
the checked project's modules and public symbols before choosing library calls.
Use `--frozen` on project checks and builds when the project includes a lockfile.
Program arguments belong after `--` for `project run` and are passed as separate
arguments, without implicit shell parsing.

## Handle diagnostics and self-heal from their source snapshot

Prefer `--output-format=json-v1` for `check`, `fix`, `schema`, and `project check`.
The type checker recovers across independent statements, class methods, and generic
templates to report up to 100 independent diagnostics in a single pass while
suppressing cascade noise from failed bindings.

Read each diagnostic's `code`, `severity`, `stage`, `message`, `data`, `related`,
and `fixes`:
- If `primary_span` is present, its offsets `start_byte` and `end_byte` are UTF-8
  byte offsets into the source identified by `source_id`; verify `source_sha256`
  before editing that snapshot. Do not treat byte offsets as Unicode character
  indexes, and do not guess a location when `primary_span` is absent.
- `data` carries structured machine facts (`expected`, `found`, `conversion_hint`,
  `unwrap_hint`, `missing_field`, `help`).
- `related` points to secondary source spans (such as the immutable `let`
  declaration on `W3001` or the prior declaration on `W1002`).
- `fixes` contains SHA-256-guarded byte-range edits (`applicability: "machine"` or
  `"maybe"`). Run `wysna fix --apply --output-format=json-v1 <file.wysna>` before
  spending an LLM repair turn to automatically repair identifier/field typos and
  immutable `let` -> `let mut` bindings.

Re-run the check after editing and require a successful result before building.

## Extract JSON Schemas, Tool Contracts, and Use the MCP Server

Generate JSON Schema (Draft 2020-12) `$defs` for `struct`/`enum` types and LLM
tool definitions (including verified MIR `effects`, `capabilities`, `pure`, and
`idempotent` flags) directly from Wysna source:

```sh
wysna schema --output-format=json-v1 src/main.wysna
```

For multi-turn agent loops, start the persistent in-memory Model Context Protocol
server over stdio to avoid Python startup and temporary-file overhead on every check:

```sh
wysna mcp --root .
```

The MCP server exposes `wysna_check`, `wysna_fix`, `wysna_fmt`, `wysna_explain`,
`wysna_schema`, `wysna_api`, and `wysna_capabilities` confined to `--root`.

For zero-Python native semantic inspection, checked edits, and editor integration in
the standalone SDK / Selfhost Stage-2+ toolchain:

```sh
wysna graph src/main.wysna --output-format=json-v1
wysna graph src/main.wysna --symbol Service --rel impact --output-format=json-v1
wysna inspect src/main.wysna --symbol run_pipeline --with-deps --output-format=json-v1
wysna patch src/main.wysna --symbol Service.run --expect-graph-hash graph:... --with "..." --apply --output-format=json-v1
wysna lsp
```

`wysna patch` rejects stale `graph:...` hashes with `W5005` and verifies types in
memory before mutating disk. When applying edits via Seed CLI or MCP (`wysna patch --apply`
and `wysna fix --apply`), cooperating Wysna writers serialize per file with a cooperative
lock, re-verify the exact source SHA-256 inside the critical section (failing with `W5005`
on concurrent modification), and publish atomically while preserving UTF-8 line endings
and file permissions. External editors that write directly without using Wysna tooling do
not hold the cooperative lock; optimistic revision verification still rejects stale snapshots
detected before replacement.

## Agent-First Imports & Token Optimization

When generating Wysna code, agents should prefer the selective `from ... use { ... }` syntax over namespace qualification:

```wysna
from std.text use {
    contains,
    to_lower,
    split as text_split,
}
```

- **Token Economy:** Direct symbol importing avoids repetitive `module.` qualifiers, saving ~13%–22% BPE tokens in dense multi-step logic.
- **Canonical Formatting:** Multiple symbols must be enclosed in braces `{ ... }` with trailing commas; single symbols can be unbraced (`from std.math use sqrt`).
- **Disambiguation:** If symbol names clash across imported modules, assign local aliases using `as` (`User as DomainUser`).
- **Bootstrap Namespace Parity:** Selective `from ... use { ... }` imports (including aliases) and whole-module namespace imports (`import "std/json"`) are both supported across Seed and the self-hosted `native-core-v1` compiler.

## Execution Quotas, Effect Cassettes, and First-Class Contracts

To safeguard agent loops against runaway execution and unrepeatable I/O side-effects:

- **Enforce quotas:** Pass `--max-steps=<N>` (instruction budget) and `--max-heap-mb=<N>` (memory budget). Exceeding limits halts execution deterministically with error code `[W4004]`. In native binaries, set `WYSNA_MAX_STEPS` and `WYSNA_MAX_HEAP_BYTES`.
- **Capture and replay effects:** Use `wysna run --record-trace=trace.json <file.wysna>` to capture all external operations (HTTP, clock, filesystem) into a versioned `wysna.effect.trace/1` cassette. Use `--replay-trace=trace.json` to rerun offline with bit-identical deterministic results.
- **Write declarative contracts:** Add `requires` preconditions and `ensures` postconditions (referencing `result`) to function declarations. `wysna schema` automatically reflects them as JSON Schema bounds for LLM tool calling.

## Respect declared limits

- Use `native-cli` for host executables, `wasi-command` for constrained WASI
  commands, and `browser-library` for the generated browser ABI. The capability
  response states the toolchain and runtime each one needs.
- The native SDK currently implements `native-core-v1` source checking, MIR/C99
  emission, native build/run/test/fmt, Agent-First tools (`graph`, `inspect`, `patch`, `explain`), and Stage-3 native `lsp` without Python. Do not assume Seed project manifests, `fix`, `schema`, `mcp`, WASI or browser targets are available through that SDK.
- Effect reports and capability declarations help review programs; `wysna run --allow/--deny` enforces static pre-execution gates (`W5006`) and Seed `wysna build --sandbox` (with `--allow/--deny`) compiles `WYSNA_DENY_CAP_*` runtime checks (`W5006`), though neither replaces an OS kernel sandbox for arbitrary native C extensions.
- AI authoring and self-healing repair benchmarks live in `bench/ai_eval/`
  (`harness.py` and `token_audit.py`); outside this evaluation suite, Wysna
  has not been measured against other languages for general AI-authored
  program quality.

See [project workflows](PROJECTS.md), [native SDK](NATIVE_SDK.md),
[effect contracts](EFFECTS.md), and [Seed distribution](SEED_DISTRIBUTION.md) for
the complete interfaces and limitations.

## Impacto de un símbolo en el proyecto

En Seed, consulta los destinos y pruebas declarados desde el manifiesto:

```sh
wysna graph --manifest examples/shared-browser-app/wysna.toml --symbol quote_validation_code --rel impact
```

Agrega `--output-format=json-v1` para obtener el resultado `wysna.project-impact/1`.
En MCP, usa `wysna_graph` con `manifest`, `symbol` y `relation: "impact"`.

Cada entrada afectada incluye una cadena de relaciones hasta el símbolo.
En una biblioteca web, el análisis parte de sus funciones públicas.
Las llamadas desde JavaScript y el código C externo aparecen como fronteras
fuera del análisis estático. Para distinguir símbolos homónimos, usa su ID
cualificado.
