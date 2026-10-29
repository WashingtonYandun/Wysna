# Product scope and maintenance policy

Wysna is an experimental language for predictable automation written by people and
AI agents. CLI tools and scripting are the first product workflow; general-purpose
use remains the direction. Version 0.5.0 is published as a pre-1.0 release; it is
not a security guarantee, and language contracts may evolve before 1.0.

## One recommended installation

The primary installation path is the complete Seed distribution, with private
CPython and Zig, installed through the graphical installer for the announced host.
Users must not need to select a compiler implementation or install Python, pip or
a C compiler to use the installed core tools. Source and silent command-line flows
remain available for contributors and agents. See [distribution status](SEED_DISTRIBUTION.md).

Installer artifacts are qualified per source commit. A package must pass installation, repair/update,
relocation where applicable, real program execution and uninstallation acceptance
on its OS before being announced as available. Signing and notarization are
separate recorded properties, not consequences of a checksum.

## Primary maintained workflow

- Seed parsing, checking, canonical types, verified HIR/MIR, C99 emission and runtime.
- Native CLI builds, scripting, project manifests/local lockfiles, formatter, LSP,
  structured diagnostics, API discovery and conservative effect reports.
- The language semantics common to native and the declared WASI profile. Host APIs
  have explicit target availability; portability does not mean ambient access to
  every host service on every target.
- Installed behavior and release provenance on Windows x64, macOS arm64 and Linux
  x64 for the supported package families.

The interpreter and AST C99 path remain independent verification oracles. They are
not separate products that an ordinary user must choose between.

## Experimental maintenance boundaries

- LLVM remains an explicit opt-in backend. Complete the already-authorized measured
  corrections, then prioritize correctness and the declared profile over expansion.
  No promotion to default follows from keeping its code.
- Wysna Compiler and its native SDK remain a bounded self-hosting implementation
  and developer distribution. They do not replace Seed's complete installed tools
  or imply universal parity.
- Browser libraries and the local service pilot retain their explicit contracts;
  they are not headline promises of mobile support or a public server platform.
- Experimental SDKs are not alternative recommendations on the main installation
  screen. Their documentation must state support, dependencies and limitations.

Do not remove a backend, oracle, target or test suite by filename or line count.
Retirement needs a replacement/compatibility decision, an inventory of unique
contracts, verified history and a migration. No such deletion is authorized by this
scope document.

## Security boundary

Native execution has the user's OS authority. Process separation alone does not
restrict filesystem access, networking or arbitrary native FFI. The service pilot
does not make untrusted native code safe.

WASI has a Wasmtime host boundary, declared preopens, fuel and linear-memory limits.
The current host's complete permissions must be inspected; a fuel budget is not
five seconds of CPU, and a linear-memory limit is not total process RSS.

`explain` reports conservative effects and unknowns. It does not prove purity,
sandbox execution or provide a complete audit trail. `wysna run --allow=...` and
`--deny=...` add a static pre-execution gate over the same report for interpreted
Seed runs. The gate refuses incomplete analyses, so indirect or uncontrolled FFI
cannot silently pass; it does not apply to native builds or `wysna project run`,
and it is not an operating-system sandbox.

A fully restricted execution profile still requires enforcement at every host
boundary, negative escape tests, bounded resource controls and audit semantics
before it can be advertised. Do not describe current Wysna as a secure runtime for
arbitrary untrusted code, or add a public code-execution endpoint on that assumption.

## CI and compatibility

Keep semantic/compiler/runtime regressions and installer acceptance on the relevant
change path. Run targeted experimental checks when their implementation or shared
dependencies change. Expensive complete campaigns also belong in release/manual or
scheduled qualification; moving a check must not create a green path for an affected
feature that was not tested.

Measure CI critical-path duration, actual matrix executions and runner usage rather
than imposing an arbitrary count of job definitions. Consolidating milestone-named
tests requires preserving their unique cases and assertions in feature tests.
Do not discard a regression merely because its original audit is complete.

Mutable captures, broader error unions, new memory/ABI models, concurrency, a public
registry and additional targets require separate design and acceptance. They are not
incidental changes to an installer or effect-report release.
