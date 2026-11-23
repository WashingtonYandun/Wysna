# 07 · Rendimiento: Mediciones Reproducibles del Ciclo de Desarrollo y Runtime

> Auditoría histórica de la línea base `a2549fa`. Los problemas y mediciones describen ese snapshot; los estados de implementación y el ciclo vigente se consultan en [00-INDEX.md](00-INDEX.md) y [12-SIGUIENTE-CICLO-LENGUAJE.md](12-SIGUIENTE-CICLO-LENGUAJE.md).

## 1. Entorno, Condiciones y Resumen de Mediciones Reproducibles

Todas las cifras de este documento provienen de mediciones ejecutadas sobre el commit `a2549fa01294b20f3b893542706015ffe00869f3` en la máquina local y guardadas en [perf_measurements.json](evidence/perf_measurements.json), [perf_clone_syntax.txt](evidence/perf_clone_syntax.txt), [sqlite_ffi_perf.json](evidence/sqlite_ffi_perf.json) y [wysna_test_baseline.txt](evidence/wysna_test_baseline.txt):

- **Entorno verificado:** macOS Darwin `25.6.0` (`arm64`), CPU `Apple M3`, RAM `8,589,934,592` bytes (`8 GB`), Python `3.14.7`, C compiler `Apple clang version 21.0.0 (clang-2100.4.1.1)`.
- **Protocolo:** Mediana de $N$ muestras (`3`, `5`, `10` o `15` según la tabla) tras ejecución de calentamiento (*warmup*), midiendo tiempo de pared en milisegundos, tamaño de archivo en bytes y memoria residente máxima (`ru_maxrss`). Lo que no pudo medirse en este host se declara explícitamente como **"no medido"** y se incluye en la sección **Hipótesis (`RESEARCH`)** con su protocolo.

### 1.1 Resumen Consolidado de las 9 Dimensiones de Rendimiento

| Dimensión requerida | Medición verificada | Comando / Evidencia | Diagnóstico |
| :--- | :--- | :--- | :--- |
| **1. Cold start de un script y chequeo (`01_hello_world.wysna`)** | • `./wysna check` (Seed Python CLI): **`75.38 ms`** (muestras: `73.89, 73.60, 78.26, 80.13, 75.38`)<br>• `./wysna check --engine=compiler` (Python wrapper → nativo): **`101.23 ms`**<br>• `.wysna_cache/wysna_selfhost check` (binario nativo directo): **`4.66 ms`** (`16.2x` más rápido)<br>• `McpServer.tool_wysna_check` (en memoria): **`1.42 ms`** (`53.1x` más rápido)<br>• `./wysna run` (intérprete Python): **`76.78 ms`**<br>• Binario nativo compilado `./hello_release`: **`2.20 ms`** (`34.9x` más rápido) | [perf_measurements.json](evidence/perf_measurements.json) | El intérprete Python paga `~73 ms` solo por arrancar CPython e importar `src/`. Para scripts repetitivos o bucles de agentes, el binario nativo (`2.20–4.66 ms`) y MCP (`1.42 ms`) eliminan ese costo. |
| **2. Compilación completa (programa grande: `selfhost/compiler.wysna`, `17,911` LOC)** | • **Seed `wysna check --stats`:** **`4,711.43 ms`** (`peak_rss: 254.9 MB`). Desglose real: `lex: 299.35 ms`, `parse: 430.66 ms`, `check: 1,467.30 ms`, y **`2,514.12 ms` (`53.4%`) desperdiciados clonando `checked.ast` 4 veces** para `analyze_reference_cycles` (`1,893.18 ms`) y `lint_discarded_expressions` (`727.10 ms`)!<br>• **Seed `wysna emit-c --stats`:** **`13,680.48 ms`** (`peak_rss: 361.5 MB`, `270,957` líneas C / `9.89 MB`). Desglose: `hir: 2,818.16 ms`, **`mir: 7,882.21 ms` (de los cuales `4,630.72 ms` son 8 llamadas a `Verifier.verify`!)**, `c_emit: 695.63 ms`.<br>• **Selfhost Stage-2 `--check`:** **`6,214.71 ms`** (`--check-mir`: `6,255.90 ms`). | [perf_measurements.json](evidence/perf_measurements.json), [perf_clone_syntax.txt](evidence/perf_clone_syntax.txt) | **Dos cuellos de botella demostrados en Seed:** 4 clonaciones profundas redundantes de `checked.ast` (**`PERF-01`**) y 8 ejecuciones completas de `mir.Verifier.verify` por módulo (**`PERF-02`**). |
| **3. Compilación incremental (`CompilationSession` sobre `examples/wysofia-ops`)** | • Cold check multi-módulo (`main.wysna` + `audit.wysna` + `std/json`): **`97.38 ms`**<br>• Warm check sin cambios (caché de sesión): **`15.32 ms`** (`6.36x` más rápido)<br>• Edición de comentario/cuerpo manteniendo `interface_digest` idéntico (*early cutoff*): **`23.70 ms`** (`4.11x` más rápido) | [perf_measurements.json](evidence/perf_measurements.json) | El *early cutoff* por `interface_digest` funciona en memoria (LSP/MCP), pero **no se persiste a disco** entre invocaciones CLI de `./wysna check`. |
| **4. Ciclo completo de pruebas del desarrollador (`./wysna test`)** | • `./wysna test` (`make test`): **`548 s` (`9m 08s`)** para `843/843` checks.<br>• Causa: `src/run_conformance.py` compila el paso 11 (`199` archivos HIR/MIR C99) de forma **secuencial en un solo hilo** y ejecuta al final el benchmark completo de 15 muestras (`run_d05_telemetry_tests`), 50 iteraciones de fuzzing (`run_d04_fuzz_tests`) y 10,000 requests de `test_foundations`. | [wysna_test_baseline.txt](evidence/wysna_test_baseline.txt), `src/run_conformance.py:529-599` | **Mayor cuello de botella de DX diaria:** un desarrollador no puede esperar `9` minutos para validar un cambio rápido (**`PERF-04`**). |
| **5. Tiempo de ejecución (Intérprete vs Seed C99 vs Selfhost C99, 15 muestras)** | • `arithmetic.wysna`: Interp `108.84 ms` vs Seed C99 **`3.16 ms`** (`34.5x`) vs Selfhost C99 **`3.11 ms`** (`34.9x`)<br>• `function_calls.wysna`: Interp `173.07 ms` vs Seed C99 **`3.09 ms`** (`56.0x`) vs Selfhost C99 **`4.28 ms`** (`40.4x`)<br>• `structs.wysna`: Interp `94.41 ms` vs Seed C99 **`3.23 ms`** (`29.2x`) vs Selfhost C99 **`3.68 ms`** (`25.6x`)<br>• `dynamic_dispatch.wysna`: Interp `92.91 ms` vs Seed C99 **`4.37 ms`** (`21.3x`) vs Selfhost C99 **`3.06 ms`** (`30.4x`) | [wysna_test_baseline.txt](evidence/wysna_test_baseline.txt) | La ejecución nativa C99 es `5x–56x` más rápida que el intérprete Python. |
| **6. Memoria y densidad ABI v3 (`WVal64` 8B) + overhead ARC/CoW** | • `sizeof(WVal) == 8` bytes (`8` valores por línea de caché de 64B).<br>• `memory_pressure.wysna`: Seed C99 `287.75 ms`, Selfhost C99 `204.60 ms`.<br>• `dict_hash_cache.wysna`: Seed C99 `217.13 ms`, Selfhost C99 `203.21 ms`.<br>• **Brecha grave de ARC en Selfhost:** En `import_csv.wysna` (`1,000` filas), **Seed MIR C99 ejecuta `4` operaciones `retain`** (`alloc: 3,007`, `release: 3,011`), mientras **Selfhost MIR C99 ejecuta `63,600` operaciones `retain`** (`15,900x` más tráfico ARC!) porque Selfhost no implementa `optimize_parameter_reads`! | [perf_measurements.json](evidence/perf_measurements.json), [sqlite_ffi_perf.json](evidence/sqlite_ffi_perf.json) | El optimizador de préstamos de parámetros `str` de Seed funciona de forma sobresaliente (`retain: 4`), pero falta en Selfhost (**`PERF-03`**). Además, `WStructBuf` sigue guardando un array de nombres `const char** keys` en cada instancia de `struct` (**`PERF-05`**). |
| **7. Tamaño de ejecutable nativo y de `.wasm`** | • `01_hello_world.wysna` nativo (`arm64` Mach-O): `--profile=dev` (`-O0 -g`): **`79,128 B` (`77.3 KB`)**; `--profile=release` (`-O2`): **`59,072 B` (`57.7 KB`)**; `--profile=size` (`-Os`): **`59,064 B` (`57.7 KB`)**.<br>• `import_csv.wysna` (+ SQLite adapter): C puro **`37,488 B`**, Seed MIR C99 **`112,656 B`**, Selfhost C99 **`112,552 B`**, Seed AST C99 **`129,664 B`**.<br>• Compilador completo `.wysna_cache/wysna_selfhost`: **`3,451,856 B` (`3.29 MB`)**.<br>• **Tamaño de `.wasm`:** **no medido localmente** (bloqueado por ausencia de Emscripten `5.0.1` y `wasi-sdk 34` en el host; ver `PERF-R01`). | [perf_measurements.json](evidence/perf_measurements.json), [sqlite_ffi_perf.json](evidence/sqlite_ffi_perf.json) | Los ejecutables C99 nativos son muy compactos (`58–112 KB`). Notar que `--profile=release` y `--profile=size` producen prácticamente el mismo tamaño (`59,072 B` vs `59,064 B`) porque ninguno aplica `strip -x` / `-dead_strip` en el enlazado. |
| **8. Latencia de FFI (`import_csv.wysna` → SQLite C FFI, `1,000` filas)** | • **C de referencia (`import_reference.c`):** run **`5.21 ms`** (`user: 1.40 ms, sys: 1.98 ms`), peak RSS **`1.97 MB`** (`1,966,080 B`).<br>• **Seed MIR C99 (`hir`):** run **`6.50 ms`** (`1.25x` respecto a C; `user: 2.55 ms, sys: 2.04 ms`), peak RSS **`2.03 MB`** (`2,031,616 B`).<br>• **Selfhost Stage-2 C99:** run **`6.61 ms`** (`1.27x` respecto a C; `user: 2.90 ms, sys: 1.96 ms`), peak RSS **`2.03 MB`**.<br>• **Seed AST C99:** run **`8.78 ms`** (`1.68x` respecto a C; `alloc: 28,787`), peak RSS **`2.06 MB`**. | [sqlite_ffi_perf.json](evidence/sqlite_ffi_perf.json) | El overhead total de Wysna MIR C99 + FFI SQLite sobre 1,000 inserciones validadas es de solo **`+1.29 ms` (`+24.7%`)** y **`+64 KB` de RSS** respecto a C escrito a mano. |
| **9. C99 vs LLVM con cargas equivalentes (`--profile=release`)** | • `01_hello_world.wysna`: C99 build **`209.09 ms`** vs LLVM build **`480.57 ms`** (`2.30x` más lento en LLVM); run `2.19 ms` vs `2.07 ms`; size `59,064 B` vs `56,312 B`.<br>• `list_mutation.wysna`: C99 run **`2.95 ms`** (`76,640 B`) vs LLVM run **`2.71 ms`** (`91,968 B`, `+20%` más grande).<br>• `nested_cow.wysna`: C99 run **`2.67 ms`** (`77,568 B`) vs LLVM run **`2.87 ms`** (`92,192 B`, `+19%` más grande).<br>• `steady_state.wysna` (campaña de `docs/LLVM_EXPERIMENTAL.md:159`): C99 run **`40.17 ms`** (`77,432 B`) vs LLVM run **`64.68 ms`** (`92,856 B`, **`1.61x` más lento en LLVM**). *(Nota: compilar `bench/steady_state.wysna` directamente con `./wysna build` sin pasar el adaptador C `_wysna_benchmark_nanoseconds` falla en el linker; ver `PERF-06` / `DX`).* | [perf_measurements.json](evidence/perf_measurements.json), `docs/LLVM_EXPERIMENTAL.md:146-161` | **C99 supera a LLVM** en compilación en frío, tamaño de binario y carga sostenida (`steady_state.wysna`), confirmando la decisión **`SIMP-04` (`FREEZE` LLVM)**. |

---

## 2. Backlog Categorizado de Rendimiento (`PERF-01` .. `PERF-06`)

### `PERF-01` · Eliminar las 4 clonaciones profundas redundantes de `CheckedProgram.ast` durante `wysna check` y `McpServer.tool_wysna_check` (ahorro medido: `-55%` del tiempo total de `check`)

- **Prioridad:** P1
- **Decisión:** INVEST
- **Evidencia:** [src/compiler/CheckedProgram.py:212-216,253-260](../../src/compiler/CheckedProgram.py#L212-L216), [src/compiler/CompilerService.py:210-220](../../src/compiler/CompilerService.py#L210-L220), [src/tooling/SourceCommand.py:368-378](../../src/tooling/SourceCommand.py#L368-L378), [src/tooling/McpServer.py:170-171](../../src/tooling/McpServer.py#L170-L171), y medición reproducible en [perf_clone_syntax.txt](evidence/perf_clone_syntax.txt). Nivel: **verificado**.
- **Problema:** En `src/compiler/CheckedProgram.py:254-259`, la propiedad `checked.ast` (alias de `checked.program`) ejecuta en **cada lectura** `clone_syntax(self._program, detached=True, inspection=True)` —una copia profunda recursiva de todos los nodos, listas y diccionarios del AST. Sobre `selfhost/compiler.wysna` (`17,911` líneas), **una sola lectura de `checked.ast` cuesta `640.40 ms`**. Durante un simple `./wysna check selfhost/compiler.wysna` (que tarda `4,711.43 ms` en total):
  1. `CompilerService.analyze_reference_cycles` (`src/compiler/CompilerService.py:216-218`) evalúa `checked.ast.body` **tres veces seguidas** en tres list comprehensions (`struct_decls`, `enum_decls`, `class_decls`), gastando **`1,893.18 ms`** (`3 × ~640 ms`)!
  2. `SourceCommand.py:378` (`lint_discarded_expressions(checked.ast, file_name)`) evalúa `checked.ast` **una cuarta vez**, gastando otros **`727.10 ms`**!
  En total, **`2,620.28 ms` (`55.6%` del tiempo total de `wysna check`!)** se desperdician clonando 4 veces un AST que ya fue clonado y congelado en `CheckedProgram.__init__` (`self._program`, línea 212) para dos funciones de solo lectura (`analyze_reference_cycles` y `lint_discarded_expressions`) que jamás mutan el AST!
- **Propuesta:**
  1. En `CompilerService.analyze_reference_cycles` y en los pases internos de solo lectura (`lint_discarded_expressions`, `SchemaCLI.generate_schemas`, `SemanticGraph.build_semantic_graph`), leer directamente la vista de inspección inmutable `checked._program` (o exponer `checked.inspection_ast` sin re-clonar cuando el consumidor es interno y read-only, y en `analyze_reference_cycles` guardar `body = checked._program.body` en una variable local antes de las 3 list comprehensions).
- **Alternativas:**
  1. *No hacer nada:* seguir desperdiciando `2.62 s` (>55% del tiempo de `check`) en cada archivo grande tanto en CLI como en LSP y MCP.
  2. *Resolverlo con lo que ya existe:* simplemente asignar `ast_body = checked._program.body` en `CompilerService.analyze_reference_cycles` y pasar `checked._program` a `lint_discarded_expressions`.
- **Impacto (1–5):** Usuario: **5** | Agentes: **5** (duplica la velocidad de `wysna check`, `wysna_check` en MCP y diagnósticos LSP en archivos medianos/grandes) | Reducción de complejidad: **3**.
- **Esfuerzo:** **S (<0.5 días)**. Supuestos: cambiar 5 líneas en `src/compiler/CompilerService.py`, `src/tooling/SourceCommand.py`, `src/tooling/McpServer.py` y `src/tooling/SchemaCLI.py`, manteniendo `checked.ast` / `checked.program` intacto para consumidores externos que esperan una copia defensiva (`test_compiler_service.py`).
- **Riesgo:** **Bajo.** `analyze_reference_cycles`, `lint_discarded_expressions` y `generate_schemas` son funciones puras de lectura sobre el AST.
- **Compatibilidad y migración:** 100% compatible con todos los contratos de `test_compiler_service.py` y `test_architecture_contract.py`.
- **Componentes afectados:** `src/compiler/CompilerService.py`, `src/tooling/SourceCommand.py`, `src/tooling/McpServer.py`, `src/tooling/SchemaCLI.py`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** `./wysna check --stats selfhost/compiler.wysna` reduce su tiempo `total` en Apple M3 de **`~4,711 ms` a `< 2,300 ms`** (`>= 50%` de reducción medida), y `python3 -m tests.unit.test_compiler_service` sigue pasando al 100%.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

### `PERF-02` · Eliminar las 6 ejecuciones redundantes de `mir.Verifier.verify(module)` entre pasadas consecutivas de `mir/Optimizer.py` (ahorro medido: `-3.47 s` / `-44%` del tiempo MIR)

- **Prioridad:** P1
- **Decisión:** SIMPLIFY
- **Evidencia:** [src/mir/Ownership.py:9-10,43,49-50,247,340-341,430](../../src/mir/Ownership.py#L9-L10), [src/mir/Optimizer.py:523-537](../../src/mir/Optimizer.py#L523-L537), [src/mir/CCodeGen.py:16](../../src/mir/CCodeGen.py#L16), y medición reproducible en [perf_clone_syntax.txt](evidence/perf_clone_syntax.txt) (`single_mir_verify_ms = 578.84 ms`; `8` llamadas = `4,630.72 ms` de los `7,882.21 ms` de la fase MIR). Nivel: **verificado**.
- **Problema:** Durante una sola llamada a `CompilerService.emit_c(checked)`, el verificador de flujo de datos MIR (`Verifier(interner).verify(module)`, que cuesta **`578.84 ms`** por invocación sobre `selfhost/compiler.wysna`) se ejecuta **8 veces completas sobre todo el módulo**, incluyendo **3 pares de llamadas consecutivas sin ninguna instrucción modificada entre ellas**:
  1. `optimize_parameter_reads` verifica al entrar (línea 10) y al salir (línea 43).
  2. Inmediatamente en la línea siguiente de `Optimizer.optimize` (`Optimizer.py:526`), `optimize_local_cow_uniqueness` **vuelve a verificar el mismo módulo intacto al entrar (línea 50)** y al salir (línea 247).
  3. Inmediatamente en la línea siguiente (`Optimizer.py:527`), `optimize_interprocedural_cow` **vuelve a verificar el mismo módulo intacto al entrar (línea 341)** y al salir (línea 430).
  4. `Optimizer.optimize` verifica al salir (`Optimizer.py:536`), e inmediatamente `CCodeGen.generate` **vuelve a verificar el mismo módulo intacto al entrar (`CCodeGen.py:16`)**!
- **Propuesta:**
  1. En `Optimizer.optimize(module, interner)` (`src/mir/Optimizer.py:523-537`), verificar el módulo **exactamente dos veces**: una vez antes de iniciar las pasadas de optimización (garantizando que el lowering produjo MIR válido) y una vez al terminar todas las pasadas de optimización (garantizando que el optimizador preservó todos los invariantes de CFG, tipos y ownership).
  2. Mantener la verificación individual dentro de `optimize_parameter_reads` / `optimize_local_cow_uniqueness` / `optimize_interprocedural_cow` cuando se llamen de forma aislada desde tests unitarios (`_verified=False` por defecto, pasando `_verified=True` desde `Optimizer.optimize`), y marcar `module._verified_arena_id = interner.arena_id` al final de `Optimizer.optimize` para que `CCodeGen.generate` no repita una octava verificación inmediata si el módulo no ha sido mutado.
- **Alternativas:**
  1. *No hacer nada:* seguir pagando `8 × 578.84 ms = 4.63 s` de verificaciones repetidas en cada build de Seed.
  2. *Desactivar `Verifier.verify` en producción:* **rechazado rotundamente**, porque el verificador MIR es una barrera de seguridad fundamental del compilador (`02-ESTADO-REAL.md` §7). Reducir de 8 a 2 verificaciones (pre-optimización y post-optimización) conserva el **100% de la garantía de seguridad** eliminando solo las 6 verificaciones duplicadas intermedias.
- **Impacto (1–5):** Usuario: **4** | Agentes: **4** | Reducción de complejidad: **3**.
- **Esfuerzo:** **S (<0.5 días)**. Supuestos: ~25 líneas en `src/mir/Ownership.py` y `src/mir/Optimizer.py`.
- **Riesgo:** **Bajo.** Todos los tests adversarios de `tests/unit/test_mir.py` que llaman a `optimize_parameter_reads` directamente siguen ejecutando `Verifier.verify` pre y post cuando se invocan fuera de `Optimizer.optimize`.
- **Compatibilidad y migración:** 100% compatible con `test_mir.py` y `test_architecture_contract.py`.
- **Componentes afectados:** `src/mir/Ownership.py`, `src/mir/Optimizer.py`, `src/mir/CCodeGen.py`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** `./wysna emit-c --stats selfhost/compiler.wysna -o /tmp/out.c` reduce `timing_ms.mir` en Apple M3 de **`~7,882 ms` a `< 4,500 ms`** (`>40%` de reducción) y `python3 -m unittest tests/unit/test_mir.py` pasa al 100%.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

### `PERF-03` · Portar `optimize_parameter_reads` al pipeline MIR de Selfhost (`selfhost/src/08_mir_lowering.wysna`) para eliminar `15,900x` retains redundantes de `str`

- **Prioridad:** P1
- **Decisión:** INVEST
- **Evidencia:** Medición reproducible en [sqlite_ffi_perf.json](evidence/sqlite_ffi_perf.json#L167-L173) vs [sqlite_ffi_perf.json](evidence/sqlite_ffi_perf.json#L236-L242), [src/mir/Ownership.py:7-44](../../src/mir/Ownership.py#L7-L44), y [selfhost/src/08_mir_lowering.wysna:1-405](../../selfhost/src/08_mir_lowering.wysna#L1-L405). Nivel: **verificado**.
- **Problema:** Al ejecutar `examples/ffi/sqlite/import_csv.wysna` sobre `1,000` filas CSV ([sqlite_ffi_perf.json](evidence/sqlite_ffi_perf.json)):
  - El binario compilado por **Seed (`hir` / MIR C99)** realiza `3,007` allocations y **solo `4` operaciones `retain`** (`release: 3,011`).
  - El binario compilado por **Selfhost (`selfhost` Stage-2 MIR C99)** sobre el mismo código fuente realiza `3,007` allocations pero **`63,600` operaciones `retain`** (`release: 66,607` — **`15,900x` más tráfico atómico de ARC**)!
  - **Causa raíz exacta:** En `src/mir/Ownership.py:7-44`, Seed ejecuta `optimize_parameter_reads(module, interner)` (un pase de 37 líneas documentado en `docs/MIR_DESIGN.md:73` que elimina los pares `clone`/`release` de parámetros inmutables `str` cuando el temporal tiene una sola definición y ningún uso que consuma ownership). Selfhost (`selfhost/src/08_mir_lowering.wysna`) nunca implementó ese pase de 37 líneas, por lo que cada lectura de un parámetro `str` dentro de los helpers de parsing (e incluso dentro del propio compilador autoalojado `wysna_selfhost`!) ejecuta `wysna_clone` (`wysna_atomic_inc_i32`) y `wysna_release` (`wysna_atomic_dec_i32`).
- **Propuesta:** Implementar el pase `mir_optimize_parameter_reads(module: MirModule)` en `selfhost/src/08_mir_lowering.wysna` antes de `mir_verify(module)`, siguiendo exactamente las mismas reglas de seguridad de `src/mir/Ownership.py:7-44` y verificando el módulo antes y después con `mir_verify`.
- **Alternativas:**
  1. *No hacer nada:* dejar que todos los programas compilados con el Native SDK (y el propio binario `wysna_selfhost`, que es intensivo en manipulación de strings!) ejecuten decenas de miles de `wysna_atomic_inc_i32`/`dec_i32` innecesarios por segundo.
  2. *Resolverlo con lo que ya existe:* compilar siempre con `--engine=seed`. No sirve para los usuarios del Native SDK libre de Python.
- **Impacto (1–5):** Usuario: **4** | Agentes: **4** (acelera también el propio binario `wysna_selfhost` al recompilarse a sí mismo en Stage 1 → Stage 2!) | Reducción de complejidad: **3** (cierra una brecha real de paridad MIR documentada en `docs/MIR_DESIGN.md:73`).
- **Esfuerzo:** **S (1 día)**. Supuestos: ~65 líneas en `selfhost/src/08_mir_lowering.wysna` + regeneración del bootstrap de 3 etapas.
- **Riesgo:** **Bajo.** El verificador nativo `08_mir_verifier.wysna` y el verificador Python `src/mir/Verifier.py` (vía `test_selfhost_mir.py`) validan formalmente que ningún slot prestado sea movido ni liberado dos veces.
- **Compatibilidad y migración:** 100% compatible; mantiene el contrato de intercambio `wysna.mir` schema 1.
- **Componentes afectados:** `selfhost/src/08_mir_lowering.wysna`, `selfhost/compiler.wysna`, `tools/validate_sqlite_import.py`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** Ejecutar `python3 tools/validate_sqlite_import.py --rows 1000 --samples 1 --report /tmp/sqlite.json` muestra `engines.selfhost.runtime_memory.retain <= 10` (bajando desde `63,600`), y el bootstrap de 3 etapas (`C0 == C1 == C2`) pasa bajo ASan/UBSan y `WYSNA_MEM_CHECK=1`.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

### `PERF-04` · Paralelizar el Paso 11 de `run_conformance.py` y añadir `./wysna test --fast` para reducir el ciclo local de `548 s` a `< 15 s`

- **Prioridad:** P1
- **Decisión:** SIMPLIFY
- **Evidencia:** Medición real de `./wysna test` en Fase 1 ([wysna_test_baseline.txt](evidence/wysna_test_baseline.txt): **`548 s` / `9m 08s`**), [src/run_conformance.py:438-443,489-494,528-599](../../src/run_conformance.py#L528-L599), y [src/tooling/CLI.py:306-357](../../src/tooling/CLI.py#L306-L357). Nivel: **verificado**.
- **Problema:** Evaluando el ciclo completo del desarrollador (no solo microbenchmarks), ejecutar `make test` (`./wysna test`) tarda **9 minutos y 8 segundos (`548 s`)** en un Apple M3 por tres razones verificadas en `src/run_conformance.py`:
  1. **Paso 11 secuencial (`src/run_conformance.py:529-558`):** Mientras el Paso 7 (`AST C99`) y el Paso 9 (`Selfhost Stage-2`) usan `ThreadPoolExecutor(max_workers=compile_workers)`, el Paso 11 (`Seed HIR/MIR C99`) compila los `98` binarios C **uno por uno de forma secuencial**!
  2. **Bootstrap completo de 3 etapas forzado (`src/run_conformance.py:468`):** `run_conformance.py` llama siempre a `bootstrap_compiler(repo_root, ...)` recompilando `17,911` líneas de `selfhost/compiler.wysna` **tres veces desde cero** incluso cuando `ensure_selfhost_compiler_binary(repo_root)` ya tiene una caché válida y verificada por hash en `.wysna_cache/bootstrap`!
  3. **Benchmarks de 15 muestras, fuzzing y stress de 10,000 iteraciones acoplados a `./wysna test` (`src/run_conformance.py:580-599`):** `run_d05_telemetry_tests()` ejecuta la suite entera de `./wysna bench` (`10` benchmarks × `15` muestras × `3` motores) y `run_foundations_tests()` ejecuta miles de iteraciones dentro de cada corrida de `./wysna test`.
- **Propuesta:**
  1. Paralelizar el Paso 11 de `src/run_conformance.py:529-558` con `ThreadPoolExecutor(max_workers=compile_workers)` igual que los Pasos 7 y 9.
  2. Añadir la bandera `./wysna test --fast` (y target `make test-fast`) que: reutilice el binario Selfhost en caché (`ensure_selfhost_compiler_binary`) si las huellas SHA-256 de `selfhost/` y `runtime/` no cambiaron, ejecute Lexer + Parser + Types + Errors + HIR + Runtime Interpreter + MIR unit tests + `test_architecture_contract`, y omita la suite de benchmarking de 15 muestras (`run_d05_telemetry_tests`) y el stress loop de 10,000 iteraciones (que siguen corriendo en `./wysna test` completo en CI y `./wysna bench`).
- **Alternativas:**
  1. *No hacer nada:* obligar a cualquier contribuidor o agente a esperar `9` minutos en cada ejecución de `./wysna test`.
  2. *Resolverlo con lo que ya existe:* correr `./wysna test tests/runtime/foo.wysna` para un solo archivo. No valida regresiones en el lexer/parser/checker/MIR ni contratos de arquitectura.
- **Impacto (1–5):** Usuario: **5** | Agentes: **5** (un agente o desarrollador puede verificar todo el compilador en `< 15 s` en vez de `548 s`) | Reducción de complejidad: **3**.
- **Esfuerzo:** **S (<1 día)**. Supuestos: ~35 líneas en `src/run_conformance.py` y `src/tooling/CLI.py` + target `test-fast` en `Makefile`.
- **Riesgo:** **Bajo.** `./wysna test` sin `--fast` conserva exactamente los `843/843` checks actuales para CI.
- **Compatibilidad y migración:** 100% retrocompatible.
- **Componentes afectados:** `src/run_conformance.py`, `src/tooling/CLI.py`, `Makefile`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:**
  1. `./wysna test --fast` termina en **`< 15 s`** en Apple M3 con `0` fallos.
  2. `./wysna test` completo reduce su tiempo total en **`>= 20%`** gracias a la paralelización del Paso 11 y sigue reportando `ALL 843/843 CONFORMANCE, EXAMPLES & SELF-HOSTING TESTS PASSED (100%)`.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

### `PERF-05` · Eliminar el array `const char** keys` por instancia en `WStructBuf` para `struct` en MIR C99 o alinear `WYSNA_SINTAXIS.md:180` con el layout real de `WListBuf`

- **Prioridad:** P2
- **Decisión:** SIMPLIFY
- **Evidencia:** [runtime/wysna_runtime_core.h:31-37,58-66](../../runtime/wysna_runtime_core.h#L31-L37), [runtime/wysna_runtime_collections.h:114-133](../../runtime/wysna_runtime_collections.h#L114-L133), [runtime/wysna_runtime_access.h:221-238](../../runtime/wysna_runtime_access.h#L221-L238), y discrepancia `D06` ([WYSNA_SINTAXIS.md:180](../../WYSNA_SINTAXIS.md#L180) vs [docs/MIR_DESIGN.md:79](../MIR_DESIGN.md#L79)). Nivel: **verificado**.
- **Problema:**
  1. **Discrepancia en `list[int]` / `list[float]` / `list[bool]`:** `WYSNA_SINTAXIS.md:180` y `docs/FULL_LANGUAGE_CAPABILITIES_AND_DEBT_AUDIT.md:151` afirman que las listas escalares se almacenan como *"búferes contiguos C sin encajonar (`unboxed scalar buffers` / `WUnboxedList`) without per-element `WVal` boxing"*. En realidad (`runtime/wysna_runtime_core.h:31-37`), `WListBuf` siempre aloja `WVal* data` (`8` bytes NaN-boxed por elemento) y usa `bool scalar_only` para hacer release en $O(1)$ sin recorrer el array y `wysna_list_get_int_at` para desempaquetar el `int64_t` al leer. Con ABI v3, como `sizeof(WVal)` ya es de `8` bytes (idéntico a `sizeof(int64_t)` y `sizeof(double)`!), el layout actual de `8` bytes por elemento en `WListBuf` ya tiene densidad óptima para `int` y `float` sin necesitar otro buffer separado; lo que debe corregirse es la afirmación falsa sobre `WUnboxedList` en la documentación.
  2. **Overhead real en cada instancia de `struct` (`WStructBuf`):** En `runtime/wysna_runtime_collections.h:114-133`, cada vez que se crea un `struct` de $N$ campos (`wysna_make_struct`), el runtime aloja `sizeof(WStructBuf) + N * sizeof(const char*) + N * sizeof(WVal)` y **copia en un bucle los $N$ punteros `const char* keys[i]` dentro de cada instancia individual del struct en el heap**! Esto multiplica por `2x` el espacio de payload de cada `struct` (`16` bytes por campo en lugar de `8` bytes) únicamente para guardar los nombres de los campos en cada instancia, a pesar de que todas las instancias de un mismo tipo `struct Point` comparten exactamente el mismo arreglo estático de nombres `keys`!
- **Propuesta:**
  1. Corregir `WYSNA_SINTAXIS.md:180` y `docs/FULL_LANGUAGE_CAPABILITIES_AND_DEBT_AUDIT.md:151` para describir con exactitud la optimización `scalar_only` ($O(1)$ `release` + `memcpy` CoW detach + lecturas escalares `wysna_list_get_*_at` sobre palabras `WVal` de 8 bytes).
  2. *(Opcional, bajo flag o en `wysna_make_struct_static_keys`)* Hacer que `WStructBuf.keys` apunte directamente a la tabla estática de nombres de campo del tipo `struct` generada en C (`static const char* const _keys_Point[] = {"x", "y"}`) en lugar de reservar y copiar `sizeof(const char*) * count` en cada `malloc` de cada instancia de `struct`!
- **Alternativas:**
  1. *No hacer nada:* seguir copiando y alojando `N * 8` bytes de punteros de nombres de campo en cada instancia de `struct` creada en un bucle.
  2. *Resolverlo con lo que ya existe:* corregir hoy mismo la documentación de `WListBuf` (esfuerzo S) y evaluar el puntero estático `st->keys = static_keys` en `wysna_make_struct`.
- **Impacto (1–5):** Usuario: **3** | Agentes: **3** | Reducción de complejidad: **3**.
- **Esfuerzo:** **S (<1 día)** para la corrección documental y para que `wysna_struct_detach` reutilice el puntero `st->keys` estático.
- **Riesgo:** **Bajo-Medio** si se toca `WStructBuf.keys` (hay que asegurar que todos los llamadores de `wysna_make_struct` pasen un array de literales C con duración estática o mantener una variante `wysna_make_struct` que copie si algún test construye claves dinámicas).
- **Compatibilidad y migración:** No altera la ABI externa de `WVal` (`8` bytes).
- **Componentes afectados:** `WYSNA_SINTAXIS.md`, `docs/FULL_LANGUAGE_CAPABILITIES_AND_DEBT_AUDIT.md`, `runtime/wysna_runtime_collections.h`, `runtime/wysna_runtime_memory.h`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** `WYSNA_SINTAXIS.md:180` coincide con `runtime/wysna_runtime_core.h:31-37` y `docs/MIR_DESIGN.md:79`; si se aplica la compartición de `keys` en `wysna_struct_detach`, `bench/structs.wysna` y `test_cow_telemetry.py` pasan con menor `cow_allocated_bytes` y `0` fallos ASan/UBSan.
- **Requiere aprobación:** no para la corrección documental; sí si se cambia el contrato de `wysna_make_struct` en `runtime/wysna_runtime_collections.h`.
- **Estado:** implementado.

---

### `PERF-06` · Autocontener `bench/steady_state.wysna` (o enlazar su adaptador C automáticamente en `wysna build`) y aplicar dead-stripping en `--profile=size`

- **Prioridad:** P2
- **Decisión:** SIMPLIFY
- **Evidencia:** Medición en [perf_measurements.json](evidence/perf_measurements.json) (`profiles.release.size_bytes = 59,072` vs `profiles.size.size_bytes = 59,064`: solo `8` bytes de diferencia; y `c99_vs_llvm_release.steady_state` falla al compilar con `./wysna build --profile=release bench/steady_state.wysna` por `Undefined symbols for architecture arm64: "_wysna_benchmark_nanoseconds"`), [src/compiler/NativeToolchain.py:45-120](../../src/compiler/NativeToolchain.py#L45-L120), [bench/steady_state.wysna:1-10](../../bench/steady_state.wysna#L1-L10). Nivel: **verificado**.
- **Problema:**
  1. En [perf_measurements.json](evidence/perf_measurements.json), compilar `examples/01_hello_world.wysna` con `--profile=release` produce un binario de **`59,072` bytes**, y con `--profile=size` produce **`59,064` bytes** (apenas **8 bytes** menos, `< 0.02%`!). Esto ocurre porque `src/compiler/NativeToolchain.py` pasa `-Os` al compilador C en `--profile=size`, pero no pasa `-Wl,-dead_strip` (macOS) / `-Wl,--gc-sections -ffunction-sections -fdata-sections` (Linux/GNU) para eliminar las funciones de runtime y tablas Unicode no referenciadas.
  2. `bench/steady_state.wysna` declara `extern "C" fn wysna_benchmark_nanoseconds() -> int` (definido en un archivo C auxiliar de `tools/benchmark_llvm.py`) en lugar de usar `std/time.monotonic_ms()` o un helper del runtime, por lo que falla con error de linker si cualquier usuario o script lo compila con `./wysna build bench/steady_state.wysna`!
- **Propuesta:**
  1. En `src/compiler/NativeToolchain.py` (y `packaging/native/driver.c`), añadir `-Wl,-dead_strip` en Darwin y `-ffunction-sections -fdata-sections -Wl,--gc-sections` en Linux/Windows GNU para los perfiles `size` y `release`.
  2. Documentar en `bench/steady_state.wysna` el flag `--link-lib` requerido o proveer un fallback para que sea ejecutable con `./wysna build`.
- **Alternativas:**
  1. *No hacer nada:* mantener un perfil `--profile=size` que solo ahorra 8 bytes frente a `release`.
  2. *Resolverlo con lo que ya existe:* correr `strip` manualmente después de `wysna build`.
- **Impacto (1–5):** Usuario: **3** | Agentes: **2** | Reducción de complejidad: **2**.
- **Esfuerzo:** **S (<0.5 días)**. Supuestos: ~15 líneas en `src/compiler/NativeToolchain.py` y `packaging/native/driver.c`.
- **Riesgo:** **Bajo.** `-dead_strip` / `--gc-sections` preserva todos los símbolos alcanzables desde `main` y conserva los símbolos de depuración para stack traces.
- **Compatibilidad y migración:** 100% compatible.
- **Componentes afectados:** `src/compiler/NativeToolchain.py`, `packaging/native/driver.c`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** `./wysna build --profile=size examples/01_hello_world.wysna -o /tmp/hello_size` reduce el tamaño del ejecutable en **`>= 20%`** respecto a `59,064 B` sin romper ningún test de `test_native_toolchain.py`.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

## 3. Hipótesis de la auditoría inicial sin medición equivalente

Los informes [PERF-R01](research/PERF-R01-cow-contention.md) y
[PERF-R02](research/PERF-R02-struct-layout.md) miden CoW y layout de structs.
Sus resultados se conservan separados de estas propuestas.

### Medición pendiente del tamaño de `.wasm` (`browser-library` y `wasi-command`) y latencia de cruce ABI JS↔Wasm

- **Por qué no se pudo medir en esta sesión:** En el host de evaluación no están instalados `emcc` (`Emscripten 5.0.1`) ni `wasi-sdk 34` (`[W5005] Emscripten 5.0.1 was not found` en [build_web.json](evidence/prog_c_wasm/build_web.json)).
- **Protocolo reproducible de medición:**
  1. En un entorno con `emsdk 5.0.1` o tras implementar **`GAP-06`** (`zig cc`), compilar `examples/shared-browser-app` y `docs/evolution/evidence/prog_c_wasm` con:
     ```sh
     ./wysna project build --manifest docs/evolution/evidence/prog_c_wasm/wysna.toml --target web -o docs/evolution/evidence/prog_c_wasm/build/web.mjs
     ```
  2. Registrar tamaño en bytes de `web.runtime.wasm` sin comprimir y con `gzip -9`, especialmente verificando si los `3,974` LOC de `runtime/wysna_unicode_tables.h` se incluyen en el `.wasm` aunque el módulo no llame a `std/text.normalize_nfc`!
  3. Medir en Node.js (`node docs/evolution/evidence/prog_c_wasm/consumer.mjs`) la mediana de 1,000 llamadas a `compute_discount_cents` (escalar `BigInt`) vs `evaluate_cart_json` (`str` UTF-8 + JSON parse/stringify).

### Propuesta diferida de persistir la caché incremental `interface_digest` de `CompilationSession` en `.wysna_cache/` para invocaciones CLI en frío

- **Estado:** En [perf_measurements.json](evidence/perf_measurements.json), el chequeo en caliente dentro de una misma `CompilationSession` en memoria baja de `97.38 ms` a `15.32 ms` (`6.36x`), pero ese ahorro se pierde entre comandos CLI separados (`./wysna check ...`) porque la caché de `CompilationSession` vive solo en RAM.
- **Protocolo para falsificarla:** Antes de añadir serialización a disco de `CompilationSession` en Python, medir en un proyecto de `>= 10` módulos cuánto tiempo consume el arranque del intérprete Python (`~73 ms`) frente al parseo/chequeo de módulos importados. Si el binario nativo `.wysna_cache/wysna_selfhost check` ya verifica proyectos medianos en `< 30 ms` sin caché en disco, serializar `CompilationSession` en Python no vale la complejidad añadida.
