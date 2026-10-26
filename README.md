# Wysna Programming Language

[![CI](https://github.com/WashingtonYandun/Wysna/actions/workflows/ci.yml/badge.svg?branch=develop)](https://github.com/WashingtonYandun/Wysna/actions/workflows/ci.yml)

<p align="center">
  <img src="assets/wawi.jpg" alt="Wysna language mascot" width="160">
</p>

Wysna is an experimental, statically typed, general-purpose language designed for AI agents to write, inspect, and validate programs with predictable semantics. Its current validated workflows include native CLI tools, scripting, C interoperability, a bounded local backend pilot, and shared native/browser logic through WebAssembly. Memory management uses deterministic reference counting (ARC) and copy-on-write (CoW); classes retain reference identity and errors use explicit typed channels.

The source version is `0.5.0`. Wysna Seed is the reference implementation and default compiler; Wysna Compiler is implemented in Wysna and ships as a [standalone native SDK](docs/NATIVE_SDK.md) for the declared `native-core-v1` profile. Both produce native executables through verified MIR and C99. Seed also provides an opt-in [experimental LLVM scalar/string backend](docs/LLVM_EXPERIMENTAL.md). Python is required by Seed and build-time bootstrap tools; installed native SDK commands run without Python or a separately installed C compiler.

`develop` carries the current integration line. Development artifacts are available from [successful CI runs](https://github.com/WashingtonYandun/Wysna/actions/workflows/ci.yml?query=branch%3Adevelop). Release promotion is separate: the existing published `v0.4.0a1-pilot.2` assets predate this SDK and use the earlier command/file naming. Do not use those older assets as installation instructions for the current `0.5.0` development line.

---

## Compiler Architecture and Naming

Wysna distinguishes the language from its two compiler implementations. Seed remains the default compiler and provides the broader project and editor tooling. The native SDK has an explicit source/command profile; neither self-hosting nor a green corpus establishes universal parity.

| Component | Public name | Internal identifier | Implementation language | Role |
| :--- | :--- | :---: | :--- | :--- |
| **Language** | **Wysna** | `wysna` | — | Specification and grammar (`.wysna`) |
| **Reference compiler** | **Wysna Seed** | `seed` | Python | Reference semantics, type interner, HIR/MIR, and default C99 pipeline |
| **Self-hosted compiler** | **Wysna Compiler** | `selfhost` | Wysna | Independent native compiler and SDK with the measured `native-core-v1` profile |
| **Bootstrap oracle** | **Bootstrap engine** | `bootstrap` | Seed AST C99 | Initial engine; its emission hash is recorded separately from the native fixed point |
| **Bootstrap stage 1** | **Stage 1** | `stage1` | Native C99 (native C0) | First self-hosted compiler binary (`compiler0.c` &rarr; `stage1`) |
| **Bootstrap stage 2** | **Stage 2** | `stage2` | Native C99 (native C1) | Compiler binary at the deterministic fixed point (`compiler1.c` &rarr; `stage2`) |

See [compiler responsibilities and enforced boundaries](docs/COMPILER_ARCHITECTURE.md)
for session ownership, semantic rule domains, target tooling and runtime headers.

### Verified compilation pipelines

Wysna Seed uses `SourceSnapshot → AST → CheckedProgram → typed HIR → verified MIR → C99`. Wysna Compiler uses its own frontend and `CheckedProgram → verified MIR → C99`. Its checked nodes own resolved bindings, types, patterns, and signatures; the emitter does not reparse source tokens or consult a live checker. The former parallel parser-to-C route has been removed.

`wysna emit-mir --implementation=seed|compiler` is a Seed tooling command for either producer. The native SDK exposes `wysna emit-mir` directly. Both use a versioned interchange contract with independent verifiers; see the [native profile and MIR contract](docs/MIR_DESIGN.md#interchange-and-native-core-v1).

| Stage | Component | Responsibility | Output representation |
| :--- | :--- | :--- | :--- |
| **Seed frontend** | Lexer and Pratt parser | UTF-8 scanning, operator precedence, and syntax diagnostics | Abstract syntax tree (`AST`) |
| **Seed semantics** | Type checker and scopes | Symbol resolution, nominal typing, and monomorphization | Checked program and canonical type arena |
| **Seed lowering** | HIR builder and verifier | Resolved operations and explicit semantic contracts | Typed high-level IR (`HIR`) |
| **Seed middle end** | Control-flow graph and MIR | Basic blocks, projections, ownership verification, and CoW operations | Verified middle-level IR (`MIR`) |
| **Seed backend** | C99 generator | MIR-to-C99 translation, ARC, and literal pooling | C99 translation unit (`.c`) |
| **Native compilation** | C toolchain (`clang` / `gcc`) | Compilation and linking against `wysna_runtime.h` and `wysna_host.h` | Standalone executable (`.exe` / ELF / Mach-O) |
| **Native pipeline** | Wysna Compiler | Checked nodes → verified MIR → C99; the SDK compiles and links with pinned Zig | Native self-hosted compiler and user executables |

The native profile includes scalars, UTF-8 strings, globals/imports, lists/dictionaries/tuples and CoW mutation, ranges, optional/Result/error-union channels, structs/enums, classes/inheritance/interfaces, guarded patterns, snapshot closures/function values, concrete generic functions/structs/enums, and the scalar/opaque C ABI.

Parameterized classes/interfaces, unresolved generic nominal values without a concrete context, native library exports, and native WASM targets are outside this SDK profile. Project/package commands, formatter, LSP, and structured agent-tooling commands remain in Seed. Seed's WASI and browser-library targets remain available.

Seed AST constructs the initial bootstrap engine. Three subsequent native MIR emissions must satisfy `C0 == C1 == C2`; the Seed AST emission is recorded separately. Cached C sources and both stage binaries are checked before reuse. This proves the recorded fixed point; behavioral parity is tested independently.

---

## Engine Parity and Conformance

The declared 203-case corpus is checked across four engines. The following snapshot is verified by [PR #114 CI](https://github.com/WashingtonYandun/Wysna/actions/runs/40253640492) on compiler commit `1db66ba`, with unchanged source inputs and zero unsupported exclusions. It is a measured corpus, not proof of every language or standard-library combination:

| Engine | Role | Implementation | Status |
| :--- | :--- | :--- | :--- |
| **`interpreter`** | Reference behavior | Direct AST interpretation in Python (Wysna Seed) | 203/203 (100.0%) |
| **`ast`** | Secondary oracle | Direct C99 generation from AST (Wysna Seed) | 203/203 (100.0%) |
| **`hir`** | Default Seed pipeline | Typed HIR &rarr; verified MIR &rarr; C99 backend (Wysna Seed) | 203/203 (100.0%) |
| **`selfhost`** | Self-hosted compiler | Native Wysna Compiler executable (`stage2`) | 203/203 (100.0%) |

See [docs/ENGINE_PARITY_MATRIX.md](docs/ENGINE_PARITY_MATRIX.md) for details and function categories.

---

## Quick Start

### Native SDK: Linux, macOS, and Windows

From a successful CI run for this development line, download the matching `native-sdk-ubuntu-latest`, `native-sdk-macos-latest`, or `native-sdk-windows-latest` artifact. Extract the SDK archive inside the downloaded artifact, preserving its directory layout. CI validates Linux x64, macOS arm64, and Windows x64 independently.

On macOS/Linux, invoke `bin/wysna` or add its directory to your terminal's `PATH`. On Windows, open `open-terminal.cmd` or invoke `bin\wysna.exe`; the launcher changes `PATH` only for that terminal session. Inside the SDK directory on macOS/Linux:

```sh
./bin/wysna --version
```

Create `hello.wysna`:

```wysna
print("Hello from Wysna!")
```

With the SDK's `bin` directory on `PATH`:

```sh
wysna check hello.wysna
wysna emit-mir hello.wysna -o hello.mir.json
wysna emit-c hello.wysna -o hello.c
wysna build hello.wysna -o hello --profile release
wysna run hello.wysna -- "two words"
```

Windows build outputs receive an `.exe` suffix. Profiles `dev`, `release`, and `size` select `-O0 -g`, `-O2`, and `-Os`, retaining runtime checks in every profile. The SDK bundles Zig 0.15.2, runtime headers, standard library, licenses, checksums, and bootstrap provenance. No Python runtime is packaged. See [Native SDK](docs/NATIVE_SDK.md) for installation, updates, caches, deadlines, external libraries, and profile boundaries.

### Wysna Seed: Source checkout and full tooling

Requirements: Python 3.10 or newer. Native builds additionally need a C99 compiler such as `clang` or `gcc`; interpretation, checking, formatting, and LSP do not require native compilation. Seed has no mandatory third-party Python packages.

```sh
git clone --branch develop https://github.com/WashingtonYandun/Wysna.git
cd Wysna
./wysna --version
./wysna run examples/01_hello_world.wysna
./wysna build examples/01_hello_world.wysna -o build/hello
./build/hello
```

On Windows, run the source CLI with `py -3 main.py` instead of `./wysna`. The separate `wysna-windows-x64-installer` CI artifact packages the Seed installer with private Python and a C toolchain, including install/repair/uninstall checks. It is a different distribution from the Python-free native SDK. Current development artifacts should not be confused with older binaries on the [Releases page](https://github.com/WashingtonYandun/Wysna/releases).

### Dependency boundaries

- **Native SDK users:** bundled Wysna Compiler, Zig 0.15.2, runtime, and stdlib. No host Python or C compiler is required for the offline core profile.
- **Seed/source contributors:** Python >=3.10 and a host C99 toolchain for native builds and bootstrap. Building the SDK also downloads and verifies the pinned toolchain; the installed SDK works offline.
- **HTTP/HTTPS:** libcurl >=8.3.0 and development/link inputs for native programs. Seed interpretation additionally uses `wysna[http]` (`pycurl>=7.48,<8`). Libcurl is not bundled in the native core SDK.
- **Backend pilot:** `wysna[service]` (`aiohttp>=3.14.5,<4`) for the Python supervisor. The SQLite examples also need SQLite headers, `libsqlite3`, and their explicit C adapter.
- **WASI through Seed:** a `wasi-sdk` toolchain (CI uses version 34) and `wysna[wasi]` (`wasmtime==50.0.1`) for execution.
- **Browser libraries through Seed:** Emscripten 5.0.1 and a JavaScript/browser host for the generated module.
- **Project dependencies:** explicit local paths and a hash-checked lockfile; a remote package registry is not implemented.

External libraries remain explicit. For example, the SDK uses `--include-dir`, `--link`, and `--library`; Seed's build tooling uses its own documented link options. A native executable still depends on its target OS/ABI and any external libraries it links.

---

## Language Highlights

### 1. Error Unions & Explicit Handling (`T ! E`)

Errors are first-class values and must be handled explicitly with `else` blocks:

```wysna
import "std/parse"

fn compute_ratio(raw: str, divisor: int) -> int ! str {
    let value = parse.parse_int(raw) else error { return Err("Invalid number format") }
    if divisor == 0 { return Err("Division by zero") }
    return value / divisor
}

fn show_ratio() {
    let result = compute_ratio("42", 2) else error { print(error) return }
    print("Ratio:", result)
}
show_ratio()
```

### 2. Algebraic Data Types & Pattern Matching

```wysna
enum Status { Active, Suspended(str), Terminated(int, str) }

fn describe(status: Status) -> str {
    return match status {
        Status.Active => "Account is currently active",
        Status.Suspended(reason) => "Suspended: " + reason,
        Status.Terminated(code, message) => "Terminated (" + str(code) + "): " + message
    }
}
print(describe(Status.Active))
```

### 3. Copy-on-Write (CoW) & Memory Management

Collections (lists, dictionaries, and structs) provide value semantics with zero-copy snapshot efficiency:

```wysna
let original = [1, 2, 3, 4]
let mut copy = original   # Shares storage until mutation
copy.append(5)
print(original)           # [1, 2, 3, 4]
print(copy)               # [1, 2, 3, 4, 5]
```

### 4. Bounded Binary I/O & Scoped Resources

```wysna
import "std/files"

fn inspect_file(path: str) -> unit ! files.FileError {
    let file = files.open(path, 0)?  # 0 read, 1 truncate/write, 2 append
    let chunk = file.read(4096)?
    print("Read bytes:", chunk.size())
    # File and Bytes release their host storage with their last owner.
    # Use close() explicitly when its status matters.
}
inspect_file("README.md") else error { print(error.operation, error.code) }
```

---

## Standard Library Modules

Wysna provides a focused standard library:

| Module | Description | Key Functions & Types |
| :--- | :--- | :--- |
| **`std/buffer`** | Fast bounded byte storage | `Bytes`, `size()`, `get()`, `append()`, `close()` |
| **`std/files`** | Streaming file I/O | `File`, `open()`, `read()`, `write()`, `is_open()`, `close()` |
| **`std/fs`** | Portable filesystem operations | `exists()`, `size()`, `mkdir()`, `remove()`, `rename()` |
| **`std/parse`** | Strict string parsing | `parse_int()`, `parse_float()` |
| **`std/json`** | JSON parsing and deterministic serialization | `parse()`, `stringify()`, typed `Value` constructors and accessors |
| **`std/text`** | String transformation utilities | `split()`, `join()`, `replace()`, `trim()`, `find()` |
| **`std/path`** | Cross-platform path handling | `join()`, `dirname()`, `basename()` |
| **`std/resources`** | Deterministic resource scopes | `Resource`, `acquire()`, `scope()`, `borrow()`, `close()` |
| **`std/algorithms`** | Functional primitives | `map`, `fold`, `sort` |
| **`std/environment`** | Program arguments and environment lookup | `arguments()`, `get()` |
| **`std/time`** | Host wall-clock access | `unix_seconds()` |
| **`std/process`** | Direct child-process execution with a timeout | `run(executable, arguments, timeout_ms)` |
| **`std/http`** | Synchronous bounded HTTP/HTTPS requests with verified TLS | `request()`, `get()`, `post_json()`, `response_json()` |
| **`wysna service serve`** | Experimental loopback JSON server with isolated native workers | Bounded workers and queue, input/output limits, SQLite pilot |
| **`std/test`** | Minimal project-test assertions | `expect()`, `expect_equal_int()`, `expect_equal_text()` |

See [docs/STDLIB.md](docs/STDLIB.md) for full API reference and examples.
The HTTP example is [examples/http_json_client.wysna](examples/http_json_client.wysna).

The local service pilot is experimental and listens on loopback only. Install its optional
host adapter from the checkout with `python3 -m pip install '.[service]'`. Build the SQLite worker from a source
checkout and start it with:

```sh
./wysna build examples/service/sqlite_worker.wysna -o build/sqlite_worker \
  --link-lib examples/service/sqlite_adapter.c --link-lib sqlite3
./wysna service serve --worker build/sqlite_worker --database build/measurements.sqlite
```

The worker accepts one JSON request envelope from a unique temporary file and writes one
JSON response to stdout. The supervisor starts a fresh native process per request, caps
active workers, queued requests, input, output, and execution time, and discards a process
after any failure. The sample exposes `GET /health`, `GET /measurements`, and
`POST /measurements` with `{"id": 1, "value": 42}`. It is a local development pilot:
it has no TLS or authentication, does not sandbox worker filesystem access, and has no
supported network deployment mode.
See [docs/SERVICE_FOUNDATIONS.md](docs/SERVICE_FOUNDATIONS.md) for the worker contract and
resource limits.

To capture a repeatable local load and resident-memory report, install the service extra
and run the campaign from a source checkout:

```sh
python3 -m pip install '.[service]'
python3 tools/measure_service.py --warmup-seconds 60 --duration-seconds 600 \
  --repetitions 3 --clients 4 --workers 4 \
  --report .wysna_cache/service-load/report.json
```

The campaign builds the native example worker, seeds a deterministic 1,000-row SQLite
dataset, alternates read-only health and count requests, and reports throughput, raw
latency samples and percentiles, saturation responses, one-second process-tree RSS
samples, and shutdown cleanup. It checks a
provisional RSS growth budget of the larger of 5% or 8 MiB between steady-load windows.
Pull request CI runs three repetitions on Ubuntu. This fixed workload does not define a
production SLO or qualify a network deployment.

The [shared browser app example](examples/shared-browser-app/README.md) calls the same
Wysna business-logic module from a native CLI and a WebAssembly browser interface.

---

## CLI & Tooling

The following commands describe **Wysna Seed**. The standalone native SDK exposes the five source commands shown above; it does not provide the project, formatter, LSP, service supervisor, or introspection commands below.

```text
wysna run <file.wysna> [-- args...]    # Execute with program arguments
wysna run --report-json <path> [--invocation-id <id>] <file.wysna> [-- args...]
wysna check <file.wysna>               # Parse and type-check without code execution
wysna check --output-format=json-v1 <file.wysna>  # Emit a versioned machine-readable result
wysna fmt --output-format=json-v1 <file.wysna>    # Return formatted source as a JSON artifact
wysna build <file.wysna> -o <bin>      # Compile to native standalone executable
wysna emit-c <file.wysna> -o <out.c>   # Generate human-readable portable C99 code
wysna fmt <file.wysna>                 # Format source while preserving comments and layout
wysna lsp                              # Launch Language Server Protocol daemon (stdio)
wysna test                             # Run full test suite across all 4 execution engines
wysna bench                            # Run self-hosted performance benchmarks
wysna project check                    # Check the nearest wysna.toml project
wysna project build -o build/app       # Build a declared project target
wysna project run -- <args...>         # Run the declared project entry
wysna project test --filter argv       # Run declared user test entries
wysna project lock                    # Write or refresh wysna.lock explicitly
wysna api std/json --output-format=json-v1  # Inspect standard-library symbols
wysna capabilities --output-format=json-v1  # Inspect this installation's support
```

Project commands use a strict `wysna.toml` manifest. Dependencies are local paths,
`project lock` is the only command that writes `wysna.lock`, and `--frozen` checks
the manifest and dependency hashes without downloading packages. See
[`examples/automation`](examples/automation) for a command runner that passes
arguments directly to a child executable without invoking a shell.

Project manifests also support a bounded `wasi-command` profile that builds
`wasm32-wasip1` modules and runs them in isolated Wasmtime instances. Filesystem access
requires explicit manifest preopens; child processes, networking, and native C FFI
are not available to this profile. See [Wysna Projects](docs/PROJECTS.md) for SDK
installation, limits, and manifest configuration.

The `browser-library` profile builds an importable JavaScript facade and WebAssembly
module with Emscripten 5.0.1. Its generated ABI exposes only top-level, non-fallible
`pub fn` operations using `int`, `float`, `bool`, `str`, and `unit`; `int` maps to JavaScript
`BigInt`, and strings cross as owned UTF-8 buffers. `WVal`, runtime pointers, host
filesystem, networking, and process APIs stay outside this browser interface.

`check`, `fmt`, `emit-c`, and `build` also accept `--output-format=json-v1`. The
formatter returns inline source content; code and binary generation report artifact
paths and SHA-256 hashes. The default text output and legacy `--json` format remain
unchanged when the option is omitted.

Diagnostic `primary_span` values identify an exact source range in the UTF-8 snapshot
named by `source_sha256`. Offsets are zero-based bytes with an exclusive end; locations
without reliable source attribution are returned as `null`.

`run --report-json <path>` writes its versioned control result to a sidecar file and
leaves the program's stdout and stderr untouched. Wysna atomically writes a `pending`
result before execution, then replaces it with the final result. If the pending result
cannot be written, the program is not started. Supply `--invocation-id <id>` when a
caller needs to match the report to a specific request; otherwise Wysna generates one.
The report distinguishes the tool result from the application's exit code.

Arguments after `--` are available through `args()`. The interpreter uses `"wysna"` as
`args()[0]`; a native executable receives the operating system's argument vector.

---

## Validation and maturity

The verified compiler snapshot in PR #114 passed 796/796 full conformance checks and 7/7 examples on Linux/Clang, Linux/GCC, and macOS. Separate gates cover the 812-row four-engine corpus, differential tests, deterministic fuzzing, invalid checked/MIR rejection, ASan/UBSan, CoW allocation/scaling, installed wheels, Windows installer lifecycle, and native SDK acceptance outside the checkout. Installed SDK acceptance includes compiler selfbuild without Python on all three validated platforms.

The service pilot passed three sustained ten-minute load/RSS repetitions with saturation checks. This is a bounded loopback workload, not a production deployment qualification or a general performance comparison. Wysna remains experimental. Seed offers a separate `llvm-scalar-v1` experiment with LLVM 20.1.8; broader LLVM coverage, a mobile platform, a public package registry, and full native tooling parity remain future work. Default compilation still goes through C99, including when Zig supplies the native SDK toolchain.

## Documentation

- [Experimental LLVM](docs/LLVM_EXPERIMENTAL.md): opt-in Seed profile, tools, semantics, debug symbols, and limits.
- [Native SDK](docs/NATIVE_SDK.md): Python-free installation, commands, dependency boundaries, and provenance.

- [Language specification](SPEC.md): syntax, expressions, and semantics.
- [Type system design](docs/TYPE_SYSTEM_DESIGN.md): nominal checking, inference, and monomorphization.
- [HIR representation](docs/HIR_DESIGN.md) & [MIR representation](docs/MIR_DESIGN.md): intermediate forms and contracts.
- [Ownership & lifetimes](docs/OWNERSHIP_LIFETIME.md): ARC, copy-on-write, weak references, and drop elaboration.
- [Standard library reference](docs/STDLIB.md): complete API documentation for all standard modules.
- [Project workflows](docs/PROJECTS.md): manifests, local dependencies, lockfiles, project commands, and user tests.
- [Engine parity matrix](docs/ENGINE_PARITY_MATRIX.md): 4-engine test results and coverage.
- [FFI & C interoperability](docs/FFI_VALIDATION.md): calling C functions and descriptor ABI.
- [Known limits & boundaries](docs/KNOWN_LIMITS.md): current alpha constraints and guidelines.
- [Contributing guide](CONTRIBUTING.md) & [Security policy](SECURITY.md).

---

## Repository Layout

```text
├── checker/       # Semantic analysis, type checking, and monomorphization
├── codegen/       # C99 code generation and literal table emission
├── common/        # Shared platform adapters, locks, and version metadata
├── docs/          # Architecture RFCs, specifications, and design reports
├── error/         # Structured compiler diagnostic codes (W1000-W5000)
├── examples/      # Canonical code examples and language tours
├── formatter/     # Comment-preserving AST code formatter
├── hir/           # High-Level IR data structures and lowering
├── lexer/         # UTF-8 scanner, token definitions, and string escapes
├── mir/           # Mid-Level IR, CFG, place projections, and drop elaboration
├── node/          # AST node definitions
├── packaging/     # Native SDK and reference Windows distribution
├── parser/        # Pratt parser and grammar rules
├── runtime/       # C99 runtime headers (wysna_runtime.h, wysna_host.h)
├── selfhost/      # Self-hosted compiler source code (compiler.wysna)
├── stdlib/        # Standard library implementations
├── tests/         # Differential testing harness and conformance suites
├── tooling/       # Language server (LSP) and developer utilities
└── tools/         # Validation, benchmarking, and packaging scripts
```

---

## License

Wysna is distributed under the terms of the [MIT License](LICENSE).
