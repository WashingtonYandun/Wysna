# SIMP-05 — Auditoría FFI y propuesta de catálogo declarativo

- **Estado del informe:** auditoría completada sobre la línea base; no implica implementación del generador propuesto.
- **Estado actual:** PR #172 cerró la discrepancia: 49 bindings host y 49 entradas de efectos, con verificación de aridades C/Python. El catálogo declarativo generador sigue como propuesta. `SIMP-R01` conserva su significado original: investigar herramientas Agent-First compartidas entre Seed y Selfhost.
- **Alcance auditado:** [`src/runtime/HostAbi.py`](../../../runtime/HostAbi.py), [`src/runtime/ForeignAbi.py`](../../../runtime/ForeignAbi.py), [`src/codegen/ForeignAbi.py`](../../../src/codegen/ForeignAbi.py), [`src/compiler/EffectModel.py`](../../../src/compiler/EffectModel.py), `runtime/wysna_host*.h`, `stdlib/*.wysna` y `selfhost/src/`.

## 1. Conteo histórico anterior al PR #172

| Capa / Archivo | Símbolos registrados | Responsabilidad |
| :--- | :---: | :--- |
| [`src/runtime/HostAbi.py`](../../../runtime/HostAbi.py#L1523-L1573) (`FUNCTIONS`) | **49** funciones `wysna_host_*` | Implementación Python de referencia para el Intérprete. |
| [`src/compiler/EffectModel.py`](../../../src/compiler/EffectModel.py#L73-L120) (`HOST_EFFECTS` / `RUNTIME_EFFECTS`) | **46** `HOST_EFFECTS` + **60** `RUNTIME_EFFECTS` | Conjunto de efectos estáticos para `--sandbox` / `--allow` / `--deny`. |
| [`stdlib/*.wysna`](../../../stdlib/) (`extern "C" fn wysna_host_*`) | **41** declaraciones únicas `wysna_host_*` | Firmas visibles al usuario en los módulos estándar. |
| [`selfhost/src/`](../../../selfhost/src/) | **21** referencias explícitas `wysna_host_*` | Uso directo del host runtime y mapeo en compilador nativo. |

### Discrepancia concreta detectada durante la auditoría

Al cruzar programáticamente `HostAbi.FUNCTIONS` (`49` entradas) contra `EffectModel.HOST_EFFECTS` (`46` entradas), faltan **3 funciones host** en `HOST_EFFECTS`:
1. `wysna_host_active_bytes`
2. `wysna_host_active_handles`
3. `wysna_host_fs_canonical`

*(Nota: estas 3 funciones existen en `HostAbi.FUNCTIONS`, pero si un programa las invocara directamente con `--sandbox`/`--deny`, activarían el mismo patrón `[W5006]` mitigado en `AGT-03`.)*

## 2. Propuesta de catálogo declarativo único (pendiente)

Un único archivo fuente de verdad (`runtime/host_catalog.json` o `tools/host_abi_spec.py`) declararía por cada símbolo host:
- `name`: ej. `"wysna_host_process_capture"`
- `params` y `return`: tipos ABI (`int`, `int32`, `float`, `bool`, `opaque`, `unit`)
- `ownership`: `"borrowed"` vs `"owned_pointer"` (elimina listas manuales como `('wysna_host_buffer_new', 'wysna_host_file_open')` en [`src/runtime/ForeignAbi.py:67`](../../../runtime/ForeignAbi.py#L67) y [`src/codegen/ForeignAbi.py:22`](../../../src/codegen/ForeignAbi.py#L22))
- `effects`: conjunto estático exacto consumido por `EffectModel.py` y `selfhost`
- `c_header`: archivo C destino y prototipo verificable

**Ahorro estimado:** Elimina la deriva de 4 tablas manuales (~`250` LOC de glue/verificación manual) y previene desincronizaciones como la brecha de 3 entradas hallada arriba mediante generación o aserción CI directa contra el catálogo único.
