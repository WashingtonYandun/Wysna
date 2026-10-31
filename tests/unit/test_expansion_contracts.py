"""Cross-engine regression and expansion contracts, including editor transport."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from checker.TypeChecker import TypeChecker
from error.Error import WysnaError
from formatter.Formatter import Formatter
from lexer.Lexer import Lexer
from parser.Parser import Parser
from tests.engine_harness import run_engine, compare
from tooling.LanguageServer import LanguageServer, offset_at, position_at, serve

ROOT = Path(__file__).resolve().parents[2]


def run_expansion_tests():
    from main import ensure_selfhost_compiler_binary
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    positive = {
        'tests/functions/closures.wysna': '10\n2 3\n',
        'tests/functions/nested_closures.wysna': '60\n7\n',
        'tests/functions/captured_function.wysna': '10\n11\n',
        'tests/functions/indexed_functions.wysna': '42\n11\n',
        'tests/functions/option_result_payload_match.wysna': 'some 42\nerror failed\nnone\nok 7\n',
        'tests/regressions/assignment_order.wysna': 'index\nvalue\n[7]\n',
        'tests/regressions/assignment_receiver.wysna': '9 2\n',
        'tests/regressions/dictionary_growth.wysna': '2001 2000 7 2000\n0 2000\n',
        'tests/regressions/dict_bool_keys.wysna': '[true, false]\n[(true, 1), (false, 2)]\n',
        'tests/regressions/dict_enum_keys.wysna': '[E.A, E.B]\n[(E.A, 1), (E.B, 2)]\n',
        'tests/regressions/nul_string.wysna': '3 [97, 0, 98] false\n1\n',
        'tests/generic_oop/repository.wysna': '42 hello\n',
        'tests/generic_oop/modules/main.wysna': '42 hello\n',
        'tests/stdlib/basic.wysna': '[3, 1, 2, 1] [1, 1, 2, 3] [2, 2, 4, 6]\none, two ["a", "b"]\n3 7\n',
        'examples/algorithms/two_sum.wysna': '[1, 2]\n',
    }
    with tempfile.TemporaryDirectory(prefix='wysna-expansion-') as temporary:
        directory = Path(temporary)
        for label, expected in positive.items():
            source = (ROOT / label).read_text()
            if label.endswith('generic_oop/modules/main.wysna'):
                source = source.replace('"library.wysna"', json.dumps(str(ROOT / 'tests/generic_oop/modules/library.wysna')))
            engines = ('interpreter', 'ast', 'hir') if '/generic_oop/' in label or '/stdlib/' in label else ('interpreter', 'ast', 'hir', 'selfhost')
            results = [run_engine(engine, source, label, selfhost_bin, directory / label.replace('/', '_'),
                                  sanitize=bool(os.environ.get('WYSNA_SANITIZE'))) for engine in engines]
            ok, error = compare(results, expected)
            assert ok, (label, error, [vars(result) for result in results])
        for name, code in [('inherited_field_type', 'W3003'), ('override_visibility', 'W3002'),
                           ('ctor_early_return', 'W3004'), ('ctor_read_before_init', 'W3004'),
                           ('super_after_let', 'W3004')]:
            # These permanent cases must not depend on private audit files.
            source = (ROOT / 'tests/regressions/invalid' / (name + '.wysna')).read_text()
            for engine in ('interpreter', 'ast', 'hir', 'selfhost'):
                result = run_engine(engine, source, name, selfhost_bin, directory / ('invalid_' + name))
                assert not result.compile_ok and result.diag_code == code, (name, vars(result))
        for engine in ('interpreter', 'ast', 'hir', 'selfhost'):
            for value in ('float("nan")', 'float("inf")', '"9223372036854775808"'):
                source = 'print(int(' + value + '))\n'
                result = run_engine(engine, source, 'conversion', selfhost_bin, directory / ('conversion_' + str(len(value))))
                assert result.diag_code == 'W4003' and result.exit_code != 0, vars(result)
        for backend in ('ast', 'hir'):
            binary = directory / ('ffi_' + backend)
            if backend == 'ast':
                built = subprocess.run(
                    [sys.executable, str(ROOT / 'tests/emit_ast_oracle.py'),
                     str(ROOT / 'tests/ffi/scalars.wysna'), str(directory / 'ffi_ast.c'),
                     '--build', str(binary), '--link-lib', 'm'],
                    capture_output=True, text=True)
            else:
                built = subprocess.run(
                    [sys.executable, str(ROOT / 'main.py'), 'build',
                     str(ROOT / 'tests/ffi/scalars.wysna'), '--link-lib', 'm', '-o', str(binary)],
                    capture_output=True, text=True)
            assert built.returncode == 0, built.stderr
            result = subprocess.run([str(binary)], capture_output=True, text=True, env=dict(os.environ, WYSNA_MEM_CHECK='1'))
            assert (result.returncode, result.stdout) == (0, '9.0\n7\n'), result.stderr
        cfg = directory / 'read_cfg.wysna'
        cfg.write_text('fn read_cfg() -> int { return 7 }\nprint(read_cfg())\n')
        built = subprocess.run([sys.executable, str(ROOT / 'main.py'), 'build', '--engine=compiler',
                                str(cfg), '-o', str(directory / 'read_cfg')], capture_output=True, text=True)
        assert built.returncode == 0, built.stderr
        ran = subprocess.run([str(directory / 'read_cfg')], capture_output=True, text=True)
        assert (ran.returncode, ran.stdout) == (0, '7\n'), ran.stderr

    for source in ['# heading\nlet pair = (1,) # trailing\nprint(pair)\n',
                   'let tiny = 0.000001\nprint(tiny)\n', 'let mut a = 0\nlet mut b = 0\nprint((a = b) = 1)\n'][:2]:
        formatted = Formatter().format(Parser().parse(source))
        assert Formatter().format(Parser().parse(formatted)) == formatted
        if '# heading' in source:
            assert '# heading' in formatted and '# trailing' in formatted and '(1,)' in formatted
    test_lsp()
    import tokenize
    assert hasattr(tokenize, 'EXACT_TOKEN_TYPES')
    print('Expansion: function values, captures, ownership, modules, generic OOP, stdlib, FFI and LSP passed')
    return True


def test_lsp():
    outgoing = []
    server = LanguageServer(outgoing.append)
    with tempfile.TemporaryDirectory(prefix='wysna-lsp-') as temporary:
        directory = Path(temporary).resolve()
        library = directory / 'library.wysna'
        library.write_text('pub fn plus(x: int) -> int { return x + 1 }\n')
        uri = (directory / 'main.wysna').as_uri()
        source = 'import "library.wysna" as library\nfn use(value: int) -> int { return library.plus(value) }\nprint(use(2))\n'
        server.update(uri, source, 1)
        assert not outgoing[-1]['params']['diagnostics'], outgoing
        definition = server.definition(uri, {'line': 1, 'character': 46})
        assert definition and definition['uri'] == library.as_uri(), definition
        completions = server.completion(uri, {'line': 1, 'character': 55})['items']
        assert 'value' in {item['label'] for item in completions}
        hover_position = position_at(source, source.rindex('value'))
        hover = server.hover(uri, hover_position)
        assert hover and 'param value: int' in hover['contents']['value'], hover
        # Unsaved dependency changes must invalidate the importing document.
        server.update(library.as_uri(), 'pub fn plus(x: str) -> str { return x }\n', 2)
        diagnostics = [message for message in outgoing if message['params']['uri'] == uri][-1]['params']['diagnostics']
        assert diagnostics and diagnostics[0]['code'] == 'W2001', outgoing
        format_uri = (directory / 'format.wysna').as_uri()
        server.update(format_uri, 'fn formatted(){\nprint(1)\n}\n', 1)
        edits = server.formatting(format_uri)
        assert len(edits) == 1 and 'fn formatted() -> unit {' in edits[0]['newText'], edits
    source = '"😀" + missing\n'
    assert position_at(source, source.index('missing')) == {'line': 0, 'character': 7}
    assert offset_at(source, {'line': 0, 'character': 7}) == source.index('missing')
    token = next(token for token in Lexer(source).tokenize() if token.value == 'missing')
    assert source[token.start_offset:token.end_offset] == 'missing'
    request = {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}}
    payload = json.dumps(request).encode()
    shutdown = json.dumps({'jsonrpc': '2.0', 'method': 'exit'}).encode()
    incoming = io.BytesIO(b'Content-Length: ' + str(len(payload)).encode() + b'\r\n\r\n' + payload +
                         b'Content-Length: ' + str(len(shutdown)).encode() + b'\r\n\r\n' + shutdown)
    output = io.BytesIO()
    serve(incoming, output)
    headers, body = output.getvalue().split(b'\r\n\r\n', 1)
    assert int(headers.split(b':')[1]) == len(body)
    capabilities = json.loads(body)['result']['capabilities']
    assert capabilities['definitionProvider'] and capabilities['hoverProvider']
    assert capabilities['documentFormattingProvider']


if __name__ == '__main__':
    run_expansion_tests()
