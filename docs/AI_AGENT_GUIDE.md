# Wysna Workflow for AI Agents

Use the installed Seed CLI as the project workflow. It provides deterministic
project checks, builds, tests, API discovery and versioned machine output. The standalone `native-core-v1` SDK provides `check`, `fmt`, `test`, `build`, `run`, `graph`,
`inspect`, `patch`, `explain`, `fix`, `schema`, `mcp`, `api`, `capabilities`, `skills`,
`project new|lock|check|build|run|test`, and Stage-3 native `lsp` without Python. The native SDK also builds standalone
WASI commands. Seed provides WASI project workflows, execution, and library-mode browser exports.

## Discover the installed toolchain

Start by reading the commands and target contracts from the compiler you will run:

```sh
wysna capabilities --output-format=json-v1
wysna api std/json --output-format=json-v1
```

The `wysna.capabilities/1` result reports the installed commands and each target's
engine, backend, triple and capabilities. Its `build_requirements` and
`run_requirements` arrays contain objects with `name`, `version` and `provided_by`
fields. Select only a profile returned by this command. A target's presence does
not mean every Wysna compiler implementation supports it; inspect the `engine`
field and use the matching installed toolchain.

## Create, check, build and test a project

```sh
wysna project new hello-agent --output-format=json-v1
wysna project check --manifest hello-agent/wysna.toml --output-format=json-v1
wysna project build --manifest hello-agent/wysna.toml --output-format=json-v1
wysna project run --manifest hello-agent/wysna.toml
wysna project test --manifest hello-agent/wysna.toml --list --output-format=json-v1
wysna project test --manifest hello-agent/wysna.toml --case test:example --output-format=json-v1 --report-json hello-agent/test-report.json --report-junit hello-agent/test-report.xml
```

Use `wysna api --manifest path/to/wysna.toml --output-format=json-v1` to inspect
the checked project's modules and public symbols before choosing library calls.
Use `--frozen` on project checks and builds when the project includes a lockfile.
Program arguments belong after `--` for `project run` and are passed as separate
arguments, without implicit shell parsing.

## Handle diagnostics and self-heal from their source snapshot

Prefer `--output-format=json-v1` for `check`, `fix`, `schema`, and `project check`.
The type checker recovers across independent statements, class methods, and generic
templates to report up to 100 independent diagnostics in a single pass while
suppressing cascade noise from failed bindings.

Read each diagnostic's `code`, `severity`, `stage`, `message`, `data`, `related`,
and `fixes`:
- If `primary_span` is present, its offsets `start_byte` and `end_byte` are UTF-8
  byte offsets into the source identified by `source_id`; verify `source_sha256`
  before editing that snapshot. Do not treat byte offsets as Unicode character
  indexes, and do not guess a location when `primary_span` is absent.
- `data` carries structured machine facts (`expected`, `found`, `context`,
  `argument_index`, `parameter_name`, `callee`, `expected_arity`,
  `found_arity`, `conversion_hint`, `unwrap_hint`, `missing_field`, `help`)
  described by [wysna-tool-result.v1.schema.json](../schemas/wysna-tool-result.v1.schema.json).
- `related` points to secondary source spans (such as the callee declaration on
  `W2001`/`W2005`, immutable `let` on `W3001`, or prior declaration on `W1002`).
- `fixes` contains SHA-256-guarded byte-range edits (`applicability: "machine"` or
  `"maybe"`). Run `wysna fix --apply --output-format=json-v1 <file.wysna>` before
  spending an LLM repair turn for automatic repair of identifier/field typos,
  immutable `let` -> `let mut` bindings, `str(...)` wrapping on `W2001` string concatenation mismatches,
  and foreign operator/visibility slips (`&&` -> `and`, binary `||` -> `or`, prefix `!` -> `not`, legacy `public` -> `pub` and `private` -> `priv`).
  Use `wysna api std/csv --output-format=json-v1` (or `wysna skills stdlib`) to confirm module exports such as `csv.parse`, `csv.parse_records`, and `csv.stringify`.

Re-run the check after editing and require a successful result before building.

## Extract JSON Schemas, Tool Contracts, and Use the MCP Server

Generate JSON Schema (Draft 2020-12) `$defs` for `struct`/`enum` types and LLM
tool definitions (including verified MIR `effects`, `capabilities`, `pure`, and
`idempotent` flags) directly from Wysna source:

```sh
wysna schema --output-format=json-v1 src/main.wysna
```

For multi-turn agent loops, start the persistent in-memory Model Context Protocol
server over stdio to avoid Python startup and temporary-file overhead on every check:

```sh
wysna mcp --root .
wysna mcp --root . --allow-exec
```

The MCP server exposes 13 tools confined to `--root`: `wysna_check`, `wysna_fix`,
`wysna_fmt`, `wysna_explain`, `wysna_schema`, `wysna_api`, `wysna_capabilities`,
`wysna_graph`, `wysna_inspect` (single symbol, index, or batch `symbols`), `wysna_patch`
(`source`, `symbol`, `hunks`, `create`, `delete`), `wysna_project_check`, `wysna_project_test`
(`list: true` always allowed; test execution requires `--allow-exec`), and `wysna_skills`.

For zero-Python native semantic inspection, checked edits, and editor integration in
the standalone SDK / Selfhost Stage-2+ toolchain:

```sh
wysna graph src/main.wysna --output-format=json-v1
wysna graph src/main.wysna --symbol Service --rel impact --output-format=json-v1
wysna inspect src/main.wysna --symbol run_pipeline --with-deps --max-bytes 2048 --output-format=json-v1
wysna patch src/main.wysna --symbol Service.run --expect-graph-hash graph:... --with "..." --apply --output-format=json-v1
wysna patch --manifest wysna.toml --edits changes.json --apply --expect-plan-hash HASH --output-format=json-v1
wysna lsp
```

`wysna inspect` enforces UTF-8 byte budgets via `--max-bytes` (`--source-offset` / `--offset`), returns resumable fetch handles in `omitted[].ref`, and surfaces `call_resolution` plus `analysis_boundaries` (`virtual-dispatch`, `indirect-call`, `c-ffi`) across `graph` and `inspect`. Both Seed and the Native SDK (`patch --manifest ... --edits ...` and `wysna_patch` MCP) support checked multi-file workspace patches with unified diffs, impact selection, plan-hash validation (`W5005`), and staged publication with rollback if a later operation fails.

Para comprobar restricciones entre clases, declara roles con un atributo
tipado y reglas en `wysna.toml`. Usa `wysna_graph` con `manifest` y `role`
para localizar clases. `wysna_project_check` y `wysna_patch` informan
`W6101` si hay una arista prohibida y `W6102` si falta evidencia.
[Contratos de arquitectura](ARCHITECTURE_CONTRACTS.md).

`wysna patch` rejects stale `graph:...` hashes with `W5005` and verifies types in
memory before mutating disk. When applying edits via Seed CLI or MCP (`wysna patch --apply`
and `wysna fix --apply`), cooperating Wysna writers serialize per file with a cooperative
lock, re-verify the exact source SHA-256 inside the critical section (failing with `W5005`
on concurrent modification), and publish atomically while preserving UTF-8 line endings
and file permissions. External editors that write directly without using Wysna tooling do
not hold the cooperative lock; optimistic revision verification still rejects stale snapshots
detected before replacement.

### Seleccionar el símbolo que se quiere consultar o editar

Si un nombre corto coincide con varias declaraciones, `graph`, `inspect` y
`patch` devuelven `W1005`. En `json-v1`, `diagnostics[].data.candidates`
incluye `id`, `qualified_name`, `signature`, archivo y ubicación. Usa el
`id` elegido como valor de `--symbol`. Para un parche con comprobación de
revisión, usa también el `data.graph_hash` de esa consulta.

Consulta declaraciones importadas desde el archivo que las usa. Para editarlas
con un parche de símbolo, abre el archivo que contiene la declaración y vuelve
a obtener su hash. El parche verifica el archivo de destino antes de usar el
rango de la declaración.

### Invocar una herramienta con un contrato tipado

Declara la herramienta y su identidad versionada en un módulo:

```wysna
struct AgentTool { id: str, version: int }

@AgentTool(id: "ops.summarize", version: 1)
pub fn summarize(count: int) -> int {
    return count + 1
}
```

Genera un adaptador y ejecútalo con un argumento JSON:

```sh
wysna schema src/tools.wysna --function summarize --emit-tool -o adapter.wysna --output-format=json-v1
wysna run adapter.wysna --sandbox --allow=arguments,stdio --timeout-ms 5000 -- '{"count":2}'
```

El adaptador valida los tipos, rechaza campos adicionales en los argumentos y
en los structs decodificados, y limita la entrada JSON a 65 536 bytes, 32
niveles y 4096 nodos. La salida incluye la identidad y la versión del contrato:

```json
{"schema_version":"wysna.agent-tool-result/1","id":"ops.summarize","version":1,"status":"ok","value":3}
```

`status` puede ser `ok`, `error` para un valor de una unión de error, o
`invalid_input` para un fallo de validación. `encoding_error` indica que
el resultado no pudo serializarse como JSON. Los fallos de ejecución del
proceso se manejan por separado. El exportador comprueba el adaptador
con los permisos `arguments` y `stdio`, y exige una función pública, pura,
síncrona y concreta. La inicialización del módulo también debe estar libre
de efectos sobre el entorno. Los parámetros, resultados y errores deben poder
representarse mediante los códecs estándar. Los structs y enums del
contrato deben ser públicos. El archivo generado usa rutas absolutas al
código fuente y se debe regenerar si cambia la ubicación del proyecto.
`-o` exige un archivo nuevo.

### Migrar la visibilidad

Usa `pub` y `priv` para indicar visibilidad explícita. Las declaraciones sin
modificador mantienen sus valores predeterminados. Los archivos con las
grafías anteriores reciben `W0006` y una corrección mecánica:

```sh
wysna fix src/tools.wysna --output-format=json-v1
wysna fix src/tools.wysna --apply --output-format=json-v1
```

## Agent-First Imports & Token Optimization

When generating Wysna code, agents should prefer the selective `from ... use { ... }` syntax over namespace qualification:

```wysna
from std.text use {
    contains,
    to_lower,
    split as text_split,
}
```

- **Token Economy:** Direct symbol importing avoids repetitive `module.` qualifiers, saving ~13%–22% BPE tokens in dense multi-step logic.
- **Canonical Formatting:** Multiple symbols must be enclosed in braces `{ ... }` with trailing commas; single symbols can be unbraced (`from std.text use to_lower`).
- **Disambiguation:** If symbol names clash across imported modules, assign local aliases using `as` (`User as DomainUser`).
- **Bootstrap Namespace Parity:** Selective `from ... use { ... }` imports (including aliases) and whole-module namespace imports (`import "std/json"`) are both supported across Seed and the self-hosted `native-core-v1` compiler.
- **Self-Healing Autofix (`wysna fix`):** Automatically repairs immutable assignment targets (`W3001`: `let` -> `let mut`), misnamed class constructors (`W3004`: `fn ClassName` -> `fn init`), scalar string concatenations (`W2001`: `+ scalar` -> `+ str(scalar)`), high-confidence identifier typos (`W1001`), and common foreign syntax (`&&` -> `and`, binary `||` -> `or`, prefix `!` -> `not`, legacy `public` -> `pub` and `private` -> `priv`; `else if` is also accepted alongside canonical `elif`). Pass `--include-maybe` to also apply tentative repairs such as appending `?` on `W2001` optional/result mismatches (`T?` / `T ! E`) when the enclosing function allows propagation (whereas supplying a fallback via `?? <default>` remains an explicit domain decision).

## Execution Quotas, Effect Cassettes, and First-Class Contracts

To safeguard agent loops against runaway execution and unrepeatable I/O side-effects:

- **Enforce quotas:** Pass `--max-steps=<N>` (instruction budget) and `--max-heap-mb=<N>` (memory budget). Exceeding limits halts execution deterministically with error code `[W4004]`. In native binaries, set `WYSNA_MAX_STEPS` and `WYSNA_MAX_HEAP_BYTES`.
- **Capture and replay effects:** Use `wysna run --record-trace=trace.json <file.wysna>` to capture all external operations (HTTP, clock, filesystem) into a versioned `wysna.effect.trace/1` cassette. Use `--replay-trace=trace.json` to rerun offline with bit-identical deterministic results.
- **Write declarative contracts:** Add `requires` preconditions and `ensures` postconditions (referencing `result`) to function declarations. `wysna schema` automatically reflects them as JSON Schema bounds for LLM tool calling.

## Respect declared limits

- Use `native-cli` for host executables, `wasi-command` for constrained WASI
  commands, and `browser-library` for the generated browser ABI. The capability
  response states the toolchain and runtime each one needs.
- The native SDK currently implements `native-core-v1` source checking, MIR/C99
  emission, native build/run/test/fmt, Agent-First tools (`graph`, `inspect`, `patch`,
  `explain`, `schema`, `fix`, `api`, `capabilities`, `skills`, `mcp`, `project new|lock|check|build|run|test`),
  and Stage-3 native `lsp` without Python. It builds standalone WASI commands with
  `build --target=wasi`. Seed provides WASI project workflows, execution, and
  library-mode browser exports.
- Effect reports and capability declarations help review programs; `wysna run --allow/--deny` enforces static pre-execution gates (`W5006`) and Seed `wysna build --sandbox` (with `--allow/--deny`) compiles `WYSNA_DENY_CAP_*` runtime checks (`W5006`), though neither replaces an OS kernel sandbox for arbitrary native C extensions.
- AI authoring and self-healing repair benchmarks live in `bench/ai_eval/`
  (`harness.py` and `token_audit.py`); outside this evaluation suite, Wysna
  has not been measured against other languages for general AI-authored
  program quality.

See [project workflows](PROJECTS.md), [native SDK](NATIVE_SDK.md),
[effect contracts](EFFECTS.md), and [Seed distribution](SEED_DISTRIBUTION.md) for
the complete interfaces and limitations.

## Impacto de un símbolo en el proyecto

Consulta los destinos y pruebas declarados desde el manifiesto:

```sh
wysna graph --manifest examples/shared-browser-app/wysna.toml --symbol quote_validation_code --rel impact
```

Agrega `--output-format=json-v1` para obtener el resultado `wysna.project-impact/1`.
En MCP, usa `wysna_graph` con `manifest`, `symbol` y `relation: "impact"`.

Cada entrada incluye `impact_status`. `affected` trae una cadena de relaciones hasta
el símbolo, que puede incluir `references_function` si la función se pasa como valor.
`uncertain` indica que hay una frontera sin resolver, como una llamada indirecta,
JavaScript o código C externo. `no-static-path` indica que el grafo no encontró una
ruta ni registró fronteras para esa entrada. El campo `affected` conserva su valor
booleano para clientes existentes.

En una biblioteca web, el análisis parte de sus funciones públicas. Para distinguir
símbolos homónimos, usa su ID cualificado.

## Planificar pruebas antes de ejecutar

Consulta `wysna skills project-tests --output-format=json-v1`. Lista primero con
la selección definitiva y exige su `result.plan_hash` mediante
`--expect-plan-hash`. Lee `result.cases[].failure_kind` y `diagnostics`; conserva
los reportes y verifica los SHA-256 de los artefactos. Usa `--discover` de forma
explícita y `--case` con el ID exacto. La [guía de proyectos](PROJECTS.md#pruebas-para-agentes-de-ia)
define límites, cancelación, fixtures y alcance del hash.
