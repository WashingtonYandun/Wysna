# Native SDK

The `native-core-v1` SDK runs Wysna Compiler and the C toolchain as native executables. It includes the runtime headers, standard library, a pinned Zig 0.15.2 toolchain and its licenses. Python and Wysna Seed are build-time tools and are not included in the SDK.

This is the native compiler profile for development on `develop`. The [standalone Seed distribution](SEED_DISTRIBUTION.md) remains available for the broader Seed tooling set. This package does not change the default backend of the Python CLI or introduce a direct LLVM backend.

## Installation and commands

Extract the archive into a new directory. On Windows, `open-terminal.cmd` opens a terminal with this SDK on its temporary PATH. On macOS and Linux, invoke `bin/wysna` or add that directory to PATH. The SDK locates its engine and toolchain from the executable's physical location, including invocation through a symlink.

```sh
wysna --version
wysna check program.wysna --output-format=json-v1
wysna fmt program.wysna --output-format=json-v1
wysna test tests/
wysna graph program.wysna --output-format=json-v1
wysna inspect program.wysna --symbol main --with-deps --output-format=json-v1
wysna patch program.wysna --symbol main --expect-graph-hash graph:... --with "..." --apply
wysna explain program.wysna --output-format=json-v1
wysna schema program.wysna --output-format=json-v1
wysna fix program.wysna --apply --output-format=json-v1
wysna skills overview --output-format=json-v1
wysna capabilities --output-format=json-v1
wysna mcp --root .
wysna lsp
wysna api std/json --output-format=json-v1
wysna project check
wysna project build --profile release
wysna project run -- --demo
wysna project test --list --output-format=json-v1
wysna project test --output-format=json-v1 --report-json tests.json --report-junit tests.xml
wysna emit-mir program.wysna -o program.mir.json
wysna emit-c program.wysna -o program.c
wysna build program.wysna -o program --profile release
wysna run program.wysna -- argument "two words"
```

Windows executables receive an `.exe` suffix and an explicit 8 MiB stack reserve, shared with the compiler bootstrap and Seed native linker policy. `run` builds a private temporary executable and forwards arguments after `--`, standard input/output/error, the working directory and the application's exit status. `--timeout-ms N` sets the application's deadline from 1 to 600000 ms; compilation phases each have a 600000 ms deadline. Interrupts cancel the child process tree before temporary artifacts are removed.

The `dev`, `release` and `size` profiles select `-O0 -g`, `-O2` and `-Os`. All profiles retain Wysna runtime checks. They do not enable fast-math or unchecked indexing. Source checking always precedes C compilation. Errors never trigger a Python fallback.

## Supported source profile

The native pipeline covers scalars, UTF-8 strings and paths, globals, collections, CoW mutation, direct ranges and nested tuple loop bindings, optional and Result channels, closed error unions, structs/enums, classes/inheritance/interfaces, pattern matching with guards, closures by snapshot, function values, concrete generic functions/structs/enums and the scalar/opaque C ABI. The profile and independent-verifier contracts are described in [MIR design](MIR_DESIGN.md).

Parameterized classes and interfaces (including constrained type parameters, independent generic methods `Box[T].transform[R]`, and parameterized class inheritance) as well as stackless CPS `async fn` coroutines and `from ... use` imports are supported in the native profile. `std/environment.arguments()`, `std/environment.get(name)` and `std/time.unix_seconds()` remain supported; C99 builds generated via Seed `wysna build --sandbox` (with optional `--allow`/`--deny`) enforce `WYSNA_DENY_CAP_*` capability sandbox guards (`W5006`) at runtime host boundaries. The native SDK includes `fmt`, `test`, `graph`, `inspect`, `patch`, `explain`, `schema` (`--emit-codecs`), `fix` (`--apply`, `--include-maybe`), `skills`, `capabilities`, `api` (`wysna.api-index/1`, stdlib and `--manifest`), `project new|lock|check|build|run|test` (`wysna.toml` v1, native C FFI flags `--include-dir`/`--link`/`--library`), `wysna mcp` (11 static tools over newline-delimited JSON-RPC 2.0 stdio with `--root` confinement), and `wysna lsp` (Stage-3 native LSP) without Python; library-mode exports and browser-library builds stay in Seed. Native profile exclusions report `W2013`; unsupported commands report `W5002`.

For a small CLI, read user arguments and environment configuration through the standard module:

```wysna
import "std/environment" as environment

let arguments = environment.arguments()
let name = if len(arguments) > 1 { arguments[1] } else { "world" }
let greeting = environment.get("WYSNA_GREETING") ?? "Hello"
print(greeting + ", " + name)
```

Run it with `wysna run greet.wysna -- Ada`; setting `WYSNA_GREETING` customizes the greeting. Environment access inherits the parent process and is not a sandbox boundary.

External C dependencies remain explicit:

```sh
wysna build program.wysna --include-dir vendor/include --link vendor/adapter.c --library-dir vendor/lib --library sqlite3
```

`--link` accepts a file, `--library` a library name, and `--include-dir` and `--library-dir` existing directories. These are argument-vector operations, never shell fragments. Libraries must be compatible with the SDK target and architecture; a host SDK that supplies only arm64e symbols is not sufficient for an arm64 target. HTTP requires separately installed libcurl 8.3 or newer and its include/link inputs; libcurl is not part of the offline core bundle.

## Paquetes reproducibles para agentes

`wysna packages pack|fetch|add|remove|restore` se ejecuta en Selfhost. El SDK lo
anuncia en `native-sdk.json` y admite archivos USTAR v1 fijados por SHA-256.
`fetch` verifica el archivo y crea la caché; `add` modifica explícitamente el
manifiesto; `project lock` registra el contenido. `restore` reconstruye `vendor/`
desde la caché sin red y conserva `wysna.lock`. Las operaciones admiten
`--output-format=json-v1`. No se ejecutan hooks de instalación ni se requiere
Python. No existe todavía un registro público ni resolución de versiones remotas.

## Files, caches and updates

Build and file-emission outputs are staged in a private directory beside the destination and renamed only after success. Existing output survives source and compiler errors. A source file, a source alias and a `.wysna` destination are rejected. Relative imports resolve beside their importing source; standard-library resolution uses this installed SDK, rejects parent traversal and symlinks outside the standard-library root, and never falls back to the current directory when a bundled module is missing. These checks do not make an installation writable by an adversary into a filesystem sandbox.

Zig's local cache is temporary. The persistent global cache is outside the SDK, under `WYSNA_CACHE_HOME` when set, otherwise the user's cache directory (`LOCALAPPDATA/Wysna` on Windows or `XDG_CACHE_HOME`/`HOME/.cache` on Unix). Toolchain cache variables are restored before the application starts. The installation can remain read-only.

Install an update into a new directory and switch the terminal's PATH or symlink. Do not unpack over an active installation. Uninstall by removing the extracted SDK directory; projects and generated executables are independent. The optional Wysna cache can be removed separately. The portable SDK makes no persistent PATH, registry or Python changes.

## Build and provenance

```sh
python tools/package_native.py --output dist/wysna-native --archive
```

The package builder verifies downloaded toolchain hashes and rejects unsafe archive members. `native-sdk.json` records the source commit, whether the checkout was dirty, compiler fingerprint, native bootstrap hashes, component provenance and file checksums. It preserves all toolchain license notices. Explicit targets use macOS 13 or newer, Windows GNU x64, or Linux musl; they do not inherit a host SDK or CPU tuning. Each target still requires its own installed acceptance gate.

Seed lowers the compiler source through verified HIR/MIR and emits `bootstrap.c` to construct an initial engine. That engine emits native MIR C0; C0 builds Stage 1, which emits C1; C1 builds Stage 2, which emits C2. Packaging requires `C0 == C1 == C2` and unchanged compiler inputs. The Seed HIR emission is recorded separately and is not relabeled as native C0. The independent AST C99 emitter remains a conformance oracle.

CI must build and test each announced platform independently, including an installation outside the checkout without host Python or CC discovery. A successful build on one platform does not certify another platform. Archive artifacts are development builds; release promotion remains separate.

## Arrancar el compilador desde un SDK nativo

En macOS arm64 y Linux x64, ejecuta desde un checkout con el compilador
consolidado en `selfhost/compiler.wysna`. Usa como entrada un SDK nativo
cuyo archivo hayas verificado con las sumas de su distribución.

```sh
tools/bootstrap_from_native.sh /ruta/al/sdk /ruta/a/un/directorio-nuevo
```

El SDK anterior construye el primer compilador con el Zig incluido. Las tres
etapas compiladas deben emitir C idéntico a partir de las mismas fuentes.
El último compilador queda en `stage3` dentro del directorio de salida. Este
recorrido usa el compilador, Zig y herramientas del sistema POSIX. El flujo
de empaquetado oficial todavía usa el arranque y los validadores de Seed.

Tanto en proyectos sin dependencias como en proyectos con dependencias de
paquetes locales, `project lock` genera el mismo lockfile que Seed y `--frozen`
comprueba el manifiesto, las versiones y las huellas SHA-256 de sus entradas.

## Comandos WASI con Zig empaquetado

```sh
wysna build --target=wasi main.wysna -o main.wasm --output-format=json-v1
```

El SDK construye un módulo WASI Preview 1 y devuelve el perfil
`wasi-command`, el triple `wasm32-wasip1` y el artefacto `wasm_module`.
La compilación usa Zig empaquetado y reserva 1 MiB de pila del huésped.
El perfil comprueba capacidades y rechaza red, procesos y FFI nativa.

Ejecuta el artefacto con un runner WASI y declara los recursos que puede
recibir el huésped. Seed ofrece la ejecución y los manifiestos de proyecto
WASI. CI construye un módulo con el SDK instalado de cada sistema y lo
ejecuta con Wasmtime en Linux.

## Pruebas de proyecto por agentes

El SDK implementa el [protocolo de pruebas de proyecto](PROJECTS.md#pruebas-para-agentes-de-ia)
con planes, selección exacta, descubrimiento explícito, precondición SHA-256,
JSON/JUnit, fixtures y captura limitada. El timeout de cada caso incluye sus
fases de compilación y ejecución. `wysna test PATH` conserva su contrato separado
de fixtures del compilador y archivos `.expected`.
