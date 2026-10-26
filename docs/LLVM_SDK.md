# Wysna Seed LLVM SDK

This is a separate, relocatable SDK for `llvm-native-v1`, distributed through CI
artifacts. It requires host Python 3.10+ and bundles LLVM 20.1.8 native tools,
Zig 0.15.2, Seed, the runtime, standard library and licenses. It does not require
llvmlite, LLVM, Clang or another C compiler on PATH for ordinary native programs.
The [Wysna Compiler SDK](NATIVE_SDK.md) remains the independent Python-free C99 product.

## Supported installations

CI builds `llvm-sdk-ubuntu-latest` for Linux x64 and `llvm-sdk-macos-latest` for
macOS arm64. Download an artifact from a successful run of the intended commit,
then unpack its inner `llvm-sdk.tar.gz`, preserving the directory tree. Invoke
`llvm-sdk/bin/wysna` or add that `bin` directory to PATH. No administrator writes
are required. `WYSNA_PYTHON` can select an absolute Python executable; otherwise the
launcher uses `python3` from PATH.

The macOS LLVM tools require macOS 14+, while generated programs target macOS 13.
Linux acceptance runs on Ubuntu 24.04 x64; the bundled C toolchain targets
`x86_64-linux-gnu.2.28`. These are declared host/target contracts, not a claim that
all Linux distributions or LLVM targets have been validated. Windows, cross builds,
Wasm and browser-library exports use other Wysna routes.

```sh
./llvm-sdk/bin/wysna version
./llvm-sdk/bin/wysna check hello.wysna --output-format=json-v1
./llvm-sdk/bin/wysna emit-llvm hello.wysna -o hello.ll
./llvm-sdk/bin/wysna build hello.wysna --profile=release -o hello
./llvm-sdk/bin/wysna run hello.wysna --timeout-ms 10000 -- "two words"
```

The SDK command explicitly selects LLVM and supports `check`, `emit-llvm`, `build`,
`run`, `version` and help. Full package/project/editor tooling remains in Seed's
normal installation. Effect reports (`wysna explain`) are a Seed command and are not
exposed by this restricted SDK. Commands do not acquire missing tools or fall back to C99.

## Dependencies and installed state

Core programs compile offline with the bundled Zig C compiler/linker and LLVM
`opt`/`llc`; macOS also bundles `dsymutil`. Host Python and normal OS loader/system
libraries remain required. Python itself is not bundled. Optional `std/http` needs
libcurl development files and `curl-config`; external FFI libraries need their
explicit headers/libraries/adapters. `--sanitize` additionally requires `CC` to
select host Clang and its matching sanitizer runtime; this developer mode is
explicitly outside the offline core contract. The verified program remains LLVM IR.

`llvm-sdk.json` records the source commit and exact source hashes, wheel checksum,
platform/triple/data layout, ABI, official component URLs/checksums, installed file
checksums and installed bytes. Startup validates the installation and actual LLVM
version. Missing, changed or incompatible components produce `W5001`. Checksums
are integrity/provenance records, not a cryptographic publisher signature.

The installation may be read-only. Seed runs with isolated Python imports and
bytecode writes disabled. LLVM temporary work goes into private build directories;
Wysna object and Zig global caches go to user-writable cache locations. Set
`WYSNA_COMPILER_CACHE_HOME` for objects and `WYSNA_CACHE_HOME` for the SDK's Zig global
cache. SDK files never serve as build outputs. Paths with spaces/Unicode and a
symlink to the command are supported.

Install an update beside the previous version, verify it, then redirect your PATH
or command symlink. The packager refuses to overwrite an existing SDK. Retain the
previous directory for rollback. Removing an old SDK does not remove user caches
or applications built with it. Applications do not require Seed or LLVM to run;
normal OS and explicitly linked FFI dependencies still apply.

## Building and acceptance

```sh
python -m build --wheel --outdir dist/wheel
python tools/package_llvm_sdk.py --wheel dist/wheel/wysna-0.5.0-py3-none-any.whl --output /tmp/wysna-llvm-sdk --archive
python tools/validate_llvm_sdk.py --sdk /tmp/wysna-llvm-sdk --report /tmp/llvm-installed.json
```

Packaging downloads pinned official archives, verifies SHA256 before extraction,
selects the required native LLVM tools, preserves licenses and checks that the
wheel matches current source files. The output contains no mutable build cache.
`--llvm-archive`, `--zig-archive` and `--cache` can use predownloaded verified inputs.
`--source-commit SHA` requires the requested checkout revision. CI consumer acceptance
also rejects a dirty or different source revision.

A separate CI consumer job has no checkout. It tests all optimization profiles,
managed programs, optional values, closures, CoW, JSON, native run reports,
read-only installation, unavailable host CC/LLVM, relocation, command-symlink update
and missing/corrupt components. Reports and SDK manifests are retained with the
exact CI head. Archive download size and installed size are different quantities;
use that run's manifest rather than assuming the SDK is lightweight.
