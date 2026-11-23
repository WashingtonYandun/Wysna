# Flujos de lanzamiento para agentes

Los tres proyectos usan el mismo SDK nativo, un manifiesto de proyecto y un
lockfile reproducible. La prueba de aceptación los instala en directorios
temporales, comprueba el lock, ejecuta pruebas y construye sus binarios con Zig
empaquetado.

- [Automatización CLI](../examples/automation) ejecuta un binario con un vector
  explícito de argumentos. La aceptación usa el binario del worker como hijo y
  comprueba su salida y código de retorno.
- [Procesamiento de datos](../examples/wysofia-ops) usa el target `ingest`.
  Valida un lote JSON y genera un reporte. La segunda ejecución comprueba que
  un lote inválido conserva el reporte válido anterior.
- [Worker de lotes](../examples/agent-worker) recibe un array JSON y devuelve un
  resultado por índice. Tiene entradas acotadas, dos callbacks en vuelo y
  un error tipado para cada elemento fuera de rango.

El mismo worker se empaqueta en USTAR. Otro proyecto lo incorpora por SHA-256,
escribe su lock y lo restaura desde la caché local, usando la API pública
`pkg/worker/batch.wysna`. El reporte guarda el hash comprobado del archivo.

```sh
python3 tools/validate_launch_projects.py --sdk /ruta/wysna-native --examples examples --report launch-acceptance.json
```

El reporte `wysna.launch-acceptance/1` vincula las pruebas al commit del SDK.
Incluye un recorrido de reparación automática. `check` entrega W3001 con una
edición verificable, `fix` la aplica, y el programa reparado debe producir la
salida esperada. El recorrido se ejecuta sin proveedores de IA. Las tasas de
éxito y el costo de autoría comparativo requieren mediciones con modelos.
