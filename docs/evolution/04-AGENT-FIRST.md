# 04 · Agent-First: Evaluación de las 5 Dimensiones, Auditoría de Herramientas y Protocolos Falsables

## 1. Evaluación de las Cinco Dimensiones Agent-First

Para que *Agent-First* sea una propiedad de ingeniería verificable y no un eslogan, evaluamos las cinco dimensiones contra la evidencia obtenida en Fase 1 y Fase 2:

| Dimensión | Estado actual | Lo que está demostrado con evidencia | Lo que falla o está sin medir |
| :--- | :---: | :--- | :--- |
| **1. Authoring** *(generar código válido con pocas decisiones arbitrarias)* | **parcial** | Gramática compacta sin `;`, tipado explícito en firmas, contratos `requires`/`ensures` en firma, `T ! E` sin excepciones ocultas. | **Fricciones reales registradas en Fase 2:** 1) Coexisten múltiples formas equivalentes para lo mismo (`read_file` vs `io.read_text` vs `files.open`; `args()`/`env()` vs `environment.arguments()`/`get()`; `json.string`/`as_string` vs `json.from_str`/`to_str`; `Class(...)` vs `new Class(...)`); 2) `return []` falla en funciones `T ! E` (`W2004`); 3) `WYSNA_SINTAXIS.md:661-674` documenta métodos en `str` y `dict` que no existen (`W3002`). |
| **2. Comprensión** *(entender sin cargar todo el repo)* | **parcial** | `wysna graph` (`--rel impact`, `--manifest`) y `wysna inspect --with-deps` extraen el slice exacto de una función con las firmas de los tipos y funciones que invoca ([prog_a_inspect.json](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_a_inspect.json)). `wysna api` lista símbolos exportados. | **3 defectos verificados:** 1) `wysna api std/http` devuelve `43 KB` de JSON porque incluye recursivamente todos los módulos transitivos importados por `std/http` en vez de solo `std/http` (`src/tooling/ApiIndex.py:242-244`); 2) `wysna graph` e `inspect` reportan `"visibility": "public"` para **todas** las funciones privadas del módulo de entrada (`src/compiler/SemanticGraph.py:320`); 3) `graph_hash` se calcula con algoritmos distintos en Seed (`SemanticGraph.py:790`) y en Selfhost (`09_agent_tools.wysna:760`), por lo que un hash de Seed falla con `W5005` en Selfhost y viceversa. |
| **3. Edición** *(modificar de forma localizada y segura)* | **parcial** | `wysna patch --symbol NAME --expect-graph-hash ... --apply` verifica el hash optimista, reemplaza la declaración por rango de bytes, verifica tipos en memoria y escribe atómicamente con lock cooperativo (`src/tooling/SourceWrite.py`). | **Defecto crítico P0 en Selfhost:** `wysna fmt` en el SDK nativo y en el LSP nativo Stage-3 (`selfhost_format_body` en `selfhost/src/09_agent_tools.wysna:1398`) **corrompe literales `"""..."""`** al re-indentar líneas dentro del string ([repro_selfhost_fmt.txt](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/repro_selfhost_fmt.txt)). Además, en el SDK nativo (`09_agent_tools.wysna`), `patch --apply` y `fix --apply` escriben con `write_file` directo sin el lock cooperativo ni verificación SHA-256 en sección crítica de `SourceWrite.py`. |
| **4. Verificación y reparación** *(diagnósticos estructurados, spans y fix-its)* | **parcial** | `wysna check --output-format=json-v1` emite códigos estables `Wxxxx`, spans UTF-8 en bytes con `source_sha256`, recuperación de hasta 100 errores y `wysna fix --apply` repara `W3001`, `W3004`, `W1001` y `W2001`. | **3 defectos verificados:** 1) `wysna_check` en el servidor MCP (`src/tooling/McpServer.py:175`) **crashea con `TypeError`** cuando el archivo tiene cualquier warning `W6001`/`W6002` ([repro_mcp_cycle_crash.txt](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/repro_mcp_cycle_crash.txt)); 2) Selfhost `check --output-format=json-v1` emite `"data": {}` vacío (sin `expected`/`found`), `"invocation_id": "native"` fijo y filtra nombres internos `__wysna_module_6__Value` en `message` ([prog_b_check_compiler.json](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_b_check_compiler.json)); 3) Seed filtra `__wysna_module_<hex>__Value` en `diagnostic.data.expected` ([prog_b_check_seed.json](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_b_check_seed.json)). |
| **5. Seguridad operativa** *(controles estáticos vs cuotas vs aislamiento real)* | **parcial** | **Cuotas de ejecución (`--max-steps`, `--max-heap-mb` → `[W4004]`)** y **cassettes de efectos (`--record-trace` / `--replay-trace`)** están verificados. Los límites de seguridad reales están honestamente declarados en `docs/SCOPE.md:54-69` y `SECURITY.md`: los binarios nativos heredan todos los permisos del proceso OS; **no existe sandbox de kernel OS** (`seccomp`/`pledge`/contenedores) para código nativo ni FFI. | **El gate estático de capacidades (`wysna run --allow=...` / `--deny=...`) está roto para uso real (`P0`):** rechaza con `[W5006]` cualquier programa que instancie una `class` (incluso pura, sin I/O) o que importe `std/http` o `std/crypto`, porque faltan 8 externs de `stdlib/` en `src/compiler/EffectModel.py:66-100` y `src/tooling/CapabilityPolicy.py` usa `complete_within_model` (que marca toda `class` como `unknown`) en lugar de `resolved_complete` ([repro_capability_stdlib.txt](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/repro_capability_stdlib.txt)). |

---

## 2. Auditoría Herramienta por Herramienta: Solapamiento, Evidencia y Servicio Semántico

Evaluación obligatoria de cada comando/herramienta Agent-First:

| Herramienta | ¿Resuelve un problema independiente o solapa a otra? | ¿Ventaja demostrable o "sin medir"? | ¿Comparte hoy un único servicio semántico? | Diagnóstico y acción |
| :--- | :--- | :--- | :--- | :--- |
| **`check` (`--output-format=json-v1`)** | **Independiente** (verificación estática y emisión de diagnósticos/fixes). | **Verificado técnicamente** (`1.42 ms` en MCP, `4.66 ms` en Selfhost CLI). Ventaja en iteraciones LLM: **sin medir en cohorte real**. | En Seed usa `CompilerService`. En Selfhost usa `check_source` (`08_checker.wysna`) con salida `json-v1` divergente (`data: {}`). | **KEEP** + unificar contrato `json-v1` entre Seed y Selfhost (**`AGT-04`**). |
| **`fix` (`--apply`, `--include-maybe`)** | **Parcialmente solapado con `check` y LSP `codeAction`.** Consume los `fixes` que `check` ya calcula. | **Verificado en tests deterministas** (`W3001`, `W3004`, `W1001`, `W2001`). En `bench/ai_eval/harness.py`, `Pass@1` mezclaba autofix con autoría cruda (**sin medir por separado en LLM real**). | En Seed llama a `CompilerService`; en Selfhost (`09_fix.wysna`, `808` LOC) reimplementa la heurística desde cero. | **KEEP** el comando, pero separar métricas `raw_pass_at_1` vs `post_fix_pass_at_1` en el benchmark (**`AGT-06`**). |
| **`patch` (`--symbol`, `--expect-graph-hash`)** | **Solapa con edición estándar de archivos + `check`**, salvo por el reemplazo por nombre de símbolo y el bloqueo optimista `graph_hash`. | **Verificado funcionalmente**, pero **sin medir** frente a edición normal de rango/líneas con un modelo real. Además, `graph_hash` es incompatible entre Seed y Selfhost. | En Seed usa `CompilerService.graph` + `SourceWrite.py`; en Selfhost (`09_agent_tools.wysna`) reimplementa spans y escribe sin `SourceWrite`. | **SIMPLIFY**: unificar cálculo de `graph_hash` y escritura atómica (`AGT-04`), y medir en `ab_cohort.py` (`AGT-06`). |
| **`inspect` (`--symbol`, `--with-deps`)** | **Solapa fuertemente con `graph --symbol`.** `inspect` devuelve el mismo nodo y el mismo `impact` que `graph --symbol`, añadiendo únicamente el snippet `source` y las firmas de `dependencies`. | **Sin medir** con modelos reales en `ab_cohort.py` (solo mediciones sintéticas de conteo de tokens léxicos en `impact_benchmark.py`). | En Seed `inspect_source` llama literalmente a `service.graph(checked)` (`AgentGraphCLI.py:181`); en Selfhost (`09_agent_tools.wysna`) comparte `ToolGraph`. | **KEEP** como vista proyectada de `graph`, corrigiendo el bug de `"visibility": "public"` (**`AGT-05`**). |
| **`graph` (`--symbol`, `--rel`, `--manifest`)** | **Independiente** para análisis de impacto inter-módulo (`--manifest ... --rel impact`), pero solapa con `inspect` y `explain` cuando se invoca sobre un solo archivo (incluye `effects` y `capabilities` en cada nodo). | **Verificado en mutaciones sintéticas** (`impact_benchmark.py`), pero **sin medir** en tasa de éxito o regresiones de un LLM real (`ab_cohort.py`). | En Seed `CompilerService.graph` invoca `self.explain(checked)` + `build_semantic_graph`; Selfhost tiene una implementación paralela en `09_agent_tools.wysna`. | **KEEP** y evaluar empíricamente con protocolo A/B (**`AGT-06`**). |
| **`explain` (`--function`, `--code`)** | **Mezcla dos herramientas distintas en un mismo comando:** 1) `wysna explain file.wysna` calcula efectos/capacidades sobre MIR; 2) `wysna explain --code Wxxxx` imprime la guía estática de un código de error. Además, sus efectos ya se incrustan en `graph`, `inspect` y `schema`. | **Verificado** como análisis estático MIR, pero **sin medir** su impacto en agentes. Además, marca toda `class` como `unknown` (`may_finalize_resource`). | En Seed usa `EffectAnalysis.py`; en Selfhost `09_agent_tools.wysna` aproxima efectos por escaneo léxico/llamadas sin el modelo MIR completo. | **INVEST** en corregir `EffectModel.py` / `EffectAnalysis.py` para que el análisis sea preciso en `class` y `stdlib` (**`AGT-03`**). |
| **`schema` (`--all`, `--function`, `--emit-codecs`)** | **Mezcla dos funciones:** 1) exportar JSON Schema Draft 2020-12 + definiciones de tools LLM; 2) generar código Wysna de serialización (`--emit-codecs`). | **Verificado funcionalmente**, pero `--emit-codecs` tiene el bug `P0` de ignorar `requires` (**`LANG-03`**), y la utilidad del JSON Schema para agentes está **sin medir**. | En Seed (`SchemaCLI.py`, `737` LOC) llama a `CompilerService.check` + `explain`; en Selfhost (`09_schema.wysna`, `1,404` LOC) está duplicado por completo. | Corregir `--emit-codecs` (**`LANG-03`**) y mantener `schema`. |
| **`api` y `capabilities`** | `capabilities` es **independiente** (describe el toolchain instalado). `api` **solapa con `inspect` sin `--symbol`** (ambos listan declaraciones y firmas de módulos/proyectos). | **Verificado**, pero `wysna api std/http` vuelca transitivamente 7 módulos (`43 KB` de JSON), desperdiciando ventana de contexto del agente. | `ApiIndex.py` parsea el AST directamente **sin pasar por `CompilerService`** para stdlib; Selfhost lo duplica en `09_api.wysna` (`785` LOC). | **SIMPLIFY** `wysna api <mod>` para devolver por defecto solo el módulo pedido (**`AGT-05`**). |
| **`skills`** | **Solapa con la documentación (`docs/AI_AGENT_GUIDE.md`, `WYSNA_SINTAXIS.md`).** Son textos estáticos embebidos en el binario/Python. | **Sin medir.** Riesgo de deriva documental respecto a `SPEC.md` y `STDLIB.md`. | No usa servicio semántico (diccionario estático de strings duplicado en `AgentGraphCLI.py` y `09_agent_tools.wysna`). | **KEEP** congelado; no añadir más tópicos estáticos duplicados. |
| **`mcp` (`wysna mcp --root`)** | **Independiente como transporte** (daemon JSON-RPC 2.0 sobre stdio que expone las 11 herramientas anteriores sin pagar cold start). | **Verificado en latencia** (`1.42 ms` vs `75.38 ms` en frío: `53x` más rápido). Actualmente `wysna_check` en MCP crashea si hay warnings `W6001`/`W6002` (**`AGT-02`**). | En Seed (`McpServer.py`) mantiene una instancia de `CompilerService` en memoria, pero para `api`, `capabilities`, `fix`, `patch` y `skills` re-invoca `_run_cli` en proceso. En Selfhost (`09_mcp.wysna`, `709` LOC) está duplicado. | **INVEST** en corregir el crash de `wysna_check` (**`AGT-02`**) y usarlo como transporte primario para agentes. |
| **`lsp` (`wysna lsp`)** | **Independiente como transporte para editores IDE** (VS Code). Solapa internamente con `check`, `fmt`, `graph` y `fix`. | **Verificado** en tests de protocolo LSP 3.17. En el SDK nativo (`09_lsp.wysna`), `textDocument/formatting` llama a `selfhost_format_body`, heredando el bug `P0` que corrompe strings `"""..."""`. | En Seed (`LanguageServer.py`, `967` LOC) usa `CompilerService`. En Selfhost (`09_lsp.wysna`, `1,576` LOC) reimplementa resolución, completado y renombrado. | Reparar `selfhost_format_body` (**`AGT-01`**) y compartir núcleo semántico (ver `05-SIMPLIFICACION.md`). |

### ¿Podrían compartir un único servicio semántico?

**Sí.** Hoy existen **tres niveles de fragmentación**:
1. Dentro de **Seed (Python)**, `check`, `explain`, `graph`, `inspect` y `lsp` usan `CompilerService`, pero `ApiIndex.py` parsea archivos por su cuenta sin `CompilerService`, `SchemaCLI.py` vuelve a llamar a `service.explain(checked)` y clona `checked.ast`, y `McpServer.py` llama a `CompilerService` en `wysna_check`/`wysna_fmt`/`wysna_explain` pero invoca `_run_cli(argv)` (que crea otro `CompilerService` nuevo desde cero) en `wysna_fix`, `wysna_graph`, `wysna_inspect` y `wysna_patch`!
2. Entre **Seed (`src/tooling/`, `7,876` LOC)** y **Selfhost (`selfhost/src/09_*.wysna`, `8,935` LOC)**, cada una de las 11 herramientas fue reescrita manualmente en Wysna con heurísticas distintas (por ejemplo, `selfhost_format_body` formatea contando llaves en vez de AST; `09_agent_tools.wysna` calcula un `graph_hash` incompatible con `SemanticGraph.py`; `09_main.wysna` emite `json-v1` con `"data": {}` vacío).
3. **Ruta de convergencia:** Unificar los 11 comandos sobre un contrato único de `CompilerService` en Seed y alinear o delegar los subcomandos nativos para que ningún comando tenga dos semánticas distintas (ver **`AGT-01`**, **`AGT-04`** y **`SIMP-01`** en `05-SIMPLIFICACION.md`).

---

## 3. Protocolo Falsable de Evaluación para Features Agent-First "Sin Medir"

Tal como establecen las reglas de este plan, **los conteos de tokens por sí solos no son un indicador de calidad** y ninguna herramienta Agent-First se da por validada sin un experimento comparativo falsable. No se ejecutan benchmarks con LLMs externos durante esta tarea de diagnóstico; el protocolo queda especificado aquí y su ejecución y blindaje constituyen el ítem **`AGT-06`**:

| Feature a evaluar | Tarea experimental (Corpus) | Métricas primarias (ordenadas por importancia) | Línea base (Condición Control A) | Tratamiento (Condición Experimental B) | Presupuesto e Hipótesis Falsable (criterio para conservar o retirar) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **1. Auto-reparación `wysna fix --apply` (`W3001`, `W3004`, `W1001`, `W2001`)** | 30 tareas de `bench/ai_eval/tasks.py` (`TASK-01`..`TASK-30`) + 5 tareas de `ab_cohort.py`. | 1. `raw_pass_at_1` (sin `fix`) vs `post_fix_pass_at_1` (tras 1 corrida de `wysna fix --apply` antes del turno LLM 1).<br>2. Media de iteraciones LLM de reparación (`0..3`).<br>3. Regresiones semánticas introducidas por `--include-maybe` (compila pero falla el oráculo funcional). | El modelo recibe el diagnóstico `wysna check --output-format=json-v1` crudo y repara en hasta 3 turnos sin ejecutar `wysna fix`. | Antes de devolver un fallo `check` al modelo, el arnés ejecuta `wysna fix --apply` (y en una sub-rama `--include-maybe`). | **Presupuesto:** máx. 3 turnos LLM, `15 s` timeout por tarea.<br>**Criterio de falsación:** Si `wysna fix --apply` mejora `Pass@1` en **`< 5` puntos porcentuales** o si `--include-maybe` introduce **`> 0` regresiones de oráculo** donde el código compila pero da salida errónea, se desactiva `--include-maybe` de toda guía por defecto. |
| **2. Contexto semántico `wysna graph --rel impact` + `wysna inspect --with-deps`** | 10 tareas multi-módulo de refactorización, adición de capacidades y diagnóstico sobre `examples/wysofia-ops`, `examples/shared-browser-app` y `AB-REFACTOR-03`..`AB-DIAGNOSIS-05`. | 1. Tasa de éxito funcional (`Pass@1` y `Pass@3`).<br>2. Tasa de regresiones en módulos vecinos o tests no tocados (`unintended_breakage_rate`).<br>3. Turnos totales y tokens facturados (prompt + tool calls + completion). | **Condición A:** El modelo puede leer archivos `.wysna` completos del workspace y correr `wysna check` / `wysna project test`. | **Condición B:** El modelo dispone además de `wysna_graph` (`relation: "impact"`) y `wysna_inspect` (`with_deps: true`) con hasta 2 consultas antes de emitir el cambio (`ab_cohort.py --interactive-tools`). | **Presupuesto:** 2 consultas de herramienta + 1 edición por intento, máx. 3 intentos.<br>**Criterio de falsación:** Si la Condición B **no mejora `Pass@3` ni reduce `unintended_breakage_rate`** (o si consume más tokens totales por los turnos extra de consulta sin ganar precisión), se simplifica la guía para agentes (`docs/AI_AGENT_GUIDE.md`) priorizando lectura directa + `wysna check` en proyectos de `< 2,000` LOC. |
| **3. Edición estructurada `wysna patch --symbol --expect-graph-hash`** | Tareas de edición localizada (`AB-REFACTOR-03`, `AB-H2-CAPABILITY-04`) sobre archivos de `200–1,000` líneas. | 1. Tasa de ediciones aplicadas sin corromper declaraciones vecinas.<br>2. Frecuencia de fallos por desajuste de firma o borrado accidental de imports/helpers. | **Condición A:** El modelo emite el archivo modificado o un bloque de reemplazo estándar validado con `wysna check`. | **Condición B:** El modelo invoca `wysna_patch` pasando solo la declaración `symbol` y `--expect-graph-hash`. | **Presupuesto:** máx. 3 intentos.<br>**Criterio de falsación:** Si `wysna_patch` falla más veces por errores de invocación (símbolo calificado incorrecto o falta de tipos auxiliares en el mismo parche) que la edición de archivo + `wysna check`, `patch` se reclasifica como primitiva interna de refactorización/LSP y no como herramienta obligatoria de autoría para LLMs. |
| **4. Extracción de contratos `wysna schema` (`--emit-codecs`)** | Tareas de frontera de datos JSON (`TASK-13`..`TASK-20`, Fase 2 Programa A y Programa B). | 1. Tasa de éxito en validación de entradas JSON malformadas y fuera de rango (`requires`).<br>2. Líneas de código boilerplate que el agente debe escribir manualmente. | **Condición A:** El agente escribe el parser y serializador JSON a mano usando `std/json`. | **Condición B:** El agente define `struct` con `requires` y usa las funciones `decode_<T>` / `encode_<T>` generadas por `wysna schema --emit-codecs` (tras aplicar **`LANG-03`**). | **Presupuesto:** máx. 3 intentos.<br>**Criterio de falsación:** Si la Condición B no reduce los fallos de validación/serialización en **`>= 20%`** frente a escribir `json.get_*` manual, se congela la expansión de `--emit-codecs`. |

---

## 4. Backlog Categorizado Agent-First (`AGT-01` .. `AGT-06`)

### `AGT-01` · Reparar el formateador nativo de Selfhost (`selfhost_format_body`) que corrompe literales de cadena multilínea `"""..."""` e incumple el formato canónico

- **Prioridad:** P0
- **Decisión:** INVEST
- **Evidencia:** [selfhost/src/09_agent_tools.wysna:1398-1535](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/selfhost/src/09_agent_tools.wysna#L1398-L1535), [selfhost/src/09_lsp.wysna:980-995](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/selfhost/src/09_lsp.wysna#L980-L995), [src/tooling/SourceCommand.py:239-254](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/src/tooling/SourceCommand.py#L239-L254), y reproducción verificada en [repro_selfhost_fmt.wysna](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/repro_selfhost_fmt.wysna) y [repro_selfhost_fmt.txt](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/repro_selfhost_fmt.txt). Nivel: **verificado**.
- **Problema:** Cuando un usuario o agente utiliza el SDK nativo (`wysna fmt`) o formatea desde VS Code con el servidor LSP nativo Stage-3 (`wysna lsp`), se ejecuta `selfhost_format_body` en `selfhost/src/09_agent_tools.wysna:1398`. Esta función:
  1. **Corrompe datos en runtime:** Recorta cada línea con `selfhost_trim_line(raw)` y cuenta caracteres `{` (`ord == 123`) y `}` (`ord == 125`) sin rastrear estados de strings multilínea `"""..."""` ni `f"""..."""`. Si un string multilínea contiene `{` o espacios iniciales, **`wysna fmt --write` modifica el texto literal dentro del string**, cambiando silenciosamente el comportamiento del programa compilado ([repro_selfhost_fmt.txt](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/repro_selfhost_fmt.txt)).
  2. **Incumple el contrato canónico (`SPEC.md:596-598`):** No formatea expresiones ni operadores (`let x=1+2` queda `let x=1+2` en Selfhost vs `let x = 1 + 2` en Seed).
  3. **Exige que el programa pase `mir_build` y `mir_verify` antes de formatear (`09_agent_tools.wysna:1399-1400`):** Un archivo con un simple error de tipos o un import pendiente no se puede formatear en el editor.
  4. **Estaba oculto en los tests de Seed** porque `./wysna fmt --engine=compiler` en `src/tooling/SourceCommand.py:239-254` llamaba a `Formatter().format` de Python en lugar de invocar `--fmt` en el binario `selfhost_bin`!
- **Propuesta:**
  1. De inmediato (`P0`), hacer que `selfhost_format_body` use el lexer real de Selfhost (`tokenize` en `selfhost/src/02_lexer.wysna`, que ya reconoce tokens `STRING` y `FSTRING` multilínea sin alterar sus bytes internos y ya conoce todos los operadores) para preservar intactos los literales de cadena multilínea, normalizar el espaciado canónico de tokens/operadores sin requerir `mir_build`/`mir_verify` (solo lex/parse válido), y verificar que el stream de tokens antes y después sea idéntico.
  2. Hacer que `./wysna fmt --engine=compiler` en `src/tooling/SourceCommand.py:239-254` delegue realmente en el binario Selfhost (`--fmt`) y añadir un test de paridad en `tests/formatter_contract.py` que exija que `selfhost fmt` preserve el AST y no altere ningún literal.
  - **Antes (salida real de `.wysna_cache/wysna_selfhost fmt` en `repro_selfhost_fmt.txt`):**
    ```wysna
    let x=1+2
    let msg = """
    keep_zero_indent {
        still_zero_indent
    }
    """
    ```
  - **Después:**
    ```wysna
    let x = 1 + 2
    let msg = """
    keep_zero_indent {
    still_zero_indent
    }
    """
    ```
- **Alternativas:**
  1. *No hacer nada:* dejar que `wysna fmt --write` y `Format Document` en VS Code con el SDK nativo corrompan strings multilínea. Inaceptable (`P0`).
  2. *Resolverlo con lo que ya existe:* usar únicamente `tokenize` (`selfhost/src/02_lexer.wysna`) que ya existe dentro de Selfhost para proteger los spans de tokens `STRING`/`FSTRING` y formatear sobre tokens sin construir MIR.
- **Impacto (1–5):** Usuario: **5** | Agentes: **5** | Reducción de complejidad: **3**.
- **Esfuerzo:** **M (2–3 días)**. Supuestos: reescribir `selfhost_format_body` en `selfhost/src/09_agent_tools.wysna` sobre el stream de tokens de `02_lexer.wysna` (~180 líneas), conectar `SourceCommand.py:239` al binario nativo y añadir el test de contrato en `tests/formatter_contract.py`.
- **Riesgo:** **Bajo-Medio.** Cualquier regresión en `selfhost_format_body` es capturada inmediatamente por `formatter_contract.py` sobre los 185 archivos del corpus.
- **Compatibilidad y migración:** 100% compatible; elimina una corrupción silenciosa de datos.
- **Componentes afectados:** `selfhost/src/09_agent_tools.wysna`, `selfhost/compiler.wysna`, `src/tooling/SourceCommand.py`, `tests/formatter_contract.py`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** Ejecutar `.wysna_cache/wysna_selfhost fmt docs/evolution/evidence/repro_selfhost_fmt.wysna` produce exactamente `let x = 1 + 2`, preserva byte a byte el contenido de `"""..."""`, funciona sobre archivos con errores de tipos pero sintaxis válida, y pasa `formatter_contract.py` sobre los 185 archivos del repositorio.
- **Requiere aprobación:** no (corrige un bug P0 que viola `SPEC.md:596-598`).
- **Estado:** implementado.

---

### `AGT-02` · Corregir crash `TypeError` en `McpServer.tool_wysna_check` ante advertencias `W6001` / `W6002` y evitar re-invocaciones CLI en memoria

- **Prioridad:** P0
- **Decisión:** INVEST
- **Evidencia:** [src/tooling/McpServer.py:163-179](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/src/tooling/McpServer.py#L163-L179), [src/tooling/SourceCommand.py:68-80](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/src/tooling/SourceCommand.py#L68-L80), [src/tooling/ToolResult.py:92-145](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/src/tooling/ToolResult.py#L92-L145), y reproducción verificada en [repro_mcp_cycle_crash.txt](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/repro_mcp_cycle_crash.txt). Nivel: **verificado**.
- **Problema:** En `src/tooling/McpServer.py:170-177`, `tool_wysna_check` junta en una misma lista `warnings` los objetos `WysnaError` de `lint_discarded_expressions` (`W6001`) y los `dict` devueltos por `self.service.analyze_reference_cycles(checked)` (`W6002`), y luego llama a `diagnostic_from_error(w, sources, severity="warning", span_cache=cache)`. Esto falla siempre por **dos causas**:
  1. `diagnostic_from_error` en `src/tooling/ToolResult.py` no acepta el argumento `severity="warning"` (`TypeError: diagnostic_from_error() got an unexpected keyword argument 'severity'`).
  2. Los elementos de `analyze_reference_cycles` son diccionarios (`{"message": ..., "advisory": ..., "classes": ...}`), no instancias de `WysnaError`.
  Como consecuencia, cualquier archivo válido que tenga una expresión descartada (`W6001`) o una clase recursiva como `class Node { public next: Node? }` (`W6002`, incluyendo el propio `selfhost/compiler.wysna`!) hace crashear la herramienta `wysna_check` del servidor MCP.
- **Propuesta:** Reutilizar la función `_warning_records(cycle_warnings, lint_warnings, path, sources)` que ya existe y funciona correctamente en `src/tooling/SourceCommand.py:68-80` dentro de `McpServer.tool_wysna_check`.
- **Alternativas:**
  1. *No hacer nada:* dejar que `wysna_check` en MCP falle con excepción no controlada en cualquier programa con clases recursivas o warnings de linter. Inaceptable (`P0`).
  2. *Resolverlo con lo que ya existe:* importar y llamar a `_warning_records` de `src/tooling/SourceCommand.py` (o mover `_warning_records` a `src/tooling/ToolResult.py`).
- **Impacto (1–5):** Usuario: **4** | Agentes: **5** (desbloquea `wysna_check` en MCP sobre código con clases recursivas y warnings) | Reducción de complejidad: **3** (elimina lógica duplicada entre `SourceCommand.py` y `McpServer.py`).
- **Esfuerzo:** **S (<0.5 días)**. Supuestos: ~10 líneas en `src/tooling/McpServer.py` y `src/tooling/ToolResult.py` + test unitario en `tests/unit/test_mcp_server.py`.
- **Riesgo:** **Bajo.**
- **Compatibilidad y migración:** Sin cambios de interfaz; repara una ruta rota del protocolo `wysna.tool-result/1`.
- **Componentes afectados:** `src/tooling/McpServer.py`, `src/tooling/SourceCommand.py`, `src/tooling/ToolResult.py`, `tests/unit/test_mcp_server.py`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** Llamar a `McpServer(".").tool_wysna_check({"source": "class Node { public next: Node?\n fn init() { this.next = none } }\n1 + 2\n"})` retorna `status: "ok"` con dos diagnósticos de severidad `"warning"` (`W6001` y `W6002`) sin lanzar `TypeError`.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

### `AGT-03` · Reparar el modelo de efectos y gate de capacidades (`W5006`) para que soporte `class` puras y los 19 módulos de `stdlib/`

- **Prioridad:** P0
- **Decisión:** INVEST
- **Evidencia:** [src/compiler/EffectModel.py:66-100](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/src/compiler/EffectModel.py#L66-L100), [src/compiler/EffectAnalysis.py:18-39,90,143,162-201](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/src/compiler/EffectAnalysis.py#L18-L39), [src/tooling/CapabilityPolicy.py:42-95](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/src/tooling/CapabilityPolicy.py#L42-L95), y reproducción verificada en [repro_capability_stdlib.txt](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/repro_capability_stdlib.txt) y [prog_a_capability_gate.txt](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_a_capability_gate.txt). Nivel: **verificado**.
- **Problema:** El gate estático de capacidades (`wysna run --deny=network` o `--allow=...`) falla con `[W5006]` en casi cualquier programa real por dos defectos en el analizador de efectos:
  1. **Faltan 8 funciones `extern "C"` de la biblioteca estándar en `HOST_EFFECTS` (`src/compiler/EffectModel.py:66-100`):** Al importar `std/http` (que declara `wysna_host_http_async_start`, `wysna_host_http_async_poll`, `wysna_host_http_async_finish`, `wysna_host_http_async_cancel`, `wysna_host_http_batch_get`) o `std/crypto` (que declara `wysna_host_crypto_sha256`, `wysna_host_crypto_hmac_sha256`, `wysna_host_crypto_random_bytes`), `CompilerService._foreign_contracts` clasifica esos externs de la propia stdlib como **declaraciones foráneas no confiables (`untrusted`)**, abortando incluso `import "std/crypto"; print(1)` bajo `--deny=network`!
  2. **Toda `class` se marca como `resource.finalize` + `unknown` en `may_finalize_resource` (`src/compiler/EffectAnalysis.py:27`):** `may_finalize_resource` devuelve `True` para cualquier `entry.tag == 'class'` sin comprobar si la clase contiene algún campo `opaque`, mientras que `_may_finalize_opaque_resource` (línea 162) sí inspecciona los campos reales del layout. Además, para las clases estándar `buffer.Bytes` y `files.File` (cuyos finalizadores de host son conocidos y confiables en el runtime: liberar memoria o cerrar un descriptor), liberar un `Bytes` en `buffer.from_text("hi")` o `text.normalize_nfc("a")` o `environment.current_directory()` contamina la función con `unknown`, haciendo que `wysna run --deny=network` falle con `[W5006] Capability policy requires a complete effect analysis` ([repro_capability_stdlib.txt](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/repro_capability_stdlib.txt)).
- **Propuesta:**
  1. Registrar en `HOST_EFFECTS` (`src/compiler/EffectModel.py:66-100`) todos los `extern "C"` de `stdlib/*.wysna` con sus efectos exactos (`wysna_host_http_*` → `{'network.http', 'memory.write'}`, `wysna_host_crypto_*` → `{'memory.read', 'memory.write'}`) y añadir un test de contrato en `tests/unit/test_architecture_contract.py` que falle si cualquier `extern "C"` en `stdlib/*.wysna` no está en `HOST_EFFECTS`.
  2. En `src/compiler/EffectAnalysis.py`, unificar `may_finalize_resource` con `_may_finalize_opaque_resource` para que las clases de usuario sin campos `opaque` (o `std/buffer.Bytes`, cuyo cierre solo libera memoria) no inyecten `unknown`, y que `std/files.File` registre el efecto modelado `{'resource.close', 'filesystem.write'}` en vez de `unknown`.
  3. Desmangler los nombres de funciones en el mensaje de error de `W5006` usando `display_name(fn)` (`src/compiler/EffectAnalysis.py:10`) en lugar de imprimir `wysna_fn___wysna_module_7374646c69622f...`.
- **Alternativas:**
  1. *No hacer nada:* dejar que `wysna run --deny=network` falle en programas con clases, `std/crypto`, `std/http`, `std/buffer`, `std/text.normalize_nfc` o `std/environment.current_directory`. Hace inutilizable el gate `--allow`/`--deny` en scripts reales.
  2. *Resolverlo con lo que ya existe:* usar `enforce_resolved_capability_policy` (que ya existe en `src/tooling/CapabilityPolicy.py:65` para `--sandbox`) también en `wysna run --allow`/`--deny`, y completar la tabla `HOST_EFFECTS`.
- **Impacto (1–5):** Usuario: **4** | Agentes: **5** (hace operativo `wysna run --allow/--deny` y limpia `"unknown"` falsos en `wysna schema` y `wysna explain`) | Reducción de complejidad: **4**.
- **Esfuerzo:** **S (1 día)**. Supuestos: ~35 líneas en `EffectModel.py`, `EffectAnalysis.py` y `CapabilityPolicy.py` + test de invariante sobre `stdlib/*.wysna`.
- **Riesgo:** **Bajo.** Mantiene `unknown` para cualquier FFI externa fuera de `stdlib/` y para llamadas indirectas no resueltas.
- **Compatibilidad y migración:** 100% compatible; elimina falsos rechazos `W5006` en programas seguros.
- **Componentes afectados:** `src/compiler/EffectModel.py`, `src/compiler/EffectAnalysis.py`, `src/tooling/CapabilityPolicy.py`, `tests/unit/test_architecture_contract.py`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:**
  1. Todos los casos en [repro_capability_stdlib.txt](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/repro_capability_stdlib.txt) (excepto `std/http` bajo `--deny=network`, que debe ser rechazado por violar `network`, no por `untrusted foreign declarations`) pasan `./wysna run --deny=network` con código `0`.
  2. `./wysna run --allow=stdio,arguments,environment,filesystem.read,filesystem.write,network,process-control docs/evolution/evidence/prog_a_cli.wysna` ejecuta con éxito (`rc=0`).
  3. Un test en `test_architecture_contract.py` verifica que el 100% de las declaraciones `extern "C"` en `stdlib/*.wysna` están presentes en `HOST_EFFECTS`.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

### `AGT-04` · Unificar el contrato `json-v1` (desmangler tipos, poblar `data`, `invocation_id` y `graph_hash`) entre Seed y Selfhost

- **Prioridad:** P1
- **Decisión:** SIMPLIFY
- **Evidencia:** Comparación directa de salidas en Fase 2: [prog_b_check_seed.json](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_b_check_seed.json) vs [prog_b_check_compiler.json](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_b_check_compiler.json), [prog_a_check_fixed.json](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_a_check_fixed.json), [src/compiler/SemanticGraph.py:790](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/src/compiler/SemanticGraph.py#L790) vs [selfhost/src/09_agent_tools.wysna:760](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/selfhost/src/09_agent_tools.wysna#L760), y [selfhost/src/01_diagnostics.wysna:140-260](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/selfhost/src/01_diagnostics.wysna#L140-L260). Nivel: **verificado**.
- **Problema:** Un agente que consume `wysna.tool-result/1` recibe payloads incompatibles según si ejecuta Seed o el SDK nativo (`--engine=compiler`):
  1. **Filtración de nombres mangled internos en errores de tipos:** En Seed, `diagnostic.message` desmangla `stdlib/json.wysna::Value`, pero `diagnostic.data.expected` expone `"list[__wysna_module_7374646c69622f6a736f6e2e7779736e61__Value] ! str"` ([prog_b_check_seed.json](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_b_check_seed.json)). En Selfhost, el propio `diagnostic.message` expone `"expected 'list[__wysna_module_6__Value] ! str'"` y `diagnostic.data` está vacío (`{}`) ([prog_b_check_compiler.json](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_b_check_compiler.json)).
  2. **Violación del esquema `check` en Selfhost:** En un `check` exitoso, Selfhost devuelve `"invocation_id": "native"` (constante) y mete un artefacto no estándar `{"kind": "checked_program", "functions": 144}` sin `sha256`, mientras `SPEC.md:670,704-711` especifica `"artifacts": []` para `check`.
  3. **`graph_hash` incompatible entre Seed y Selfhost:** Un `graph:...` obtenido con `wysna graph` en Seed es rechazado con `[W5005]` por `wysna patch` en el SDK nativo (y viceversa) porque `SemanticGraph.py:790` y `09_agent_tools.wysna:760` hashean campos distintos.
- **Propuesta:**
  1. En Seed (`src/checker/Suggestions.py:enrich_from_message` y `src/error/Error.py`), aplicar la función de desmangled de módulos antes de poblar `diagnostic.data["expected"]` y `diagnostic.data["found"]`.
  2. En Selfhost (`selfhost/src/01_diagnostics.wysna` y `08_semantics.wysna`), desmangler `__wysna_module_<N>__` al nombre del módulo (`json.Value` o `stdlib/json.wysna::Value`), poblar `data: {"kind": "type_mismatch", "expected": ..., "found": ...}`, y dejar `"artifacts": []` en `check --output-format=json-v1`.
  3. Unificar la fórmula canónica de `graph_hash` (por ejemplo, SHA-256 truncado a 16 hex sobre los pares ordenados `(file_rel_or_id, source_sha256)` de los módulos comprobados) en `SemanticGraph.py` y `09_agent_tools.wysna`.
- **Alternativas:**
  1. *No hacer nada:* mantener dos dialectos incompatibles de `json-v1` y `graph_hash`. Rompe herramientas MCP/VS Code cuando el usuario cambia entre la distribución Seed y el Native SDK.
  2. *Resolverlo con lo que ya existe:* reutilizar el decodificador de nombres de módulo que ya existe en `selfhost/src/03_modules.wysna` y en `src/compiler/ModuleNames.py`.
- **Impacto (1–5):** Usuario: **4** | Agentes: **5** | Reducción de complejidad: **3**.
- **Esfuerzo:** **M (1–2 días)**. Supuestos: ~30 líneas en `src/checker/Suggestions.py` / `ModuleNames.py`, ~70 líneas en `selfhost/src/01_diagnostics.wysna` y `09_agent_tools.wysna`.
- **Riesgo:** **Bajo.**
- **Compatibilidad y migración:** Alinea ambos motores al contrato `wysna.tool-result/1` ya publicado en `SPEC.md:660-714`.
- **Componentes afectados:** `src/checker/Suggestions.py`, `src/compiler/SemanticGraph.py`, `selfhost/src/01_diagnostics.wysna`, `selfhost/src/09_agent_tools.wysna`, `selfhost/compiler.wysna`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** Ejecutar `wysna check --output-format=json-v1` y `wysna check --engine=compiler --output-format=json-v1` sobre un error de tipos con tipos importados no contiene la subcadena `__wysna_module_` en `message` ni en `data`, ambos incluyen `data.expected` y `data.found`, y `wysna graph` devuelve el mismo `graph_hash` en ambos motores.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

### `AGT-05` · Corregir la precisión de `wysna graph`/`inspect` (`visibility: "private"` vs `"public"`) y acotar `wysna api <mod>` al módulo consultado

- **Prioridad:** P1
- **Decisión:** SIMPLIFY
- **Evidencia:** [src/compiler/SemanticGraph.py:320,388,415,442,469](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/src/compiler/SemanticGraph.py#L320), [prog_a_graph.json](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_a_graph.json), [src/tooling/ApiIndex.py:226-249](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/src/tooling/ApiIndex.py#L226-L249), y [api_std_http.txt](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/api_std_http.txt). Nivel: **verificado**.
- **Problema:**
  1. En `src/compiler/SemanticGraph.py:320,388,415,442,469`, la visibilidad de cada nodo se calcula como `'public' if raw_name in exports or is_entry else 'private'`. Como `is_entry` es `True` para el archivo principal, **todas las funciones y tipos privados (`fn foo`, `struct Bar` sin `pub`) del módulo de entrada se reportan falsamente como `"visibility": "public"`** en `wysna graph` y `wysna inspect` ([prog_a_graph.json](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_a_graph.json)).
  2. En `src/tooling/ApiIndex.py:242-244` (y `selfhost/src/09_api.wysna`), cuando un agente ejecuta `wysna api std/http` para consultar la API de `std/http`, `_load_stdlib_index` recorre recursivamente todos los `import "std/..."` internos y devuelve **7 módulos completos (`43 KB` de JSON: `std/algorithms`, `std/buffer`, `std/bytes`, `std/http`, `std/json`, `std/parse`, `std/text`)** ordenados alfabéticamente (por lo que `std/http` ni siquiera aparece al principio de la salida!).
- **Propuesta:**
  1. En `src/compiler/SemanticGraph.py` (y `selfhost/src/09_agent_tools.wysna`), reportar `'visibility': 'public' if getattr(stmt, 'is_exported', False) or raw_name in exports else 'private'`.
  2. En `src/tooling/ApiIndex.py:226-249` y `selfhost/src/09_api.wysna`, cuando el usuario especifica un módulo (`wysna api std/http`), devolver **únicamente** el módulo solicitado en `result.modules` (añadiendo opcionalmente un flag `--with-deps` si alguien desea expandir sus dependencias transitivas), y rellenar `availability.selfhost: "supported"` para los módulos estándar que ya están verificados en `native-core-v1`.
- **Alternativas:**
  1. *No hacer nada:* seguir inyectando `43 KB` de módulos no solicitados en el contexto del agente al consultar `wysna api std/http` y seguir reportando funciones privadas como públicas.
  2. *Resolverlo con lo que ya existe:* filtrar en el cliente JSON el array `result.modules` buscando `identity == "std/http"`. No evita el gasto de tokens en clientes MCP que inyectan la respuesta completa de la herramienta en el prompt del LLM.
- **Impacto (1–5):** Usuario: **3** | Agentes: **5** (reduce en un `80%` el tamaño del payload de `wysna api <mod>` y corrige la visibilidad de símbolos en `graph`/`inspect`) | Reducción de complejidad: **3**.
- **Esfuerzo:** **S (<1 día)**. Supuestos: ~15 líneas en `SemanticGraph.py`, ~15 líneas en `ApiIndex.py`, y ajuste equivalente en `selfhost/src/09_agent_tools.wysna` y `09_api.wysna`.
- **Riesgo:** **Bajo.** Revisar si algún test unitario en `tests/unit/test_api_index.py` espera los módulos transitivos al pedir `wysna api std/http` y actualizarlo.
- **Compatibilidad y migración:** `wysna api` sin argumentos sigue devolviendo los 19 módulos de `stdlib/`; `wysna api <mod>` devuelve el módulo `<mod>`.
- **Componentes afectados:** `src/compiler/SemanticGraph.py`, `src/tooling/ApiIndex.py`, `selfhost/src/09_agent_tools.wysna`, `selfhost/src/09_api.wysna`, `tests/unit/test_api_index.py`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:**
  1. `wysna graph docs/evolution/evidence/prog_a_cli.wysna --symbol fetch_remote_policy --output-format=json-v1` reporta `"visibility": "private"` para `fn fetch_remote_policy` y `"visibility": "public"` para funciones declaradas con `pub fn`.
  2. `wysna api std/http --output-format=json-v1` devuelve exactamente `1` módulo (`std/http`) en `result.modules`.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

### `AGT-06` · Instrumentar y separar métricas crudas vs asistidas en `bench/ai_eval/` para ejecutar el protocolo A/B falsable

- **Prioridad:** P1
- **Decisión:** INVEST
- **Evidencia:** [bench/ai_eval/harness.py:32-83,315-365](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/bench/ai_eval/harness.py#L32-L83), [bench/ai_eval/ab_cohort.py:36-210](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/bench/ai_eval/ab_cohort.py#L36-L210), [bench/ai_eval/README.md:19-27](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/bench/ai_eval/README.md#L19-L27). Nivel: **verificado**.
- **Problema:** Al inspeccionar el arnés `bench/ai_eval/` detectamos tres problemas que impiden medir honestamente el valor de las herramientas Agent-First:
  1. **Desactualización del `SYSTEM_PROMPT` y de las tareas bloqueadas (`bench/ai_eval/harness.py:32-83`, `README.md:20-26`):** El `SYSTEM_PROMPT` de `harness.py:47` todavía le dice al LLM: *"Note: for loops require a list; to iterate a dict use `for k, v in dict_items(d)`"* y en la línea 65 omite `.items()`, `.contains()`, `.is_empty()`, `x in col`, `f"..."`, `+=` y tuplas `(T1, T2)`, que ya existen en `v0.8.0`. Además, `bench/ai_eval/README.md:25-26` marca `TASK-26` como bloqueada por `lockfile v1` (que ya existe desde `v0.7.0`/`v0.8.0`).
  2. **Tamaño de muestra insuficiente en `ab_cohort.py`:** `_build_default_cohort_tasks()` en `bench/ai_eval/ab_cohort.py:36-192` solo tiene **5 tareas pequeñas** (`1` por familia, de 10–20 líneas cada una), donde un archivo entero cabe en 150 tokens y consultar `wysna graph`/`inspect` obviamente añade más overhead que leer las 15 líneas del archivo.
  3. **Falta de separación en el reporte A/B entre tokens de consulta de herramientas, regresiones por `--include-maybe` y `raw_pass_at_1` vs `post_fix_pass_at_1`:** Sin desglosar cuántas tareas pasan por autoría directa del LLM (`raw_pass_at_1`), cuántas repara `wysna fix --apply` en 0 turnos (`post_fix_pass_at_1`) y cuántas requieren turnos adicionales con `graph`/`inspect`, no se puede falsificar ninguna de las 4 hipótesis de la Sección 3.
- **Propuesta:**
  1. Sincronizar `SYSTEM_PROMPT` en `bench/ai_eval/harness.py:32-83` con la superficie real de `v0.8.0` y añadir un test en CI que verifique que todos los snippets del `SYSTEM_PROMPT` pasan `wysna check`.
  2. Ampliar `bench/ai_eval/ab_cohort.py` con al menos **10 tareas multi-archivo realistas** basadas en proyectos de `>300` líneas (copias aisladas de `examples/wysofia-ops/`, `examples/shared-browser-app/` y `examples/automation-sync/`), donde leer todo el repositorio sí tiene un costo real frente a usar `graph --rel impact` e `inspect --with-deps`.
  3. Registrar explícitamente en el JSON de salida de `harness.py` y `ab_cohort.py`: `raw_pass_at_1`, `post_fix_pass_at_1`, `pass_at_3`, `llm_repair_turns`, `tool_query_turns`, `total_tokens`, y `unintended_regression_count`.
- **Alternativas:**
  1. *No hacer nada:* mantener el arnés con un prompt de `v0.5.0` y una cohorte A/B de 5 micro-archivos de 15 líneas. Cualquier campaña LLM que se corra sobre eso producirá resultados sesgados e inconclusos.
  2. *Resolverlo con lo que ya existe:* correr solo `impact_benchmark.py` (que muta código sintéticamente sin LLM). Mide recall del grafo, pero no mide si un agente usa mejor el lenguaje.
- **Impacto (1–5):** Usuario: **2** | Agentes: **5** (proporciona el instrumento científico falsable para decidir qué herramientas Agent-First conservar o eliminar) | Reducción de complejidad: **3**.
- **Esfuerzo:** **M (2–3 días)**. Supuestos: actualizar `harness.py` y `ab_cohort.py`, desbloquear `TASK-26` y añadir tareas sobre copias de `examples/wysofia-ops` y `examples/shared-browser-app`.
- **Riesgo:** **Bajo.** Solo afecta `bench/ai_eval/`.
- **Compatibilidad y migración:** Preserva los campos existentes del esquema `wysna.ai-eval/2` y `wysna.ai-eval-ab/1` añadiendo los desgloses nuevos.
- **Componentes afectados:** `bench/ai_eval/harness.py`, `bench/ai_eval/tasks.py`, `bench/ai_eval/ab_cohort.py`, `bench/ai_eval/README.md`, `bench/ai_eval/tests/test_harness.py`.
- **Depende de:** `AGT-02`, `AGT-04`, `AGT-05`.
- **Criterio de aceptación del evaluador:** sus pruebas deterministas pasan y los runners `--provider=mock` completan el protocolo, sin exigir 100% de autoría a las respuestas de fallback, el reporte JSON incluye `raw_pass_at_1`, `post_fix_pass_at_1` y `unintended_regression_count`, y un test unitario valida con `wysna check` todas las construcciones sintácticas del `SYSTEM_PROMPT`.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

## 5. Hipótesis (`RESEARCH`)

### `AGT-R01` · Hipótesis: `wysna patch --symbol` reduce errores de edición de agentes frente a reemplazo de líneas estándar en archivos de `>300` LOC

- **Estado:** **La hipótesis concreta sigue sin medir.** El piloto exploratorio del protocolo conjunto midió prefetch semántico, no el uso de wysna patch --symbol; sus resultados no permiten aceptar ni descartar esta hipótesis.
- **Cómo falsificarla:** implementar y ejecutar la comparación delimitada en [AGT-R01-patch-protocol.md](research/AGT-R01-patch-protocol.md). El runner actual solo ofrece lectura, graph e inspect; `--interactive-tools` no compara las dos formas de editar. La ejecución con modelos queda aplazada por decisión del mantenedor.
