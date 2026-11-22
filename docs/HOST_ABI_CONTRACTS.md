# Contratos host: efectos y ownership

El catálogo `src/compiler/EffectModel.py::HOST_EFFECTS` debe cubrir todas las funciones expuestas por `runtime/HostAbi.py::FUNCTIONS`. La prueba `test_every_host_binding_has_an_effect_contract_and_matching_c_arity` compara ambos conjuntos y la aridad posicional Python/C de cada símbolo. Los parámetros auxiliares Python que solo admiten nombre no forman parte de la ABI.

## Contratos completados para v0.8.0

- `wysna_host_active_handles() -> int`: inspecciona el registro de recursos. Efectos `resource.inspect` y `host-state.read`. No adquiere ni libera recursos y no necesita capacidad de filesystem.
- `wysna_host_active_bytes() -> int`: inspecciona los buffers registrados. Mismos efectos y ausencia de transferencia de ownership. Es telemetría del adaptador; esta versión no cambia su definición ni equipara capacidad reservada nativa con longitud lógica del intérprete.
- `wysna_host_fs_canonical(path: opaque, output: opaque) -> int`: toma prestados ambos handles. Lee filesystem y el buffer de entrada, amplía el buffer de salida, utiliza memoria temporal y actualiza el estado de error host. Devuelve longitud escrita o `-1`; no devuelve un handle propietario ni cierra los argumentos. La lectura de filesystem se conserva en el análisis. Los efectos de memoria y potencial fallo de asignación se modelan conservadoramente.

La prueba de runtime repite resolución válida y rechazo de un buffer de salida no vacío. Comprueba que sobreviven los dos handles prestados, los cierra explícitamente y exige volver al número inicial de handles y bytes. Se ejecuta en los cuatro motores y con ASan/UBSan nativos en POSIX.

## Confianza y políticas

Registrar un nombre no certifica una declaración FFI de usuario. `CompilerService._foreign_contracts` exige procedencia de la biblioteca estándar instalada; la política sigue rechazando declaraciones externas no certificadas antes de enlazarlas o ejecutarlas.

La telemetría modelada no añade capacidades externas. La resolución canónica conserva `filesystem.read`; no se etiqueta como escritura de archivos. Las posibles finalizaciones de valores opacos pueden mantener incompleto el análisis y provocar rechazo conservador de `--allow/--deny`. Este cambio no concede pureza ni completa artificialmente ese análisis.

La cobertura de 49 símbolos es cobertura del catálogo y de aridades. Las pruebas de ownership aquí descritas corresponden a los tres contratos completados, junto con las regresiones FFI y de recursos existentes; no constituyen una prueba universal de toda interacción FFI.
