# 08 · Calidad y Experiencia de Desarrollador (DX): Bugs, Soundness, Docs y Quick Wins

> Auditoría histórica de la línea base `a2549fa`. Los problemas y mediciones describen ese snapshot; los estados de implementación y el ciclo vigente se consultan en [00-INDEX.md](00-INDEX.md) y [12-SIGUIENTE-CICLO-LENGUAJE.md](12-SIGUIENTE-CICLO-LENGUAJE.md).

Todos los ítems con esfuerzo **S (<1 día)**, riesgo **bajo** y **sin necesidad de aprobación** llevan explícitamente la etiqueta `[QUICK-WIN]`.

## 1. Backlog Categorizado de Calidad y DX (`DX-01` .. `DX-07`)

### `DX-01` · `[QUICK-WIN]` Sincronizar y reparar los snippets rotos y firmas falsas en `WYSNA_SINTAXIS.md`, `docs/STDLIB.md` y `docs/PROJECTS.md`

- **Prioridad:** P0
- **Decisión:** INVEST
- **Evidencia:** Discrepancias verificadas en Fase 1 y Fase 2 (`02-ESTADO-REAL.md` §6):
  - **D01:** [WYSNA_SINTAXIS.md:660-664](../../WYSNA_SINTAXIS.md#L660-L664) usa `text.trim()`, `clean.split(" ")`, `clean.to_upper()`, `clean.starts_with("Wys")` que fallan con `W3002` ([repro_str_methods.txt](evidence/repro_str_methods.txt)).
  - **D02:** [WYSNA_SINTAXIS.md:674](../../WYSNA_SINTAXIS.md#L674) usa `dict_val.remove("a")` que falla con `W3002` ([repro_dict_remove.txt](evidence/repro_dict_remove.txt)).
  - **D03:** [docs/STDLIB.md:66](../STDLIB.md#L66) documenta `io.read_line() -> str?`, `io.read_all() -> str`, `io.flush() -> bool` cuando las firmas reales en `stdlib/io.wysna` son `read_line() -> Line ! IoError`, `read_all() -> str ! IoError` y `flush() -> unit ! IoError` ([api_std_io.txt](evidence/api_std_io.txt)).
  - **D04:** [docs/STDLIB.md:106](../STDLIB.md#L106) documenta `http.batch_get(urls: list[str], max_concurrency: int = 8) -> list[Response ! HttpError]` cuando la firma real en `stdlib/http.wysna:371` es `batch_get(urls: list[str]) -> list[BatchItemResult]` ([api_std_http.txt](evidence/api_std_http.txt)).
  - **D11:** [docs/PROJECTS.md:24-34](../PROJECTS.md#L24-L34) muestra `language = "0.7"` y `compiler = "0.7.0"`, que falla con `[W5002] package.language must be exactly '0.8' for this compiler` ([check_v07_manifest.json](evidence/prog_c_wasm/check_v07_manifest.json)).
  Nivel: **verificado**.
- **Problema:** La primera experiencia de cualquier desarrollador o agente con Wysna es copiar los bloques de código de `WYSNA_SINTAXIS.md`, `docs/STDLIB.md` y `docs/PROJECTS.md`. Hoy esos tres documentos contienen ejemplos y firmas que no compilan o no existen en el código fuente, destruyendo la confianza en la documentación y provocando errores de compilación inmediatos en la Fase 2.
- **Propuesta:**
  1. Corregir en `docs/STDLIB.md:66,106` las firmas de `std/io` (`Line ! IoError`, `str ! IoError`, `unit ! IoError`) y `std/http` (`batch_get(urls: list[str]) -> list[BatchItemResult]` y `batch_get_all(urls: list[str]) -> list[Response] ! HttpError`).
  2. Actualizar `docs/PROJECTS.md:30-33` a `language = "0.8"` y `compiler = "0.8.0"`.
  3. Alinear `WYSNA_SINTAXIS.md:660-675` con los métodos realmente soportados en el commit actual (o una vez aterrizados **`LANG-04`** y **`GAP-03`**) y añadir un test automatizado en `tests/unit/test_documentation_examples.py` que extraiga y verifique con `wysna check` todos los bloques ```` ```wysna ```` completos de `README.md`, `WYSNA_SINTAXIS.md`, `SPEC.md` y `docs/*.md`.
- **Alternativas:**
  1. *No hacer nada:* mantener ejemplos rotos en los manuales principales del repositorio. Inaceptable (`P0`).
  2. *Resolverlo con lo que ya existe:* corregir los textos de los 3 archivos Markdown y agregar el validador de bloques en `tests/unit/`.
- **Impacto (1–5):** Usuario: **5** | Agentes: **5** | Reducción de complejidad: **3**.
- **Esfuerzo:** **S (<0.5 días)** `[QUICK-WIN]`. Supuestos: edición directa de 3 archivos Markdown + ~60 líneas en un test extractor de bloques `.wysna`.
- **Riesgo:** **Bajo.**
- **Compatibilidad y migración:** Sin impacto en código existente.
- **Componentes afectados:** `WYSNA_SINTAXIS.md`, `docs/STDLIB.md`, `docs/PROJECTS.md`, `tests/unit/`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** Todas las firmas en `docs/STDLIB.md` coinciden 1 a 1 con la salida de `./wysna api --output-format=json-v1`, el manifiesto de ejemplo de `docs/PROJECTS.md` pasa `wysna project check`, y todos los bloques ejecutables de `WYSNA_SINTAXIS.md` pasan `wysna check` en Seed y Selfhost.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

### `DX-02` · `[QUICK-WIN]` Eliminar las 15 afirmaciones obsoletas de `v0.5.0–v0.7.0` en `SPEC.md`, `docs/KNOWN_LIMITS.md`, `docs/AI_AGENT_GUIDE.md`, `docs/TYPE_SYSTEM_DESIGN.md` y limpiar archivos huérfanos

- **Prioridad:** P1
- **Decisión:** REMOVE
- **Evidencia:** Discrepancias verificadas `D05`, `D09`, `D10` en `02-ESTADO-REAL.md` §6:
  - [SPEC.md:3,25,146,149,423,459,488,708](../../SPEC.md#L3): dice definir `0.7.0`; omite `pub` y `extern` de la lista de keywords en §2; duplica el párrafo de `tuple[T1, T2, ...]` en líneas 146 y 149; afirma en línea 423 que `class` y tipos genéricos son rechazados en `T ! E1 | E2`; afirma en líneas 459 y 488 que `refcount` no usa atómicos y que no existe recolector de ciclos.
  - [docs/TYPE_SYSTEM_DESIGN.md:120-121,141,173](../TYPE_SYSTEM_DESIGN.md#L120-L121): afirma que `tuple` tiene `is_valid_dict_key: false` y que `class`/genéricos están fuera de `ErrorUnion`.
  - [docs/KNOWN_LIMITS.md:103,159,237](../KNOWN_LIMITS.md#L103), [docs/AI_AGENT_GUIDE.md:7,83-84,140](../AI_AGENT_GUIDE.md#L7), [docs/ENGINE_PARITY_MATRIX.md:57](../ENGINE_PARITY_MATRIX.md#L57), [docs/MIR_DESIGN.md:28](../MIR_DESIGN.md#L28), [docs/SEED_DISTRIBUTION.md:103](../SEED_DISTRIBUTION.md#L103), [docs/FULL_LANGUAGE_CAPABILITIES_AND_DEBT_AUDIT.md:13,267,295,333,484,487](../FULL_LANGUAGE_CAPABILITIES_AND_DEBT_AUDIT.md#L13): contradicen a `README.md` y `CHANGELOG.md` sobre qué comandos están en el Native SDK (`project new`, `project lock`, `schema`, `fix`, `mcp` con 11 tools, `interface Foo[T]`).
  - Archivo sin seguimiento `NEXT_STEPTS_AF.md` (`90,457` bytes en la raíz del repo, reportado por `git status`).
  Nivel: **verificado**.
- **Problema:** Durante los ciclos `v0.6.0` → `v0.7.0` → `v0.8.0`, se actualizaron secciones nuevas de los documentos sin borrar las frases antiguas en los mismos archivos. Como resultado, un mismo archivo (`docs/KNOWN_LIMITS.md` o `docs/FULL_LANGUAGE_CAPABILITIES_AND_DEBT_AUDIT.md`) afirma en un párrafo que `interface Foo[T]` y `wysna.lock` están soportados en Selfhost y tres párrafos más abajo afirma que siguen excluidos con `W2013`.
- **Propuesta:**
  1. Actualizar `SPEC.md`, `docs/TYPE_SYSTEM_DESIGN.md`, `docs/KNOWN_LIMITS.md`, `docs/AI_AGENT_GUIDE.md`, `docs/ENGINE_PARITY_MATRIX.md`, `docs/MIR_DESIGN.md` y `docs/SEED_DISTRIBUTION.md` eliminando las 15 afirmaciones obsoletas listadas arriba.
  2. Archivar o eliminar el borrador local sin seguimiento `NEXT_STEPTS_AF.md` de la raíz del repositorio para mantener `git status` limpio.
- **Alternativas:**
  1. *No hacer nada:* dejar que la especificación formal (`SPEC.md`) contradiga al propio compilador y a `README.md`.
  2. *Resolverlo con lo que ya existe:* limpiar los párrafos obsoletos en un PR de higiene documental.
- **Impacto (1–5):** Usuario: **4** | Agentes: **5** (evita que agentes que leen `SPEC.md` o `AI_AGENT_GUIDE.md` reciban instrucciones contradictorias) | Reducción de complejidad: **4**.
- **Esfuerzo:** **S (<0.5 días)** `[QUICK-WIN]`. Supuestos: edición puntual de las líneas identificadas en 7 archivos Markdown.
- **Riesgo:** **Bajo.**
- **Compatibilidad y migración:** Sin impacto en código.
- **Componentes afectados:** `SPEC.md`, `docs/TYPE_SYSTEM_DESIGN.md`, `docs/KNOWN_LIMITS.md`, `docs/AI_AGENT_GUIDE.md`, `docs/ENGINE_PARITY_MATRIX.md`, `docs/MIR_DESIGN.md`, `docs/SEED_DISTRIBUTION.md`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** Ninguno de los documentos en `docs/` ni `SPEC.md` contiene referencias obsoletas a `"0.7.0"` como versión actual, ni afirma que `tuple` no puede ser clave de `dict`, ni afirma que `project new`/`project lock`/`interface Foo[T]` faltan en el Native SDK `0.8.0`.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

### `DX-03` · `[QUICK-WIN]` Excluir `README.md`, `SPEC.md` y `docs/*.md` del hash `source_fingerprint()` en `tools/ci_conformance.py`

- **Prioridad:** P1
- **Decisión:** SIMPLIFY
- **Evidencia:** [tools/ci_conformance.py:20-43](../../tools/ci_conformance.py#L20-L43) y salida verificada en Fase 1 en [ci_conformance_matrix_check.txt](evidence/ci_conformance_matrix_check.txt) (`Warning: no verified run for current source tree; STALE: source/fixture hashes or commit changed: .github/workflows/ci.yml, README.md, SPEC.md`). Nivel: **verificado**.
- **Problema:** `source_fingerprint()` en `tools/ci_conformance.py:30-35` incluye dentro del hash de entradas del compilador a `SPEC.md`, `README.md`, `docs/STDLIB.md`, `docs/OWNERSHIP_LIFETIME.md`, `docs/KNOWN_LIMITS.md`, `docs/SERVICE_FOUNDATIONS.md` y `.github/workflows/ci.yml`. Como consecuencia, editar una sola tilde en `README.md` o `SPEC.md` invalida `summary.json` y hace que `python3 tools/ci_conformance.py --matrix` marque toda la matriz de paridad de los 4 motores (`209/209`) como **`STALE`** ([ci_conformance_matrix_check.txt](evidence/ci_conformance_matrix_check.txt)), obligando a re-ejecutar las `209 × 4` compilaciones completas del corpus solo por un cambio de documentación!
- **Propuesta:** Limitar `source_fingerprint()` en `tools/ci_conformance.py:23-36` exclusivamente a las entradas que afectan el comportamiento compilado o ejecutado del corpus: `src/`, `runtime/`, `selfhost/src/`, `stdlib/`, `tests/`, `examples/`, `packaging/`, `main.py`, `wysna`, `run_conformance.py` y `pyproject.toml`, excluyendo archivos Markdown (`*.md`) y workflows de GitHub.
- **Alternativas:**
  1. *No hacer nada:* que cada commit de documentación deje `ci_conformance.py --matrix` en estado `STALE`.
  2. *Resolverlo con lo que ya existe:* re-ejecutar `python3 tools/ci_conformance.py --profile=release` (varios minutos de compilación C) después de cada cambio en un archivo `.md`. Ineficiente e innecesario.
- **Impacto (1–5):** Usuario: **2** | Agentes: **3** | Reducción de complejidad: **4**.
- **Esfuerzo:** **S (<0.2 días)** `[QUICK-WIN]`. Supuestos: eliminar los 6 nombres `.md` y `.github/workflows/ci.yml` de la tupla en `tools/ci_conformance.py:30-34`.
- **Riesgo:** **Bajo.** Cualquier cambio real en el compilador, runtime, stdlib o tests sigue invalidando el hash.
- **Compatibilidad y migración:** 100% compatible.
- **Componentes afectados:** `tools/ci_conformance.py`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** Editar un archivo `.md` no invalida `source_fingerprint()` en `tools/ci_conformance.py`, mientras que editar cualquier archivo en `src/`, `runtime/`, `selfhost/`, `stdlib/` o `tests/` sí lo invalida.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

### `DX-04` · `[QUICK-WIN]` Soportar evaluación inline `wysna run -c '<code>'` / `wysna check -c '<code>'` y documentar el contrato JSON del worker de `wysna service serve`

- **Prioridad:** P2
- **Decisión:** INVEST
- **Evidencia:** Fricción real en Fase 2:
  1. Al intentar ejecutar `./wysna run -c 'let text = ...'` (o comprobar cómo `--check` en `src/tooling/CLI.py:391` reserva `-c` como alias corto oculto de `--check`!), `CLI.py` interpretó el código como ruta de archivo y falló con `FileNotFoundError: No such file or directory: '\nlet text = ...'`.
  2. Al construir el Programa B (`prog_b_api.wysna`) para `wysna service serve`, ni `docs/SERVICE_FOUNDATIONS.md:72-82` ni `./wysna service --help` ([prog_b_service_help.txt](evidence/prog_b_service_help.txt)) documentaban el esquema JSON del archivo de entrada (`method`, `path`, `headers`, `body`) ni que `body` en el JSON de salida `{"status": 200, "body": ...}` debe ser un `json.Value` y no un `str` serializado (lo que nos produjo doble codificación `"{\"ok\":true}"` en la primera corrida de [prog_b_run.txt](evidence/prog_b_run.txt)!).
  3. Además, el comando `service` ni siquiera aparece en la salida de `wysna capabilities --output-format=json-v1` ([capabilities.json](evidence/capabilities.json)).
  Nivel: **verificado**.
- **Problema:** Tanto desarrolladores como agentes de IA prueban expresiones rápidas en la terminal con `-e 'print(1 + 2)'` (estilo Node/Perl/Ruby) o `-c` (estilo Python/sh) sin querer crear archivos temporales en disco. Y cuando usan `wysna service serve`, tienen que adivinar el contrato JSON de entrada/salida o leer `src/tooling/ServiceHost.py`.
- **Propuesta:**
  1. Añadir `--eval <CODE>` / `-e <CODE>` en `wysna run` y `wysna check` (`src/tooling/CLI.py` y `packaging/native/driver.c`), ejecutando `run_pipeline(source_code=CODE, file_name="<eval>")` en memoria sin archivo temporal.
  2. Documentar en `docs/SERVICE_FOUNDATIONS.md:72-80` y en `wysna service --help` el esquema JSON exacto de request (`{"method": str, "path": str, "headers": dict[str, str], "body": str?}`) y de response (`{"status": int, "body": json.Value}`).
- **Alternativas:**
  1. *No hacer nada:* obligar a crear archivos `.wysna` temporales para cada prueba de 1 línea en la terminal.
  2. *Resolverlo con lo que ya existe:* usar `wysna repl` con pipe o `python3 -c` con `tempfile`. Más lento e incómodo en automatización CLI.
- **Impacto (1–5):** Usuario: **4** | Agentes: **5** (permite one-liners `./wysna run -e 'print(1 + 2)'` sin crear basura en el workspace) | Reducción de complejidad: **2**.
- **Esfuerzo:** **S (<0.5 días)** `[QUICK-WIN]`. Supuestos: ~20 líneas en `src/tooling/CLI.py` y `docs/SERVICE_FOUNDATIONS.md`.
- **Riesgo:** **Bajo.** `run_pipeline` ya soporta `file_name="<stdin>"` con `source_code` en memoria (`src/tooling/SourceCommand.py:107,545`).
- **Compatibilidad y migración:** 100% aditivo (usando `-e` / `--eval` se preserva `-c` como alias histórico de `--check`).
- **Componentes afectados:** `src/tooling/CLI.py`, `docs/SERVICE_FOUNDATIONS.md`, `src/tooling/ServiceCLI.py`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** `./wysna run -e 'print(40 + 2)'` imprime `42` y sale con código `0`; `./wysna check -e 'let x: int = "bad"' --output-format=json-v1` devuelve `W2001` sobre `"<eval>"`.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

### `DX-05` · Mejorar diagnósticos de runtime en panics (`W4001`..`W4005`): incluir traza de llamadas (`call stack`) y valores en violaciones de contratos `[W4005]`

- **Prioridad:** P1
- **Decisión:** INVEST
- **Evidencia:** [docs/KNOWN_LIMITS.md:123-137](../KNOWN_LIMITS.md#L123-L137), [runtime/wysna_runtime_core.h:279-288](../../runtime/wysna_runtime_core.h#L279-L288), [runtime/wysna_runtime_builtins.h:680-738](../../runtime/wysna_runtime_builtins.h#L680-L738). Nivel: **verificado**.
- **Problema:** Cuando un binario nativo C99 falla en runtime con `[W4001]` (división por cero), `[W4002]` (índice o clave de diccionario no encontrada: `d[k]`), `[W4004]` (cuota excedida) o `[W4005]` (violación de contrato `requires`/`ensures`), el runtime solo imprime **una línea única** con la última posición `wysna_g_source_file:wysna_g_source_line:wysna_g_source_col` sin mostrar qué cadena de funciones llamó a la operación fallida (por ejemplo, si `d[k]` falló dentro de un helper llamado desde 15 sitios distintos, el desarrollador o agente no sabe quién fue el caller!). Además, en `[W4005]`, el mensaje solo muestra la expresión del contrato (`requires amount > 0`) sin el nombre de la función o struct.
- **Propuesta:** Mantener en `WysnaRuntimeContext` (`runtime/wysna_runtime_core.h:177`) un pequeño buffer circular o pila ligera de marcos activos (`const char* call_stack_fn[64]`, `const char* call_stack_file[64]`, `int32_t call_stack_line[64]`, `int32_t call_depth`) actualizado al entrar/salir de funciones generadas en C99 (o en modo `--profile=dev`), de modo que `wysna_runtime_fail` y `wysna_builtin_contract_assert_at` impriman las últimas $K$ funciones de la pila de llamadas en `stderr` debajo de la línea canónica `[W400x] RuntimeError: ...` (preservando intacta la primera línea para compatibilidad con todos los tests `# RUNTIME_PANIC: W400x`).
- **Alternativas:**
  1. *No hacer nada:* seguir depurando panics en programas multi-función sin stack trace.
  2. *Resolverlo con lo que ya existe:* compilar con `--profile=dev` y correr el binario dentro de `lldb` / `gdb`. Muy lento para flujos automatizados y agentes de IA.
- **Impacto (1–5):** Usuario: **5** | Agentes: **5** (un agente recibe inmediatamente la traza `at parse_row (cli.wysna:31) <- run_cli (cli.wysna:97)` en `stderr`) | Reducción de complejidad: **2**.
- **Esfuerzo:** **M (2 días)**. Supuestos: ~40 líneas en `runtime/wysna_runtime_core.h` / `wysna_runtime_builtins.h`, ~30 líneas en `src/mir/CCodeGen.py` y `selfhost/src/08_mir_c99.wysna`, y ~15 líneas en `src/runtime/Interpreter.py`.
- **Riesgo:** **Bajo.** La primera línea de `stderr` (`[W400x] ...`) se mantiene idéntica, por lo que `_assert_native_outcome` (`panic_code in res.stderr`) y todos los tests de pánico existentes siguen pasando sin cambios.
- **Compatibilidad y migración:** 100% retrocompatible con el contrato `# RUNTIME_PANIC: W400x`.
- **Componentes afectados:** `runtime/wysna_runtime_core.h`, `runtime/wysna_runtime_builtins.h`, `src/mir/CCodeGen.py`, `selfhost/src/08_mir_c99.wysna`, `src/runtime/Interpreter.py`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** Provocar un panic `W4002` o `W4005` a 3 niveles de profundidad de llamada imprime en `stderr` la línea `[W400x] RuntimeError: ...` seguida de los 3 marcos `at <fn> (<file>:<line>)` tanto en el intérprete como en el binario C99.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

### `DX-06` · Reorganizar `tests/unit/` por dominio funcional en lugar de códigos de hitos históricos (`test_c03_*`, `test_d01_*`, `test_phase1_*`, `test_audit_*`)

- **Prioridad:** P2
- **Decisión:** SIMPLIFY
- **Evidencia:** Listado de `tests/unit/` (`118` archivos, `25,101` líneas; ver `src/run_conformance.py:33-44,580-599` y `docs/SCOPE.md:80-82`: *"Consolidating milestone-named tests requires preserving their unique cases and assertions in feature tests. Do not discard a regression merely because its original audit is complete"*). Nivel: **verificado**.
- **Problema:** En `tests/unit/` hay `118` archivos Python acumulados por cada PR o auditoría histórica con nombres opacos como `test_c03_generic_specialization.py`, `test_c04_adt_layout.py`, `test_c05_types_inference.py`, `test_c06_default_backend.py`, `test_d01_parity_matrix.py`, `test_d04_semantic_fuzzing.py`, `test_d05_telemetry_benchmarks.py`, `test_phase1_sandbox_and_incremental.py`, `test_phase2_selfhost_generics_and_tooling.py`, `test_phase4_native_lsp.py`, `test_audit_remediation.py`, `test_audit_completion.py`, `test_audit_readiness.py`, `test_audit_findings.py`. Un desarrollador que modifica el typechecker o el LSP no sabe qué archivo de test corresponde a su subsistema sin hacer `grep` por los 118 archivos.
- **Propuesta:** Respetando estrictamente la protección de `docs/SCOPE.md:80-82` (**conservar el 100% de los casos y aserciones únicas, cero pérdida de cobertura**), agrupar o mapear las suites de `tests/unit/` bajo nombres de dominio claros (`test_checker_*.py`, `test_mir_*.py`, `test_selfhost_*.py`, `test_lsp_*.py`, `test_tooling_*.py`, `test_runtime_*.py`) y exponer en `./wysna test --suite=<checker|mir|selfhost|tooling|stdlib>` la ejecución selectiva por subsistema.
- **Alternativas:**
  1. *No hacer nada:* seguir añadiendo `test_pr165_*.py`, `test_v09_phase1_*.py` hasta superar los 200 archivos con nombres cronológicos.
  2. *Resolverlo con lo que ya existe:* añadir primero el selector `--suite=<dominio>` en `TestRunner.py` / `run_conformance.py` sin siquiera renombrar archivos físicos si algún script de CI los invoca por nombre de módulo!
- **Impacto (1–5):** Usuario: **2** | Agentes: **4** | Reducción de complejidad: **4**.
- **Esfuerzo:** **M (1–2 días)**. Supuestos: mapear los 118 módulos de `tests/unit/` en suites por dominio en `src/tooling/TestRunner.py` y verificar en `.github/workflows/ci.yml` los nombres invocados explícitamente.
- **Riesgo:** **Bajo** si se mantienen los nombres importados por `.github/workflows/ci.yml` o se actualizan en el mismo commit.
- **Compatibilidad y migración:** Cero tests eliminados; el conteo total de assertions antes y después debe ser idéntico.
- **Componentes afectados:** `tests/unit/`, `src/run_conformance.py`, `src/tooling/TestRunner.py`, `.github/workflows/ci.yml`.
- **Depende de:** `PERF-04`.
- **Criterio de aceptación:** `./wysna test --suite=checker` o `--suite=tooling` ejecuta en `< 5 s` todos los tests unitarios del dominio correspondiente, y el número total de tests ejecutados en CI es mayor o igual al baseline (`843+`).
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

### `DX-07` · `[QUICK-WIN]` Limpiar artefactos generados `src/wysna.egg-info` del árbol fuente y añadir diagnóstico claro en `install.sh` / `wysna doctor` para dependencias opcionales (`libcurl`, `sqlite3`)

- **Prioridad:** P2
- **Decisión:** SIMPLIFY
- **Evidencia:** [tree_inventory.txt](evidence/tree_inventory.txt) (`src/wysna.egg-info`: `6` archivos, `869` líneas en `src/`), [Makefile:26-29](../../Makefile#L26-L29) (`clean: rm -rf .wysna_cache build dist *.egg-info` — no limpia `src/*.egg-info` porque `package-dir` en `pyproject.toml:36` es `"" = "src"`!), [install.sh:1-45](../../install.sh#L1-L45). Nivel: **verificado**.
- **Problema:**
  1. Como `pyproject.toml:36` define `[tool.setuptools.package-dir] "" = "src"`, `pip install -e .` crea `src/wysna.egg-info/`, pero `Makefile:27` (`rm -rf *.egg-info`) busca en la raíz en lugar de `src/*.egg-info`.
  2. Cuando un usuario en un checkout de código fuente ejecuta `./wysna capabilities`, no existe un comando rápido que verifique en vivo si `cc`, `curl-config` (`libcurl >= 8.3`), `sqlite3` y `pycurl` están realmente presentes y operativos en su máquina (`wysna doctor` solo existe dentro del bundle empaquetado de `SEED_DISTRIBUTION.md:75`).
- **Propuesta:**
  1. Actualizar `Makefile:27` para limpiar `src/*.egg-info`.
  2. Permitir que `./wysna capabilities` (o `./wysna doctor` también en un source checkout) compruebe en vivo la presencia de `cc`, `curl-config`, `sqlite3` y el binario `.wysna_cache/wysna_selfhost`, reportando su estado real (`available: true/false`).
- **Alternativas:**
  1. *No hacer nada:* mantener `Makefile clean` incompleto y `wysna doctor` inexistente en checkouts de desarrollo.
  2. *Resolverlo con lo que ya existe:* correr `which cc curl-config` manualmente.
- **Impacto (1–5):** Usuario: **3** | Agentes: **3** | Reducción de complejidad: **2**.
- **Esfuerzo:** **S (<0.5 días)** `[QUICK-WIN]`. Supuestos: 1 línea en `Makefile` y ~25 líneas en `src/tooling/ApiIndex.py` (`run_capabilities_command`).
- **Riesgo:** **Bajo.**
- **Compatibilidad y migración:** 100% aditivo en `wysna.capabilities/1`.
- **Componentes afectados:** `Makefile`, `src/tooling/ApiIndex.py`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** `make clean` elimina `src/wysna.egg-info`, y `./wysna capabilities --output-format=json-v1` incluye el estado detectado en el host para `c99-compiler` y `libcurl`.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

## 2. Hipótesis (`RESEARCH`)

### Propuesta diferida de inspección de WVal en LLDB/GDB

El informe [DX-R01](research/DX-R01-line-directives.md) comprueba localización de
código fuente mediante `#line`. La inspección del contenido de `WVal` se considera
una mejora posterior de depuración.

- **Observación:** Al depurar un binario C99 o LLVM con `lldb` o `gdb`, todas las variables no escalares (`str`, `list`, `dict`, `struct`, `enum`, `T?`, `T ! E`) se muestran como `WVal { bits = 18444914486360932352 }` debido a la codificación NaN-boxed de ABI v3 (`runtime/wysna_runtime_core.h:238-270`).
- **Cómo falsificarla:** Escribir un script de 40 líneas de Python para LLDB (`tools/lldb_wysna.py`) que decodifique los 16 bits superiores (`bits >> 48`) o llame a `wysna_to_cstr(val)` y medir si simplifica el diagnóstico de bugs de runtime/FFI durante el ciclo. Si rara vez se usa `lldb` porque los diagnósticos `[W400x]` con stack trace (**`DX-05`**) bastan, no se empaqueta en el SDK.
