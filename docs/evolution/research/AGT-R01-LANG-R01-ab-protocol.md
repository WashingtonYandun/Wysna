# AGT-R01 y LANG-R01 — Protocolo A/B y Resultados con `MockProvider` (Listo para Campaña LLM Real)

- **Estado:** Harness verificado extremo a extremo (`MockProvider` en verde); ejecución con LLMs reales **pendiente de credenciales API / servidor local activo** (`GEMINI_API_KEY`, `OPENAI_API_KEY` y `ANTHROPIC_API_KEY` no configuradas en esta sesión).
- **Herramienta:** [`bench/ai_eval/ab_cohort.py`](../../../bench/ai_eval/ab_cohort.py) + [`bench/ai_eval/harness.py`](../../../bench/ai_eval/harness.py) + [`bench/ai_eval/tasks.json`](../../../bench/ai_eval/tasks.json).

## 1. Cobertura implementada en el harness v0.8.0

1. **Métricas desagregadas (`AGT-06`):** `bench/ai_eval/harness.py` y `models.py` registran por separado `raw_pass_at_1` (primera compilación sin `fix`), `post_autofix_pass_at_1_rate` (tras `wysna fix --apply`), iteraciones hasta verde y bytes de contexto.
2. **Cohorte multi-archivo `>300` LOC (`AB-MULTIFILE-06`):** 5 módulos realistas (`models.wysna`, `parser.wysna`, `validator.wysna`, `reporter.wysna`, `main.wysna`, **`>330` líneas `.wysna`**) que evalúan edición quirúrgica localizada (`collateral_lines_changed == 0`). Verificada en [`test_ab_cohort.py`](../../../bench/ai_eval/tests/test_ab_cohort.py).
3. **Suite específica `LANG-R01` (`--suite=import-style`):** Tareas pareadas `LANG-R01-IMPORT-AS` (`import "std/..." as ...`) vs `LANG-R01-FROM-USE` (`from "std/..." use { ... }`) para medir tasa de alucinaciones entre ambas sintaxis antes de decidir deprecación.

## 2. Verificación observada sin red (`2026-11-21`)

- `PYTHONPATH=.:src python3 -m pytest -q bench/ai_eval/tests/test_ab_cohort.py bench/ai_eval/tests/test_harness.py`: **`17 passed in 20.84s`**.
- Ejecuciones CLI verificadas:
  - `python3 bench/ai_eval/ab_cohort.py --provider=mock --repetitions=1 --suite=default` → `decision: "inconclusive"`, 12 corridas sin errores del harness.
  - `python3 bench/ai_eval/ab_cohort.py --provider=mock --repetitions=1 --suite=import-style` → `decision: "inconclusive"`, 4 corridas sin errores del harness.

## 3. Comandos exactos para ejecutar con modelos reales (`AGT-R01` + `LANG-R01`)

### Opción A — Gemini API
```bash
export GEMINI_API_KEY="tu-api-key"
PYTHONPATH=.:src python3 bench/ai_eval/run_eval.py --provider gemini --model gemini-2.5-flash --output bench/ai_eval/results/v08_eval.json
PYTHONPATH=.:src python3 bench/ai_eval/ab_cohort.py --provider gemini --model gemini-2.5-flash --suite all --repetitions 3 --interactive-tools --output bench/ai_eval/results/v08_ab_cohort.json
```

### Opción B — Modelo local compatible OpenAI (LM Studio / Ollama / vLLM, ej. en servidor Windows SSH)
```bash
PYTHONPATH=.:src python3 bench/ai_eval/run_eval.py --provider openai --base-url http://localhost:1234/v1 --model <tu-modelo> --output bench/ai_eval/results/v08_local_eval.json
PYTHONPATH=.:src python3 bench/ai_eval/ab_cohort.py --provider openai --base-url http://localhost:1234/v1 --model <tu-modelo> --suite all --repetitions 3 --interactive-tools --output bench/ai_eval/results/v08_local_ab_cohort.json
```

**Criterio de éxito de [`01-FOCO.md`](../01-FOCO.md) a validar en esas corridas:** `raw_pass_at_1 >= 90%`, `post_fix_pass_at_1 >= 95%`, mediana `<= 1.5` iteraciones de reparación.
