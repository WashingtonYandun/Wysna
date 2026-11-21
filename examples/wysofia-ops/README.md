# wysofia-ops (Wysna v0.8.0 Native Dogfooding Application)

`wysofia-ops` is an operational telemetry scoring and JSON/SQLite audit service/CLI written in pure Wysna and built/tested using the Python-free native SDK (`native-core-v1`).

## Features Exercised

- Strict `wysna.toml` v1 project layout (`[package]`, `[toolchain]`, `[sources]`, multi-target `[targets.app]` + `[targets.sqlite-audit]`, and `[[tests]]`).
- Cross-module project imports (`import "audit.wysna"` resolved via `[sources] root = "src"` in both `src/` and `tests/`).
- Standard library JSON parsing & serialization (`std/json`).
- Design-by-Contract (`requires` / `ensures`) exported to Draft 2020-12 tool schemas (`wysna schema`).
- Optional SQLite C FFI persistence (`native/ops_sqlite.c` linked with `--library sqlite3`).

## Usage (Standalone Native SDK — Zero Python)

```sh
wysna project check --manifest examples/wysofia-ops/wysna.toml --output-format=json-v1
wysna project test --manifest examples/wysofia-ops/wysna.toml
wysna project build --manifest examples/wysofia-ops/wysna.toml -o /tmp/wysofia-ops --profile release
wysna project run --manifest examples/wysofia-ops/wysna.toml -- --demo
wysna api --manifest examples/wysofia-ops/wysna.toml --output-format=json-v1
wysna schema examples/wysofia-ops/src/audit.wysna --output-format=json-v1

# SQLite FFI target (requires sqlite3 development headers):
wysna project run --manifest examples/wysofia-ops/wysna.toml \
  --target sqlite-audit \
  --include-dir examples/wysofia-ops/native \
  --link examples/wysofia-ops/native/ops_sqlite.c \
  --library sqlite3
```

## Ingesta de archivos con reporte persistido

El target `ingest` lee un JSON, valida todos los eventos, calcula el resumen y escribe el reporte después de validar el lote completo:

```sh
wysna project build --manifest examples/wysofia-ops/wysna.toml --target ingest -o /tmp/wysna-ingest
/tmp/wysna-ingest events.json report.json
```

Códigos de salida: `0` correcto, `1` JSON/dominio inválido, `2` argumentos o lectura inválidos, `3` fallo al escribir el reporte. Un lote inválido conserva un reporte anterior. La escritura final usa `write_file`; no promete reemplazo atómico frente a fallos del disco o del proceso durante la escritura.

La paridad del flujo completo con Seed y un SDK instalado se comprueba sin servicios externos:

```sh
python3 tools/validate_ops_workflow.py --sdk /ruta/wysna-native --report /tmp/ops-workflow.json
```

El SDK compila y ejecuta el flujo con PATH aislado. Python ejecuta el validador y la comparación Seed; no forma parte del SDK nativo.
