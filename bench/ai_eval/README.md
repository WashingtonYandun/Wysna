# Wysna AI Authoring and Repair Benchmark (SPEC-002)

This benchmark implements the canonical evaluation protocol defined in **[SPEC-002 (Herramientas y autoría por IA)](../../SPEC.md)** and **ADR-030 / ADR-032**. Its purpose is to empirically measure how easily an AI agent can author, typecheck, execute, and repair idiomatic Wysna code.

---

## 1. Methodology & Evaluation Protocol

1. **Cohort-frozen 30 Tasks:** The evaluation corpus contains exactly 30 tasks spanning 4 core domains. Tasks testing capabilities not yet present in the current toolchain (e.g. native SQLite driver) are formally registered as `BLOCKED` with explicit rationale, rather than removed.
2. **Hidden Test Oracles:** The model receives only task descriptions, constraints, and workspace skeleton fixtures. All functional validation runs against independent, hidden oracles.
3. **Multi-Turn Repair Loop ($\le 3$ Repairs):**
   - **Attempt 0 (Initial Generation):** Measures direct authoring capability (`Pass@1`).
   - **Attempts 1–3 (Diagnostic Feedback):** If the program fails static check (`wysna check --json`) or functional execution, compiler diagnostics (error code, file, line, span, and message) are fed back to the model.
   - **Effectiveness gate:** 90% raw functional Pass@1, 95% functional Pass@1 including deterministic autofix, and median repair submissions <= 1.5 on the full 30-task corpus. See `acceptance.py`. Mock reports cannot satisfy this gate.
4. **Zero Collateral Repository Impact:** All evaluation workspaces run inside isolated temporary directories (`tempfile.TemporaryDirectory()`). The compiler under test is treated as a black-box toolchain via CLI.

---

## 2. Corpus Taxonomy (30 Tasks)

| Domain | Tasks | Scope | Supported in 0.8.0 |
| :--- | :---: | :--- | :---: |
| **CLI & Scripting** | 12 (`TASK-01` .. `TASK-12`) | Argument parsing (`args()`), subcommands, flags, exit codes, string formatting. | 12 / 12 |
| **Data & Files** | 8 (`TASK-13` .. `TASK-20`) | JSON, CSV aggregation, key flattening, list merges, file I/O (`read_file`, `write_file`), byte checksums. | 8 / 8 |
| **Multi-Module Projects** | 6 (`TASK-21` .. `TASK-26`) | Module imports (`pub fn`), cross-module structs, circular dependency resolution, lockfile verification. | 6 / 6 |
| **System & SQLite** | 4 (`TASK-27` .. `TASK-30`) | Resource lifecycle safety, in-memory relational queries, transactional commit/rollback (plus reusable C FFI in `examples/packages/sqlite`). | 4 / 4 |

---

## 3. Metrics Recorded

- **`raw_pass_at_1_rate`:** First-response functional success without autofix. `pass_at_1_rate` includes deterministic autofix but no additional model response.
- **`Pass@3`:** Functional success within three total model responses. The default budget allows four total responses: initial generation plus three repairs; `resolved_rate` reports success within that whole budget.
- **Taxonomy of Failures:**
  - `syntax`: Parser error or invalid token sequence.
  - `type`: Type checker failure (mismatch, missing field, unhandled error union).
  - `circular_dependency`: Unresolved import cycle (`W1003`).
  - `api_hallucination`: Invocations of non-existent standard library or Python functions.
  - `oracle_mismatch`: Clean compilation, but functional output diverges from expected.
  - `runtime_panic`: Toolchain crash, unhandled exception, or unexpected exit code.
- **Latency & Token Efficiency:** Milliseconds elapsed and token counts per attempt.

---

## 4. Usage

### Dry-run / CI Verification (Mock Provider)
```bash
python3 bench/ai_eval/run_eval.py --provider=mock --output=bench/ai_eval/results/dry_run.json
```

### Single Task Test
```bash
python3 bench/ai_eval/run_eval.py --provider=mock --task=TASK-01
```

### Real AI Evaluation via Gemini API
```bash
export GEMINI_API_KEY="your-api-key"
python3 bench/ai_eval/run_eval.py --provider=gemini --model=gemini-2.5-flash --output=bench/ai_eval/results/gemini_baseline.json
```

### Local Models (Ollama / LM Studio / OpenAI-Compatible)
```bash
python3 bench/ai_eval/run_eval.py \
  --provider=openai \
  --base-url="http://localhost:1234/v1" \
  --model="qwen2.5-coder-7b-instruct" \
  --output=bench/ai_eval/results/local_baseline.json
```

### Resumable Real-Model Evaluation

Pass `--checkpoint` to persist each completed task atomically. If the process
stops because of a provider quota, interruption, or network failure, rerun the
same command with the same checkpoint path. The runner resumes only when the
model, endpoint, task corpus, source revision/tree, task filter, and repair
budget still match. Use a different path for `--output`; the final JSON report
is written after the remaining tasks finish.

```bash
python3 bench/ai_eval/run_eval.py \
  --provider=openai \
  --base-url=https://openrouter.ai/api/v1 \
  --model=nvidia/nemotron-3.5-super-vl:free \
  --checkpoint=bench/ai_eval/results/nemotron_30.checkpoint.json \
  --output=bench/ai_eval/results/nemotron_30.json
```

The API key is read from `OPENROUTER_API_KEY` (or can be provided through the
provider's supported configuration). Do not use a paid model for this run.

---

## 5. Running Tests
```bash
PYTHONPATH=. pytest bench/ai_eval/tests/test_harness.py
```

## Consultas elegidas por el modelo

Usa `ab_cohort.py --interactive-tools` para comparar dos condiciones con el
mismo modelo y las mismas pruebas. Ambas pueden leer un archivo Wysna del
proyecto de trabajo. La condición B también puede consultar `graph` e
`inspect` por símbolo. Cada intento admite dos consultas y una respuesta
final. La medición suma los turnos y tokens de las consultas al total.

```sh
python3 bench/ai_eval/ab_cohort.py --interactive-tools --provider openai --base-url http://localhost:1234/v1 --model MODEL_ID --output /tmp/wysna-ab.json
```

Los informes con `oracle_revision: 2` verifican el diagnóstico de rechazo en stderr y
el estado del archivo inmediatamente después de cada lote inválido. El oráculo
prueba un destino ausente y otro que ya contiene datos. Para comparar tasas de
éxito históricas, ejecuta ambas condiciones con la misma revisión del oráculo.

Ejecuta la evaluación con código generado dentro de un entorno aislado.
El proveedor simulado comprueba el protocolo de consultas. Para medir éxito,
tiempo y tokens de un modelo, utiliza un proveedor real con uso reportado.

## Impacto observado en proyectos

`impact_benchmark.py` contrasta el grafo con cambios de comportamiento en las
pruebas declaradas de `shared-browser-app` y `wysofia-ops`. Por defecto aplica
cinco mutaciones en copias temporales y ejecuta las pruebas antes y después.
También admite comparar una revisión de Git con otra revisión o con el árbol
de trabajo. Los cambios de código de salida y stdout sirven como oráculo
para pruebas que pasan en la base y mantienen la misma fuente.
Ambas revisiones se evalúan con el mismo compilador y biblioteca estándar
del checkout que ejecuta el ensayo.

```sh
python3 bench/ai_eval/impact_benchmark.py --output /tmp/wysna-impact.json
python3 bench/ai_eval/impact_benchmark.py \
  --project examples/wysofia-ops --base 2c77631 --entry src/main.wysna \
  --symbol parse_events_json --symbol decode_event --output /tmp/wysna-impact-revisions.json
```

Pasa `--native-compiler` con la ruta al compilador Selfhost para medir también
su grafo. El informe incluye pruebas observadas que el grafo encontró,
pruebas inciertas, omisiones, tiempo del grafo y tiempo de las pruebas
seleccionadas. La selección incluye las entradas inciertas. La comparación del
contexto usa tokens léxicos como aproximación. Los tokens facturados y el
tiempo del modelo se miden con el proveedor real en el ensayo A/B.

## Campañas aplazadas y verificación sin red

Las campañas reales están aplazadas por decisión del mantenedor, sin presupuesto ni credenciales. No se programa una ejecución al reiniciarse la cuota. Los proveedores simulados verifican oráculos, recuperación y métricas; no estiman la eficacia de un modelo real.

Ambos runners aceptan `--checkpoint` y guardan cada tarea/condición terminada. La condición en curso se repite si el proceso falla dentro de ella. No existe recuperación a mitad de una respuesta del proveedor. Repetir el mismo comando conserva las condiciones terminadas, sus intentos y sus diagnósticos. Cambios en corpus, revisión/árbol, modelo, endpoint o parámetros invalidan el checkpoint. No mezclar puntos de control de revisión 2 con la revisión 3 del oráculo de imports.

OpenRouter admite exclusivamente IDs terminados en `:free`. Un error diario `free-models-per-day` detiene la corrida sin reintentos; los límites transitorios mantienen backoff. No hay fallback a modelos de pago. Las credenciales no se guardan en checkpoints.

La suite `import-style` tiene tres tareas (suma, producto y máximo), con casos de entrada diferentes por tarea. A requiere importación por namespace y B importación selectiva. El oráculo comprueba las declaraciones reales del AST. La suite `default` mide contexto semántico sobre seis tareas. `--interactive-tools` mide consultas elegidas por el modelo; ninguna de estas opciones implementa todavía la comparación específica de `patch --symbol`.

Para validar el protocolo localmente, sin contactar servicios:

    python3 -m pytest -q bench/ai_eval/tests/
    python3 bench/ai_eval/run_eval.py --provider=mock --checkpoint=/tmp/mock30.checkpoint.json --output=/tmp/mock30.json
    python3 bench/ai_eval/ab_cohort.py --provider=mock --suite=import-style --repetitions=3 --checkpoint=/tmp/mock-imports.checkpoint.json --output=/tmp/mock-imports.json

Las respuestas de fallback del mock no resuelven el corpus completo; sus tasas no representan la calidad de Wysna ni de un modelo.
