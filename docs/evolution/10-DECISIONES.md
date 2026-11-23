# 10 — Decisiones Arquitectónicas que Requieren Aprobación (ADRs)

> Auditoría histórica de la línea base `a2549fa`. Los problemas y mediciones describen ese snapshot; los estados de implementación y el ciclo vigente se consultan en [00-INDEX.md](00-INDEX.md) y [12-SIGUIENTE-CICLO-LENGUAJE.md](12-SIGUIENTE-CICLO-LENGUAJE.md).

> La compatibilidad de `new` descrita en ADR-03 es histórica: la limpieza 1A del candidato [ADR-05](ADR-05-arquitectura-hibrida-tooling-selfhost.md) retira ese alias y conserva `Class(...)`.

Este documento recoge las **5 decisiones arquitectónicas y de evolución (`ADR-00` a `ADR-04`)** que requieren aprobación explícita del mantenedor antes de ejecutarse: la elección del foco estratégico único y el congelamiento de subsistemas secundarios (`01-FOCO.md` / `SIMP-04`), más los **4 ítems del backlog marcados con `Requiere aprobación: sí`** ([`LANG-04`](03-LENGUAJE.md), [`GAP-03`](06-BRECHAS.md), [`LANG-05`](03-LENGUAJE.md) y [`GAP-06`](06-BRECHAS.md)).

| ADR | Ítem / Alcance | Decisión propuesta | Impacto principal | Estado |
| :--- | :--- | :--- | :--- | :--- |
| **ADR-00** | [`01-FOCO.md`](01-FOCO.md), [`SIMP-04`](05-SIMPLIFICACION.md) | Foco único en **Scripting/Automatización Tipada Agent-First** y congelamiento (`FREEZE`) de `--backend=llvm-experimental` y del servidor HTTP `wysna service serve`. | Foco estratégico y asignación de presupuesto de ingeniería | Implementado |
| **ADR-01** | [`LANG-04`](03-LENGUAJE.md) | Incorporar los **7 métodos intrínsecos de `str`** (`.trim()`, `.split()`, `.to_lower()`, `.to_upper()`, `.starts_with()`, `.ends_with()`, `.replace()`) ya prometidos en `WYSNA_SINTAXIS.md:660-664`. | Superficie de métodos primitivos en `Seed` y `Selfhost` | Implementado |
| **ADR-02** | [`GAP-03`](06-BRECHAS.md) | Incorporar **`.remove(key) -> V?` y `.clear() -> void`** en `dict[K, V]` con semántica ARC + Copy-on-Write (`CoW`). | Superficie de colecciones built-in y runtime ARC | Implementado |
| **ADR-03** | [`LANG-05`](03-LENGUAJE.md) | Canonizar la instanciación de `class` a **`Class(...)` sin keyword `new`** en `wysna fmt` y documentación, manteniendo `new` como alias aceptado en el parser. | Formato canónico y reducción de entropía para agentes | Implementado |
| **ADR-04** | [`GAP-06`](06-BRECHAS.md) | Compilar los targets WebAssembly (`web` y `wasi`) con el toolchain **`zig cc` (`Zig 0.15.2`) ya empaquetado**, eliminando la dependencia externa obligatoria de `Emscripten 5.0.1` y `wasi-sdk 34`. | Toolchain y empaquetado de targets WebAssembly | Selección de target en Seed integrada. Port Zig del SDK diferido por [ADR-05](ADR-05-arquitectura-hibrida-tooling-selfhost.md). |

---

## ADR-00 — Foco Estratégico en Scripting/Automatización Tipada Agent-First y Congelamiento de LLVM / Servidor HTTP

### 1. Problema con evidencia
- Wysna mantiene simultáneamente **4 motores de ejecución C99/Intérprete**, un backend experimental **`--backend=llvm-experimental`** (`1,499` LOC en `src/backend/llvm/`), un piloto de servidor HTTP por subprocesos (**`wysna service serve`** en `src/tooling/ServiceRuntime.py`), un sistema de targets WebAssembly (`src/tooling/ProjectSystem.py`) y **11 subcomandos Agent-First** duplicados entre Python (`src/tooling/`) y Wysna (`selfhost/src/09_*.wysna`).
- En la Fase 2, el flujo con mayor tracción real —scripting, transformación de datos CSV/JSON, contratos y automatización CLI verificada por agentes— completó su ejecución extremo a extremo ([`prog_a_run.txt`](evidence/prog_a_run.txt)), pero sufrió **6 bloqueos `P0`** de corrección y paridad: corrupción de strings multilínea en `selfhost fmt` ([`repro_selfhost_fmt.txt`](evidence/repro_selfhost_fmt.txt)), fallo de inferencia en `return []` con `T ! E` ([`prog_a_check_seed.json`](evidence/prog_a_check_seed.json)), pánicos no recuperables `[W4005]` en codecs JSON con `requires` ([`prog_b_run.txt`](evidence/prog_b_run.txt)), crash `TypeError` en `McpServer` ([`repro_mcp_cycle_crash.txt`](evidence/repro_mcp_cycle_crash.txt)), falsos rechazos `[W5006]` en capability gates ([`repro_capability_stdlib.txt`](evidence/repro_capability_stdlib.txt)) y métodos documentados que no compilan ([`repro_str_methods.txt`](evidence/repro_str_methods.txt)).
- Mientras tanto, el backend C99 (`clang -O2`) ya supera a Python 3.13 por `3.3x–102x` ([`perf_measurements.json`](evidence/perf_measurements.json)), por lo que expandir `llvm-experimental` hoy no desbloquea ningún caso de uso real.

### 2. Alternativas
- **Alternativa A (Recomendada):** Concentrar el ciclo completo (Hitos 1, 2 y 3) en **Scripting y Automatización Tipada Agent-First**, congelando (`FREEZE`) la expansión de `--backend=llvm-experimental`, el piloto `wysna service serve`, el port de Wasm a `Selfhost` y cualquier nuevo subcomando CLI.
- **Alternativa B:** Dividir el esfuerzo en paralelo entre Scripting, paridad completa de `--backend=llvm-experimental`, un servidor HTTP nativo y el port de Wasm a `Selfhost`.
- **Alternativa C (No hacer nada):** Mantener todas las líneas abiertas sin prioridad única ni política de congelamiento explícita.

### 3. Trade-offs

| Criterio | Alternativa A (Foco único + FREEZE) | Alternativa B (Expansión paralela) | Alternativa C (No hacer nada) |
| :--- | :--- | :--- | :--- |
| **Corrección** | **Alta:** Cierra el 100% de los bugs `P0` y la divergencia `Seed ↔ Selfhost`. | **Baja:** Multiplica la superficie sin cerrar la deuda de paridad actual. | **Baja:** Persisten bugs destructivos como `AGT-01`. |
| **Costo de mantenimiento** | **Bajo:** Congela `1,499` LOC de LLVM y evita nuevos subsistemas. | **Muy alto:** Exige mantener 2 backends nativos (C99 y LLVM) en 2 compiladores. | **Alto:** Crecimiento no acotado de deuda doc↔código. |
| **Compatibilidad** | **100% compatible:** No elimina flags existentes; solo congela su alcance. | Compatible pero inestable. | Compatible. |
| **Impacto en agentes** | **Muy positivo:** Elimina los falsos positivos, crashes MCP y trampas documentales que hacen fallar a los agentes en `pass@1`. | **Negativo:** Más features parcialmente implementadas aumentan la tasa de alucinación. | **Negativo:** `pass@1` degradado por contradicciones doc↔código. |
| **Reversibilidad** | **Total:** Cada subsistema congelado tiene una condición cuantitativa de reapertura en [`09-ROADMAP.md`](09-ROADMAP.md). | Difícil de revertir una vez comprometidas APIs públicas. | N/A |

### 4. Recomendación
Aprobar la **Alternativa A**.

### 5. Riesgos
- Usuarios que lean referencias antiguas a LLVM o `wysna service serve` podrían asumir que están listos para producción; se mitiga con [`DX-02`](08-CALIDAD-DX.md) y [`SIMP-04`](05-SIMPLIFICACION.md) etiquetándolos explícitamente como `experimental / congelado`.

### 6. Condición de reversión
Se reabre cualquiera de las líneas congeladas únicamente cuando se cumplan las condiciones cuantitativas definidas en la tabla **Congelado / Diferido** de [`09-ROADMAP.md`](09-ROADMAP.md) (por ejemplo, `raw_pass_at_1 >= 90%` y `100%` de paridad `Seed ↔ Selfhost` en tooling).

---

## ADR-01 (`LANG-04`) — Incorporar los 7 Métodos Intrínsecos de `str` frente a Mantenerlos Solo en `std/text`

### 1. Problema con evidencia
- [`WYSNA_SINTAXIS.md:660-664`](../../WYSNA_SINTAXIS.md#L660-L664) documenta como sintaxis oficial del lenguaje 7 métodos sobre valores `str`: `s.trim()`, `s.split(",")`, `s.to_lower()`, `s.to_upper()`, `s.starts_with("a")`, `s.ends_with("b")` y `s.replace("a", "b")`.
- Sin embargo, [`src/checker/AccessRules.py:120-210`](../../src/checker/AccessRules.py#L120-L210) y [`selfhost/src/08_semantics.wysna:1451-1474`](../../selfhost/src/08_semantics.wysna#L1451-L1474) solo implementan `.length()`, `.contains(sub)`, `.is_empty()` y `.slice(start, end)`.
- En la Fase 2, tanto el Programa A ([`prog_a_check_seed.json`](evidence/prog_a_check_seed.json)) como el repro aislado ([`repro_str_methods.txt`](evidence/repro_str_methods.txt)) fallaron con `[W3002] Type 'str' has no member 'trim'` (sin `hint` hacia `std/text`), a pesar de que el runtime C ([`runtime/wysna_runtime_text.h`](../../runtime/wysna_runtime_text.h)) ya implementa las 7 funciones `wysna_host_str_*` usadas por [`stdlib/text.wysna`](../../stdlib/text.wysna).

### 2. Alternativas
- **Alternativa A (Recomendada):** Implementar los 7 métodos intrínsecos sobre el tipo primitivo `str` en `Seed` y `Selfhost`, bajándolos directamente a las funciones `wysna_host_str_*` ya existentes en el runtime, y mantener `std/text` como wrapper funcional compatible.
- **Alternativa B:** No añadir métodos a `str`, eliminar las líneas `660-664` de `WYSNA_SINTAXIS.md`, y añadir un `hint` con `autofix` en `W3002` que sugiera importar `std/text` (`text.trim(s)`).
- **Alternativa C (No hacer nada):** Dejar `str` con solo 3 métodos y la documentación desalineada. (Inaceptable: rompe el código generado por agentes y usuarios en el primer intento).

### 3. Trade-offs

| Criterio | Alternativa A (7 métodos intrínsecos en `str`) | Alternativa B (Solo funciones en `std/text` + hint) | Alternativa C (No hacer nada) |
| :--- | :--- | :--- | :--- |
| **Corrección** | **Alta:** Hace verdadero el contrato ya documentado y reutiliza `wysna_host_str_*` ya probados. | **Alta:** Restaura coherencia doc↔código sin tocar codegen. | **Baja:** Discrepancia grave doc↔código. |
| **Costo de mantenimiento** | **Bajo (~80 LOC en Seed + Selfhost):** El runtime C y el intérprete ya tienen las primitivas `wysna_host_str_*`. | **Muy bajo (~20 LOC):** Solo edición documental y un hint en `MemberResolution.py`. | Cero hoy, alto costo en fallos de usuario. |
| **Compatibilidad** | **100% retrocompatible:** `std/text` sigue funcionando sin cambios. | **100% retrocompatible** con código que ya compilaba, pero invalida ejemplos de `WYSNA_SINTAXIS.md`. | N/A |
| **Impacto en agentes** | **Muy positivo:** Permite encadenar `line.trim().split(",")` sin requerir un import adicional ni recordar qué métodos viven en `str` (`.contains()`, `.slice()`) y cuáles en `std/text` (`trim`, `split`). | **Moderado:** Reduce fallos tras una ronda de reparación (`hint`), pero sigue penalizando `raw_pass_at_1` porque LLMs entrenados en Python/TS/Rust escriben `s.trim()` y `s.split()` por defecto. | **Muy negativo:** Falla el 100% de las veces en `pass@1`. |
| **Reversibilidad** | Moderada (una vez publicados como métodos built-in, retirarlos sería un breaking change). | Total (siempre se pueden añadir los métodos más adelante). | N/A |

### 4. Recomendación
Aprobar la **Alternativa A**: implementar los 7 métodos intrínsecos en `str` delegando en `wysna_host_str_*`. Si se prefiere minimizar estrictamente la superficie del compilador en el corto plazo, ejecutar la **Alternativa B** en el Hito 1 como corrección documental (`DX-01`) antes de abrir el Hito 2.

### 5. Riesgos
- Riesgo de asimetría en la gestión de memoria ARC del valor devuelto (`char*` vs `WListBuf*` en `split`) entre `Seed` y `Selfhost`. Se mitiga delegando exactamente a los mismos símbolos `wysna_host_str_*` que ya invoca `stdlib/std/text.wysna` y verificando `wysna_rc_stats()` (`rc_balance == 0`).

### 6. Condición de reversión
Si la implementación en los 4 motores introduce cualquier fuga de ARC o supera las `120` líneas de código combinadas entre `Seed` y `Selfhost`, revertir inmediatamente a la **Alternativa B** (`std/text` + `hint` en `W3002`).

---

## ADR-02 (`GAP-03`) — Incorporar `dict.remove(key)` y `dict.clear()` en `dict[K, V]`

### 1. Problema con evidencia
- [`WYSNA_SINTAXIS.md:674`](../../WYSNA_SINTAXIS.md#L674) documenta `m.remove("a")` sobre `dict[K, V]`, pero [`src/checker/AccessRules.py:120-210`](../../src/checker/AccessRules.py#L120-L210) y [`runtime/wysna_runtime_collections.h`](../../runtime/wysna_runtime_collections.h) solo implementan `.length()`, `.contains(key)`, `.get(key)`, `.get_or(key, default)`, `.keys()` y `.values()`.
- En la Fase 2, al construir el CRUD con persistencia JSON del Programa B ([`prog_b_api.wysna`](evidence/prog_b_api.wysna)) y en [`repro_dict_remove.txt`](evidence/repro_dict_remove.txt), eliminar una clave de un `dict[str, Task]` resultó imposible in-place (`[W3002] Type 'dict[str, int]' has no member 'remove'`) y obligó a clonar todo el diccionario iterando `.keys()` en $O(n)$.

### 2. Alternativas
- **Alternativa A (Recomendada):** Implementar `m.remove(key: K) -> V?` y `m.clear() -> void` sobre `dict[K, V]` en `Seed`, `Selfhost`, `Interpreter` y `runtime/wysna_runtime_collections.h` respetando Copy-on-Write (`wysna_dict_detach` cuando `rc > 1`) y liberando (`release_key`, `release_val`) las entradas eliminadas.
- **Alternativa B:** Mantener `dict[K, V]` sin eliminación in-place, borrar `m.remove("a")` de `WYSNA_SINTAXIS.md:674` y documentar la reconstrucción por copia como patrón oficial.
- **Alternativa C (No hacer nada):** Mantener la discrepancia actual entre `WYSNA_SINTAXIS.md:674` y el compilador.

### 3. Trade-offs

| Criterio | Alternativa A (`dict.remove` + `dict.clear` con CoW) | Alternativa B (Solo eliminación por copia $O(n)$) | Alternativa C (No hacer nada) |
| :--- | :--- | :--- | :--- |
| **Corrección** | **Alta:** Alinea `dict[K, V]` con `list[T]` (que ya tiene `.pop()` y `.clear()`) y con la documentación. | **Moderada:** Coherente documentalmente, pero deja a `dict` sin mutación de borrado. | **Baja:** Ejemplo oficial roto (`W3002`). |
| **Costo de mantenimiento** | **Moderado (~140 LOC entre runtime C, Seed y Selfhost):** Requiere cuidado en tombstones/compactación y destructores ARC. | **Mínimo (~5 LOC):** Solo cambio documental. | Cero. |
| **Compatibilidad** | **100% retrocompatible:** Solo añade 2 métodos nuevos a `dict[K, V]`. | Rompe expectativas de `WYSNA_SINTAXIS.md:674`. | N/A |
| **Impacto en agentes** | **Muy positivo:** Todo agente espera poder borrar una clave de un mapa mutable en cachés, sesiones y CRUDs sin escribir un bucle de filtrado de 9 líneas. | **Negativo:** Obliga a generar boilerplate propenso a errores cada vez que se borra una clave. | **Muy negativo.** |
| **Reversibilidad** | Moderada (nuevo método en tipo built-in). | Total. | N/A |

### 4. Recomendación
Aprobar la **Alternativa A** e implementarla en el Hito 2 junto con tests de estrés de ARC + CoW (`allocs == frees`).

### 5. Riesgos
- **Corrupción de tabla hash o fuga ARC al eliminar con `rc > 1`:** Si `wysna_dict_remove` muta el buffer sin clonar cuando `d->rc > 1`, violaría la semántica de valor con CoW; si olvida invocar `release_key` sobre la clave borrada mientras transfiere ownership del valor al retorno `V?`, fugaría memoria. Se mitiga reutilizando `wysna_dict_detach` y validando con `wysna_rc_stats()` en `tests/unit/test_rc_Verification.py`.

### 6. Condición de reversión
Si `wysna_dict_remove` presenta cualquier regresión en la suite de paridad de 4 motores o desbalance en `test_rc_Verification.py`, revertir el PR y aplicar temporalmente la **Alternativa B** hasta corregir el manejo de tombstones en `WDictBuf`.

---

## ADR-03 (`LANG-05`) — Canonizar la Instanciación de `class` a `Class(...)` sin `new` en `wysna fmt`

### 1. Problema con evidencia
- Wysna acepta hoy dos sintaxis idénticas para instanciar una `class`: `Counter(1)` y `new Counter(1)` ([`src/parser/Parser.py`](../../src/parser/Parser.py), [`selfhost/src/03_syntax.wysna`](../../selfhost/src/03_syntax.wysna)).
- El propio repositorio mezcla ambas convenciones (`52` ocurrencias de `new Class(...)` en tests/docs frente a llamadas directas `Class(...)` en `examples/flagship/`), mientras que los `struct` prohíben `new` (`[W2046] 'new' target must be a class`).
- Para un agente LLM, si una API cambia un tipo de `class` a `struct` (o viceversa), el sitio de llamada con `new` se rompe con `[W2046]`, mientras que `Type(...)` funciona uniformemente para ambos.

### 2. Alternativas
- **Alternativa A (Recomendada):** Mantener `new Class(...)` aceptado en el parser por compatibilidad, pero hacer que `wysna fmt` normalice `new Class(args)` a `Class(args)` y usar exclusivamente `Class(args)` en toda la documentación y `SYSTEM_PROMPT`.
- **Alternativa B:** Hacer `new Class(...)` obligatorio para todas las `class` y prohibir `Class(...)` sin `new`.
- **Alternativa C (No hacer nada):** Mantener ambas formas en el formateador y en la documentación.

### 3. Trade-offs

| Criterio | Alternativa A (Canonizar a `Class(...)` en `fmt`) | Alternativa B (Exigir `new Class(...)`) | Alternativa C (No hacer nada) |
| :--- | :--- | :--- | :--- |
| **Corrección** | **Alta:** Unifica la sintaxis de construcción de valores (`struct`) y referencias (`class`). | **Moderada:**Hace explícita la semántica de referencia en el call-site, pero rompe código existente. | **Moderada:** Sin cambios. |
| **Costo de mantenimiento** | **Muy bajo (~15 LOC en `Formatter.py` y `09_agent_tools.wysna`):** El AST `NewExpr` ya existe. | **Alto:** Rompe todos los ejemplos y programas que instancian clases sin `new`. | Cero. |
| **Compatibilidad** | **100% retrocompatible:** Ningún programa existente deja de compilar. | **Incompatible (breaking change):** Requiere migrar código existente. | 100% compatible. |
| **Impacto en agentes** | **Positivo:** Elimina una decisión arbitraria al instanciar tipos importados de un módulo externo (el agente no necesita saber si `Client` es `struct` o `class` para construirlo). | **Negativo:** Si el agente no sabe si un tipo importado es `struct` o `class`, fallará con `[W2046]` la mitad de las veces. | **Neutral/Negativo:** Mantiene dos estilos en ejemplos y prompts. |
| **Reversibilidad** | **Total:** Es una regla de impresión en `wysna fmt`, no un cambio en la gramática aceptada. | Baja (rompe código de usuario). | Total. |

### 4. Recomendación
Aprobar la **Alternativa A** para el Hito 3 (después de estabilizar `selfhost fmt` en `AGT-01`).

### 5. Riesgos
- Tests de `test_agent_tooling.py` que verifiquen que `wysna fmt` preserva la keyword `new` deberán actualizarse para reflejar la normalización a `Class(...)` (mientras los tests de parser siguen probando `new Class(...)`).

### 6. Condición de reversión
Si durante la evaluación empírica `AGT-06` / `AGT-R01` se observa que ocultar `new` induce confusión semántica entre copia CoW (`struct`) y mutación compartida por referencia (`class`), revertir la regla de `wysna fmt` en un único commit sin impacto en compatibilidad.

---

## ADR-04 (`GAP-06`) — Compilar Targets WebAssembly (`web` y `wasi`) con el `zig cc` Empaquetado

### 1. Problema con evidencia
- En la Fase 2, el Programa C ([`docs/evolution/evidence/prog_c_wasm/`](evidence/prog_c_wasm)) pasó `wysna project check --target web` (`ok: true` en [`check_web_target.json`](evidence/prog_c_wasm/check_web_target.json)), pero `wysna project build --target web` falló con `[W5005] Target 'web' requires Emscripten 5.0.1 ('emcc' on PATH or WYSNA_EMCC)` ([`build_web.json`](evidence/prog_c_wasm/build_web.json)).
- Igualmente, el target `wasi` exige una instalación externa exacta de `wasi-sdk 34` (`src/tooling/ProjectSystem.py:42-43`).
- Sin embargo, el empaquetado de Wysna ya diseña y soporta la distribución de un toolchain **`Zig 0.15.2`** autocontenido (`tools/build_native_dist.py:21-22`, `packaging/native/driver.c`), cuyo compilador C (`zig cc`) incluye de serie soporte cross-target para `wasm32-wasi` y `wasm32-freestanding` sin instalar SDKs externos.

### 2. Alternativas
- **Alternativa A (Recomendada):** Mantener `emcc` (`Emscripten 5.0.1`) y `wasi-sdk 34` como overrides opcionales (`WYSNA_EMCC`, `WYSNA_WASI_CC`), pero utilizar por defecto el `zig cc` empaquetado (`-target wasm32-wasi` para `wasi` y `-target wasm32-freestanding -fno-entry -rdynamic` con un loader JS ligero propio para `web`).
- **Alternativa B:** Mantener `Emscripten 5.0.1` y `wasi-sdk 34` como requisitos externos obligatorios y añadir un comando `wysna doctor --install-wasm-toolchains` que descargue ~1.2 GB de Emscripten/wasi-sdk.
- **Alternativa C (No hacer nada):** Mantener el error `[W5005]` cuando el usuario no haya instalado manualmente `Emscripten 5.0.1`.

### 3. Trade-offs

| Criterio | Alternativa A (`zig cc` empaquetado por defecto) | Alternativa B (Instalador de Emscripten/wasi-sdk) | Alternativa C (No hacer nada) |
| :--- | :--- | :--- | :--- |
| **Corrección** | **Alta:** Para módulos de lógica pura exportada (`export fn`, números, strings, JSON), `wasm32-freestanding` / `wasm32-wasi` compila el mismo C99 sin el overhead de Emscripten. | **Alta:** Conserva el glue JS actual (`ccall`, `UTF8ToString`). | **Baja en UX:** Falla out-of-the-box en máquinas limpias. |
| **Costo de mantenimiento** | **Moderado-Alto (`L`):** Requiere reemplazar `Module.ccall`/`UTF8ToString` en el bridge JS generado por `ProjectSystem.py` por un helper JS estándar (~60 LOC sobre `WebAssembly.instantiate` + `TextEncoder`/`TextDecoder`). | **Moderado (`M`):** Requiere mantener scripts de descarga multiplataforma para SDKs de terceros de >1 GB. | Cero. |
| **Compatibilidad** | **Alta:** Los archivos `.wysna` y `wysna.toml` del usuario no cambian; además `WYSNA_EMCC` sigue funcionando si alguien requiere Emscripten explícitamente. | **100% compatible.** | Compatible. |
| **Impacto en agentes** | **Muy positivo:** Un agente puede ejecutar `wysna project build --target web` y validar el módulo `.wasm` inmediatamente sin bloqueos de entorno `[W5005]`. | **Moderado:** Requiere descarga pesada y red en el primer build. | **Negativo:** Bloquea cualquier flujo Wasm en entornos sin Emscripten 5.0.1. |
| **Reversibilidad** | **Alta:** El backend Emscripten en `ProjectSystem.py` se conserva detrás de `WYSNA_EMCC`. | Total. | N/A |

### 4. Recomendación
Aprobar la **Alternativa A** y programarla en la **Cola Posterior** (inmediatamente después de cerrar los Hitos 1, 2 y 3 del foco principal), adelantando únicamente su sub-etapa `S` (auto-selección del único target en `wysna.toml` cuando no exista `[targets.app]`).

### 5. Riesgos
- En `-target wasm32-freestanding`, las funciones de libc estándar (`malloc`, `free`, `snprintf`, `memcpy`) deben provenir de `wasm32-wasi` (`libc` de Zig sin syscalls de I/O) o de un allocator lineal ligero para que `runtime/wysna_rt.h` enlace sin símbolos faltantes. Se mitiga compilando el target `web` contra `-target wasm32-wasi -mexec-model=reactor` en `zig cc` (que provee `libc`/`malloc`/`snprintf` completos en ~25 KB y solo requiere un stub JS mínimo para `fd_write` en caso de pánico).

### 6. Condición de reversión
Si `-target wasm32-wasi -mexec-model=reactor` bajo `zig cc` presenta incompatibilidades de ABI con el paso de strings o incremento de tamaño `.wasm` superior a `100 KB` frente a `emcc -O2`, mantener `emcc` como primario para `web` y usar `zig cc` únicamente para el target `wasi`.
