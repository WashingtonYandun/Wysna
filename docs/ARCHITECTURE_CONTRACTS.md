# Contratos de arquitectura

Wysna puede comprobar dependencias directas entre clases de un objetivo. Declara
los roles en código y las restricciones en `wysna.toml`. Seed y el SDK nativo
utilizan las identidades que resuelve el compilador.

## Declarar roles

Define un esquema de atributo en un archivo del proyecto. El campo `role` debe
ser `str`. Las clases pueden importar el esquema con alias.

```wysna
from .architecture use Architecture as Layer

@Layer(role: "Controller")
pub class InvoiceController {}
```

El esquema del ejemplo es `pub struct Architecture { role: str }`. Los
atributos de clase son metadatos estáticos. El compilador comprueba sus
argumentos contra el esquema. Una subclase declara sus propios metadatos.

```toml
[architecture]
role_attribute = "src/architecture.wysna::Architecture"
roles = ["Controller", "Service", "Repository"]

[[architecture.rules]]
id = "controller-no-repository"
from_role = "Controller"
relations = ["calls", "uses_type"]
deny_roles = ["Repository"]
severity = "error"
```

- `calls` examina declaraciones de destino resueltas. La clase que declara el
  método aporta su rol.
- `uses_type` examina referencias directas a tipos, entre ellas campos,
  parámetros, retornos y construcciones. La regla se aplica a clases dentro
  de `sources.root`.
- La ruta del esquema se interpreta desde la raíz de `wysna.toml`. El
  esquema debe estar en el conjunto de módulos que carga el objetivo. Un
  import sin uso aparece en el grafo y no crea una dependencia entre roles.
- La política se evalúa por objetivo. Una cadena
  `Controller → Service → Repository` cumple la regla directa del ejemplo.

## Verificar y consultar el resultado

```sh
wysna project check --manifest examples/architecture/wysna.toml --output-format=json-v1
wysna graph --manifest examples/architecture/wysna.toml --role Repository --output-format=json-v1
wysna inspect examples/architecture/src/main.wysna --symbol InvoiceController --output-format=json-v1
```

Agrega `--target` a `project check` o `graph --role` para elegir otro objetivo.
En MCP, `wysna_project_check` recibe `manifest` y un `target` opcional.
`wysna_graph` acepta `manifest`, `role` y un `target` opcional. Inspeccionar la
clase revela el nombre local del atributo, sus argumentos y la ruta del esquema
resuelto.

`result.architecture` emite `wysna.architecture/1` con el estado de cada regla.

- `verified` indica que se comprobó el alcance del objetivo.
- `violation` emite `W6101` cuando una arista resuelta contradice la regla.
- `incomplete` emite `W6102` cuando falta evidencia relevante. La propiedad
  `diagnostics[].data.boundary` distingue un esquema ausente, un rol
  desconocido, un destino del proyecto sin clasificar y una llamada indirecta
  o virtual pertinente. Las llamadas FFI quedan fuera de los destinos Wysna.

Los errores llevan `rule_id`, relación, identidades y roles. También incluyen
la ubicación del uso y, cuando hay un destino conocido, la declaración
relacionada. `graph_hash` identifica las fuentes comprobadas. `revision`
vincula ese hash con el contenido del manifiesto para esta evaluación.

## Validar cambios antes de escribir

```sh
wysna patch --manifest examples/architecture/wysna.toml --edits edits.json --output-format=json-v1
wysna patch --manifest examples/architecture/wysna.toml --edits edits.json --expect-plan-hash HASH --apply --output-format=json-v1
```

`patch` y `wysna_patch` comprueban los objetivos con el conjunto de cambios
en memoria. Una violación o una comprobación inconclusa bloquea el lote. Si se
edita el manifiesto o una anotación entre previsualizar y aplicar, se requiere
una nueva previsualización con el hash actualizado.

El [proyecto de referencia](../examples/architecture/wysna.toml) contiene la
cadena válida. La [suite de integración](../tests/unit/test_architecture_contracts.py)
prueba esa cadena, el acceso directo, una referencia de tipo prohibida y una
llamada indirecta, además de CLI, MCP y la publicación mediante hash.
