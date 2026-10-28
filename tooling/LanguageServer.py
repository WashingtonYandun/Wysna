"""LSP over stdio using checked modules, lexical scopes and source snapshots."""
import json
import os
from pathlib import Path
import re
import sys
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname
from common.constants import KEYWORDS
from compiler.CheckedProgram import SymbolInfo
from compiler.CompilerService import CompilerService
from error.Error import WysnaError
from formatter.Formatter import Formatter
from lexer.Lexer import Lexer
from node.NodeType import (Node, IdentExpr, MemberExpr, FnDecl, ExternFnDecl, LetDecl, Param,
                           StructDecl, StructField, EnumDecl, VariantDecl, ClassDecl, ClassField,
                           InterfaceDecl, InterfaceMethod, MethodDecl, TypeParam, IdentifierPattern)
from parser.Parser import Parser


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
        self.stopping = False

    def publish(self, uri):
        document = self.documents[uri]
        path, source = source_path(uri), document['text']
        diagnostics = []
        self.checked.pop(uri, None)
        try:
            self.checked[uri] = self.compiler.check(source, path)
        except WysnaError as error:
            for error in getattr(error, 'diagnostics', (error,)):
                message = error.message
                if getattr(error, 'help', None):
                    message += '\nHelp: ' + error.help
                error_source = self.compiler.source_text(error.file, source)
                lines = error_source.splitlines(keepends=True)
                start = sum(map(len, lines[:max(error.line - 1, 0)])) + max(error.col - 1, 0)
                end = min(start + 1, len(error_source))
                try:
                    token = next((t for t in Lexer(error_source).tokenize()
                                  if t.line == error.line and t.col == error.col), None)
                    if token and hasattr(token, 'end_offset'):
                        end = token.end_offset
                except WysnaError:
                    pass
                if error.file != path:
                    # Dependency errors must not use another file's coordinates in this document.
                    diagnostics.append({'range': {'start': position_at(source, 0), 'end': position_at(source, 0)},
                                        'severity': 1, 'code': error.code, 'source': 'Wysna',
                                        'message': f'{error.file}:{error.line}:{error.col}: {message}'})
                else:
                    diagnostics.append({'range': {'start': position_at(source, start), 'end': position_at(source, end)},
                                        'severity': 1, 'code': error.code, 'source': 'Wysna', 'message': message})
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
                       if t.line == symbol.line and t.value == name), None)
        lines = source.splitlines(keepends=True)
        start = target.start_offset if target else sum(map(len, lines[:symbol.line - 1])) + symbol.col - 1
        end = target.end_offset if target else start + len(name)
        return {'uri': Path(path).as_uri(), 'range': {'start': position_at(source, start), 'end': position_at(source, end)}}

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
        return {'contents': {'kind': 'markdown', 'value': f'```wysna\n{label}\n```'}}

    @staticmethod
    def _source_name(name):
        name = str(name)
        prefix = '__wysna_module_'
        if name.startswith(prefix) and '__' in name[len(prefix):]:
            return name.rsplit('__', 1)[-1]
        return name

    @staticmethod
    def _symbol_key(symbol):
        return (symbol.file, symbol.line, symbol.col, symbol.name)

    def _location_at(self, path, line, column, name, after=False):
        # Units for other roots do not snapshot this path and return an empty default.
        source = next((text for text in (unit.source_text(path) for unit in self.checked.values())
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

    def references(self, uri, position, include_declaration=True):
        symbol = self.symbol_at(uri, position)
        if symbol is None:
            return []
        identity = self._symbol_key(symbol)
        locations, seen = [], set()

        def add(path, line, column, name, after=False, fallback=False):
            location = self._location_at(path, line, column, name, after=after)
            if location is None and fallback:
                location = self._location_at(path, line, 0, name, after=True)
            if location is not None:
                start = location['range']['start']
                key = (location['uri'], start['line'], start['character'])
                if key not in seen:
                    seen.add(key)
                    locations.append(location)

        for checked_uri, unit in self.checked.items():
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

    def rename(self, uri, position, new_name):
        if (not isinstance(new_name, str)
                or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', new_name)
                or new_name in KEYWORDS):
            return None
        symbol = self.symbol_at(uri, position)
        if symbol is None or symbol.kind == 'builtin' or not Path(symbol.file).is_absolute():
            return None
        locations = self.references(uri, position)
        if not locations:
            return None
        changes = {}
        for location in locations:
            changes.setdefault(location['uri'], []).append(
                {'range': location['range'], 'newText': new_name})
        return {'changes': changes}

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

    def dispatch(self, message):
        method, params = message.get('method'), message.get('params', {})
        result = None
        if method == 'initialize':
            result = {'capabilities': {'positionEncoding': 'utf-16', 'textDocumentSync': {'openClose': True, 'change': 2},
                                      'definitionProvider': True, 'hoverProvider': True,
                                      'documentFormattingProvider': True,
                                      'referencesProvider': True, 'renameProvider': True,
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
            self.compiler.remove_overlay(source_path(uri))
            for opened in self.documents:
                self.publish(opened)
            self.send({'jsonrpc': '2.0', 'method': 'textDocument/publishDiagnostics', 'params': {'uri': uri, 'diagnostics': []}})
        elif method == 'textDocument/definition':
            result = self.definition(params['textDocument']['uri'], params['position'])
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
        elif method == 'textDocument/formatting':
            result = self.formatting(params['textDocument']['uri'])
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
