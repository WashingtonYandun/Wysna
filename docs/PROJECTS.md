# Wysna Projects

Project mode adds a manifest, deterministic local dependency resolution, explicit
lockfile updates, project commands, and isolated user test entries. Single-file
commands remain available and `wysna test` continues to run compiler maintenance
tests.

## Create a project

Run `wysna project new example-tool` to create a new directory with a manifest,
native CLI entry point and one test. The destination must not already exist and
the command never overwrites existing files. Then run `wysna project check`,
`wysna project run` and `wysna project test` from that directory. Run
`wysna project lock` before using `--frozen`.

`--output-format=json-v1` returns a versioned result with the created files as
artifacts. If creation fails and another process has changed or added files, the
command preserves those files and reports the partial directory for inspection.

## Manifest version 1

Create `wysna.toml` at the package root:

```toml
manifest_version = 1

[package]
name = "example-tool"
version = "0.1.0"
language = "0.9"

[toolchain]
compiler = "0.9.0"
stdlib = "bundled"

[sources]
root = "src"

[targets.app]
entry = "src/main.wysna"
profile = "native-cli"

[targets.wasm]
entry = "src/main.wysna"
profile = "wasi-command"
fuel = 100000000
memory_limit_mb = 128
preopens = [{ path = "data", access = "read-only" }]

[targets.web]
entry = "src/library.wysna"
profile = "browser-library"
memory_limit_mb = 128

[dependencies.stats]
path = "vendor/stats"

[[tests]]
name = "valid-input"
entry = "tests/valid_input.wysna"
timeout_ms = 10000
```

The compiler and standard library versions must match the installed toolchain.
Manifest keys are strict; unsupported fields and profiles fail instead of being
silently ignored. Dependency paths must remain inside the declaring package. Each
dependency has its own `wysna.toml` and source root. Project manifests do not run
install hooks, plugins, or registry lookups.

The `wasi-command` profile builds a `wasm32-wasip1` command from Seed-generated C99.
The CI compatibility baseline uses [wasi-sdk 34](https://github.com/WebAssembly/wasi-sdk/releases/tag/wasi-sdk-34)
and verifies its release archive checksum; set `WASI_SDK_PATH` to a compatible SDK installation.
Running the artifact requires the [Wasmtime Python binding 50.0.1](https://pypi.org/project/wasmtime/50.0.1/).
Install the optional runner dependency with
`python -m pip install -e ".[wasi]"` in a checkout, or install the published package
with its `wasi` extra. WASI commands receive only their declared arguments, no inherited
environment, standard streams, clock/random access, and explicitly declared directory
preopens. A preopen maps to the same guest path; for example, `data` is available as
`data/input.txt`. `read-only` grants no filesystem writes. Each target defaults to 100
million fuel and 128 MiB of linear memory; `fuel` and `memory_limit_mb` can lower or
raise these values within manifest validation limits. Child processes, networking,
native C FFI, browser execution, and WASIp2 are outside this profile.
Wysna reserves a 1 MiB guest stack and places it before static data so stack overflow
traps instead of overwriting global state. The stack consumes part of the configured
linear-memory limit. Browser execution is a separate target profile.

Target checks reject unsupported imports and capabilities before invoking the SDK.
WASI build provenance includes the target triple, artifact kind, compiler identity,
module imports/exports, configured capabilities, fuel, memory and stack limits, and
preopens.

## Browser library

The `browser-library` profile uses Seed-generated C99 and Emscripten 5.0.1 to produce
three files: the public ES module facade (`web.mjs`), its Emscripten loader
(`web.runtime.mjs`), and the WebAssembly module (`web.runtime.wasm`). Import the public
facade from a browser application or Node.js; it creates isolated instances and keeps
the loader object and linear-memory pointers private.

Only top-level, non-fallible `pub fn` declarations become operations. Parameters may be `int`,
`float`, `bool`, or `str`; results may also be `unit`. JavaScript integers must be
signed 64-bit `BigInt` values. Strings use explicit-length UTF-8 buffers, preserve
embedded NUL bytes, and are copied at the ABI boundary. Generated bindings own and
free those buffers, read the current memory view after growth, reject reentrant calls,
and invalidate an instance after a WebAssembly trap. Calls after destruction fail.
The default maximum linear memory is 128 MiB; set `memory_limit_mb` from 8 to 1024 in
the target manifest to change it.

The initial profile rejects top-level initialization and host APIs such as files,
environment access, time, processes, HTTP, native FFI, and console output. It does not
auto-call a Wysna `main` function. Composite values can cross as JSON encoded in `str`
when the application chooses that contract. `project run` does not execute this
library profile; import the generated facade in the host instead.

```wysna
pub fn add(left: int, right: int) -> int {
    return left + right
}

pub fn greet(name: str) -> str {
    return "Hello, " + name
}
```

```js
import { createWysna } from "./build/web.mjs";

const library = await createWysna();
const total = library.exports.add(41n, 1n);
const greeting = library.exports.greet("Washo");
library.destroy();
```

Emscripten is an explicit build dependency and is not downloaded by Wysna. CI uses
the official `emscripten-core/setup-emsdk` action pinned to SDK 5.0.1. Build
provenance records the SDK version, imports/exports, ABI version, memory limit, and
generated exports. Browser APIs and DOM access remain owned by the host application.

## Shared native and browser application

The shared browser app example keeps business logic in one Wysna module and calls it
from both a native command and a browser interface. The browser interface owns HTML,
input validation, and presentation; the Wysna module owns the calculation. CI builds
both targets and compares the native and WebAssembly results for the same input.

Build the CLI and WebAssembly library from the repository root:

    python main.py project build --manifest examples/shared-browser-app/wysna.toml --target app --output examples/shared-browser-app/build/quote
    python main.py project build --manifest examples/shared-browser-app/wysna.toml --target web --output examples/shared-browser-app/build/pricing.mjs

Run the native command with a quantity and a unit price in cents:

    examples/shared-browser-app/build/quote 3 1299

Serve the example directory and open its web interface:

    python3 -m http.server --directory examples/shared-browser-app 8000

The shared module returns 3897 cents for that input in both environments. See
[the complete example](../examples/shared-browser-app/README.md) for its browser UI
and project manifest.

Imports use existing Wysna syntax. Relative imports resolve inside the declared
`sources.root`; tests outside that directory can import package modules through
the same root. Declared dependencies use `pkg/<alias>/<module-path>`, for example:

```wysna
import "pkg/stats/summary.wysna"
```

Project resolution never falls back to the current working directory. Physical
path containment, symlink boundaries, and portable case/Unicode collisions are
checked before compiling.

## Commands

```sh
wysna project lock [--manifest path/to/wysna.toml] [--offline]
wysna project check [--manifest path/to/wysna.toml] [--frozen] [--offline]
wysna project build [--target app] [-o build/app] [--frozen] [--output-format=json-v1]
wysna project run [--target app] [-- args...]
wysna project test [--filter name] [--report-json report.json]
```

Without `--manifest`, Wysna searches from the current directory toward its parents.
`project lock` is the explicit lockfile writer. Its versioned JSON lock records
package identities, dependency aliases, exact compiler and standard-library
versions, and ordered hashes for dependency inputs. `--frozen` requires the lock
to exist and rejects manifest or dependency drift. Root-package source edits do
not require a lock refresh. Resolution is local and offline by design.

`project check` type-checks the selected target and validates its required
capabilities without executing it. `project build` generates the artifact declared
by the target profile and reports source, lock, target, and toolchain provenance in
machine output. Native `project run` uses the reference interpreter and sets
`args()[0]` to "wysna"; arguments after `--` are passed unchanged. A `wasi-command`
target is compiled and executed in a fresh Wasmtime instance with the manifest's
capability and resource limits.

Each `[[tests]]` entry runs in its own process. Tests execute sequentially, use
their declared timeout, and preserve stdout/stderr with a 1 MiB cap per stream.
`--filter` selects names by substring and fails if it matches nothing. An empty
suite exits nonzero and is reported as empty; it is not treated as functional
coverage. The aggregate report records each case's status, duration, exit code,
source hash, timeout, and bounded-output hashes.

## Scripting APIs

`std/environment` exposes the runner's argument vector and optional environment
lookup. `std/time` exposes Unix epoch seconds. `std/process.run` invokes an
executable with a separate argument list, without invoking a shell implicitly,
and enforces a timeout. Child stdout and stderr inherit the Wysna program's
streams. See [`examples/automation`](../examples/automation) for a project that
orchestrates a child command and has a user test entry. `std/http` adds a
synchronous HTTP/HTTPS client with verified TLS, explicit timeouts and body/header
limits; it returns redirects without following them and never retries automatically.
The native CLI profile reports network as an available host capability, but it is
not a network sandbox. See [`examples/http_json_client.wysna`](../examples/http_json_client.wysna)
for a JSON endpoint call.

`wysna api std/json --output-format=json-v1` returns public symbol signatures,
error channels, documentation when present, and explicit `unknown` values for
ownership and effect facts that are not currently encoded in the language metadata.
`wysna api --manifest path/to/wysna.toml` indexes a checked project module graph.
`wysna capabilities --output-format=json-v1` reports commands and target profiles
supported by the installed Seed compiler. The additive `target_profiles` field
includes the owning engine, backend, triple, artifact kind, capabilities and
build/run requirement objects (`name`, `version`, `provided_by`); consumers should
select a profile only when its engine matches the compiler they are invoking.
`wysna explain path/to/source.wysna --manifest path/to/wysna.toml` produces a
separate read-only [effect report](EFFECTS.md); project target checks do not
consume that report.

## Pruebas para agentes de IA

El flujo recomendado es listar, seleccionar y ejecutar un plan verificable:

```sh
wysna project test --discover --list --frozen --output-format=json-v1
wysna project test --discover --case test:example --list --output-format=json-v1
wysna project test --discover --case test:example --expect-plan-hash HASH --output-format=json-v1 --report-json tests.json --report-junit tests.xml
```

El hash debe corresponder a la misma selección y al mismo estado del lock. Si se
usa `--frozen` al listar, también debe usarse al ejecutar. `--list` no compila ni
ejecuta las entradas. Devuelve `wysna.project-test-plan/1` en `result`, con IDs,
rutas relativas, SHA-256 de cada entrada y su timeout. El hash cubre el manifiesto, el lock, la huella de la implementación del SDK,
los casos seleccionados, los archivos `.wysna` y los manifiestos de las
dependencias locales. El conjunto incluye los helpers de pruebas. `--list`
identifica una revisión y el árbol puede seguir cambiando durante una ejecución. Un
hash obsoleto devuelve `W5005` y código 2 antes de iniciar los casos. El
reporte de ejecución incluye el mismo `workspace_revision` del plan.

Los casos declarados conservan el orden del manifiesto. `--discover` añade, en
orden de ruta, archivos `tests/**/test_*.wysna` y `tests/**/*_test.wysna`; no añade
una entrada ya declarada ni sigue symlinks. Sus IDs son `test:auto:RUTA`.
`--case` exige un ID exacto; `--filter` conserva la selección por subcadena del
nombre. Una selección admite hasta 4096 casos. Una suite vacía devuelve código 2.

Cada caso se ejecuta en un proceso separado como programa `native-cli`,
independientemente del target de la aplicación. El timeout incluye comprobación,
compilación y ejecución. Cada stream conserva como máximo 1 MiB; excederlo detiene
el caso y registra `output_limited`. Los casos siguientes continúan tras un fallo
normal. Una cancelación detiene la suite, devuelve 130 y registra cuántos casos se
seleccionaron (`selected`) y ejecutaron (`executed`). Se limpia el árbol de
procesos y el directorio temporal del caso antes de publicar el resultado.

Con `--output-format=json-v1`, stdout contiene un único `wysna.tool-result/1`.
`result` conserva el reporte `wysna.project-test-report/1`; cada caso incorpora
`id`, `failure_kind`, `diagnostics`, stdout/stderr decodificados como UTF-8 con
sustitución de bytes inválidos y los SHA-256 de sus bytes capturados. Los NUL se
conservan en JSON. JUnit sustituye caracteres no válidos en XML. Un fallo de
publicación devuelve `W5004`/3 y conserva el resultado ejecutado en stdout.
Cada archivo de reporte se publica mediante un reemplazo individual; JSON y
JUnit no forman una transacción conjunta. Los artefactos publicados incluyen su
ruta y SHA-256. Los destinos no pueden sobrescribir manifiestos, locks ni fuentes.

`std/test` mantiene `expect`, `expect_equal_int` y `expect_equal_text` y añade
`expect_equal[T]`, `expect_not_equal[T]`, `expect_close`, `expect_some[T]`,
`expect_none[T]` y `fixture_directory()`. Esta última devuelve `str ! FixtureError`
y requiere el entorno del runner. Cada caso recibe un `WYSNA_TEST_TMPDIR` único,
eliminado después del caso. No es un sandbox: el programa mantiene el acceso al
host que tenga el proceso. Seed y el SDK nativo admiten este flujo; el SDK no
requiere Python. `wysna skills project-tests --output-format=json-v1` expone las
instrucciones al agente desde la herramienta instalada.

### Cambios coordinados con el mismo plan de pruebas

`wysna patch --manifest wysna.toml --edits changes.json --output-format=json-v1`
previsualiza un lote en Seed y en el SDK nativo. Cada edición incluye un
`path` relativo a un archivo `.wysna`. Usa `source`, `symbol` y
`replacement`, o `hunks` para modificarlo. Usa `create: true` con
`source` para crearlo y `delete: true` para borrarlo. Conserva las opciones
`--discover` y `--frozen` del plan de pruebas.

La previsualización comprueba los destinos, pruebas y archivos editados en memoria.
Devuelve diffs y hashes, los IDs de las pruebas seleccionadas y la revisión esperada
tras aplicar el cambio. Una prueba que carga un archivo editado aparece como
`affected`. Las demás se incluyen de forma conservadora como `uncertain`.

Para aplicar el lote, usa los mismos argumentos con `--apply --expect-plan-hash HASH`.
Ambos motores vuelven a comprobar la revisión, preparan las copias antes de
publicar y revierten las operaciones publicadas si falla una posterior. Seed
también serializa escritores cooperativos con locks por archivo. Si un cambio
externo impide restaurar un archivo, el error identifica la copia conservada
cuando existe. Los lectores externos pueden observar pasos intermedios. La
reversión requiere que el proceso siga ejecutándose.

Después de aplicar, ejecuta `wysna project test --list --output-format=json-v1`
con el mismo motor y la misma selección. `result.workspace_revision` debe coincidir con
`workspace_revision_after` del parche. Ejecuta entonces las pruebas con el
`--expect-plan-hash` de ese listado, conservando la selección y las opciones.
