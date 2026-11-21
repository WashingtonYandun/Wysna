# AGT-R01 — Comparación específica de edición por símbolo

Estado: protocolo preparado; adaptador experimental y ejecución real pendientes. No existe evidencia para aprobar o descartar esta hipótesis. La campaña queda aplazada hasta que el mantenedor disponga de acceso y solicite ejecutarla.

## Variable y controles

Reutilizar el workspace de cinco módulos y más de 330 líneas de AB-MULTIFILE-06. Mantener idénticos objetivo, fixtures, modelo, oráculos funcionales y presupuesto de contexto en las dos condiciones.

- A: el modelo emite archivo, rango de líneas esperado y reemplazo; el adaptador valida el hash de fuente antes de aplicar la edición.
- B: el modelo emite archivo, símbolo y declaración completa; el adaptador aplica `wysna patch FILE --symbol SYMBOL --expect-graph-hash HASH --with REPLACEMENT --apply --output-format=json-v1`.
- Ambas condiciones leen los mismos archivos y reciben los mismos diagnósticos. El coste de obtener el grafo/hash se cuenta como parte de B. No introducir prefetch adicional ni consultas semánticas exclusivas fuera de las necesarias para localizar/aplicar el parche.

## Tareas

1. Corregir el límite superior de importes en validator.wysna manteniendo el resto de declaraciones intacto.
2. Cambiar un formato de reporte en reporter.wysna conservando la API y los consumidores.
3. Corregir la validación de un campo en parser.wysna manteniendo los casos de dominio válidos.

Las tres deben tener casos positivos, negativos y pruebas de módulos vecinos. El adaptador debe verificar hashes, confines del workspace, cambios fuera de la declaración y ausencia de escritura cuando se rechaza una edición. Usar cuatro repeticiones AB/BA/AB/BA para equilibrar exactamente el orden. Guardar cada condición terminada, la respuesta completa y los archivos resultantes.

## Métricas y decisión

Medir éxito funcional sin cambios colaterales, rechazos de edición, intentos de reparación, tokens totales y tiempo total incluyendo herramientas. Mantener la herramienta como opción y recomendarla como primera elección solo si no baja la tasa de éxito ni suben regresiones, y mejora al menos 20% el número de reparaciones o los tokens en más de una tarea. Con doce pares el resultado sigue siendo exploratorio; no justificar retirada de APIs ni afirmaciones generales sobre otros modelos.

No ejecutar este protocolo mediante `ab_cohort.py --interactive-tools`: ese modo solo expone lectura, graph e inspect. Implementar primero el adaptador descrito y comprobarlo con fixtures deterministas en un cambio separado cuando se retome esta investigación.
