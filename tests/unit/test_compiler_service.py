"""Compiler consumers share checked identities without sharing mutable syntax trees."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import FrozenInstanceError
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

from compiler.CompilerService import CompilerService
from compiler.CompilationSession import CompilationSession
from compiler.NativeToolchain import NativeToolchain
from error.Error import WysnaError
from node.NodeType import MemberExpr, Node


ROOT = Path(__file__).resolve().parents[2]


def syntax_nodes(node):
    if isinstance(node, Node):
        yield node
        for child in vars(node).values():
            if isinstance(child, (Node, list)):
                yield from syntax_nodes(child)
    elif isinstance(node, list):
        for child in node:
            yield from syntax_nodes(child)


def expect_invariant(action):
    try:
        action()
    except WysnaError as error:
        assert error.code == 'W9999', str(error)
    else:
        raise AssertionError('Invalid checked result was accepted')


def test_check_file_keeps_original_crlf_source_snapshot(tmp_path):
    source_bytes = b'import "library.wysna"\r\nfn main() -> unit { print(library.answer()) }\r\n'
    library_bytes = b'pub fn answer() -> int {\r\n    return 42\r\n}\r\n'
    source_path = tmp_path / 'crlf.wysna'
    library_path = tmp_path / 'library.wysna'
    source_path.write_bytes(source_bytes)
    library_path.write_bytes(library_bytes)

    checked = CompilerService(project_root=str(tmp_path)).check_file(source_path)

    assert checked.source.text.encode('utf-8') == source_bytes
    assert checked.source.content_hash == hashlib.sha256(source_bytes).hexdigest()
    assert checked._session.sources.snapshots[str(library_path)] == library_bytes.decode('utf-8')


def test_checked_snapshot_and_consumer_isolation():
    with tempfile.TemporaryDirectory(prefix='wysna-service-') as temporary:
        root = Path(temporary).resolve()
        entry = root / 'main.wysna'
        library = root / 'library.wysna'
        library.write_text('pub fn plus(value: int) -> int { return value + 1 }\n')
        source = 'import "library.wysna"\nfn main() { print(library.plus(41)) }\n'
        session = CompilationSession(str(root))
        service = CompilerService(session)
        checked = service.check(source, str(entry))
        canonical = session.modules[str(entry)].program
        original_syntax = canonical.to_dict()
        assert not hasattr(checked, 'checker')
        assert checked.source.text == source
        assert checked.source.content_hash == hashlib.sha256(source.encode()).hexdigest()
        try:
            checked.source.text = 'print(0)'
        except FrozenInstanceError:
            pass
        else:
            raise AssertionError('Source snapshot is mutable')
        try:
            checked.telemetry['requests'] = 0
        except TypeError:
            pass
        else:
            raise AssertionError('Checked telemetry is mutable')
        original = checked.program.to_dict()
        assert any(isinstance(node, MemberExpr) for node in syntax_nodes(checked.program))
        # Neither lowering nor interpretation may read or check dependencies again.
        with patch.object(session.sources, 'read', side_effect=AssertionError('Source reread')):
            with patch('checker.TypeChecker.TypeChecker._check_program', side_effect=AssertionError('Rechecked')):
                first = service.emit_c(checked).source
                linked = service.link(checked)
                linked.body.clear()
                service.lower_hir(checked).functions.clear()
                service.lower_mir(checked).functions.clear()
                for backend in ('hir', 'ast'):
                    emission = service.emit_c(checked, backend=backend)
                    built = NativeToolchain(str(ROOT)).build(emission.source, str(root / backend))
                    executed = subprocess.run([built.output_path], capture_output=True, text=True, timeout=30)
                    assert (executed.returncode, executed.stdout) == (0, '42\n'), executed.stderr
                output = io.StringIO()
                service.interpret(checked, stdout=output)
                assert output.getvalue() == '42\n'
                assert service.emit_c(checked).source == first
        assert not hasattr(canonical, 'linked_program')
        assert canonical.to_dict() == original_syntax
        detached = checked.program
        detached.body.clear()
        assert checked.program.to_dict() == original
        assert any(isinstance(node, MemberExpr) for node in syntax_nodes(checked.program))


def test_link_cache_refreshes_generated_specializations():
    from checker.WysnaType import IntType

    session = CompilationSession()
    unit = session.check_source('fn identity[T](value: T) -> T { return value }\nprint(42)', '<cache>')
    first = session.link(unit.program)
    assert session.link(unit.program) is first
    template = unit.checker.global_scope.lookup_symbol('identity').type
    specialized = unit.checker._monomorphize_fn(template, [IntType], 1, 1)
    session.refresh_specializations(unit)
    refreshed = session.link(unit.program)
    assert refreshed is not first
    assert sum(getattr(node, 'name', None) == specialized.name for node in refreshed.body) == 1
    session.refresh_specializations(unit)
    assert session.link(unit.program) is refreshed
    assert not hasattr(unit.program, 'linked_program')


def test_stale_and_foreign_results_rejected_before_lowering():
    service = CompilerService()
    checked = service.check('print(1)', '<service>')
    other = CompilerService()
    for action in (other.link, other.lower_hir, other.lower_mir, other.emit_c):
        expect_invariant(lambda action=action: action(checked))
    service.emit_c(checked)
    service.check('print(2)', '<service>')
    for action in (service.link, service.lower_hir, service.lower_mir, service.emit_c):
        expect_invariant(lambda action=action: action(checked))
    assert checked.source.text == 'print(1)'


def test_dependency_and_options_invalidation():
    with tempfile.TemporaryDirectory(prefix='wysna-service-overlay-') as temporary:
        root = Path(temporary).resolve()
        library = root / 'library.wysna'
        library.write_text('pub fn value() -> int { return 1 }\n')
        source = 'import "library.wysna"\nprint(library.value())\n'
        session = CompilationSession(str(root))
        service = CompilerService(session)
        entry = str(root / 'main.wysna')
        old = service.check(source, entry)
        service.set_overlay(str(library), 'pub fn value() -> int { return 7 }\n')
        changed = service.check(source, entry)
        expect_invariant(lambda: service.link(old))
        output = io.StringIO()
        service.interpret(changed, stdout=output)
        assert output.getvalue() == '7\n'
        session.options['strict'] = True
        refreshed = service.check(source, entry)
        expect_invariant(lambda: service.lower_mir(changed))
        assert refreshed.source.text == source


def test_cli_uses_service_and_preserves_outputs():
    import main
    output, errors = io.StringIO(), io.StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        assert main.run_pipeline('let value: int = "wrong"', file_name='<service>',
                                 check_only=True, json_diagnostics=True) == 1
    diagnostic = json.loads(output.getvalue())
    assert diagnostic['code'] == 'W2001' and not errors.getvalue()
    output, errors = io.StringIO(), io.StringIO()
    with redirect_stdout(output), redirect_stderr(errors):
        assert main.run_pipeline('print(42)', show_stats=True) == 0
    assert output.getvalue() == '42\n'
    assert json.loads(errors.getvalue())['telemetry']['monomorphization'] is not None


def test_direct_selfhost_does_not_use_seed_checker():
    import main
    from compiler.NativeToolchain import SelfhostEmissionResult
    with tempfile.TemporaryDirectory(prefix='wysna-service-selfhost-') as temporary:
        root = Path(temporary)
        entry, emitted = root / 'input.wysna', root / 'output.c'
        entry.write_text('selfhost owns this source')
        with patch.object(CompilerService, 'check', side_effect=AssertionError('Seed pre-check')):
            with patch('tooling.SourceCommand.ensure_selfhost_compiler_binary', return_value='compiler'):
                with patch.object(NativeToolchain, 'emit_selfhost', return_value=SelfhostEmissionResult('/* direct */\n', 1.0)) as emit:
                    assert main.run_pipeline(entry.read_text(), str(entry), backend='selfhost',
                                             emit_c_path=str(emitted)) == 0
        assert emitted.read_text() == '/* direct */\n'
        emit.assert_called_once_with('compiler', str(entry))


def test_lsp_metadata_survives_lowering_and_multiple_roots():
    from tooling.LanguageServer import LanguageServer, position_at

    with tempfile.TemporaryDirectory(prefix='wysna-service-lsp-') as temporary:
        root = Path(temporary).resolve()
        library = root / 'library.wysna'
        library.write_text('pub fn plus(value: int) -> int { return value + 1 }\n', encoding='utf-8')
        uri = (root / 'main.wysna').as_uri()
        source = ('import "library.wysna" as library\n'
                  'fn use(value: int) -> int { return library.plus(value) }\n'
                  'print(use(41))\n')
        messages = []
        server = LanguageServer(messages.append)
        server.update(uri, source, 1)
        assert not messages[-1]['params']['diagnostics']
        checked = server.checked[uri]
        position = position_at(source, source.index('plus'))
        definition = server.definition(uri, position)
        hover = server.hover(uri, position)
        assert definition and definition['uri'] == library.as_uri(), definition
        assert hover and 'int' in hover['contents']['value'], hover

        server.compiler.lower_hir(checked)
        server.compiler.link(checked).body.clear()
        assert server.definition(uri, position) == definition
        assert server.hover(uri, position) == hover
        assert any(isinstance(node, MemberExpr) for node in syntax_nodes(checked.program))

        other_uri = (root / 'other.wysna').as_uri()
        server.update(other_uri, 'let other = 7\nprint(other)\n', 1)
        cached = server.checked[uri]
        assert not cached.is_current
        expect_invariant(lambda: server.compiler.lower_hir(cached))
        assert cached.source.text == source
        assert server.definition(uri, position) == definition
        assert server.hover(uri, position) == hover


def test_completion_before_later_shadow_keeps_outer_binding():
    from tooling.LanguageServer import LanguageServer, position_at

    with tempfile.TemporaryDirectory(prefix='wysna-service-scope-') as temporary:
        uri = (Path(temporary).resolve() / 'main.wysna').as_uri()
        source = ('let value = 7\n'
                  'fn use() {\n'
                  '    print(value)\n'
                  '    let value = "inner"\n'
                  '    print(value)\n'
                  '}\n'
                  'use()\n')
        messages = []
        server = LanguageServer(messages.append)
        server.update(uri, source, 1)
        assert not messages[-1]['params']['diagnostics']
        checked = server.checked[uri]
        before = position_at(source, source.index('value', source.index('print(')))
        after = position_at(source, source.rindex('value'))
        for position, expected_type in ((before, 'int'), (after, 'str')):
            symbols = checked.visible_symbols(position['line'] + 1, position['character'] + 1)
            assert symbols['value'].type == expected_type, symbols['value']
            items = {item['label']: item['detail'] for item in server.completion(uri, position)['items']}
            assert items['value'] == expected_type, items
            hover = server.hover(uri, position)
            assert hover and 'value: ' + expected_type in hover['contents']['value'], hover


def test_provided_ast_requires_matching_unchecked_source():
    from node.NodeType import Program
    from parser.Parser import Parser

    source = 'let value: int = "wrong"'
    service = CompilerService()
    expect_invariant(lambda: service.check(source, '<provided>', program=Program([])))
    expect_invariant(lambda: service.check(source, '<provided>', program=object()))

    parsed = Parser(file='<provided>').parse('print(42)')
    expect_invariant(lambda: service.check('print(7)', '<provided>', program=parsed))
    parsed.body.clear()
    expect_invariant(lambda: service.check('print(42)', '<provided>', program=parsed))

    parsed = Parser(file='<provided>').parse('print(42)')
    parsed.source_text = source
    expect_invariant(lambda: service.check(source, '<provided>', program=parsed))

    fresh = Parser(file='<provided>').parse('print(42)')
    checked = service.check('print(42)', '<provided>', program=fresh)
    output = io.StringIO()
    service.interpret(checked, stdout=output)
    assert output.getvalue() == '42\n'
    assert not hasattr(fresh, 'interner') and not hasattr(fresh, 'checked_module')


def test_provided_checked_ast_cannot_cross_semantic_generations():
    source = 'fn identity[T](value: T) -> T { return value }\nprint(identity(42))\n'
    service = CompilerService()
    checked = service.check(source, '<provided>')
    inspected = checked.program
    expect_invariant(lambda: CompilerService().check(source, '<provided>', program=inspected))
    expect_invariant(lambda: service.check(source, '<provided>', program=inspected))
    service.check('print(7)', '<provided>')
    expect_invariant(lambda: service.check(source, '<provided>', program=inspected))


def test_public_inspection_contains_only_snapshot_semantics():
    from collections.abc import Mapping
    from dataclasses import fields, is_dataclass
    from checker.SymbolTable import Scope, Symbol
    from checker.TypeInterner import TypeInterner
    from checker.WysnaType import StrType, WysnaType
    from node.NodeType import IdentExpr
    from tooling.LanguageServer import LanguageServer, position_at

    session = CompilationSession()
    service = CompilerService(session)
    source = 'let value = 42\nprint(value)\n'
    checked = service.check(source, '<inspection>')
    inspected = checked.program
    assert not hasattr(inspected, 'interner')
    assert not hasattr(inspected, 'session')
    assert not hasattr(inspected, 'checked_module')
    seen = set()

    def inspect(value):
        if id(value) in seen:
            return
        seen.add(id(value))
        assert not isinstance(value, (Scope, Symbol, TypeInterner, WysnaType, CompilationSession)), type(value)
        if isinstance(value, Node):
            for child in vars(value).values():
                inspect(child)
        elif isinstance(value, Mapping):
            for child in value.values():
                inspect(child)
        elif isinstance(value, (tuple, list, set, frozenset)):
            for child in value:
                inspect(child)
        elif is_dataclass(value):
            for field in fields(value):
                inspect(getattr(value, field.name))

    for view in (inspected, checked.ast, service.link(checked), checked.modules,
                 checked.declarations, checked.lookup_symbol('value'), checked.visible_symbols(2, 7)):
        inspect(view)
    reference = next(node for node in syntax_nodes(inspected)
                     if isinstance(node, IdentExpr) and node.name == 'value')
    frozen_symbol = reference.resolved_symbol
    try:
        frozen_symbol.name = 'changed'
    except (FrozenInstanceError, AttributeError):
        pass
    else:
        raise AssertionError('Inspected binding exposes mutable semantic state')
    raw_symbol = session.modules['<inspection>'].checker.global_scope.lookup_symbol('value')
    raw_symbol.type = StrType
    assert checked.lookup_symbol('value').type == 'int'
    assert str(reference.resolved_symbol.type) == 'int'

    with tempfile.TemporaryDirectory(prefix='wysna-service-snapshot-') as temporary:
        path = Path(temporary).resolve() / 'main.wysna'
        uri = path.as_uri()
        messages = []
        server = LanguageServer(messages.append)
        server.compiler = service
        server.update(uri, source, 1)
        position = position_at(source, source.rindex('value'))
        hover = server.hover(uri, position)
        raw_symbol = session.modules[str(path)].checker.global_scope.lookup_symbol('value')
        raw_symbol.type = StrType
        assert server.hover(uri, position) == hover
        assert 'value: int' in hover['contents']['value']


def test_owned_parse_checks_once_and_isolates_syntax():
    from lexer.Lexer import Lexer

    service = CompilerService()
    source = 'print(42)\n'
    tokenize = Lexer.tokenize
    calls = []

    def counted_tokenize(lexer):
        calls.append(lexer.file)
        return tokenize(lexer)

    with patch.object(Lexer, 'tokenize', counted_tokenize):
        parsed = service.parse(source, '<parsed>')
        parsed.program.body.clear()
        checked = service.check_parsed(parsed)
    assert len(calls) == 1, calls
    assert parsed.source.text == source
    assert parsed.timing_ms['lex'] >= 0 and parsed.timing_ms['parse'] >= 0
    assert checked.source.text == source
    output = io.StringIO()
    service.interpret(checked, stdout=output)
    assert output.getvalue() == '42\n'
    expect_invariant(lambda: CompilerService().check_parsed(parsed))
    expect_invariant(lambda: service.check_parsed(object()))


def test_owned_parse_refreshes_dependencies_without_reparsing_root():
    from parser.Parser import Parser

    with tempfile.TemporaryDirectory(prefix='wysna-service-parsed-') as temporary:
        root = Path(temporary).resolve()
        entry = str(root / 'main.wysna')
        library = root / 'library.wysna'
        library.write_text('pub fn value() -> int { return 1 }\n', encoding='utf-8')
        source = 'import "library.wysna"\nprint(library.value())\n'
        service = CompilerService()
        parsed = service.parse(source, entry)
        old = service.check_parsed(parsed)
        service.check('print(9)\n', str(root / 'other.wysna'))
        assert not old.is_current
        library.write_text('pub fn value() -> int { return 7 }\n', encoding='utf-8')
        parse = Parser.parse
        parsed_files = []

        def dependency_parse(parser, *args, **kwargs):
            assert parser.file != entry, 'Owned parsed root was parsed again'
            parsed_files.append(parser.file)
            return parse(parser, *args, **kwargs)

        with patch.object(Parser, 'parse', dependency_parse):
            refreshed = service.check_parsed(parsed)
        assert str(library) in parsed_files, parsed_files
        assert refreshed.is_current and refreshed.source.text == source
        output = io.StringIO()
        service.interpret(refreshed, stdout=output)
        assert output.getvalue() == '7\n'


def run_compiler_service_tests():
    test_checked_snapshot_and_consumer_isolation()
    test_link_cache_refreshes_generated_specializations()
    test_stale_and_foreign_results_rejected_before_lowering()
    test_dependency_and_options_invalidation()
    test_cli_uses_service_and_preserves_outputs()
    test_direct_selfhost_does_not_use_seed_checker()
    test_lsp_metadata_survives_lowering_and_multiple_roots()
    test_completion_before_later_shadow_keeps_outer_binding()
    test_provided_ast_requires_matching_unchecked_source()
    test_provided_checked_ast_cannot_cross_semantic_generations()
    test_public_inspection_contains_only_snapshot_semantics()
    test_owned_parse_checks_once_and_isolates_syntax()
    test_owned_parse_refreshes_dependencies_without_reparsing_root()
    print('Compiler service: snapshots, isolation, generation checks, consumers and CLI passed')
    return True


if __name__ == '__main__':
    run_compiler_service_tests()
