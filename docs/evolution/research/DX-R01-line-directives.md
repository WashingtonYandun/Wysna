# DX-R01 — Prototipo de Directivas `#line` en el Backend MIR C99

- **Estado:** Completado (prototipo opt-in implementado y verificado en CI local, `2026-11-21`)
- **Alcance:** [`src/mir/CCodeGen.py`](../../../src/mir/CCodeGen.py), [`src/compiler/CompilerService.py`](../../../src/compiler/CompilerService.py), [`src/tooling/CLI.py`](../../../src/tooling/CLI.py), [`src/tooling/SourceCommand.py`](../../../src/tooling/SourceCommand.py).

## 1. Qué se implementó

- Nuevo flag CLI **`--line-directives`** aceptado en `wysna emit-c --line-directives` y `wysna build --line-directives` bajo `--engine=seed` (`--backend=c99`).
- Parámetro opt-in `emit_line_directives=False` en `CCodeGen` y `line_directives=False` en `CompilerService.emit_c(...)`.
- Dentro de `CCodeGen.function()`, antes de las sentencias generadas por cada operación MIR con `source.file` válido y `source.line > 0`, cuando cambia `(file, line)`, se emite:
  ```c
  #line <line> "<ruta/archivo.wysna>"
  ```

## 2. Evidencia de verificación

- Test [`test_emit_c_line_directives_compiles_under_clang_g`](../../../tests/unit/test_tooling_engine_parity.py):
  - Ejecutó `main.py emit-c --line-directives dbg_sample.wysna -o dbg_sample.c`.
  - Verificó la presencia de directivas `#line 2 "..."` / `#line 4 "..."` mapeando al `.wysna` original.
  - Compiló el C resultante con `clang -g -std=c99` sin errores ni warnings fatales y ejecutó el binario nativo obteniendo salida `15`.

## 3. Hallazgos y recomendación

- **Beneficio real:** Permite mapear líneas en DWARF (`clang -g`), `lldb`/`gdb`, `ASan`/`UBSan` y perf profilers directamente al código `.wysna` sin tocar el formato C generado por defecto.
- **Costo / Trade-off:** Al activarlo, los errores del compilador C reportan líneas `.wysna` (excelente para debuggers), pero para depurar el propio generador C conviene mantenerlo **opt-in** (`--line-directives` apagado por defecto) tal como quedó implementado. Portarlo a `Selfhost` (`selfhost/src/08_mir_c99.wysna`) requiere `< 15` líneas si se adopta como estándar de builds `--profile=dev`.
