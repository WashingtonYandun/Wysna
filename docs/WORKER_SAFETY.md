# Contrato de seguridad de workers

Estado: candidato posterior a v0.9.0. La publicación v0.9.0 no contiene estas
correcciones; el tag y sus artefactos permanecen intactos. El cierre del candidato
requiere bootstrap fijo, conformidad diferencial y CI del commit entregado.

## Frontera comprobada

`std/algorithms.parallel_map`, `parallel_batch`, `parallel_filter`, el mapper de
`parallel_reduce` y `std/async.spawn` aceptan callbacks cerrados y comprobables:
funciones nombradas, lambdas o alias inmutables. Se inspeccionan sus parámetros,
resultado, capturas, cuerpo, contratos y helpers transitivos en Seed y Selfhost.
También se comprueban listas vacías y el fallback a un solo worker.

W2014 (`data.kind = worker_transfer`) identifica el `path` y `value_type` que no
pueden cruzar la frontera. Se admiten int/float/bool/str/unit/none y compuestos de
valores transferibles: list, dict, tuple, struct, enum, opcionales y resultados
incluidos sus errores. Se rechazan clases, interfaces, Weak, handles opacos y
funciones almacenadas como datos, incluso dentro de un compuesto. Un callback
no puede crear clases locales como alternativa: el grafo del recolector de ciclos
permanece confinado al propietario principal.

W2015 (`data.kind = worker_effect`) describe el `reason` de rechazo y la frontera
mediante `boundary_file`, `boundary_line`, `boundary_col`. Se rechazan lecturas de
globals no capturados, efectos del host, FFI, código async, acceso al scheduler,
workers anidados, callbacks mutables o no resueltos y referencias indirectas a las
propias funciones de despacho. Las capturas explícitas de valores sí se copian.
Una función auxiliar conserva su propio alcance: no puede apropiarse de las
capturas de quien la llama para ocultar una lectura global.

Este primer contrato no tiene polimorfismo de efectos: un wrapper que recibe un
callback desconocido y lo envía a un worker se rechaza. Los builtins usados como
callback requieren un adaptador lambda comprobable. No hay atributos que omitan
estas comprobaciones ni macros que concedan permisos.

## Propiedad y costes

Antes de iniciar el trabajo se copian los grafos completos de entrada y entorno
por worker. Un memo conserva los alias internos del mismo grafo; los buffers ARC,
CoW y strings no quedan compartidos entre propietarios. Los resultados se copian
antes de publicarlos al consumidor. Dentro del worker se mantiene CoW normal.

Los snapshots tienen un límite de profundidad 256 y rechazan ciclos y categorías
no transferibles también en runtime (W4003), como defensa para consumidores C.
Los literales UTF-8 de un worker usan almacenamiento propio: sus índices Unicode
perezosos no mutan el descriptor estático del propietario principal. Se conservan
longitudes explícitas y bytes NUL embebidos.

Transferir cuesta tiempo y memoria proporcionales al grafo recorrido. No se
promete captura de coste constante, eliminación de toda asignación ni destrucción
con latencia acotada. `WVal` conserva ABI 3 y 8 bytes: int tiene semántica Int64,
rango inmediato de 48 bits con signo y representación indirecta fuera de él.

## Inventario de estado compartido

- **Diagnósticos, pila y profundidad de worker:** TLS nativo; cada intérprete hijo
  tiene TLS y contexto de ejecución propios. Un fallo fatal concurrente se imprime
  completo una sola vez mediante el lock de diagnóstico.
- **Buffers de valores y caches Unicode:** confinados al propietario mediante los
  snapshots. Los metadatos de tipos, nombres estáticos y declaraciones son
  inmutables. Los hashes y contadores instrumentados usan operaciones atómicas;
  la telemetría CoW del intérprete usa lock.
- **Cuotas y contabilidad:** límites inicializados antes de lanzar hilos,
  contadores nativos atómicos y lock del propietario en el intérprete. Las copias
  de frontera consumen presupuesto de asignación instrumentada.
- **Tareas spawn:** registro y publicación protegidos por lock; la función se
  copia antes del lanzamiento y el resultado antes de marcar la tarea completa.
  El contador de workers activos es atómico. Los outputs de map se escriben en
  posiciones disjuntas y se leen después de los joins.
- **Ciclos de clases y Weak:** confinados al propietario principal. El recolector
  no se ejecuta desde un worker; las reglas estáticas impiden crear ese grafo allí.
- **Scheduler, HTTP, filesystem, reloj, procesos, FFI y trazas de efectos:** no se
  exponen a callbacks de CPU. Siguen sus rutas cooperativas y políticas de host;
  no se afirma que sus registros completos sean seguros para acceso arbitrario
  concurrente. Las identidades de intrínsecos dependen de la declaración, aridad y
  ruta canónica de biblioteca, no del nombre de un archivo de usuario.

## Orden y fallos

Map/batch/filter conservan el orden de entrada. Reduce mapea en paralelo y aplica
el combiner mediante un fold izquierdo secuencial; no reasocia operaciones de
coma flotante. Esto no promete un orden de efectos: los efectos de host no se
admiten dentro de los callbacks de CPU.

Un `Err` tipado es un resultado normal y transferible si lo es su contenido.
`int("hola")`, `ord("")` y `chr(-1)` producen W4003; `pop()` sobre una lista
vacía produce W4002. División por cero, índices inválidos, cuotas y contratos incumplidos son traps
W4001–W4005, no valores recuperables mediante `else err`. Con workers activos,
C99 imprime el diagnóstico y termina el proceso sin ejecutar joins de salida que
podrían bloquearse. El intérprete propaga el primer fallo fatal y detiene a los
workers en puntos de evaluación. No ejecuta cleanup de aplicación tras un trap
ni promete interrumpir una llamada FFI bloqueante.

Los contratos de campos se validan en alcance de declaración, incluyendo
genéricos, imports y actualización por spread. Los campos se evalúan una sola
vez. El candidato corrige su omisión en Selfhost y el oráculo AST de v0.9.0,
además de la generación inválida de `ensures` sobre un retorno implícito nativo.

## Aislamiento y trabajo pendiente

Estos controles cubren programas comprobados por el compilador y las rutas
instrumentadas. El intercambio MIR es un artefacto interno de confianza; debe
regenerarse al cambiar el compilador y su verificador no sustituye el chequeo de
fuente ni un sandbox. C manual, FFI y binarios nativos arbitrarios no quedan aislados
por cuotas, replay, ARC o W2014/W2015. Para ejecutar código no confiable se necesita
un proceso y una política de aislamiento del sistema operativo apropiados.

Los [grupos de CPU](TASK_GROUPS.md) usan esta frontera de transferencia y
añaden límite de concurrencia, resultados por índice y cancelación cooperativa.
Los grupos GET usan el scheduler HTTP y cancelan sus transferencias al cerrar.
Timers y corrutinas conservan la interfaz `Task`. Los anchos numéricos nuevos y
perfiles móviles siguen en R3/R4.

## Verificación reproducible

```sh
python -m pytest -q tests/unit/test_worker_safety.py tests/unit/test_worker_transfer.py tests/unit/test_security_contracts.py
WYSNA_SANITIZE=1 python -m pytest -q tests/unit/test_worker_transfer.py
clang -std=c99 -O1 -g -pthread -I. -fsanitize=thread tests/runtime/worker_transfer.c -o /tmp/worker-transfer-tsan
WYSNA_MEM_CHECK=1 /tmp/worker-transfer-tsan
python tools/ci_conformance.py --profile=release
```

El fixture C comprueba copias, alias, crecimiento del memo, UTF-8/NUL, fallbacks,
rechazos anidados y traps simultáneos. Las pruebas de lenguaje ejecutan los
cuatro motores y fijan códigos W4001/W4005. TSan cubre el fixture en toolchains
compatibles; un resultado limpio no demuestra ausencia universal de carreras.
