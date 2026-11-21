# Cierre local del plan sin servicios de IA

Fecha: 2026-11-21. El mantenedor aplazó todas las mediciones con modelos reales por falta de presupuesto y credenciales. No se han utilizado claves ni contactado proveedores de IA durante este cierre. No se programa una campaña para el reinicio de una cuota.

## Trabajo completado

- El provider de OpenRouter solo permite IDs explícitos terminados en `:free`. El límite diario detiene el proceso sin reintentar; las respuestas transitorias conservan backoff.
- `run_eval.py` y `ab_cohort.py` guardan puntos de control atómicos. La reanudación conserva las tareas/condiciones terminadas y rechaza cambios en fuente, corpus, modelo, endpoint o parámetros. La condición en curso se vuelve a ejecutar tras una interrupción; no se conserva una respuesta de proveedor incompleta.
- Cada intento conserva respuesta completa, archivos emitidos, diagnósticos y métricas. El tiempo A/B incluye preparación del contexto semántico. Las comparaciones de imports y contexto se informan por separado cuando se elige la suite mixta.
- La suite de imports contiene tres problemas pareados con entradas variables. El oráculo comprueba la declaración del import en el AST y rechaza una solución que solamente imprime el resultado esperado.
- La selección explícita de CI ahora incluye toda la carpeta de pruebas del evaluador, además de los contratos previos del producto.
- El criterio de eficacia es único y ejecutable en `bench/ai_eval/acceptance.py`: 30 tareas soportadas, 90% de éxito funcional inicial sin autofix, 95% de éxito funcional inicial incluyendo autofix y mediana de hasta 1,5 reparaciones entre tareas resueltas. Se conserva el umbral más exigente ya expresado en el roadmap; se retiró la contradicción de 75%/90% del foco y el 80% del README. La mediana publica el número de tareas resueltas que la componen.
- Los reportes mock declaran `not_measured_with_real_model`. `Pass@3` significa hasta tres respuestas totales; el presupuesto por defecto de tres reparaciones permite cuatro respuestas y se informa por separado mediante `resolved_rate`.
- El target `ingest` de `wysofia-ops` completa lectura de archivo, validación del lote, transformación y persistencia de un reporte JSON. Un lote rechazado no modifica el reporte existente. No se promete escritura atómica si el proceso o disco falla durante `write_file`.

## Evidencia local

- 46 pruebas y 14 subtests pasaron para evaluador, contratos de CI y empaquetado nativo.
- Se descargó el SDK macOS del run 44941044666 de PR #169, se verificó el SHA-256 del artefacto y se validó fuera del checkout, con PATH aislado. La aceptación ampliada pasó 70 comprobaciones, incluyendo bootstrap nativo, checksums y el nuevo flujo de ingesta.
- El validador del flujo de archivos pasó seis casos con salida y persistencia idénticas en Seed y en el binario compilado por el SDK instalado: válido, vacío, status fuera de rango, costo negativo, JSON malformado y archivo ausente.
- Python ejecutó los validadores y la comparación Seed. Los procesos del SDK usaron sus herramientas empaquetadas con PATH aislado. No se afirma independencia de Python para Seed.
- El SDK identifica como origen `54e1233`, no el futuro merge de este PR. Su fuente del compilador coincide con la fuente comprobada mediante el validador; los cambios actuales son del evaluador y del ejemplo.

La prueba CLI con mock completó las 30 tareas y 18 condiciones de imports y reanudó ambos checkpoints sin repetir resultados. Este ejercicio comprueba el protocolo, no la calidad de autoría. Su resumen está en [protocolo sin red](evidence/offline-eval-protocol.json).

Reportes reproducibles: [SDK instalado](evidence/offline-native-install-report.json) y [flujo Seed/nativo](evidence/offline-ops-workflow.json).

## Trabajo aplazado que requiere modelos

1. Ejecutar la línea base de 30 tareas con un modelo real y reportar sus resultados sin imputar fallos de cuota al lenguaje.
2. Seleccionar las tres causas de fallo más frecuentes a partir de esa línea base, crear regresiones y corregirlas. No se inventa ese ranking usando las respuestas de fallback del mock.
3. Ejecutar las cohortes independientes de contexto y sintaxis con el mismo modelo y parámetros. El piloto anterior continúa siendo exploratorio y no determina retirada de sintaxis.
4. Implementar el adaptador específico del [experimento patch por símbolo](research/AGT-R01-patch-protocol.md) cuando se retome esa investigación y después medirlo. El runner interactivo existente no implementa esa comparación.

## Integración

PR #169 permanece en draft con CI exitoso. PR #170 apunta a main para que su CI se ejecute aunque #169 no se integre todavía. No hay comentarios de revisión pendientes en las consultas realizadas. La revisión automática bloqueó el intento de merge por la instrucción previa de mantener los PRs en draft; el cierre del código puede completarse y validarse, pero la integración requiere aprobación explícita nueva.
