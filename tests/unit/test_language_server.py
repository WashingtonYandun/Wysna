"""Language-server edits, references and rename follow checked provenance."""

from pathlib import Path
import tempfile
import unittest

from tooling.LanguageServer import LanguageServer, offset_at, position_at


class LanguageServerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='wysna-lsp-unit-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.messages = []
        self.server = LanguageServer(self.messages.append)
        self.library = self.root / 'library.wysna'
        self.library.write_text('pub fn plus(value: int) -> int { return value + 1 }\n',
                                encoding='utf-8')
        self.main = self.root / 'main.wysna'
        self.source = ('import "library.wysna" as library\n'
                       'fn use(value: int) -> int { let result = library.plus(value); '
                       'return result }\n'
                       'print(use(2))\n')

    @property
    def library_uri(self):
        return self.library.as_uri()

    @property
    def main_uri(self):
        return self.main.as_uri()

    def request(self, method, params, request_id=None):
        message = {'jsonrpc': '2.0', 'method': method, 'params': params}
        if request_id is not None:
            message['id'] = request_id
        self.server.dispatch(message)

    def open(self, uri, text, version=1):
        self.request('textDocument/didOpen', {'textDocument': {
            'uri': uri, 'languageId': 'wysna', 'version': version, 'text': text}})

    def diagnostics(self, uri):
        published = [message for message in self.messages
                     if message.get('method') == 'textDocument/publishDiagnostics'
                     and message['params']['uri'] == uri]
        return published[-1]['params']['diagnostics']

    def slices(self, source, locations):
        return [source[offset_at(source, location['range']['start']):
                       offset_at(source, location['range']['end'])]
                for location in locations]

    def test_initialize_advertises_incremental_sync_and_navigation(self):
        self.request('initialize', {}, 1)
        capabilities = self.messages[-1]['result']['capabilities']
        self.assertEqual(capabilities['textDocumentSync'], {'openClose': True, 'change': 2})
        self.assertTrue(capabilities['definitionProvider'])
        self.assertTrue(capabilities['referencesProvider'])
        self.assertTrue(capabilities['renameProvider'])
        self.assertTrue(capabilities['documentFormattingProvider'])
        self.assertEqual(capabilities['positionEncoding'], 'utf-16')

    def test_did_change_applies_ordered_ranges_and_ignores_stale_versions(self):
        self.open(self.library_uri, self.library.read_text(encoding='utf-8'))
        self.open(self.main_uri, self.source)
        self.assertEqual(self.diagnostics(self.main_uri), [])

        start = position_at(self.source, self.source.index('use(2)') + len('use('))
        after_first = {'line': start['line'], 'character': start['character'] + 1}
        self.request('textDocument/didChange', {
            'textDocument': {'uri': self.main_uri, 'version': 2},
            'contentChanges': [
                {'range': {'start': start, 'end': after_first}, 'text': '4'},
                {'range': {'start': start, 'end': after_first}, 'text': '41'},
            ],
        })
        updated = self.server.documents[self.main_uri]['text']
        self.assertTrue(updated.endswith('print(use(41))\n'), updated)
        self.assertEqual(self.diagnostics(self.main_uri), [])
        definition = self.server.definition(
            self.main_uri, position_at(updated, updated.index('plus')))
        self.assertEqual(definition['uri'], self.library_uri)

        self.request('textDocument/didChange', {
            'textDocument': {'uri': self.main_uri, 'version': 2},
            'contentChanges': [{'text': 'broken\n'}],
        })
        self.assertEqual(self.server.documents[self.main_uri]['text'], updated)

        unicode_source = 'let greeting = "😀"\nprint(greeting)\n'
        unicode_path = self.root / 'unicode.wysna'
        unicode_uri = unicode_path.as_uri()
        self.open(unicode_uri, unicode_source)
        emoji = unicode_source.index('😀')
        self.assertEqual(position_at(unicode_source, emoji), {'line': 0, 'character': 16})
        self.assertEqual(offset_at(unicode_source, {'line': 0, 'character': 16}), emoji)
        self.request('textDocument/didChange', {
            'textDocument': {'uri': unicode_uri, 'version': 2},
            'contentChanges': [{
                'range': {'start': position_at(unicode_source, emoji),
                          'end': position_at(unicode_source, emoji + 1)},
                'text': 'ok',
            }],
        })
        self.assertEqual(self.server.documents[unicode_uri]['text'],
                         'let greeting = "ok"\nprint(greeting)\n')

    def test_references_and_rename_follow_local_and_imported_symbols(self):
        self.open(self.library_uri, self.library.read_text(encoding='utf-8'))
        self.open(self.main_uri, self.source)

        value_position = position_at(self.source, self.source.index('value'))
        value_references = self.server.references(self.main_uri, value_position)
        self.assertEqual(self.slices(self.source, value_references), ['value', 'value'])
        without_declaration = self.server.references(
            self.main_uri, value_position, include_declaration=False)
        self.assertEqual(self.slices(self.source, without_declaration), ['value'])
        rename = self.server.rename(self.main_uri, value_position, 'amount')
        edits = rename['changes'][self.main_uri]
        self.assertEqual(len(edits), 2)
        self.assertTrue(all(edit['newText'] == 'amount' for edit in edits))

        plus_position = position_at(self.source, self.source.index('plus'))
        self.assertNotIn('__wysna_module_', self.server.hover(
            self.main_uri, plus_position)['contents']['value'])
        member_references = self.server.references(self.main_uri, plus_position)
        by_uri = {uri: self.slices(self.library.read_text(encoding='utf-8'), locations)
                  if uri == self.library_uri else self.slices(self.source, locations)
                  for uri, locations in self._by_uri(member_references).items()}
        self.assertEqual(by_uri, {self.library_uri: ['plus'], self.main_uri: ['plus']})
        member_rename = self.server.rename(self.main_uri, plus_position, 'add')
        self.assertIsNone(member_rename)

        declaration_position = position_at(self.source, self.source.index('use('))
        declaration_references = self.server.references(self.main_uri, declaration_position)
        self.assertEqual(self.slices(self.source, declaration_references), ['use', 'use'])

        unused_source = 'fn unused(value: int) -> int { return 1 }\nprint(2)\n'
        unused_uri = (self.root / 'unused.wysna').as_uri()
        self.open(unused_uri, unused_source)
        unused_position = position_at(unused_source, unused_source.index('unused'))
        self.assertIn('fn unused: fn(int) -> int', self.server.hover(
            unused_uri, unused_position)['contents']['value'])
        self.assertEqual(self.slices(unused_source, self.server.references(
            unused_uri, unused_position)), ['unused'])
        unused_rename = self.server.rename(unused_uri, unused_position, 'never')
        self.assertEqual(self.slices(unused_source, unused_rename['changes'][unused_uri]),
                         ['unused'])

        builtin_position = position_at(self.source, self.source.index('print'))
        self.assertIsNone(self.server.rename(self.main_uri, builtin_position, 'echo'))
        self.assertIsNone(self.server.rename(self.main_uri, value_position, 'fn'))
        self.assertIsNone(self.server.rename(self.main_uri, value_position, '1value'))
        self.assertIsNone(self.server.rename(self.main_uri, value_position, ''))

    def test_rename_preserves_assignment_targets_and_refuses_capture(self):
        source = ('let mut value = 1\n'
                  'value = value + 1\n'
                  'fn show() { let other = 2; print(value) }\nshow()\n')
        uri = (self.root / 'rename.wysna').as_uri()
        self.open(uri, source)
        position = position_at(source, source.index('value'))
        self.assertIsNone(self.server.rename(uri, position, 'other'))
        self.assertIsNone(self.server.rename(uri, position, '__wysna_internal'))
        self.assertIsNone(self.server.rename(uri, position, 'print'))
        result = self.server.rename(uri, position, 'amount')
        self.assertEqual(len(result['changes'][uri]), 4)
        self.assertEqual(self.slices(source, result['changes'][uri]),
                         ['value', 'value', 'value', 'value'])

    def test_rename_refuses_public_declarations_without_workspace_index(self):
        source = 'pub fn value() -> int { return 1 }\n'
        uri = (self.root / 'public.wysna').as_uri()
        self.open(uri, source)
        self.assertIsNone(self.server.rename(uri, position_at(source, source.index('value')), 'other'))

    def test_definition_resolves_same_name_parameter_on_declaration_line(self):
        source = 'fn item(item: int) -> int { return item }\nprint(item(1))\n'
        uri = (self.root / 'same-line.wysna').as_uri()
        self.open(uri, source)
        use = source.index('return item') + len('return ')
        expected = source.index('item: int')
        location = self.server.definition(uri, position_at(source, use))
        self.assertEqual(offset_at(source, location['range']['start']), expected)
        self.assertEqual(self.slices(source, [location]), ['item'])

    def test_imported_names_with_double_underscores_keep_their_source_name(self):
        library_source = 'pub fn long__name() -> int { return 2 }\n'
        self.library.write_text(library_source, encoding='utf-8')
        source = 'import "library.wysna" as library\nprint(library.long__name())\n'
        self.open(self.library_uri, library_source)
        self.open(self.main_uri, source)
        position = position_at(source, source.index('long__name'))
        self.assertIn('long__name', self.server.hover(self.main_uri, position)['contents']['value'])
        location = self.server.definition(self.main_uri, position)
        self.assertEqual(location['uri'], self.library_uri)
        self.assertEqual(self.slices(library_source, [location]), ['long__name'])

    def test_dependency_edits_and_close_publish_diagnostics_for_open_dependents(self):
        library_source = self.library.read_text(encoding='utf-8')
        self.open(self.library_uri, library_source)
        self.open(self.main_uri, self.source)
        self.assertEqual(self.diagnostics(self.main_uri), [])

        self.request('textDocument/didChange', {
            'textDocument': {'uri': self.library_uri, 'version': 2},
            'contentChanges': [{'text': 'pub fn plus(value: str) -> str { return value }\n'}],
        })
        diagnostics = self.diagnostics(self.main_uri)
        self.assertTrue(diagnostics, self.messages)
        self.assertEqual(diagnostics[0]['code'], 'W2001')
        self.assertEqual(diagnostics[0]['source'], 'Wysna')

        self.request('textDocument/didClose', {'textDocument': {'uri': self.library_uri}})
        self.assertEqual(self.diagnostics(self.main_uri), [])

        self.request('textDocument/didChange', {
            'textDocument': {'uri': self.main_uri, 'version': 2},
            'contentChanges': [{'text': 'import "library.wysna" as library\n'}],
        })
        self.assertEqual(self.diagnostics(self.main_uri), [])

    def test_code_action_and_signature_help_and_warnings(self):
        typo_source = 'struct U { name: str }\nfn f(u: U) -> str { return u.nme }\n'
        typo_uri = (self.root / 'typo.wysna').as_uri()
        self.open(typo_uri, typo_source)
        actions = self.server.code_action(typo_uri)
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]['kind'], 'quickfix')
        self.assertTrue(actions[0]['isPreferred'])
        edit = actions[0]['edit']['changes'][typo_uri][0]
        self.assertEqual(edit['newText'], 'name')

        sig_source = 'fn add(a: int, b: str) -> int { return a }\nfn main() { print(add(1, "x")) }\n'
        sig_uri = (self.root / 'sig.wysna').as_uri()
        self.open(sig_uri, sig_source)
        pos = position_at(sig_source, sig_source.index('add(1, ') + len('add(1, '))
        help_res = self.server.signature_help(sig_uri, pos)
        self.assertIsNotNone(help_res)
        self.assertEqual(help_res['activeParameter'], 1)
        self.assertIn('fn add(a: int, b: str) -> int', help_res['signatures'][0]['label'])

        warn_source = 'fn f(a: int, b: int) -> int {\n    let x = a\n    -b\n    return x\n}\n'
        warn_uri = (self.root / 'warn.wysna').as_uri()
        self.open(warn_uri, warn_source)
        diags = self.diagnostics(warn_uri)
        self.assertEqual(len(diags), 1)
        self.assertEqual(diags[0]['code'], 'W6001')
        self.assertEqual(diags[0]['severity'], 2)
        self.assertIn(warn_uri, self.server.checked)

    @staticmethod
    def _by_uri(locations):
        grouped = {}
        for location in locations:
            grouped.setdefault(location['uri'], []).append(location)
        return grouped


if __name__ == '__main__':
    unittest.main()
