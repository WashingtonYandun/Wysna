# 03 · Lenguaje: Sintaxis, Semántica y Sistema de Tipos

La evaluación original y sus repros conservan el estado de la auditoría. `LANG-01` a `LANG-06` ya figuran como implementados; no se deben interpretar todos los diagnósticos en presente de la tabla histórica como bugs abiertos actuales. El siguiente ciclo contrastado con main está en [12-SIGUIENTE-CICLO-LENGUAJE.md](12-SIGUIENTE-CICLO-LENGUAJE.md).

## 1. Evaluación Integral de los 9 Ejes del Lenguaje

Antes de proponer cambios, se evalúan los nueve ejes solicitados desde la perspectiva de un desarrollador (humano o agente) que construye aplicaciones reales, distinguiendo aciertos (`KEEP`) de defectos reproducibles:

| Eje evaluado | Estado real | Diagnóstico técnico con evidencia | Decisión |
| :--- | :---: | :--- | :---: |
| **1. `from ... use` frente a `import`** | **verificado** (con colisión de prelude en `import ... as env`) | Ambas formas están implementadas en Seed y Selfhost (`src/parser/Parser.py`, `selfhost/src/03_modules.wysna`). Cumplen roles complementarios legítimos: `import "std/json" as json` da claridad de espacio de nombres (`json.parse`, `json.object`), mientras `from std.text use { trim, split }` evita prefijos repetitivos. **Fricción detectada en Fase 2:** `import "std/environment" as env` falla en Seed con `W1002` (*"Duplicate declaration of symbol 'env' previously declared at line 1:1"*) por colisión con la función global `env(name)` del prelude, pero **pasa sin error en Selfhost** ([prog_a_check_seed.json](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_a_check_seed.json) vs [prog_a_check_compiler.json](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_a_check_compiler.json)). | **KEEP** ambas sintaxis; corregir paridad y diagnóstico de prelude (**`LANG-02`**). |
| **2. `T ! E`, `?`, `else error` y `Result`** | **verificado** (con bug de inferencia en `return []`/`{}`) | El diseño `T ! E` + `?` + `else err { ... }` es uno de los mayores aciertos de Wysna: evita excepciones ocultas y es más conciso que `Result<T, E>` con `match` obligatorio. **Dos defectos reproducibles:** 1) `return []` o `return {}` dentro de una función `-> list[T] ! E` (o `-> list[T]?`) falla con `W2004: expected 'list[Value] ! str', found 'list[any]'` ([prog_b_check_seed.json](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_b_check_seed.json)); 2) cuando un programa necesita transformar un error al propagar (`parse_int(s)?` dentro de `fn f() -> int ! AppError`), `std/result.map_error` requiere envolver en una lambda verbal (`result.map_error(parse_int(s), |e: ParseError| => ...)?`), lo que lleva en la práctica a repetir bloques `else err { return Err(...) }` de 3 líneas en cada llamada ([prog_a_cli.wysna:29-35](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_a_cli.wysna#L29-L35)). | **KEEP** el modelo `T ! E`; corregir inferencia bidireccional en canales (**`LANG-01`**). |
| **3. Contratos `requires` / `ensures`** | **parcial** | Funcionan bien como aserciones de diseño (`[W4005]`) y como fuente de restricciones para JSON Schema (`wysna schema`). **Defecto estructural en aplicaciones de datos (Fase 2 Programa B):** un contrato `struct Item { id: int requires id > 0 }` aborta el proceso con `[W4005]` si recibe datos inválidos; no existe forma de validar un `struct` construido desde entradas externas (`JSON`/`CLI`) devolviendo `Item ! ContractError`, y `wysna schema --emit-codecs` genera `decode_Item` que hace `panic` `[W4005]` en lugar de devolver `Err(JsonError)` ([prog_b_schema_codecs.txt](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_b_schema_codecs.txt)). | **INVEST** en validación recuperable de contratos en fronteras de datos (**`LANG-03`**). |
| **4. Closures y captura de variables** | **verificado** (captura por snapshot inmutable) | Las lambdas `\|x: int\| => x * 2` capturan variables externas por snapshot Copy-on-Write inmutable (`src/checker/ClosureConversion.py`, `docs/KNOWN_LIMITS.md:17-62`). Intentar mutar una variable capturada produce `W3001` con mensaje explícito. Esta restricción mantiene el modelo ARC/CoW libre de celdas compartidas (`RefCell`) y carreras de datos en `parallel_map`. | **KEEP** captura por snapshot inmutable; documentar patrón con `class` cuando se requiera estado compartido. |
| **5. Structs, clases, interfaces y genéricos** | **verificado** (con dos formas de construir clases) | La separación entre `struct` (valor CoW sin identidad, con actualización funcional `Struct { ..base, field: val }`) y `class` (identidad por referencia ARC, herencia simple `extends`, polimorfismo `implements Interface[T]`) es limpia y está en paridad entre Seed y Selfhost. **Redundancia sintáctica:** para instanciar una clase coexisten dos sintaxis equivalentes (`new BankAccount("Washo", 500)` en `WYSNA_SINTAXIS.md:473` y `stdlib/process.wysna:26` vs `Dog("Rex", "Husky")` en `SPEC.md:227` y `WYSNA_SINTAXIS.md:53`), que bajan al mismo nodo `ConstructObject` (`docs/HIR_DESIGN.md:121`). | **SIMPLIFY** deprecando gradualmente `new Class(...)` en el formateador a favor de `Class(...)` (**`LANG-05`**). |
| **6. Async (`async fn` / `await`)** | **verificado** (bajo uso real en stdlib) | `async fn` y `await` bajan a máquinas de estados CPS sin pila (`src/mir/AsyncLowering.py`, `selfhost/src/08_mir_c99.wysna`) y `std/async` ofrece `Task[T]`, `spawn`, `sleep_ms`, `all`, `race`. Sin embargo, casi toda la stdlib (`std/io`, `std/files`, `std/fs`, `std/process`, `std/http.get`, `std/http.post_json`) es síncrona; solo `std/http.get_async` y `std/async.sleep_ms`/`spawn` se integran con el bucle cooperativo. | **FREEZE** la gramática `async`/`await` actual; no añadir streams asíncronos ni sintaxis nueva en este ciclo. |
| **7. Mutabilidad (`let` vs `let mut`)** | **verificado** | Inmutabilidad por defecto en locales (`let`) y parámetros, mutabilidad explícita con `let mut` y operadores compuestos `+=`, `-=`, `*=` (`SPEC.md:300-306`). La regla de que mutar un campo a través de una instancia de `class` no exige `let mut` en la variable local mientras que mutar un `struct` o `list` sí lo exige (`docs/HIR_DESIGN.md:169-175`) es consistente con la frontera valor vs referencia. | **KEEP** sin cambios. |
| **8. Patrones con boilerplate repetitivo** | **parcial** | En Fase 2 detectamos tres fuentes de boilerplate repetitivo: **1)** `let empty: list[T] = []; return empty` por el fallo de `return []` en funciones `T ! E` (**`LANG-01`**); **2)** construir diccionarios JSON manualmente con `json.from_int`/`json.from_str` campo por campo porque `--emit-codecs` no valida `requires` ni está integrado en el flujo (**`LANG-03`**); **3)** tener que escribir `text.trim(text.split(s, ",")[0])` importando `std/text` porque `str` solo tiene `.contains()` y `.is_empty()` como métodos mientras `WYSNA_SINTAXIS.md:661-664` promete `.trim()`, `.split()`, `.to_upper()`, `.starts_with()` (**`LANG-04`**). | Resolver con **`LANG-01`**, **`LANG-03`** y **`LANG-04`** sin añadir keywords nuevas. |
| **9. Precedencia de operadores y formato canónico** | **parcial** | El orden de evaluación `L→R` está bien especificado y garantizado por HIR/MIR. **Dos problemas verificados:** 1) La tabla normativa de 12 niveles de precedencia en `SPEC.md:239-254` **omite** los operadores `?` (postfijo), `else` / `else err`, `await`, y `+=`/`-=`/`*=`; 2) el formateador nativo de Selfhost (`selfhost_format_body` en `selfhost/src/09_agent_tools.wysna:1398`) **no implementa el formato canónico de `SPEC.md:596`** y corrompe literales `"""..."""` ([repro_selfhost_fmt.txt](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/repro_selfhost_fmt.txt)). | Documentar tabla de precedencia completa (**`LANG-06`**) y reparar `fmt` nativo (ver **`AGT-01`** en `04-AGENT-FIRST.md`). |

---

## 2. Backlog Categorizado de Lenguaje (`LANG-01` .. `LANG-06`)

### `LANG-01` · Inferencia bidireccional de colecciones vacías (`[]` y `{}`) a través de canales `T ! E` y `T?`

- **Prioridad:** P0
- **Decisión:** INVEST
- **Evidencia:** [src/checker/CollectionRules.py:194-198](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/src/checker/CollectionRules.py#L194-L198), [src/checker/CollectionRules.py:239-243](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/src/checker/CollectionRules.py#L239-L243), [selfhost/src/08_semantics.wysna:1540-1595](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/selfhost/src/08_semantics.wysna#L1540-L1595), y reproducción de Fase 2 en [prog_b_check_seed.json](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_b_check_seed.json) y [prog_b_check_compiler.json](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_b_check_compiler.json). Nivel: **verificado**.
- **Problema:** Al escribir una función falible o anulable que devuelve una lista o diccionario (por ejemplo, `fn load_store(path: str) -> list[json.Value] ! str`), el retorno temprano de una colección vacía (`if not exists { return [] }`) falla en Seed y en Selfhost con `[W2004] Return type mismatch: expected 'list[Value] ! str', found 'list[any]'`. Esto contradice directamente `WYSNA_SINTAXIS.md:241` y `SPEC.md:301`, y obliga al desarrollador o agente a declarar una variable temporal anotada antes de cada retorno vacío.
- **Propuesta:** Desenvolver el tipo de éxito `T` cuando `checker.expected_type` sea `ResultType(ok=T, err=E)` o `OptionalType(inner=T)` durante la inferencia bidireccional de literales de colección (`ListExpr`, `DictExpr`, `TupleExpr`) tanto en Seed (`CollectionRules.py`) como en Selfhost (`08_semantics.wysna`).
  - **Antes (código real requerido hoy en `prog_b_api.wysna:34-36`):**
    ```wysna
    fn load_store(db_path: str) -> list[json.Value] ! str {
        let empty: list[json.Value] = []
        if not exists {
            return empty
        }
        return empty
    }
    ```
  - **Después:**
    ```wysna
    fn load_store(db_path: str) -> list[json.Value] ! str {
        if not exists {
            return []
        }
        return []
    }
    ```
- **Alternativas:**
  1. *No hacer nada:* mantener el error `W2004` y corregir `SPEC.md:301` / `WYSNA_SINTAXIS.md:241` para advertir que `return []` no funciona en funciones `T ! E` ni `T?`. Descartado porque casi toda función de I/O o parsing en Wysna retorna `T ! E`.
  2. *Resolverlo con lo que ya existe:* exigir una variable intermedia `let empty: list[T] = []; return empty` o emitir un fix-it en `wysna fix`. Reduce el síntoma tras fallar, pero no evita el error `Pass@1` de autoría.
- **Impacto (1–5):** Usuario: **5** | Agentes: **5** | Reducción de complejidad: **3** (elimina boilerplate en funciones `T ! E` y alinea el código con `SPEC.md:301`).
- **Esfuerzo:** **S (<1 día)**. Supuestos: ~20 líneas en `src/checker/CollectionRules.py` (helper `_unwrap_channel_expected_type`) y ~25 líneas equivalentes en `selfhost/src/08_semantics.wysna` + regeneración del bundle `selfhost/compiler.wysna`.
- **Riesgo:** **Bajo.** Solo afecta cuando el literal `[]` o `{}` carece de elementos y el contexto esperado es `list[T] ! E`, `dict[K, V] ! E`, `list[T]?` o `dict[K, V]?` (donde el tipo de éxito `T` no coincide con el tipo de error `E`).
- **Compatibilidad y migración:** 100% retrocompatible; todo programa válido actual sigue compilando sin cambios.
- **Componentes afectados:** `src/checker/CollectionRules.py`, `selfhost/src/08_semantics.wysna`, `selfhost/compiler.wysna`, `tests/types/valid/`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** Un test en `tests/runtime/` y `tests/types/valid/` con `fn f() -> list[int] ! str { return [] }`, `fn g() -> dict[str, int] ! str { return {} }` y `fn h() -> list[str]? { return [] }` pasa en `./wysna test` a través de los 4 motores (`interpreter`, `ast`, `hir`, `selfhost`).
- **Requiere aprobación:** no (alinea la implementación con el comportamiento ya especificado en `SPEC.md:301`).
- **Estado:** implementado.

---

### `LANG-02` · Unificar la regla de colisión con símbolos del prelude (`env`, `bytes`, `str`, `int`, `float`) entre Seed y Selfhost

- **Prioridad:** P1
- **Decisión:** SIMPLIFY
- **Evidencia:** Reproducción en Fase 2 al escribir `import "std/environment" as env` en [prog_a_cli.wysna](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_a_cli.wysna): Seed rechaza con `[W1002] Duplicate declaration of symbol 'env' (previously declared at line 1:1)` ([prog_a_check_seed.json](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_a_check_seed.json)) mientras Selfhost acepta con `status: "ok"` ([prog_a_check_compiler.json](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_a_check_compiler.json)); ver también [src/checker/SymbolTable.py:45-92](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/src/checker/SymbolTable.py#L45-L92) y [src/checker/DeclarationPass.py:85-145](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/src/checker/DeclarationPass.py#L85-L145). Nivel: **verificado**.
- **Problema:**
  1. En Seed, los identificadores del prelude global (`env`, `args`, `bytes`, `len`, `int`, `float`, `str`, `ord`, `chr`, `slice`, `read_file`, `write_file`, `unix_time`, `exit`, `print`, `eprint`) se insertan en el mismo `global_scope` que las declaraciones del módulo con ubicación ficticia `line 1, col 1`. Cuando un desarrollador o agente escribe `import "std/environment" as env` o `import "std/bytes"` (cuyo alias por defecto es `bytes`!), Seed emite un error confuso diciendo que `'env'` o `'bytes'` fue *"previously declared at line 1:1"* en el propio archivo del usuario.
  2. Peor aún, Selfhost (`selfhost/src/03_modules.wysna` y `08_semantics.wysna`) **sí permite** `import "std/environment" as env`, rompiendo la paridad semántica `Seed ↔ Selfhost`.
- **Propuesta:** Separar el `prelude_scope` como ámbito padre inmutable del `module_scope` en Seed (o, si se decide prohibir el sombreado de builtins del prelude, aplicar la misma prohibición en Selfhost y emitir un mensaje explícito `"Cannot shadow built-in prelude function 'env'; choose a different alias such as 'environ'"` sin span falso `line 1:1`, permitiendo al menos que `import "std/bytes"` funcione porque `std/bytes` es un módulo oficial de la stdlib cuyo nombre base colisiona con `bytes(s)`!).
  - **Antes (hoy en Seed):**
    ```wysna
    import "std/environment" as env
    # [W1002] Duplicate declaration of symbol 'env' (previously declared at line 1:1)
    ```
  - **Después (Opción recomendada — diagnóstico claro + paridad Seed/Selfhost, o ámbito prelude padre):**
    ```wysna
    # Si se mantiene la prohibición de sombrear el prelude en ambos motores:
    # [W1002] Symbol 'env' conflicts with built-in prelude function 'env(name: str) -> str?'; use a distinct alias (e.g., 'import "std/environment" as environ').
    ```
- **Alternativas:**
  1. *No hacer nada:* mantener la divergencia donde un programa con `import "std/environment" as env` compila en el Native SDK pero falla en Seed. Inaceptable por violar la autoridad semántica de Seed (`SPEC.md:5`).
  2. *Resolverlo con lo que ya existe:* usar siempre `import "std/environment" as environ` en código nuevo. No corrige la divergencia entre motores ni el span falso `line 1:1`.
  3. *Permitir que un alias de `import` sombree el prelude en el ámbito del módulo:* haría que `import "std/bytes"` e `import "std/environment" as env` funcionen de forma natural, pero cambia una regla de ámbito existente.
- **Impacto (1–5):** Usuario: **4** | Agentes: **4** | Reducción de complejidad: **3**.
- **Esfuerzo:** **S (<1 día)**. Supuestos: marcar los símbolos del prelude con `is_prelude=True` / `origin="prelude"` en `src/checker/SymbolTable.py` y alinear la regla y el mensaje en `src/checker/DeclarationPass.py` y `selfhost/src/08_semantics.wysna`.
- **Riesgo:** **Bajo.**
- **Compatibilidad y migración:** Si se alinea Selfhost con Seed prohibiendo sombrear el prelude (con diagnóstico explícito y autofix sugerido `as environ`), ningún test del corpus actual se rompe (`843/843` ya cumplen la regla de Seed).
- **Componentes afectados:** `src/checker/SymbolTable.py`, `src/checker/DeclarationPass.py`, `selfhost/src/08_semantics.wysna`, `tests/types/invalid/`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** `wysna check` y `wysna check --engine=compiler` producen el mismo código diagnóstico (`W1002`) ante `import "std/environment" as env`, y el mensaje indica explícitamente que `'env'` es un símbolo del prelude global (sin `previously declared at line 1:1`).
- **Requiere aprobación:** no (si se alinea Selfhost a la regla normativa actual de Seed y se corrige el mensaje).
- **Estado:** implementado.

---

### `LANG-03` · Validación recuperable de contratos `requires` en decodificadores JSON (`wysna schema --emit-codecs`)

- **Prioridad:** P0
- **Decisión:** INVEST
- **Evidencia:** [src/tooling/SchemaCLI.py:472-603](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/src/tooling/SchemaCLI.py#L472-L603), [selfhost/src/09_schema.wysna:950-1210](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/selfhost/src/09_schema.wysna#L950-L1210), y salida real de Fase 2 en [prog_b_schema_codecs.txt](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_b_schema_codecs.txt). Nivel: **verificado**.
- **Problema:** Wysna promueve los contratos de campo en `struct` (`struct Item { id: int requires id > 0, amount_cents: int requires amount_cents >= 0 }`) como una característica central para validar datos antes de guardarlos (`README.md:9,30-38`). Sin embargo, construir un `struct` cuyos campos incumplen `requires` dispara un `panic` de proceso irrecuperable (`[W4005]`). Cuando un usuario o agente genera codecs con `wysna schema --emit-codecs`, la función generada `pub fn decode_Item(raw_val: json.Value) -> Item ! json.JsonError` construye `Item { id: _f_id, ... }` directamente **sin evaluar antes las expresiones `requires` de cada campo** ([prog_b_schema_codecs.txt:15-20](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_b_schema_codecs.txt#L15-L20)). Como resultado, pasar un JSON externo con `{"id": 0, ...}` a `from_json_Item` mata el proceso con `[W4005]` en vez de retornar `Err(JsonError)`.
- **Propuesta:** Sin introducir ninguna keyword nueva en el lenguaje, hacer que `wysna schema --emit-codecs` (tanto en `src/tooling/SchemaCLI.py` como en `selfhost/src/09_schema.wysna`) emita comprobaciones explícitas de cada predicado `requires` del campo (sustituyendo el nombre del campo por `_f_<campo>`) antes del literal `Struct { ... }`, retornando `Err(json.error(0, "<campo>: contract violation (<predicado>)"))` cuando falle.
  - **Antes (salida real actual de `./wysna schema --emit-codecs` en `prog_b_schema_codecs.txt`):**
    ```wysna
    pub fn decode_Item(raw_val: json.Value) -> Item ! json.JsonError {
        let _f_id = json.get_int(raw_val, "id")?
        let _f_title = json.get_str(raw_val, "title")?
        let _f_amount_cents = json.get_int(raw_val, "amount_cents")?
        return Item { id: _f_id, title: _f_title, amount_cents: _f_amount_cents }
    }
    ```
  - **Después:**
    ```wysna
    pub fn decode_Item(raw_val: json.Value) -> Item ! json.JsonError {
        let _f_id = json.get_int(raw_val, "id")?
        if not (_f_id > 0) {
            return Err(json.error(0, "id: failed contract 'id > 0'"))
        }
        let _f_title = json.get_str(raw_val, "title")?
        let _f_amount_cents = json.get_int(raw_val, "amount_cents")?
        if not (_f_amount_cents >= 0) {
            return Err(json.error(0, "amount_cents: failed contract 'amount_cents >= 0'"))
        }
        return Item { id: _f_id, title: _f_title, amount_cents: _f_amount_cents }
    }
    ```
- **Alternativas:**
  1. *No hacer nada:* dejar que `decode_<Struct>` aborte el proceso con `W4005` ante JSON inválido. Hace inútil `--emit-codecs` para entradas externas no confiables (APIs HTTP, CLIs, tool calls de LLMs).
  2. *Resolverlo con lo que ya existe:* escribir a mano las validaciones `if id <= 0` antes de construir el `struct` (como tuvimos que hacer en `prog_b_api.wysna:67-70`), duplicando cada cláusula `requires`.
  3. *Cambiar la semántica del lenguaje para que `Struct { ... }` con `requires` evalúe siempre a `Struct ! ContractError`:* rompería todos los constructores existentes y añadiría ceremonia a la construcción interna con invariantes ya probadas. Emitirlo en `decode_<Struct>` resuelve la frontera de datos sin cambiar la semántica de construcción interna.
- **Impacto (1–5):** Usuario: **5** | Agentes: **5** | Reducción de complejidad: **4** (conecta `requires` con `T ! JsonError` sin keywords nuevas).
- **Esfuerzo:** **S (<1 día)**. Supuestos: ~45 líneas en `src/tooling/SchemaCLI.py` y ~60 líneas en `selfhost/src/09_schema.wysna` para reescribir el identificador del campo a `_f_<campo>` y emitir el `if not (...) { return Err(...) }`.
- **Riesgo:** **Bajo.** Solo modifica el código generado por `--emit-codecs` para `struct` con cláusulas `requires`.
- **Compatibilidad y migración:** Compatible hacia adelante; los llamadores de `decode_<Struct>` ya manejan `T ! json.JsonError`.
- **Componentes afectados:** `src/tooling/SchemaCLI.py`, `selfhost/src/09_schema.wysna`, `selfhost/compiler.wysna`, `tests/unit/test_agent_tools.py`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** Ejecutar `from_json_Item("{\"id\": 0, \"title\": \"x\", \"amount_cents\": 10}")` sobre el código emitido por `wysna schema --emit-codecs` (en Seed y en Selfhost) devuelve `Err(JsonError)` con el nombre del campo y el contrato violado, sin abortar el proceso con `[W4005]`.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

### `LANG-04` · Resolver la fractura entre métodos de `str` documentados (`s.trim()`, `s.split()`, `s.starts_with()`, `s.ends_with()`, `s.replace()`, `s.to_lower()`, `s.to_upper()`) y `std/text`

- **Prioridad:** P1
- **Decisión:** INVEST
- **Evidencia:** [WYSNA_SINTAXIS.md:660-665](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/WYSNA_SINTAXIS.md#L660-L665), [SPEC.md:767-769](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/SPEC.md#L767-L769), [src/checker/AccessRules.py:120-210](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/src/checker/AccessRules.py#L120-L210), y reproducción en [repro_str_methods.wysna](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/repro_str_methods.wysna) / [repro_str_methods.txt](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/repro_str_methods.txt). Nivel: **verificado**.
- **Problema:** `WYSNA_SINTAXIS.md:660-665` (el manual de referencia en español del repositorio) enseña explícitamente que `str` soporta `text.trim()`, `clean.split(" ")`, `clean.to_upper()`, `clean.starts_with("Wys")`, además de `.contains()` e `.is_empty()`. Sin embargo, el compilador solo implementa `.contains()` e `.is_empty()` sobre `str` y rechaza `.trim()`, `.split()`, `.to_upper()`, `.to_lower()`, `.starts_with()`, `.ends_with()` y `.replace()` con `W3002`. Tanto desarrolladores humanos como LLMs entrenados en Python/Rust/TS/JS (y guiados por `WYSNA_SINTAXIS.md`) intentan encadenar `line.trim().split(",")`, fallando en `Pass@1` (`api_hallucination` en la taxonomía de `bench/ai_eval/`).
- **Propuesta:** Tenemos dos caminos limpios; recomendamos **implementar en el compilador los 7 métodos intrínsecos de `str` ya documentados en `WYSNA_SINTAXIS.md:660-665`** (`.trim()`, `.split(sep)`, `.to_lower()`, `.to_upper()`, `.starts_with(prefix)`, `.ends_with(suffix)`, `.replace(old, new)`) delegando en las primitivas existentes del runtime C99 e intérprete (o, como alternativa mínima sin tocar el lenguaje, eliminar el bloque falso de `WYSNA_SINTAXIS.md:660-664` y añadir un fix-it automático en `W3002` que reescriba `s.trim()` a `text.trim(s)`).
  - **Antes (falla hoy con `W3002` pese a estar en `WYSNA_SINTAXIS.md:661-664`):**
    ```wysna
    let clean = line.trim()
    let cols = clean.split(",")
    ```
  - **Después (soportado o auto-reparado por `wysna fix`):**
    ```wysna
    let clean = line.trim()
    let cols = clean.split(",")
    ```
- **Alternativas:**
  1. *No hacer nada:* mantener `WYSNA_SINTAXIS.md:661-664` con código que no compila. Inaceptable.
  2. *Resolverlo solo en documentación y diagnósticos (sin métodos nuevos en `str`):* borrar `text.trim()`/`split()`/`to_upper()`/`starts_with()` de `WYSNA_SINTAXIS.md:661-664` y agregar en `AccessRules.py` un hint `W3002`: `"Use 'text.trim(s)' from 'std/text' instead"`. Es la opción de menor superficie si se prefiere mantener `str` con solo 2 métodos.
  3. *Implementar los 7 métodos en `str` (Seed + Selfhost + LLVM):* elimina el import obligatorio de `std/text` en scripts pequeños y permite encadenamiento fluido `line.trim().split(",")` tal como ya hacen `.contains()` e `.is_empty()`.
- **Impacto (1–5):** Usuario: **5** | Agentes: **5** (elimina una de las alucinaciones `Pass@1` más frecuentes en LLMs) | Reducción de complejidad: **3**.
- **Esfuerzo:** **M (2–3 días)** si se implementan los 7 métodos en Seed, Selfhost y runtime; **S (<0.5 días)** si se corrige `WYSNA_SINTAXIS.md` y se añade el `help`/fix-it en `W3002`.
- **Riesgo:** **Bajo.** Los algoritmos ASCII/UTF-8 ya existen en `stdlib/text.wysna` y `runtime/wysna_runtime_text.h`.
- **Compatibilidad y migración:** 100% aditivo; `std/text` conserva todas sus funciones actuales (`text.trim(s)`, `text.split(s, sep)`, etc.).
- **Componentes afectados:** `WYSNA_SINTAXIS.md`, `SPEC.md`, `src/checker/AccessRules.py`, `runtime/ExpressionRules.py`, `runtime/wysna_runtime_text.h`, `src/mir/Lowering.py`, `selfhost/src/08_semantics.wysna`, `selfhost/src/08_mir_c99.wysna`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** [repro_str_methods.wysna](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/repro_str_methods.wysna) compila y ejecuta con salida idéntica en los 4 motores (o, si se aprueba la alternativa documental en `10-DECISIONES.md`, `WYSNA_SINTAXIS.md` no contiene métodos inexistentes y `W3002` sugiere `std/text.<fn>`).
- **Requiere aprobación:** sí (añade métodos built-in al tipo primitivo `str` o decide mantenerlos solo en `std/text`; ver `10-DECISIONES.md`).
- **Estado:** implementado.

---

### `LANG-05` · Unificar la sintaxis canónica de construcción de clases (`Class(...)` vs `new Class(...)`)

- **Prioridad:** P2
- **Decisión:** SIMPLIFY
- **Evidencia:** [WYSNA_SINTAXIS.md:53](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/WYSNA_SINTAXIS.md#L53) (*"Usa `Type(...)` para construir clases"*), [WYSNA_SINTAXIS.md:473](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/WYSNA_SINTAXIS.md#L473) (*"`let account = new BankAccount(\"Washo\", 500)`"*), [SPEC.md:227-231](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/SPEC.md#L227-L231), [docs/HIR_DESIGN.md:121](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/HIR_DESIGN.md#L121), [src/formatter/Formatter.py:320-345](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/src/formatter/Formatter.py#L320-L345). Nivel: **verificado**.
- **Problema:** Wysna tiene hoy dos formas equivalentes de instanciar una clase: `ClassName(args...)` y `new ClassName(args...)`. Incluso dentro del mismo documento `WYSNA_SINTAXIS.md`, la línea 53 recomienda `Type(...)` mientras la línea 473 enseña `new BankAccount(...)`, y `stdlib/process.wysna:26` usa `new buffer.Bytes()`. Para un lenguaje Agent-First cuya filosofía es *"una sola representación canónica sin decisiones arbitrarias"* (`SPEC.md:11,597`), tener una keyword reservada `new` que es 100% redundante con `ClassName(...)` contradice el principio de diseño y divide el estilo del código.
- **Propuesta:** Mantener `new ClassName(...)` aceptado en el parser por compatibilidad hacia atrás, pero hacer que **`wysna fmt` normalice canónicamente `new ClassName(...)` a `ClassName(...)`** (igual que ya normaliza `else =>` a `_ =>` según `WYSNA_SINTAXIS.md:401`), y actualizar todos los ejemplos de `WYSNA_SINTAXIS.md` y `stdlib/` a `ClassName(...)`.
  - **Antes (`WYSNA_SINTAXIS.md:473` y `stdlib/process.wysna:26`):**
    ```wysna
    let account = new BankAccount("Washo", 500)
    let result = new buffer.Bytes()
    ```
  - **Después (normalizado automáticamente por `wysna fmt`):**
    ```wysna
    let account = BankAccount("Washo", 500)
    let result = buffer.Bytes()
    ```
- **Alternativas:**
  1. *No hacer nada:* mantener `new` y sin-`new` indefinidamente en la documentación y en el formateador. Mantiene una decisión arbitraria innecesaria para humanos y agentes.
  2. *Resolverlo con lo que ya existe:* actualizar únicamente la documentación (`WYSNA_SINTAXIS.md:473`) sin normalizar en `wysna fmt` (opción sin necesidad de aprobación si se prefiere no tocar la salida del formateador).
  3. *Eliminar la keyword `new` del lexer/parser de inmediato:* rompería tests existentes que prueban `new`. Normalizar en `wysna fmt` (o documentar `Class(...)` como única forma canónica) evita rupturas bruscas.
- **Impacto (1–5):** Usuario: **2** | Agentes: **4** (elimina una bifurcación sintáctica arbitraria) | Reducción de complejidad: **3**.
- **Esfuerzo:** **S (<1 día)**. Supuestos: cambiar `format_new_expr` en `src/formatter/Formatter.py` y actualizar los tests de formato que esperaban `new `, o en su defecto unificar `WYSNA_SINTAXIS.md:473` y `stdlib/`.
- **Riesgo:** **Bajo.** En `HirBuilder.py` y `selfhost`, `NewExpr` y `CallExpr` sobre una clase producen exactamente el mismo nodo `ConstructObject`.
- **Compatibilidad y migración:** El parser sigue aceptando `new Class(...)` sin error; `wysna fmt --write` migra cualquier archivo automáticamente.
- **Componentes afectados:** `WYSNA_SINTAXIS.md`, `stdlib/process.wysna`, `stdlib/files.wysna`, `src/formatter/Formatter.py`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** Todos los ejemplos de `WYSNA_SINTAXIS.md`, `SPEC.md` y `stdlib/*.wysna` usan `Class(...)` de forma uniforme, y `wysna fmt` produce una única representación canónica.
- **Requiere aprobación:** sí (si `wysna fmt` reescribe `new Class(...)` → `Class(...)`; no requiere aprobación si solo se unifica en docs y `stdlib/`).
- **Estado:** implementado.

---

### `LANG-06` · Completar la tabla normativa de precedencia de operadores en `SPEC.md` (`?`, `else`/`else err`, `await`, `+=`/`-=`/`*=`)

- **Prioridad:** P1
- **Decisión:** SIMPLIFY
- **Evidencia:** [SPEC.md:236-254](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/SPEC.md#L236-L254) vs [src/parser/Parser.py:420-690](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/src/parser/Parser.py#L420-L690) y [CHANGELOG.md:19](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/CHANGELOG.md#L19) (*"fixed `await f()?` / `await f() else err` precedence"*). Nivel: **verificado**.
- **Problema:** La sección 5 de `SPEC.md` (*"Evaluation follows an explicit 12-level precedence hierarchy"*, líneas 238–254) define 12 niveles desde `()` hasta `=`, pero **no incluye** cinco operadores clave del lenguaje:
  1. Postfijo `?` (propagación de `T?` / `T ! E`, que liga al nivel postfijo junto con `.`, `[]`, `()`).
  2. Unario prefijo `await` (que según `CHANGELOG.md:19` tiene menor precedencia que `.`, `()`, `[]` y `?` para que `await f()?` sea `await (f())?` o `(await f())?`, pero más alta que `else err` para que `await f() else err { ... }` capture el resultado esperado).
  3. Posfijo de recuperación `else { ... }` / `else err { ... }`.
  4. Operadores de asignación compuesta `+=`, `-=`, `*=` (nivel de asignación junto con `=`).
  Un desarrollador o agente que lea `SPEC.md` no puede saber si `await fetch(url)?` o `a + b else err { 0 }` requiere paréntesis sin leer `src/parser/Parser.py` o `selfhost/src/08_semantics.wysna`.
- **Propuesta:** Actualizar la tabla normativa de `SPEC.md:239-254` y `WYSNA_SINTAXIS.md:124-137` para reflejar exactamente los niveles implementados en `src/parser/Parser.py` y verificados por los tests de precedencia (`await`, `?`, `else`/`else err`, `+=`/`-=`/`*=`), con ejemplos explícitos de agrupación canónica (`(await f())?`, `await f() else err { ... }`).
  - **Antes (`SPEC.md:239-254`):**
    - Nivel 1: `()`, Nivel 2: `. []`, Nivel 3: `- not`, ..., Nivel 11: `??`, Nivel 12: `=` (sin `?`, `await`, `else err`, `+=`, `-=`, `*=`).
  - **Después (`SPEC.md` §5):**
    - Tabla completa de 14 niveles incluyendo Nivel 2: `.` `[]` `()` `?` (postfijos, izquierda), Nivel 3: `-` `not` `await` (prefijos, derecha; con nota explícita de que `?` postfijo sobre `await expr?` aplica tras el `await` según la regla de `Parser.py`), Nivel 12: `else` / `else err` (recuperación de canal), Nivel 13: `=` `+=` `-=` `*=` (asignación, derecha).
- **Alternativas:**
  1. *No hacer nada:* dejar fuera de la tabla de precedencia de `SPEC.md` los operadores más distintivos del lenguaje (`?`, `else err`, `await`).
  2. *Resolverlo con lo que ya existe:* obligar a los usuarios a inferir la precedencia leyendo `CHANGELOG.md` o `tests/`.
- **Impacto (1–5):** Usuario: **4** | Agentes: **4** | Reducción de complejidad: **2**.
- **Esfuerzo:** **S (<0.5 días)**. Supuestos: actualización documental en `SPEC.md` y `WYSNA_SINTAXIS.md` contrastada línea por línea con `src/parser/Parser.py` y `selfhost/src/08_semantics.wysna`.
- **Riesgo:** **Bajo** (no cambia la gramática implementada; documenta la verdad del parser).
- **Compatibilidad y migración:** Sin impacto en código existente.
- **Componentes afectados:** `SPEC.md`, `WYSNA_SINTAXIS.md`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** Todos los operadores léxicos de `WYSNA_SINTAXIS.md:124-137` aparecen en la tabla de precedencia de `SPEC.md` §5 y coinciden con un test en `tests/parser/valid/` que verifica `await f()?`, `await f() else err { ... }`, `a ?? b else { c }` y `x += y * z`.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

## Siguiente ciclo · LANG-07 · Errores genéricos concretos en el compilador nativo

- **Prioridad:** P1, paridad del sistema de tipos.
- **Problema reproducido en main:** Seed acepta `BoxErr[int] | GenErr[str]`; el SDK nativo lo rechaza con `W2001: generic nominal types cannot be error union alternatives`.
- **Cambio implementado en candidato:** permitir alternativas nominales genéricas concretas; analizar patrones `Failure[T](error)` mediante el parser de tipos existente; mantener una identidad por especialización al comprobar exhaustividad; canonicalizar de nuevo una unión al sustituir parámetros genéricos.
- **Caso de seguridad:** `Failure[T] | Failure[int]` con `T=int` se rechaza con `W2001`, igual que Seed. Antes de la canonicalización ese borde producía `W9999` al bajar a MIR.
- **Prueba de aceptación:** mismo resultado en intérprete, AST C99, Seed MIR C99 y Selfhost; regresión previa también verificada con LLVM. Rechazar handlers no exhaustivos (`W2003`), alternativas ajenas (`W2001`) y propagación incompatible (`W2006`). Comprobar payloads gestionados con ASan/UBSan en POSIX y memoria balanceada en los motores nativos.
- **Fuentes:** `selfhost/src/08_semantics.wysna`, bundle regenerado y `tests/unit/test_native_generic_error_unions.py`.
- **Compatibilidad:** no añade sintaxis ni cambia la ABI; amplía el perfil nativo para una construcción ya soportada en Seed.
- **Estado:** implementado en rama candidata; sujeto a revisión e integración.

---

## 3. Hipótesis (`RESEARCH`)

### `LANG-R01` · Hipótesis: Añadir azúcar sintáctico o método `.map_err(...)` para transformar errores sin bloques `else err { return Err(...) }`

- **Observación anecdótica (1 agente en Fase 2):** En [prog_a_cli.wysna:29-62](file:///Users/washingtonyandun/Documents/ChatGPT/w_lang_compiler/docs/evolution/evidence/prog_a_cli.wysna#L29-L62), al llamar a funciones de distintos módulos (`parse.parse_int` → `ParseError`, `http.get` → `HttpError`, `json.get_str` → `JsonError`, `io.read_text` → `IoError`) dentro de una función CLI que retorna `T ! str`, fue necesario escribir 6 bloques `else err { return Err(f"...") }` de 3 líneas cada uno. Aunque existe `std/result.map_error(val, |e: ErrType| => ...)?`, exige importar `std/result` y anotar el tipo del parámetro en la lambda.
- **Por qué es `RESEARCH` y no `INVEST` hoy:** Wysna ya soporta uniones cerradas de error (`T ! ParseError | HttpError | JsonError | IoError`) donde `?` propaga automáticamente cualquier subconjunto sin conversión (`SPEC.md:414-430`). Antes de añadir un método intrínseco `.map_err(...)` sobre valores `T ! E` o nueva sintaxis, debemos medir en el corpus de `bench/ai_eval/` si enseñar a los agentes a usar uniones `T ! E1 | E2` en funciones intermedias y un único `match err` en el borde reduce más errores y tokens que añadir otro mecanismo de transformación de errores.
- **Protocolo para falsificarla:** En la campaña `bench/ai_eval/` (`TASK-13`..`TASK-20`), medir cuántas veces un modelo falla con `W2006` por incompatibilidad de canal de error en `?`. Si `< 10%` de los fallos `W2006` se deben a transformación de errores entre capas (o si se resuelven documentando `T ! E1 | E2` en `wysna skills`), se descarta añadir `.map_err(...)` intrínseco.
