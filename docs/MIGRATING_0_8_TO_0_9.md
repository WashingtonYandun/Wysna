# Migración de Wysna 0.8 a 0.9

La actualización es explícita: el compilador 0.9 no modifica automáticamente un
manifiesto ni un lock de 0.8. Conserva una copia de tu proyecto y verifica que
`wysna --version` indique `0.9.0` antes de regenerar artefactos.

## 1. Actualizar manifiestos y dependencias

En cada `wysna.toml` del proyecto y sus dependencias locales, cambia únicamente
las versiones de lenguaje y toolchain; la versión de tu paquete es independiente:

```toml
[package]
language = "0.9"

[toolchain]
compiler = "0.9.0"
stdlib = "bundled"
```

Un manifiesto 0.8 se rechaza con `W5002` y exit 2. Actualizarlo deja obsoleto el
lock anterior: `--frozen` también lo rechaza con `W5002`, sin sobrescribirlo.
Regenera y verifica deliberadamente:

```bash
wysna project lock --manifest wysna.toml
wysna project check --manifest wysna.toml --frozen --output-format=json-v1
wysna project test --manifest wysna.toml --frozen --output-format=json-v1
wysna project run --manifest wysna.toml --frozen
```

Si usas paquetes USTAR fijados por SHA-256, migra primero sus fuentes/manifiestos,
vuelve a ejecutar `packages pack`, adquiere el archivo con su nuevo pin y actualiza
la dependencia antes de `project lock`. No reutilices el SHA de un archivo modificado.
`packages` sigue siendo exclusivamente nativo, sin hooks ni resolución remota.

## 2. Usar la sintaxis canónica

- `new Counter(...)` pasa a `Counter(...)`.
- El opcional vacío se escribe `None`; `none` se rechaza.
- Los comodines de `match` se escriben `_ =>`; `* =>` y `else =>` se rechazan.
- Los patrones de tupla requieren paréntesis: `(left, right) =>`.
- Las capturas de closures son snapshots inmutables. Devuelve un valor actualizado
  cuando necesites transformar estado. Una clase puede mantener estado mutable
  dentro de un hilo; eso no concede seguridad para compartirla entre workers.

El código que ya usaba las formas canónicas conserva su semántica. W2003 mantiene
su código y añade `data.uncovered_pattern_families` para reparar matches incompletos.
Los consumidores JSON deben admitir los campos de metadatos añadidos a símbolos.

## 3. Anotaciones y herramientas

`@deprecated("mensaje")` y `@must_use` producen advertencias W6004/W6005. Los
esquemas propios son structs no genéricos aplicados a funciones de nivel superior:

```wysna
struct AgentTool { name: str, read_only: bool }
@AgentTool(name: "lookup", read_only: true)
@must_use
pub fn lookup(id: int) -> int { id }
```

Solo se aceptan campos nombrados con literales `str`, `bool` o Int64, sin campos
ausentes ni extra. Los metadatos no ejecutan código, no conceden permisos y no
convierten callbacks en funciones seguras. `api`, `inspect` y `schema` exponen
`attributes: [{name, arguments}]`.

Los comandos `project`, `packages`, `mcp`, `lsp` y `schema` usan el SDK nativo.
Seed conserva WASI/Browser y los oráculos del lenguaje. El SDK nativo indica W2013
cuando un destino requiere Seed; no se introduce soporte móvil implícito.

## Prueba de migración

`tests/fixtures/language_combinations/migration_v08` conserva un manifiesto y un lock generados
por el SDK 0.8 y probados con `project test --frozen`. La regresión de 0.9 verifica
en Seed y nativo el rechazo sin mutaciones y la regeneración del lock con entradas
LF y CRLF. La aceptación instalada del SDK ejecuta obligatoriamente el proyecto
migrado en los tres OS con Zig empaquetado. No se usan modelos ni servicios de IA para medirlo.
