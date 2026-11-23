# Grupos de tareas de CPU

`std/async.run_group` ejecuta una función por entrada con concurrencia acotada
y devuelve los resultados por índice. El grupo espera a sus workers antes de
regresar. Usa los mismos snapshots de entrada, captura y resultado que
[`parallel_map`](WORKER_SAFETY.md), y aplica W2014 y W2015 al callback.

```wysna
import "std/async" as tasks

fn square(value: int) -> int ! str {
    if value < 0 { return Err("negative input") }
    return value * value
}

let outcomes = tasks.run_group([2, -1, 3], 2, 1000, false, square) else error { [] }
print(outcomes)
```

El segundo argumento limita los callbacks activos. El pool usa hasta cuatro
workers y puede usar menos por plataforma o por `WYSNA_PARALLEL_THREADS`. El
tercer argumento es un presupuesto en milisegundos desde el comienzo del
grupo. `0` vence antes de iniciar un callback y `-1` omite el plazo. Una
concurrencia menor que uno o un plazo menor que `-1` devuelve
`TaskGroupError`.

Cada índice termina como `Completed(T)`, `Failed(E)`, `Cancelled` o
`DeadlineExceeded`. Con `fail_fast = false` se conservan los errores tipados
de cada callback. Con `fail_fast = true`, el primer error observado se
conserva y solicita la cancelación de lo pendiente. Una tarea puede solicitar
el cierre de su grupo con `cancel_group()`. El plazo y la cancelación se
propagan a la cola y a los callbacks activos. Un callback largo debe consultar
`group_cancelled()` en sus puntos de interrupción y volver. Los resultados
que ya se publicaron se conservan. Si la salida del callback observa el grupo
detenido, se registra el motivo de detención para ese índice.

El orden de la lista es estable. El conjunto de callbacks que alcanzan a
terminar antes del cierre depende de cuándo se observa el error o el plazo.
Un trap conserva su semántica fatal. Los callbacks cooperativos mantienen la
semántica de propiedad aislada, y el perfil Linux aislado puede imponer un
límite externo a un programa completo. Las tareas de I/O continúan en el
scheduler cooperativo existente.
