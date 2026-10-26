# Wysna

Predictable automation, written by people and AI agents.

Wysna is an experimental, statically typed programming language for CLI tools,
scripting and native programs. It combines explicit error channels, deterministic
reference counting and copy-on-write values with tools that inspect and validate
programs before execution.

The current source version is **0.5.0 development**. CI produces installer candidates
and qualification reports for each source commit. The published `v0.4.0a1-pilot.2` prerelease is older
and does not contain the current toolchain. See [scope and status](docs/SCOPE.md).

## Install

The recommended product is the **complete Seed distribution**. Its installers
include private Python and Zig; ordinary core use needs no host Python, pip,
virtual environment, Visual Studio or separately installed C compiler.

- **Windows x64:** open the matching `.exe` installer and follow the wizard.
- **macOS arm64:** open the matching `.pkg` with the system Installer.
- **Linux x64:** open the matching `.deb` or `.rpm` in the package manager for your
  distribution. These are separate package families, not a universal Linux image.

Download only an artifact whose version, source commit and acceptance report match.
Current installer availability and dependencies are recorded in
[installation documentation](docs/SEED_DISTRIBUTION.md). Command-line and silent
installation remain optional automation paths. Source contributors can install
with Python 3.10+; native source builds also require a C99 toolchain.

## Write and run

Save this as `hello.wysna`:

```wysna
import "std/json" as json
let value = json.number("42") else error { json.null() }
let output = json.stringify(json.object({"answer": value})) else error { "{}" }
print(output)
```

Use your editor or terminal:

```sh
wysna check hello.wysna
wysna run hello.wysna
wysna build hello.wysna -o hello
wysna explain hello.wysna --output-format=json-v1
```

`run` executes source through the reference runtime; `build` produces a native
executable through verified MIR and C99. Windows build outputs use `.exe`.
Arguments after `--` belong to the program. The installed distribution also offers
`wysna doctor` to check its files.

## Build useful tools

The standard library covers JSON, files and bounded byte buffers, text/path
operations, environment, processes, time, resources and testing. Native HTTP uses
libcurl with TLS verification, deadlines and response limits. External C libraries
and development inputs remain explicit dependencies.

Projects use `wysna.toml`, contained local dependencies and hash-checked lockfiles.
Single-file programs remain available. There is no public package registry.

- [Projects and tests](docs/PROJECTS.md)
- [Standard library](docs/STDLIB.md)
- [Executable language examples](examples/language)
- [C interoperability](docs/FFI_VALIDATION.md)
- [Installed JSON/HTTP/SQLite application](examples/automation-sync/README.md)

## Tools for editors and agents

The primary tools are `check`, `run`, `build`, `fmt`, `explain`, `project`, `api`,
`capabilities` and `lsp`. Structured commands use a versioned `json-v1` envelope;
run reports are sidecars that leave application output intact.

`explain` reports conservative local/transitive effects, capabilities, source
locations and unknown calls without executing source or loading external libraries.
It is **not** a sandbox, permission enforcer, purity proof or security guarantee.
See [effect-report contracts](docs/EFFECTS.md).

## Portability and security

The main workflow is trusted native automation. Native programs and the reference
runtime have the user's OS authority. Process isolation alone does not restrict
filesystem or network access.

The declared WASI command profile has a Wasmtime host boundary, fuel, linear-memory
limits and explicit directory preopens. Host APIs have target-specific availability.
A restricted capability policy and complete audit trail are not implemented merely
because effects can be reported.

The language uses snapshot closures and single-threaded ARC/CoW. Strong cycles are
not collected automatically. See [known limits](docs/KNOWN_LIMITS.md) and
[ownership](docs/OWNERSHIP_LIFETIME.md) before relying on lifetime behavior.

## Development and experiments

The reference implementation, independent execution oracles and self-hosted
compiler have separate verification roles. LLVM, the bounded native Compiler SDK,
browser libraries and the local service pilot retain explicit experimental scopes;
ordinary installation does not require choosing between them.

Regression tests, independent verifiers, bootstrap, sanitizers and installed-package
acceptance provide evidence for their declared inputs. Green tests are not universal
parity, production qualification or proof of AI-authoring superiority. No performance
improvement is promised merely by selecting LLVM.

- [Documentation index](docs/README.md)
- [Architecture](docs/COMPILER_ARCHITECTURE.md)
- [Language specification](SPEC.md)
- [Measured engine coverage](docs/ENGINE_PARITY_MATRIX.md)
- [Changes](CHANGELOG.md)
- [License](LICENSE)
