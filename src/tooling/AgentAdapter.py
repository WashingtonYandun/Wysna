"""Build a source adapter for an opted-in, pure, typed Wysna function."""
import json
import re

from compiler.CompilerService import CompilerService
from error.Error import WysnaError
from node.NodeType import EnumDecl, StructDecl
from tooling.CapabilityPolicy import enforce_resolved_capability_policy


def _literal(value):
    return json.dumps(value, ensure_ascii=False)


def emit_agent_adapter(entry, declaration, projected, records, tool):
    # The schema projection and codec generator are shared with ordinary schema export.
    from tooling.SchemaCLI import _decode_scalar_expr, _encode_scalar_expr, generate_codecs_source

    def reject(message):
        raise WysnaError(message, code='W2013', stage='schema', file=entry,
                         line=declaration.line, col=declaration.col)

    marks = [attribute['arguments'] for attribute in tool['attributes'] if attribute['name'] == 'AgentTool']
    if (declaration.name == 'main' or not getattr(declaration, 'is_exported', False) or declaration.is_async
            or declaration.type_params or not tool['pure'] or len(marks) != 1):
        reject('Agent adapter requires a public, synchronous, concrete, pure @AgentTool function')
    identity, version = marks[0].get('id'), marks[0].get('version')
    if (not isinstance(identity, str) or re.fullmatch(r'[A-Za-z][A-Za-z0-9_.-]{0,63}', identity) is None
            or type(version) is not int or version < 1):
        reject('@AgentTool requires a stable id and a positive integer version')

    # Local schema export includes documentary types that are not part of the callable ABI.
    supported = []
    for item in records:
        if not isinstance(item, (StructDecl, EnumDecl)) or item.type_params:
            continue
        if isinstance(item, EnumDecl) and any(variant.payload_types for variant in item.variants):
            continue
        supported.append(item)
    names = {item.name for item in supported}
    referenced = set()
    decoders = []
    for index, param in enumerate(projected.params):
        key = _literal(param.name)
        expression = _decode_scalar_expr(f'json.get_required(input, {key})?', param.type_ann, names)
        if expression is None:
            reject(f"Tool parameter '{param.name}' does not have a supported JSON codec")
        decoders.append(f'    let agent_arg_{index} = {expression}')
        _remember_type(param.type_ann, names, referenced)

    returned = projected.return_type
    fallible = type(returned).__name__ == 'ResultType'
    outcome = returned.ok_type if fallible else returned
    success = _encode_scalar_expr('agent_value', outcome, names)
    if type(outcome).__name__ == 'NamedType' and outcome.name == 'unit':
        success = 'json.null()'
    if success is None:
        reject('Tool result does not have a supported JSON codec')
    _remember_type(outcome, names, referenced)
    if fallible:
        failure = _encode_scalar_expr('agent_error', returned.err_type, names)
        if failure is None:
            reject('Tool error does not have a supported JSON codec')
        _remember_type(returned.err_type, names, referenced)

    by_name = {item.name: item for item in supported}
    pending = list(referenced)
    while pending:
        record = by_name[pending.pop()]
        if isinstance(record, StructDecl):
            for field in record.fields:
                previous = set(referenced)
                _remember_type(field.type_ann, names, referenced)
                pending.extend(referenced - previous)
    records = [item for item in supported if item.name in referenced]
    imports = [f'from {_literal(entry)} use {declaration.name} as agent_target']
    for item in records:
        if not getattr(item, 'is_exported', False):
            reject(f"Tool record '{item.name}' must be public")
        original = item.name if item.source_file == entry else item.name.rsplit('__', 1)[0]
        alias = f' as {item.name}' if original != item.name else ''
        imports.append(f'from {_literal(item.source_file)} use {original}{alias}')
    codecs = generate_codecs_source(records, strict=True)
    for name in sorted(referenced):
        if f'pub fn encode_{name}(' not in codecs or f'pub fn decode_{name}(' not in codecs:
            reject(f"Tool record '{name}' does not have a supported JSON codec")

    allowed = ', '.join(_literal(param.name) for param in projected.params)
    argument_list = ', '.join(f'agent_arg_{index}' for index in range(len(decoders)))
    call = f'agent_target({argument_list})'
    if fallible:
        result_lines = [f'    return Ok(match {call} {{',
                        f'        Ok(agent_value) => agent_result("ok", {success}),',
                        f'        Err(agent_error) => agent_result("error", {failure}),', '    })']
    else:
        result_lines = [f'    let agent_value = {call}', f'    return Ok(agent_result("ok", {success}))']
    body = '\n'.join([*decoders, *result_lines])
    encoding_failure = _literal(json.dumps({
        'schema_version': 'wysna.agent-tool-result/1', 'id': identity, 'version': version,
        'status': 'encoding_error', 'value': 'Tool result could not be encoded as JSON',
    }, separators=(',', ':')))
    source = '\n'.join(imports) + '\n\n' + codecs + f'''
fn agent_members(raw: json.Value, keys: list[str]) -> json.Value ! json.JsonError {{
    let fields = json.to_object(raw)?
    for key in fields.keys() {{
        if not (key in keys) {{ return Err(json.error(0, "Unknown field: " + key)) }}
    }}
    return Ok(raw)
}}

fn agent_result(status: str, value: json.Value) -> json.Value {{
    let fields: dict[str, json.Value] = {{
        "schema_version": json.string("wysna.agent-tool-result/1"),
        "id": json.string({_literal(identity)}),
        "version": json.from_int({version}),
        "status": json.string(status),
        "value": value,
    }}
    return json.object(fields)
}}

fn agent_invoke(raw: str) -> json.Value ! json.JsonError {{
    let names: list[str] = [{allowed}]
    let input = agent_members(json.parse_with_limits(raw, 65536, 32, 4096)?, names)?
{body}
}}

let agent_arguments = args()
let agent_reply = if len(agent_arguments) == 2 {{
    agent_invoke(agent_arguments[1]) else error {{ agent_result("invalid_input", json.string(error.message)) }}
}} else {{ agent_result("invalid_input", json.string("Supply one JSON argument")) }}
print(json.stringify_ordered(agent_reply) else {{ {encoding_failure} }})
'''
    service = CompilerService()
    checked = service.check(source, entry + '.adapter.wysna')
    report, untrusted = service.explain_with_foreign_bindings(checked)
    enforce_resolved_capability_policy(report, 'allow', {'arguments', 'stdio'}, entry,
                                       untrusted_bindings=untrusted)
    return {'id': identity, 'version': version, 'source': source}


def _remember_type(annotation, names, referenced):
    pending = [annotation]
    while pending:
        current = pending.pop()
        name = getattr(current, 'name', None)
        if name in names:
            referenced.add(name)
        for child in ('inner', 'element_type', 'value_type'):
            nested = getattr(current, child, None)
            if nested is not None:
                pending.append(nested)
