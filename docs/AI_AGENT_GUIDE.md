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

## Handle diagnostics from their source snapshot

Prefer `--output-format=json-v1` for `check` and `project check`. Read each
diagnostic's code, stage and message. If `primary_span` is present, its offsets
`start_byte` and `end_byte` are UTF-8 byte offsets into the source identified by
`source_id`; verify `source_sha256` before editing that snapshot. Do not treat byte
offsets as Unicode character indexes, and do not guess a location when
`primary_span` is absent.
Re-run the check after editing and require a successful result before building.

## Respect declared limits

- Use `native-cli` for host executables, `wasi-command` for constrained WASI
  commands, and `browser-library` for the generated browser ABI. The capability
  response states the toolchain and runtime each one needs.
- The native SDK currently implements `native-core-v1` source checking, MIR/C99
  emission, native build and run. Do not assume Seed project commands, WASI or
  browser targets are available through that SDK.
- Effect reports and capability declarations help review programs; they are not
  operating-system sandboxes.
- The language is designed for predictable human and agent workflows, but Wysna
  has not been measured against other languages for AI-authored program quality.

See [project workflows](PROJECTS.md), [native SDK](NATIVE_SDK.md),
[effect contracts](EFFECTS.md), and [Seed distribution](SEED_DISTRIBUTION.md) for
the complete interfaces and limitations.
