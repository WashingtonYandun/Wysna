# ADR-05 · Arquitectura Híbrida (Compilador Dual como Oráculo; Tooling en Selfhost)

- **Fecha:** 2026-11-22
- **Estado:** Aprobado (en implementación — Bloque 1)
- **Alcance:** `SIMP-R01`, frontera del Native SDK y secuencia pre-medición

## Regla en una línea

> **El lenguaje (sintaxis, tipos, HIR/MIR) se valida contra Seed; el tooling (`project`, `packages`, `mcp`, `lsp`, `schema`) vive solo en Selfhost.**

## Decisión

1. **Arquitectura Híbrida (Opción C — `SIMP-R01`):**
   - **Compilador dual hasta `v1.0`:** los cambios de lenguaje (limpieza sintáctica, `match`, `@atributos`) siguen validándose contra el oráculo diferencial de 4 motores (`Interpreter`, `Seed AST C99`, `Seed MIR C99`, `Selfhost MIR C99`). El retiro del checker de Seed ocurre solo después de congelar `v1.0`.
   - **Tooling único en Selfhost:** `project`, `packages`, `mcp`, `lsp` y `schema` se mantienen exclusivamente en Selfhost. `./wysna project|packages|mcp|lsp|schema` delega al binario nativo (`.wysna_cache/dev-sdk/bin/wysna`); `--engine=seed` / `WYSNA_DELEGATE_NATIVE=0` queda solo como escape explícito para oráculos Seed y targets WASI/Browser. `wysna packages` es exclusivamente nativo.
   - **Condición no negociable (`<5 s`):** `tools/build_selfhost_dev.py` emite y compila unidades de tooling (`project`, `mcp`, `driver`) de forma independiente con caché por hash SHA-256 de módulo, sin tocar `08_semantics`/`08_mir` ni disparar bootstrap de 3 etapas en iteraciones locales.

2. **Frontera del Native SDK:**
   - **Dentro de Selfhost:** `--sandbox` (`W5006`, reutilizando `WYSNA_DENY_CAP_*` del runtime C99) y lints `W6001`/`W6002`.
   - **Fuera de Selfhost:** REPL, `--backend=llvm-experimental` (congelado), `library-mode` y WebAssembly/WASI.
   - **Stub accionable `--target=wasi`:** `packaging/native/driver.c` detecta `--target=wasi`/`web`/`wasm32-*` y emite `[W2013]` (exit 2) indicando usar la distribución Seed (port a Native SDK diferido a post-`v1.0` aprovechando el `zig` empaquetado).

3. **Secuencia aprobada (pre-campaña LLM):**
   1. **Bloque 1 (time-box):** Build dev `<5 s` por unidad + delegación mínima `SIMP-R01` + stub `--target=wasi` + `CONTRIBUTING.md`/ADRs.
   2. **Bloque 2 (`FUZZ-A01`, time-box):** Fuzzing diferencial multiarchivo (genéricos, errores, closures, async) + corrección de divergencias.
   3. **Bloque 3 (Cierre Native SDK):** `--sandbox` + `W6001`/`W6002` en Selfhost + `PKG-A01` (solo Selfhost, conservando los 19 tests de seguridad USTAR v1) + `OPS-A01`.
   4. **Bloque 4 (Pulido del lenguaje pre-`v1.0`):** limpieza sintáctica (`1A`), cierre formal de closures por snapshot inmutable (`2A` + hint `W3001`), mejoras de `match` (`|`, `..`/`..=`, struct patterns, ramas inalcanzables) y `@atributos` estáticos cerrados.

## Consecuencias

- Se elimina el doble impuesto de mantener ~8,000+ LOC paralelas de tooling en Python (`PKG-A01` se implementa una sola vez en nativo).
- Se conserva intacto el oráculo diferencial de 4 motores para evolucionar el lenguaje con seguridad antes de `v1.0`.
- El bootstrap de 3 etapas (`selfhost/compiler.wysna`, `C0 == C1 == C2`) permanece intacto para CI y empaquetado de releases.
