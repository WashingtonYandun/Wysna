"""Versioned, self-contained JSON interchange for verified MIR modules."""
from dataclasses import fields, is_dataclass
import json

from error.Error import WysnaError
from checker.TypeInterner import TypeInterner
from mir.Nodes import (
    BasicBlock, Function, Layout, Module, Operation, Place, Projection, Slot, Source, Terminator,
)


FORMAT = "wysna.mir"
SCHEMA_VERSION = 1
SEMANTIC_REVISION = "0.5.0"
RUNTIME_ABI_VERSION = 2

_NODE_TYPES = {
    item.__name__: item
    for item in (BasicBlock, Function, Layout, Module, Operation, Place, Projection, Slot, Source, Terminator)
}


def _fail(message):
    raise WysnaError(message, file="<mir-exchange>", line=1, col=1,
                     code="W9999", stage="mir-exchange")


def _encode(value):
    if is_dataclass(value):
        return {
            "$type": type(value).__name__,
            **{field.name: _encode(getattr(value, field.name)) for field in fields(value)},
        }
    if isinstance(value, tuple):
        return {"$tuple": [_encode(item) for item in value]}
    if isinstance(value, list):
        return [_encode(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _encode(item) for key, item in value.items()}
    if value is None or type(value) in (str, int, float, bool):
        return value
    _fail(f"Unsupported MIR exchange value: {type(value).__name__}")


def _decode(value):
    if isinstance(value, list):
        return [_decode(item) for item in value]
    if isinstance(value, dict):
        if set(value) == {"$tuple"}:
            items = value["$tuple"]
            if not isinstance(items, list):
                _fail("Malformed tuple in MIR exchange payload")
            return tuple(_decode(item) for item in items)
        if "$type" in value:
            type_name = value.get("$type")
            constructor = _NODE_TYPES.get(type_name)
            if constructor is None:
                _fail(f"Unknown MIR exchange node type: {type_name!r}")
            expected = {field.name for field in fields(constructor)}
            if set(value) != expected | {"$type"}:
                _fail(f"Malformed MIR exchange node: {type_name}")
            decoded = {key: _decode(item) for key, item in value.items() if key != "$type"}
            try:
                return constructor(**decoded)
            except (TypeError, ValueError) as error:
                _fail(f"Invalid MIR exchange node {type_name}: {error}")
        return {key: _decode(item) for key, item in value.items()}
    if value is None or type(value) in (str, int, float, bool):
        return value
    _fail(f"Unsupported decoded MIR exchange value: {type(value).__name__}")


def _module_type_ids(module):
    """Yield TypeIds referenced by the MIR module's public data structures."""
    for slot in module.globals:
        yield slot.type_id
    for function in module.functions:
        yield function.return_type
        for slot in function.slots:
            yield slot.type_id
        for block in function.blocks:
            for operation in block.operations:
                yield from _data_type_ids(operation.data)
    for layout in module.layouts.values():
        yield layout.type_id
        for _, type_id in layout.fields:
            yield type_id
        for method in layout.methods.values():
            if isinstance(method, (list, tuple)) and len(method) == 3:
                _, parameters, returned = method
                yield from parameters
                yield returned
    for external in module.externs.values():
        if isinstance(external, dict):
            yield from external.get("params", ())
            if "return_type" in external:
                yield external["return_type"]


def _data_type_ids(value, key=None):
    if isinstance(value, Place):
        for projection in value.projections:
            yield projection.owner_type
            yield projection.type_id
        return
    if isinstance(value, Projection):
        yield value.owner_type
        yield value.type_id
        return
    if isinstance(value, dict):
        for child_key, child in value.items():
            if child_key in ("type_id", "owner_type", "error_type_id"):
                yield child
            else:
                yield from _data_type_ids(child, child_key)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _data_type_ids(child, key)


def _validate_runtime_types(type_ids, interner):
    visited = set()

    def visit(type_id):
        if type(type_id) is not int or interner.get_entry(type_id).tag == "error":
            _fail(f"MIR references an invalid TypeId: {type_id!r}")
        if type_id in visited:
            return
        visited.add(type_id)
        entry = interner.get_entry(type_id)
        if entry.tag == "type_param":
            _fail(f"MIR references unresolved generic type parameter: {interner.to_string(type_id)}")
        children = []
        if entry.tag in ("optional", "weak", "list"):
            children = [entry.data]
        elif entry.tag in ("result", "dict", "tuple", "error_union"):
            children = list(entry.data)
        elif entry.tag == "function":
            params, returned, _ = entry.data
            children = [*params, returned]
        elif entry.tag == "struct":
            definition = interner.struct_defs.get(entry.data)
            children = list(definition.fields.values()) if definition else []
        elif entry.tag == "enum":
            definition = interner.enum_defs.get(entry.data)
            if definition:
                children = [*definition.type_args]
                children.extend(item for payload in definition.variant_payloads.values() for item in payload)
        elif entry.tag == "interface":
            definition = interner.interface_defs.get(entry.data)
            children = list(definition.methods.values()) if definition else []
        elif entry.tag == "class":
            definition = interner.class_defs.get(entry.data)
            if definition:
                children = [type_id for type_id, _ in definition.fields.values()]
                children.extend(type_id for type_id, _ in definition.methods.values())
                children.extend(definition.interfaces)
                if definition.superclass is not None:
                    children.append(definition.superclass)
                if definition.init_sig is not None:
                    children.append(definition.init_sig)
        for child in children:
            visit(child)

    for type_id in type_ids:
        visit(type_id)


def export_module(module: Module, interner: TypeInterner) -> str:
    """Serialize a verified MIR module with its complete type arena."""
    from mir.Verifier import Verifier

    _validate_runtime_types(_module_type_ids(module), interner)
    Verifier(interner).verify(module)
    envelope = {
        "format": FORMAT,
        "schema_version": SCHEMA_VERSION,
        "semantic_revision": SEMANTIC_REVISION,
        "runtime_abi_version": RUNTIME_ABI_VERSION,
        "type_arena": interner.to_snapshot(),
        "module": _encode(module),
    }
    return json.dumps(envelope, indent=2, sort_keys=True) + "\n"


def import_module(payload: str):
    """Load and verify a MIR module, returning ``(module, interner)``."""
    try:
        envelope = json.loads(payload)
    except (TypeError, json.JSONDecodeError) as error:
        _fail(f"Invalid MIR exchange JSON: {error}")
    if not isinstance(envelope, dict) or envelope.get("format") != FORMAT:
        _fail("Unsupported MIR exchange format")
    if envelope.get("schema_version") != SCHEMA_VERSION:
        _fail(f"Unsupported MIR schema version: {envelope.get('schema_version')!r}")
    if envelope.get("semantic_revision") != SEMANTIC_REVISION:
        _fail(f"Unsupported Wysna semantic revision: {envelope.get('semantic_revision')!r}")
    if envelope.get("runtime_abi_version") != RUNTIME_ABI_VERSION:
        _fail(f"Unsupported runtime ABI version: {envelope.get('runtime_abi_version')!r}")
    try:
        interner = TypeInterner.from_snapshot(envelope.get("type_arena"))
    except (TypeError, ValueError, KeyError) as error:
        _fail(f"Invalid MIR type arena: {error}")
    module = _decode(envelope.get("module"))
    if not isinstance(module, Module):
        _fail("MIR exchange payload must contain a Module")
    layouts = {}
    for key, layout in module.layouts.items():
        try:
            type_id = int(key)
        except (TypeError, ValueError):
            _fail(f"Invalid MIR layout TypeId: {key!r}")
        if type_id in layouts:
            _fail(f"Duplicate MIR layout TypeId: {type_id}")
        layouts[type_id] = layout
    module.layouts = layouts
    module.arena_id = interner.arena_id
    _validate_runtime_types(_module_type_ids(module), interner)
    from mir.Verifier import Verifier
    Verifier(interner).verify(module)
    return module, interner
