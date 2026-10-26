# Effect and capability reports

`wysna explain` is a read-only Wysna Seed command. It starts from a checked program,
lowers it through the same HIR/MIR boundary used by native builds, verifies the MIR,
and computes a conservative overapproximation of direct and transitive effects. It
does not interpret source, load FFI libraries, invoke a C/LLVM toolchain, or run the
program.

The native SDK and the Seed LLVM SDK expose a restricted command set and do not
provide `explain`.

## Command

```sh
wysna explain hello.wysna
wysna explain hello.wysna --function main
wysna explain hello.wysna --output-format=json-v1
wysna explain hello.wysna --manifest wysna.toml --frozen
```

`--function NAME` selects exactly one reported identity or display name. Text output
prints only that function among the function rows; JSON output adds `selected_function`
but still contains the complete report.

Releasing managed opaque values, dynamic classes or captured owners can invoke
host resource finalizers. When their behavior is unresolved the report includes
`resource.finalize` and `unknown`; a cleanup operation is not silently certified
pure. Explicit host close also includes possible filesystem writes from flushing.

With `--manifest`, the source must belong to the project source root. `--frozen`
requires and verifies the existing lockfile. The report then gains a `project` field
with the resolved lock data.

Exit status is `0` on success, `1` for source/semantic diagnostics, `2` for usage
errors, and `3` for filesystem failures. `--output-format=json-v1` emits one
tool-result document, including on errors.

## Model

The model is versioned as `wysna.effects-model/1`; reports use
`wysna.effect-report/1` and `analysis_scope: lowered-concrete-MIR`.

| Effect | Meaning |
| :--- | :--- |
| `memory.read`, `memory.write`, `memory.allocate`, `memory.retain`, `memory.release` | Runtime memory traffic |
| `failure.raise` | A checked runtime failure is possible |
| `stdio.write` | Standard output or error |
| `arguments.read`, `environment.read`, `clock.read` | Process arguments, environment, wall clock |
| `filesystem.read`, `filesystem.write` | Host filesystem access |
| `network.http` | HTTP/HTTPS access |
| `process.spawn`, `process.wait`, `process.exit` | Child processes and process termination |
| `resource.acquire`, `resource.inspect`, `resource.close` | Host resource handles |
| `host-state.read` | Host adapter error state |
| `global.read`, `global.write` | Access through a global MIR slot |
| `ffi.native` | Native FFI without a trusted effect contract |
| `unknown` | An operation or call target the model cannot resolve |

Only host-facing effects map to a capability name. Memory traffic, failures, resources
and host state remain effects but do not become capabilities.

| Effects | Capability |
| :--- | :--- |
| `stdio.write` | `stdio` |
| `arguments.read` | `arguments` |
| `environment.read` | `environment` |
| `clock.read` | `clock` |
| `filesystem.read` | `filesystem.read` |
| `filesystem.write` | `filesystem.write` |
| `network.http` | `network` |
| `process.spawn`, `process.wait` | `processes` |
| `process.exit` | `process-control` |
| `ffi.native` | `native-ffi` |
| `unknown` | `unknown` |

Each report contains a `program` summary for the entry point `wysna_program` and a
`functions` array. Every function row records `identity`, display `name`, `source`,
`direct_effects`, `calls`, `witnesses`, `unknown_calls`, `transitive_effects`,
`unresolved_functions`, `capabilities` and `complete_within_model`. A witness names
the block, operation, source location, selected effects and trust source.

`complete_within_model` is false when the function or any reachable callee contains an
unknown call. `unresolved_functions` lists those reachable functions. The fixed point
handles recursion and long call chains without depending on Python recursion depth.

## Trust

| Trust value | Meaning |
| :--- | :--- |
| `mir-model` | Effects assigned to a verified MIR operation |
| `runtime-contract` | Effects declared for a compiler runtime helper |
| `bundled-host-contract` | Effects of a bundled standard-library `extern` whose native name is in the host model |
| `unknown` | Indirect/virtual call, uncontrolled FFI, missing contract, or unknown operation |

An external declaration is trusted only when it comes from the bundled `stdlib`
directory and its native name has an explicit host contract. Matching by name alone
is not enough; all other FFI is reported as `ffi.native` plus `unknown`.

## Limitations

- The report is an overapproximation, not a purity proof or a capability sandbox. It
  does not enforce capabilities and is not a security or performance estimate.
- Unreachable branches may be included. Unknown calls propagate to every caller and
  prevent a completeness claim.
- Uninstantiated generic templates have no function summary. Instantiate them before
  analysis.
- Indirect and virtual calls are not resolved.
- Results depend on the explicit runtime and bundled-host contracts. A new runtime
  operation without a contract is reported as `unknown`.
- `wysna api` and `wysna capabilities` remain separate: `api` indexes symbols and
  `capabilities` describes the installation, while `explain` analyzes one program.

## JSON example

```json
{
  "schema_version": "wysna.effect-report/1",
  "compiler_version": "0.5.0",
  "analysis_scope": "lowered-concrete-MIR",
  "model": "wysna.effects-model/1",
  "entry_point": "wysna_program",
  "source_inputs": {"hello.wysna": "<sha256>"},
  "mode": "conservative-static-overapproximation",
  "program": {
    "transitive_effects": ["failure.raise", "memory.allocate", "stdio.write"],
    "capabilities": ["stdio"],
    "complete_within_model": true,
    "unresolved_functions": []
  },
  "functions": []
}
```
