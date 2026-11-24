# Rendimiento del SDK nativo

Medición del 24 de noviembre de 2026 en Apple M3 con macOS 26.6.2 ARM64. Se comparan el SDK de referencia `177954c`, cuyo árbol coincide con `main` en `5aa0ac7`, y el candidato `05de9ac`.

## Resultados

- **Ordenamiento estable de 16.384 enteros.** La mediana bajó de 21,47 a 13,45 ms. El runtime registró 49.151 objetos asignados en la referencia y 16.385 en el candidato.
- **Rollback de 40.000 operaciones.** La mediana bajó de 9,11 a 7,29 ms. Las retenciones ARC bajaron de 480.016 a 200.014. Se mantuvo el resultado del rollback y el chequeo de memoria reportó cero objetos vivos al salir.
- **Diccionario de 50.000 enteros con 120.000 consultas.** La mediana bajó de 7,58 a 7,11 ms. La carga incluye aciertos y fallos.
- **Compilador con un literal de 4.096 elementos.** `check` bajó de 151,85 a 128,00 ms y `emit-c` bajó de 177,05 a 149,73 ms. Ambos SDK emitieron los mismos 287.667 bytes de C para esa entrada.
- **Arranque del CLI mínimo.** La mediana quedó en 1,72 y 1,70 ms. El binario pasó de 90.376 a 90.392 bytes.

Las cifras se limitan a estos programas y a este entorno. Las muestras completas, el p95, los tamaños, los SHA y los contadores de memoria están en [results-macos-arm64.json](results-macos-arm64.json).

## Reproducción

Construye cada SDK desde el commit correspondiente con `tools/package_native.py` y sus componentes fijados. Usa el programa de rollback del [comparativo de lenguajes](https://github.com/WashingtonYandun/Wysna/tree/5882f7a7e4670790ef247283f90b4bb45d78051d/bench/cross_language/cases/wysna/ledger). Ejecuta desde la raíz del repositorio con Python 3.10 o superior.

```sh
python3 bench/native_hotpaths/run.py \
  --reference /ruta/sdk-referencia/bin/wysna \
  --candidate /ruta/sdk-candidato/bin/wysna \
  --ledger /ruta/al/ledger/main.wysna \
  --output bench/native_hotpaths/results-local.json
```

Cada fila refleja 30 mediciones por SDK después de tres calentamientos en orden alternado. Los tiempos incluyen el proceso y su salida. Cada ejecución verifica el código de retorno y la salida esperada. Una ejecución adicional por programa registra los contadores ARC con la comprobación de memoria activa. El runner usa entradas sintéticas y cero llamadas a modelos.
