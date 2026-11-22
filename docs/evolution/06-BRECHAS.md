# 06 · Brechas: Capacidades para Scripting, Web y Propósito General

> Auditoría histórica de la línea base `a2549fa`. Los problemas y mediciones describen ese snapshot; los estados de implementación y el ciclo vigente se consultan en [00-INDEX.md](00-INDEX.md) y [12-SIGUIENTE-CICLO-LENGUAJE.md](12-SIGUIENTE-CICLO-LENGUAJE.md).

## 1. Matriz `Capacidad × Caso de Uso`

Estado verificado (`verificado` / `parcial` / `ausente`) e importancia (`crítica` / `útil` / `prescindible`) para los cuatro casos de uso de la visión de producto:
- **CU1:** Scripting y Automatización CLI *(Foco del ciclo — `01-FOCO.md`)*
- **CU2:** Herramientas Nativas y Agentes Locales
- **CU3:** Backends HTTP Pequeños
- **CU4:** Lógica Compartida en Navegador (Wasm)

| Capacidad | Estado actual | CU1: Scripting / CLI | CU2: Herramientas Nativas | CU3: Backend HTTP Pequeño | CU4: Lógica Wasm en Navegador | Evidencia y diagnóstico real |
| :--- | :---: | :---: | :---: | :---: | :---: | :--- |
| **1. Módulos y dependencias** | **parcial** | Crítica | Crítica | Crítica | Útil | `import` y `from ... use` multi-archivo (**verificado**); manifiestos `wysna.toml` v1 y `wysna.lock` con dependencias por ruta local `path = "vendor/..."` (**verificado** en Seed y Native SDK). **Ausente:** dependencias remotas (Git/registro) y espacios de nombres de paquetes externos. |
| **2. Stdlib: I/O y Filesystem (`std/io`, `std/files`, `std/fs`, `std/path`)** | **parcial** | Crítica | Crítica | Útil | Prescindible | Lectura/escritura de archivos, creación exclusiva (`create_exclusive`), `exists`, `size`, `mkdir`, `remove`, `rename`, `stdin` (`read_line`, `read_all`) (**verificado**). **Ausente:** listar directorios (`fs.read_dir` / `list_dir` existe solo como helper interno `wysna_host_project_entries` para `project lock`, **no expuesto en `std/fs`!**), archivos/directorios temporales seguros (`fs.temp_dir`), `path.ext`, permisos y recorrido de árboles (**`GAP-02`**). |
| **3. Stdlib: Procesos y Entorno (`std/process`, `std/environment`)** | **parcial** | Crítica | Crítica | Útil | Prescindible | `environment.arguments()`, `get()`, `has()`, `get_or()`, `current_directory()` (**verificado**). **Brecha crítica en `std/process`:** `process.run` solo devuelve el código de salida `int ! ProcessError` y hereda `stdout`/`stderr` del padre; **es imposible capturar la salida de un comando externo** (`git`, `jq`, `curl`, etc.) desde un script Wysna (`stdlib/process.wysna:42-43`) (**`GAP-01`**). |
| **4. Stdlib: Tiempo (`std/time`)** | **verificado** | Útil | Útil | Útil | Prescindible | `unix_seconds`, `now_ms`, `monotonic_ms`, `elapsed_ms`, `sleep`, conversiones UTC `DateTime` y formato/parsing `ISO-8601` (`stdlib/time.wysna:1-216`). Cubre las necesidades de scripting y telemetría. |
| **5. Stdlib: JSON, CSV y Texto (`std/json`, `std/text`, `std/parse`)** | **parcial** | Crítica | Crítica | Crítica | Crítica | `std/json` con límites de seguridad y `std/text` con normalización Unicode UAX #15 (**verificado**). **Brechas:** 1) No existe módulo `std/csv` (el parser de `examples/ffi/sqlite/import_csv.wysna` rechaza comillas, comas escapadas y signos: `docs/FFI_VALIDATION.md:9`) (**`GAP-04`**); 2) no existen expresiones regulares ni búsqueda por patrones en `std/text`; 3) `dict[K, V]` no tiene `.remove(k)` ni `.clear()` (**`GAP-03`**). |
| **6. HTTP Cliente (`std/http`)** | **parcial** | Crítica | Útil | Útil | Prescindible | `get`, `post_json`, `request`, `batch_get`, `get_async` (**verificado** cuando `libcurl >= 8.3` está instalado). **Fricción:** No sigue redirecciones `3xx` ni siquiera de forma opt-in (`follow_redirects`), exige instalar `libcurl` + `curl-config` (o `pycurl` en intérprete) fuera del core offline (`docs/STDLIB.md:111-112`), y limita cuerpos a `1 MiB` fijo. |
| **7. HTTP Servidor** | **ausente** (solo piloto CGI `wysna service serve`) | Prescindible | Prescindible | Crítica | Prescindible | `docs/STDLIB.md:112` (*"an HTTP server are not included"*). `wysna service serve` requiere Python + `aiohttp`, solo escucha en `127.0.0.1` y lanza 1 proceso OS nuevo + archivo temporal por cada petición HTTP ([prog_b_run.txt](evidence/prog_b_run.txt)). |
| **8. Concurrencia y Asincronía (`std/algorithms`, `std/async`)** | **verificado** (acotado) | Útil | Útil | Crítica | Prescindible | Concurrencia determinista por lotes (`parallel_map`, `parallel_filter`, `parallel_reduce`, `http.batch_get`), `std/async.spawn` en hilos OS y corrutinas CPS `async fn`/`await` (**verificado**). Sin canales (`chan`), mutexes de usuario ni sockets asíncronos (deliberadamente fuera de alcance según `docs/KNOWN_LIMITS.md:179-190`). |
| **9. Testing (`std/test`, `wysna project test`)** | **parcial** | Crítica | Crítica | Crítica | Útil | `std/test` solo tiene **3 funciones**: `expect(cond, msg)`, `expect_equal_int(a, b, msg)` y `expect_equal_text(a, b, msg)` (`stdlib/test.wysna:1-20`), que terminan inmediatamente todo el proceso en el primer fallo (`exit(1)`). No hay comparación de `float`, `bool` o colecciones, ni ejecución de múltiples tests por archivo con reporte de fallos acumulados. |
| **10. Logging** | **ausente** | Útil | Útil | Crítica | Prescindible | Solo existen `print(...)` y `eprint(...)`. No hay niveles de log, formato estructurado JSONL ni timestamps integrados (cada script los construye a mano con `f"..."` y `std/json`). |
| **11. Depuración (Debugging)** | **parcial** | Útil | Útil | Útil | Útil | C99 `--profile=dev` emite `-O0 -g` y LLVM emite DWARF (`docs/LLVM_EXPERIMENTAL.md:75-84`). Sin embargo, como toda variable no escalar es un `WVal` NaN-boxed (`uint64_t bits`), al inspeccionar un `str`, `list` o `struct` en `lldb`/`gdb` solo se ve un entero hexadecimal de 64 bits (`0xfff6...`) sin pretty-printers de LLDB/GDB. |
| **12. Interop: C FFI, JS/DOM y Bases de Datos** | **parcial** | Útil | Crítica | Crítica | Crítica | **C FFI (`extern "C" fn`):** soporta solo escalares (`int`, `float`, `bool`, `str`) y punteros `opaque`; requiere escribir un adaptador `.c` manual para cualquier librería C como SQLite (`docs/FFI_VALIDATION.md:15`). **Bases de datos:** no hay módulo `std/sqlite` en la stdlib (cada ejemplo copia su propio `adapter.c`) (**`GAP-05`**). **JS/DOM:** sin acceso directo al DOM; Wasm exporta solo `pub fn` escalares/`str` infalibles. |
| **13. Distribución y Portabilidad** | **parcial** | Crítica | Crítica | Útil | Crítica | El Native SDK (`macOS arm64`, `Linux x64`, `Windows x64`) empaqueta `wysna-compiler` + `Zig 0.15.2` + `stdlib` sin Python (**verificado**). **Brechas:** 1) Instaladores sin firma digital ni notarización (`docs/SEED_DISTRIBUTION.md:117-118`); 2) los targets `browser-library` y `wasi-command` fallan si el usuario no instala Emscripten `5.0.1` o `wasi-sdk 34` por su cuenta ([build_web.json](evidence/prog_c_wasm/build_web.json)) (**`GAP-06`**). |
| **14. Calidad de errores en runtime** | **parcial** | Crítica | Crítica | Crítica | Útil | `W4001` (división por cero) y `W4002` (índice/clave fuera de rango) reportan `archivo:línea:columna` de la operación (`docs/KNOWN_LIMITS.md:127-137`). **Brecha:** No existe stack trace (traza de pila de llamadas) cuando ocurre un `panic` (`W4001`..`W4005`), y en `[W4005]` (violación de contrato `requires`/`ensures`) solo se reporta la ubicación sin mostrar los valores de los argumentos que violaron el contrato. |

---

## 2. Decisión Estratégica para Web (Con Evidencia de Fase 2)

El prompt exige decidir con evidencia entre tres opciones para la web:
1. *Escribir toda la app web en Wysna.*
2. *Usar Wysna para módulos de lógica compartida vía Wasm.*
3. *Interoperar con el ecosistema JS.*

### Decisión recomendada: **Opción 2 + 3 combinadas (Wysna para módulos puros de reglas de negocio y validación compartida CLI/Wasm, interoperando con un host JS/TS dueño del DOM, estado UI y red)** — descartando por completo la Opción 1.

**Evidencia que sostiene esta decisión:**
1. **Por qué se descarta escribir toda la app web en Wysna (Opción 1):** En el target `browser-library` (`src/tooling/TargetValidation.py` y `docs/PROJECTS.md:109-111`), el compilador **rechaza estáticamente** cualquier acceso a DOM, red (`std/http`), sistema de archivos, reloj, entorno, procesos, FFI y salida por consola. Construir bindings reactivos de DOM, router de SPA, bundling CSS y estado de navegador en Wysna requeriría recrear TypeScript + React/Vite desde cero sin ninguna ventaja técnica.
2. **Dónde sí aporta valor diferencial real un módulo Wasm de Wysna (Opción 2 + 3):** En el **Programa C de Fase 2** ([prog_c_wasm/src/library.wysna](evidence/prog_c_wasm/src/library.wysna)) y en [examples/shared-browser-app/](../../examples/shared-browser-app/README.md), compartir un módulo puro de cálculo y validación (precios, descuentos, reglas fiscales, validación de payloads con contratos `requires`/`ensures` y aritmética exacta de 64 bits) entre una herramienta CLI nativa y una aplicación web JS/TS evita duplicar reglas de negocio entre backend/CLI y frontend, y permite que `wysna graph --manifest ... --rel impact` detecte cuándo un cambio en una regla de negocio afecta tanto al binario nativo como al bundle web.
3. **Qué falta para que esa opción (Opción 2 + 3) sea viable sin fricción:**
   - Hoy compilar `browser-library` está bloqueado en cualquier máquina que no tenga instalado **Emscripten 5.0.1** externo (`[W5005] Emscripten 5.0.1 was not found` en [build_web.json](evidence/prog_c_wasm/build_web.json)), pese a que Wysna ya empaqueta **Zig 0.15.2** (que puede compilar C99 directamente a `wasm32-freestanding` / `wasm32-wasi`) (**`GAP-06`**).
   - Además, como la frontera ABI de `BrowserBindings.py` solo acepta escalares y `str`, la forma más limpia y económica de cruzar estructuras complejas y errores `T ! E` hacia JS no es exponer punteros `WVal` al navegador, sino usar las funciones `from_json_<Struct>` / `to_json_<Struct>` generadas por `wysna schema --emit-codecs` junto con definiciones `.d.ts` generadas desde el mismo esquema.

---

## 3. Conjunto Mínimo Viable (MVP) por Caso de Uso y Soluciones Existentes Recomendadas

Siguiendo la regla *"Si integrar una solución existente es técnica y económicamente superior, recomiéndala en lugar de construir un gestor de paquetes o framework propio"*:

| Caso de Uso | Conjunto Mínimo Viable (MVP) en Wysna | Qué integrar del ecosistema existente (NO construir desde cero) |
| :--- | :--- | :--- |
| **1. Scripting y Automatización CLI (Prioridad 1)** | • Captura de `stdout`/`stderr` en `std/process` (**`GAP-01`**).<br>• Listado de directorios (`fs.list_dir`) y directorios temporales en `std/fs` (**`GAP-02`**).<br>• `dict.remove(k)` y `dict.clear()` (**`GAP-03`**).<br>• Parser/serializador `std/csv` RFC 4180 (**`GAP-04`**).<br>• Módulo `std/sqlite` integrado sobre un único adaptador C mantenido (**`GAP-05`**). | • **Gestor de paquetes:** **NO construir un registro público propio tipo Crates.io/npm.** Mantener dependencias locales en `wysna.toml` (`path = "vendor/..."`) y usar **submódulos Git o `git clone --depth 1` con commit SHA fijado en `wysna.lock`**. Para librerías C del sistema, apoyarse en `pkg-config` / Zig C compiler. |
| **2. Backends HTTP Pequeños** | • Mantener Wysna enfocado en **workers de lógica de dominio, validadores y CLIs transaccionales** (`JSON → validación con contratos → SQLite → JSON`). | • **Servidor HTTP / TLS / Routing:** **NO construir un framework HTTP/TLS propio en Wysna.** Para exponer lógica Wysna por HTTP en producción, ejecutarla detrás de un proxy/gateway existente (**Caddy, Nginx, FastAPI/Node/Go wrapper, o CGI/FastCGI/Unix socket worker**) que ya resuelve TLS, HTTP/2, autenticación, rate-limiting y observabilidad. |
| **3. Lógica Wasm en Navegador** | • Compilar `browser-library` y `wasi-command` usando el **`zig cc -target wasm32-...`** ya empaquetado en el SDK (**`GAP-06`**) + wrappers JS/TS generados desde `wysna schema`. | • **UI / Bundling Web:** **NO construir framework UI ni bundler en Wysna.** Usar **Vite + TypeScript/React/Next.js** en el host e importar el módulo `.mjs`/`.wasm` generado por Wysna para ejecutar reglas puras compartidas. |

---

## 4. Backlog Categorizado de Brechas (`GAP-01` .. `GAP-06`)

### `GAP-01` · Captura de `stdout`, `stderr` y `exit_code` en `std/process.capture` (`ProcessOutput`)

- **Prioridad:** P1
- **Decisión:** INVEST
- **Evidencia:** [stdlib/process.wysna:41-61](../../stdlib/process.wysna#L41-L61), [docs/STDLIB.md:100-101,147-150](../STDLIB.md#L100-L101), y discrepancia verificada `D05` en [docs/FULL_LANGUAGE_CAPABILITIES_AND_DEBT_AUDIT.md:214](../FULL_LANGUAGE_CAPABILITIES_AND_DEBT_AUDIT.md#L214). Nivel: **verificado**.
- **Problema:** En `stdlib/process.wysna:43`, la única función existente es `pub fn run(executable: str, arguments: list[str], timeout_ms: int) -> int ! ProcessError`, la cual hereda `stdout` y `stderr` del proceso padre sin capturarlos. Esto hace imposible en un script de automatización Wysna ejecutar herramientas externas (`git rev-parse HEAD`, `uname -m`, `sqlite3`, `curl`, etc.) y leer su salida en una variable `str`, obligando a redirigir a archivos temporales a través de `/bin/sh -c` (lo cual contradice precisamente la garantía de seguridad de `std/process` de no invocar un shell implícito!).
- **Propuesta:** Añadir en `std/process` el tipo `pub struct ProcessOutput { status: int, stdout: str, stderr: str }` y la función `pub fn capture(executable: str, arguments: list[str], timeout_ms: int, max_output_bytes: int) -> ProcessOutput ! ProcessError` (con un techo acotado de `1..16,777,216` bytes por stream para preservar las garantías de memoria acotada del host en `runtime/wysna_host_process.h` y `src/runtime/HostAbi.py`).
  - **Antes (hoy imposible sin invocar `/bin/sh -c "cmd > /tmp/out"`):**
    ```wysna
    import "std/process" as proc
    # proc.run("git", ["rev-parse", "HEAD"], 5000) solo devuelve 0 (int), pierde el SHA
    ```
  - **Después:**
    ```wysna
    import "std/process" as proc
    let out = proc.capture("git", ["rev-parse", "HEAD"], 5000, 65536)?
    if out.status == 0 {
        print(out.stdout)
    }
    ```
- **Alternativas:**
  1. *No hacer nada:* mantener `std/process` incapaz de leer la salida de subprocesos y corregir `docs/FULL_LANGUAGE_CAPABILITIES_AND_DEBT_AUDIT.md:214`. Deja una brecha crítica para el foco de Scripting y Automatización (`01-FOCO.md`).
  2. *Resolverlo con lo que ya existe:* invocar `proc.run("/bin/sh", ["-c", "git rev-parse HEAD > /tmp/tmpfile"], 5000)` y luego `io.read_text("/tmp/tmpfile")`. No es portable a Windows (`cmd.exe` vs `/bin/sh`), reintroduce inyección de shell y deja archivos temporales si falla.
- **Impacto (1–5):** Usuario: **5** | Agentes: **5** | Reducción de complejidad: **3**.
- **Esfuerzo:** **M (2–3 días)**. Supuestos: añadir pipes acotados en `runtime/wysna_host_process.h` (POSIX `pipe`/`poll` y Win32 `CreatePipe`), `src/runtime/HostAbi.py` (`subprocess.run(..., capture_output=True)`), registrar el extern en `EffectModel.py` (`{'process.spawn', 'process.wait', 'memory.write'}`) y exponer `capture` en `stdlib/process.wysna`.
- **Riesgo:** **Medio.** Requiere drenar `stdout` y `stderr` concurrentemente o con `poll` para evitar deadlocks cuando el hijo llena el buffer del pipe del kernel (>64 KB) antes de terminar, y respetar el límite `max_output_bytes` y el timeout `timeout_ms`.
- **Compatibilidad y migración:** 100% aditivo; `process.run` mantiene su firma y comportamiento actuales.
- **Componentes afectados:** `stdlib/process.wysna`, `runtime/wysna_host_process.h`, `src/runtime/HostAbi.py`, `src/compiler/EffectModel.py`, `docs/STDLIB.md`, `tests/stdlib/`.
- **Depende de:** `AGT-03`.
- **Criterio de aceptación:** Un test en `tests/stdlib/` ejecuta `process.capture` sobre un comando hijo en los 4 motores (`interpreter`, `ast`, `hir`, `selfhost`), verifica captura exacta de `stdout`, `stderr` y `status != 0`, y comprueba que superar `max_output_bytes` o `timeout_ms` retorna `Err(ProcessError)` sin fugas de descriptores.
- **Requiere aprobación:** no (adición acotada a `std/process` que materializa lo ya afirmado en `docs/FULL_LANGUAGE_CAPABILITIES_AND_DEBT_AUDIT.md:214`).
- **Estado:** implementado.

---

### `GAP-02` · Completar operaciones básicas de directorio y rutas en `std/fs` y `std/path` (`fs.list_dir`, `fs.is_dir`, `fs.is_file`, `path.ext`)

- **Prioridad:** P1
- **Decisión:** INVEST
- **Evidencia:** [stdlib/fs.wysna:1-22](../../stdlib/fs.wysna#L1-L22), [stdlib/path.wysna:1-30](../../stdlib/path.wysna#L1-L30), y [runtime/wysna_host_project.h:55-120](../../runtime/wysna_host_project.h#L55-L120) (`wysna_host_project_entries` ya implementa listado de directorios en C y en `HostAbi.py` para `09_project_lock.wysna`, pero no está expuesto en `std/fs`!). Nivel: **verificado**.
- **Problema:** `stdlib/fs.wysna` tiene solo 22 líneas y 5 funciones (`exists`, `size`, `mkdir`, `remove`, `rename`). Un script de automatización en Wysna **no puede listar los archivos de un directorio** (`list_dir`), **no puede distinguir si una ruta es un archivo o un directorio** (`is_dir` / `is_file`), y `std/path` (`30` líneas: solo `join`, `basename`, `dirname`) no tiene `ext(path)` para filtrar archivos por extensión (`.json`, `.csv`). Lo más irónico es que el runtime C (`runtime/wysna_host_project.h`) y `HostAbi.py` **ya tienen implementado el listado determinista de directorios**, pero solo para uso privado de `selfhost/src/09_project_lock.wysna`!
- **Propuesta:** Exponer en `std/fs` las funciones `pub fn list_dir(path: str) -> list[str] ! FsError` (nombres de entradas directos ordenados lexicográficamente, excluyendo `.` y `..`), `pub fn is_dir(path: str) -> bool ! FsError` y `pub fn is_file(path: str) -> bool ! FsError` reutilizando la infraestructura de host existente, y añadir `pub fn ext(path: str) -> str` en `std/path` (puro Wysna, ~12 líneas).
- **Alternativas:**
  1. *No hacer nada:* un lenguaje orientado a *"automation and tools"* (`README.md:7`) que no puede listar los archivos `.json` o `.csv` de una carpeta de entrada no puede escribir el 50% de los scripts reales de procesamiento por lotes.
  2. *Resolverlo con lo que ya existe:* obligar al usuario a pasar cada archivo individualmente por `args()` desde bash (`wysna run script.wysna -- data/*.csv`). Funciona en shells Unix, pero falla en automatización autónoma o en Windows `cmd.exe`.
- **Impacto (1–5):** Usuario: **5** | Agentes: **4** | Reducción de complejidad: **3** (reutiliza `wysna_host_fs` / `wysna_host_project_entries` ya existentes en el runtime).
- **Esfuerzo:** **S (1 día)**. Supuestos: ~35 líneas en `stdlib/fs.wysna` y `stdlib/path.wysna`, ~45 líneas en `runtime/wysna_host.h` y `src/runtime/HostAbi.py`.
- **Riesgo:** **Bajo.**
- **Compatibilidad y migración:** 100% aditivo.
- **Componentes afectados:** `stdlib/fs.wysna`, `stdlib/path.wysna`, `runtime/wysna_host.h`, `src/runtime/HostAbi.py`, `src/compiler/EffectModel.py`, `docs/STDLIB.md`, `tests/stdlib/`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** Un test en `tests/stdlib/` crea un directorio temporal con dos archivos, verifica `fs.is_dir`, `fs.is_file`, `fs.list_dir` (ordenado determinísticamente) y `path.ext("report.json") == ".json"` en los 4 motores.
- **Requiere aprobación:** no.
- **Estado:** implementado.

---

### `GAP-03` · Implementar `dict.remove(key)` y `dict.clear()` en `dict[K, V]`

- **Prioridad:** P1
- **Decisión:** INVEST
- **Evidencia:** [WYSNA_SINTAXIS.md:674](../../WYSNA_SINTAXIS.md#L674) (documenta `dict_val.remove("a")`), [SPEC.md:760-766](../../SPEC.md#L760-L766), [runtime/wysna_runtime_access.h:292-312](../../runtime/wysna_runtime_access.h#L292-L312), y reproducción verificada en [repro_dict_remove.wysna](evidence/repro_dict_remove.wysna) y [repro_dict_remove.txt](evidence/repro_dict_remove.txt). Nivel: **verificado**.
- **Problema:** `WYSNA_SINTAXIS.md:674` ya documenta `dict_val.remove("a")`, pero el método no fue implementado ni en el typechecker (`AccessRules.py`, `08_semantics.wysna`) ni en el runtime (`wysna_runtime_access.h`, `Interpreter.py`). Hoy, `list[T]` tiene `.append()`, `.pop()` y `.clear()`, pero `dict[K, V]` **no tiene ninguna operación para eliminar una clave ni para vaciar el diccionario**. Si un programa necesita borrar una entrada de una caché, sesión o conjunto de claves vistas, tiene que construir un diccionario nuevo copiando todas las demás entradas en $O(N)$.
- **Propuesta:** Implementar en `dict[K, V]` (exigiendo receptor mutable `let mut d` igual que `list.pop` y `list.clear`, con desprendimiento CoW `wysna_dict_detach`):
  1. `d.remove(key: K) -> bool` (o `V?`): elimina la clave si existe, libera su clave/valor con `wysna_release`, reindexa el bucket en `WDictBuf` manteniendo el orden de inserción (tal como exige `SPEC.md:763-765`), y retorna `true` si la clave existía (`false` si no).
  2. `d.clear() -> unit`: libera todas las claves y valores en `WDictBuf` y reinicia `len = 0` (simétrico a `list.clear() -> unit`).
  - **Antes (falla hoy con `W3002` en `repro_dict_remove.wysna`):**
    ```wysna
    let mut dict_val = {"a": 1, "b": 2}
    dict_val.remove("a")
    ```
  - **Después:**
    ```wysna
    let mut dict_val = {"a": 1, "b": 2}
    let removed = dict_val.remove("a")  # true, dict_val queda {"b": 2}
    dict_val.clear()                    # {}
    ```
- **Alternativas:**
  1. *No hacer nada:* borrar `dict_val.remove("a")` de `WYSNA_SINTAXIS.md:674` y dejar a `dict[K, V]` sin eliminación de claves. Deja una limitación grave en una estructura de datos primaria del lenguaje.
  2. *Resolverlo con lo que ya existe:* copiar el diccionario entero excluyendo la clave en un bucle `for k, v in d`. Complejidad $O(N)$ y alto tráfico ARC/CoW por cada borrado.
- **Impacto (1–5):** Usuario: **5** | Agentes: **5** (cumple el contrato ya prometido en `WYSNA_SINTAXIS.md:674`) | Reducción de complejidad: **2**.
- **Esfuerzo:** **M (1–2 días)**. Supuestos: ~50 líneas en `runtime/wysna_runtime_access.h`, `src/checker/AccessRules.py`, `src/hir/HirBuilder.py`, `src/mir/Lowering.py`, `src/runtime/ExpressionRules.py` y `selfhost/src/08_semantics.wysna` + `08_mir_collections.wysna`.
- **Riesgo:** **Bajo-Medio.** Debe verificarse con `WYSNA_MEM_CHECK=1` y ASan/LSan que `wysna_dict_remove` y `wysna_dict_clear` hagan `wysna_dict_detach` cuando `refcount > 1` (preservando copias CoW anteriores intactas) y liberen `d->keys[i]` y `d->vals[i]` exactamente una vez.
- **Compatibilidad y migración:** 100% aditivo (permite además usar `dict_val.remove("a")` como sentencia descartando el `bool` retornado, por lo que el ejemplo de `WYSNA_SINTAXIS.md:674` compila tal cual).
- **Componentes afectados:** `src/checker/AccessRules.py`, `src/hir/HirBuilder.py`, `src/mir/Lowering.py`, `src/compiler/EffectModel.py`, `runtime/wysna_runtime_access.h`, `src/runtime/ExpressionRules.py`, `selfhost/src/08_semantics.wysna`, `selfhost/src/08_mir_collections.wysna`, `SPEC.md`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** [repro_dict_remove.wysna](evidence/repro_dict_remove.wysna) y un test de semántica CoW (`let a = {"x": "hello", "y": "world"}; let mut b = a; b.remove("x")`) pasan en los 4 motores bajo `WYSNA_MEM_CHECK=1` y ASan/UBSan con `0` fugas y `a["x"] == "hello"`.
- **Requiere aprobación:** sí (amplía los métodos intrínsecos de `dict[K, V]` en `SPEC.md` §11.2; ver `10-DECISIONES.md`).
- **Estado:** implementado.

---

### `GAP-04` · Añadir módulo `std/csv` en Wysna puro (RFC 4180 con campos entrecomillados, escapes y encabezados)

- **Prioridad:** P2
- **Decisión:** INVEST
- **Evidencia:** [docs/FFI_VALIDATION.md:9](../FFI_VALIDATION.md#L9) (*"It rejects quotes, empty fields, signs, Unicode digits, NUL, and extra columns; this is not a general CSV parser"*), y Fase 2 Programa A ([prog_a_cli.wysna:14-44](evidence/prog_a_cli.wysna#L14-L44), donde tuvimos que usar `text.split(line, ",")`, que se rompe si cualquier campo de texto contiene una coma entre comillas `"Quito, Ecuador"`). Nivel: **verificado**.
- **Problema:** Procesar archivos CSV y JSON es el caso de uso central de scripting de datos (`README.md:9`, `bench/ai_eval/README.md:24`). Mientras `std/json` cuenta con un módulo dedicado de `780` líneas en `stdlib/json.wysna`, **no existe `std/csv`**. Cada usuario o agente termina escribiendo `text.split(line, ",")`, lo cual falla silenciosamente con datos reales que incluyen campos entrecomillados (`"Yandún, Washington"`), comillas escapadas (`""`), campos vacíos o saltos de línea CRLF dentro de comillas.
- **Propuesta:** Añadir `stdlib/csv.wysna` implementado **100% en Wysna puro** (0 líneas de C FFI nuevo, 0 cambios en el compilador, 100% compatible con Native CLI, Selfhost, WASI y Browser Wasm), con límites acotados al estilo de `std/json`:
  - `pub struct CsvError { line: int, col: int, message: str }`
  - `pub fn parse_rows(source: str) -> list[list[str]] ! CsvError`
  - `pub fn parse_records(source: str) -> list[dict[str, str]] ! CsvError` (usando la primera fila como encabezados)
  - `pub fn stringify_rows(rows: list[list[str]]) -> str`
- **Alternativas:**
  1. *No hacer nada:* seguir usando `text.split(line, ",")` en cada script. Falla con cualquier CSV del mundo real que tenga comas o comillas en campos de texto.
  2. *Resolverlo con lo que ya existe:* copiar el parser manual de `examples/ffi/sqlite/import_csv.wysna` (que solo acepta dos enteros sin signo y rechaza comillas). No sirve para datos generales.
- **Impacto (1–5):** Usuario: **4** | Agentes: **5** (evita que los agentes escriban parsers CSV frágiles con `split(",")`) | Reducción de complejidad: **2** (vive 100% en `stdlib/csv.wysna` sin tocar el compilador ni el runtime C).
- **Esfuerzo:** **S (1 día)**. Supuestos: ~140 líneas de Wysna puro en `stdlib/csv.wysna` + tests en `tests/stdlib/` y documentación en `docs/STDLIB.md`.
- **Riesgo:** **Bajo** (no toca el compilador ni el runtime C).
- **Compatibilidad y migración:** 100% aditivo.
- **Componentes afectados:** `stdlib/csv.wysna`, `docs/STDLIB.md`, `tests/stdlib/`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** Un test en `tests/stdlib/` parsea y serializa filas con comas dentro de comillas (`"Quito, EC"`), comillas escapadas (`"\"Wawi\""`), campos vacíos y saltos `CRLF`/`LF` en los 4 motores.
- **Requiere aprobación:** no (módulo nuevo en `stdlib/` escrito en Wysna puro, sin cambios de lenguaje ni ABI).
- **Estado:** implementado.

---

### `GAP-05` · Consolidar los 3 adaptadores C de SQLite duplicados en ejemplos (`examples/*/ops_sqlite.c`, `adapter.c`) o documentar plantilla FFI única

- **Prioridad:** P2
- **Decisión:** MERGE
- **Evidencia:** Tres adaptadores SQLite en C mantenidos por separado dentro de `examples/`: [examples/ffi/sqlite/adapter.c](../../examples/ffi/sqlite/adapter.c), [examples/wysofia-ops/native/ops_sqlite.c](../../examples/wysofia-ops/native/ops_sqlite.c), y [examples/service/sqlite_adapter.c](../../examples/service/sqlite_adapter.c). Nivel: **verificado**.
- **Problema:** Los tres ejemplos insignia del repositorio (`ffi/sqlite`, `wysofia-ops`, `shared-browser-app`) persisten datos en SQLite, pero como no existe un paquete o adaptador SQLite reutilizable, cada ejemplo escribe y compila su propio archivo `.c` con sus propias declaraciones `extern "C" fn` hechas a medida para su tabla (`ops_sqlite.c`, `quote_sqlite.c`, `adapter.c`). Como advirtió el propio `docs/FFI_VALIDATION.md:15`: *"this adapter is product code that must be maintained"*. Cuando un usuario o agente quiere usar SQLite en un proyecto nuevo (como en el Programa B de la Fase 2 o en `TASK-29`/`TASK-30` de `bench/ai_eval/`, que siguen marcadas como `BLOCKED: SQLite driver` en `bench/ai_eval/README.md:26`!), no puede usar SQLite desde Wysna sin escribir un archivo `.c` manual.
- **Propuesta:** Crear un paquete local reutilizable o módulo opcional `std/sqlite` (o `examples/packages/sqlite/` consumible vía `[dependencies.sqlite]` en `wysna.toml` con un único adaptador C parametrizado `open`, `exec`, `prepare`, `bind_int`, `bind_text`, `step`, `column_int`, `column_text`, `finalize`, `close` envuelto en `std/resources`), eliminando la necesidad de escribir C ad-hoc por cada tabla SQLite y desbloqueando `TASK-29` y `TASK-30` de `bench/ai_eval/`.
- **Alternativas:**
  1. *No hacer nada:* mantener `TASK-29` y `TASK-30` bloqueadas en `bench/ai_eval/` y exigir que cada usuario escriba un archivo `.c` por cada tabla SQLite.
  2. *Resolverlo con lo que ya existe:* persistir datos como archivos JSON atómicos con `std/io` / `std/files.create_exclusive` + `std/fs.rename` (como hicimos en `prog_b_api.wysna`). Funciona muy bien para archivos de estado pequeños (<1 MB), pero no reemplaza consultas relacionales ni transacciones concurrentes.
- **Impacto (1–5):** Usuario: **4** | Agentes: **5** (desbloquea las 2 últimas tareas bloqueadas de `bench/ai_eval/`) | Reducción de complejidad: **4** (consolida 3 adaptadores `.c` ad-hoc en 1 interfaz reutilizable).
- **Esfuerzo:** **M (3–4 días)**. Supuestos: un adaptador C genérico de ~200 líneas + wrapper Wysna de ~150 líneas con `std/resources` y linkeo `--library sqlite3`.
- **Riesgo:** **Medio.** Debe mantener sentencias parametrizadas (`bind_int`/`bind_text`) para impedir SQL injection y liberar `sqlite3_stmt*` y `sqlite3*` de forma determinista al cerrar.
- **Compatibilidad y migración:** Los ejemplos existentes pueden migrar gradualmente al paquete compartido sin romper sus tests de validación actuales.
- **Componentes afectados:** `examples/`, `bench/ai_eval/tasks.py`, `docs/FFI_VALIDATION.md`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** Un proyecto Wysna con `wysna.toml` puede ejecutar DDL (`CREATE TABLE`), `INSERT` parametrizado en transacción (`BEGIN`/`COMMIT`/`ROLLBACK`) y `SELECT` tipado sin que el usuario escriba ninguna línea de C, y `TASK-29` / `TASK-30` de `bench/ai_eval/` pasan de `BLOCKED` a `SUPPORTED`.
- **Requiere aprobación:** no (si se entrega como paquete local reutilizable con `--library sqlite3`; sí si se incorpora al core offline de `stdlib/`).
- **Estado:** implementado.

---

### `GAP-06` · Soportar compilación Wasm (`wasi-command` y `browser-library`) con el toolchain Zig ya empaquetado y auto-seleccionar target único en `wysna project check`

- **Prioridad:** P2
- **Decisión:** SIMPLIFY
- **Evidencia:** Bloqueos reproducidos en Fase 2 Programa C: [check_v08_manifest.json](evidence/prog_c_wasm/check_v08_manifest.json) (`[W5002] unknown target 'app'` al correr `wysna project check` sobre un manifiesto con `[targets.web]`), [build_web.json](evidence/prog_c_wasm/build_web.json) (`[W5005] Emscripten 5.0.1 was not found`), [src/tooling/ProjectCLI.py:165-210](../../src/tooling/ProjectCLI.py#L165-L210), [src/compiler/WasiToolchain.py:1-94](../../src/compiler/WasiToolchain.py#L1-L94), [src/compiler/BrowserLibraryToolchain.py:1-182](../../src/compiler/BrowserLibraryToolchain.py#L1-L182). Nivel: **verificado**.
- **Problema:**
  1. **Defecto UX en `wysna project check` / `build` / `run`:** Si un manifiesto `wysna.toml` define un único target que no se llama `app` (por ejemplo `[targets.web]` en el Programa C de Fase 2), ejecutar `wysna project check --manifest wysna.toml` sin `--target web` falla con `[W5002] unknown target 'app'`, porque `ProjectCLI.py` asume `"app"` por defecto en lugar de elegir el único target declarado en `manifest.targets` (o verificar todos los targets declarados en `project check`).
  2. **Dependencia externa pesada para Wasm (`Emscripten 5.0.1` y `wasi-sdk 34`):** Wysna ya descarga y empaqueta **Zig 0.15.2** en sus distribuciones (`docs/NATIVE_SDK.md:3`, `docs/SEED_DISTRIBUTION.md:4`), y `zig cc -target wasm32-wasi` / `zig cc -target wasm32-freestanding` incluye `clang` + `wasm-ld` + `wasi-libc` listos para compilar C99 a `.wasm` sin instalar `wasi-sdk` ni `Emscripten`! Sin embargo, `WasiToolchain.py` exige `WASI_SDK_PATH` (`wasi-sdk 34`) y `BrowserLibraryToolchain.py` exige `emcc` (`Emscripten 5.0.1`), dejando ambos targets rotos fuera de CI ([build_web.json](evidence/prog_c_wasm/build_web.json)).
- **Propuesta:**
  1. **Quick-Win inmediato en `ProjectCLI.py` y `selfhost/src/09_project.wysna`:** Cuando `--target` no se especifica: en `project check`, verificar el target `"app"` si existe, o si solo hay 1 target declarado en `[targets.*]`, seleccionar ese target automáticamente (o verificar todos los targets declarados); en `project build`/`run`, si solo existe 1 target en `[targets.*]`, seleccionarlo automáticamente en vez de fallar con `unknown target 'app'`.
  2. Permitir que `WasiToolchain.py` (y, en una segunda etapa, `BrowserLibraryToolchain.py` con un cargador JS directo `WebAssembly.instantiate` sobre memoria lineal exportada) utilice `zig cc -target wasm32-wasi` cuando `WASI_SDK_PATH` no esté definido pero `zig` del SDK sí esté disponible.
- **Alternativas:**
  1. *No hacer nada:* mantener `wasi-command` y `browser-library` inaccesibles en máquinas de desarrollo sin `emsdk` / `wasi-sdk` manuales y seguir fallando con `unknown target 'app'` en proyectos web.
  2. *Resolverlo con lo que ya existe:* documentar que siempre hay que pasar `--target <name>` en `docs/PROJECTS.md:179` y exigir que el usuario instale Emscripten 5.0.1 por su cuenta.
- **Impacto (1–5):** Usuario: **4** | Agentes: **4** | Reducción de complejidad: **4** (reutiliza el Zig 0.15.2 ya empaquetado en vez de exigir 2 SDKs externos adicionales).
- **Esfuerzo:** **S (<0.5 días)** para la auto-selección de target en `ProjectCLI.py` + `09_project.wysna`; **L (1–2 semanas)** para migrar el toolchain Wasm de Emscripten/`wasi-sdk` a `zig cc`.
- **Riesgo:** **Bajo** para la auto-selección de target; **Medio** para el cargador JS sin Emscripten (debe preservar el contrato ABI de `createWysna()`, `exports`, `destroy()` y límites de memoria lineal de `BrowserBindings.py`).
- **Compatibilidad y migración:** Si `--target` se pasa explícitamente o si `[targets.app]` existe, el comportamiento es idéntico al actual.
- **Componentes afectados:** `src/tooling/ProjectCLI.py`, `selfhost/src/09_project.wysna`, `docs/PROJECTS.md`, `src/compiler/WasiToolchain.py`, `src/compiler/BrowserLibraryToolchain.py`.
- **Depende de:** Ninguno.
- **Criterio de aceptación:** `./wysna project check --manifest docs/evolution/evidence/prog_c_wasm/wysna.toml --output-format=json-v1` (sin `--target web`) selecciona automáticamente el único target `web` y devuelve `status: "ok"`.
- **Requiere aprobación:** sí (para reemplazar Emscripten por `zig cc` en `browser-library`; el fix de auto-selección de target único en `project check` no requiere aprobación; ver `10-DECISIONES.md`).
- **Estado:** implementado.

---

## 5. Hipótesis (`RESEARCH`)

### `GAP-R01` · Hipótesis: Añadir aserciones no terminantes y runner por funciones `test_*` en `std/test`

- **Observación:** Hoy `std/test` (`stdlib/test.wysna:1-20`) solo tiene 3 funciones (`expect`, `expect_equal_int`, `expect_equal_text`) que llaman a `exit(1)` en el primer fallo, deteniendo todo el archivo de test sin ejecutar las demás aserciones.
- **Cómo falsificarla:** En el uso de `wysna project test` sobre `examples/wysofia-ops` y nuevos proyectos de scripting, medir si añadir `expect_equal_float`, `expect_equal_bool` y un contador de fallos que reporte todas las aserciones fallidas antes de salir con `exit(1)` reduce las rondas de depuración de agentes en `bench/ai_eval/`.
