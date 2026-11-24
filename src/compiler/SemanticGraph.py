"""Compile-time semantic relationship graph over checked Wysna programs.

Builds a deterministic, read-only graph of interfaces, classes, structs, enums,
functions, methods, call edges, type dependencies, instantiations, and
transitive impact ("blast radius") from immutable CheckedProgram snapshots.
"""
from collections import deque
import hashlib
import json
from pathlib import Path
import re

from error.Error import WysnaError
from lexer.Lexer import Lexer
from node import NodeType as A
from tokens.TokenType import TokenType


_MODIFIER_TOKENS = frozenset({
    TokenType.PUB,
    TokenType.ASYNC,
    TokenType.PRIV,
})

_OPEN_DELIMITERS = frozenset({TokenType.LPAREN, TokenType.LBRACKET, TokenType.LBRACE})
_CLOSE_DELIMITERS = frozenset({TokenType.RPAREN, TokenType.RBRACKET, TokenType.RBRACE})
_MANGLED_MODULE_RE = re.compile(r'^__wysna_module_([0-9a-f]+)__(.+)$')


def _decode_qualified_name(raw_name: str, entry_module_id: str) -> tuple[str, str]:
    """Return (short_name, qualified_display_name) for raw or mangled symbol names."""
    match = _MANGLED_MODULE_RE.match(raw_name or '')
    if match:
        mod_id = bytes.fromhex(match[1]).decode('utf-8', errors='replace')
        short = match[2]
        return short, f'{mod_id}::{short}'
    if entry_module_id:
        return raw_name, raw_name
    return raw_name, raw_name


def _format_type_ann(node) -> str:
    if node is None:
        return 'unit'
    if isinstance(node, A.ResultType):
        return f'{_format_type_ann(node.ok_type)} ! {_format_type_ann(node.err_type)}'
    if isinstance(node, A.ErrorUnionType):
        return ' | '.join(_format_type_ann(item) for item in node.error_types)
    if isinstance(node, A.OptionalType):
        return f'{_format_type_ann(node.inner)}?'
    if isinstance(node, A.ListType):
        return f'list[{_format_type_ann(node.element_type)}]'
    if isinstance(node, A.DictType):
        return f'dict[{_format_type_ann(node.key_type)}, {_format_type_ann(node.value_type)}]'
    if isinstance(node, A.TupleType):
        return '(' + ', '.join(_format_type_ann(item) for item in node.element_types) + ')'
    if isinstance(node, A.GenericType):
        args = ', '.join(_format_type_ann(item) for item in node.type_args)
        _, name = _decode_qualified_name(node.name, '')
        short, _ = _decode_qualified_name(node.name, '')
        return f'{short}[{args}]'
    if isinstance(node, A.FunctionType):
        params = ', '.join(_format_type_ann(item) for item in node.params)
        return f'fn({params}) -> {_format_type_ann(node.return_type)}'
    if isinstance(node, A.NamedType):
        short, _ = _decode_qualified_name(node.name, '')
        return short
    return str(node)


def _format_fn_signature(node, display: str, owner_name: str = '') -> str:
    prefix = 'async fn ' if getattr(node, 'is_async', False) else 'fn '
    if isinstance(node, A.ExternFnDecl):
        prefix = f'extern "{node.extern_abi}" fn '
    type_params = [str(tp) for tp in getattr(node, 'type_params', ())]
    generics = f"[{', '.join(type_params)}]" if type_params else ''
    params = ', '.join(
        f'{p.name}: {_format_type_ann(p.type_ann)}' for p in getattr(node, 'params', ())
    )
    ret = _format_type_ann(getattr(node, 'return_type', None))
    full_name = f'{owner_name}.{display}' if owner_name else display
    return f'{prefix}{full_name}{generics}({params}) -> {ret}'


def _utf8_byte_offset(source_text: str, char_offset: int) -> int:
    return len(source_text[:max(0, char_offset)].encode('utf-8'))


def find_declaration_span(
    source_text: str,
    line: int,
    col: int,
    *,
    has_brace_body: bool = True,
    tokens=None,
) -> tuple[int, int, int, int]:
    """Locate the exact UTF-8 byte span (start_byte, end_byte, start_line, end_line) of a declaration."""
    if not source_text:
        return 0, 0, line, line
    if tokens is None:
        try:
            tokens = Lexer(source_text).tokenize()
        except Exception:
            return 0, 0, line, line
    if not tokens:
        return 0, 0, line, line

    target_idx = None
    for idx, tok in enumerate(tokens):
        if tok.type == TokenType.EOF:
            break
        if tok.line == line and tok.col >= col:
            target_idx = idx
            break
        if tok.line > line:
            target_idx = idx
            break
    if target_idx is None:
        return 0, 0, line, line

    start_idx = target_idx
    while start_idx > 0 and tokens[start_idx - 1].type in _MODIFIER_TOKENS:
        if tokens[start_idx].line - tokens[start_idx - 1].line > 1:
            break
        start_idx -= 1

    start_tok = tokens[start_idx]
    start_char = getattr(start_tok, 'start_offset', 0)
    start_line = start_tok.line

    end_tok = tokens[target_idx]
    if has_brace_body:
        brace_depth = 0
        opened = False
        for cur in range(target_idx, len(tokens)):
            tok = tokens[cur]
            if tok.type == TokenType.LBRACE:
                brace_depth += 1
                opened = True
            elif tok.type == TokenType.RBRACE and opened:
                brace_depth -= 1
                if brace_depth == 0:
                    end_tok = tok
                    break
            elif tok.type == TokenType.EOF:
                break
    else:
        delim_depth = 0
        last_real = tokens[target_idx]
        for cur in range(target_idx, len(tokens)):
            tok = tokens[cur]
            if tok.type == TokenType.EOF:
                break
            if tok.type in _OPEN_DELIMITERS:
                delim_depth += 1
            elif tok.type in _CLOSE_DELIMITERS and delim_depth > 0:
                delim_depth -= 1
            elif delim_depth == 0 and tok.type in (TokenType.NEWLINE, TokenType.SEMICOLON):
                break
            if tok.type != TokenType.NEWLINE:
                last_real = tok
        end_tok = last_real

    end_char = getattr(end_tok, 'end_offset', start_char)
    end_line = end_tok.line
    return (
        _utf8_byte_offset(source_text, start_char),
        _utf8_byte_offset(source_text, end_char),
        start_line,
        end_line,
    )


def _extract_type_names_from_annotation(type_node) -> list[str]:
    """Collect nominal type names referenced inside a syntactic TypeNode."""
    if type_node is None:
        return []
    names = []
    stack = [type_node]
    while stack:
        current = stack.pop()
        if isinstance(current, A.NamedType):
            names.append(current.name)
        elif isinstance(current, A.GenericType):
            names.append(current.name)
            stack.extend(current.type_args)
        elif isinstance(current, A.OptionalType):
            stack.append(current.inner)
        elif isinstance(current, A.ListType):
            stack.append(current.element_type)
        elif isinstance(current, A.DictType):
            stack.extend((current.key_type, current.value_type))
        elif isinstance(current, A.TupleType):
            stack.extend(current.element_types)
        elif isinstance(current, A.ResultType):
            stack.extend((current.ok_type, current.err_type))
        elif isinstance(current, A.ErrorUnionType):
            stack.extend(current.error_types)
        elif isinstance(current, A.FunctionType):
            stack.extend(current.params)
            stack.append(current.return_type)
    return names


def _extract_type_names_from_type_string(type_str: str) -> list[str]:
    """Extract identifier tokens from a formatted Semantic TypeInfo string."""
    if not type_str:
        return []
    return re.findall(r'[A-Za-z_][A-Za-z0-9_]*(?:::[A-Za-z_][A-Za-z0-9_]*)?', type_str)


def _walk_expressions(node):
    """Yield AST nodes beneath a declaration body (excluding nested inner decls if any)."""
    if isinstance(node, A.Node):
        yield node
        for key, value in vars(node).items():
            if key in ('resolved_symbol', 'inferred_type'):
                continue
            if isinstance(value, (A.Node, list)):
                yield from _walk_expressions(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_expressions(item)


def _identifier_target(expr, fallback):
    symbol = getattr(expr, 'resolved_symbol', None)
    if symbol is None:
        return fallback
    return symbol.declaration_name or symbol.name


def build_semantic_graph(checked, *, effects_report=None) -> dict:
    """Build a complete semantic relationship graph from a CheckedProgram snapshot."""
    unit_programs = getattr(checked, 'unit_programs', None)
    if not unit_programs:
        unit_programs = {checked.source.file: checked.program}

    sources = checked.sources
    modules = checked.modules
    entry_file = checked.source.file
    entry_mod_info = modules.get(entry_file)
    entry_mod_id = entry_mod_info.module_id if entry_mod_info else ''

    # Tokenize each file at most once for fast declaration span extraction.
    token_cache = {}
    for file_path, snapshot in sources.items():
        try:
            token_cache[file_path] = Lexer(snapshot.text, file=file_path).tokenize()
        except Exception:
            token_cache[file_path] = []

    nodes = {}
    raw_to_node_id = {}
    short_to_node_ids = {}
    methods_by_owner_raw = {}
    module_nodes = []

    def register_lookup(raw_name: str, short_name: str, qual_name: str, node_id: str) -> None:
        raw_to_node_id[raw_name] = node_id
        raw_to_node_id[qual_name] = node_id
        short_to_node_ids.setdefault(short_name, []).append(node_id)

    def span_for(file_path: str, line: int, col: int, has_brace_body: bool = True) -> tuple[int, int, int, int]:
        snap = sources.get(file_path)
        text = snap.text if snap is not None else ''
        return find_declaration_span(
            text, line, col, has_brace_body=has_brace_body, tokens=token_cache.get(file_path)
        )

    # First pass: build module list and all top-level declaration nodes + class methods.
    decl_ast_pairs = []

    for file_path, program in unit_programs.items():
        mod_info = modules.get(file_path)
        mod_id = mod_info.module_id if mod_info else file_path
        is_entry = bool(mod_info.is_entry) if mod_info else (file_path == entry_file)
        module_nodes.append({
            'id': mod_id,
            'file': file_path,
            'is_entry': is_entry,
            'dependencies': list(mod_info.dependencies) if mod_info else [],
        })
        exports = set(mod_info.exports.keys()) if mod_info else set()

        for stmt in getattr(program, 'body', ()):
            stmt_file = getattr(stmt, 'source_file', None) or file_path
            raw_name = getattr(stmt, 'name', '')
            if isinstance(stmt, A.InterfaceDecl):
                short, qual = _decode_qualified_name(raw_name, entry_mod_id)
                node_id = f'interface:{qual}'
                s_byte, e_byte, s_line, e_line = span_for(stmt_file, stmt.line, stmt.col, True)
                method_list = []
                for m in stmt.methods:
                    sig = _format_fn_signature(m, m.name)
                    m_id = f'method:{qual}.{m.name}'
                    nodes[m_id] = {
                        'id': m_id,
                        'name': m.name,
                        'qualified_name': f'{qual}.{m.name}',
                        'kind': 'interface_method',
                        'owner': node_id,
                        'file': stmt_file,
                        'line': m.line,
                        'col': m.col,
                        'start_byte': s_byte,
                        'end_byte': e_byte,
                        'end_line': e_line,
                        'signature': sig,
                        'implemented_by': [],
                        'called_by': [],
                        'uses_types': [],
                    }
                    method_list.append({'id': m_id, 'name': m.name, 'signature': sig, 'line': m.line})
                    methods_by_owner_raw.setdefault(raw_name, {})[m.name] = m_id
                nodes[node_id] = {
                    'id': node_id,
                    'name': short,
                    'qualified_name': qual,
                    'kind': 'interface',
                    'module': mod_id,
                    'file': stmt_file,
                    'line': s_line,
                    'col': stmt.col,
                    'start_byte': s_byte,
                    'end_byte': e_byte,
                    'end_line': e_line,
                    'visibility': 'public' if bool(getattr(stmt, 'is_exported', False)) or (not is_entry and raw_name in exports) else 'private',
                    'methods': method_list,
                    'implemented_by': [],
                    'used_by': [],
                }
                register_lookup(raw_name, short, qual, node_id)
                decl_ast_pairs.append((node_id, raw_name, stmt, stmt_file))

            elif isinstance(stmt, A.ClassDecl):
                short, qual = _decode_qualified_name(raw_name, entry_mod_id)
                node_id = f'class:{qual}'
                s_byte, e_byte, s_line, e_line = span_for(stmt_file, stmt.line, stmt.col, True)
                field_list = [
                    {
                        'name': f.name,
                        'type': _format_type_ann(f.type_ann),
                        'visibility': 'public' if f.is_public else 'private',
                        'line': f.line,
                    }
                    for f in stmt.fields
                ]
                method_list = []
                for m in stmt.methods:
                    ms_byte, me_byte, ms_line, me_line = span_for(stmt_file, m.line, m.col, True)
                    sig = _format_fn_signature(m, m.name, short)
                    m_id = f'method:{qual}.{m.name}'
                    nodes[m_id] = {
                        'id': m_id,
                        'name': m.name,
                        'qualified_name': f'{qual}.{m.name}',
                        'kind': 'method',
                        'owner': node_id,
                        'file': stmt_file,
                        'line': ms_line,
                        'col': m.col,
                        'start_byte': ms_byte,
                        'end_byte': me_byte,
                        'end_line': me_line,
                        'visibility': 'public' if m.is_public else 'private',
                        'signature': sig,
                        'implements_method': None,
                        'calls': [],
                        'called_by': [],
                        'instantiates': [],
                        'uses_types': [],
                    }
                    raw_to_node_id[f'{raw_name}.{m.name}'] = m_id
                    raw_to_node_id[f'{qual}.{m.name}'] = m_id
                    short_to_node_ids.setdefault(f'{short}.{m.name}', []).append(m_id)
                    methods_by_owner_raw.setdefault(raw_name, {})[m.name] = m_id
                    method_list.append({'id': m_id, 'name': m.name, 'signature': sig, 'line': ms_line})
                    decl_ast_pairs.append((m_id, f'{raw_name}.{m.name}', m, stmt_file))

                nodes[node_id] = {
                    'id': node_id,
                    'name': short,
                    'qualified_name': qual,
                    'kind': 'class',
                    'module': mod_id,
                    'file': stmt_file,
                    'line': s_line,
                    'col': stmt.col,
                    'start_byte': s_byte,
                    'end_byte': e_byte,
                    'end_line': e_line,
                    'visibility': 'public' if bool(getattr(stmt, 'is_exported', False)) or (not is_entry and raw_name in exports) else 'private',
                    'super_class': None,
                    'subclasses': [],
                    'implements': [],
                    'fields': field_list,
                    'methods': method_list,
                    'instantiated_by': [],
                    'used_by': [],
                }
                register_lookup(raw_name, short, qual, node_id)
                decl_ast_pairs.append((node_id, raw_name, stmt, stmt_file))

            elif isinstance(stmt, A.StructDecl):
                short, qual = _decode_qualified_name(raw_name, entry_mod_id)
                node_id = f'struct:{qual}'
                s_byte, e_byte, s_line, e_line = span_for(stmt_file, stmt.line, stmt.col, True)
                nodes[node_id] = {
                    'id': node_id,
                    'name': short,
                    'qualified_name': qual,
                    'kind': 'struct',
                    'module': mod_id,
                    'file': stmt_file,
                    'line': s_line,
                    'col': stmt.col,
                    'start_byte': s_byte,
                    'end_byte': e_byte,
                    'end_line': e_line,
                    'visibility': 'public' if bool(getattr(stmt, 'is_exported', False)) or (not is_entry and raw_name in exports) else 'private',
                    'fields': [
                        {'name': f.name, 'type': _format_type_ann(f.type_ann), 'line': f.line}
                        for f in stmt.fields
                    ],
                    'instantiated_by': [],
                    'used_by': [],
                }
                register_lookup(raw_name, short, qual, node_id)
                decl_ast_pairs.append((node_id, raw_name, stmt, stmt_file))

            elif isinstance(stmt, A.EnumDecl):
                short, qual = _decode_qualified_name(raw_name, entry_mod_id)
                node_id = f'enum:{qual}'
                s_byte, e_byte, s_line, e_line = span_for(stmt_file, stmt.line, stmt.col, True)
                nodes[node_id] = {
                    'id': node_id,
                    'name': short,
                    'qualified_name': qual,
                    'kind': 'enum',
                    'module': mod_id,
                    'file': stmt_file,
                    'line': s_line,
                    'col': stmt.col,
                    'start_byte': s_byte,
                    'end_byte': e_byte,
                    'end_line': e_line,
                    'visibility': 'public' if bool(getattr(stmt, 'is_exported', False)) or (not is_entry and raw_name in exports) else 'private',
                    'variants': [str(v) for v in stmt.variants],
                    'used_by': [],
                }
                register_lookup(raw_name, short, qual, node_id)
                decl_ast_pairs.append((node_id, raw_name, stmt, stmt_file))

            elif isinstance(stmt, A.ExternFnDecl):
                short, qual = _decode_qualified_name(raw_name, entry_mod_id)
                node_id = f'extern:{qual}'
                s_byte, e_byte, s_line, e_line = span_for(stmt_file, stmt.line, stmt.col, False)
                nodes[node_id] = {
                    'id': node_id,
                    'name': short,
                    'qualified_name': qual,
                    'kind': 'extern',
                    'abi': stmt.extern_abi,
                    'module': mod_id,
                    'file': stmt_file,
                    'line': s_line,
                    'col': stmt.col,
                    'start_byte': s_byte,
                    'end_byte': e_byte,
                    'end_line': e_line,
                    'signature': _format_fn_signature(stmt, short),
                    'called_by': [],
                }
                register_lookup(raw_name, short, qual, node_id)

            elif isinstance(stmt, A.FnDecl):
                short, qual = _decode_qualified_name(raw_name, entry_mod_id)
                node_id = f'fn:{qual}'
                s_byte, e_byte, s_line, e_line = span_for(stmt_file, stmt.line, stmt.col, True)
                nodes[node_id] = {
                    'id': node_id,
                    'name': short,
                    'qualified_name': qual,
                    'kind': 'fn',
                    'module': mod_id,
                    'file': stmt_file,
                    'line': s_line,
                    'col': stmt.col,
                    'start_byte': s_byte,
                    'end_byte': e_byte,
                    'end_line': e_line,
                    'visibility': 'public' if bool(getattr(stmt, 'is_exported', False)) or (not is_entry and raw_name in exports) else 'private',
                    'is_async': bool(getattr(stmt, 'is_async', False)),
                    'signature': _format_fn_signature(stmt, short),
                    'requires_count': len(getattr(stmt, 'requires', ()) or ()),
                    'attributes': [attribute.metadata() for attribute in getattr(stmt, 'attributes', [])],
                    'ensures_count': len(getattr(stmt, 'ensures', ()) or ()),
                    'calls': [],
                    'called_by': [],
                    'instantiates': [],
                    'uses_types': [],
                    'effects': [],
                    'capabilities': [],
                }
                register_lookup(raw_name, short, qual, node_id)
                decl_ast_pairs.append((node_id, raw_name, stmt, stmt_file))

    def resolve_node_id(name: str, preferred_kinds: tuple[str, ...] | None = None) -> str | None:
        if not name:
            return None
        if name in nodes:
            if not preferred_kinds or nodes[name]['kind'] in preferred_kinds:
                return name
        if name in raw_to_node_id:
            cand = raw_to_node_id[name]
            if not preferred_kinds or nodes[cand]['kind'] in preferred_kinds:
                return cand
        candidates = short_to_node_ids.get(name, ())
        if preferred_kinds:
            candidates = [c for c in candidates if nodes[c]['kind'] in preferred_kinds]
        if len(candidates) == 1:
            return candidates[0]
        return None

    def resolve_method_id(owner_name: str, method_name: str) -> str | None:
        direct = resolve_node_id(f'{owner_name}.{method_name}', ('method', 'interface_method'))
        if direct:
            return direct
        owner_id = resolve_node_id(owner_name, ('class', 'interface'))
        queue = deque([owner_id] if owner_id else [])
        visited = set()
        while queue:
            current = queue.popleft()
            if current in visited or current not in nodes:
                continue
            visited.add(current)
            cnode = nodes[current]
            for m in cnode.get('methods', ()):
                if m.get('name') == method_name:
                    return m.get('id')
            if cnode.get('super_class'):
                queue.append(cnode['super_class'])
            queue.extend(cnode.get('implements', ()))
        return None

    edges = []
    seen_edges = set()

    def add_edge(src: str, dst: str, kind: str, *, file: str = '', line: int = 0, context: str = '') -> None:
        if src == dst or src not in nodes or dst not in nodes:
            return
        key = (src, dst, kind, context)
        if key in seen_edges:
            return
        seen_edges.add(key)
        edge = {'from': src, 'to': dst, 'kind': kind}
        if context:
            edge['context'] = context
        if file:
            edge['file'] = file
        if line:
            edge['line'] = line
        edges.append(edge)

    def is_indirect_call(call):
        if getattr(call, 'indirect_call', False):
            return True
        if isinstance(call.callee, A.MemberExpr):
            receiver = getattr(call.callee.object, 'inferred_type', None)
            return getattr(receiver, 'kind', None) == 'InterfaceType'
        return False

    def record_type_usage(src_id: str, type_name: str, file: str, line: int, context: str) -> None:
        target_id = resolve_node_id(type_name, ('interface', 'class', 'struct', 'enum'))
        if target_id is None:
            return
        add_edge(src_id, target_id, 'uses_type', file=file, line=line, context=context)
        if target_id not in nodes[src_id].setdefault('uses_types', []):
            nodes[src_id]['uses_types'].append(target_id)
        if src_id not in nodes[target_id].setdefault('used_by', []):
            nodes[target_id]['used_by'].append(src_id)

    def record_instantiation(src_id: str, type_name: str, file: str, line: int) -> None:
        target_id = resolve_node_id(type_name, ('class', 'struct'))
        if target_id is None:
            return
        add_edge(src_id, target_id, 'instantiates', file=file, line=line)
        if target_id not in nodes[src_id].setdefault('instantiates', []):
            nodes[src_id]['instantiates'].append(target_id)
        if src_id not in nodes[target_id].setdefault('instantiated_by', []):
            nodes[target_id]['instantiated_by'].append(src_id)
        record_type_usage(src_id, type_name, file, line, 'instantiate')

    def record_call(src_id: str, target_id: str | None, file: str, line: int) -> None:
        if target_id is None or target_id not in nodes:
            return
        add_edge(src_id, target_id, 'calls', file=file, line=line)
        if target_id not in nodes[src_id].setdefault('calls', []):
            nodes[src_id]['calls'].append(target_id)
        if src_id not in nodes[target_id].setdefault('called_by', []):
            nodes[target_id]['called_by'].append(src_id)

    # Second pass: resolve structural, inheritance, type usage, instantiation, and call edges.
    for node_id, raw_name, stmt, stmt_file in decl_ast_pairs:
        if isinstance(stmt, A.ClassDecl):
            cls_node = nodes[node_id]
            if stmt.superclass:
                sup_raw = stmt.superclass.name if isinstance(stmt.superclass, A.TypeNode) else str(stmt.superclass)
                sup_id = resolve_node_id(sup_raw, ('class',))
                if sup_id:
                    cls_node['super_class'] = sup_id
                    if node_id not in nodes[sup_id]['subclasses']:
                        nodes[sup_id]['subclasses'].append(node_id)
                    add_edge(node_id, sup_id, 'extends', file=stmt_file, line=stmt.line)
            for intf_ref in stmt.interfaces:
                intf_raw = intf_ref.name if isinstance(intf_ref, A.TypeNode) else str(intf_ref)
                intf_id = resolve_node_id(intf_raw, ('interface',))
                if intf_id:
                    if intf_id not in cls_node['implements']:
                        cls_node['implements'].append(intf_id)
                    if node_id not in nodes[intf_id]['implemented_by']:
                        nodes[intf_id]['implemented_by'].append(node_id)
                    add_edge(node_id, intf_id, 'implements', file=stmt_file, line=stmt.line)
                    # Link concrete class methods to the interface methods they satisfy.
                    intf_methods = methods_by_owner_raw.get(intf_raw, {})
                    if not intf_methods:
                        short_i, _ = _decode_qualified_name(intf_raw, entry_mod_id)
                        intf_methods = methods_by_owner_raw.get(short_i, {})
                    for cm in stmt.methods:
                        cm_id = f"{node_id.replace('class:', 'method:')}.{cm.name}"
                        im_id = intf_methods.get(cm.name)
                        if cm_id in nodes and im_id and im_id in nodes:
                            nodes[cm_id]['implements_method'] = im_id
                            if cm_id not in nodes[im_id]['implemented_by']:
                                nodes[im_id]['implemented_by'].append(cm_id)
                            add_edge(cm_id, im_id, 'implements_method', file=stmt_file, line=cm.line)

            for field in stmt.fields:
                for type_ref in _extract_type_names_from_annotation(field.type_ann):
                    record_type_usage(node_id, type_ref, stmt_file, field.line, 'field')

        elif isinstance(stmt, A.StructDecl):
            for field in stmt.fields:
                for type_ref in _extract_type_names_from_annotation(field.type_ann):
                    record_type_usage(node_id, type_ref, stmt_file, field.line, 'field')

        elif isinstance(stmt, A.InterfaceDecl):
            for method in stmt.methods:
                m_id = f"{node_id.replace('interface:', 'method:')}.{method.name}"
                for p in method.params:
                    for type_ref in _extract_type_names_from_annotation(p.type_ann):
                        record_type_usage(m_id, type_ref, stmt_file, method.line, 'param')
                        record_type_usage(node_id, type_ref, stmt_file, method.line, 'param')
                for type_ref in _extract_type_names_from_annotation(method.return_type):
                    record_type_usage(m_id, type_ref, stmt_file, method.line, 'return')
                    record_type_usage(node_id, type_ref, stmt_file, method.line, 'return')

        elif isinstance(stmt, (A.FnDecl, A.MethodDecl)):
            for p in stmt.params:
                for type_ref in _extract_type_names_from_annotation(p.type_ann):
                    record_type_usage(node_id, type_ref, stmt_file, p.line, 'param')
            for type_ref in _extract_type_names_from_annotation(stmt.return_type):
                record_type_usage(node_id, type_ref, stmt_file, stmt.line, 'return')

            # Scan function/method body expressions for instantiations, calls, and type refs.
            owner_cls_id = nodes[node_id].get('owner') if isinstance(stmt, A.MethodDecl) else None
            for expr in _walk_expressions(stmt.body):
                expr_line = getattr(expr, 'line', stmt.line)
                if isinstance(expr, A.LetDecl) and expr.type_ann is not None:
                    for type_ref in _extract_type_names_from_annotation(expr.type_ann):
                        record_type_usage(node_id, type_ref, stmt_file, expr.line, 'local')
                elif isinstance(expr, A.NewExpr):
                    record_instantiation(node_id, expr.class_name, stmt_file, expr_line)
                    init_target = resolve_node_id(f'{expr.class_name}.init', ('method',))
                    if init_target:
                        record_call(node_id, init_target, stmt_file, expr_line)
                elif isinstance(expr, A.StructInitExpr):
                    record_instantiation(node_id, expr.struct_name, stmt_file, expr_line)
                elif isinstance(expr, A.CallExpr):
                    if is_indirect_call(expr):
                        nodes[node_id]['indirect_call'] = True
                    callee = expr.callee
                    if isinstance(callee, A.IdentExpr):
                        callee_raw = _identifier_target(callee, callee.name)
                        ctor_target = resolve_node_id(callee_raw, ('class', 'struct'))
                        if ctor_target is not None:
                            record_instantiation(node_id, callee_raw, stmt_file, expr_line)
                            if ctor_target.startswith('class:'):
                                init_target = resolve_node_id(f'{callee_raw}.init', ('method',))
                                if init_target:
                                    record_call(node_id, init_target, stmt_file, expr_line)
                        else:
                            record_call(node_id, resolve_node_id(callee_raw, ('fn', 'extern')), stmt_file, expr_line)
                    elif isinstance(callee, A.MemberExpr):
                        mod_member = getattr(callee, 'resolved_module_member', None)
                        if mod_member:
                            ctor_target = resolve_node_id(mod_member, ('class', 'struct'))
                            if ctor_target is not None:
                                record_instantiation(node_id, mod_member, stmt_file, expr_line)
                                if ctor_target.startswith('class:'):
                                    init_target = resolve_node_id(f'{mod_member}.init', ('method',))
                                    if init_target:
                                        record_call(node_id, init_target, stmt_file, expr_line)
                            else:
                                record_call(node_id, resolve_node_id(mod_member, ('fn', 'extern')), stmt_file, expr_line)
                        else:
                            recv_type = getattr(callee.object, 'inferred_type', None)
                            if recv_type is None and isinstance(callee.object, A.ThisExpr) and owner_cls_id:
                                owner_name = nodes[owner_cls_id]['qualified_name']
                                recv_display = owner_name
                            else:
                                recv_display = getattr(recv_type, 'display', '') if recv_type is not None else ''
                            base_type = recv_display.split('[', 1)[0].strip() if recv_display else ''
                            if base_type:
                                method_id = resolve_method_id(base_type, callee.property)
                                record_call(node_id, method_id, stmt_file, expr_line)
                                # If interface method is called, propagate edge from caller to interface usage.
                                owner_type_id = resolve_node_id(base_type, ('class', 'interface'))
                                if owner_type_id:
                                    record_type_usage(node_id, base_type, stmt_file, expr_line, 'call_receiver')

                reference = getattr(expr, 'function_reference', None)
                if reference:
                    target = resolve_node_id(_identifier_target(expr, reference), ('fn', 'extern'))
                    add_edge(node_id, target, 'references_function', file=stmt_file, line=expr_line)
                inferred = getattr(expr, 'inferred_type', None)
                if inferred is not None and getattr(inferred, 'kind', '') in ('ClassType', 'InterfaceType', 'StructType', 'EnumType'):
                    for type_token in _extract_type_names_from_type_string(getattr(inferred, 'display', '')):
                        record_type_usage(node_id, type_token, stmt_file, expr_line, 'expr')

    # Handle top-level script statements outside named functions in entry program.
    entry_program = unit_programs.get(entry_file, checked.program)
    script_node_id = 'fn:__entry__'
    top_calls = []
    top_references = []
    top_indirect_call = False
    top_instantiations = []
    top_type_usages = []
    for stmt in getattr(entry_program, 'body', ()):
        if isinstance(stmt, (A.FnDecl, A.ExternFnDecl, A.ClassDecl, A.InterfaceDecl, A.StructDecl, A.EnumDecl, A.ImportDecl)):
            continue
        for expr in _walk_expressions(stmt):
            expr_line = getattr(expr, 'line', getattr(stmt, 'line', 1))
            reference = getattr(expr, 'function_reference', None)
            if reference:
                top_references.append((_identifier_target(expr, reference), expr_line))
            if isinstance(expr, A.LetDecl) and expr.type_ann is not None:
                for type_ref in _extract_type_names_from_annotation(expr.type_ann):
                    top_type_usages.append((type_ref, expr.line, 'local'))
            elif isinstance(expr, A.NewExpr):
                top_instantiations.append((expr.class_name, expr_line))
                top_calls.append((f'{expr.class_name}.init', ('method',), expr_line))
            elif isinstance(expr, A.StructInitExpr):
                top_instantiations.append((expr.struct_name, expr_line))
            elif isinstance(expr, A.CallExpr):
                top_indirect_call |= is_indirect_call(expr)
                callee = expr.callee
                if isinstance(callee, A.IdentExpr):
                    callee_raw = _identifier_target(callee, callee.name)
                    ctor_target = resolve_node_id(callee_raw, ('class', 'struct'))
                    if ctor_target is not None:
                        top_instantiations.append((callee_raw, expr_line))
                        if ctor_target.startswith('class:'):
                            top_calls.append((f'{callee_raw}.init', ('method',), expr_line))
                    else:
                        top_calls.append((callee_raw, ('fn', 'extern'), expr_line))
                elif isinstance(callee, A.MemberExpr):
                    mod_member = getattr(callee, 'resolved_module_member', None)
                    if mod_member:
                        ctor_target = resolve_node_id(mod_member, ('class', 'struct'))
                        if ctor_target is not None:
                            top_instantiations.append((mod_member, expr_line))
                            if ctor_target.startswith('class:'):
                                top_calls.append((f'{mod_member}.init', ('method',), expr_line))
                        else:
                            top_calls.append((mod_member, ('fn', 'extern'), expr_line))
                    else:
                        recv_type = getattr(callee.object, 'inferred_type', None)
                        recv_display = getattr(recv_type, 'display', '') if recv_type is not None else ''
                        base_type = recv_display.split('[', 1)[0].strip() if recv_display else ''
                        if base_type:
                            top_calls.append(('method_lookup', (base_type, callee.property), expr_line))

    if top_calls or top_instantiations or top_type_usages or top_references or top_indirect_call:
        nodes[script_node_id] = {
            'id': script_node_id,
            'name': '__entry__',
            'qualified_name': '__entry__',
            'kind': 'entrypoint',
            'module': entry_mod_id,
            'file': entry_file,
            'line': 1,
            'col': 1,
            'start_byte': 0,
            'end_byte': 0,
            'end_line': 1,
            'calls': [],
            'instantiates': [],
            'uses_types': [],
        }
        if top_indirect_call:
            nodes[script_node_id]['indirect_call'] = True
        for reference, expr_line in top_references:
            target = resolve_node_id(reference, ('fn', 'extern'))
            add_edge(script_node_id, target, 'references_function', file=entry_file, line=expr_line)
        for type_ref, expr_line, ctx in top_type_usages:
            record_type_usage(script_node_id, type_ref, entry_file, expr_line, ctx)
        for type_ref, expr_line in top_instantiations:
            record_instantiation(script_node_id, type_ref, entry_file, expr_line)
        for target_ref, target_kinds, expr_line in top_calls:
            if target_ref == 'method_lookup':
                base_t, prop = target_kinds
                record_call(script_node_id, resolve_method_id(base_t, prop), entry_file, expr_line)
            else:
                record_call(script_node_id, resolve_node_id(target_ref, target_kinds), entry_file, expr_line)

    # Merge static effects from Explain analysis when available.
    if effects_report and isinstance(effects_report, dict):
        effect_by_name = {
            row.get('name'): row for row in effects_report.get('functions', ())
        }
        for node_id, node in nodes.items():
            if node['kind'] in ('fn', 'method'):
                row = effect_by_name.get(node['qualified_name']) or effect_by_name.get(node['name'])
                if row:
                    node['effects'] = list(row.get('transitive_effects', ()))
                    caps = sorted({
                        cap for eff in node['effects']
                        for cap in (_effect_to_capability(eff),) if cap
                    })
                    node['capabilities'] = caps

    # Sort all node lists and collections deterministically before computing graph_hash.
    for node in nodes.values():
        for key in (
            'implemented_by', 'used_by', 'subclasses', 'implements',
            'instantiated_by', 'calls', 'called_by', 'instantiates',
            'uses_types', 'effects', 'capabilities',
        ):
            if key in node and isinstance(node[key], list):
                node[key] = sorted(set(node[key]))

    sorted_nodes = [nodes[key] for key in sorted(nodes)]
    sorted_edges = sorted(
        edges,
        key=lambda e: (e['from'], e['to'], e['kind'], e.get('context', ''), e.get('line', 0)),
    )

    source_pairs = sorted((str(Path(k).resolve()), sources[k].content_hash) for k in sources)
    hash_payload = '\n'.join(f'{path}={digest}' for path, digest in source_pairs).encode('utf-8')
    graph_hash = 'graph:' + hashlib.sha256(hash_payload).hexdigest()[:16]

    return {
        'schema_version': 'wysna.graph/1',
        'graph_hash': graph_hash,
        'entry_file': entry_file,
        'summary': {
            'modules': len(module_nodes),
            'nodes': len(sorted_nodes),
            'edges': len(sorted_edges),
            'interfaces': sum(1 for n in sorted_nodes if n['kind'] == 'interface'),
            'classes': sum(1 for n in sorted_nodes if n['kind'] == 'class'),
            'structs': sum(1 for n in sorted_nodes if n['kind'] == 'struct'),
            'enums': sum(1 for n in sorted_nodes if n['kind'] == 'enum'),
            'functions': sum(1 for n in sorted_nodes if n['kind'] == 'fn'),
            'methods': sum(1 for n in sorted_nodes if n['kind'] == 'method'),
        },
        'modules': sorted(module_nodes, key=lambda m: m['id']),
        'nodes': sorted_nodes,
        'edges': sorted_edges,
    }


def _effect_to_capability(effect: str) -> str | None:
    mapping = {
        'stdio.write': 'stdio',
        'stdio.read': 'stdio',
        'arguments.read': 'arguments',
        'environment.read': 'environment',
        'clock.read': 'clock',
        'filesystem.read': 'filesystem.read',
        'filesystem.write': 'filesystem.write',
        'network.http': 'network',
        'process.spawn': 'processes',
        'process.wait': 'processes',
        'process.exit': 'process-control',
        'ffi.native': 'native-ffi',
    }
    return mapping.get(effect)


def find_graph_node(graph: dict, symbol: str) -> dict | None:
    """Resolve a symbol query by exact id, qualified_name, or unique short name."""
    nodes = graph.get('nodes', [])
    for node in nodes:
        if node['id'] == symbol:
            return node
    for node in nodes:
        if node['qualified_name'] == symbol:
            return node
    matches = [node for node in nodes if node['name'] == symbol]
    if len(matches) == 1:
        return matches[0]
    if matches:
        # Prefer top-level declarations over interface/method duplicates when ambiguous.
        top_matches = [n for n in matches if n['kind'] in ('interface', 'class', 'struct', 'enum', 'fn', 'extern')]
        if len(top_matches) == 1:
            return top_matches[0]
        return matches[0]
    suffix_matches = [
        node for node in nodes
        if node['qualified_name'].endswith('::' + symbol) or node['qualified_name'].endswith('.' + symbol)
    ]
    return suffix_matches[0] if len(suffix_matches) == 1 else None


def resolve_graph_symbol(graph: dict, symbol: str, stage: str) -> dict | None:
    """Resolve user-supplied selectors without choosing among homonyms."""
    nodes = graph['nodes']
    for field in ('id', 'qualified_name', 'name'):
        matches = [node for node in nodes if node[field] == symbol
                   and not (field == 'qualified_name' and node['name'] == symbol)]
        if matches:
            break
    else:
        matches = [node for node in nodes
                   if node['qualified_name'].endswith(('::' + symbol, '.' + symbol))]
    if len(matches) < 2:
        return matches[0] if matches else None
    fields = ('id', 'qualified_name', 'kind', 'file', 'line', 'col', 'signature')
    candidates = [{field: node.get(field, '') for field in fields}
                  for node in sorted(matches, key=lambda item: (item['id'], item['file'], item['line']))]
    choices = ', '.join(candidate['id'] for candidate in candidates)
    error = WysnaError(f"Ambiguous symbol '{symbol}'; pass a candidate ID to --symbol: {choices}",
                       file=graph['entry_file'], code='W1005', stage=stage)
    error.data = {'kind': 'ambiguous_symbol', 'symbol': symbol,
                  'graph_hash': graph['graph_hash'], 'candidates': candidates}
    raise error


def compute_blast_radius(graph: dict, target_id: str) -> dict:
    """Compute direct and transitive reverse dependents for a target graph node."""
    edges = graph.get('edges', [])
    reverse_adj = {}
    for edge in edges:
        reverse_adj.setdefault(edge['to'], set()).add((edge['from'], edge['kind']))

    direct = sorted({src for src, _ in reverse_adj.get(target_id, ())})
    visited = {target_id}
    queue = deque([target_id])
    transitive = set()
    by_kind = {}
    while queue:
        current = queue.popleft()
        for src, rel_kind in sorted(reverse_adj.get(current, ())):
            if current == target_id:
                by_kind.setdefault(rel_kind, []).append(src)
            if src not in visited:
                visited.add(src)
                transitive.add(src)
                queue.append(src)

    return {
        'target': target_id,
        'direct_dependents': direct,
        'transitive_dependents': sorted(transitive),
        'direct_by_relation': {k: sorted(set(v)) for k, v in sorted(by_kind.items())},
        'impact_count': len(transitive),
    }


def query_semantic_graph(graph: dict, *, symbol: str | None = None, relation: str | None = None) -> dict:
    """Filter a semantic graph by symbol and/or relationship view."""
    if symbol is None and relation is None:
        return graph

    node = resolve_graph_symbol(graph, symbol, 'graph') if symbol else None
    if symbol and node is None:
        return {
            'schema_version': graph['schema_version'],
            'graph_hash': graph['graph_hash'],
            'query': {'symbol': symbol, 'relation': relation},
            'found': False,
            'node': None,
            'edges': [],
        }

    if node is None:
        filtered_edges = [
            e for e in graph.get('edges', [])
            if relation is None or e['kind'] == relation or (relation == 'hierarchy' and e['kind'] in ('extends', 'implements'))
        ]
        return {
            'schema_version': graph['schema_version'],
            'graph_hash': graph['graph_hash'],
            'query': {'symbol': None, 'relation': relation},
            'edges': filtered_edges,
            'summary': graph['summary'],
        }

    node_id = node['id']
    edges = graph.get('edges', [])
    blast = compute_blast_radius(graph, node_id)

    if relation == 'implements':
        related_edges = [e for e in edges if e['kind'] in ('implements', 'implements_method') and (e['from'] == node_id or e['to'] == node_id)]
    elif relation == 'hierarchy':
        related_edges = [e for e in edges if e['kind'] in ('extends', 'implements') and (e['from'] == node_id or e['to'] == node_id)]
    elif relation == 'usages':
        related_edges = [e for e in edges if e['kind'] in ('uses_type', 'instantiates', 'implements') and (e['to'] == node_id or e['from'] == node_id)]
    elif relation == 'callers':
        related_edges = [e for e in edges if e['kind'] == 'calls' and e['to'] == node_id]
    elif relation == 'callees':
        related_edges = [e for e in edges if e['kind'] == 'calls' and e['from'] == node_id]
    elif relation == 'impact':
        affected_ids = set(blast['transitive_dependents']) | {node_id}
        related_edges = [e for e in edges if e['from'] in affected_ids and e['to'] in affected_ids]
    else:
        related_edges = [e for e in edges if e['from'] == node_id or e['to'] == node_id]

    return {
        'schema_version': graph['schema_version'],
        'graph_hash': graph['graph_hash'],
        'query': {'symbol': symbol, 'relation': relation},
        'found': True,
        'node': node,
        'impact': blast,
        'edges': related_edges,
    }
