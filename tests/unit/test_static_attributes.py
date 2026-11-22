"""Closed static annotations preserve execution and diagnose agent mistakes."""
from pathlib import Path
import json
import subprocess
import sys
import tempfile
import unittest

from compiler.Bootstrap import ensure_selfhost_compiler_binary
from compiler.CompilerService import CompilerService
from tests.engine_harness import ENGINES, run_engine

ROOT = Path(__file__).resolve().parents[2]


class StaticAttributeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = Path(ensure_selfhost_compiler_binary(str(ROOT)))

    def check_engines(self, source, output=None, code=None):
        with tempfile.TemporaryDirectory(prefix='wysna-attributes-') as directory:
            for engine in ENGINES:
                with self.subTest(engine=engine, source=source):
                    result = run_engine(engine, source, 'static-attributes', self.compiler, Path(directory))
                    if code:
                        self.assertFalse(result.compile_ok, vars(result))
                        self.assertEqual(result.diag_code, code, vars(result))
                    else:
                        self.assertTrue(result.compile_ok and result.runtime_ok, vars(result))
                        self.assertEqual(result.stdout, output, vars(result))

    def test_attributes_preserve_generic_async_and_public_execution(self):
        self.check_engines('@deprecated("use current")\n@must_use\npub fn old[T](x: T) -> T { x }\n'
            '@must_use\nasync fn task() -> int { 7 }\n'
            'print(old(42), await task())\n', '42 7\n')

    def test_invalid_annotations_are_rejected_by_every_engine(self):
        cases = [
            ('@unknown\nfn f() {}', 'W0006'),
            ('let value = @', 'W0006'),
            ('let value = @must_use', 'W0006'),
            ('print(@must_use)', 'W0006'),
            ('pub @must_use\nfn f() -> int { 1 }', 'W0006'),
            ('@deprecated(" ")\nfn f() {}', 'W0006'),
            ('@deprecated(print("must not execute"))\nfn f() {}', 'W0006'),
            ('@deprecated("x", "y")\nfn f() {}', 'W0006'),
            ('@must_use()\nfn f() -> int { 1 }', 'W0006'),
            ('@must_use\n@must_use\nfn f() -> int { 1 }', 'W1002'),
            ('@must_use\nfn f() {}', 'W2001'),
            ('@must_use\nlet x = 1', 'W0006'),
            ('@deprecated("x")\nstruct S { x: int }', 'W0006'),
            ('@deprecated("x")\nextern "C" fn external() -> int', 'W0006'),
            ('fn outer() { @must_use\nfn inner() -> int { 1 } }', 'W0006'),
            ('class C { @must_use\nfn method() -> int { 1 } }', 'W0006'),
            ('@must_use\nfn old[T](x:T)->T{x}\nfn nothing() {}\nold(nothing())', 'W2001'),
        ]
        for source, code in cases:
            self.check_engines(source + '\n', code=code)

    def test_lints_track_consumption_and_branch_tails_in_json(self):
        source = '''@deprecated("use current")
@must_use
fn old() -> int { 42 }
old()
let value = old()
print(old())
fn ignored(flag: bool) {
    if flag { old() } elif false { old() } else { old() }
}
fn consumed(flag: bool) -> int { if flag { old() } else { old() } }
fn matched(flag: bool) { match flag { true => old(), false => old() } }
fn returned() -> int { return old() }
class C { fn ignored() { old() } }
let callback: fn() -> unit = || -> unit { old() }
fn current() -> int { 42 }
current()
'''
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'lint.wysna'
            path.write_text(source)
            for command in ([sys.executable, str(ROOT / 'main.py')], [str(self.compiler)]):
                result = subprocess.run([*command, 'check', str(path), '--output-format=json-v1'],
                                        cwd=ROOT, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                payload = json.loads(result.stdout)
                deprecated = [item for item in payload['diagnostics'] if item['code'] == 'W6004']
                required = [item for item in payload['diagnostics'] if item['code'] == 'W6005']
                self.assertEqual(len(deprecated), 13, payload)
                self.assertEqual(len(required), 8, payload)
                self.assertTrue(all(item['severity'] == 'warning' for item in deprecated + required))

    def test_imported_generic_metadata_survives_namespace_rewriting(self):
        from tests.fuzz.multifile_fuzzer import MultiFileCase, run_multifile_case
        case = MultiFileCase(seed=4401, files={
            'lib.wysna': 'fn deprecated() {}\n@deprecated("use current")\n@must_use\n'
                'pub fn old[T](x: T) -> T { x }\n',
            'main.wysna': 'import "lib.wysna" as lib\nprint(lib.old(42))\nlib.old(7)\n'}, entry='main.wysna')
        with tempfile.TemporaryDirectory() as directory:
            for result in run_multifile_case(case, self.compiler, Path(directory)):
                self.assertTrue(result.compile_ok and result.runtime_ok, vars(result))
                self.assertEqual(result.stdout, '42\n', vars(result))
            root = Path(directory)
            for name, source in case.files.items():
                (root / name).write_text(source)
            for command in ([sys.executable, str(ROOT / 'main.py')], [str(self.compiler)]):
                result = subprocess.run([*command, 'check', str(root / 'main.wysna'), '--output-format=json-v1'],
                                        cwd=ROOT, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                codes = [item['code'] for item in json.loads(result.stdout)['diagnostics']]
                self.assertEqual(codes.count('W6004'), 2, result.stdout)
                self.assertEqual(codes.count('W6005'), 1, result.stdout)

    def test_indirect_callbacks_do_not_inherit_declaration_attributes(self):
        source = '@deprecated("use current")\n@must_use\nfn old()->int{42}\nlet callback = old\ncallback()\n'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'callbacks.wysna'
            path.write_text(source)
            for command in ([sys.executable, str(ROOT / 'main.py')], [str(self.compiler)]):
                result = subprocess.run([*command, 'check', str(path), '--output-format=json-v1'],
                    cwd=ROOT, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertFalse({'W6004', 'W6005'} & {d['code'] for d in json.loads(result.stdout)['diagnostics']})

    def test_native_lsp_publishes_attribute_warnings(self):
        from tests.unit.test_native_sandbox_and_lints import _lsp_frame, _lsp_read_messages
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'annotations.wysna'
            source = '@deprecated("use current")\n@must_use\nfn old()->int{42}\nold()\n'
            path.write_text(source)
            stream = b''.join(_lsp_frame(payload) for payload in [
                {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
                {'jsonrpc': '2.0', 'method': 'textDocument/didOpen', 'params': {'textDocument': {
                    'uri': path.resolve().as_uri(), 'languageId': 'wysna', 'version': 1, 'text': source}}},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'shutdown'},
                {'jsonrpc': '2.0', 'method': 'exit'},
            ])
            result = subprocess.run([str(self.compiler), 'lsp'], input=stream, capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            messages = _lsp_read_messages(result.stdout)
            diagnostics = [d for message in messages if message.get('method') == 'textDocument/publishDiagnostics'
                           for d in message['params']['diagnostics']]
            self.assertEqual({d['code'] for d in diagnostics}, {'W6004', 'W6005'}, messages)
            self.assertTrue(all(d['severity'] == 2 for d in diagnostics))

    def test_formatter_roundtrip_and_explain_codes(self):
        from formatter.Formatter import Formatter
        from parser.Parser import Parser
        source = '@deprecated("use \\"new\\" API")\n@must_use\npub fn old() -> int { 42 }\n'
        formatted = Formatter().format(Parser().parse(source))
        self.assertIn('@must_use\npub fn old', formatted)
        checked = CompilerService().check(formatted, '<formatted>')
        self.assertEqual([a.name for a in checked._unit.program.body[0].attributes], ['deprecated', 'must_use'])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'format.wysna'
            path.write_text(source)
            result = subprocess.run([str(self.compiler), 'fmt', str(path)], cwd=ROOT,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn('@must_use', result.stdout)
            CompilerService().check(result.stdout, '<native-formatted>')
        for command in ([sys.executable, str(ROOT / 'main.py')], [str(self.compiler)]):
            for code in ('W6004', 'W6005'):
                result = subprocess.run([*command, 'explain', code, '--output-format=json-v1'],
                    cwd=ROOT, capture_output=True, text=True, timeout=60)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn(code, result.stdout)
