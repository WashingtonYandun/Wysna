# AGT-R01 y LANG-R01 — Protocolo A/B y evidencia

## Estado

Las campañas reales están aplazadas por decisión del mantenedor; no se volverá a llamar a proveedores de IA en este cierre. Hay un piloto real gratuito y un protocolo corregido para medir la sintaxis de imports. El piloto anterior es evidencia exploratoria del prefetch semántico; no responde directamente a las hipótesis actuales de AGT-R01 ni LANG-R01.

- **Proveedor/modelo:** OpenRouter, nvidia/nemotron-3.5-super-vl:free.
- **Modo:** proveedor compatible con OpenAI; context_mode=prefetched; una repetición en orden AB.
- **Oráculo:** revisión 2.
- **Artefacto:** [v08_nemotron_3_5_super_vl_free_ab.json](../../../bench/ai_eval/results/v08_nemotron_3_5_super_vl_free_ab.json).
- **Revisión del compilador:** el JSON no registra el commit probado; no se atribuye el resultado a un SHA específico.
- **Réplica corregida intentada (2026-11-21):** OpenRouter respondió 429 — límite diario de modelos gratuitos agotado, cero solicitudes restantes, reinicio 2026-11-22 00:00 UTC. No se obtuvo reporte nuevo; no se usó ni se intentará un modelo de pago.

## 1. Qué midió el piloto anterior

El reporte bruto de --suite=all tuvo 8 tareas por condición y 16 ejecuciones. Su tasa general fue A=8/8 y B=6/8. Sin embargo, dos tareas de importación no recibieron prefetch semántico, así que no deben contar para estimar ese efecto. Al filtrar las seis tareas Agent-First relevantes:

| Evidencia | Condición A | Condición B |
| :--- | ---: | ---: |
| Tareas resueltas | 6/6 (100%) | 4/6 (66,7%) |
| Fallos | 0 | 2 |
| Regresiones del oráculo | 0 | 0 |

B falló AB-REFACTOR-03 y AB-H2-CAPABILITY-04 con oracle_mismatch. Entre las cuatro tareas Agent-First resueltas en ambas condiciones, el recálculo sobre las filas del JSON da una reducción mediana de tiempo de 6,65% y una reducción de tokens de -39,51% (B usó más tokens). No se alcanza el umbral de 20% y B pierde dos tareas; el resultado justifica mantener el prefetch apagado por defecto. Los valores globales del JSON (-75,85% de tiempo y -96,65% de tokens) mezclan las dos tareas de imports y no se interpretan como efecto de prefetch.

A/B aquí significa **sin contexto semántico prefijado** frente a **prefetch de graph, inspect y explain**. No es comparación de wysna patch --symbol frente a edición por líneas ni evaluación de herramientas interactivas elegidas por el modelo. Por eso el resultado no acepta ni descarta la hipótesis concreta AGT-R01.

## 2. Qué se observó sobre imports y qué no

Las tareas LANG-R01-IMPORT-AS y LANG-R01-FROM-USE pasaron en ambas condiciones, una observación por forma y condición. Sin embargo, eran tareas separadas: la condición A/B seguía midiendo el contexto, no la forma sintáctica. El objetivo y prompt tampoco eran idénticos. El piloto solo confirma que el modelo produjo una solución aceptada una vez para cada variante; **no permite decir que una sintaxis sea mejor**.

La cohorte del harness se corrigió para tener tres tareas pareadas (suma, producto y máximo), con el mismo objetivo y oráculo en A y B. La revisión 3 comprueba la forma del import en el AST y emplea entradas variables para rechazar respuestas constantes. A solicita import "std/parse" as parse; B solicita from "std/parse" use { parse_int }. Al no usar fixtures, ninguna condición recibe prefetch semántico.

## 3. Protocolo gratuito corregido

Ejecutar dos cohortes separadas, cada una con tres repeticiones que alternan el orden AB, BA, AB y el mismo límite de reparaciones. La primera mide prefetch semántico sobre seis tareas; la segunda mide la forma de importación sobre tres tareas equivalentes por condición.

    # OPENROUTER_API_KEY debe estar en el entorno y no debe guardarse en el reporte.
    python3 bench/ai_eval/ab_cohort.py \
      --provider=openai --base-url=https://openrouter.ai/api/v1 \
      --model=nvidia/nemotron-3.5-super-vl:free --suite=default \
      --repetitions=3 --max-repair-cycles=3 \
      --checkpoint=bench/ai_eval/results/v08_nemotron_prefetch_3rep.checkpoint.json \
      --output=bench/ai_eval/results/v08_nemotron_prefetch_3rep.json

    python3 bench/ai_eval/ab_cohort.py \
      --provider=openai --base-url=https://openrouter.ai/api/v1 \
      --model=nvidia/nemotron-3.5-super-vl:free --suite=import-style \
      --repetitions=3 --max-repair-cycles=3 \
      --checkpoint=bench/ai_eval/results/v08_nemotron_import_style_3rep.checkpoint.json \
      --output=bench/ai_eval/results/v08_nemotron_import_style_3rep.json
No se debe sustituir el modelo por una variante de pago ni añadir fallback a modelos de pago. El token de OpenRouter no se escribe en archivos ni se pasa en texto plano como argumento de línea de comandos. La réplica queda aplazada indefinidamente hasta que el mantenedor disponga de acceso y solicite ejecutarla; el reinicio de cuota no autoriza una nueva llamada.

### Criterios de interpretación

- **Prefetch:** conservarlo apagado por defecto. Reabrir expansión solo con una cohorte mayor que revierta la pérdida de éxito y muestre una mejora repetida de al menos 20% en tokens o tiempo.
- **Imports:** no retirar ninguna sintaxis. Recomendar from ... use solo si mantiene la tasa de éxito y mejora al menos 20% en tokens o reparaciones a lo largo de las tres repeticiones; en cualquier otro caso, mantener ambas y declarar el resultado inconcluso.
- **AGT-R01:** sigue pendiente una tarea específica que compare wysna patch --symbol con edición estándar de líneas sobre los mismos archivos y oráculos de más de 300 LOC. La campaña disponible no hace esa comparación.

## 4. Benchmark de 30 tareas con reanudación

El intento previo de run_eval.py terminó con código 1 y no dejó reporte final. Cuando se retome expresamente la campaña, usar el worktree que contenga el provider y los checkpoints:

    python3 bench/ai_eval/run_eval.py \
      --provider=openai \
      --base-url=https://openrouter.ai/api/v1 \
      --model=nvidia/nemotron-3.5-super-vl:free \
      --max-repair-cycles=3 \
      --checkpoint=bench/ai_eval/results/v08_nemotron_30.checkpoint.json \
      --output=bench/ai_eval/results/v08_nemotron_30.json

Después de una interrupción, repetir exactamente el mismo comando y conservar el checkpoint. Cambiar modelo, corpus, código evaluado, filtro, endpoint o presupuesto de reparación invalida la reanudación; se debe elegir un checkpoint nuevo. El archivo final solo aparece cuando terminan las tareas restantes.

## 5. Verificación del harness sin red

Las notas anteriores registran la validación de MockProvider del 2026-11-21 (17 passed y cohortes mock inconclusas). La suite ampliada se valida sin red en este cierre; los resultados y limitaciones están en [11-CIERRE-SIN-LLM.md](../11-CIERRE-SIN-LLM.md).
