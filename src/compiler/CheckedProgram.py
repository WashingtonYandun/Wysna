"""Session-bound semantic results and isolated syntax views for Seed consumers."""
from dataclasses import dataclass, field
import hashlib
from types import MappingProxyType

from checker.SymbolTable import Symbol
from checker.WysnaType import WysnaType
from error.Error import WysnaError
from node.NodeType import Node, Program


@dataclass(frozen=True)
class SourceSnapshot:
    """Immutable source content, independent of subsequent file or overlay changes."""
    file: str
    text: str
    revision: str | int | None = None
    content_hash: str = field(init=False)

    def __post_init__(self):
        object.__setattr__(self, 'content_hash', hashlib.sha256(self.text.encode('utf-8')).hexdigest())

    @property
    def path(self):
        return self.file


class ParsedProgram:
    """Service-owned raw syntax with immutable source and phase metadata."""
    __slots__ = ('_owner', '_source', '_program', '_timing_ms')

    def __init__(self, owner, source, program, timing_ms):
        self._owner = owner
        self._source = source
        self._program = program
        self._timing_ms = MappingProxyType(dict(timing_ms))

    @property
    def source(self):
        return self._source

    @property
    def timing_ms(self):
        return self._timing_ms

    @property
    def program(self):
        return clone_syntax(self._program, detached=True)


def clone_syntax(value, memo=None, *, detached=False, inspection=False):
    """Copy syntax containers while retaining the canonical semantic arena and symbols.

    Generic deepcopy would recursively duplicate sessions, checkers and type arenas.
    Only syntax nodes and their containers belong to a consumer's mutable tree.
    Public inspection replaces semantic descriptors with immutable display metadata.
    """
    if memo is None:
        memo = {}
    if id(value) in memo:
        return memo[id(value)]
    if inspection and isinstance(value, Symbol):
        result = SymbolInfo.from_symbol(value)
        memo[id(value)] = result
        return result
    if inspection and isinstance(value, WysnaType):
        result = TypeInfo(str(value), type(value).__name__)
        memo[id(value)] = result
        return result
    if isinstance(value, Node):
        result = type(value).__new__(type(value))
        memo[id(value)] = result
        for name, child in vars(value).items():
            if detached and name in ('session', 'checked_module', 'linked_program'):
                continue
            if inspection and name in ('interner', 'checker'):
                continue
            setattr(result, name, clone_syntax(child, memo, detached=detached, inspection=inspection))
        if inspection and isinstance(value, Program):
            result.semantic_view = True
        return result
    if isinstance(value, list):
        result = []
        memo[id(value)] = result
        result.extend(clone_syntax(child, memo, detached=detached, inspection=inspection) for child in value)
        return result
    if isinstance(value, dict):
        result = {}
        memo[id(value)] = result
        result.update((key, clone_syntax(child, memo, detached=detached, inspection=inspection))
                      for key, child in value.items())
        return result
    if isinstance(value, tuple):
        result = tuple(clone_syntax(child, memo, detached=detached, inspection=inspection) for child in value)
        memo[id(value)] = result
        return result
    return value


@dataclass(frozen=True)
class SymbolInfo:
    """Read-only symbol information for tooling without exposing checker state."""
    name: str
    type: str
    is_mut: bool
    kind: str
    line: int
    col: int
    file: str

    @classmethod
    def from_symbol(cls, symbol):
        return cls(symbol.name, str(symbol.type), symbol.is_mut, symbol.kind,
                   symbol.line, symbol.col, getattr(symbol, 'file', ''))


@dataclass(frozen=True)
class TypeInfo:
    """A semantic type's display metadata without its mutable arena definitions."""
    display: str
    kind: str

    def __str__(self):
        return self.display


@dataclass(frozen=True)
class ModuleInfo:
    module_id: str
    path: str
    generation: int
    source_hash: str
    dependencies: tuple
    is_entry: bool
    exports: object
    types: object


@dataclass(frozen=True)
class DeclarationInfo:
    decl_id: tuple
    name: str
    kind: str
    file: str
    line: int
    col: int


class CheckedProgram:
    """A checked generation with immutable metadata and fresh syntax per consumer.

    Tooling metadata remains a usable snapshot after invalidation. Executable consumers
    must pass through CompilerService, which rejects stale or foreign generations.
    The existing checker and type representation remain private implementation details.
    """
    __slots__ = ('_session', '_unit', '_interner', '_generation', '_options_key',
                 '_source', '_sources', '_modules', '_declarations', '_telemetry',
                 '_symbols', '_scope_symbols', '_root_symbols', '_program')

    def __init__(self, session, unit, *, revision=None):
        self._session = session
        self._unit = unit
        self._interner = session.interner
        self._generation = session.generation
        self._options_key = session.options_key()
        units, visited = [], set()

        def collect(current):
            if current.path in visited:
                return
            visited.add(current.path)
            units.append(current)
            for dependency in current.dependencies:
                collect(dependency)

        collect(unit)
        sources = {current.path: SourceSnapshot(current.path, current.program.source_text,
                                                revision if current is unit else None)
                   for current in units}
        self._source = sources[unit.path]
        self._sources = MappingProxyType(sources)
        symbol_cache = {}

        def symbol_info(symbol):
            if id(symbol) not in symbol_cache:
                symbol_cache[id(symbol)] = SymbolInfo.from_symbol(symbol)
            return symbol_cache[id(symbol)]

        self._modules = MappingProxyType({current.path: ModuleInfo(
            current.module_id, current.path, current.generation, current.source_hash,
            tuple(dependency.module_id for dependency in current.dependencies), current.is_entry,
            MappingProxyType({name: symbol_info(symbol)
                              for name, symbol in current.exports.symbols.items()}),
            MappingProxyType({name: str(type_) for name, type_ in current.exports.types.items()}))
            for current in units})
        module_ids = {current.module_id for current in units}
        self._declarations = MappingProxyType({identity: DeclarationInfo(
            identity, declaration.name, type(declaration).__name__,
            getattr(declaration, 'source_file', unit.path), declaration.line, declaration.col)
            for identity, declaration in session.declarations.items() if identity[0] in module_ids})
        self._telemetry = MappingProxyType(dict(unit.checker.telemetry.to_dict()))
        symbols = {}
        for current in units:
            for name, symbol in current.checker.global_scope.symbols.items():
                if name not in symbols:
                    symbols[name] = symbol_info(symbol)
        self._symbols = MappingProxyType(symbols)
        scope_cache = {}
        self._root_symbols = self._capture_scope(unit.checker.global_scope, symbol_info, scope_cache)
        self._scope_symbols = tuple((start, end, self._capture_scope(scope, symbol_info, scope_cache))
                                    for start, end, scope in unit.checker.scope_spans)
        self._program = clone_syntax(unit.program, detached=True, inspection=True)

    @staticmethod
    def _capture_scope(scope, symbol_info, cache):
        layers = []
        while scope is not None:
            if id(scope) not in cache:
                cache[id(scope)] = MappingProxyType({name: symbol_info(symbol)
                                                     for name, symbol in scope.symbols.items()})
            layers.append(cache[id(scope)])
            scope = scope.parent
        return tuple(layers)

    @property
    def source(self):
        return self._source

    @property
    def sources(self):
        return self._sources

    @property
    def modules(self):
        return self._modules

    @property
    def declarations(self):
        return self._declarations

    @property
    def telemetry(self):
        return self._telemetry

    @property
    def generation(self):
        return self._generation

    @property
    def program(self):
        return clone_syntax(self._program, detached=True, inspection=True)

    @property
    def ast(self):
        return self.program

    @property
    def is_current(self):
        return (self._generation == self._session.generation
                and self._interner is self._session.interner
                and self._options_key == self._session.options_key()
                and self._session.modules.get(self._unit.path) is self._unit)

    def require_current(self, session):
        if session is not self._session:
            raise WysnaError('Checked program belongs to a different compilation session',
                             file=self.source.file, code='W9999', stage='semantic-boundary')
        if not self.is_current:
            raise WysnaError('Checked program belongs to an invalidated compilation generation',
                             file=self.source.file, code='W9999', stage='semantic-boundary')

    def lookup_symbol(self, name):
        return self._symbols.get(name)

    def visible_symbols(self, line, col):
        location = (line, col)
        candidates = [(start, symbols) for start, end, symbols in self._scope_symbols
                      if start <= location <= end]
        layers = max(candidates, key=lambda item: item[0])[1] if candidates else self._root_symbols
        symbols = {}
        for scope in layers:
            for name, symbol in scope.items():
                if (name not in symbols
                        and not name.startswith(('_WYSNA_', '__wysna_internal_', '__wysna_module_'))
                        and (symbol.kind != 'var' or (symbol.line, symbol.col) <= location)):
                    symbols[name] = symbol
        return MappingProxyType(symbols)

    def source_text(self, path, default=''):
        snapshot = self._sources.get(self._session.path(path))
        return snapshot.text if snapshot is not None else default
