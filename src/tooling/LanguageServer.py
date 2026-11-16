"""LSP over stdio using checked modules, lexical scopes and source snapshots."""
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname
from checker.Lints import lint_discarded_expressions
from common.constants import KEYWORDS
from compiler.CheckedProgram import SymbolInfo
from compiler.CompilerService import CompilerService
from compiler.SemanticGraph import build_semantic_graph, find_graph_node
from error.Error import WysnaError
from formatter.Formatter import Formatter
from lexer.Lexer import Lexer
from node.NodeType import (Node, IdentExpr, MemberExpr, FnDecl, ExternFnDecl, LetDecl, Param,
                           StructDecl, StructField, EnumDecl, VariantDecl, ClassDecl, ClassField,
                           InterfaceDecl, InterfaceMethod, MethodDecl, TypeParam, IdentifierPattern)
from parser.Parser import Parser
from tokens.TokenType import TokenType


DECLARATIONS = (FnDecl, ExternFnDecl, LetDecl, Param, StructDecl, StructField, EnumDecl,
                VariantDecl, ClassDecl, ClassField, InterfaceDecl, InterfaceMethod, MethodDecl,
                TypeParam, IdentifierPattern)
DECLARATION_KINDS = {FnDecl: 'fn', ExternFnDecl: 'fn', MethodDecl: 'fn', InterfaceMethod: 'fn',
                     LetDecl: 'var', IdentifierPattern: 'var', Param: 'param',
                     StructDecl: 'struct', StructField: 'field', EnumDecl: 'enum',
                     VariantDecl: 'variant', ClassDecl: 'class', ClassField: 'field',
                     InterfaceDecl: 'interface', TypeParam: 'typeparam'}


def source_path(uri):
    parsed = urlparse(uri)
    if parsed.scheme != 'file' or parsed.netloc not in ('', 'localhost'):
        raise ValueError('Only local file URIs are supported')
    return str(Path(url2pathname(parsed.path)).resolve())


def offset_at(source, position):
    lines = source.splitlines(keepends=True)
    line = position['line']
    if line >= len(lines):
        return len(source)
    units, column = 0, 0
    for character in lines[line]:
        width = len(character.encode('utf-16-le')) // 2
        if units + width > position['character']:
            break
        units += width
        column += 1
    return sum(map(len, lines[:line])) + column


def position_at(source, offset):
    prefix = source[:offset]
    line = prefix.count('\n')
    last = prefix.rsplit('\n', 1)[-1]
    return {'line': line, 'character': len(last.encode('utf-16-le')) // 2}


def nodes(node):
    if isinstance(node, Node):
        yield node
        for child in vars(node).values():
            if isinstance(child, (Node, list)):
                yield from nodes(child)
    elif isinstance(node, list):
        for child in node:
            yield from nodes(child)


class LanguageServer:
    def __init__(self, send):
        self.send = send
        self.compiler = CompilerService()
        self.documents = {}
        self.checked = {}
        self.fixes = {}
        self._graphs = {}
        self.roots = []
        self.stopping = False

    def _graph_for(self, uri):
        unit = self.checked.get(uri)
        if unit is None:
            return None, None
        key = id(unit)
        if self._graphs.get(uri, (None, None))[0] != key:
            self._graphs[uri] = (key, build_semantic_graph(unit))
        return unit, self._graphs[uri][1]

    def _diagnostic_entry(self, item, path, source, severity=1):
        message = item.message
        if getattr(item, 'help', None):
            message += '\nHelp: ' + item.help
        error_source = self.compiler.source_text(item.file, source)
        lines = error_source.splitlines(keepends=True)
        start = sum(map(len, lines[:max(item.line - 1, 0)])) + max(item.col - 1, 0)
        end = min(start + 1, len(error_source))
        try:
            token = next((t for t in Lexer(error_source).tokenize()
                          if t.line == item.line and t.col == item.col), None)
            if token and hasattr(token, 'end_offset'):
                end = token.end_offset
        except WysnaError:
            pass
        if item.file != path:
            # Dependency errors must not use another file's coordinates in this document.
            return {'range': {'start': position_at(source, 0), 'end': position_at(source, 0)},
                    'severity': severity, 'code': item.code, 'source': 'Wysna',
                    'message': f'{item.file}:{item.line}:{item.col}: {message}'}, []
        diag_range = {'start': position_at(source, start), 'end': position_at(source, end)}
        entry = {'range': diag_range, 'severity': severity, 'code': item.code,
                 'source': 'Wysna', 'message': message}
        actions = []
        for fix in getattr(item, 'fixes', ()):
            text_edits = []
            for edit in fix.get('edits', ()):
                edit_line = edit.get('line', 1)
                edit_col = edit.get('col', 1)
                old_text = edit.get('old', '')
                line_start = sum(map(len, lines[:max(edit_line - 1, 0)]))
                line_end = line_start + len(lines[edit_line - 1].rstrip('\r\n')) if 0 < edit_line <= len(lines) else len(source)
                col_offset = min(line_start + max(edit_col - 1, 0), line_end)
                idx = source.find(old_text, col_offset, line_end) if old_text else col_offset
                if idx < 0:
                    text_edits = []
                    break
                text_edits.append({
                    'range': {
                        'start': position_at(source, idx),
                        'end': position_at(source, idx + len(old_text)),
                    },
                    'newText': edit.get('new', ''),
                })
            if text_edits:
                actions.append({
                    'range': diag_range,
                    'code': item.code,
                    'title': fix.get('description', 'Apply fix'),
                    'applicability': fix.get('applicability', 'maybe'),
                    'edits': text_edits,
                })
        return entry, actions

    def publish(self, uri):
        document = self.documents[uri]
        path, source = source_path(uri), document['text']
        diagnostics = []
        fix_records = []
        self.checked.pop(uri, None)
        try:
            parsed = self.compiler.parse(source, path, recover=True)
            checked = self.compiler.check_parsed(parsed)
            self.checked[uri] = checked
            warnings = list(lint_discarded_expressions(checked.ast, path))
            warnings.extend(self.compiler.analyze_reference_cycles(checked))
            for warning in warnings:
                entry, actions = self._diagnostic_entry(warning, path, source, severity=2)
                diagnostics.append(entry)
                fix_records.extend(actions)
        except WysnaError as error:
            for item in getattr(error, 'diagnostics', (error,)):
                entry, actions = self._diagnostic_entry(item, path, source, severity=1)
                diagnostics.append(entry)
                fix_records.extend(actions)
        self.fixes[uri] = fix_records
        self.send({'jsonrpc': '2.0', 'method': 'textDocument/publishDiagnostics',
                   'params': {'uri': uri, 'version': document['version'], 'diagnostics': diagnostics}})

    def update(self, uri, text, version):
        if uri in self.documents and version <= self.documents[uri]['version']:
            return
        self.documents[uri] = {'text': text, 'version': version}
        self.compiler.set_overlay(source_path(uri), text)
        # Recheck open dependents as well; the service invalidates changed snapshots.
        for opened in list(self.documents):
            self.publish(opened)

    def change(self, uri, changes, version):
        """Apply ordered LSP content changes, including ranged edits."""
        document = self.documents.get(uri)
        if document is None:
            return
        text = document['text']
        for change in changes:
            if 'range' in change:
                start = offset_at(text, change['range']['start'])
                end = offset_at(text, change['range']['end'])
                text = text[:start] + change['text'] + text[end:]
            else:
                text = change['text']
        self.update(uri, text, version)

    def token_at(self, uri, position):
        source = self.documents[uri]['text']
        offset = offset_at(source, position)
        return next((token for token in Lexer(source).tokenize()
                     if getattr(token, 'start_offset', -1) <= offset < getattr(token, 'end_offset', -1)), None)

    def definition(self, uri, position):
        unit = self.checked.get(uri)
        if not unit:
            return None
        symbol = self.symbol_at(uri, position)
        if symbol is None or not Path(getattr(symbol, 'file', '')).is_absolute():
            return None
        path = symbol.file
        source = unit.source_text(path)
        name = self._source_name(symbol.name)
        target = next((t for t in Lexer(source).tokenize()
                       if t.line == symbol.line and t.col >= symbol.col and t.value == name), None)
        if target is None:
            return None
        return {'uri': Path(path).as_uri(), 'range': {'start': position_at(source, target.start_offset),
                                                     'end': position_at(source, target.end_offset)}}

    def symbol_at(self, uri, position):
        unit = self.checked.get(uri)
        if not unit:
            return None
        token = self.token_at(uri, position)
        if not token:
            return None
        source = self.documents[uri]['text']
        path = source_path(uri)
        tokens = Lexer(source).tokenize()
        previous = next((tokens[index - 1] for index, candidate in enumerate(tokens)
                         if candidate.line == token.line and candidate.col == token.col and index > 0), None)
        tree = list(nodes(unit.program))
        for node in tree:
            if isinstance(node, IdentExpr) and (node.line, node.col) == (token.line, token.col):
                symbol = getattr(node, 'resolved_symbol', None)
                if symbol is not None:
                    return SymbolInfo.from_symbol(symbol)
            if (isinstance(node, MemberExpr) and getattr(node, 'resolved_module_member', None)
                    and previous and (node.line, node.col) == (previous.line, previous.col)
                    and node.property == token.value):
                symbol = unit.lookup_symbol(node.resolved_module_member)
                if symbol:
                    return symbol
            resolved = getattr(node, 'resolved_symbol', None)
            if resolved is not None:
                info = resolved if isinstance(resolved, SymbolInfo) else SymbolInfo.from_symbol(resolved)
                if (info.file, info.line, info.col, info.name) == (
                        path, token.line, token.col, token.value):
                    return info
        for node in tree:
            if (isinstance(node, DECLARATIONS) and getattr(node, 'source_file', None) == path
                    and getattr(node, 'line', 0) == token.line
                    and getattr(node, 'name', None) == token.value):
                column = next((candidate.col for candidate in tokens
                               if candidate.line == node.line and candidate.col >= node.col
                               and candidate.value == node.name), None)
                if column != token.col:
                    continue
                kind = DECLARATION_KINDS[type(node)]
                declaration = unit.lookup_symbol(node.name)
                if (declaration is not None and declaration.file == path
                        and declaration.line == node.line and declaration.kind == kind):
                    return declaration
                for candidate in tree:
                    resolved = getattr(candidate, 'resolved_symbol', None)
                    if resolved is None:
                        continue
                    info = resolved if isinstance(resolved, SymbolInfo) else SymbolInfo.from_symbol(resolved)
                    if self._symbol_key(info) == (path, node.line, node.col, node.name):
                        return info
                return SymbolInfo(token.value, self._declared_type(node), False,
                                  kind, token.line, token.col, path)
        return None

    @staticmethod
    def _declared_type(node):
        if isinstance(node, (FnDecl, MethodDecl, ExternFnDecl, InterfaceMethod)):
            params = ', '.join(f"{param.name}: {param.type_ann}" for param in node.params)
            return f"fn({params}) -> {node.return_type}"
        if isinstance(node, (LetDecl, Param, StructField, ClassField, TypeParam)):
            return str(node.type_ann) if node.type_ann else ''
        return ''

    def hover(self, uri, position):
        symbol = self.symbol_at(uri, position)
        if symbol is None:
            return None
        label = f"{symbol.kind} {self._source_name(symbol.name)}"
        if symbol.type:
            label += f": {symbol.type}"
        lines = [f'```wysna\n{label}\n```']
        if symbol.kind in ('interface', 'class', 'struct', 'enum'):
            _, graph = self._graph_for(uri)
            if graph is not None:
                gnode = find_graph_node(graph, symbol.name) or find_graph_node(graph, self._source_name(symbol.name))
                if gnode is not None:
                    node_names = {n['id']: n['qualified_name'] for n in graph.get('nodes', ())}
                    def fmt_ids(ids):
                        return ', '.join(node_names.get(item, item) for item in ids)
                    relations = []
                    if gnode.get('implemented_by'):
                        relations.append(f"- **Implemented by:** {fmt_ids(gnode['implemented_by'])}")
                    if gnode.get('implements'):
                        relations.append(f"- **Implements:** {fmt_ids(gnode['implements'])}")
                    if gnode.get('super_class'):
                        relations.append(f"- **Extends:** {node_names.get(gnode['super_class'], gnode['super_class'])}")
                    if gnode.get('subclasses'):
                        relations.append(f"- **Subclasses:** {fmt_ids(gnode['subclasses'])}")
                    if gnode.get('instantiated_by'):
                        relations.append(f"- **Instantiated by:** {fmt_ids(gnode['instantiated_by'])}")
                    if gnode.get('used_by'):
                        relations.append(f"- **Used by:** {fmt_ids(gnode['used_by'])}")
                    if relations:
                        lines.append('\n'.join(relations))
        return {'contents': {'kind': 'markdown', 'value': '\n\n'.join(lines)}}

    def implementation(self, uri, position):
        unit, graph = self._graph_for(uri)
        if unit is None or graph is None:
            return []
        symbol = self.symbol_at(uri, position)
        candidates = []
        if symbol is not None:
            candidates.extend((symbol.name, self._source_name(symbol.name)))
        token = self.token_at(uri, position)
        if token is not None and token.value:
            candidates.append(token.value)

        gnode = None
        for name in candidates:
            gnode = find_graph_node(graph, name)
            if gnode is not None:
                break

        # If cursor sits on an interface method call (MemberExpr), resolve its interface_method node.
        if (gnode is None or gnode['kind'] not in ('interface', 'interface_method', 'class')) and token is not None:
            for ast_node in nodes(unit.program):
                if isinstance(ast_node, MemberExpr) and ast_node.property == token.value and ast_node.line == token.line:
                    recv_type = getattr(ast_node.object, 'inferred_type', None)
                    recv_name = getattr(recv_type, 'display', '').split('[', 1)[0].strip() if recv_type else ''
                    if recv_name:
                        cand_node = find_graph_node(graph, f'{recv_name}.{token.value}')
                        if cand_node is not None:
                            gnode = cand_node
                            break

        if gnode is None:
            return []

        target_ids = []
        if gnode['kind'] in ('interface', 'interface_method'):
            target_ids = list(gnode.get('implemented_by', ()))
        elif gnode['kind'] == 'class':
            target_ids = list(gnode.get('subclasses', ()))

        nodes_by_id = {n['id']: n for n in graph.get('nodes', ())}
        locations, seen = [], set()
        for tid in target_ids:
            tnode = nodes_by_id.get(tid)
            if tnode is None:
                continue
            path = tnode['file']
            source = unit.source_text(path)
            if not source and Path(path).is_file():
                try:
                    source = Path(path).read_text(encoding='utf-8')
                except OSError:
                    source = ''
            if not source:
                continue
            tok = next(
                (t for t in Lexer(source).tokenize()
                 if t.line >= tnode['line'] and t.value == tnode['name']),
                None,
            )
            if tok is None:
                continue
            loc = {
                'uri': Path(path).as_uri(),
                'range': {
                    'start': position_at(source, tok.start_offset),
                    'end': position_at(source, tok.end_offset),
                },
            }
            key = (loc['uri'], loc['range']['start']['line'], loc['range']['start']['character'])
            if key not in seen:
                seen.add(key)
                locations.append(loc)
        return sorted(locations, key=lambda item: (item['uri'], item['range']['start']['line'], item['range']['start']['character']))

    @staticmethod
    def _source_name(name):
        name = str(name)
        prefix = '__wysna_module_'
        if name.startswith(prefix) and '__' in name[len(prefix):]:
            return name[len(prefix):].split('__', 1)[1]
        return name

    @staticmethod
    def _symbol_key(symbol):
        return (symbol.file, symbol.line, symbol.col, symbol.name)

    def _location_at(self, path, line, column, name, after=False, units=None):
        # Units for other roots do not snapshot this path and return an empty default.
        pool = (units if units is not None else self.checked).values()
        source = next((text for text in (unit.source_text(path) for unit in pool)
                       if text), None)
        if source is None:
            return None
        candidates = [candidate for candidate in Lexer(source).tokenize()
                      if candidate.line == line and candidate.value == name
                      and (candidate.col > column if after else candidate.col == column)]
        token = min(candidates, key=lambda candidate: candidate.col, default=None)
        if token is None:
            return None
        return {'uri': Path(path).as_uri(),
                'range': {'start': position_at(source, token.start_offset),
                          'end': position_at(source, token.end_offset)}}

    def references(self, uri, position, include_declaration=True, units=None):
        symbol = self.symbol_at(uri, position)
        if symbol is None:
            return []
        identity = self._symbol_key(symbol)
        locations, seen = [], set()

        def add(path, line, column, name, after=False, fallback=False):
            location = self._location_at(path, line, column, name, after=after, units=units)
            if location is None and fallback:
                location = self._location_at(path, line, 0, name, after=True, units=units)
            if location is not None:
                start = location['range']['start']
                key = (location['uri'], start['line'], start['character'])
                if key not in seen:
                    seen.add(key)
                    locations.append(location)

        for checked_uri, unit in (units if units is not None else self.checked).items():
            for node in nodes(unit.program):
                path = getattr(node, 'source_file', None) or source_path(checked_uri)
                resolved = getattr(node, 'resolved_symbol', None)
                if resolved is not None:
                    info = resolved if isinstance(resolved, SymbolInfo) else SymbolInfo.from_symbol(resolved)
                    if self._symbol_key(info) == identity:
                        if isinstance(node, DECLARATIONS) and not include_declaration:
                            continue
                        if isinstance(node, MemberExpr):
                            add(path, node.line, node.col, node.property, after=True)
                        else:
                            add(path, node.line, node.col,
                                getattr(node, 'property', getattr(node, 'name', '')))
                        continue
                if (path, getattr(node, 'line', 0), getattr(node, 'col', 0),
                        getattr(node, 'name', '')) == identity:
                    if isinstance(node, DECLARATIONS) and not include_declaration:
                        continue
                    add(path, node.line, node.col, node.name)
        if include_declaration and Path(symbol.file).is_absolute():
            add(symbol.file, symbol.line, symbol.col, self._source_name(symbol.name),
                fallback=True)
        return sorted(locations, key=lambda item: (item['uri'], item['range']['start']['line'],
                                                   item['range']['start']['character']))

    WORKSPACE_SKIP_DIRECTORIES = frozenset({'node_modules', 'build', 'dist', 'target', '__pycache__'})
    WORKSPACE_FILE_LIMIT = 5000

    def set_roots(self, params):
        roots = []
        for folder in params.get('workspaceFolders') or ():
            roots.append(source_path(folder['uri']))
        if params.get('rootUri'):
            roots.append(source_path(params['rootUri']))
        self.roots = list(dict.fromkeys(root for root in roots if os.path.isdir(root)))

    def workspace_files(self):
        files = []
        for root in self.roots:
            for directory, children, names in os.walk(root):
                children[:] = sorted(child for child in children
                                     if not child.startswith('.')
                                     and child not in self.WORKSPACE_SKIP_DIRECTORIES)
                files.extend(str(Path(directory, name).resolve())
                             for name in sorted(names) if name.endswith('.wysna'))
                if len(files) > self.WORKSPACE_FILE_LIMIT:
                    return None
        return sorted(set(files))

    def workspace_units(self):
        """Checks every workspace source (open buffers win over disk). None if the index is incomplete."""
        files = self.workspace_files()
        if files is None:
            return None
        units = dict(self.checked)
        for path in files:
            uri = Path(path).as_uri()
            if uri in units:
                continue
            if uri in self.documents:
                return None
            try:
                units[uri] = self.compiler.check_file(path)
            except (WysnaError, OSError, UnicodeDecodeError):
                return None
        return units

    def _workspace_rename(self, uri, position, symbol, new_name):
        """Rename a top-level function or variable across the whole workspace, or refuse.

        The edit is produced only when every workspace file checks cleanly, every identifier
        spelled like the old name inside an edited file is a verified reference, and the new
        name collides with nothing. A partial edit is never returned.
        """
        units = self.workspace_units()
        if units is None:
            return None
        symbol_uri = Path(symbol.file).as_uri()
        owner = units.get(symbol_uri)
        if owner is None:
            return None
        declaration = next((node for node in owner.program.body
                            if getattr(node, 'name', None) == self._source_name(symbol.name)
                            and getattr(node, 'line', None) == symbol.line), None)
        if declaration is None or not isinstance(declaration, (FnDecl, LetDecl)):
            return None
        locations = self.references(uri, position, units=units)
        if not locations:
            return None
        from checker.SymbolTable import create_global_scope
        builtins = create_global_scope()
        if builtins.lookup_symbol(new_name) or builtins.lookup_type(new_name):
            return None
        original = self._source_name(symbol.name)
        by_uri = {}
        for location in locations:
            by_uri.setdefault(location['uri'], []).append(location)
        changes = {}
        for target_uri, group in by_uri.items():
            text = self.documents[target_uri]['text'] if target_uri in self.documents else next(
                (unit.source_text(source_path(target_uri)) for unit in units.values()
                 if unit.source_text(source_path(target_uri))), None)
            if text is None:
                return None
            try:
                tokens = Lexer(text).tokenize()
            except WysnaError:
                return None
            if any(token.type == TokenType.IDENTIFIER and token.value == new_name for token in tokens):
                return None
            occurrences = {token.start_offset for token in tokens
                           if token.type == TokenType.IDENTIFIER and token.value == original}
            edits = {offset_at(text, location['range']['start']) for location in group}
            if occurrences != edits:
                return None
            changes[target_uri] = [{'range': location['range'], 'newText': new_name}
                                   for location in group]
        return {'changes': changes}

    def rename(self, uri, position, new_name):
        if (not isinstance(new_name, str)
                or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', new_name)
                or new_name in KEYWORDS
                or new_name.startswith(('__wysna_', '_WYSNA_'))):
            return None
        symbol = self.symbol_at(uri, position)
        if symbol is None or symbol.kind not in ('var', 'param', 'fn'):
            return None
        if self.roots and symbol.kind in ('var', 'fn') and Path(symbol.file).is_absolute():
            workspace_edit = self._workspace_rename(uri, position, symbol, new_name)
            if workspace_edit is not None:
                return workspace_edit
        if symbol.file != source_path(uri):
            return None
        unit = self.checked.get(uri)
        if unit is None:
            return None
        declaration = next((node for node in unit.program.body
                            if getattr(node, 'name', None) == symbol.name
                            and getattr(node, 'line', None) == symbol.line), None)
        if getattr(declaration, 'is_exported', False) or (symbol.kind == 'fn' and declaration is None):
            return None
        locations = self.references(uri, position)
        if not locations or any(location['uri'] != uri for location in locations):
            return None
        source = self.documents[uri]['text']
        tokens = Lexer(source).tokenize()
        if any(token.type == TokenType.IDENTIFIER and token.value == new_name for token in tokens):
            return None
        from checker.SymbolTable import create_global_scope
        builtins = create_global_scope()
        if builtins.lookup_symbol(new_name) or builtins.lookup_type(new_name):
            return None
        # Refuse partial edits: unresolved syntactic uses may otherwise change behavior.
        original = self._source_name(symbol.name)
        occurrences = {token.start_offset for token in tokens
                       if token.type == TokenType.IDENTIFIER and token.value == original}
        edits = {offset_at(source, location['range']['start']) for location in locations}
        if occurrences != edits:
            return None
        changes = {}
        for location in locations:
            changes.setdefault(location['uri'], []).append(
                {'range': location['range'], 'newText': new_name})
        return {'changes': changes}

    SEMANTIC_TOKEN_TYPES = ['keyword', 'number', 'string', 'comment', 'function', 'method', 'variable',
                            'parameter', 'property', 'struct', 'enum', 'enumMember', 'class', 'interface',
                            'typeParameter', 'type', 'operator']
    SEMANTIC_TOKEN_MODIFIERS = ['declaration', 'readonly']
    _BUILTIN_TYPES = frozenset({'int', 'float', 'str', 'bool', 'unit', 'list', 'dict', 'tuple', 'opaque', 'fn', 'weak'})
    _KIND_TO_SEMANTIC = {'builtin': 'function', 'var': 'variable', 'param': 'parameter', 'fn': 'function', 'struct': 'struct',
                         'enum': 'enum', 'variant': 'enumMember', 'class': 'class', 'field': 'property',
                         'interface': 'interface', 'typeparam': 'typeParameter'}

    def _identifier_kinds(self, unit, path, tokens):
        """Maps (line, column) of identifier tokens to a symbol kind and a declaration flag."""
        kinds = {}
        by_line = {}
        for token in tokens:
            if token.type == TokenType.IDENTIFIER:
                by_line.setdefault(token.line, []).append(token)
        for node in nodes(unit.program):
            if isinstance(node, IdentExpr):
                symbol = getattr(node, 'resolved_symbol', None)
                if symbol is not None:
                    info = symbol if isinstance(symbol, SymbolInfo) else SymbolInfo.from_symbol(symbol)
                    kinds[(node.line, node.col)] = (info.kind, False)
            elif (isinstance(node, DECLARATIONS) and getattr(node, 'source_file', None) == path
                  and getattr(node, 'name', None)):
                candidate = next((t for t in by_line.get(node.line, ())
                                  if t.col >= node.col and t.value == node.name), None)
                if candidate is not None:
                    kinds[(candidate.line, candidate.col)] = (DECLARATION_KINDS[type(node)], True)
        return kinds

    def semantic_tokens(self, uri):
        """LSP textDocument/semanticTokens/full: lexical classes refined by checked symbol kinds."""
        document = self.documents.get(uri)
        if document is None:
            return {'data': []}
        source = document['text']
        try:
            tokens = Lexer(source).tokenize()
        except WysnaError:
            return {'data': []}
        unit = self.checked.get(uri)
        path = source_path(uri)
        kinds = self._identifier_kinds(unit, path, tokens) if unit is not None else {}
        legend = {name: index for index, name in enumerate(self.SEMANTIC_TOKEN_TYPES)}
        line_starts = [0]
        for index, character in enumerate(source):
            if character == '\n':
                line_starts.append(index + 1)

        def utf16(line_index, start, end):
            base = line_starts[line_index]
            prefix = len(source[base:start].encode('utf-16-le')) // 2
            return prefix, len(source[start:end].encode('utf-16-le')) // 2

        spans = []

        def add(start, end, type_name, modifiers=0):
            """Splits multi-line spans so every emitted token stays on one line."""
            cursor = start
            while cursor < end:
                line_index = source.count('\n', 0, cursor) if cursor == start else line_index + 1
                line_end = source.find('\n', cursor, end)
                piece_end = end if line_end < 0 else line_end
                if piece_end > cursor:
                    spans.append((line_index, cursor, piece_end, legend[type_name], modifiers))
                if line_end < 0:
                    break
                cursor = line_end + 1

        def emit_comments(gap_start, gap_end):
            """Emit line (#) and nested block (/* ... */) comments inside a non-code slice."""
            pos = gap_start
            while pos < gap_end:
                if source[pos] == '#':
                    line_end = source.find('\n', pos, gap_end)
                    stop = gap_end if line_end < 0 else line_end
                    add(pos, stop, 'comment')
                    pos = stop
                elif source.startswith('/*', pos) and pos + 2 <= gap_end:
                    c_start = pos
                    pos += 2
                    depth = 1
                    while pos < gap_end and depth > 0:
                        if source.startswith('/*', pos) and pos + 2 <= gap_end:
                            depth += 1
                            pos += 2
                        elif source.startswith('*/', pos) and pos + 2 <= gap_end:
                            depth -= 1
                            pos += 2
                        else:
                            pos += 1
                    add(c_start, pos, 'comment')
                else:
                    pos += 1

        previous_end, previous_type = 0, None
        for index, token in enumerate(tokens):
            start, end = getattr(token, 'start_offset', None), getattr(token, 'end_offset', None)
            if start is None or end is None or token.type in (TokenType.NEWLINE, TokenType.EOF):
                continue
            if start > previous_end:
                emit_comments(previous_end, start)
            previous_end = end
            if token.type == TokenType.INT or token.type == TokenType.FLOAT:
                add(start, end, 'number')
            elif token.type == TokenType.STR:
                add(start, end, 'string')
            elif token.type == TokenType.IDENTIFIER:
                kind, declaration = kinds.get((token.line, token.col), (None, False))
                modifiers = 1 if declaration else 0
                before = tokens[index - 1] if index else None
                after = tokens[index + 1] if index + 1 < len(tokens) else None
                if kind is not None:
                    add(start, end, self._KIND_TO_SEMANTIC.get(kind, 'variable'), modifiers)
                elif before is not None and before.type == TokenType.DOT:
                    called = after is not None and after.type == TokenType.LPAREN
                    add(start, end, 'method' if called else 'property')
                elif after is not None and after.type == TokenType.LPAREN:
                    add(start, end, 'function')
                elif str(token.value)[:1].isupper() or token.value in self._BUILTIN_TYPES:
                    add(start, end, 'type')
                else:
                    add(start, end, 'variable')
            elif token.type.name in ('TRUE', 'FALSE', 'NONE') or token.value in KEYWORDS:
                add(start, end, 'keyword')
        if previous_end < len(source):
            emit_comments(previous_end, len(source))
        spans.sort()
        data, last_line, last_char = [], 0, 0
        for line_index, start, end, type_index, modifiers in spans:
            column, length = utf16(line_index, start, end)
            delta_line = line_index - last_line
            delta_char = column if delta_line else column - last_char
            data.extend([delta_line, delta_char, length, type_index, modifiers])
            last_line, last_char = line_index, column
        return {'data': data}

    def formatting(self, uri):
        document = self.documents.get(uri)
        if document is None:
            return []
        source = document['text']
        try:
            program = Parser(file=source_path(uri)).parse(source)
            formatted = Formatter().format(program)
        except WysnaError:
            return []
        if formatted == source:
            return []
        return [{'range': {'start': position_at(source, 0), 'end': position_at(source, len(source))},
                 'newText': formatted}]

    def completion(self, uri, position):
        unit = self.checked.get(uri)
        if not unit:
            return {'isIncomplete': True, 'items': []}
        source = self.documents[uri]['text']
        offset = offset_at(source, position)
        location = (source[:offset].count('\n') + 1, len(source[:offset].rsplit('\n', 1)[-1]) + 1)
        symbols = unit.visible_symbols(*location)
        items = [{'label': name, 'kind': 3 if symbol.kind in ('fn', 'builtin') else 6,
                  'detail': str(symbol.type)} for name, symbol in sorted(symbols.items())]
        return {'isIncomplete': False, 'items': items}

    @staticmethod
    def _ranges_overlap(first, second):
        if first is None or second is None:
            return True
        f_start = (first['start']['line'], first['start']['character'])
        f_end = (first['end']['line'], first['end']['character'])
        s_start = (second['start']['line'], second['start']['character'])
        s_end = (second['end']['line'], second['end']['character'])
        return f_start <= s_end and s_start <= f_end

    def code_action(self, uri, range_=None):
        actions = []
        for fix in self.fixes.get(uri, ()):
            if range_ is not None and not self._ranges_overlap(fix['range'], range_):
                continue
            actions.append({
                'title': f"[{fix['code']}] {fix['title']}",
                'kind': 'quickfix',
                'isPreferred': fix['applicability'] == 'machine',
                'edit': {'changes': {uri: fix['edits']}},
            })
        return actions

    def signature_help(self, uri, position):
        document = self.documents.get(uri)
        if document is None:
            return None
        source = document['text']
        offset = offset_at(source, position)
        prefix = source[:offset]
        depth = 0
        commas = 0
        open_paren = -1
        for idx in range(len(prefix) - 1, -1, -1):
            ch = prefix[idx]
            if ch in ')]}':
                depth += 1
            elif ch in '([{':
                if depth > 0:
                    depth -= 1
                elif ch == '(':
                    open_paren = idx
                    break
                else:
                    return None
            elif ch == ',' and depth == 0:
                commas += 1
        if open_paren < 0:
            return None
        match = re.search(r'([A-Za-z_][A-Za-z0-9_]*)\s*$', prefix[:open_paren])
        if not match:
            return None
        callee = match.group(1)
        unit = self.checked.get(uri)
        sig_str = None
        param_labels = []
        if unit is not None:
            for node in unit.program.body:
                if isinstance(node, FnDecl) and self._source_name(node.name) == callee:
                    param_labels = [f"{p.name}: {p.type_ann}" for p in node.params]
                    ret = str(node.return_type) if node.return_type is not None else 'unit'
                    sig_str = f"fn {callee}({', '.join(param_labels)}) -> {ret}"
                    break
            if sig_str is None:
                location = (prefix.count('\n') + 1, len(prefix.rsplit('\n', 1)[-1]) + 1)
                sym = unit.visible_symbols(*location).get(callee)
                if sym is not None and str(sym.type).startswith('fn('):
                    sig_str = f"{callee}: {sym.type}"
                    inner = str(sym.type)[3:].split(') ->', 1)[0]
                    param_labels = [p.strip() for p in inner.split(',') if p.strip()]
        if sig_str is None:
            from checker.SymbolTable import create_global_scope
            sym = create_global_scope().lookup_symbol(callee)
            if sym is not None and str(sym.type).startswith('fn('):
                sig_str = f"{callee}: {sym.type}"
                inner = str(sym.type)[3:].split(') ->', 1)[0]
                param_labels = [p.strip() for p in inner.split(',') if p.strip()]
        if sig_str is None:
            return None
        active_param = min(commas, max(len(param_labels) - 1, 0))
        return {
            'signatures': [{
                'label': sig_str,
                'parameters': [{'label': p} for p in param_labels],
            }],
            'activeSignature': 0,
            'activeParameter': active_param,
        }

    def dispatch(self, message):
        method, params = message.get('method'), message.get('params', {})
        result = None
        if method == 'initialize':
            self.set_roots(params)
            result = {'capabilities': {'positionEncoding': 'utf-16', 'textDocumentSync': {'openClose': True, 'change': 2},
                                      'definitionProvider': True, 'hoverProvider': True,
                                      'implementationProvider': True,
                                      'documentFormattingProvider': True,
                                      'referencesProvider': True, 'renameProvider': True,
                                      'codeActionProvider': True,
                                      'semanticTokensProvider': {
                                          'legend': {'tokenTypes': self.SEMANTIC_TOKEN_TYPES,
                                                     'tokenModifiers': self.SEMANTIC_TOKEN_MODIFIERS},
                                          'full': True, 'range': False},
                                      'signatureHelpProvider': {'triggerCharacters': ['(', ',']},
                                      'completionProvider': {'triggerCharacters': ['.']}},
                      'serverInfo': {'name': 'wysna'}}
        elif method == 'shutdown':
            self.stopping = True
        elif method == 'exit':
            return False
        elif self.stopping:
            if 'id' in message:
                self.send({'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32600, 'message': 'Server is shutting down'}})
            return True
        elif method == 'textDocument/didOpen':
            doc = params['textDocument']
            self.update(doc['uri'], doc['text'], doc['version'])
        elif method == 'textDocument/didChange':
            doc = params['textDocument']
            if params['contentChanges']:
                self.change(doc['uri'], params['contentChanges'], doc['version'])
        elif method == 'textDocument/didClose':
            uri = params['textDocument']['uri']
            self.documents.pop(uri, None)
            self.checked.pop(uri, None)
            self.fixes.pop(uri, None)
            self.compiler.remove_overlay(source_path(uri))
            for opened in self.documents:
                self.publish(opened)
            self.send({'jsonrpc': '2.0', 'method': 'textDocument/publishDiagnostics', 'params': {'uri': uri, 'diagnostics': []}})
        elif method == 'textDocument/definition':
            result = self.definition(params['textDocument']['uri'], params['position'])
        elif method == 'textDocument/implementation':
            result = self.implementation(params['textDocument']['uri'], params['position'])
        elif method == 'textDocument/completion':
            result = self.completion(params['textDocument']['uri'], params['position'])
        elif method == 'textDocument/hover':
            result = self.hover(params['textDocument']['uri'], params['position'])
        elif method == 'textDocument/references':
            context = params.get('context', {})
            result = self.references(params['textDocument']['uri'], params['position'],
                                     include_declaration=context.get('includeDeclaration', True))
        elif method == 'textDocument/rename':
            result = self.rename(params['textDocument']['uri'], params['position'],
                                 params.get('newName', ''))
        elif method == 'textDocument/semanticTokens/full':
            result = self.semantic_tokens(params['textDocument']['uri'])
        elif method == 'textDocument/formatting':
            result = self.formatting(params['textDocument']['uri'])
        elif method == 'textDocument/codeAction':
            result = self.code_action(params['textDocument']['uri'], params.get('range'))
        elif method == 'textDocument/signatureHelp':
            result = self.signature_help(params['textDocument']['uri'], params['position'])
        elif 'id' in message:
            self.send({'jsonrpc': '2.0', 'id': message['id'], 'error': {'code': -32601, 'message': 'Method not found'}})
            return True
        if 'id' in message:
            self.send({'jsonrpc': '2.0', 'id': message['id'], 'result': result})
        return True


def serve(input_stream=None, output_stream=None):
    input_stream = input_stream or sys.stdin.buffer
    output_stream = output_stream or sys.stdout.buffer

    def send(message):
        payload = json.dumps(message, ensure_ascii=False).encode('utf-8')
        output_stream.write(f'Content-Length: {len(payload)}\r\n\r\n'.encode('ascii') + payload)
        output_stream.flush()

    server = LanguageServer(send)
    while True:
        headers = {}
        while True:
            line = input_stream.readline()
            if not line:
                return
            if line in (b'\r\n', b'\n'):
                break
            key, value = line.decode('ascii').split(':', 1)
            headers[key.lower()] = value.strip()
        length = int(headers.get('content-length', '0'))
        if not 0 < length <= 16 * 1024 * 1024:
            raise ValueError('Invalid LSP message length')
        payload = input_stream.read(length)
        if len(payload) != length:
            return
        try:
            message = json.loads(payload)
            if not isinstance(message, dict):
                raise ValueError('JSON-RPC message must be an object')
            if not server.dispatch(message):
                return
        except (ValueError, KeyError) as error:
            send({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32602, 'message': str(error)}})
        except Exception as error:
            # Protocol stdout must remain JSON-RPC even for a compiler invariant failure.
            send({'jsonrpc': '2.0', 'id': message.get('id'), 'error': {'code': -32603, 'message': 'W9999: ' + str(error)}})
