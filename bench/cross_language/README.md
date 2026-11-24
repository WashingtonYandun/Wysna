# Comparativo de ejecución y compilación

Este directorio contiene los programas, el runner, las muestras y el [informe HTML](report.html). Descarga el HTML o abre la copia del repositorio con tu navegador. Las tablas se pueden leer sin red.

## Medición del 24 de noviembre de 2026

Apple M3, macOS ARM64. Se registraron 1.050 procesos y se pasaron 56 comprobaciones previas. Los dos SDK de Wysna se midieron en la misma ronda.

- **JSON con 10.000 registros.** 186,08 → 16,25 ms. RSS pico mediano de 5,05 a 4,80 MiB.
- **Recompilación del CLI editado.** 366,90 → 137,42 ms.
- **Serialización de Unicode sin escapes.** 6,02 → 2,56 ms al comparar con el SDK del PR #189.
- **Arranque y tamaño de CLI.** 1,91 → 2,01 ms. Binario release de 67,5 a 88,3 KiB.
- **Rollback grande.** 9,07 → 9,43 ms. Go y Rust registraron 5,11 y 4,13 ms.
- **Java.** 24,43 ms de arranque de JVM, 67,76 ms en JSON grande y 41,98 ms en rollback grande.

Los archivos [results.json](results.json) y [encoding.json](encoding.json) contienen las muestras y las revisiones exactas.

## Reproducción

En macOS ARM64 compara dos SDK nativos de Wysna junto a Go, Rust, Python, JavaScript y Java. Para repetir el protocolo instala Python 3.11 o superior, Node.js, el JDK, Go, Rust con Cargo y el SDK de Wysna. Usa las versiones del informe para repetir la medición en condiciones comparables.

Para Java necesitas [Jackson Core 2.21.5](https://repo.maven.apache.org/maven2/com/fasterxml/jackson/core/jackson-core/2.21.5/jackson-core-2.21.5.jar). El runner valida su SHA-256 y usa el JAR local. El paquete ocupa espacio aparte de los archivos `.class`, y sólo se carga en la prueba de JSON.

Ejecuta desde la raíz del repositorio.

```sh
cargo fetch --locked --manifest-path bench/cross_language/cases/rust/json/Cargo.toml
python3 bench/cross_language/run.py \
  --wysna /ruta/al/sdk-nuevo/bin/wysna \
  --baseline-wysna /ruta/al/sdk-0.9.0/bin/wysna \
  --jackson /ruta/al/jackson-core-2.21.5.jar \
  --output bench/cross_language/results-local.json
python3 bench/cross_language/render.py bench/cross_language/results-local.json \
  --output bench/cross_language/report-local.html
```

`--baseline-wysna` añade el SDK anterior a la misma ronda de medición. Las opciones `--go`, `--rustc`, `--cargo`, `--node`, `--java` y `--javac` admiten rutas explícitas. `--cargo-home` permite seleccionar una caché de Cargo aislada. El runner compila Rust en modo offline una vez descargadas las dependencias. Cada ejecución comprueba la salida y el código de retorno antes de guardar métricas.

## Qué mide

- **Arranque.** Lanza un proceso, escribe `ready` y termina.
- **JSON.** Lee el mismo array con 1.000 o 10.000 objetos y devuelve el número de registros aceptados y rechazados y la suma de unidades aceptadas. Cada objeto tiene dos enteros y un booleano.
- **Rollback.** Confirma un lote en memoria y rechaza otro de igual tamaño con un duplicado al final. Los dos tamaños suman 2.000 y 40.000 intentos de operación.
- **Compilación.** Registra una compilación fría del programa JSON y cinco compilaciones del CLI mínimo tras editar un literal. En Java se mide `javac`, en Python `py_compile` y en JavaScript `node --check`.

La latencia de ejecución incluye el arranque de cada proceso y la salida estándar. Java se mide como CLI de JVM nueva. Rust, Go y Wysna se miden como procesos nativos nuevos. El runner ejecuta las variantes en serie con un orden rotado y registra la memoria residente pico del proceso. Una compilación fría sirve como dato descriptivo.

Los fixtures del benchmark son sintéticos y se regeneran con el mismo código. Las medidas corresponden al hardware y perfil del informe. Las pruebas negativas comprueban JSON truncado, errores de tipo y tamaño de lote inválido. Los tests de conformidad de Wysna conservan los límites de entrada, nodos y profundidad, Unicode, y el overflow de enteros.

El comparativo se ejecuta con cero llamadas a modelos.

## Serialización de JSON

El experimento de serialización confronta el SDK del PR #189 y el SDK con la mejora de escapes. Usa dos entradas Unicode, una con escapes repartidos por el texto. Se comprueba la salida exacta y se guardan las muestras en `encoding.json`.

```sh
python3 bench/cross_language/encode.py \
  --reference /ruta/al/sdk-pr189/bin/wysna \
  --candidate /ruta/al/sdk-nuevo/bin/wysna \
  --output bench/cross_language/encoding-local.json
python3 bench/cross_language/render.py bench/cross_language/results-local.json \
  --encoding bench/cross_language/encoding-local.json \
  --output bench/cross_language/report-local.html
```

Para medir lectura, decodificación y agregación del SDK por etapas, ejecuta
`python3 bench/cross_language/json_stages.py --wysna /ruta/al/sdk/bin/wysna --output json-stages-local.json`.
