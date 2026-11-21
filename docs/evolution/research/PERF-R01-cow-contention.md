# PERF-R01 — Contención CoW (`rc > 1`) y Escalabilidad en Buffers Mutables (`1,000`–`100,000` filas)

- **Estado:** Completado (`2026-11-21`, macOS arm64, `-O2` bajo `--profile=release`)
- **Pregunta:** ¿Cuánta contención CoW (`rc > 1`) sufren los bucles de mutación sobre listas en `Seed` y `Selfhost`, especialmente al pasar el buffer por parámetro de solo lectura (`optimize_parameter_reads`) frente a aliasing explícito (`let keep = base`)?

## 1. Medición empírica reproducida

Programa evaluado (`--profile=release`, `WYSNA_COW_METRICS=1`, `WYSNA_MEM_CHECK=1`, con 1 corrida previa de calentamiento y helper de lectura `helper_head(base)` dentro del bucle):

| Motor | `n` | Modo | Tiempo (ms) | `detach_list` | `copied_slots` | `copied_bytes` | `allocated_bytes` | `allocs` |
| :--- | ---: | :--- | ---: | ---: | ---: | ---: | ---: | ---: |
| `Seed MIR → C99` | `1,000` | `rc == 1` (`helper_head` prestado) | `6.66` | `0` | `0` | `0 B` | `0 B` | `0` |
| `Seed MIR → C99` | `1,000` | `rc > 1` (`let keep = base`) | `3.81` | `1,000` | `1,000,000` | `8.00 MB` | `8.22 MB` | `2,000` |
| `Seed MIR → C99` | `10,000` | `rc == 1` (`helper_head` prestado) | `2.79` | `0` | `0` | `0 B` | `0 B` | `0` |
| `Seed MIR → C99` | `10,000` | `rc > 1` (`let keep = base`) | `34.29` | `10,000` | `100,000,000` | `800.00 MB` | `1.31 GB` | `20,000` |
| `Seed MIR → C99` | `100,000` | `rc == 1` (`helper_head` prestado) | `5.20` | `0` | `0` | `0 B` | `0 B` | `0` |
| `Selfhost MIR → C99` | `1,000` | `rc == 1` (`helper_head` prestado) | `7.53` | `0` | `0` | `0 B` | `0 B` | `0` |
| `Selfhost MIR → C99` | `1,000` | `rc > 1` (`let keep = base`) | `6.18` | `1,000` | `1,000,000` | `8.00 MB` | `8.22 MB` | `2,000` |
| `Selfhost MIR → C99` | `10,000` | `rc == 1` (`helper_head` prestado) | `3.37` | `0` | `0` | `0 B` | `0 B` | `0` |
| `Selfhost MIR → C99` | `10,000` | `rc > 1` (`let keep = base`) | `41.74` | `10,000` | `100,000,000` | `800.00 MB` | `1.31 GB` | `20,000` |
| `Selfhost MIR → C99` | `100,000` | `rc == 1` (`helper_head` prestado) | `5.09` | `0` | `0` | `0 B` | `0 B` | `0` |

## 2. Hallazgos clave

1. **Paridad `Seed ↔ Selfhost` confirmada tras `PERF-03`:** Al pasar `base` a una función auxiliar de solo lectura (`helper_head(base)`) dentro de un bucle de `100,000` iteraciones, ambos compiladores producen **`0` detaches y `0` bytes copiados** (`5.20 ms` en Seed vs `5.09 ms` en Selfhost). `optimize_parameter_reads` evita por completo la contención espuria de `rc`.
2. **Escalado cuadrático real bajo aliasing verdadero (`rc > 1`):** Cuando existe un alias vivo (`let keep = base`) durante cada mutación, multiplicar filas por `10x` (`1,000 → 10,000`) multiplica el tráfico de memoria por **`100x`** (`8 MB → 800 MB` copiados; `1.31 GB` reservados en `20,000` allocs). A `100,000` iteraciones el costo sería ~`80 GB` de copias.

## 3. Recomendación

- **No se necesita propagación interprocedural pesada adicional en el corto plazo** para parámetros prestados: el pase actual ya logra `0` detaches a `100k` en ambos motores.
- **Acción recomendada de bajo costo (DX):** Documentar en [`docs/OWNERSHIP_LIFETIME.md`](../../OWNERSHIP_LIFETIME.md) que mantener un binding alias (`let snapshot = list`) vivo dentro de un bucle mutante dispara CoW $O(n^2)$, y exponer el profiling con `WYSNA_COW_METRICS=1` como guía oficial cuando se sospeche regresión.
