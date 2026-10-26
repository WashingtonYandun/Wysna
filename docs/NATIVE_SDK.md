# Native SDK

The `native-core-v1` SDK runs Wysna Compiler and the C toolchain as native executables. It includes the runtime headers, standard library, a pinned Zig 0.15.2 toolchain and its licenses. Python and Wysna Seed are build-time tools and are not included in the SDK.

This is the native compiler profile for development on `develop`. The [standalone Seed distribution](SEED_DISTRIBUTION.md) remains available for the broader Seed tooling set. This package does not change the default backend of the Python CLI or introduce a direct LLVM backend.

## Installation and commands

Extract the archive into a new directory. On Windows, `open-terminal.cmd` opens a terminal with this SDK on its temporary PATH. On macOS and Linux, invoke `bin/wysna` or add that directory to PATH. The SDK locates its engine and toolchain from the executable's physical location, including invocation through a symlink.

```sh
wysna --version
wysna check program.wysna
wysna emit-mir program.wysna -o program.mir.json
wysna emit-c program.wysna -o program.c
wysna build program.wysna -o program --profile release
wysna run program.wysna -- argument "two words"
```

Windows executables receive an `.exe` suffix and an explicit 8 MiB stack reserve, shared with the compiler bootstrap and Seed native linker policy. `run` builds a private temporary executable and forwards arguments after `--`, standard input/output/error, the working directory and the application's exit status. `--timeout-ms N` sets the application's deadline from 1 to 600000 ms; compilation phases each have a 120000 ms deadline. Interrupts cancel the child process tree before temporary artifacts are removed.

The `dev`, `release` and `size` profiles select `-O0 -g`, `-O2` and `-Os`. All profiles retain Wysna runtime checks. They do not enable fast-math or unchecked indexing. Source checking always precedes C compilation. Errors never trigger a Python fallback.

## Supported source profile

The native pipeline covers scalars, UTF-8 strings and paths, globals, collections, CoW mutation, direct ranges and nested tuple loop bindings, optional and Result channels, closed error unions, structs/enums, classes/inheritance/interfaces, pattern matching with guards, closures by snapshot, function values, concrete generic functions/structs/enums and the scalar/opaque C ABI. The profile and independent-verifier contracts are described in [MIR design](MIR_DESIGN.md).

Parameterized classes/interfaces, generic nominal values whose type arguments remain unresolved, library-mode exports, WebAssembly targets and the Seed project/package/LSP/formatter commands are outside this SDK profile. An unresolved nominal value needs an explicit type context. Native profile exclusions report `W2013`; unsupported commands report `W5002`. The SDK does not claim universal Seed parity.

External C dependencies remain explicit:

```sh
wysna build program.wysna --include-dir vendor/include --link vendor/adapter.c --library sqlite3
```

`--link` accepts a file, `--library` a library name, and `--include-dir` an existing directory. These are argument-vector operations, never shell fragments. HTTP requires separately installed libcurl 8.3 or newer and its include/link inputs; libcurl is not part of the offline core bundle.

## Files, caches and updates

Build and file-emission outputs are staged in a private directory beside the destination and renamed only after success. Existing output survives source and compiler errors. A source file, a source alias and a `.wysna` destination are rejected. Relative imports resolve beside their importing source; standard-library resolution is confined to this installed SDK; a missing bundled module never falls back to the current directory.

Zig's local cache is temporary. The persistent global cache is outside the SDK, under `WYSNA_CACHE_HOME` when set, otherwise the user's cache directory (`LOCALAPPDATA/Wysna` on Windows or `XDG_CACHE_HOME`/`HOME/.cache` on Unix). Toolchain cache variables are restored before the application starts. The installation can remain read-only.

Install an update into a new directory and switch the terminal's PATH or symlink. Do not unpack over an active installation. Uninstall by removing the extracted SDK directory; projects and generated executables are independent. The optional Wysna cache can be removed separately. The portable SDK makes no persistent PATH, registry or Python changes.

## Build and provenance

```sh
python tools/package_native.py --output dist/wysna-native --archive
```

The package builder verifies downloaded toolchain hashes and rejects unsafe archive members. `native-sdk.json` records the source commit, whether the checkout was dirty, compiler fingerprint, native bootstrap hashes, component provenance and file checksums. It preserves all toolchain license notices. Explicit targets use macOS 13 or newer, Windows GNU x64, or Linux musl; they do not inherit a host SDK or CPU tuning. Each target still requires its own installed acceptance gate.

Seed AST emits `bootstrap.c` to construct an initial engine. That engine emits native MIR C0; C0 builds Stage 1, which emits C1; C1 builds Stage 2, which emits C2. Packaging requires `C0 == C1 == C2` and unchanged compiler inputs. The Seed AST emission is recorded separately and is not relabeled as native C0.

CI must build and test each announced platform independently, including an installation outside the checkout without host Python or CC discovery. A successful build on one platform does not certify another platform. Archive artifacts are development builds; release promotion remains separate.
