"""Generate JSON Schema (Draft 2020-12) and LLM Tool Definitions from Wysna types and functions.

Bridges Wysna's nominal type system (`struct`, `enum`, `T?`, `T ! E`) and verified MIR
effect analysis (`wysna.effects-model/1`) directly into machine-consumable schemas for
structured outputs and agent tool-calling without external schema libraries.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

from common.Version import WYSNA_VERSION
from compiler.CompilerService import CompilerService
from error.Error import WysnaError
from node.NodeType import EnumDecl, FnDecl, Node, StructDecl
from tooling.ApiIndex import _leading_documentation, _type_name
from tooling.ToolResult import (
    diagnostic_from_error,
    diagnostic_record,
    tool_result,
    write_tool_result,
)

JSON_SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"
_READ_ONLY_CAPABILITIES = frozenset({"arguments", "environment", "clock", "filesystem.read"})


class _Parser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def _type_node_to_schema(node, known_defs):
    """Translate a Wysna AST type annotation node into a JSON Schema Draft 2020-12 object."""
    if node is None:
        return {}
    kind = type(node).__name__
    if kind in ("NamedType", "PrimitiveType"):
        name = node.name
        if name == "int":
            return {"type": "integer"}
        if name == "u8":
            return {"type": "integer", "minimum": 0, "maximum": 255}
        if name == "float":
            return {"type": "number"}
        if name == "bool":
            return {"type": "boolean"}
        if name == "str":
            return {"type": "string"}
        if name in ("unit", "none"):
            return {"type": "null"}
        if name in known_defs:
            return {"$ref": f"#/$defs/{name}"}
        return {"x-wysna-type": name, "x-wysna-unrepresentable": True}
    if kind == "OptionalType":
        return {"anyOf": [_type_node_to_schema(node.inner, known_defs), {"type": "null"}]}
    if kind == "ListType":
        return {"type": "array", "items": _type_node_to_schema(node.element_type, known_defs)}
    if kind == "DictType":
        schema = {
            "type": "object",
            "additionalProperties": _type_node_to_schema(node.value_type, known_defs),
        }
        key_name = _type_name(node.key_type)
        if key_name != "str":
            schema["x-wysna-key-type"] = key_name
        return schema
    if kind == "TupleType":
        items = [_type_node_to_schema(item, known_defs) for item in node.element_types]
        return {
            "type": "array",
            "prefixItems": items,
            "minItems": len(items),
            "maxItems": len(items),
        }
    if kind == "ResultType":
        return {
            "oneOf": [
                {
                    "type": "object",
                    "properties": {"ok": _type_node_to_schema(node.ok_type, known_defs)},
                    "required": ["ok"],
                    "additionalProperties": False,
                },
                {
                    "type": "object",
                    "properties": {"err": _type_node_to_schema(node.err_type, known_defs)},
                    "required": ["err"],
                    "additionalProperties": False,
                },
            ]
        }
    if kind == "ErrorUnionType":
        return {"oneOf": [_type_node_to_schema(item, known_defs) for item in node.error_types]}
    if kind == "GenericType":
        if node.name in known_defs:
            return {"$ref": f"#/$defs/{node.name}", "x-wysna-type": _type_name(node)}
        return {"type": "object", "x-wysna-type": _type_name(node)}
    raw_name = _type_name(node)
    if raw_name in known_defs:
        return {"$ref": f"#/$defs/{raw_name}"}
    return {"x-wysna-type": raw_name}


def _extract_numeric_literal(node):
    kind = type(node).__name__
    if kind in ("IntLiteral", "FloatLiteral"):
        return node.value
    if kind == "UnaryExpr" and getattr(node, "op", None) == "-":
        inner = _extract_numeric_literal(node.operand)
        if inner is not None:
            return -inner
    return None


def _extract_target_and_kind(node):
    """Return (target_name, 'value' | 'len') if node refers to an identifier or len(identifier)."""
    kind = type(node).__name__
    if kind == "IdentExpr":
        return node.name, "value"
    if kind == "MemberExpr":
        obj_kind = type(node.object).__name__
        if obj_kind == "ThisExpr" or (obj_kind == "IdentExpr" and node.object.name == "self"):
            return node.property, "value"
    if kind == "CallExpr":
        callee = node.callee
        args = getattr(node, "args", getattr(node, "arguments", ()))
        if type(callee).__name__ == "IdentExpr" and callee.name == "len" and len(args) == 1:
            inner_target, inner_kind = _extract_target_and_kind(args[0])
            if inner_target is not None and inner_kind == "value":
                return inner_target, "len"
    return None, None


_FLIP_OP = {">": "<", ">=": "<=", "<": ">", "<=": ">=", "==": "=="}


def _apply_expr_to_schemas(expr, prop_schemas):
    kind = type(expr).__name__
    if kind == "BinaryExpr":
        op = getattr(expr, "op", getattr(expr, "operator", None))
        if op == "and":
            _apply_expr_to_schemas(expr.left, prop_schemas)
            _apply_expr_to_schemas(expr.right, prop_schemas)
            return
        if op in _FLIP_OP:
            target, tkind = _extract_target_and_kind(expr.left)
            bound = _extract_numeric_literal(expr.right)
            if target is None or bound is None:
                target, tkind = _extract_target_and_kind(expr.right)
                bound = _extract_numeric_literal(expr.left)
                op = _FLIP_OP[op]
            if target is not None and bound is not None and target in prop_schemas:
                schema = prop_schemas[target]
                if not isinstance(schema, dict):
                    return
                if tkind == "value":
                    if op == ">=":
                        schema["minimum"] = bound
                    elif op == ">":
                        schema["exclusiveMinimum"] = bound
                    elif op == "<=":
                        schema["maximum"] = bound
                    elif op == "<":
                        schema["exclusiveMaximum"] = bound
                elif tkind == "len" and isinstance(bound, int):
                    stype = schema.get("type")
                    min_key = "minLength" if stype == "string" else ("minItems" if stype == "array" else None)
                    max_key = "maxLength" if stype == "string" else ("maxItems" if stype == "array" else None)
                    if min_key and op == ">=":
                        schema[min_key] = bound
                    elif min_key and op == ">":
                        schema[min_key] = bound + 1
                    elif max_key and op == "<=":
                        schema[max_key] = bound
                    elif max_key and op == "<" and bound > 0:
                        schema[max_key] = bound - 1


def _rewrite_contract_field_refs(expr, field_names):
    from copy import deepcopy
    from node.NodeType import CallExpr, IdentExpr, MemberExpr, Node, ThisExpr

    cloned = deepcopy(expr)

    def _transform(node):
        if isinstance(node, MemberExpr):
            obj_is_self = isinstance(node.object, ThisExpr) or (
                isinstance(node.object, IdentExpr) and node.object.name == "self"
            )
            if obj_is_self and node.property in field_names:
                return IdentExpr(name=f"_f_{node.property}", line=node.line, col=node.col)
            node.object = _transform(node.object)
            return node
        if isinstance(node, CallExpr):
            if not isinstance(node.callee, IdentExpr):
                node.callee = _transform(node.callee)
            node.args = [_transform(arg) for arg in node.args]
            return node
        if isinstance(node, IdentExpr) and node.name in field_names:
            return IdentExpr(name=f"_f_{node.name}", line=node.line, col=node.col)
        if isinstance(node, Node):
            for key, val in vars(node).items():
                if isinstance(val, Node):
                    setattr(node, key, _transform(val))
                elif isinstance(val, list):
                    setattr(node, key, [_transform(item) if isinstance(item, Node) else item for item in val])
        return node

    return _transform(cloned)


def _format_contracts(exprs):
    if not exprs:
        return []
    from formatter.Formatter import Formatter
    fmt = Formatter()
    return [fmt.format_expr(e, 0) for e in exprs]


def _apply_expr_to_schemas_for_result(ensures_exprs, output_schema):
    holder = {"result": output_schema}
    for ens in ensures_exprs:
        _apply_expr_to_schemas(ens, holder)


def _struct_schema(decl, lines, known_defs):
    properties = {}
    required = []
    for field in decl.fields:
        properties[field.name] = _type_node_to_schema(field.type_ann, known_defs)
        if type(field.type_ann).__name__ != "OptionalType":
            required.append(field.name)
    for field in decl.fields:
        reqs = getattr(field, "requires", None) or ()
        if reqs:
            for req in reqs:
                _apply_expr_to_schemas(req, properties)
            properties[field.name]["x-wysna-requires"] = _format_contracts(reqs)
    schema = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }
    doc = _leading_documentation(lines, decl.line)
    if doc:
        schema["description"] = doc
    return schema


def _enum_schema(decl, lines, known_defs):
    doc = _leading_documentation(lines, decl.line)
    if all(not variant.payload_types for variant in decl.variants):
        schema = {
            "type": "string",
            "enum": [variant.name for variant in decl.variants],
        }
        if doc:
            schema["description"] = doc
        return schema

    variants = []
    for variant in decl.variants:
        if not variant.payload_types:
            variants.append({
                "type": "object",
                "properties": {"tag": {"const": variant.name}},
                "required": ["tag"],
                "additionalProperties": False,
            })
        elif len(variant.payload_types) == 1:
            variants.append({
                "type": "object",
                "properties": {
                    "tag": {"const": variant.name},
                    "value": _type_node_to_schema(variant.payload_types[0], known_defs),
                },
                "required": ["tag", "value"],
                "additionalProperties": False,
            })
        else:
            items = [_type_node_to_schema(item, known_defs) for item in variant.payload_types]
            variants.append({
                "type": "object",
                "properties": {
                    "tag": {"const": variant.name},
                    "value": {
                        "type": "array",
                        "prefixItems": items,
                        "minItems": len(items),
                        "maxItems": len(items),
                    },
                },
                "required": ["tag", "value"],
                "additionalProperties": False,
            })
    schema = {"oneOf": variants}
    if doc:
        schema["description"] = doc
    return schema


def _tool_definition(fn_decl, lines, known_defs, effect_row, *, spelling=None):
    properties = {}
    required = []
    for param in fn_decl.params:
        properties[param.name] = _type_node_to_schema(param.type_ann, known_defs)
        if type(param.type_ann).__name__ != "OptionalType":
            required.append(param.name)
    fn_reqs = getattr(fn_decl, "requires", None) or ()
    for req in fn_reqs:
        _apply_expr_to_schemas(req, properties)
    input_schema = {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }

    ret_node = fn_decl.return_type
    fallible = type(ret_node).__name__ == "ResultType"
    if fallible:
        output_schema = _type_node_to_schema(ret_node.ok_type, known_defs)
        error_schema = _type_node_to_schema(ret_node.err_type, known_defs)
    elif ret_node is None:
        output_schema = {"type": "null"}
        error_schema = None
    else:
        output_schema = _type_node_to_schema(ret_node, known_defs)
        error_schema = None

    fn_ens = getattr(fn_decl, "ensures", None) or ()
    if fn_ens and isinstance(output_schema, dict):
        _apply_expr_to_schemas_for_result(fn_ens, output_schema)

    spelling = spelling or fn_decl
    params_sig = ", ".join(f"{p.name}: {_type_name(p.type_ann)}" for p in spelling.params)
    ret_sig = _type_name(spelling.return_type) if spelling.return_type is not None else "unit"
    type_params = [str(item) for item in getattr(fn_decl, "type_params", ())]
    generic_prefix = f"[{', '.join(type_params)}]" if type_params else ""
    signature = f"fn {fn_decl.name}{generic_prefix}({params_sig}) -> {ret_sig}"

    if effect_row is not None:
        effects = list(effect_row.get("resolved_effects", effect_row.get("transitive_effects", ())))
        capabilities = list(effect_row.get("resolved_capabilities", effect_row.get("capabilities", ())))
        complete = bool(effect_row.get("resolved_complete", effect_row.get("complete_within_model", False)))
    else:
        effects = []
        capabilities = []
        complete = False

    has_global_mutation = "global.write" in effects
    has_global_state = has_global_mutation or "global.read" in effects
    pure = bool(complete and not capabilities and not has_global_state)
    idempotent = bool(
        complete
        and not has_global_mutation
        and set(capabilities).issubset(_READ_ONLY_CAPABILITIES)
    )

    tool_def = {
        "name": fn_decl.name,
        "visibility": "public" if getattr(fn_decl, "is_exported", False) else "private",
        "signature": signature,
        "attributes": [attribute.metadata() for attribute in getattr(fn_decl, 'attributes', [])],
        "description": _leading_documentation(lines, fn_decl.line),
        "input_schema": input_schema,
        "output_schema": output_schema,
        "error_schema": error_schema,
        "fallible": fallible,
        "contract_projection": "representable_subset" if fn_reqs or fn_ens else "none",
        "requires": _format_contracts(fn_reqs),
        "ensures": _format_contracts(fn_ens),
        "effects": effects,
        "capabilities": capabilities,
        "complete_within_model": complete,
        "pure": pure,
        "idempotent": idempotent,
    }
    return tool_def


def _encode_scalar_expr(expr, type_node, known_defs):
    kind = type(type_node).__name__
    if kind in ("NamedType", "PrimitiveType"):
        name = type_node.name
        if name == "int":
            return f"json.from_int({expr})"
        if name == "u8":
            return f"json.from_int(int({expr}))"
        if name == "float":
            return f"json.from_float({expr})"
        if name == "bool":
            return f"json.from_bool({expr})"
        if name == "str":
            return f"json.from_str({expr})"
        if name in known_defs:
            return f"encode_{name}({expr})"
    return None


def _decode_scalar_expr(val_expr, type_node, known_defs):
    kind = type(type_node).__name__
    if kind in ("NamedType", "PrimitiveType"):
        name = type_node.name
        if name == "int":
            return f"json.to_int({val_expr})?"
        if name == "u8":
            return f'(u8(json.to_int({val_expr})?) else {{ return Err(json.error(0, "u8 out of range")) }})'
        if name == "float":
            return f"json.to_float({val_expr})?"
        if name == "bool":
            return f"json.to_bool({val_expr})?"
        if name == "str":
            return f"json.to_str({val_expr})?"
        if name in known_defs:
            return f"decode_{name}({val_expr})?"
    return None


def generate_codecs_source(declarations) -> str:
    """Synthesize type-safe Wysna JSON encoder/decoder functions for nominal structs and enums."""
    struct_decls = [d for d in declarations if isinstance(d, StructDecl) and not getattr(d, "type_params", ())]
    enum_decls = [d for d in declarations if isinstance(d, EnumDecl) and not getattr(d, "type_params", ())]
    known_defs = {d.name for d in struct_decls} | {d.name for d in enum_decls}

    blocks = ['import "std/json"']

    for decl in enum_decls:
        name = decl.name
        if all(not v.payload_types for v in decl.variants):
            arms = [f'        {name}.{v.name} => json.string("{v.name}"),' for v in decl.variants]
            blocks.append(
                f"pub fn encode_{name}(value: {name}) -> json.Value {{\n"
                f"    return match value {{\n"
                + "\n".join(arms)
                + "\n    }\n}"
            )
            blocks.append(
                f"pub fn to_json_{name}(value: {name}) -> str ! json.JsonError {{\n"
                f"    return json.stringify(encode_{name}(value))\n}}"
            )
            checks = [
                f'    if tag == "{v.name}" {{\n        return {name}.{v.name}\n    }}'
                for v in decl.variants
            ]
            blocks.append(
                f"pub fn decode_{name}(raw_val: json.Value) -> {name} ! json.JsonError {{\n"
                f"    let tag = json.to_str(raw_val)?\n"
                + "\n".join(checks)
                + f'\n    return Err(json.error(0, "unknown variant for {name}: " + tag))\n}}'
            )
            blocks.append(
                f"pub fn from_json_{name}(raw: str) -> {name} ! json.JsonError {{\n"
                f"    return decode_{name}(json.parse(raw)?)\n}}"
            )
        elif all(len(v.payload_types) <= 1 for v in decl.variants):
            arms = []
            checks = []
            supported = True
            for v in decl.variants:
                if not v.payload_types:
                    arms.append(
                        f'        {name}.{v.name} => {{\n'
                        f'            let mut _obj: dict[str, json.Value] = {{}}\n'
                        f'            _obj["tag"] = json.string("{v.name}")\n'
                        f'            json.object(_obj)\n'
                        f'        }},'
                    )
                    checks.append(f'    if tag == "{v.name}" {{\n        return {name}.{v.name}\n    }}')
                else:
                    enc = _encode_scalar_expr("_payload", v.payload_types[0], known_defs)
                    dec = _decode_scalar_expr('json.get_required(raw_val, "value")?', v.payload_types[0], known_defs)
                    if enc is None or dec is None:
                        supported = False
                        break
                    arms.append(
                        f'        {name}.{v.name}(_payload) => {{\n'
                        f'            let mut _obj: dict[str, json.Value] = {{}}\n'
                        f'            _obj["tag"] = json.string("{v.name}")\n'
                        f'            _obj["value"] = {enc}\n'
                        f'            json.object(_obj)\n'
                        f'        }},'
                    )
                    checks.append(
                        f'    if tag == "{v.name}" {{\n'
                        f'        return {name}.{v.name}({dec})\n'
                        f'    }}'
                    )
            if supported:
                blocks.append(
                    f"pub fn encode_{name}(value: {name}) -> json.Value {{\n"
                    f"    return match value {{\n"
                    + "\n".join(arms)
                    + "\n    }\n}"
                )
                blocks.append(
                    f"pub fn to_json_{name}(value: {name}) -> str ! json.JsonError {{\n"
                    f"    return json.stringify(encode_{name}(value))\n}}"
                )
                blocks.append(
                    f"pub fn decode_{name}(raw_val: json.Value) -> {name} ! json.JsonError {{\n"
                    f'    let tag = json.get_str(raw_val, "tag")?\n'
                    + "\n".join(checks)
                    + f'\n    return Err(json.error(0, "unknown variant for {name}: " + tag))\n}}'
                )
                blocks.append(
                    f"pub fn from_json_{name}(raw: str) -> {name} ! json.JsonError {{\n"
                    f"    return decode_{name}(json.parse(raw)?)\n}}"
                )

    for decl in struct_decls:
        name = decl.name
        enc_lines = ["    let mut _fields: dict[str, json.Value] = {}"]
        dec_lines = []
        ctor_fields = []
        supported = True

        for field in decl.fields:
            fname = field.name
            fnode = field.type_ann
            fkind = type(fnode).__name__
            ftype_str = _type_name(fnode)

            scalar_enc = _encode_scalar_expr(f"value.{fname}", fnode, known_defs)
            if scalar_enc is not None:
                enc_lines.append(f'    _fields["{fname}"] = {scalar_enc}')
                if fkind in ("NamedType", "PrimitiveType") and fnode.name in ("int", "float", "bool", "str"):
                    dec_lines.append(f'    let _f_{fname} = json.get_{fnode.name}(raw_val, "{fname}")?')
                else:
                    raw_field = f'json.get_required(raw_val, "{fname}")?'
                    decoder = _decode_scalar_expr(raw_field, fnode, known_defs)
                    dec_lines.append(f'    let _f_{fname} = {decoder}')
                ctor_fields.append(f"{fname}: _f_{fname}")
                continue

            if fkind == "OptionalType":
                inner_enc = _encode_scalar_expr("_opt_v", fnode.inner, known_defs)
                inner_dec = _decode_scalar_expr("_opt_item", fnode.inner, known_defs)
                if inner_enc is None or inner_dec is None:
                    supported = False
                    break
                enc_lines.append(
                    f'    _fields["{fname}"] = match value.{fname} {{\n'
                    f'        Some(_opt_v) => {inner_enc},\n'
                    f'        None => json.null(),\n'
                    f'    }}'
                )
                inner_kind = type(fnode.inner).__name__
                if inner_kind in ("NamedType", "PrimitiveType") and fnode.inner.name in ("int", "float", "bool", "str"):
                    dec_lines.append(
                        f'    let _f_{fname} = json.get_optional_{fnode.inner.name}(raw_val, "{fname}")?'
                    )
                else:
                    dec_lines.append(
                        f'    let _raw_opt_{fname} = json.get(raw_val, "{fname}")\n'
                        f'    let _f_{fname}: {ftype_str} = match _raw_opt_{fname} {{\n'
                        f'        Some(_opt_item) => if not json.is_null(_opt_item) {{\n'
                        f'            Some({inner_dec})\n'
                        f'        }} else {{\n'
                        f'            None\n'
                        f'        }},\n'
                        f'        None => None,\n'
                        f'    }}'
                    )
                ctor_fields.append(f"{fname}: _f_{fname}")
                continue

            if fkind == "ListType":
                elem_enc = _encode_scalar_expr("_item", fnode.element_type, known_defs)
                elem_dec = _decode_scalar_expr("_item", fnode.element_type, known_defs)
                if elem_enc is None or elem_dec is None:
                    supported = False
                    break
                enc_lines.append(
                    f"    let mut _arr_{fname}: list[json.Value] = []\n"
                    f"    for _item in value.{fname} {{\n"
                    f"        _arr_{fname}.append({elem_enc})\n"
                    f"    }}\n"
                    f'    _fields["{fname}"] = json.array(_arr_{fname})'
                )
                dec_lines.append(
                    f'    let _raw_arr_{fname} = json.to_array(json.get_required(raw_val, "{fname}")?)?\n'
                    f'    let mut _f_{fname}: {ftype_str} = []\n'
                    f'    for _item in _raw_arr_{fname} {{\n'
                    f'        _f_{fname}.append({elem_dec})\n'
                    f'    }}'
                )
                ctor_fields.append(f"{fname}: _f_{fname}")
                continue

            if fkind == "DictType" and _type_name(fnode.key_type) == "str":
                val_enc = _encode_scalar_expr("_v", fnode.value_type, known_defs)
                val_dec = _decode_scalar_expr("_v", fnode.value_type, known_defs)
                if val_enc is None or val_dec is None:
                    supported = False
                    break
                enc_lines.append(
                    f"    let mut _map_{fname}: dict[str, json.Value] = {{}}\n"
                    f"    for (_k, _v) in dict_items(value.{fname}) {{\n"
                    f"        _map_{fname}[_k] = {val_enc}\n"
                    f"    }}\n"
                    f'    _fields["{fname}"] = json.object(_map_{fname})'
                )
                dec_lines.append(
                    f'    let _raw_map_{fname} = json.to_object(json.get_required(raw_val, "{fname}")?)?\n'
                    f'    let mut _f_{fname}: {ftype_str} = {{}}\n'
                    f'    for (_k, _v) in dict_items(_raw_map_{fname}) {{\n'
                    f'        _f_{fname}[_k] = {val_dec}\n'
                    f'    }}'
                )
                ctor_fields.append(f"{fname}: _f_{fname}")
                continue

            supported = False
            break

        if not supported:
            continue

        field_names = {f.name for f in decl.fields}
        contract_fields = [f for f in decl.fields if getattr(f, "requires", None)]
        if contract_fields:
            from formatter.Formatter import Formatter
            fmt = Formatter()
            for field in contract_fields:
                for req in field.requires:
                    rewritten = _rewrite_contract_field_refs(req, field_names)
                    cond_src = fmt.format_expr(rewritten, 0)
                    orig_src = fmt.format_expr(req, 0).replace("\\", "\\\\").replace('"', '\\"')
                    dec_lines.append(
                        f'    if not ({cond_src}) {{\n'
                        f'        return Err(json.error(0, "{name}.{field.name}: failed contract \'{orig_src}\'"))\n'
                        f'    }}'
                    )

        enc_lines.append("    return json.object(_fields)")
        blocks.append(
            f"pub fn encode_{name}(value: {name}) -> json.Value {{\n"
            + "\n".join(enc_lines)
            + "\n}"
        )
        blocks.append(
            f"pub fn to_json_{name}(value: {name}) -> str ! json.JsonError {{\n"
            f"    return json.stringify(encode_{name}(value))\n}}"
        )
        ctor_str = ", ".join(ctor_fields)
        dec_lines.append(f"    return {name} {{ {ctor_str} }}")
        blocks.append(
            f"pub fn decode_{name}(raw_val: json.Value) -> {name} ! json.JsonError {{\n"
            + "\n".join(dec_lines)
            + "\n}"
        )
        blocks.append(
            f"pub fn from_json_{name}(raw: str) -> {name} ! json.JsonError {{\n"
            f"    return decode_{name}(json.parse(raw)?)\n}}"
        )

    return "\n\n".join(blocks) + "\n"


def _schema_definition_key(declaration, entry):
    if declaration.source_file == entry:
        return declaration.name
    short_name = declaration.name.split('__', 2)[-1] if declaration.name.startswith('__wysna_module_') else declaration.name
    try:
        relative = os.path.relpath(declaration.source_file, Path(entry).parent)
    except ValueError:
        relative = declaration.source_file
    digest = hashlib.sha256(relative.replace('\\', '/').encode('utf-8')).hexdigest()[:16]
    return f'{short_name}__{digest}'


def _schema_type_nodes(nodes):
    pending = list(nodes)
    while pending:
        item = pending.pop()
        if isinstance(item, (list, tuple)):
            pending.extend(item)
        elif isinstance(item, Node):
            if type(item).__name__ in ('NamedType', 'GenericType'):
                yield item
            pending.extend(child for child in vars(item).values() if isinstance(child, (Node, list, tuple)))


def _schema_projection(service, checked, selected):
    linked = service.link(checked).body
    entry = checked.source.file
    records = {item.name: item for item in linked if isinstance(item, (StructDecl, EnumDecl))}
    functions = {item.name: item for item in linked if isinstance(item, FnDecl) and item.source_file == entry}
    projected = [functions[item.name] for item in selected]
    source_names = {item.name for item in checked.ast.body if isinstance(item, (StructDecl, EnumDecl))}
    reachable = {name: item for name, item in records.items()
                 if item.source_file == entry and name in source_names}
    pending = [*[function.return_type for function in projected],
               *[function.params for function in projected], *reachable.values()]
    while pending:
        for annotation in _schema_type_nodes([pending.pop()]):
            name = annotation.name
            if name in records and name not in reachable:
                reachable[name] = records[name]
                pending.append(records[name])
    keys = {name: _schema_definition_key(item, entry) for name, item in reachable.items()}
    for item in [*projected, *reachable.values()]:
        for annotation in _schema_type_nodes([item]):
            annotation.name = keys.get(annotation.name, annotation.name)
    for name, item in reachable.items():
        item.name = keys[name]
    return projected, reachable.values()


def generate_schemas(path, source=None, *, include_all=False, function_name=None,
                     emit_codecs=False, service=None):
    """Type-check ``path`` (or ``source``) and return JSON Schemas and tool definitions."""
    owns_service = service is None
    if owns_service:
        service = CompilerService()
    resolved_path = str(Path(path).resolve())
    if source is None:
        source = Path(resolved_path).read_bytes().decode("utf-8")
        checked = service.check_file(Path(resolved_path))
    else:
        checked = service.check(source, resolved_path)

    explain_report = service.explain(checked)
    effect_by_name = {row["name"]: row for row in explain_report.get("functions", ())}

    lines = source.splitlines()
    declarations = checked.ast.body
    fn_decls = [d for d in declarations if isinstance(d, FnDecl)]

    if function_name is not None:
        selected_fns = [d for d in fn_decls if d.name == function_name]
        if not selected_fns:
            raise ValueError(f"Function '{function_name}' not found in '{path}'")
    elif include_all:
        selected_fns = [d for d in fn_decls if d.name != "main" or len(fn_decls) == 1]
    else:
        exported_fns = [d for d in fn_decls if getattr(d, "is_exported", False)]
        if exported_fns:
            selected_fns = exported_fns
        else:
            non_main = [d for d in fn_decls if d.name != "main"]
            selected_fns = non_main if non_main else fn_decls

    projected, records = _schema_projection(service, checked, selected_fns)
    known_defs = {item.name for item in records}
    defs = {}
    source_lines = {checked.source.file: lines}
    for item in records:
        if item.source_file not in source_lines:
            source_lines[item.source_file] = service.source_text(item.source_file).splitlines()
        definition = _struct_schema if isinstance(item, StructDecl) else _enum_schema
        defs[item.name] = definition(item, source_lines[item.source_file], known_defs)
    tools = [
        _tool_definition(function, lines, known_defs, effect_by_name.get(function.name), spelling=original)
        for original, function in zip(selected_fns, projected)
    ]

    result = {
        "schema_version": "wysna.schema/1",
        "compiler_version": WYSNA_VERSION,
        "$schema": JSON_SCHEMA_DRAFT,
        "$defs": defs,
        "tools": tools,
    }
    if emit_codecs:
        result["codecs_source"] = generate_codecs_source(declarations)
    return result


def run_schema_command(argv):
    parser = _Parser(prog="wysna schema", description=__doc__)
    parser.add_argument("source")
    parser.add_argument("--all", action="store_true", dest="include_all",
                        help="Include private top-level functions even when 'pub fn' declarations exist")
    parser.add_argument("--function", help="Emit tool schema for a single named function")
    parser.add_argument("--emit-codecs", action="store_true",
                        help="Generate type-safe Wysna JSON codec functions for structs and enums")
    parser.add_argument("-o", "--output", dest="output_path",
                        help="Write generated output or codecs to a file")
    parser.add_argument("--json", action="store_const", const="json-v1", dest="output_format",
                        help="Emit canonical json-v1 tool result envelope")
    parser.add_argument("--output-format", choices=("json-v1",))
    json_output = any(arg in ("--output-format=json-v1", "--json") for arg in argv) or "--output-format" in argv
    service = CompilerService()
    try:
        options = parser.parse_args(list(argv))
        json_output = options.output_format == "json-v1"
        try:
            payload = generate_schemas(
                options.source,
                include_all=options.include_all,
                function_name=options.function,
                emit_codecs=options.emit_codecs,
                service=service,
            )
        except UnicodeDecodeError as error:
            raise WysnaError("Source is not valid UTF-8",
                             file=str(Path(options.source).resolve()),
                             code="W1001", stage="lexer") from error
        if options.output_path:
            out_content = (
                payload["codecs_source"]
                if options.emit_codecs and not json_output
                else json.dumps(payload, indent=2) + "\n"
            )
            Path(options.output_path).write_text(out_content, encoding="utf-8")
        if json_output:
            envelope = tool_result("schema", "ok", 0)
            envelope["result"] = payload
            write_tool_result(envelope, sys.stdout)
        elif not options.output_path:
            if options.emit_codecs:
                print(payload["codecs_source"], end="")
            else:
                print(json.dumps(payload, indent=2))
        return 0
    except (WysnaError, OSError, UnicodeError, ValueError) as error:
        semantic = isinstance(error, WysnaError)
        filesystem = isinstance(error, OSError)
        code = 1 if semantic else 3 if filesystem else 2
        if json_output:
            if semantic:
                sources = service.source_texts()
                cache = {}
                diagnostics = [
                    diagnostic_from_error(item, sources, span_cache=cache)
                    for item in getattr(error, "diagnostics", (error,))
                ]
            else:
                diagnostics = [
                    diagnostic_record(
                        "W5004" if filesystem else "W5002",
                        "filesystem" if filesystem else "schema",
                        str(error),
                    )
                ]
            status = "diagnostic_error" if semantic else "toolchain_error" if filesystem else "usage_error"
            write_tool_result(tool_result("schema", status, code, diagnostics=diagnostics), sys.stdout)
        else:
            print(f"wysna schema: {error}", file=sys.stderr)
        return code
