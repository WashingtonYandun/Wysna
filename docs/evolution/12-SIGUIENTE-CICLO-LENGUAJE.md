# Siguiente ciclo: tipos, errores y garantías del lenguaje

Revisión del 2026-11-21 sobre main `fcdd1af`. Los PRs del evaluador se conservan separados. Este trabajo no usa modelos ni servicios externos de IA.

## Decisión inmediata: cerrar la paridad de errores genéricos

`LANG-01` a `LANG-06` ya están marcados como implementados en el backlog original. Repetir sus propuestas como si estuvieran pendientes sería incorrecto. Al contrastar `KNOWN_LIMITS.md`, el compilador nativo y la regresión existente de errores genéricos apareció una brecha real:

- Seed acepta una función que devuelve `int ! BoxErr[int] | GenErr[str]` y permite recuperar cada payload con `match`.
- El SDK nativo de main rechaza la misma función con `W2001: generic nominal types cannot be error union alternatives`.
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

## Orden de trabajo posterior

| Orden | Trabajo concreto | Motivo | Criterio de salida |
| --- | --- | --- | --- |
| 1 | Integrar `LANG-07` tras revisar su evidencia y CI | Es una diferencia de semántica reproducida entre implementaciones | Positivos y negativos coinciden en diagnósticos y salida; no hay fallos de memoria |
| 2 | Resolver las entradas host ausentes y llevar efectos/ownership FFI a un catálogo verificable (`SIMP-R01`) | El código vigente sigue sin registrar `wysna_host_active_bytes`, `wysna_host_active_handles` y `wysna_host_fs_canonical` en `HOST_EFFECTS` | Cobertura de todos los símbolos host, firmas ABI coherentes y política `--allow/--deny` que conserve efectos reales; PR separado |
| 3 | Ampliar la matriz de combinaciones de tipos, módulos y canales con programas de aplicaciones | La paridad de un conjunto de fixtures no demuestra que todas las combinaciones sean equivalentes | Cada divergencia nueva lleva repro mínimo, diagnóstico esperado y regresión de runtime cuando corresponda |

El segundo bloque requiere revisar los efectos reales de cada función antes de asignarlos; no se resolverá declarando puros todos los externs. El tercero se ejecuta sobre capacidades existentes y no autoriza nueva gramática por defecto.

## Decisiones de arquitectura

- Mantener C99 como backend estable y el runtime `WVal` actual durante este bloque.
- Conservar ambas formas de importación y los canales `T ! E` / `?` / `else err`.
- No convertir el resultado del microbenchmark de `PERF-R02` en una migración global de structs: sus números pertenecen a un caso especializado en C. Reabrir esa implementación después de medir una carga Wysna representativa, con al menos 20% de mejora y paridad de igualdad, formato y ownership.
- No usar la ausencia de acceso a modelos como bloqueo para corregir semántica, inferencia y paridad del compilador.

La evidencia del cambio de tipos se conserva en [LANG-07](evidence/lang07-generic-error-unions.json) y en las pruebas de `tests/unit/test_native_generic_error_unions.py`. La integración permanece sujeta a aprobación explícita; no se modifica main ni develop desde esta rama.
