# Wysna Workflow for AI Agents

Use the installed Seed CLI as the project workflow. It provides deterministic
project checks, builds, tests, API discovery and versioned machine output. The
native `native-core-v1` SDK is a separate file-oriented compiler profile; it does
not provide Seed's project manifest, lockfile, test runner, formatter, LSP or
WebAssembly targets.

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

## Respect declared limits

- Use `native-cli` for host executables, `wasi-command` for constrained WASI
  commands, and `browser-library` for the generated browser ABI. The capability
  response states the toolchain and runtime each one needs.
- The native SDK currently implements `native-core-v1` source checking, MIR/C99
  emission, native build and run. Do not assume Seed project commands, WASI or
  browser targets are available through that SDK.
- Effect reports and capability declarations help review programs; they are not
  operating-system sandboxes.
- AI authoring and self-healing repair benchmarks live in `bench/ai_eval/`
  (`harness.py` and `token_audit.py`).

See [project workflows](PROJECTS.md), [native SDK](NATIVE_SDK.md),
[effect contracts](EFFECTS.md), and [Seed distribution](SEED_DISTRIBUTION.md) for
the complete interfaces and limitations.
