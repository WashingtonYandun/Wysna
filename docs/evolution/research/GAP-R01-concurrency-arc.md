# GAP-R01 — Auditoría de Seguridad Multi-Hilo del Runtime ARC/CoW Actual

- **Estado:** Completado (`2026-11-21`)
- **Archivos inspeccionados:** [`runtime/wysna_runtime_core.h`](../../../runtime/wysna_runtime_core.h), [`runtime/wysna_runtime_memory.h`](../../../runtime/wysna_runtime_memory.h), [`runtime/wysna_host.h`](../../../runtime/wysna_host.h).

## 1. Hallazgo principal: el runtime ya tiene primitivos atómicos parciales, pero el contexto global NO es thread-safe

Actualización 2026-11-22: la rama `feat/v0.9-agent-contracts-annotations-patterns`
mueve ubicación y pila de diagnóstico a TLS, con una regresión que falla antes de
la corrección. Los demás puntos conservan su estado pendiente de reauditoría y
corrección; este cambio no cierra la seguridad multihilo completa. La secuencia
vigente está en [R1–R4](../13-RUNTIME-AGENT-FIRST.md).

Contrario a la suposición inicial de [`06-BRECHAS.md`](../06-BRECHAS.md) (`int rc` no atómico), la inspección directa del runtime C99 demuestra dos mitades asimétricas:

### Lo que SÍ es atómico / sincronizado hoy
1. **Contadores ARC (`refcount`, `strong`, `weak`) y contadores globales de telemetría:** [`runtime/wysna_runtime_core.h:88-120`](../../../runtime/wysna_runtime_core.h#L88-L120) ya envuelve incrementos y decrementos con `__atomic_load_n`, `__atomic_add_fetch`, `__atomic_sub_fetch` y `__atomic_compare_exchange_n` bajo Clang/GCC (y [`wysna_runtime_memory.h:52-94`](../../../runtime/wysna_runtime_memory.h#L52-L94) usa esas macros).
2. **Lista púrpura de recolección de ciclos (`purple_head`):** [`wysna_runtime_memory.h:11,30`](../../../runtime/wysna_runtime_memory.h#L11-L43) protege las mutaciones de la lista doblemente enlazada con `wysna_spinlock_acquire(&wysna_runtime_context.runtime_lock)`.

### Lo que IMPIDE concurrencia multi-hilo compartida segura hoy (data races / UB)
1. **Contexto global singleton sin `_Thread_local`:** [`wysna_runtime_core.h:177-224`](../../../runtime/wysna_runtime_core.h#L177-L224) declara un único `static WysnaRuntimeContext wysna_runtime_context = {0}`. Los siguientes campos se leen/escriben sin atomicidad ni aislamiento por hilo:
   - `source_file`, `source_line`, `source_col`
   - `call_stack_fn[64]`, `call_stack_file[64]`, `call_stack_line[64]`, `call_depth` (los stack traces de `DX-05` se corromperían entre hilos concurrentes)
   - `collecting_cycles`, `quotas_initialized`, `string_literals`
2. **Registro de recursos host sin mutex:** [`runtime/wysna_host.h:60-120`](../../../runtime/wysna_host.h#L60-L120) gestiona `wysna_host_entries` (lista enlazada simple), `next_id`, `error_code` y `error_domain` en un struct global único `wysna_host_context` sin `pthread_mutex` ni TLS.
3. **CoW `detach` + mutación en contenedores mutables (`list`, `dict`):** Si dos hilos clonan el mismo `WListBuf` (`rc = 2`) y leen metadatos (`len`, `buckets`) o ejecutan `detach` simultáneamente sin atomicidad en `d->buckets` / `d->len`, puede haber *use-after-free* cuando el último hilo decremente tras leer `rc`. (Igualmente `st->control->color = W_COLOR_BLACK` en `wysna_clone` línea `78` es escritura no atómica).

## 2. Opciones de arquitectura y recomendación

| Opción | Cambios necesarios en el runtime C99 | Costo / Riesgo | Veredicto |
| :--- | :--- | :---: | :--- |
| **A. Workers aislados por hilo (actor / `Send` por copia o transferencia única) + `TLS`** | Marcar `wysna_runtime_context` y `wysna_host_context` como `_Thread_local` (o pasarlos por parámetro), prohibir compartir objetos mutables entre hilos, y transferir mensajes vía canales serializados o buffers con `rc == 1` único. | **Bajo-Moderado** | **Recomendada.** Aprovecha los contadores ARC ya existentes sin meter locks por lectura/escritura en cada acceso de lista/dict. |
| **B. Memoria compartida estilo Go/Java sobre el heap actual** | Añadir locks/RW-locks por `WListBuf`/`WDictBuf`/`WStructBuf`, hacer `wysna_collect_cycles()` *stop-the-world* multi-hilo y proteger `wysna_host_entries` con mutex global. | **Muy Alto** | **Descartada:** Destruye el rendimiento single-thread (`-3.3x..102x` vs Python) y complica enormemente `CoW` y ciclos. |
