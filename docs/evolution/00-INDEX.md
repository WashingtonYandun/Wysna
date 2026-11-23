# 00 — Índice Maestro del Plan de Evolución de Wysna (`v0.8.0` → Ciclo Siguiente)

- **Repositorio:** `https://github.com/WashingtonYandun/Wysna`
- **Rama y commit evaluado:** `docs/evolution-plan` @ `a2549fa01294b20f3b893542706015ffe00869f3` (`v0.8.0`)
- **Línea base verificada:** `./wysna test` (`843/843` suites en verde, `548.2 s`), `test_architecture_contract` (`17/17` en verde), binario nativo `.wysna_cache/wysna_selfhost` compilado (`3.45 MB`).
- **Pregunta rectora:** *¿Qué hacemos, en qué orden y qué dejamos de hacer para que Wysna sea un lenguaje mejor y no solo un compilador más complejo?*

---

## Estado vigente de la arquitectura y el runtime

PR #176 estableció la arquitectura híbrida; PR #177 integró R0 en `ba0953e`
con 39/39 controles correctos. La alineación de versión y migración 0.9 se prepara
en un cambio separado. Consultar primero
[ADR-05](ADR-05-arquitectura-hibrida-tooling-selfhost.md) y el
[ciclo y su evidencia](12-SIGUIENTE-CICLO-LENGUAJE.md#arquitectura-híbrida-integrada-y-siguiente-ciclo).
`SIMP-R01` ya tiene una decisión implementada: lenguaje con oráculo de cuatro
motores y tooling en Selfhost, con paquetes exclusivamente nativos. Los bloques
1–4 tienen código y regresiones; CI e integración se verifican por separado.
Las mediciones con modelos continúan aplazadas por decisión del mantenedor.

El siguiente trabajo está en [13-RUNTIME-AGENT-FIRST.md](13-RUNTIME-AGENT-FIRST.md)
y [ADR-06](ADR-06-metadatos-tipados-y-runtime-agentes.md): metadatos propios tipados,
introspección y reparación de patrones, seguidos de transferencia segura entre
workers, grupos estructurados, tipos de ancho explícito y portabilidad comprobada.
R0 está integrado; R1–R4 siguen pendientes y tienen criterios de cierre.

## Estado histórico de cierre y siguiente ciclo

La tabla de 37 ítems conserva el backlog de la auditoría inicial y sus marcas de implementación. No es una certificación universal de paridad ni una medición de eficacia con agentes. Los capítulos 01–10 conservan evidencia histórica de `a2549fa`; sus secciones «problema» y «antes» no describen automáticamente el producto actual.

`LANG-07` y los contratos host 49/49 están integrados mediante #171/#172. [v0.8.0](https://github.com/WashingtonYandun/Wysna/releases/tag/v0.8.0) está publicada desde `c37fa4e`. El [PR #173](https://github.com/WashingtonYandun/Wysna/pull/173) reparó trazas LLVM y arranque del benchmark en `main`/`develop` `c49e3d5`; su candidato aprobó CI 39/39 y calificación LLVM 13/13. La CI principal de ese main aprobó 38 jobs con un control exclusivo de PR omitido. El run LLVM extendido del main final conserva su estado independiente en [44980185856](https://github.com/WashingtonYandun/Wysna/actions/runs/44980185856).

El ciclo aprobado está en [12-SIGUIENTE-CICLO-LENGUAJE.md](12-SIGUIENTE-CICLO-LENGUAJE.md): pruebas de proyecto y dependencias reproducibles diseñadas para agentes de IA, compatibilidad, fuzzing y validación de un proyecto real. Sus tareas nuevas se mantienen pendientes hasta tener código y evidencia. Las campañas con modelos siguen aplazadas; los mocks comprueban protocolos, no calidad de autoría ni ahorro de tokens facturados.

`SIMP-05` es el contrato host y su [informe FFI](research/SIMP-05-ffi-catalog.md). `SIMP-R01` investiga herramientas compartidas entre implementaciones. El informe de catálogo se había asociado al ID equivocado; un estudio completado no convierte su propuesta en funcionalidad implementada.

## 1. Mapa de Documentos del Plan

| Archivo | Propósito | Ítems accionables | Hipótesis `RESEARCH` |
| :--- | :--- | :---: | :---: |
| [`00-INDEX.md`](00-INDEX.md) | Foco único, Top 10 priorizado, tabla maestra de los 37 ítems + 7 hipótesis y enlaces a evidencia | — | — |
| [`01-FOCO.md`](01-FOCO.md) | Propuesta de valor en 5 líneas, prioridad estratégica única, alternativas descartadas, congelados y métrica | — | — |
| [`02-ESTADO-REAL.md`](02-ESTADO-REAL.md) | Línea base verificada, arquitectura real, tabla de 19 subsistemas, 8 discrepancias doc↔código y 6 fortalezas `KEEP` | — | — |
| [`03-LENGUAJE.md`](03-LENGUAJE.md) | Evaluación de 9 dimensiones de sintaxis, semántica y sistema de tipos desde la experiencia real de uso | 6 (`LANG-01`..`06`) | 1 (`LANG-R01`) |
| [`04-AGENT-FIRST.md`](04-AGENT-FIRST.md) | Evaluación de 5 dimensiones Agent-First, auditoría de 11 herramientas, seguridad operativa y protocolo A/B falsable | 6 (`AGT-01`..`06`) | 1 (`AGT-R01`) |
| [`05-SIMPLIFICACION.md`](05-SIMPLIFICACION.md) | Matriz de solapamiento (11 pares), decisiones explícitas por subsistema (`KEEP`/`MERGE`/`FREEZE`/`SIMPLIFY`) | 6 (`SIMP-01`..`06`) | 1 (`SIMP-R01`) |
| [`06-BRECHAS.md`](06-BRECHAS.md) | Matriz capacidad × caso de uso (14 capacidades), decisión web basada en evidencia y MVPs por dominio | 6 (`GAP-01`..`06`) | 1 (`GAP-R01`) |
| [`07-RENDIMIENTO.md`](07-RENDIMIENTO.md) | Mediciones reproducibles de compilación, runtime, memoria ARC/CoW, tamaño binario y suite de tests | 6 (`PERF-01`..`06`) | 2 (`PERF-R01`..`R02`) |
| [`08-CALIDAD-DX.md`](08-CALIDAD-DX.md) | Bugs y soundness, fricción DX de Fase 2, ejemplos rotos, trazas de pánico y 5 `[QUICK-WIN]` | 7 (`DX-01`..`07`) | 1 (`DX-R01`) |
| [`09-ROADMAP.md`](09-ROADMAP.md) | Tres hitos secuenciales por dependencia, cola posterior ordenada y tabla de congelados con condición de reapertura | — | — |
| [`10-DECISIONES.md`](10-DECISIONES.md) | 5 ADRs (`ADR-00` a `ADR-04`) para el foco/congelamientos y los 4 ítems con `Requiere aprobación: sí` | — | — |
| [`evidence/`](evidence) | 38 artefactos reproducibles: baseline de tests, benchmarks, Programas A/B/C de Fase 2 y repros aislados | — | — |

---

## 2. Foco Estratégico Único del Próximo Ciclo

> **Foco Único ([`01-FOCO.md`](01-FOCO.md)):** **Confiabilidad de Extremo a Extremo en Scripting y Automatización Tipada Agent-First.**
>
> En lugar de expandir backends experimentales (`--backend=llvm-experimental`), construir un framework HTTP servidor propio o añadir nuevos subcomandos CLI, el próximo ciclo concentra el 100% de la ingeniería en hacer **cero-fricción, 100% consistente entre `Seed` y `Selfhost`, y empíricamente verificable** el flujo donde Wysna ya tiene ventaja real demostrada: **herramientas CLI, automatización de procesos y pipelines de datos tipados (`CSV`/`JSON`/HTTP cliente/SQLite) con contratos `requires`/`ensures`, diagnósticos estructurados `json-v1` y compilación a binario nativo C99 (`3.3x–102x` más rápido que Python 3.13)**.

---

## 3. Top 10 de Ítems Prioritarios (Ordenados por Impacto y Orden de Ejecución)

| # | ID | Título | Categoría | Prioridad | Esfuerzo | Decisión | Por qué está en el Top 10 |
| :---: | :--- | :--- | :--- | :---: | :---: | :---: | :--- |
| **1** | **[`AGT-01`](04-AGENT-FIRST.md)** | Reparar corrupción de strings multilínea (`"""..."""`) y espaciado en el formateador nativo Selfhost | Agent-First (`04`) | `P0` | `M` | `INVEST` | **Bug destructivo:** `wysna fmt --engine=compiler` inserta espacios dentro de literales `"""..."""`, alterando silenciosamente la semántica del programa ([`repro_selfhost_fmt.txt`](evidence/repro_selfhost_fmt.txt)). |
| **2** | **[`LANG-01`](03-LENGUAJE.md)** | Propagar el tipo interno de `ResultType` (`T ! E`) y `OptionalType` (`T?`) hacia literales vacíos `[]` y `{}` en `return` | Lenguaje (`03`) | `P0` | `S` | `INVEST` | Bloqueó los Programas A y B en la primera compilación (`[W2004]` al escribir `return []` en `-> list[T] ! E`, [`prog_a_check_seed.json`](evidence/prog_a_check_seed.json)). |
| **3** | **[`LANG-03`](03-LENGUAJE.md)** | Emitir validación recuperable de contratos `requires` en `wysna schema --emit-codecs` (`Err(JsonError)` en vez de pánico `[W4005]`) | Lenguaje (`03`) | `P0` | `S` | `INVEST` | En el Programa B, un JSON externo inválido abortó el proceso entero con `SIGABRT [W4005]` al construir el struct dentro de `decode_<Struct>` ([`prog_b_run.txt`](evidence/prog_b_run.txt)). |
| **4** | **[`AGT-03`](04-AGENT-FIRST.md)** | Registrar los 8 externs faltantes de `stdlib` en `EffectModel.py` y eliminar falsos positivos `[W5006]` sobre `class` de usuario | Agent-First (`04`) | `P0` | `S` | `INVEST` | `wysna run --deny=network` rechaza cualquier programa que defina una `class` pura o importe `std/http`/`std/crypto` con `[W5006] unknown effect` ([`repro_capability_stdlib.txt`](evidence/repro_capability_stdlib.txt)). |
| **5** | **[`AGT-02`](04-AGENT-FIRST.md)** | Reparar crash `TypeError: severity` en `McpServer.tool_wysna_check` ante warnings `W6001`/`W6002` | Agent-First (`04`) | `P0` | `S` | `INVEST` | Rompe el servidor MCP (`wysna mcp`) cada vez que un agente verifica código con ciclos de referencia (`W6001`) o expresiones descartadas (`W6002`) ([`repro_mcp_cycle_crash.txt`](evidence/repro_mcp_cycle_crash.txt)). |
| **6** | **[`DX-01`](08-CALIDAD-DX.md)** | `[QUICK-WIN]` Corregir ejemplos y firmas falsas en `WYSNA_SINTAXIS.md`, `docs/STDLIB.md` y `docs/PROJECTS.md` + test CI de docs | Calidad y DX (`08`) | `P0` | `S` | `INVEST` | Elimina las trampas documentales (`s.trim()`, `m.remove()`, `http.get()->str`, `language="0.7"`) que causaron los fallos de Fase 2. |
| **7** | **[`PERF-01`](07-RENDIMIENTO.md)** | Eliminar los 4 clones profundos redundantes del AST en `CompilerService.check_source` | Rendimiento (`07`) | `P1` | `S` | `INVEST` | Reduce el tiempo de `wysna check --engine=seed` en **`-55.6%`** (`4,715 ms → 2,095 ms` en `selfhost/compiler.wysna`, [`perf_clone_syntax.txt`](evidence/perf_clone_syntax.txt)). |
| **8** | **[`PERF-04`](07-RENDIMIENTO.md)** | Paralelizar el Paso 11 de `run_conformance.py` y añadir `./wysna test --fast` (`< 15 s`) | Rendimiento (`07`) | `P1` | `S` | `SIMPLIFY` | Reduce la suite completa en `~110 s` y provee un modo `--fast` (`< 15 s` frente a `548 s`) indispensable para desarrollo iterativo. |
| **9** | **[`LANG-04`](03-LENGUAJE.md)** | Implementar los 7 métodos intrínsecos de `str` (`.trim()`, `.split()`, `.to_lower()`, `.to_upper()`, `.starts_with()`, `.ends_with()`, `.replace()`) | Lenguaje (`03`) | `P1` | `M` | `INVEST` | Cumple el contrato prometido en `WYSNA_SINTAXIS.md:660-664` reutilizando las funciones `wysna_host_str_*` ya existentes en `runtime/wysna_rt.h`. |
| **10** | **[`GAP-01`](06-BRECHAS.md)** | Añadir `process.capture(command, args?) -> ProcessOutput ! ProcessError` en `std/process` | Brechas (`06`) | `P1` | `M` | `INVEST` | Desbloquea la automatización real de subprocesos (`stdout`/`stderr`/`status`) sin archivos temporales ni `system(sh -c ...)` inseguro. |

---

## 4. Tabla Maestra de Todos los Ítems del Backlog (37 Ítems Accionables)

| ID | Título | Categoría | Prioridad | Esfuerzo | Decisión | Requiere aprobación | Estado |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: |
| [`LANG-01`](03-LENGUAJE.md) | Propagar el tipo interno de `ResultType` (`T ! E`) y `OptionalType` (`T?`) hacia colecciones vacías (`[]`, `{}`) en `return` | `03-LENGUAJE` | `P0` | `S` | `INVEST` | no | implementado |
| [`LANG-02`](03-LENGUAJE.md) | Unificar reglas de colisión con preludio (`import "std/environment" as env`) y span real entre `Seed` y `Selfhost` | `03-LENGUAJE` | `P1` | `S` | `SIMPLIFY` | no | implementado |
| [`LANG-03`](03-LENGUAJE.md) | Emitir validación recuperable de contratos `requires` en `wysna schema --emit-codecs` (`Err(JsonError)` en vez de pánico `[W4005]`) | `03-LENGUAJE` | `P0` | `S` | `INVEST` | no | implementado |
| [`LANG-04`](03-LENGUAJE.md) | Implementar los 7 métodos intrínsecos de `str` documentados en `WYSNA_SINTAXIS.md:660-664` | `03-LENGUAJE` | `P1` | `M` | `INVEST` | **sí** ([`ADR-01`](10-DECISIONES.md)) | implementado |
| [`LANG-05`](03-LENGUAJE.md) | Canonizar la instanciación de clases a `Class(...)` sobre `new Class(...)` en `wysna fmt` y documentación | `03-LENGUAJE` | `P2` | `S` | `SIMPLIFY` | **sí** ([`ADR-03`](10-DECISIONES.md)) | implementado |
| [`LANG-06`](03-LENGUAJE.md) | Completar la tabla formal de precedencia y asociatividad de operadores en `SPEC.md:239-254` | `03-LENGUAJE` | `P1` | `S` | `SIMPLIFY` | no | implementado |
| [`AGT-01`](04-AGENT-FIRST.md) | Reparar corrupción de strings multilínea (`"""..."""`) y espaciado en el formateador nativo Selfhost (`wysna fmt` / `fix`) | `04-AGENT-FIRST` | `P0` | `M` | `INVEST` | no | implementado |
| [`AGT-02`](04-AGENT-FIRST.md) | Reparar crash `TypeError: severity` en `McpServer.tool_wysna_check` cuando el archivo emite warnings (`W6001`, `W6002`) | `04-AGENT-FIRST` | `P0` | `S` | `INVEST` | no | implementado |
| [`AGT-03`](04-AGENT-FIRST.md) | Sincronizar `EffectModel.py` con `stdlib` (`std/http`, `std/crypto`) y eliminar falsos positivos `[W5006]` sobre `class` de usuario | `04-AGENT-FIRST` | `P0` | `S` | `INVEST` | no | implementado |
| [`AGT-04`](04-AGENT-FIRST.md) | Unificar el contrato `json-v1` (demangling `__wysna_module_*`, `data.expected`/`found` y `graph_hash`) entre `Seed` y `Selfhost` | `04-AGENT-FIRST` | `P1` | `M` | `SIMPLIFY` | no | implementado |
| [`AGT-05`](04-AGENT-FIRST.md) | Corregir `"visibility": "public"` en funciones privadas del módulo raíz y acotar `wysna api <mod>` al módulo consultado | `04-AGENT-FIRST` | `P1` | `S` | `SIMPLIFY` | no | implementado |
| [`AGT-06`](04-AGENT-FIRST.md) | Actualizar `SYSTEM_PROMPT` a `v0.8.0`, separar `raw_pass_at_1` vs `post_fix_pass_at_1` y ampliar `ab_cohort.py` a tareas multi-archivo | `04-AGENT-FIRST` | `P1` | `M` | `INVEST` | no | implementado |
| [`SIMP-01`](05-SIMPLIFICACION.md) | Cachear artefactos semánticos en `CheckedProgram` e instituir test diferencial JSON `Seed ↔ Selfhost` para tooling | `05-SIMPLIFICACION` | `P1` | `M` | `SIMPLIFY` | no | implementado |
| [`SIMP-02`](05-SIMPLIFICACION.md) | Marcar `selfhost/compiler.wysna` como artefacto generado (`linguist-generated=true`) y automatizar su bundle en `Bootstrap.py` | `05-SIMPLIFICACION` | `P2` | `S` | `SIMPLIFY` | no | implementado |
| [`SIMP-03`](05-SIMPLIFICACION.md) | Retirar la fachada `HirCCodeGen` en favor de `CompilerService.emit_c` y renombrar `"Seed HIR C99"` a `"Seed MIR C99"` | `05-SIMPLIFICACION` | `P2` | `S` | `MERGE` | no | implementado |
| [`SIMP-04`](05-SIMPLIFICACION.md) | Congelar (`FREEZE`) la expansión de `--backend=llvm-experimental` y `LLVM SDK`, conservando solo su smoke test MIR | `05-SIMPLIFICACION` | `P2` | `S` | `FREEZE` | no | implementado |
| [`SIMP-05`](05-SIMPLIFICACION.md) | Conservar (`KEEP`) Intérprete y `AST → C99` como oráculos y automatizar test de paridad triple de símbolos `wysna_host_*` | `05-SIMPLIFICACION` | `P2` | `S` | `KEEP` | no | implementado |
| [`SIMP-06`](05-SIMPLIFICACION.md) | Consolidar un único camino canónico en documentación y skills para I/O, entorno, iteración de `dict` y helpers `std/json` | `05-SIMPLIFICACION` | `P2` | `S` | `DEPRECATE` | no | implementado |
| [`GAP-01`](06-BRECHAS.md) | Añadir `process.capture(command, args?) -> ProcessOutput ! ProcessError` en `std/process` | `06-BRECHAS` | `P1` | `M` | `INVEST` | no | implementado |
| [`GAP-02`](06-BRECHAS.md) | Completar operaciones de directorio en `std/fs` (`list_dir`, `is_dir`, `is_file`) y `path.ext` en `std/path` | `06-BRECHAS` | `P1` | `S` | `INVEST` | no | implementado |
| [`GAP-03`](06-BRECHAS.md) | Implementar `dict.remove(key) -> V?` y `dict.clear()` en `dict[K, V]` | `06-BRECHAS` | `P1` | `M` | `INVEST` | **sí** ([`ADR-02`](10-DECISIONES.md)) | implementado |
| [`GAP-04`](06-BRECHAS.md) | Incorporar módulo `std/csv` (RFC 4180) implementado en Wysna puro | `06-BRECHAS` | `P2` | `S` | `INVEST` | no | implementado |
| [`GAP-05`](06-BRECHAS.md) | Consolidar los 3 wrappers C de SQLite (`examples/`) en un paquete local reutilizable y desbloquear `TASK-29`/`TASK-30` | `06-BRECHAS` | `P2` | `M` | `MERGE` | no | implementado |
| [`GAP-06`](06-BRECHAS.md) | Auto-seleccionar el único target de `wysna.toml` y soportar compilación Wasm (`web`/`wasi`) mediante el `zig cc` empaquetado | `06-BRECHAS` | `P2` | `S` / `L` | `SIMPLIFY` | **sí** ([`ADR-04`](10-DECISIONES.md)) | implementado |
| [`PERF-01`](07-RENDIMIENTO.md) | Eliminar los 4 clones profundos redundantes de `CheckedProgram.ast` en `CompilerService.check_source` (`-55.6%` en `wysna check`) | `07-RENDIMIENTO` | `P1` | `S` | `INVEST` | no | implementado |
| [`PERF-02`](07-RENDIMIENTO.md) | Eliminar las 6 ejecuciones redundantes de `mir.Verifier.verify(module)` en `Optimizer.py` y `Ownership.py` (`-44%` en fase MIR) | `07-RENDIMIENTO` | `P1` | `S` | `SIMPLIFY` | no | implementado |
| [`PERF-03`](07-RENDIMIENTO.md) | Portar `optimize_parameter_reads` al lowering MIR de `Selfhost` para eliminar `15,900x` retains redundantes de `str` | `07-RENDIMIENTO` | `P1` | `S` | `INVEST` | no | implementado |
| [`PERF-04`](07-RENDIMIENTO.md) | Paralelizar el Paso 11 de `run_conformance.py` y añadir `./wysna test --fast` (`< 15 s`) | `07-RENDIMIENTO` | `P1` | `S` | `SIMPLIFY` | no | implementado |
| [`PERF-05`](07-RENDIMIENTO.md) | Corregir documentación falsa de `WUnboxedList` y compartir arreglos estáticos de `keys` entre instancias de `WStructBuf` | `07-RENDIMIENTO` | `P2` | `S` | `SIMPLIFY` | no | implementado |
| [`PERF-06`](07-RENDIMIENTO.md) | Activar dead-stripping (`-Wl,-dead_strip` / `--gc-sections`) en `--profile=size` y hacer `bench/steady_state.wysna` autocontenido | `07-RENDIMIENTO` | `P2` | `S` | `SIMPLIFY` | no | implementado |
| [`DX-01`](08-CALIDAD-DX.md) | `[QUICK-WIN]` Corregir ejemplos y firmas falsas en `WYSNA_SINTAXIS.md`, `docs/STDLIB.md` y `docs/PROJECTS.md` + test CI de docs | `08-CALIDAD-DX` | `P0` | `S` | `INVEST` | no | implementado |
| [`DX-02`](08-CALIDAD-DX.md) | `[QUICK-WIN]` Eliminar las 15 afirmaciones obsoletas `v0.5.0–v0.7.0` en `SPEC.md` y `docs/*.md` | `08-CALIDAD-DX` | `P1` | `S` | `REMOVE` | no | implementado |
| [`DX-03`](08-CALIDAD-DX.md) | `[QUICK-WIN]` Excluir `*.md` y `.github/workflows/ci.yml` de `source_fingerprint()` en `tools/ci_conformance.py` | `08-CALIDAD-DX` | `P1` | `S` | `SIMPLIFY` | no | implementado |
| [`DX-04`](08-CALIDAD-DX.md) | `[QUICK-WIN]` Añadir `wysna run -e '<code>'` / `wysna check -e '<code>'` y documentar el esquema JSON de `wysna service serve` | `08-CALIDAD-DX` | `P2` | `S` | `INVEST` | no | implementado |
| [`DX-05`](08-CALIDAD-DX.md) | Incluir call stack trace en pánicos (`W4001`..`W4005`) y contexto de función/struct en violaciones de contrato `[W4005]` | `08-CALIDAD-DX` | `P1` | `M` | `INVEST` | no | implementado |
| [`DX-06`](08-CALIDAD-DX.md) | Agrupar los 42 archivos de `tests/unit/` en suites por dominio (`--suite=checker\|mir\|selfhost\|tooling\|stdlib`) | `08-CALIDAD-DX` | `P2` | `M` | `SIMPLIFY` | no | implementado |
| [`DX-07`](08-CALIDAD-DX.md) | `[QUICK-WIN]` Limpiar `src/*.egg-info` en `Makefile clean` y reportar disponibilidad de `cc`/`libcurl`/`sqlite3` en `wysna capabilities` | `08-CALIDAD-DX` | `P2` | `S` | `SIMPLIFY` | no | implementado |

---

## 5. Tabla de Hipótesis de Investigación (`RESEARCH` — 7 Hipótesis)

| ID | Título | Categoría | Prioridad | Esfuerzo | Decisión | Requiere aprobación | Estado | Reporte |
| :--- | :--- | :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| [`LANG-R01`](03-LENGUAJE.md) | Evaluar en una tarea equivalente si from "std/..." use ... reduce errores frente a import "std/..." as ... | `03-LENGUAJE` | `P3` | `S` | `RESEARCH` | no | tres tareas pareadas con oráculo AST; pruebas sin red; medición real aplazada por falta de recursos | [`AGT-R01-LANG-R01-ab-protocol.md`](research/AGT-R01-LANG-R01-ab-protocol.md) |
| [`AGT-R01`](04-AGENT-FIRST.md) | Medir si wysna patch --symbol reduce errores de edición frente a reemplazo estándar de líneas en archivos de >300 LOC | `04-AGENT-FIRST` | `P2` | `M` | `RESEARCH` | no | piloto prefetch 6/6 frente a 4/6; protocolo específico patch preparado; medición real aplazada | [`AGT-R01-LANG-R01-ab-protocol.md`](research/AGT-R01-LANG-R01-ab-protocol.md) |
| [`SIMP-R01`](05-SIMPLIFICACION.md) | Evaluar herramientas Agent-First compartidas o delegación nativa desde Seed | `05-SIMPLIFICACION` | `P3` | `M` | `RESEARCH` | no | pendiente de medición de duplicación y paridad; no implementado | [hipótesis original](05-SIMPLIFICACION.md) |
| [`GAP-R01`](06-BRECHAS.md) | Evaluar el modelo de concurrencia de hilos o workers aislados frente al runtime ARC actual *(refcounts `__atomic_*` pero contexto global no TLS)* | `06-BRECHAS` | `P3` | `M` | `RESEARCH` | no | completado | [`GAP-R01-concurrency-arc.md`](research/GAP-R01-concurrency-arc.md) |
| [`PERF-R01`](07-RENDIMIENTO.md) | Medir contención CoW (`rc > 1`) y escalabilidad en manipulación de buffers mutables en bucles dentro de `Selfhost` (`1,000`–`100,000` filas) | `07-RENDIMIENTO` | `P2` | `S` | `RESEARCH` | no | completado | [`PERF-R01-cow-contention.md`](research/PERF-R01-cow-contention.md) |
| [`PERF-R02`](07-RENDIMIENTO.md) | Evaluar el impacto en localidad de caché de migrar `WStructBuf` (`void** values`) a `struct` C nominal con campos tipados inline | `07-RENDIMIENTO` | `P3` | `M` | `RESEARCH` | no | completado | [`PERF-R02-struct-layout.md`](research/PERF-R02-struct-layout.md) |
| [`DX-R01`](08-CALIDAD-DX.md) | Evaluar la emisión de directivas `#line <line> "<file>.wysna"` en los backends C99 para depuración a nivel fuente en `lldb`/`gdb` | `08-CALIDAD-DX` | `P3` | `S` | `RESEARCH` | no | completado *(prototipo `--line-directives`)* | [`DX-R01-line-directives.md`](research/DX-R01-line-directives.md) |

---

## 6. Resumen Estadístico del Plan

| Categoría | Archivo | `P0` | `P1` | `P2` | Total Accionables | Hipótesis `RESEARCH` | Requieren Aprobación |
| :--- | :--- | :---: | :---: | :---: | :---: | :---: | :---: |
| **Lenguaje** | [`03-LENGUAJE.md`](03-LENGUAJE.md) | 2 | 3 | 1 | **6** | 1 (`LANG-R01`) | 2 (`LANG-04`, `LANG-05`) |
| **Agent-First** | [`04-AGENT-FIRST.md`](04-AGENT-FIRST.md) | 3 | 3 | 0 | **6** | 1 (`AGT-R01`) | 0 |
| **Simplificación** | [`05-SIMPLIFICACION.md`](05-SIMPLIFICACION.md) | 0 | 1 | 5 | **6** | 1 (`SIMP-R01`) | 0 |
| **Brechas** | [`06-BRECHAS.md`](06-BRECHAS.md) | 0 | 3 | 3 | **6** | 1 (`GAP-R01`) | 2 (`GAP-03`, `GAP-06`) |
| **Rendimiento** | [`07-RENDIMIENTO.md`](07-RENDIMIENTO.md) | 0 | 4 | 2 | **6** | 2 (`PERF-R01`, `PERF-R02`) | 0 |
| **Calidad y DX** | [`08-CALIDAD-DX.md`](08-CALIDAD-DX.md) | 1 | 3 | 3 | **7** | 1 (`DX-R01`) | 0 |
| **TOTAL** | **6 categorías** | **6** | **17** | **14** | **37** | **7** | **4 ítems (+ ADR-00)** |
