# PERF-R02 — `WStructBuf` (Keys Estáticas) vs `struct` C Nominal Inline

- **Estado:** Completado (`2026-11-21`, macOS arm64, `clang -O2 -std=c99`)
- **Criterio de decisión en [`09-ROADMAP.md`](../09-ROADMAP.md):** Justifica migrar structs calientes a representación C nominal solo si la mejora medida es `>= 20%`.

## 1. Microbenchmark reproducido (`1,000,000` asignaciones + lectura de 2 campos + liberación)

Comparación directa compilada con `clang -O2 -std=c99`:
- **Representación A (`WStructBuf` post-`PERF-05`):** `wysna_make_struct_static("NominalPoint", 4, keys, vals)` + `wysna_field_get_at` + `wysna_release`.
- **Representación B (`NominalPoint` struct C nominal):** `struct { int32_t refcount; int64_t a, b, c, d; }` asignado con `wysna_alloc_safe(sizeof(NominalPoint))`, acceso directo `np->a + np->d` y `free(np)`.

| Métrica | `WStructBuf` (keys estáticas) | `NominalPoint` C struct inline | Diferencia / Mejora |
| :--- | ---: | ---: | :---: |
| **Tiempo `1M` ops (`clang -O2`)** | `56.55 ms` | `14.71 ms` | **`-73.99%` (`3.84x` más rápido)** |
| **Tamaño en heap por instancia (4 campos `int`)** | `88 bytes` (`56 B` header + `32 B` `WVal`) | `40 bytes` (`4 B` rc + pad + `32 B` campos) | **`-54.5%` memoria/instancia** |
| **Checksum funcional** | `1` (exacto) | `1` (exacto) | Equivalente |

## 2. Hallazgos y decisión (`GO` con alcance acotado)

- **Supera ampliamente el umbral (`73.99% >> 20%`):** La ganancia viene de eliminar las dos indirecciones (`st->vals`), evitar el boxing/unboxing `WVal` en campos escalares (`int`/`float`/`bool`) y reducir el encabezado de `56 bytes` (`WStructBuf` compartido con `class` + vtable + weak control) a `4–8 bytes`.
- **Plan de evolución recomendado para el próximo ciclo:** No reemplazar `WStructBuf` globalmente de golpe (pues `wysna_format_value` y `wysna_eq` dependen de introspección genérica de `keys`/`vals`); en su lugar, generar structs C nominales especializados en `src/mir/CCodeGen.py` para `struct` sin campos complejos/genéricos ni reflexión, generando automáticamente sus funciones especializadas `eq`/`drop`/`fmt`.
