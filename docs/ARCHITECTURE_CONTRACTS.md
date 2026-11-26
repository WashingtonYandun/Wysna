# Architecture contracts

Wysna can verify direct dependencies between classes in a target. Declare roles in code and constraints in `wysna.toml`. Both Seed and the native SDK validate against the identities resolved by the compiler.

## Declaring roles

Define an attribute schema in a project file. The `role` field must have type `str`. Classes can import the schema with an alias.

```wysna
from .architecture use Architecture as Layer

@Layer(role: "Controller")
pub class InvoiceController {}
```

The schema in this example is `pub struct Architecture { role: str }`. Class attributes are static metadata checked by the compiler against the schema. Subclasses must declare their own metadata.

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

- `calls` inspects resolved target declarations. The class defining the called method provides its role.
- `uses_type` inspects direct type references, including fields, parameters, return types, and constructions. The rule applies to classes inside `sources.root`.
- The schema path is resolved from the `wysna.toml` root and must belong to the modules loaded by the target. An unused import appears in the graph and does not create a role dependency.
- Policy rules are evaluated per target. A `Controller → Service → Repository` chain satisfies the direct rule in the example above.

## Verifying and querying results

```sh
wysna project check --manifest examples/architecture/wysna.toml --output-format=json-v1
wysna graph --manifest examples/architecture/wysna.toml --role Repository --output-format=json-v1
wysna inspect examples/architecture/src/main.wysna --symbol InvoiceController --output-format=json-v1
```

Pass `--target` to `project check` or `graph --role` to select another target. In MCP, `wysna_project_check` accepts `manifest` and an optional `target`, and `wysna_graph` accepts `manifest`, `role`, and an optional `target`. Inspecting a class reveals the attribute's local name, its arguments, and the resolved schema path.

`result.architecture` emits `wysna.architecture/1` with the status of each rule:

- `verified` indicates that the target scope was completely checked.
- `violation` emits `W6101` when a resolved edge contradicts a rule.
- `incomplete` emits `W6102` when relevant evidence is missing. `diagnostics[].data.boundary` distinguishes a missing schema, an unknown role, an unclassified project destination, and a relevant indirect or virtual call. FFI calls remain outside Wysna targets.

Errors carry `rule_id`, relation, identities, and roles, along with the usage span and, when a target symbol is known, its declaration span. `graph_hash` identifies the checked sources, and `revision` binds that hash to the manifest contents for that evaluation.

## Validating edits before writing

```sh
wysna patch --manifest examples/architecture/wysna.toml --edits edits.json --output-format=json-v1
wysna patch --manifest examples/architecture/wysna.toml --edits edits.json --expect-plan-hash HASH --apply --output-format=json-v1
```

`patch` and `wysna_patch` verify targets against the in-memory edit set. Either a violation or an incomplete check blocks application. If the manifest or any annotation changes between preview and apply, a fresh preview with the updated hash is required.

The [reference project](../examples/architecture/wysna.toml) contains the valid chain. The [integration test suite](../tests/unit/test_architecture_contracts.py) verifies that valid chain, direct access rejection, forbidden type references, indirect calls, CLI, MCP, and hash-guarded patch application.
