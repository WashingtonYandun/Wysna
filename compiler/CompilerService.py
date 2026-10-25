"""Reusable Seed checking and lowering orchestration for CLI and tooling."""
from dataclasses import dataclass
from pathlib import Path
import time

from compiler.CheckedProgram import CheckedProgram, ParsedProgram, SourceSnapshot, clone_syntax
from compiler.CompilationSession import CompilationSession
from error.Error import WysnaError
from node.NodeType import Node, Program


@dataclass(frozen=True)
class EmissionResult:
    source: str
    telemetry: dict
    timing_ms: dict
    abi_exports: tuple = ()


class CompilerService:
    def __init__(self, session=None, *, project_root=None, options=None, project_resolver=None):
        self._session = session if session is not None else CompilationSession(
            project_root, options, project_resolver=project_resolver)
        self._parse_owner = object()

    def parse(self, source, file='<stdin>', *, recover=False, revision=None):
        from lexer.Lexer import Lexer
        from parser.Parser import Parser
        snapshot = SourceSnapshot(self._session.path(str(file)), source, revision)
        started = time.perf_counter()
        tokens = Lexer(source, file=snapshot.file).tokenize()
        lexed = time.perf_counter()
        program = Parser(file=snapshot.file).parse(source, tokens=tokens, recover=recover)
        parsed = time.perf_counter()
        return ParsedProgram(self._parse_owner, snapshot, program,
                             {'lex': (lexed - started) * 1000,
                              'parse': (parsed - lexed) * 1000})

    def check_parsed(self, parsed):
        if not isinstance(parsed, ParsedProgram) or parsed._owner is not self._parse_owner:
            raise WysnaError('Parsed program belongs to a different compiler service',
                             code='W9999', stage='semantic-boundary')
        from checker.TypeChecker import TypeChecker
        path = parsed.source.file
        checker = TypeChecker(file=path, session=self._session)
        unit = self._session.check_program(clone_syntax(parsed._program), checker)
        return CheckedProgram(self._session, unit, revision=parsed.source.revision)

    def check(self, source, file='<stdin>', *, program=None, revision=None):
        path = self._session.path(str(file))
        if program is None:
            unit = self._session.check_source(source, path)
        else:
            if not isinstance(program, Program) or getattr(program, 'source_text', None) != source:
                raise WysnaError('Parsed program does not match the supplied source snapshot',
                                 file=path, code='W9999', stage='semantic-boundary')
            self._require_raw_program(program, path)
            from parser.Parser import Parser
            candidate = Parser(file=path).parse(source)
            try:
                supplied_syntax = program.to_dict()
            except (AttributeError, TypeError, RecursionError, ValueError) as error:
                raise WysnaError('Parsed program contains invalid source syntax', file=path,
                                 code='W9999', stage='semantic-boundary') from error
            if supplied_syntax != candidate.to_dict():
                raise WysnaError('Parsed program syntax does not match the supplied source snapshot',
                                 file=path, code='W9999', stage='semantic-boundary')
            from checker.TypeChecker import TypeChecker
            checker = TypeChecker(file=path, session=self._session)
            unit = self._session.check_program(candidate, checker)
        return CheckedProgram(self._session, unit, revision=revision)

    @staticmethod
    def _require_raw_program(program, path):
        semantic_fields = {'session', 'checker', 'interner', 'checked_module', 'linked_program',
                           'semantic_view', 'inferred_type', 'inferred_type_id', 'resolved_symbol',
                           'resolved_type_id', 'session_resolved_type', 'session_arena_id'}
        pending, visited = [program], set()
        while pending:
            node = pending.pop()
            if id(node) in visited:
                continue
            visited.add(id(node))
            if isinstance(node, Node):
                if semantic_fields.intersection(vars(node)):
                    raise WysnaError('Compiler service requires raw parsed syntax, not a checked semantic view',
                                     file=path, code='W9999', stage='semantic-boundary')
                pending.extend(child for child in vars(node).values() if isinstance(child, (Node, list)))
            elif isinstance(node, list):
                pending.extend(node)

    def check_source(self, source, file='<stdin>', **kwargs):
        return self.check(source, file, **kwargs)

    def check_file(self, path):
        path = self._session.path(str(path))
        source = self._session.sources.overlays.get(path)
        if source is None:
            source = Path(path).read_bytes().decode('utf-8')
        return self.check(source, path)

    def set_overlay(self, path, source):
        self._session.sources.overlays[self._session.path(path)] = source

    def remove_overlay(self, path):
        self._session.sources.overlays.pop(self._session.path(path), None)

    def source_snapshot(self, path):
        path = self._session.path(path)
        source = self._session.sources.snapshots.get(path)
        return SourceSnapshot(path, source) if source is not None else None

    def source_text(self, path, default=''):
        snapshot = self.source_snapshot(path)
        return snapshot.text if snapshot is not None else default

    def source_texts(self):
        """Return the immutable text snapshots loaded for the current compilation."""
        return dict(self._session.sources.snapshots)

    def _validate(self, checked):
        if not isinstance(checked, CheckedProgram):
            raise WysnaError('Compiler service requires a checked program',
                             code='W9999', stage='semantic-boundary')
        checked.require_current(self._session)

    def link(self, checked):
        self._validate(checked)
        linked = self._session.link(checked._unit.program)
        return clone_syntax(linked, detached=True, inspection=True)

    def _linked_ast(self, checked):
        """Create typed consumer syntax; canonical descriptors never leave the service."""
        self._validate(checked)
        linked = self._session.link(checked._unit.program)
        return clone_syntax(linked, detached=True)

    def interpret(self, checked, stdout=None, foreign_libraries=(), execution_context=None):
        self._validate(checked)
        from runtime.Interpreter import Interpreter
        interpreter = Interpreter(stdout=stdout, file=checked.source.file,
                                  execution_context=execution_context)
        interpreter.foreign_libraries = foreign_libraries
        return interpreter.interpret(self._linked_ast(checked))

    def lower_hir(self, checked):
        self._validate(checked)
        from hir.HirBuilder import HirBuilder
        return HirBuilder(checked._interner, file_path=checked.source.file).lower(self._linked_ast(checked))

    def lower_mir(self, checked):
        self._validate(checked)
        from mir.Lowering import Lowering
        return Lowering(checked._interner, checked.source.file).lower(self.lower_hir(checked))

    def export_mir(self, checked):
        """Export verified interchange without exposing the checked type arena."""
        self._validate(checked)
        from mir.Exchange import export_module
        return export_module(self.lower_mir(checked), checked._interner)

    def emit_c(self, checked, backend='hir', *, target_profile=None):
        self._validate(checked)
        browser_library = target_profile == 'browser-library'
        if backend == 'ast':
            if browser_library:
                raise WysnaError('browser-library requires verified HIR and MIR emission',
                                 file=checked.source.file, code='W2013', stage='target')
            from codegen.CCodeGen import CCodeGen
            started = time.perf_counter()
            generator = CCodeGen(file=checked.source.file)
            source = generator.generate(self._linked_ast(checked))
            source = self._enable_http_runtime(source)
            emitted = (time.perf_counter() - started) * 1000
            return EmissionResult(source, generator.telemetry.to_dict(),
                                  {'hir': 0.0, 'mir': 0.0, 'c_emit': emitted, 'codegen': emitted})
        if backend != 'hir':
            raise ValueError('Unsupported Seed backend: ' + str(backend))
        from hir.HirCCodeGen import HirCCodeGen
        started = time.perf_counter()
        module = self.lower_hir(checked)
        lowered = time.perf_counter()
        generator = HirCCodeGen(checked._interner, file_path=checked.source.file)
        source = generator.generate(module, library_mode=browser_library)
        source = self._enable_http_runtime(source)
        emitted = time.perf_counter()
        return EmissionResult(source, generator.telemetry.to_dict(),
                              {'hir': (lowered - started) * 1000,
                               'mir': generator.phase_ms['mir'],
                               'c_emit': generator.phase_ms['c_emit'],
                               'codegen': (emitted - lowered) * 1000},
                              tuple(getattr(generator, 'abi_exports', ())))

    @staticmethod
    def _enable_http_runtime(source):
        if 'wysna_host_http_request' not in source or 'WYSNA_HAS_HTTP' in source:
            return source
        include = '#include "runtime/wysna_runtime.h"'
        if include in source:
            return source.replace(include, '#define WYSNA_HAS_HTTP 1\n' + include, 1)
        return '#define WYSNA_HAS_HTTP 1\n' + source
