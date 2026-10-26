<p align="center">
  <img src="assets/wawi.jpg" alt="Wysna mascot" width="140" />
</p>

<h1 align="center">Wysna</h1>

<p align="center">
  <strong>Predictable automation, written by people and AI agents.</strong>
</p>

<p align="center">
  <a href="https://github.com/WashingtonYandun/Wysna/actions/workflows/ci.yml">
    <img src="https://github.com/WashingtonYandun/Wysna/actions/workflows/ci.yml/badge.svg?branch=develop" alt="CI status on develop" />
  </a>
  <a href="https://github.com/WashingtonYandun/Wysna/tree/v0.5.0-rc.1">
    <img src="https://img.shields.io/badge/version-0.5.0--rc.1-blue?style=flat-square" alt="Version 0.5.0-rc.1" />
  </a>
  <a href="LICENSE">
    <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" alt="MIT license" />
  </a>
  <img src="https://img.shields.io/badge/platforms-Windows%20x64%20%7C%20macOS%20arm64%20%7C%20Linux%20x64-lightgrey?style=flat-square" alt="Windows x64, macOS arm64 and Linux x64" />
</p>

<p align="center">
  <a href="#why-wysna">Why Wysna</a> ·
  <a href="#quick-start">Quick start</a> ·
  <a href="#language-tour">Language tour</a> ·
  <a href="#toolchain">Toolchain</a> ·
  <a href="#status-and-scope">Status</a> ·
  <a href="#documentation">Docs</a>
</p>

---

Wysna is an experimental, statically typed language for **CLI tools, scripting and
native programs**. It combines explicit error channels, deterministic reference
counting and copy-on-write values with tools that inspect and validate programs
before they run.

> [!WARNING]
> **0.5.0 is a release candidate, not a production guarantee.** Installer artifacts
> are unsigned development candidates until a matching release is published. The
> previous public prerelease, `v0.4.0a1-pilot.2`, predates the current toolchain.
> Read [scope and status](docs/SCOPE.md) before relying on Wysna for critical work.

## Why Wysna

| Principle | What it gives you |
| :--- | :--- |
| **Errors are values** | `T ! E` puts failure in the signature. `else error`, `?` and exhaustive `match` make every failure path explicit; there is no hidden exception-based control flow. |
| **Memory you can reason about** | Single-threaded ARC and copy-on-write provide deterministic cleanup and cheap snapshots. Strong cycles are not collected automatically; `Weak` is the escape hatch. |
| **A verified pipeline** | Source → typed HIR or checked nodes → verified MIR → C99. Independent oracles, a self-hosted compiler and conformance tests check the same language from different angles. |
| **One install for the core** | The Seed distribution carries private CPython and Zig, so core CLI, projects, formatter and LSP work without host Python, a virtual environment or a C SDK. |
| **Tools for humans and agents** | Scriptable commands and versioned `json-v1` envelopes let editors, scripts and agents inspect programs before execution. |
| **Native first, portable by design** | Native C99 is the default. WASI is a declared target; browser libraries and LLVM are explicit, bounded experiments. |

## Quick start

### Install the Seed distribution

The recommended installation is the **complete Seed distribution**. CI builds
installer candidates for each source commit; download only an artifact whose version,
source commit and acceptance report match.

| Host | Package | Launcher |
| :--- | :--- | :--- |
| Windows x64 | `.exe` (Inno Setup) | `wysna.exe` or `open-terminal.cmd` |
| macOS arm64 | `.pkg` | `/usr/local/bin/wysna` |
| Linux x64 | `.deb` / `.rpm` | `/usr/bin/wysna` |

Installer candidates are attached to successful
[develop CI runs](https://github.com/WashingtonYandun/Wysna/actions/workflows/ci.yml?query=branch%3Adevelop).
They are unsigned release candidates. See
[Seed distribution](docs/SEED_DISTRIBUTION.md) for installation, repair, update,
uninstall and dependency details. Other hosts can use the source checkout described
in [Development](#development); the native SDK and WASI profiles have their own limits.

```sh
wysna --version
wysna doctor
```

`doctor` exists only in the installed distribution. On Windows, `open-terminal.cmd`
sets up a terminal-local `PATH`; build outputs use `.exe`.

### Hello, Wysna

Save this as `hello.wysna`:

```wysna
import "std/json" as json

let value = json.number("42") else error { json.null() }
let output = json.stringify(json.object({"answer": value})) else error { "{}" }
print(output)
```

```sh
wysna check hello.wysna
wysna run hello.wysna          # {"answer":42}
wysna build hello.wysna -o hello
wysna explain hello.wysna --output-format=json-v1
```

`run` executes source through the reference runtime. `build` produces a native
executable through verified MIR and C99. Arguments after `--` belong to the program.

## Language tour

### Explicit error channels

```wysna
import "std/parse"

fn compute_ratio(raw: str, divisor: int) -> int ! str {
    let value = parse.parse_int(raw) else error { return Err("invalid number") }
    if divisor == 0 { return Err("division by zero") }
    return value / divisor
}
```

Error sets are part of the type. `?` propagates a compatible error set, `else error`
handles it locally, and `match` inspects the payload.

### Algebraic data types and pattern matching

```wysna
enum Status { Active, Suspended(str), Terminated(int, str) }

fn describe(status: Status) -> str {
    return match status {
        Status.Active => "active",
        Status.Suspended(reason) => "suspended: " + reason,
        Status.Terminated(code, message) => "terminated (" + str(code) + "): " + message
    }
}

print(describe(Status.Active))
```

### Copy-on-write values

```wysna
let original = [1, 2, 3, 4]
let mut copy = original   # shares storage until mutation
copy.append(5)
print(original)           # [1, 2, 3, 4]
print(copy)               # [1, 2, 3, 4, 5]
```

See [more executable examples](examples/language), including bounded resources,
error channels and pattern matching.

## Toolchain

| Command | Purpose |
| :--- | :--- |
| `wysna check` | Typecheck without executing. |
| `wysna run` | Execute source through the reference runtime. |
| `wysna build` | Compile a native executable through verified MIR → C99. |
| `wysna fmt` | Canonical, deterministic formatting. |
| `wysna explain` | Conservative effect and capability report over verified MIR. |
| `wysna project` | Lock, check, build, run and test local projects. |
| `wysna api` / `wysna capabilities` | Discover standard-library APIs and target availability. |
| `wysna lsp` | Language server over stdio. |

Structured commands accept `--output-format=json-v1`. `explain` reports transitive
effects and marks unknown FFI or indirect calls; it is **not** a sandbox, permission
enforcer or purity proof. See [effect contracts](docs/EFFECTS.md).

## Standard library

| Area | Modules |
| :--- | :--- |
| Data | `std/json`, `std/text`, `std/parse`, `std/algorithms`, `std/bytes` |
| I/O and files | `std/files`, `std/buffer`, `std/fs`, `std/path`, `std/io`, `std/resources` |
| Host | `std/environment`, `std/process`, `std/time`, `std/http` |
| Testing | `std/test` |

Native HTTP uses libcurl with TLS verification, deadlines and response limits. It
remains an explicit dependency, as do C FFI libraries and their headers. Projects use
`wysna.toml`, contained local dependencies and hash-checked lockfiles; there is no
public package registry.

## Status and scope

Wysna is developed as a set of explicitly bounded contracts, not a promise of
universal parity.

| Area | Status |
| :--- | :--- |
| Native CLI, scripting, projects, formatter, LSP core, effect reports | **Supported** for the declared profiles; host and package limits are recorded in [scope](docs/SCOPE.md). |
| Seed distribution with private CPython and Zig | **Release candidate**; unsigned installers are qualified per source commit. |
| WASI command profile | **Declared target** with a Wasmtime boundary, fuel, memory limits and explicit preopens. |
| LLVM backend and LLVM SDK | **Experimental**, opt-in and qualification-gated. |
| Wysna Compiler / native SDK | **Experimental**, bounded by the `native-core-v1` profile. |
| Browser library, local service pilot, JSON/HTTP/SQLite app | **Experimental**, with explicit dependencies and limits. |
| Public package registry, concurrency, sandbox for untrusted native code | **Not implemented**. |

### Two compilers, one language

- **Wysna Seed** is the reference implementation in Python and the default compiler.
  It provides the complete project and editor tooling, the C99 backend, and the
  independent interpreter and AST oracles.
- **Wysna Compiler** is the self-hosted implementation in Wysna. It ships as a bounded
  native SDK for the `native-core-v1` profile and is not a universal replacement for
  Seed.

Both use verified MIR and C99. See [compiler architecture](docs/COMPILER_ARCHITECTURE.md)
and [engine parity](docs/ENGINE_PARITY_MATRIX.md).

## Security

Native programs and the reference runtime execute with the user's OS authority.
Process isolation alone does not restrict filesystem, network or FFI access. The WASI
profile has a Wasmtime boundary with declared preopens, fuel and linear-memory limits;
`explain` reports effects but does not enforce a policy or sandbox execution.

Do not run untrusted native code based on effect reports. See [SECURITY.md](SECURITY.md)
and [scope](docs/SCOPE.md) for the full boundary.

## Documentation

| Start here | Go deeper |
| :--- | :--- |
| [Scope and status](docs/SCOPE.md) | [Compiler architecture](docs/COMPILER_ARCHITECTURE.md) |
| [Seed distribution](docs/SEED_DISTRIBUTION.md) | [MIR design](docs/MIR_DESIGN.md) |
| [Projects and tests](docs/PROJECTS.md) | [Effect reports](docs/EFFECTS.md) |
| [Standard library](docs/STDLIB.md) | [Known limits](docs/KNOWN_LIMITS.md) |
| [Language specification](SPEC.md) | [Engine parity](docs/ENGINE_PARITY_MATRIX.md) |
| [Examples](examples/language) | [Changelog](CHANGELOG.md) |

The full [documentation index](docs/README.md) covers ownership, FFI, the native SDK,
LLVM, the service pilot and distribution details.

## Development

Source work requires Python 3.10 or newer and a C99 compiler for native builds. A
source checkout exposes the full toolchain, including `wysna test` and `wysna bench`.

```sh
git clone --branch develop https://github.com/WashingtonYandun/Wysna.git
cd Wysna
./wysna --version
./wysna test
```

On Windows, run the source CLI as `py -3 main.py`.

Run `./wysna test --sanitize` for ASan/UBSan builds, or
`python3 tools/ci_conformance.py --profile=release` for the release corpus. The
self-hosted compiler has a deterministic bootstrap check:

```sh
python3 -m tests.unit.test_bootstrap_conformance
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for architecture, test layers and the pull
request workflow.

## Contributing

Contributions are welcome. Start with [CONTRIBUTING.md](CONTRIBUTING.md), follow the
[Code of Conduct](CODE_OF_CONDUCT.md), and report security issues privately as
described in [SECURITY.md](SECURITY.md).

## License

MIT © 2024–2026 Washington Yandún. See [LICENSE](LICENSE).
