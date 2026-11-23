# Siguiente ciclo: tipos, errores y garantías del lenguaje

## Arquitectura híbrida integrada y siguiente ciclo

[PR #176](https://github.com/WashingtonYandun/Wysna/pull/176) está integrado en
`main` y `develop` en `4ad7c5e`. El [CI del merge](https://github.com/WashingtonYandun/Wysna/actions/runs/45073946830)
terminó correctamente. El siguiente trabajo vive en `feat/v0.9-agent-contracts-annotations-patterns`;
su secuencia está en [Runtime agent-first](13-RUNTIME-AGENT-FIRST.md) y [ADR-06](ADR-06-metadatos-tipados-y-runtime-agentes.md).
La decisión vigente es [ADR-05](ADR-05-arquitectura-hibrida-tooling-selfhost.md):
el lenguaje conserva el oráculo diferencial de cuatro motores y el tooling se
implementa en Selfhost; `packages` es exclusivamente nativo.

Los cuatro bloques tienen implementación y regresiones: build incremental por
unidad y delegación mínima; `FUZZ-A01`; sandbox/lints, paquetes USTAR y `OPS-A01`;
y limpieza 1A, closures por snapshot inmutable, patrones compuestos de `match`
y atributos estáticos cerrados. El routing de proyectos WASI se conserva en Seed.

Sobre `ed05906` pasaron **212/212 casos en cada uno de los cuatro motores**,
sin exclusiones, con fuentes intactas y bootstrap de tres etapas (`C0 == C1 == C2`).
Las regresiones del compilador (`0cb0787`) pasaron 64 pruebas y 143 subcasos; fuzzing multiarchivo
y limpieza sintáctica, 10 pruebas; la muestra adicional de atributos con valores
administrados, closures y async pasó los cuatro motores con ASan/UBSan en los
backends nativos. La aceptación macOS del SDK instalado, construido en `1b4f1ac`, pasó 93 comprobaciones,
incluido empaquetar, fijar, restaurar sin archivo original y ejecutar un consumidor
con lock congelado, sin Python ni toolchain del host para los procesos del SDK.
La suite general sobre `1b4f1ac` pasó 878/878 controles; después, la ampliación de W6005 para `unit ! E` y `else err` pasó sus siete pruebas específicas. Estos conteos corresponden a suites distintas y no se suman como pruebas únicas.
La [matriz](../ENGINE_PARITY_MATRIX.md) registra el digest medido; el CI del SHA
final sigue siendo una puerta de integración independiente. No se ha publicado
una nueva release ni realizado una campaña con modelos.

> Las secciones siguientes conservan los hitos y la evidencia histórica con sus revisiones.

Revisión del 2026-11-21 sobre main `fcdd1af`. Los PRs del evaluador se conservan separados. Este trabajo no usa modelos ni servicios externos de IA.

Actualización del mismo día: `LANG-07` está integrado mediante PR #171, junto con los PRs #169/#170. El PR #172 completó los tres contratos host y las combinaciones de tipos en `c37fa4e`; cada PR pasó 39/39 checks. La [CI del commit final](https://github.com/WashingtonYandun/Wysna/actions/runs/44971203954) y el [publicador](https://github.com/WashingtonYandun/Wysna/actions/runs/44973834360) terminaron correctamente. [v0.8.0](https://github.com/WashingtonYandun/Wysna/releases/tag/v0.8.0) está publicada: 19 activos contrastados mediante checksums y procedencia, aceptación SDK Linux/macOS/Windows de 78/78/71 comprobaciones y repetición de 78 comprobaciones sobre el SDK macOS descargado de la release.

## Decisión inmediata: cerrar la paridad de errores genéricos

`LANG-01` a `LANG-06` ya están marcados como implementados en el backlog original. Repetir sus propuestas como si estuvieran pendientes sería incorrecto. Al contrastar `KNOWN_LIMITS.md`, el compilador nativo y la regresión existente de errores genéricos apareció una brecha real:

- Seed acepta una función que devuelve `int ! BoxErr[int] | GenErr[str]` y permite recuperar cada payload con `match`.
- El SDK nativo de la línea base `fcdd1af` rechaza la misma función con `W2001: generic nominal types cannot be error union alternatives`.
- La prueba existente omitía Selfhost en ese caso, de modo que no cubría la discrepancia.

Se implementa `LANG-07`: aceptar los nominales genéricos concretos en uniones de error, resolver sus argumentos en patrones y conservar identidades distintas para `Failure[int]` y `Failure[str]`. También se normaliza la unión después de sustituir parámetros. Si `Failure[T] | Failure[int]` se especializa con `T=int`, el resultado se rechaza con `W2001`, en lugar de llegar a MIR y terminar con `W9999`.

### Resultado práctico

Un módulo puede publicar errores reutilizables `Failure[T]`, propagarlos con `?` y recuperarlos de forma exhaustiva en el SDK nativo. No necesita duplicar tipos de error por cada tipo de payload ni cambiar la firma para usar el compilador nativo.

### Criterio de cierre

- Ejecutar una misma muestra con structs, enums y clases genéricas en los cuatro motores, incluyendo payloads con listas y funciones genéricas que propagan errores.
- Conservar la separación entre especializaciones del mismo nominal, también en variantes enum sin payload.
- Mantener rechazos correctos de handlers incompletos, alternativas que no pertenecen a la unión y propagación hacia un canal incompatible.
- Validar imports entre módulos, balance de memoria, ASan/UBSan en POSIX y bootstrap de tres etapas con punto fijo.
- Incorporar las pruebas específicas al gate nativo de CI en Windows, macOS y Linux.

## Cierre verificable para v0.8.0

1. **LANG-07 integrado:** errores genéricos concretos, identidad por especialización y normalización tras sustitución, con CI 39/39 y punto fijo de bootstrap.
2. **SIMP-05, catálogo host:** se incorporan `wysna_host_active_bytes`, `wysna_host_active_handles` y `wysna_host_fs_canonical`. El gate exige igualdad entre los 49 símbolos ABI y sus 49 entradas de efectos y compara las aridades Python/C. Se verifican los efectos de filesystem, el préstamo de buffers, su cierre y la conservación del rechazo a FFI de usuario no certificada. El contrato y sus límites están en [HOST_ABI_CONTRACTS.md](../HOST_ABI_CONTRACTS.md). La mención anterior a `SIMP-R01` era incorrecta: esa hipótesis corresponde a compartir herramientas Agent-First, no a estos contratos.
3. **Combinaciones del lenguaje:** reexportación de errores nominales genéricos entre módulos, métodos genéricos sobre clases parametrizadas y propagación mediante `await`/`?`. Los positivos se ejecutan en los cuatro motores; los negativos comparan `W2001`, `W2003` y `W2006` entre Seed y Selfhost. Se conservan los checks de memoria y ASan/UBSan en POSIX.
4. **SDK y publicación:** los cuatro programas de aceptación (incluidos los buffers host) se copian al consumidor instalado y se ejecutan con PATH aislado. La publicación rechaza un paquete cuyo reporte no contiene esas comprobaciones. Se etiqueta v0.8.0 únicamente después de aprobar la CI del commit exacto de main; los archivos publicados deben coincidir con los hashes de los artefactos aceptados.

No se añade gramática. El cierre de estos casos no demuestra paridad universal; cada nueva divergencia necesita su propio repro y regresión.

## Decisiones de arquitectura

- Mantener C99 como backend estable y el runtime `WVal` actual durante este bloque.
- Conservar ambas formas de importación y los canales `T ! E` / `?` / `else err`.
- No convertir el resultado del microbenchmark de `PERF-R02` en una migración global de structs: sus números pertenecen a un caso especializado en C. Reabrir esa implementación después de medir una carga Wysna representativa, con al menos 20% de mejora y paridad de igualdad, formato y ownership.
- No usar la ausencia de acceso a modelos como bloqueo para corregir semántica, inferencia y paridad del compilador.

La evidencia inicial del cambio de tipos se conserva en [LANG-07](evidence/lang07-generic-error-unions.json). Los gates posteriores están en `tests/unit/test_host_contracts.py`, `tests/unit/test_language_combinations.py` y `tools/validate_native_sdk.py`. El mantenedor autorizó integrar el cierre y publicar v0.8.0 tras su validación; las campañas de IA siguen aplazadas.

## Mantenimiento inmediato: reparar la calificación LLVM

La [calificación extendida del tag v0.8.0](https://github.com/WashingtonYandun/Wysna/actions/runs/44973834359) detectó una regresión de diagnóstico: `panic_division.wysna` imprime `at divide` y `at main` en C99, mientras LLVM omite ambos frames. Falla en los tres perfiles de Linux y macOS. La CI de integración y la publicación del backend estable C99 sí aprobaron sus controles; esos resultados no sustituyen la calificación LLVM.

La reparación se integró mediante [PR #173](https://github.com/WashingtonYandun/Wysna/pull/173) en `c49e3d5`, con CI del candidato 39/39 y calificación completa 13/13. Incluye:

1. Registrar los frames síncronos mediante el puente LLVM/runtime, retirar cada frame al retornar y presentar nombres importados igual que C99.
2. Comparar íntegramente estado, stdout y stderr en regresiones de pánicos, imports y retornos escalares, gestionados y unitarios para `dev`, `release` y `size`.
3. Declarar las rutas de importación del benchmark y ejecutar su worker desde un directorio temporal con Python aislado, sin depender de `PYTHONPATH`. Esto protege el fallo de importación observado en [runs anteriores](https://github.com/WashingtonYandun/Wysna/actions/runs/44351776811).
4. Aprobar tanto la CI del PR como el workflow completo `llvm-qualification.yml` sobre el mismo candidato: corpus normal y sanitizado, SDK instalado y las seis comparaciones de plataforma/perfil.

El criterio de cierre es el resultado real de esos workflows. No se modifica el tag publicado, no se reduce el corpus y no se cambia el protocolo de medición ni sus presupuestos. LLVM conserva su estado experimental congelado.

Validación local del candidato: 34 pruebas de backend/evidencia verificadas (la prueba de breakpoints se repitió fuera del sandbox para permitir LLDB) y 459/459 filas del corpus LLVM normal en 462.83 s, sin casos omitidos y con fuentes intactas durante la medición. En CI aprobaron 459/459 filas por ejecución normal/sanitizada de ambos hosts y por calificación de ambos SDK; seis comparaciones contienen 12 cargas cada una y muestras frías/calientes/runtime 5/5/15 por motor. Es evidencia del candidato `6289b5c`, no del tag anterior. El run del SHA final `c49e3d5` aprobó 13/13; sus seis reportes conservan 146 violaciones de presupuesto y fuentes intactas. Es [44980185856](https://github.com/WashingtonYandun/Wysna/actions/runs/44980185856).

## Ciclo aprobado: proyectos para agentes de IA

Los agentes son el usuario principal. Las mejoras deben permitir descubrir capacidades, seleccionar trabajo, verificar entradas y reparar fallos sin interpretar texto de terminal ni responder preguntas interactivas. La evaluación de calidad con modelos continúa aplazada; la verificación de protocolos utiliza pruebas locales y CI.

1. **DOC-A01 — Cierre documental.** Referencias portables, separación explícita de línea base y estado vigente, identificación correcta de investigaciones y evidencia con su commit. Este cambio cierra la coherencia documental; no convierte mediciones aplazadas en resultados.
2. **TEST-A01 — Pruebas de proyecto.** Ampliar `project test` con listado/descubrimiento determinista, salida `json-v1`, datos de fallo y streams acotados por caso, fixtures temporales y JUnit. Conservar filtro, timeout y el rechazo de suites vacías. Cierre: paridad Seed/SDK nativo en los tres sistemas, sin Python para procesos del SDK; un agente puede listar, ejecutar una selección y localizar el fallo desde el JSON.
3. **PKG-A01 — Dependencias reproducibles.** Extender la base local con adquisición explícita, caché verificada y empaquetado. Empezar con fuentes fijadas y contenido verificable; las operaciones de construcción congelada permanecen offline. No ejecutar hooks de instalación. Cierre: un proyecto con dependencias se restaura y prueba desde un directorio limpio, el lock detecta alteraciones y todos los errores tienen clasificación estable en JSON. El registro público es una etapa posterior.
4. **FUZZ-A01 — Combinaciones y compatibilidad.** Ampliar el generador existente con proyectos multiarchivo, genéricos, errores, closures y async dentro del perfil común. Conservar semillas y reducción de casos; agregar proyectos de referencia de v0.8. Cierre: divergencias reproducibles y minimizadas, rechazos esperados sin `W9999` y contratos CLI/JSON compatibles.
5. **OPS-A01 — Integración real.** Usar dependencias y pruebas de proyecto en `wysofia-ops`, con escritura segura y rutas de recuperación verificadas. Cierre: flujo completo desde SDK instalado, lock congelado, datos válidos/inválidos y fallos de escritura simulados; el agente obtiene artefactos y diagnósticos verificables.

TEST-A01, PKG-A01, FUZZ-A01 y OPS-A01 están aprobados para implementación; todavía no se presentan como terminados. Cada bloque requiere su propio código, regresiones y evidencia. Se conserva C99 como backend estable, LLVM congelado y el límite explícito entre Seed y el perfil nativo.


## TEST-A01: candidato implementado y validado localmente

Se implementaron planes `wysna.project-test-plan/1`, IDs exactos, descubrimiento
optativo, precondición de hash, JSON/JUnit, diagnósticos por caso, captura de 1 MiB
por stream, fixtures y cancelación con reporte parcial en Seed y SDK nativo.
`std/test` añade igualdad genérica, tolerancia flotante, opcionales y fixtures.
La guía instalada `skills project-tests` expone el protocolo al agente.

Validación local: 108 pruebas y 18 subpruebas de la suite ampliada; 37 pruebas y
14 subpruebas del grupo final de contratos. El SDK macOS reconstruido con Zig
fijado pasó 82 comprobaciones instalado, con PATH aislado y sin Python para sus
procesos hijos. Es un build del árbol de trabajo, no una reedición de v0.8.0.
La paridad remota en los tres sistemas sigue pendiente de CI. La [evidencia de
entradas y comprobaciones](evidence/test-a01-agent-project-tests.json) distingue
este candidato de la release publicada.
