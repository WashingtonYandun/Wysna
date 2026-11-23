# Evolución del runtime para agentes: decisiones y secuencia ejecutable

Fecha: 2026-11-22. R0 integrado: `ba0953e` (PR #177, CI 39/39); base híbrida de PR #176.
v0.9.0 está publicada; R1 se entrega como candidato posterior, sin modificar ese tag. Esta hoja separa
implementación en curso de funcionalidades futuras. No requiere proveedores de IA.

## Decisiones

1. **Concurrencia estructurada sobre el runtime existente.** Se conservan `async`/
   `await`, `std/async` y los pools OS. El siguiente contrato será un grupo de tareas
   con límite de concurrencia, deadline, cancelación cooperativa y cierre que espera
   a sus hijos. No se introduce un segundo scheduler ni una sintaxis alternativa de
   funciones. Las tareas de CPU usan workers; el I/O mantiene corrutinas y polling.
2. **Transferencia segura antes de ampliar memoria compartida.** Los callbacks de
   workers tendrán una comprobación estructural de valores transferibles. Escalares,
   strings y compuestos de valores transferibles podrán pasar por snapshots; clases
   con identidad mutable, interfaces, `Weak` y handles FFI no se aceptarán inicialmente.
   Se comprobarán también efectos transitivos y acceso a globals: una captura
   inmutable no demuestra que una clase capturada sea segura entre hilos.
3. **ARC atómico y CoW se mantienen.** Los contadores atómicos existentes no
   convierten toda mutación en thread-safe. Se aíslan estados por hilo donde procede;
   cuotas y recursos del programa necesitan reglas compartidas explícitas. No se
   cambia el recolector ni se añade un borrow checker completo en esta iteración.
4. **C99 sigue como salida estable.** Primero Linux/macOS/Windows y contenedores
   Linux. WASI/navegador conservan Seed y su perfil de capacidades; el SDK nativo
   mantiene W2013 para esos destinos hasta tener paridad real. Android/iOS requieren
   perfiles, toolchains y pruebas propias: no se anuncian como soportados por emitir C.
5. **`int` conserva 64 bits de semántica.** El runtime actual ya usa `WVal` de 8
   bytes, enteros inmediatos de 48 bits con signo con fallback para Int64 y escalares sin boxing
   donde el lowering lo permite. Cambiar el ancho predeterminado rompería programas
   y resultados entre plataformas. Se introducirán anchos explícitos empezando por
   `u8`, `i32`, `u32` y `f32`, con conversiones comprobadas y sin estrechamiento implícito.
   `int32` de FFI no se presentará como si ya fuera un `i32` completo del lenguaje.
6. **Anotaciones propias como datos tipados.** Se reutilizan esquemas `struct`,
   aplicados con `@Schema(field: literal)` a funciones de nivel superior. Primera
   versión: `str`, `bool`, `int`; campos completos, sin genéricos en el esquema,
   expresiones, macros, hooks ni efectos de seguridad implícitos. `@AgentTool` no
   concede permisos y `@Sensitive` no cifra ni redacta por sí solo.

## R0 — Correcciones de base en esta rama

- Catálogo único de versión, comandos y guías `skills`, con salidas nativas generadas
  y verificación de frescura. El SDK ejecutable no carga Python para consultarlo.
- Anotaciones propias comprobadas contra tipos de `struct`; `api`, `inspect`, `schema`
  exponen `attributes: [{name, arguments}]`. Se conservan los dos atributos integrados.
- W2003 incluye `data.uncovered_pattern_families`, ordenadas de forma estable.
  Son familias que necesitan cobertura adicional, no testigos de valores mínimos.
  Las guardas siguen sin aportar cobertura incondicional.
- Ubicación y pila de diagnóstico nativas por hilo. Una regresión con barrera
  reprodujo la sobrescritura entre workers antes del cambio.
- Intrínsecos nativos de async/paralelismo identificados por declaración, aridad
  y ruta canónica de biblioteca, conservados al especializar genéricos. Se eliminan
  las sustituciones por número de línea y por mero nombre de archivo.
- Fuzzing multiarchivo ampliado con anotaciones, genéricos y patrones struct/or/rango.

Estas implementaciones solo se consideran cerradas al aprobar el bootstrap fijo,
los cuatro motores, los sanitizadores aplicables y el CI del SHA publicado.

## R1 — Contrato seguro de workers

Implementación candidata y reglas: [WORKER_SAFETY](../WORKER_SAFETY.md).
Incluye snapshots completos y privados, comprobaciones W2014/W2015 en Seed y
Selfhost, cierre fatal sin bloqueos y correcciones de contratos en los oráculos.
La evidencia se registra para el commit verificado; no se atribuye a v0.9.0.

Orden de trabajo:

1. Inventariar los estados compartidos del runtime y clasificar cada uno: TLS,
   inmutable, atómico, protegido por lock o confinado al scheduler. Incluir Host ABI,
   caches de strings, ciclo de vida de tareas y recolección de ciclos.
2. Implementar la comprobación estructural de transferencia y de efectos de callbacks
   en Seed y Selfhost. Prohibir globals no capturados (incluidos los inmutables), FFI desconocida y mutación de objetos
   compartidos desde workers; diagnósticos JSON con captura/efecto y ubicación causal.
3. Ejecutar stress de snapshots CoW, callbacks genéricos, workers anidados, fallos y
   shutdown; TSan en Linux/macOS donde el toolchain lo permita, ASan/UBSan y memcheck.
4. Exponer en `capabilities` los límites realmente comprobados por perfil.

Cierre: ningún caso rechazado alcanza la ejecución; resultados equivalentes entre
motores; sin carreras reportadas en la matriz anunciada. Una ejecución sin errores
no constituye prueba universal de ausencia de carreras. No se habilita un API de
objetos mutables compartidos como atajo.

## R2 — Grupos, backpressure y cancelación

Primera entrega. Usar un grupo para ejecutar CPU transferible con límite de
trabajo en vuelo y resultados por índice. Definir estados cola, en ejecución y
terminal. Un caso de prueba con barrera debe verificar el límite real y que,
tras cerrar, cada tarea tenga un estado terminal y cada handle se libere.
Extender el scheduler de I/O solo después de tener esa prueba y un reloj de
prueba para reproducir la carrera entre cancelación y finalización.

- Diseñar `TaskGroup[T]` como biblioteca sobre el scheduler, sin una nueva palabra
  clave. Entrada explícita: `max_concurrency`, `deadline_ms`, política ante primer error.
- Mantener orden de resultados por entrada; reportar estados por tarea y errores
  tipados (deadline, cancelación, fallo), con IDs para correlación de agentes.
- La cancelación es cooperativa en puntos de suspensión/polling; no termina un hilo
  arbitrariamente ni deja un heap parcialmente mutado. FFI bloqueante no cooperativa
  necesita un proceso aislado o una limitación explícita de deadline.
- El grupo siempre libera handles y espera sus hijos; el límite debe controlar
  trabajo en vuelo, no limitar únicamente el tamaño de un lote de entrada.

Cierre: pruebas deterministas de cancelación antes/después de start, carrera entre
finalización y cancelación, errores múltiples y cierre; sin hijos vivos ni fugas;
workers activos nunca exceden el límite configurado. Benchmark CPU y de I/O por
separado, con hardware, workers, throughput, p95, pico RSS y tiempo de shutdown.

## R3 — Tipos de ancho explícito y densidad

Primera entrega. Especificar `u8` y las conversiones de frontera. Probar
`0`, `255`, `-1`, `256`, round-trip de bytes y tamaño real de almacenamiento.
Completar `list[u8]` antes de ampliar la misma matriz a `i32`, `u32` y `f32`.

1. Especificar literales contextuales, overflow, promociones y conversiones. Mantener
   `int`/`float` simples para agentes; exigir conversión al mezclar signedness/ancho.
2. Implementar `u8`, `i32`, `u32`, `f32` en checker, intérprete, HIR/MIR, ambos emisores,
   Selfhost, FFI, schema, formatter y diagnóstico. Cada ancho debe ser un tipo real.
3. Representar `list[u8]` como bytes compactos y medir después `list[i32]`/`list[f32]`.
   No cambiar el ABI dinámico de 8 bytes solo para añadir nombres de tipos.
4. Probar límites mínimo/máximo, overflow, casts, NaN/rounding, serialización y ABI en
   los tres OS; golden tests y fuzz diferencial con los mismos valores frontera.

Cierre: almacenamiento de payload de un millón de `u8` cercano a 1 MB y de `i32` a
4 MB, más encabezado/capacidad documentados; medición de RSS por separado. Ningún cast
fuera de rango trunca silenciosamente. La reducción de memoria debe aparecer en
datos medidos, no inferirse de `sizeof(int32_t)`.

## R4 — Portabilidad y compilación

La publicación 0.9.0 ya cubre procedencia, checksums y aceptación instalada
sin herramientas del host en macOS, Linux y Windows. El siguiente gate de
portabilidad es ejecutar el SDK Linux en un contenedor sin privilegios,
con filesystem de instalación de solo lectura, y comprobar `check`, `build`
y `run` con los recursos declarados por el proyecto.

- Conservar el build incremental por unidad y la trazabilidad de entradas/versiones.
  Medir compilación fría, caliente, tamaño y ejecución sobre el mismo corpus antes
  de alterar backend o caché.
- Validar SDK instalado sin Python ni compilador del host en Linux/macOS/Windows;
  fijar checksums/procedencia y añadir smoke en contenedor Linux sin privilegios.
- Hacer explícito para cada target qué soporta: procesos, red, filesystem, workers,
  sandbox y librerías externas. Un artefacto cross-compilado sin ejecución es solo
  evidencia de compilación.
- Abrir Android/iOS únicamente tras elegir artefacto, ABI y lifecycle host; primero
  comprobar un pequeño consumidor embebido en cada plataforma real.

## Orden de entrega

R0 → R1 → R2. R3 comienza con especificación y mediciones, después de cerrar R1;
R4 acompaña cada entrega. La publicación v0.9 no absorbe promesas de R1–R4 todavía
sin implementación: su alcance queda fijado por código, migración y pruebas reales.
Las evaluaciones con modelos continúan aplazadas; este trabajo se verifica sin red
hacia proveedores de IA.
