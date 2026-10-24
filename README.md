# Wysna Programming Language

[![CI](https://github.com/WashingtonYandun/Wysna/actions/workflows/ci.yml/badge.svg)](https://github.com/WashingtonYandun/Wysna/actions/workflows/ci.yml)

<p align="center">
  <img src="assets/wawi.jpg" alt="Wysna language mascot" width="160">
</p>

Wysna is an experimental statically typed language for native programming, CLI tools, and high-performance applications. Wysna Seed compiles to portable C99 and combines deterministic reference counting (ARC), copy-on-write (CoW) value semantics, explicit error unions, and controlled C interoperability.

This branch prepares Wysna `0.5.0`. Wysna Seed is the reference compiler and primary production implementation. Wysna Compiler is a modular self-hosted compiler for bootstrap; its supported subset is measured by the four-engine matrix.

---

## Compiler Architecture and Naming

Wysna distinguishes the language from its two compiler implementations. The self-hosted compiler is not yet the default production pipeline.

| Component | Public name | Internal identifier | Implementation language | Role |
| :--- | :--- | :---: | :--- | :--- |
| **Language** | **Wysna** | `wysna` | — | Specification and grammar (`.wysna`) |
| **Reference compiler** | **Wysna Seed** | `seed` | Python | Reference semantics, type interner, HIR/MIR, and default C99 pipeline |
| **Self-hosted compiler** | **Wysna Compiler** | `selfhost` | Wysna | Independent bootstrap compiler with a narrower, measured feature profile |
| **Bootstrap stage 1** | **Stage 1** | `stage1` | Native C99 (compiled by Seed) | First self-hosted compiler binary (`compiler0.c` &rarr; `stage1`) |
| **Bootstrap stage 2** | **Stage 2** | `stage2` | Native C99 (recompiled by Stage 1) | Compiler binary at the deterministic fixed point (`compiler1.c` &rarr; `stage2`) |

See [compiler responsibilities and enforced boundaries](docs/COMPILER_ARCHITECTURE.md)
for session ownership, semantic rule domains, target tooling and runtime headers.

### Wysna Seed Compiler Pipeline

Wysna Seed uses multiple stages for type analysis and portable native code generation. Wysna Compiler owns compilation state per invocation and has a direct C99 route plus an experimental MIR producer over checked nodes, with scalar, string and basic result support. `wysna emit-mir --implementation=seed|compiler` exports versioned IR; see the [supported profile](docs/MIR_DESIGN.md#interchange-and-the-compiler-profile).

| Stage | Component | Responsibility | Output representation |
| :--- | :--- | :--- | :--- |
| **Seed frontend** | Lexer and Pratt parser | UTF-8 scanning, operator precedence, and syntax diagnostics | Abstract syntax tree (`AST`) |
| **Seed semantics** | Type checker and scopes | Symbol resolution, nominal typing, and monomorphization | Typed high-level IR (`HIR`) |
| **Seed middle end** | Control-flow graph and MIR | Basic blocks, projections, ownership verification, and CoW operations | Verified middle-level IR (`MIR`) |
| **Seed backend** | C99 generator | MIR-to-C99 translation, ARC, and literal pooling | C99 translation unit (`.c`) |
| **Native compilation** | C toolchain (`clang` / `gcc`) | Compilation and linking against `wysna_runtime.h` and `wysna_host.h` | Standalone executable (`.exe` / ELF / Mach-O) |
| **Self-hosting** | Wysna Compiler | Direct C99 emission; the fixed point validates deterministic output, not IR parity | Native self-hosted binary (`stage2`) |

---

## Engine Parity and Conformance

Every core and standard-library function is continuously checked across four engines:

| Engine | Role | Implementation | Status |
| :--- | :--- | :--- | :--- |
| **`interpreter`** | Reference behavior | Direct AST interpretation in Python (Wysna Seed) | 203/203 (100.0%) |
| **`ast`** | Secondary oracle | Direct C99 generation from AST (Wysna Seed) | 203/203 (100.0%) |
| **`hir`** | Primary production pipeline | Typed HIR &rarr; verified MIR &rarr; C99 backend (Wysna Seed) | 203/203 (100.0%) |
| **`selfhost`** | Self-hosted compiler | Native Wysna Compiler executable (`stage2`) | 203/203 (100.0%) |

See [docs/ENGINE_PARITY_MATRIX.md](docs/ENGINE_PARITY_MATRIX.md) for details and function categories.

---

## Quick Start

### Windows: Standalone Installer

Download `wysna-<version>-windows-x64-setup.exe` from [Wysna Releases](https://github.com/WashingtonYandun/Wysna/releases):

1. Run the `-setup.exe` installer.
2. Keep the default option to add Wysna to your system `PATH`.
3. Open a new terminal (PowerShell or CMD) and verify the installation:

```powershell
wysna version
```

Create a simple `hello.wysna` file:

```wysna
print("Hello from Wysna!")
```

Run directly via the interpreter or compile to a native standalone executable:

```powershell
wysna run hello.wysna
wysna build hello.wysna -o hello.exe
.\hello.exe
```

The standalone installer bundles a private Python runtime and a portable C toolchain: it works fully offline, requires no administrator privileges, and has no dependency on Visual Studio or MSVC build tools.

### macOS & Linux: Source Checkout

Requirements: Python 3.10 or higher and a C99 compiler (`clang` or `gcc`).

```sh
git clone https://github.com/WashingtonYandun/Wysna.git
cd Wysna
./wysna --version
./wysna run examples/01_hello_world.wysna
./wysna test
```

Compiling to a native executable:

```sh
./wysna build examples/01_hello_world.wysna -o build/hello
./build/hello
```

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

Wysna provides a focused, high-performance standard library:

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
host adapter with `pip install 'wysna[service]'`. Build the SQLite worker from a source
checkout and start it with:

```sh
wysna build examples/service/sqlite_worker.wysna -o build/sqlite_worker \
  --link-lib examples/service/sqlite_adapter.c --link-lib sqlite3
wysna service serve --worker build/sqlite_worker --database build/measurements.sqlite
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

## Documentation

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
