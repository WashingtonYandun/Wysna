"""Whole-program Wysna Seed source ownership, checked-module cache and identity arena."""
import hashlib
import json
import os
from pathlib import Path
from typing import Optional
from checker.TypeInterner import TypeInterner
from checker.SymbolTable import Scope
from checker.WysnaType import ModuleType
from error.Error import TypeError, WysnaError
from node import NodeType as A
from compiler.ModuleNames import DECLARATIONS, ModuleNames, PREFIX


class ModuleId(str):
    """Project-relative path, or an absolute file URI across Windows volumes."""


class DeclId(tuple):
    def __new__(cls, module_id, kind=None, name=None):
        values = module_id if kind is None and name is None else (module_id, kind, name)
        return tuple.__new__(cls, values)


class SourceManager:
    def __init__(self):
        self.snapshots = {}
        self.overlays = {}
        self.batch = {}
        self.read_counts = {}

    def read(self, path):
        if path not in self.batch:
            self.batch[path] = (self.overlays[path] if path in self.overlays
                                else Path(path).read_bytes().decode('utf-8'))
            self.read_counts[path] = self.read_counts.get(path, 0) + 1
        self.snapshots[path] = self.batch[path]
        return self.batch[path]


class CheckedModule:
    def __init__(self, module_id, path, program, checker, exports, generation, dependencies, is_entry=False):
        self.module_id = module_id
        self.path = path
        self.program = program
        self.checker = checker
        self.exports = exports
        self.generation = generation
        self.is_entry = is_entry
        self.dependencies = dependencies
        self.source_hash = hashlib.sha256(program.source_text.encode('utf-8')).hexdigest()
        self.cache_key = (self.source_hash, checker.session.options_key(), is_entry, tuple(dependency.cache_key for dependency in dependencies))


class CompilationSession:
    def __init__(self, project_root=None, options=None, project_resolver=None):
        self.project_root = os.path.realpath(project_root or os.getcwd())
        self.project_resolver = project_resolver
        self.options = dict(options or {})
        self.sources = SourceManager()
        self.interner = TypeInterner()
        self.modules = {}
        self._linked_programs = {}
        self.declarations = {}
        self.active = []
        self.generation = 0
        self.parse_counts = {}
        self.check_counts = {}
        # One closure-name counter per compilation: a per-module counter restarts at 1
        # in every module, so two modules each holding a lambda produced the same
        # `_WYSNA_Closure_N_Env` and `_WYSNA_Closure_N`, and the linked program broke.
        self.closure_counter = 0
        self.root_path = None
        self._options_key = self.options_key()

    def options_key(self):
        return json.dumps(self.options, sort_keys=True, separators=(',', ':'))

    def path(self, path):
        return path if path.startswith('<') else os.path.realpath(path)

    def module_id(self, path):
        if path.startswith('<'):
            return ModuleId(path)
        try:
            identity = Path(os.path.relpath(path, self.project_root)).as_posix()
        except ValueError:
            # Windows has no relative path between different drives or UNC shares.
            identity = Path(path).as_uri()
        return ModuleId(identity)

    def prepare(self, path, source):
        self.sources.batch = {path: source}
        changed = self.options_key() != self._options_key or (self.root_path is not None and self.root_path != path)
        self.root_path = path
        for previous, content in list(self.sources.snapshots.items()):
            try:
                current = source if previous == path else self.sources.read(previous)
            except OSError:
                current = None
            changed = changed or current != content
        if path in self.modules:
            old_source = getattr(self.modules[path].program, 'source_text', None)
            changed = changed or old_source != source
        if changed:
            self.modules.clear()
            self._linked_programs.clear()
            self.declarations.clear()
            self.interner = TypeInterner()
            self.generation += 1
            self.sources.snapshots.clear()
        self._options_key = self.options_key()
        self.sources.snapshots[path] = source

    def check_source(self, source, file='<stdin>'):
        path = self.path(file)
        self.prepare(path, source)
        return self._check_unit(path, source, imported=False)

    def check_program(self, program, checker):
        path = self.path(checker.file)
        source = program.source_text if hasattr(program, 'source_text') else json.dumps(program.to_dict(), sort_keys=True)
        self.prepare(path, source)
        if hasattr(checker, '_checked_once') and path not in self.modules:
            from checker.TypeChecker import TypeChecker
            fresh = TypeChecker(file=checker.file, session=self)
            vars(checker).update(vars(fresh))
        original_program = program
        if hasattr(program, 'checked_module') and path not in self.modules:
            from parser.Parser import Parser
            program = Parser(file=path).parse(source)
        unit = self._check_unit(path, source, program, checker, imported=False)
        if original_program is not unit.program:
            vars(original_program).clear()
            vars(original_program).update(vars(unit.program))
        vars(checker).update(vars(unit.checker))
        return unit

    def validate_names(self, node, path):
        if isinstance(node, list):
            for child in node:
                self.validate_names(child, path)
        elif isinstance(node, A.Node):
            for field in ('name', 'namespace', 'err_name'):
                name = getattr(node, field, None)
                if isinstance(name, str) and name.startswith((PREFIX, '_WYSNA_', '__wysna_internal_')):
                    raise TypeError('The Wysna compiler namespace is reserved for internal identities',
                                    file=path, line=node.line, col=node.col, code='W1002')
            for child in vars(node).values():
                if isinstance(child, (A.Node, list)):
                    self.validate_names(child, path)

    def _check_unit(self, path, source, program=None, checker=None, imported=True):
        if path in self.modules:
            return self.modules[path]
        from parser.Parser import Parser
        from checker.TypeChecker import TypeChecker
        module_id = self.module_id(path)
        if program is None:
            program = Parser(file=path).parse(source, recover=True)
            self.parse_counts[path] = self.parse_counts.get(path, 0) + 1
        self.validate_names(program, path)
        for node in program.body:
            if isinstance(node, (A.StructDecl, A.EnumDecl, A.ClassDecl, A.InterfaceDecl)) and (
                    node.name in ('Option', 'Result', 'Weak') or node.name.startswith(('Option__', 'Result__'))):
                raise TypeError('Builtin Option/Result/Weak identities are reserved',
                                file=path, line=node.line, col=node.col, code='W1002')
            if isinstance(node, DECLARATIONS) and node.name.startswith((PREFIX, '_WYSNA_', '__wysna_internal_')):
                raise TypeError('The Wysna compiler namespace is reserved for internal identities',
                                file=path, line=node.line, col=node.col, code='W1002')
        names = ModuleNames(program, module_id).qualify(program) if imported else {
            node.name: node.name for node in program.body if isinstance(node, DECLARATIONS)}
        for node in program.body:
            if isinstance(node, A.UseDecl):
                for sym in node.symbols:
                    bound = sym.bound_name
                    if bound in names:
                        raise TypeError('Imported symbol conflicts with a declaration', file=path,
                                        line=sym.line, col=sym.col, code='T008')
                    names[bound] = bound
            elif isinstance(node, A.ImportDecl):
                if node.namespace in names:
                    raise TypeError('Import namespace conflicts with a declaration', file=path,
                                    line=node.line, col=node.col, code='T008')
                names[node.namespace] = node.namespace
        original_declarations = list(program.body)
        checker = checker or TypeChecker(file=path, session=self)
        checker.file = path
        checker.interner = self.interner
        checker.session = self
        program.source_text = source
        self.active.append(path)
        checker._checked_once = True
        try:
            checker._check_program(program)
            checker._checked_once = True
        except WysnaError as error:
            modules = [(module.module_id, module.names) for module in self.modules.values()]
            modules.append((module_id, names))
            for origin, exports_ in modules:
                for local, emitted in exports_.items():
                    if local != emitted:
                        error.message = error.message.replace(emitted, str(origin) + '::' + local)
            raise
        finally:
            self.active.pop()
        exports = Scope()
        public_names = set()
        for node in original_declarations:
            if getattr(node, "is_exported", False):
                if isinstance(node, A.UseDecl):
                    for sym in node.symbols:
                        public_names.add(sym.bound_name)
                else:
                    name = getattr(node, "source_name", getattr(node, "name", getattr(node, "namespace", None)))
                    if name:
                        public_names.add(name)
        for name, emitted in names.items():
            if imported and name not in public_names:
                continue
            if emitted in checker.global_scope.symbols:
                exports.symbols[name] = checker.global_scope.symbols[emitted]
            if emitted in checker.global_scope.types:
                exports.types[name] = checker.global_scope.types[emitted]
        dependencies = []
        for node in program.body:
            if isinstance(node, A.ImportDecl):
                if hasattr(node, 'checked_modules') and node.checked_modules:
                    dependencies.extend(node.checked_modules)
                elif getattr(node, 'checked_module', None):
                    dependencies.append(node.checked_module)
        unit = CheckedModule(module_id, path, program, checker, exports, self.generation, dependencies, is_entry=not imported)
        unit.names = names
        unit.imported_symbol_names = getattr(checker, 'imported_symbol_names', {})
        program.session = self
        program.checked_module = unit
        self.modules[path] = unit
        self.check_counts[path] = self.check_counts.get(path, 0) + 1
        for node in original_declarations + program.body:
            if isinstance(node, DECLARATIONS):
                source_name = getattr(node, 'source_name', node.name)
                logical_name = source_name if names.get(source_name) == node.name else node.name
                node.decl_id = DeclId(module_id, type(node).__name__, logical_name)
                self.declarations[node.decl_id] = node
            self.decorate(node, path, module_id)
        return unit

    def decorate(self, node, path, module_id):
        if isinstance(node, list):
            for child in node:
                self.decorate(child, path, module_id)
        elif isinstance(node, A.Node):
            node.source_file = path
            node.module_id = module_id
            for child in list(vars(node).values()):
                if isinstance(child, (A.Node, list)):
                    self.decorate(child, path, module_id)

    def find_module_file(self, raw_path: str, importer: str) -> Optional[str]:
        if self.project_resolver is not None:
            cand = self.project_resolver.resolve_import(raw_path, importer)
            return self.path(str(cand)) if cand is not None else None

        directory = os.path.dirname(importer) if not importer.startswith('<') else os.getcwd()
        if raw_path.startswith('std/') or raw_path.startswith('std.'):
            subpath = raw_path[4:].replace('.', '/')
            base = Path(__file__).resolve().parent.parent / 'stdlib' / subpath
            cand = self.path(str(base.with_suffix('.wysna')))
            if os.path.isfile(cand) or cand in self.sources.overlays:
                return cand
            if os.path.isfile(str(base)) or str(base) in self.sources.overlays:
                return self.path(str(base))
            return None

        if raw_path.startswith('.'):
            dot_count = 0
            while dot_count < len(raw_path) and raw_path[dot_count] == '.':
                dot_count += 1
            remainder = raw_path[dot_count:].replace('.', '/')
            target_dir = directory
            for _ in range(dot_count - 1):
                target_dir = os.path.dirname(target_dir)
            cand = self.path(os.path.join(target_dir, remainder))
            if os.path.isfile(cand) or cand in self.sources.overlays:
                return cand
            if os.path.isfile(cand + '.wysna') or (cand + '.wysna') in self.sources.overlays:
                return self.path(cand + '.wysna')
            return None

        # Relative to importer directory
        cand = self.path(os.path.join(directory, raw_path))
        if os.path.isfile(cand) or cand in self.sources.overlays:
            return cand
        if os.path.isfile(cand + '.wysna') or (cand + '.wysna') in self.sources.overlays:
            return self.path(cand + '.wysna')

        if '.' in raw_path and not raw_path.endswith('.wysna'):
            slash_path = raw_path.replace('.', '/')
            cand = self.path(os.path.join(directory, slash_path))
            if os.path.isfile(cand) or cand in self.sources.overlays:
                return cand
            if os.path.isfile(cand + '.wysna') or (cand + '.wysna') in self.sources.overlays:
                return self.path(cand + '.wysna')

        # Raw path directly (e.g. from cwd or absolute)
        cand = self.path(raw_path)
        if os.path.isfile(cand) or cand in self.sources.overlays:
            return cand
        if os.path.isfile(cand + '.wysna') or (cand + '.wysna') in self.sources.overlays:
            return self.path(cand + '.wysna')

        if '.' in raw_path and not raw_path.endswith('.wysna'):
            slash_path = raw_path.replace('.', '/')
            cand = self.path(slash_path)
            if os.path.isfile(cand) or cand in self.sources.overlays:
                return cand
            if os.path.isfile(cand + '.wysna') or (cand + '.wysna') in self.sources.overlays:
                return self.path(cand + '.wysna')
            cand_cwd = self.path(os.path.join(os.getcwd(), slash_path))
            if os.path.isfile(cand_cwd) or cand_cwd in self.sources.overlays:
                return cand_cwd
            if os.path.isfile(cand_cwd + '.wysna') or (cand_cwd + '.wysna') in self.sources.overlays:
                return self.path(cand_cwd + '.wysna')

        return None

    def load_module_by_path(self, candidate: str, importer: str, line: int = 1, col: int = 1):
        if candidate in self.active:
            chain = ' -> '.join(str(self.module_id(path)) for path in self.active + [candidate])
            raise TypeError('Circular import detected: ' + chain, file=importer,
                            line=line, col=col, code='W1003')
        if candidate in self.modules:
            return self.modules[candidate]
        try:
            source = self.sources.read(candidate)
        except OSError as error:
            raise TypeError(str(error), file=importer, line=line, col=col, code='W1003')
        return self._check_unit(candidate, source)

    def import_module(self, statement, importer):
        candidate = self.find_module_file(statement.path, importer)
        if candidate is None:
            msg = f"Module not found: '{statement.path}'"
            if self.project_resolver is not None:
                msg = f"Module not found in declared project roots: '{statement.path}'"
            raise TypeError(msg, file=importer, line=statement.line, col=statement.col, code='W1003')
        unit = self.load_module_by_path(candidate, importer, statement.line, statement.col)
        statement.checked_module = unit
        module_type = ModuleType(statement.namespace, unit.exports)
        module_type.checked_module = unit
        return module_type

    def refresh_specializations(self, unit):
        checker = unit.checker
        existing = {id(node) for node in unit.program.body}
        generated = checker.monomorphized_oop_decls + checker.monomorphized_struct_decls + checker.monomorphized_enum_decls + checker.monomorphized_fn_decls
        for node in generated:
            if id(node) not in existing:
                node.decl_id = DeclId(unit.module_id, type(node).__name__, node.name)
                self.declarations[node.decl_id] = node
                self.decorate(node, unit.path, unit.module_id)
                unit.program.body.append(node)
                existing.add(id(node))
                self._linked_programs.clear()

    def link(self, program):
        from compiler.CheckedProgram import clone_syntax
        unit = program.checked_module
        if (unit.generation != self.generation or program.interner is not self.interner
                or self.modules.get(unit.path) is not unit):
            raise WysnaError('Checked module belongs to an invalidated compilation generation',
                         file=unit.path, code='W9999', stage='module-linking')
        if unit.path in self._linked_programs:
            return self._linked_programs[unit.path]
        body, visited, copies = [], set(), {}
        def rewrite(node, symbol_mappings):
            if isinstance(node, list):
                return [rewrite(child, symbol_mappings) for child in node]
            if not isinstance(node, A.Node):
                return node
            if isinstance(node, A.MemberExpr) and hasattr(node, 'resolved_module_member'):
                replacement = A.IdentExpr(node.resolved_module_member, node.line, node.col)
                for field, value in vars(node).items():
                    if field not in ('object', 'property'):
                        setattr(replacement, field, value)
                return replacement
            if isinstance(node, A.IdentExpr):
                if hasattr(node, 'resolved_symbol') and hasattr(node.resolved_symbol, 'name') and node.resolved_symbol.name.startswith(PREFIX):
                    node.name = node.resolved_symbol.name
                elif node.name in symbol_mappings:
                    node.name = symbol_mappings[node.name]
                if hasattr(node, 'function_reference') and node.function_reference in symbol_mappings:
                    node.function_reference = symbol_mappings[node.function_reference]
            elif isinstance(node, (A.NamedType, A.GenericType)):
                if node.name in symbol_mappings:
                    node.name = symbol_mappings[node.name]
            elif isinstance(node, A.StructInitExpr):
                if node.struct_name in symbol_mappings:
                    node.struct_name = symbol_mappings[node.struct_name]
            elif isinstance(node, A.NewExpr):
                if node.class_name in symbol_mappings:
                    node.class_name = symbol_mappings[node.class_name]
            elif isinstance(node, A.EnumPattern):
                if node.enum_name and node.enum_name in symbol_mappings:
                    node.enum_name = symbol_mappings[node.enum_name]
            elif isinstance(node, A.ClassDecl):
                if isinstance(node.superclass, str) and node.superclass in symbol_mappings:
                    node.superclass = symbol_mappings[node.superclass]
                node.interfaces = [symbol_mappings.get(intf, intf) for intf in node.interfaces]
            for field, child in list(vars(node).items()):
                if isinstance(child, (A.Node, list)):
                    setattr(node, field, rewrite(child, symbol_mappings))
            return node
        def collect(current):
            if current.module_id in visited:
                return
            visited.add(current.module_id)
            for dependency in current.dependencies:
                collect(dependency)
            mappings = getattr(current, 'imported_symbol_names', {})
            body.extend(rewrite(clone_syntax(node, copies), mappings) for node in current.program.body
                        if not isinstance(node, A.ImportDecl))
        collect(unit)
        linked = A.Program(body)
        linked.interner = self.interner
        linked.call_entrypoint = not any(not isinstance(node, (A.FnDecl, A.StructDecl, A.EnumDecl, A.InterfaceDecl, A.ClassDecl, A.ImportDecl)) for node in program.body)
        linked.session_linked = True
        self._linked_programs[unit.path] = linked
        return linked
