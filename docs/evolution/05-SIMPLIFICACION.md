# 05 · Simplificación: Matriz de Consolidación, Subsistemas y Reducción de Complejidad

> Auditoría histórica de la línea base `a2549fa`. Los problemas y mediciones describen ese snapshot; los estados de implementación y el ciclo vigente se consultan en [00-INDEX.md](00-INDEX.md) y [12-SIGUIENTE-CICLO-LENGUAJE.md](12-SIGUIENTE-CICLO-LENGUAJE.md).

## 1. Matriz de Consolidación de Subsistemas

Cada fila utiliza estrictamente una de las cinco categorías permitidas para **Razón de existencia**: `independencia deliberada` / `duplicación transitoria por migración` / `compatibilidad` / `deuda injustificada` / `consolidable`, y asigna una **Decisión** explícita (`KEEP`, `SIMPLIFY`, `MERGE`, `REPLACE`, `DEPRECATE`, `REMOVE`, `FREEZE`, `DEFER`).

| Componente A | Componente B | Solapamiento real (evidencia) | Razón de existencia | Decisión | Ahorro esperado medible | Riesgo y consumidores |
| :--- | :--- | :--- | :--- | :---: | :--- | :--- |
| **Frontend + Checker + MIR de Seed (`src/lexer`, `parser`, `checker`, `mir`: `16,341` LOC Python)** | **Frontend + Checker + MIR de Selfhost (`selfhost/src/01_*`..`08_*`: `8,976` LOC Wysna)** | Ambos tokenizan, parsean, verifican tipos, especializan genéricos, bajan a MIR, verifican MIR y emiten C99 (`209/209` fixtures en `docs/ENGINE_PARITY_MATRIX.md:24-35`). | `independencia deliberada` | **KEEP** | `0` líneas eliminadas a propósito: Selfhost actúa como compilador nativo sin Python (`4.66 ms` cold check) y Seed como implementación de referencia y oráculo de bootstrap (`C0 == C1 == C2`). Se acota el costo exigiendo tests diferenciales por cada PR de lenguaje. | **Riesgo alto si se elimina cualquiera:** eliminar Seed rompería WASI, Browser Wasm, REPL, LLVM y el oráculo de bootstrap; eliminar Selfhost destruiría el SDK nativo sin Python. Consumidores: CI, `package_native.py`, `package_distribution.py`. |
| **Tooling Agent-First / LSP / MCP / Project en Python (`src/tooling/*.py`: `7,876` LOC + `SemanticGraph.py` `977` LOC)** | **Tooling Agent-First / LSP / MCP / Project en Selfhost (`selfhost/src/09_*.wysna`: `8,935` LOC — `49.9%` de Selfhost)** | `09_agent_tools`, `09_lsp`, `09_schema`, `09_fix`, `09_project`, `09_project_lock`, `09_api` y `09_mcp` reimplementan desde cero en Wysna los 11 comandos de `src/tooling/`, pero cuando se invoca `./wysna <cmd>` desde el checkout o Seed Dist se ejecuta Python (`src/tooling/CLI.py:163-205`), mientras `driver.c` ejecuta `09_*.wysna` con divergencias graves (`selfhost_format_body` corrompe strings `"""..."""`, `graph_hash` distinto, `data: {}` vacío). | `duplicación transitoria por migración` | **SIMPLIFY** (**`SIMP-01`**) | Reducir **100% de las divergencias de protocolo** entre ambos mediante una suite única de contratos CLI/MCP (`test_agent_tools_parity`) y congelar nuevas features en `09_*.wysna` hasta que pasen exactamente los mismos tests JSON que `src/tooling/`. | **Riesgo medio.** Consumidores: `./wysna` (Seed CLI), `packaging/native/driver.c` (Native SDK), extensión VS Code (`editors/vscode-wysna/`). |
| **Archivos fuente modulares `selfhost/src/*.wysna` (`34` archivos, `17,911` LOC)** | **Bundle monolítico `selfhost/compiler.wysna` (`1` archivo, `17,911` LOC versionado en Git)** | `selfhost/compiler.wysna` es la concatenación byte a byte de `selfhost/src/*.wysna` generada por `tools/bundle_selfhost.py` (`88` LOC), verificada en `test_selfhost_split_conformance.py`. Duplica `17,911` líneas en cada diff de Git de Selfhost. | `compatibilidad` | **SIMPLIFY** (**`SIMP-02`**) | Eliminar **`17,911` líneas duplicadas en diffs de PRs** generando ` .wysna_cache/compiler.bundle.wysna` automáticamente en `Bootstrap.py` / `Makefile` desde `selfhost/src/*.wysna` (o manteniéndolo solo como artefacto de release). | **Riesgo bajo-medio.** Consumidores: `src/compiler/Bootstrap.py:19`, `src/run_conformance.py:88,465`, `tools/package_native.py`, `tools/bootstrap_from_native.sh`. |
| **AST Tipado (`src/node/NodeType.py` + `CheckedProgram.py`)** | **Typed HIR (`src/hir/`: `2,785` LOC) y Verified MIR (`src/mir/`: `4,788` LOC)** | Tres representaciones sucesivas en Seed (`AST → HIR → MIR`). Sin embargo, `src/hir/HirCCodeGen.py` (`44` LOC) ya no emite C desde HIR: solo llama a `mir.Lowering` y `mir.CCodeGen` (`HirCCodeGen.py:13-33`). | `independencia deliberada` (AST/HIR/MIR) + `deuda injustificada` (`HirCCodeGen.py` y paso 11 duplicado en `run_conformance.py`) | **KEEP** HIR y MIR; **REMOVE** fachada `HirCCodeGen` como falso backend separado (**`SIMP-03`**) | **Cada IR justifica una propiedad verificable distinta:**<br>• **AST:** sintaxis con trivia para `fmt`, LSP y oráculo `AST C99` (`src/codegen/CCodeGen.py`).<br>• **HIR (`HirVerifier.py`):** normaliza orden de evaluación `L→R` (`HirSequence`) y rutas de mutación CoW (`HirLValuePath`) sin CFG.<br>• **MIR (`mir/Verifier.py`):** CFG de bloques básicos con análisis de flujo de datos que prueba inicialización definitiva y balance LIFO de `clone`/`store`/`release`.<br>Simplificar `HirCCodeGen` ahorra **199 compilaciones C99 redundantes** en `run_conformance.py` si se unifica con el runner. | **Riesgo bajo.** Consumidores de `HirCCodeGen`: `src/run_conformance.py:31,545`, `bench/run_bench.py:32,154`, `tests/unit/test_c06_default_backend.py`. |
| **Backend C99 (`src/mir/CCodeGen.py` `786` LOC + `selfhost/src/08_mir_c99.wysna` `561` LOC)** | **Backend Directo LLVM (`src/mir/Llvm*.py` `1,109` LOC + `src/compiler/Llvm*.py` `494` LOC + `runtime/wysna_llvm_*` `290` LOC + `tools/package_llvm_sdk.py` `189` LOC = `2,082` LOC)** | Ambos consumen MIR verificado de Seed para producir ejecutables nativos en macOS `arm64` y Linux `x64`. En la campaña medida (`docs/LLVM_EXPERIMENTAL.md:146-161`), C99 compila `1.39x–2.56x` más rápido en frío, genera binarios más pequeños en 11/12 casos y ejecuta `steady_state.wysna` `1.61x` más rápido (`40.17 ms` vs `64.68 ms`). | `independencia deliberada` (experimento opt-in) | **KEEP** C99 como default; **FREEZE** LLVM (**`SIMP-04`**) | **0 horas de ingeniería invertidas en expandir LLVM** durante el próximo ciclo (`2,082` LOC congeladas en modo mantenimiento mínimo). Se conserva el código porque ya pasa `209/209` fixtures y sirve como segundo consumidor de MIR que valida que MIR no dependa de semántica implícita de C. | **Riesgo bajo.** Consumidores de LLVM: `.github/workflows/ci.yml` (`llvm-smoke`), `.github/workflows/llvm-qualification.yml`, `tests/unit/test_llvm_backend.py`. |
| **Oráculo Intérprete (`src/runtime/Interpreter.py` + `HostAbi.py`: `4,607` LOC Python en `runtime/`)** | **Oráculo `AST → C99` (`src/codegen/CCodeGen.py`: `943` LOC) y Runtime C99 (`runtime/wysna_runtime*.h` + `wysna_host*.h`: `13,517` LOC C)** | Tres implementaciones de ejecución de las primitivas del lenguaje y del host. Además, los archivos Python del intérprete viven mezclados dentro del directorio `runtime/` junto con los headers `.h` de C99 (`pyproject.toml:37-41`). | `independencia deliberada` (oráculos) + `deuda injustificada` (mezcla Python/C en `runtime/`) | **KEEP** ambos oráculos; **SIMPLIFY** estructura y contratos de `HostAbi` (**`SIMP-05`**) | Conservar ambos oráculos es crítico (detectan bugs donde MIR C99 e intérprete divergen), pero unificar la tabla de contratos `extern` entre `HostAbi.py`, `wysna_host.h` y `EffectModel.py` elimina bugs como **`AGT-03`** (`8` externs olvidados). | **Riesgo bajo.** Consumidores: `pyproject.toml`, `src/compiler/CompilerService.py:141,249`, `src/run_conformance.py`. |
| **Funciones Globales del Prelude (`read_file`, `write_file`, `args`, `env`, `unix_time`, `dict_items`, `join_strings`, `bytes`)** | **Módulos de `stdlib/` (`std/io.read_text`, `std/io.write_text`, `std/environment.arguments`, `std/environment.get`, `std/time.unix_seconds`, `dict.items()`, `std/text.join`, `std/bytes.encode`)** | Ocho funciones globales del prelude duplican exactamente APIs de `stdlib/` o métodos de colección (`WYSNA_SINTAXIS.md:627-645` vs `docs/STDLIB.md:43-96`) y contaminan el espacio global de nombres (causando el fallo `W1002` al escribir `import "std/environment" as env` o `import "std/bytes"`). | `compatibilidad` | **DEPRECATE** aliases redundantes en documentación y guías de agentes (**`SIMP-06`**) | Reducir de **2–3 formas equivalentes a 1 forma canónica documentada** por operación común (conservando los símbolos en el prelude compilado para no romper los `199` tests de `tests/runtime/` ni `selfhost/`). | **Riesgo bajo** (no se eliminan del compilador en `0.8.x`; se retiran de prompts/ejemplos nuevos para eliminar ambigüedad de autoría). |
| **Dos familias de API en `stdlib/json.wysna` (`boolean`, `string`, `number`, `as_string`, `as_bool`, `as_array`, `as_object`)** | **Segunda familia en el mismo `stdlib/json.wysna` (`from_bool`, `from_str`, `from_int`, `from_float`, `to_str`, `to_bool`, `to_int`, `to_float`, `to_array`, `to_object`)** | En `stdlib/json.wysna` (`780` LOC) coexisten constructores redundantes (`json.boolean(b)` idéntico a `json.from_bool(b)`; `json.string(s)` idéntico a `json.from_str(s)`). `docs/STDLIB.md:38-40` documenta solo `boolean()`/`string()` y omite `from_int()`/`from_str()`/`to_array()`, mientras `wysna schema --emit-codecs` genera `json.from_int()`/`json.from_str()`/`json.to_array()`. | `consolidable` | **SIMPLIFY** (**`SIMP-06`**) | Documentar `from_int`/`from_float`/`from_str`/`from_bool` (constructores infalibles), `get_*`/`to_*` (accesores `T ! JsonError`) y `as_*` (accesores `T?`) con una tabla única en `docs/STDLIB.md`, marcando `json.boolean` y `json.string` como alias de compatibilidad. | **Riesgo bajo.** Consumidores: `stdlib/json.wysna`, `src/tooling/SchemaCLI.py`, `examples/`. |
| **Comandos Agent-First: `graph`, `inspect`, `explain`, `api`** | **Comandos Agent-First: `check`, `fix`, `patch`, `schema`, `skills`, `capabilities`** | `inspect` llama internamente a `graph` (`AgentGraphCLI.py:181`); `graph` y `schema` llaman internamente a `explain` (`CompilerService.py:206`, `SchemaCLI.py:619`); `api` sin `--manifest` e `inspect` sin `--symbol` ambos listan firmas de declaraciones públicas. | `consolidable` | **MERGE** capa interna de servicio semántico; **FREEZE** superficie de 11 comandos CLI/MCP | Evitar recalcular `lower_hir` + `lower_mir` + `explain` múltiples veces en una misma sesión cacheando el `explain_report` y el `semantic_graph` dentro de `CheckedProgram` por generación. | **Riesgo bajo.** Consumidores: `src/compiler/CompilerService.py`, `src/tooling/AgentGraphCLI.py`, `src/tooling/SchemaCLI.py`, `src/tooling/McpServer.py`. |
| **Artefactos y archivos huérfanos en el árbol (`NEXT_STEPTS_AF.md`, `src/wysna.egg-info/`, snippets rotos en `WYSNA_SINTAXIS.md` y `docs/PROJECTS.md`)** | **Documentación canónica (`SPEC.md`, `docs/STDLIB.md`, `docs/KNOWN_LIMITS.md`, `docs/AI_AGENT_GUIDE.md`)** | `NEXT_STEPTS_AF.md` (`90 KB` untracked en la raíz con typo en el nombre) es un borrador de auditoría de `v0.6.0`/`v0.7.0`; `src/wysna.egg-info/` son artefactos de build local; y `KNOWN_LIMITS.md`/`AI_AGENT_GUIDE.md`/`TYPE_SYSTEM_DESIGN.md` contienen párrafos obsoletos de `v0.5.0–v0.7.0`. | `deuda injustificada` | **REMOVE** basura de árbol y párrafos obsoletos (ver **`DX-01`**, **`DX-02`** en `08-CALIDAD-DX.md`) | Eliminar `~90 KB` de ruido en raíz y **11 contradicciones documentales** verificadas en `02-ESTADO-REAL.md` §6. | **Riesgo nulo.** |

---

## 2. Evaluación Específica de las Preguntas Arquitectónicas Obligatorias

### 2.1 ¿Qué propiedad verificable justifica AST, HIR y MIR?

- **AST (`src/node/NodeType.py`):** Representa la sintaxis superficial exacta con posiciones de línea/columna y permite adjuntar trivia de comentarios para `wysna fmt` (`src/formatter/Trivia.py`), diagnósticos de sintaxis con recuperación de hasta 100 errores y el oráculo diferencial `AST → C99` (`src/codegen/CCodeGen.py`), el cual sirve específicamente para detectar bugs introducidos por las transformaciones de HIR o MIR. **Propiedad verificable:** `fmt(fmt(src)) == fmt(src)` y `ast.to_dict()["wysna_ast_version"] == "0.1"`.
- **HIR (`src/hir/`):** Desazucara el AST tipado tras la monomorfización, explicita las conversiones implícitas a `Ok(...)`/`Some(...)` (`_mark_result_lift`), convierte accesos anidados mutables en rutas Copy-on-Write explícitas (`HirLValuePath` distinguiendo raíz `MutableLocal` con detach CoW de raíz `ClassObjectExpr` sin detach CoW) y fuerza el orden de evaluación estricto de izquierda a derecha (`HirSequence` / `HirBindTemp`). **Propiedad verificable (`src/hir/HirVerifier.py`):** Ningún nodo tiene `type_id == 0` (`TYPE_ERROR`), todo `break`/`continue` ocurre a `loop_depth > 0` y toda ruta de asignación CoW tiene una raíz válida.
- **MIR (`src/mir/`):** Convierte el árbol jerárquico de HIR en un grafo de flujo de control (CFG) plano de bloques básicos con terminadores explícitos (`goto`, `branch`, `return`, `unreachable`), slots tipados con clase de almacenamiento (`int64_t`/`double`/`bool` desboxeados vs `WVal`), máquinas de estados CPS para `async fn` (`AsyncLowering.py`) e instrucciones explícitas de ciclo de vida ARC (`clone`, `store` con `moves`, `release`). **Propiedad verificable (`src/mir/Verifier.py`):** Análisis de punto fijo sobre el CFG que rechaza lecturas de slots no inicializados o ya movidos (`consumed`), movimientos desde parámetros prestados (`borrowed`), retornos con referencias `owned` vivas sin liberar y extracciones de `Result`/`ErrorUnion` sin un `test` dominante que pruebe la variante.
- **Conclusión:** Las **tres representaciones están justificadas en Seed** y deben conservarse (`KEEP`). Lo que **no** está justificado es mantener la clase wrapper `HirCCodeGen` (`src/hir/HirCCodeGen.py`) fingiendo que existe un backend `"Seed HIR C99"` distinto de `"Seed MIR C99"` (**`SIMP-03`**).

### 2.2 ¿Qué features del lenguaje deberían vivir en la stdlib (y viceversa)?

1. **Features que hoy viven en el prelude global del lenguaje y deberían vivir (canónicamente) en `stdlib/`:**
   - `read_file(path)` y `write_file(path, content)` → ya existen como `io.read_text(path)` e `io.write_text(path, content)` en `std/io`.
   - `env(name)` y `args()` → ya existen como `environment.get(name)` y `environment.arguments()` en `std/environment`.
   - `unix_time()` → ya existe como `time.unix_seconds()` en `std/time`.
   - `join_strings(parts, sep)` → ya existe como `text.join(parts, sep)` en `std/text`.
   - `bytes(s)` → ya existe como `bytes.encode(s)` en `std/bytes`.
   - `dict_items(d)` → ya fue superado por el método nativo `d.items()` y la iteración directa `for k, v in d`.
   Mantener esos 8 nombres en el prelude global solo por compatibilidad con tests antiguos y `selfhost/` está bien, pero deben marcarse como legado de bootstrap y retirarse de las guías de usuario/agentes (**`SIMP-06`**).
2. **Features que NO deben añadirse como sintaxis del lenguaje porque bastan funciones/herramientas existentes:**
   - No añadir sintaxis de validación de esquemas JSON en la gramática: `struct` con `requires` + `wysna schema --emit-codecs` (reparado con **`LANG-03`**) lo resuelve como código Wysna normal.
   - No añadir bloques `defer` o `try/finally` a la gramática en este ciclo: `std/resources.scope` y la liberación automática al último propietario de `File` y `Bytes` cubren los recursos estándar.

---

## 3. Backlog Categorizado de Simplificación (`SIMP-01` .. `SIMP-06`)

### `SIMP-01` · Unificar mediante suite de paridad estricta y caché de `CheckedProgram` las herramientas Agent-First (`src/tooling/` vs `selfhost/src/09_*.wysna`)

- **Prioridad:** P1
- **Decisión:** SIMPLIFY
- **Evidencia:** [tree_inventory.txt](evidence/tree_inventory.txt) (`src/tooling/`: `7,876` LOC Python vs `selfhost/src/09_*.wysna`: `8,935` LOC Wysna), [src/compiler/CompilerService.py:198-209](../../src/compiler/CompilerService.py#L198-L209), [src/tooling/AgentGraphCLI.py:181](../../src/tooling/AgentGraphCLI.py#L181), [src/tooling/SchemaCLI.py:619](../../src/tooling/SchemaCLI.py#L619). Nivel: **verificado**.
- **Problema:** La mitad exacta del código del compilador autoalojado (`8,935` de `17,911` líneas en `selfhost/src/09_*.wysna`) es una reimplementación manual en Wysna de los 11 comandos de `src/tooling/`. Como no comparten código y no existía un test diferencial que comparara el JSON completo emitido por `src/tooling/` contra el JSON emitido por `09_*.wysna` sobre los mismos programas, ambos divergieron en `fmt` (**`AGT-01`**), en `check --output-format=json-v1` y `graph_hash` (**`AGT-04`**), y en `visibility` (**`AGT-05`**). Además, dentro de Seed, invocar `graph`, `inspect` y `schema` recalcula `lower_hir` + `lower_mir` + `explain` desde cero cada vez sobre el mismo `CheckedProgram`.
- **Propuesta:**
  1. Cachear perezosamente (`lazy property`) el reporte de `explain` y el `semantic_graph` dentro de `CheckedProgram` en Seed para que `graph`, `inspect`, `schema` y `McpServer` reutilicen un único cálculo MIR/grafo por generación.
  2. Crear un test de contrato diferencial (`tests/unit/test_tooling_engine_parity.py`) que ejecute los comandos `check`, `fmt`, `graph`, `inspect`, `explain`, `schema` (`--emit-codecs`), `fix` y `api` con `--output-format=json-v1` tanto en Seed (`./wysna`) como en el binario nativo Selfhost (`.wysna_cache/wysna_selfhost`) sobre un corpus compartido y exija igualdad estructural de esquemas, códigos, `graph_hash`, codecs emitidos y código formateado.
  3. Congelar la adición de nuevas herramientas en `selfhost/src/09_*.wysna`.
- **Alternativas:**
  1. *No hacer nada:* seguir ampliando `src/tooling/` y `09_*.wysna` en paralelo sin gate de paridad JSON; la deriva seguirá creciendo.
  2. *Eliminar `09_*.wysna` del compilador Selfhost:* ahorraría `8,935` líneas en `selfhost/src/`, pero rompería la promesa publicada en `README.md:23` y `docs/NATIVE_SDK.md:13-26` de que el Native SDK ejecuta `graph`, `inspect`, `patch`, `schema`, `fix`, `mcp` y `lsp` sin Python.
  3. *Eliminar `src/tooling/*.py` y delegar todo `./wysna <tool>` al binario Selfhost:* obligaría a recompilar el bootstrap de Selfhost (`~21 s`) antes de poder usar cualquier herramienta tras editar el compilador y perdería el soporte para perfiles exclusivos de Seed (`browser-library`, `wasi-command`).
- **Impacto (1–5):** Usuario: **3** | Agentes: **5** | Reducción de complejidad: **4**.
- **Esfuerzo:** **M (2–3 días)**. Supuestos: ~40 líneas de caché en `CheckedProgram.py` / `CompilerService.py` y ~180 líneas en `tests/unit/test_tooling_engine_parity.py`.
- **Riesgo:** **Bajo.** No elimina capacidades de ninguna distribución y blinda la paridad entre ambas.
- **Compatibilidad y migración:** Transparente para consumidores CLI, MCP y Native SDK.
- **Componentes afectados:** `src/compiler/CheckedProgram.py`, `src/compiler/CompilerService.py`, `tests/unit/test_tooling_engine_parity.py`.
- **Depende de:** `AGT-01`, `AGT-04`, `AGT-05`, `LANG-03`.
- **Criterio de aceptación:** `python3 -m unittest tests/unit/test_tooling_engine_parity.py` pasa al 100% verificando igualdad de salidas `json-v1` entre Seed y Selfhost para `check`, `fmt`, `graph`, `inspect`, `explain`, `schema` y `fix`.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

### `SIMP-02` · Dejar de duplicar `17,911` líneas en cada PR generando `selfhost/compiler.wysna` como artefacto de build o pre-commit verificado

- **Prioridad:** P2
- **Decisión:** SIMPLIFY
- **Evidencia:** [tree_inventory.txt](evidence/tree_inventory.txt) (`selfhost/src/` `34` archivos = `17,911` líneas; `selfhost/compiler.wysna` = `17,911` líneas idénticas), [tools/bundle_selfhost.py:1-88](../../tools/bundle_selfhost.py#L1-L88), [src/compiler/Bootstrap.py:15-48](../../src/compiler/Bootstrap.py#L15-L48). Nivel: **verificado**.
- **Problema:** Cada cambio de una sola línea en cualquiera de los 34 módulos de `selfhost/src/*.wysna` exige regenerar y commitear también `selfhost/compiler.wysna` (`17,911` líneas). Esto duplica el tamaño de todos los diffs de Git en PRs que tocan Selfhost, provoca conflictos de merge innecesarios entre ramas y añade `17,911` líneas redundantes al árbol fuente (`35,822` líneas totales en `selfhost/`). La única razón por la que `selfhost/compiler.wysna` existe es que el bootstrap compila un único archivo concatenado (`Bootstrap.py:19`) y `tools/bootstrap_from_native.sh` lo toma como entrada.
- **Propuesta:** Hacer que `src/compiler/Bootstrap.py` (y `Makefile` / `tools/package_native.py`) ensamble en memoria o en `.wysna_cache/compiler.wysna` el bundle determinista a partir de `selfhost/src/*.wysna` usando `tools/bundle_selfhost.py` cuando `selfhost/src/` esté presente (y si se desea conservar `selfhost/compiler.wysna` para `tools/bootstrap_from_native.sh` sin Python, marcarlo como `linguist-generated=true binary` en `.gitattributes` o generarlo en el empaquetado del SDK).
- **Alternativas:**
  1. *No hacer nada:* seguir versionando y revisando diffs duplicados de `17,911` líneas en Git.
  2. *Resolverlo con lo que ya existe (Quick-Win sin quitar el archivo de Git):* añadir `selfhost/compiler.wysna linguist-generated=true` en `.gitattributes` para que GitHub oculte el diff duplicado en los PRs, y hacer que `Bootstrap.py` auto-ejecute `bundle_selfhost` si detecta que `selfhost/src/*.wysna` es más reciente que `selfhost/compiler.wysna`.
- **Impacto (1–5):** Usuario: **1** | Agentes: **4** (evita que agentes y revisores carguen o editen por error `selfhost/compiler.wysna` en vez de `selfhost/src/*.wysna`) | Reducción de complejidad: **4**.
- **Esfuerzo:** **S (<0.5 días)**. Supuestos: ~15 líneas en `.gitattributes` y `src/compiler/Bootstrap.py`.
- **Riesgo:** **Bajo.** Mantiene `test_selfhost_split_conformance.py` y compatibilidad total con `tools/bootstrap_from_native.sh`.
- **Compatibilidad y migración:** Transparente; ningún consumidor externo se rompe.
- **Componentes afectados:** `.gitattributes`, `src/compiler/Bootstrap.py`, `tools/bundle_selfhost.py`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** Editar un archivo en `selfhost/src/` y ejecutar `./wysna build --engine=compiler ...` regenera automáticamente el bundle antes del bootstrap sin requerir un paso manual separado, y `.gitattributes` marca `selfhost/compiler.wysna` como `linguist-generated=true`.
- **Requiere aprobación:** no (con la opción `.gitattributes` + auto-bundle en `Bootstrap.py`).
- **Estado:** implementado.

---

### `SIMP-03` · Retirar la fachada redundante `HirCCodeGen` y renombrar la columna `"Seed HIR C99"` a `"Seed MIR C99"`

- **Prioridad:** P2
- **Decisión:** MERGE
- **Evidencia:** [src/hir/HirCCodeGen.py:1-44](../../src/hir/HirCCodeGen.py#L1-L44), [src/compiler/CompilerService.py:222-244](../../src/compiler/CompilerService.py#L222-L244), [src/run_conformance.py:31,528-559](../../src/run_conformance.py#L528-L559), [bench/run_bench.py:32,145-166](../../bench/run_bench.py#L145-L166), [docs/ENGINE_PARITY_MATRIX.md:24](../ENGINE_PARITY_MATRIX.md#L24). Nivel: **verificado**.
- **Problema:** `src/hir/HirCCodeGen.py` es un vestigio histórico de cuando HIR emitía C directamente. Hoy sus métodos `emit_expr` y `emit_stmt` lanzan `W9999` (`HirCCodeGen.py:34-44`) y `generate()` simplemente ejecuta `mir.Lowering(self.interner).lower(module)` seguido de `mir.CCodeGen(self.interner).generate(self.mir)` —exactamente lo mismo que `CompilerService.emit_c(checked)` (`CompilerService.py:222-244`), pero saltándose `CompilerService._enable_http_runtime` y `CompilationSession`! Peor aún, la documentación (`docs/OWNERSHIP_LIFETIME.md:4,117`, `docs/ENGINE_PARITY_MATRIX.md:24`, `bench/run_bench.py:107`) sigue llamando a este camino `"Wysna Seed (HIR C99)"` o `"HirCCodeGen is the default native emitter"`, confundiendo a cualquier lector sobre si el emisor C99 consume HIR o MIR.
- **Propuesta:**
  1. Hacer que `src/run_conformance.py`, `bench/run_bench.py` y `tests/engine_harness.py` usen `CompilerService.emit_c(checked)` como ruta canónica de emisión C99 de Seed.
  2. Mantener un alias delgado en `src/hir/HirCCodeGen.py` solo si algún test unitario legado (`test_c06_default_backend.py`) lo importa directamente, o actualizar esos 3 tests unitarios para usar `CompilerService.emit_c`.
  3. Actualizar las etiquetas de `docs/ENGINE_PARITY_MATRIX.md`, `tools/ci_conformance.py` y `docs/OWNERSHIP_LIFETIME.md:117` de `"Wysna Seed (HIR C99)"` a `"Wysna Seed (MIR C99)"`.
- **Alternativas:**
  1. *No hacer nada:* mantener `HirCCodeGen.py` duplicando `CompilerService.emit_c` y seguir llamando `"HIR C99"` a un backend que en realidad emite desde MIR.
  2. *Resolverlo con lo que ya existe:* delegar en `CompilerService.emit_c`.
- **Impacto (1–5):** Usuario: **2** | Agentes: **3** | Reducción de complejidad: **4** (elimina una falsa abstracción y alinea nombres con la arquitectura real).
- **Esfuerzo:** **S (<1 día)**. Supuestos: actualizar `src/run_conformance.py:528-558`, `bench/run_bench.py:145-160`, `tools/ci_conformance.py:90,100,172` y `docs/OWNERSHIP_LIFETIME.md:117`.
- **Riesgo:** **Bajo.** El código C emitido por `HirCCodeGen.generate` y `CompilerService.emit_c` proviene del mismo `mir.CCodeGen`.
- **Compatibilidad y migración:** Se preserva la clave interna `'hir'` en `tests/engine_profiles.json` y `summary.json` si los workflows de CI la leen por nombre, cambiando únicamente la etiqueta visible y unificando el código en `CompilerService.emit_c`.
- **Componentes afectados:** `src/hir/HirCCodeGen.py`, `src/compiler/CompilerService.py`, `src/run_conformance.py`, `bench/run_bench.py`, `tools/ci_conformance.py`, `docs/OWNERSHIP_LIFETIME.md`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** `python3 -m tests.unit.test_architecture_contract` y `./wysna test` pasan al 100%, y la documentación no afirma en ningún lugar que `HirCCodeGen` emita C directamente desde HIR.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

### `SIMP-04` · Congelar el backend directo LLVM (`--backend=llvm-experimental` y `LLVM SDK`) sin eliminar su oráculo MIR

- **Prioridad:** P2
- **Decisión:** FREEZE
- **Evidencia:** [docs/LLVM_EXPERIMENTAL.md:142-162](../LLVM_EXPERIMENTAL.md#L142-L162) y mediciones propias en [perf_measurements.json](evidence/perf_measurements.json) (`hello`: C99 build `209.09 ms` vs LLVM build `480.57 ms`; `steady_state.wysna` en `docs/LLVM_EXPERIMENTAL.md:159`: C99 `40.17 ms` vs LLVM `64.68 ms`, `1.61x` más lento en LLVM; tamaño de binario mayor en LLVM en 11 de 12 casos). Nivel: **verificado**.
- **Problema:** El backend LLVM (`src/mir/Llvm*.py`, `src/compiler/Llvm*.py`, `runtime/wysna_llvm_*`, `tools/package_llvm_sdk.py`: `2,082` líneas + un SDK distribuido aparte que requiere Python en el host y no funciona en Windows ni en Selfhost) consume tiempo de mantenimiento cada vez que se añade una operación MIR o primitiva de runtime. A la vez, las mediciones reproducibles demuestran que el backend C99 por defecto (`cc -std=c99 -O2`) ya invoca al optimizador de Clang/LLVM sobre código C99 con funciones `static inline` de `runtime/wysna_runtime*.h`, logrando **mejor inlining entre el programa y el runtime C** que el backend `--backend=llvm-experimental` (donde el runtime vive precompilado en una unidad de traducción separada `wysna_llvm_runtime.c` detrás de llamadas opacas `@wysna_llvm_*`!).
- **Propuesta:**
  1. **No eliminar** el backend LLVM (cumple la regla de protección: sirve como segundo consumidor independiente de MIR que verifica que MIR no dependa de trucos léxicos de C).
  2. **Congelar (`FREEZE`)** toda inversión de nuevas optimizaciones, nuevos targets (Windows/Wasm) o paridad de herramientas en `llvm-experimental` y `LLVM SDK`.
  3. Reducir su costo en CI manteniendo únicamente el job ligero `llvm-smoke` en PRs normales y moviendo la campaña pesada de calificación/empaquetado (`llvm-qualification.yml`) exclusivamente a ejecución manual (`workflow_dispatch`) o releases etiquetadas cuando cambien `src/mir/` o `runtime/wysna_llvm_*`.
- **Alternativas:**
  1. *No hacer nada (seguir invirtiendo en optimizar LLVM para alcanzar a C99):* requeriría implementar LTO cross-language (ThinLTO entre el `.ll` emitido y `wysna_llvm_runtime.c`) y portar LLVM a Selfhost, distrayendo semanas del foco de producto.
  2. *Eliminar por completo el backend LLVM (`2,082` LOC):* perderíamos el único consumidor de MIR que no pasa por un compilador C99.
- **Impacto (1–5):** Usuario: **2** | Agentes: **2** | Reducción de complejidad: **5** (frena una línea entera de expansión arquitectónica sin retorno medible hoy).
- **Esfuerzo:** **S (<0.5 días)**. Supuestos: documentar la política de congelación en `docs/SCOPE.md` y ajustar triggers de `.github/workflows/llvm-qualification.yml` si aplica.
- **Riesgo:** **Bajo.** El smoke test `llvm-smoke` sigue protegiendo el contrato MIR → LLVM.
- **Compatibilidad y migración:** `--backend=llvm-experimental` sigue funcionando exactamente como hoy.
- **Componentes afectados:** `docs/SCOPE.md`, `docs/LLVM_EXPERIMENTAL.md`, `.github/workflows/llvm-qualification.yml`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** Cero ítems de expansión LLVM en los Hitos 1, 2 y 3 del roadmap; `test_llvm_backend.py` sigue pasando sin regresiones.
- **Requiere aprobación:** no (reafirma y endurece la política actual de `docs/SCOPE.md:38-40`).
- **Estado:** implementado.

---

### `SIMP-05` · Conservar los oráculos de ejecución (Intérprete y `AST → C99`) pero unificar el registro de contratos `HostAbi`

El cierre de v0.8.0 refuerza la comprobación inicial del subconjunto stdlib: exige igualdad entre los 49 bindings host y sus efectos, verifica aridades Python/C e incorpora pruebas de préstamo/cierre de buffers en los cuatro motores y en el SDK instalado. Ver [HOST_ABI_CONTRACTS.md](../HOST_ABI_CONTRACTS.md). Los oráculos mantienen implementaciones independientes y las declaraciones FFI de usuario siguen sin recibir confianza por nombre.

- **Prioridad:** P2
- **Decisión:** KEEP
- **Evidencia:** [runtime/Interpreter.py](../../runtime/Interpreter.py), [runtime/HostAbi.py](../../runtime/HostAbi.py), [runtime/wysna_host.h](../../runtime/wysna_host.h), [src/codegen/CCodeGen.py](../../src/codegen/CCodeGen.py), [src/compiler/EffectModel.py:66-100](../../src/compiler/EffectModel.py#L66-L100). Nivel: **verificado**.
- **Problema:** Podría parecer tentador eliminar `src/codegen/CCodeGen.py` (`AST → C99`, `943` LOC) o `src/runtime/Interpreter.py` (`4,607` LOC) para quedarse solo con `MIR → C99`. Sin embargo, la evidencia histórica del proyecto (`NEXT_STEPTS_AF.md:96,128`, donde el bug `A01` se descubrió precisamente por la diferencia entre intérprete y C99, y nuestra hallazgo en [sqlite_ffi_perf.json](evidence/sqlite_ffi_perf.json) comparando conteos `alloc`/`retain` entre `ast`, `hir` y `selfhost`) demuestra que ambos oráculos detectan clases distintas de errores. El verdadero problema de mantenimiento no es que existan los oráculos, sino que cada nueva primitiva de host (`wysna_host_*`) debe registrarse a mano en **cuatro sitios desconectados** (`runtime/wysna_host*.h`, `src/runtime/HostAbi.py`, `src/compiler/EffectModel.py` y `src/compiler/NativeToolchain.py`), lo que causó el bug **`AGT-03`** (8 funciones de `std/http` y `std/crypto` registradas en `HostAbi.py` y `wysna_host.h` pero olvidadas en `EffectModel.py`).
- **Propuesta:**
  1. **Mantener (`KEEP`)** `src/runtime/Interpreter.py` y `src/codegen/CCodeGen.py` (`AST → C99` restringido al arnés de pruebas).
  2. Añadir una verificación automática en `tests/unit/test_architecture_contract.py` que cruce las funciones `wysna_host_*` despachadas en `src/runtime/HostAbi.py`, declaradas en `stdlib/*.wysna` y modeladas en `src/compiler/EffectModel.py:HOST_EFFECTS`, fallando en CI si algún símbolo `wysna_host_*` falta en cualquiera de las tres tablas.
- **Alternativas:**
  1. *No hacer nada:* seguir sincronizando a mano 4 archivos cada vez que se añade una función a `stdlib/`, arriesgando nuevos fallos `W5006`.
  2. *Eliminar el oráculo `AST → C99` (`src/codegen/CCodeGen.py`):* ahorraría `943` líneas, pero dejaría a `MIR → C99` sin un segundo emisor C99 en Seed capaz de aislar si un bug de memoria C proviene del lowering HIR/MIR o de los headers `runtime/wysna_runtime*.h`.
- **Impacto (1–5):** Usuario: **2** | Agentes: **4** | Reducción de complejidad: **4**.
- **Esfuerzo:** **S (<0.5 días)**. Supuestos: ~35 líneas en `tests/unit/test_architecture_contract.py`.
- **Riesgo:** **Bajo.**
- **Compatibilidad y migración:** Sin impacto en usuarios; blinda la consistencia interna del runtime.
- **Componentes afectados:** `tests/unit/test_architecture_contract.py`, `src/compiler/EffectModel.py`, `src/runtime/HostAbi.py`.
- **Depende de:** `AGT-03`.
- **Criterio de aceptación:** `python3 -m tests.unit.test_architecture_contract` verifica que el conjunto de símbolos `extern "C"` en `stdlib/*.wysna` es un subconjunto exacto tanto de los handlers de `src/runtime/HostAbi.py` como de `src/compiler/EffectModel.py:HOST_EFFECTS`.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

### `SIMP-06` · Consolidar una única API canónica en documentación y prompts para I/O, entorno, tiempo y constructores `std/json`

- **Prioridad:** P2
- **Decisión:** DEPRECATE
- **Evidencia:** [WYSNA_SINTAXIS.md:627-645](../../WYSNA_SINTAXIS.md#L627-L645) vs [docs/STDLIB.md:38-41,63-66,91-96](../STDLIB.md#L38-L41), [api_std_json.txt](evidence/api_std_json.txt), y observación de Fase 2 en [prog_a_cli.wysna](evidence/prog_a_cli.wysna). Nivel: **verificado**.
- **Problema:** Un agente o desarrollador que escribe un script en Wysna enfrenta duplicación de APIs en cuatro áreas básicas:
  1. **Archivos de texto:** prelude `read_file(p)` / `write_file(p, c)` vs `std/io.read_text(p)` / `std/io.write_text(p, c)`.
  2. **Argumentos y variables de entorno:** prelude `args()` / `env(k)` vs `std/environment.arguments()` / `std/environment.get(k)`.
  3. **Tiempo Unix:** prelude `unix_time()` vs `std/time.unix_seconds()`.
  4. **Diccionarios y Strings:** prelude `dict_items(d)` vs método `d.items()` / `for k, v in d`; prelude `join_strings(xs, sep)` vs `std/text.join(xs, sep)`.
  5. **Constructores y accesores en `std/json`:** `json.boolean(b)` vs `json.from_bool(b)`; `json.string(s)` vs `json.from_str(s)`; y en `docs/STDLIB.md:38-40` ni siquiera se documentan `json.from_int(n)`, `json.from_float(x)`, `json.to_array(v)`, `json.to_object(v)`, `json.to_str(v)` ni `json.get_required(v, k)`!
- **Propuesta:**
  1. **Sin romper código existente** (conservando todos los símbolos en el compilador y en `stdlib/json.wysna` para compatibilidad con `selfhost/` y tests), establecer y documentar en `WYSNA_SINTAXIS.md`, `docs/STDLIB.md`, `wysna skills` y `bench/ai_eval/harness.py` **una sola ruta canónica recomendada**:
     - Para scripts de un solo archivo: builtins directos `args()`, `env(name)`, `read_file(path)`, `write_file(path, data)` y métodos nativos `d.items()` / `for k, v in d` (marcando `dict_items` y `join_strings` en `SPEC.md` §11.1 como primitivos internos de bootstrap).
     - En `docs/STDLIB.md` (`std/json`), documentar explícitamente la familia tipada completa (`from_int`, `from_float`, `from_str`, `from_bool`, `array`, `object`, `null`; `get_int`, `get_float`, `get_str`, `get_bool`, `get_optional_*`, `get_required`, `to_int`, `to_float`, `to_str`, `to_bool`, `to_array`, `to_object`), marcando `json.string` y `json.boolean` como alias de `from_str` y `from_bool`.
- **Alternativas:**
  1. *No hacer nada:* mantener `docs/STDLIB.md` incompleto en `std/json` y seguir enseñando `for k, v in dict_items(d)` en `bench/ai_eval/harness.py:47` mientras `WYSNA_SINTAXIS.md:547` enseña `for k, v in scores`.
  2. *Eliminar `read_file`, `write_file`, `args`, `env`, `dict_items`, `join_strings` del compilador:* rompería `selfhost/src/*.wysna` (que depende de ellos para compilarse sin importar `stdlib/`) y más de 60 tests en `tests/runtime/`.
- **Impacto (1–5):** Usuario: **4** | Agentes: **5** (elimina decisiones arbitrarias en generación de código y documenta las funciones reales de `std/json`) | Reducción de complejidad: **3**.
- **Esfuerzo:** **S (<1 día)**. Supuestos: actualización coordinada en `docs/STDLIB.md`, `WYSNA_SINTAXIS.md`, `SPEC.md`, `src/tooling/AgentGraphCLI.py` (`skills`) y `bench/ai_eval/harness.py`.
- **Riesgo:** **Bajo** (cero cambios incompatibles en el compilador).
- **Compatibilidad y migración:** 100% retrocompatible.
- **Componentes afectados:** `docs/STDLIB.md`, `WYSNA_SINTAXIS.md`, `SPEC.md`, `src/tooling/AgentGraphCLI.py`, `bench/ai_eval/harness.py`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** `docs/STDLIB.md` documenta con firma exacta los 38 símbolos públicos de `std/json` reportados por `wysna api std/json`, y `WYSNA_SINTAXIS.md` / `harness.py` presentan una única forma canónica para iterar diccionarios (`for k, v in d`) y construir valores JSON (`json.from_int`, `json.from_str`, `json.from_bool`).
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

## 4. Hipótesis (`RESEARCH`)

### `SIMP-R01` · Hipótesis: Compilar las herramientas Agent-First una sola vez desde código Wysna compartido o invocar el binario nativo desde la CLI de Seed

- **Observación:** Incluso después de **`SIMP-01`** (que blinda la paridad mediante tests diferenciales), mantener `7,876` líneas en `src/tooling/*.py` y `8,935` líneas en `selfhost/src/09_*.wysna` significa que cualquier mejora futura a `schema`, `fix`, `graph` o `lsp` requiere escribirse dos veces (una en Python y otra en Wysna).
- **Cómo falsificarla:** Medir en el próximo ciclo cuántos PRs tocan simultáneamente `src/tooling/` y `selfhost/src/09_*.wysna`. Si **`>= 3` PRs** requieren cambios duplicados en ambos árboles y `09_*.wysna` ya alcanza el 100% de paridad en `test_tooling_engine_parity.py`, evaluar hacer que la CLI de Python (`src/tooling/CLI.py`) delegue `graph`, `inspect`, `patch`, `schema`, `fix` y `api` (para el perfil `native-core-v1`) directamente al binario `.wysna_cache/wysna_selfhost`, retirando las implementaciones Python duplicadas.
