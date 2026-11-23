# SDK Linux aislado para agentes

El lanzador ejecuta un comando del SDK nativo Linux con Docker. El SDK y el
proyecto se exponen de solo lectura. El compilador y el programa comparten el
mismo contenedor desechable. Se necesita un motor Docker Linux y Python 3.11+
para el lanzador del host. El contenedor contiene el SDK estático y su Zig
empaquetado.

## Uso

Desde el checkout, extrae el SDK Linux verificado en `installed/wysna-native`.
Construye la imagen local con ese directorio como contexto. El Dockerfile usa
`scratch` y el build no necesita descargar una imagen base.

```sh
image="$(docker build --quiet --network=none --file packaging/container/Dockerfile installed/wysna-native)"
python3 tools/run_isolated.py --image "$image" --project ./my-project -- check /project/src/main.wysna
python3 tools/run_isolated.py --image "$image" --project ./my-project -- build /project/src/main.wysna -o /work/program
python3 tools/run_isolated.py --image "$image" --project ./my-project --timeout 180 -- run /project/src/main.wysna
```

`--project` concede lectura al directorio elegido. Prepara un directorio con las
fuentes, dependencias bloqueadas e inputs requeridos para la tarea. Coloca
salidas temporales en `/work`. La fase de build verifica el binario dentro del
contenedor y el lanzador elimina ese espacio al terminar.

## Límites y resultados

- El SDK corre como el usuario Linux `65532`, con capacidades Linux eliminadas,
  raíz de solo lectura y sin elevación de privilegios. El proyecto está en
  `/project`. Los montajes anidados del host quedan fuera.
- Corre con red deshabilitada y sin reenviar credenciales de proxy. Los procesos
  internos y los hilos comparten un límite de 128 PIDs y 1 GiB de RAM. El motor
  aplica un máximo equivalente a una CPU. Las únicas áreas de trabajo escribibles
  son montajes temporales limitados.
- El resultado JSON `wysna.isolated-run/1` incluye `status`, `exit_code`,
  `container_removed` y los primeros 64 KiB de cada flujo de salida. Agotamiento
  de memoria y timeout tienen estados propios. Un timeout termina el contenedor
  completo y produce código 124. Un fallo al eliminarlo produce
  `cleanup_failed` e identifica el contenedor.
- El presupuesto de tiempo predeterminado incluye el arranque en frío de Zig.
  Cada invocación usa una caché temporal propia.
- El motor Docker, la imagen y el kernel pertenecen a la frontera de confianza.
  Para aislar tenantes mutuamente hostiles, ejecuta el perfil sobre una VM
  dedicada. En un daemon remoto, `--project` se interpreta en el filesystem del
  daemon. Utiliza un daemon local al preparar el directorio.
- El perfil está pensado para programas compatibles con el SDK Linux offline.
  Las capacidades Wysna de `--sandbox` pueden añadirse como control de aplicación
  en comandos compatibles. La compilación y la ejecución quedan dentro de la
  misma frontera del contenedor.

CI construye la imagen con el archivo Linux del SDK y comprueba `check`, `build`,
`run`, directorios de solo lectura, ausencia de un valor de entorno del host
y eliminación del contenedor al vencer el deadline.
