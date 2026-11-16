"""Whole-program Wysna Seed source ownership, checked-module cache and identity arena."""
import hashlib
import json
import os
from pathlib import Path
from typing import Optional
from common.Paths import get_repo_root
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
    def __init__(self, module_id, path, program, checker, exports, generation, dependencies, is_entry=False, *, interface_digest=None, nominal_signature=None):
        self.module_id = module_id
        self.path = path
        self.program = program
        self.checker = checker
        self.exports = exports
        self.generation = generation
        self.is_entry = is_entry
        self.dependencies = dependencies
        self.source_hash = hashlib.sha256(program.source_text.encode('utf-8')).hexdigest()
        self.interface_digest = interface_digest
        self.nominal_signature = nominal_signature if nominal_signature is not None else {}
        self.cache_key = (self.source_hash, checker.session.options_key(), is_entry, tuple(dependency.cache_key for dependency in dependencies))


def _syntax_digest_payload(node):
    if isinstance(node, A.Node):
        payload = {'$node': type(node).__name__}
        for key, value in sorted(vars(node).items()):
            if key in ('line', 'col', 'end_line', 'end_col', 'source_text', 'session',
                       'checked_module', 'linked_program', 'decl_id', 'source_file', 'module_id'):
                continue
            payload[key] = _syntax_digest_payload(value)
        return payload
    if isinstance(node, list):
        return [_syntax_digest_payload(item) for item in node]
    if isinstance(node, dict):
        return {str(k): _syntax_digest_payload(v) for k, v in sorted(node.items())}
    if isinstance(node, tuple):
        return [_syntax_digest_payload(item) for item in node]
    return node


def _compute_interface_and_nominals(program, is_entry=False):
    """Compute a deterministic digest of exported API signatures and nominal layout map."""
    exported_items = []
    nominals = {}
    for stmt in getattr(program, 'body', ()):
        if isinstance(stmt, (A.ImportDecl, A.UseDecl)):
            if is_entry or getattr(stmt, 'is_exported', False) or isinstance(stmt, A.UseDecl):
                exported_items.append(_syntax_digest_payload(stmt))
            continue
        if isinstance(stmt, (A.StructDecl, A.EnumDecl, A.ClassDecl, A.InterfaceDecl)):
            nominals[stmt.name] = hashlib.sha256(
                json.dumps(_syntax_digest_payload(stmt), sort_keys=True, separators=(',', ':')).encode('utf-8')
            ).hexdigest()
            if getattr(stmt, 'is_exported', False):
                exported_items.append(_syntax_digest_payload(stmt))
            continue
        if isinstance(stmt, A.FnDecl) and getattr(stmt, 'is_exported', False):
            # Include body when generic (monomorphized in importers); otherwise keep signature + contracts only.
            if getattr(stmt, 'type_params', None):
                exported_items.append(_syntax_digest_payload(stmt))
            else:
                exported_items.append({
                    '$node': 'FnSig',
                    'name': stmt.name,
                    'is_async': bool(getattr(stmt, 'is_async', False)),
                    'params': _syntax_digest_payload(stmt.params),
                    'return_type': _syntax_digest_payload(stmt.return_type),
                    'error_type': _syntax_digest_payload(getattr(stmt, 'error_type', None)),
                    'requires': _syntax_digest_payload(getattr(stmt, 'requires', [])),
                    'ensures': _syntax_digest_payload(getattr(stmt, 'ensures', [])),
                })
            continue
        if isinstance(stmt, A.ExternFnDecl) and getattr(stmt, 'is_exported', False):
            exported_items.append(_syntax_digest_payload(stmt))
            continue
        if isinstance(stmt, A.LetDecl) and getattr(stmt, 'is_exported', False):
            item = {
                '$node': 'LetSig',
                'name': stmt.name,
                'is_mut': bool(getattr(stmt, 'is_mut', False)),
                'type_ann': _syntax_digest_payload(getattr(stmt, 'type_ann', None)),
            }
            if getattr(stmt, 'type_ann', None) is None:
                item['initializer'] = _syntax_digest_payload(getattr(stmt, 'initializer', None))
            exported_items.append(item)
    digest = hashlib.sha256(
        json.dumps(exported_items, sort_keys=True, separators=(',', ':')).encode('utf-8')
    ).hexdigest()
    return digest, nominals


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
        self.reused_module_counts = {}
        self.interface_cutoff_hits = 0
        self._ast_cache = {}
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

    def _parse_cached(self, path, source):
        from compiler.CheckedProgram import clone_syntax
        from parser.Parser import Parser
        source_hash = hashlib.sha256(source.encode('utf-8')).hexdigest()
        cache_key = (path, source_hash)
        cached_ast = self._ast_cache.get(cache_key)
        if cached_ast is not None:
            program = clone_syntax(cached_ast)
            program.source_text = source
            return program
        program = Parser(file=path).parse(source, recover=True)
        self.parse_counts[path] = self.parse_counts.get(path, 0) + 1
        self._ast_cache[cache_key] = clone_syntax(program)
        return program

    def _evict_module(self, mod_path):
        unit = self.modules.pop(mod_path, None)
        self.sources.snapshots.pop(mod_path, None)
        if unit is not None:
            dead_keys = [decl_id for decl_id in self.declarations if decl_id[0] == unit.module_id]
            for decl_id in dead_keys:
                self.declarations.pop(decl_id, None)
        return unit

    def _refresh_unchanged_unit(self, old_unit):
        from compiler.CheckedProgram import clone_syntax
        new_deps = [self.modules.get(dep.path, dep) for dep in old_unit.dependencies]
        cloned_program = clone_syntax(old_unit.program)
        cloned_program.source_text = old_unit.program.source_text
        for stmt in getattr(cloned_program, 'body', ()):
            if isinstance(stmt, A.ImportDecl):
                dep_unit = getattr(stmt, 'checked_module', None)
                if dep_unit is not None and dep_unit.path in self.modules:
                    stmt.checked_module = self.modules[dep_unit.path]
                if hasattr(stmt, 'checked_modules') and stmt.checked_modules:
                    stmt.checked_modules = [self.modules.get(d.path, d) for d in stmt.checked_modules]
        fresh = CheckedModule(
            old_unit.module_id,
            old_unit.path,
            cloned_program,
            old_unit.checker,
            old_unit.exports,
            self.generation,
            new_deps,
            is_entry=old_unit.is_entry,
            interface_digest=old_unit.interface_digest,
            nominal_signature=old_unit.nominal_signature,
        )
        fresh.names = old_unit.names
        fresh.imported_symbol_names = getattr(old_unit, 'imported_symbol_names', {})
        cloned_program.session = self
        cloned_program.checked_module = fresh
        self.modules[old_unit.path] = fresh
        self.reused_module_counts[old_unit.path] = self.reused_module_counts.get(old_unit.path, 0) + 1
        self.interface_cutoff_hits += 1
        for node in getattr(cloned_program, 'body', ()):
            if isinstance(node, DECLARATIONS) and hasattr(node, 'decl_id'):
                self.declarations[node.decl_id] = node
            self.decorate(node, old_unit.path, old_unit.module_id)
        return fresh

    def prepare(self, path, source):
        self.sources.batch = {path: source}
        options_changed = self.options_key() != self._options_key
        if options_changed:
            self._ast_cache.clear()
            self.modules.clear()
            self._linked_programs.clear()
            self.declarations.clear()
            self.interner = TypeInterner()
            self.generation += 1
            self.sources.snapshots.clear()
            self.root_path = path
            self._options_key = self.options_key()
            self.sources.snapshots[path] = source
            return

        current_texts = {path: source}
        changed_paths = set()
        for previous, content in list(self.sources.snapshots.items()):
            try:
                current = source if previous == path else self.sources.read(previous)
            except OSError:
                current = None
            if current is not None:
                current_texts[previous] = current
            if current != content:
                changed_paths.add(previous)
        if path in self.modules:
            old_unit = self.modules[path]
            old_source = getattr(old_unit.program, 'source_text', None)
            if old_source != source or not old_unit.is_entry:
                changed_paths.add(path)

        # Switching entry module invalidates old is_entry wrapper(s), leaving shared libs cached.
        switched_entries = {m_path for m_path, m_unit in self.modules.items() if m_unit.is_entry and m_path != path}
        if not changed_paths and not switched_entries and path in self.modules:
            self.root_path = path
            self._options_key = self.options_key()
            self.sources.snapshots[path] = source
            return

        self.root_path = path
        self._linked_programs.clear()
        self.generation += 1

        # Build reverse dependency graph over cached modules.
        reverse_deps = {}
        for mod_path, mod_unit in self.modules.items():
            for dep in mod_unit.dependencies:
                reverse_deps.setdefault(dep.path, set()).add(mod_path)

        dirty_paths = set(changed_paths) | switched_entries
        interface_broken = set(switched_entries)
        nominal_collision = False
        recheck_with_cutoff = []

        # Determine whether changed modules altered public interfaces or mutated existing nominals.
        for mod_path in sorted(changed_paths):
            old_unit = self.modules.get(mod_path)
            cur_text = current_texts.get(mod_path)
            if old_unit is None or cur_text is None:
                interface_broken.add(mod_path)
                continue
            try:
                parsed = self._parse_cached(mod_path, cur_text)
                if getattr(parsed, 'diagnostics', None):
                    interface_broken.add(mod_path)
                    continue
                new_iface, new_nominals = _compute_interface_and_nominals(parsed, is_entry=old_unit.is_entry)
            except WysnaError:
                interface_broken.add(mod_path)
                continue
            # Check if any previously declared nominal changed or was removed.
            for nom_name, nom_hash in (old_unit.nominal_signature or {}).items():
                if new_nominals.get(nom_name) != nom_hash:
                    nominal_collision = True
                    break
            if old_unit.interface_digest != new_iface:
                interface_broken.add(mod_path)
            else:
                recheck_with_cutoff.append((mod_path, cur_text))

        if nominal_collision:
            self.modules.clear()
            self.declarations.clear()
            self.interner = TypeInterner()
            self.sources.snapshots.clear()
            self._options_key = self.options_key()
            self.sources.snapshots[path] = source
            return

        # Expand dirty set ONLY across dependents whose upstream interface actually changed.
        pending = list(interface_broken)
        while pending:
            curr = pending.pop()
            for importer in reverse_deps.get(curr, ()):
                if importer not in dirty_paths:
                    dirty_paths.add(importer)
                    pending.append(importer)

        # Also collect unaffected importers of interface-preserving changed modules for shallow relinking.
        cutoff_dependents = set()
        if recheck_with_cutoff:
            cutoff_queue = [p for p, _ in recheck_with_cutoff]
            while cutoff_queue:
                curr = cutoff_queue.pop()
                for importer in reverse_deps.get(curr, ()):
                    if importer not in dirty_paths and importer not in cutoff_dependents:
                        cutoff_dependents.add(importer)
                        cutoff_queue.append(importer)

        # Check if any newly deleted file wasn't in self.modules (just snapshots): purge snapshot.
        for mod_path in list(dirty_paths):
            self._evict_module(mod_path)

        # If a module edited only non-interface implementation details, re-check it first so
        # clean downstream dependents can safely cut off without re-parsing/re-typechecking.
        cutoff_rechecked = False
        try:
            for mod_path, cur_text in recheck_with_cutoff:
                if mod_path in self.modules or cur_text is None:
                    continue
                if mod_path != path:
                    self._check_unit(mod_path, cur_text, imported=True)
                    self.sources.snapshots[mod_path] = cur_text
                    cutoff_rechecked = True
        except Exception:
            for dep_path in cutoff_dependents:
                self._evict_module(dep_path)
            raise

        if cutoff_rechecked and cutoff_dependents:
            # Refresh clean importers in topological order (dependencies before importers).
            ordered_refresh = []
            visited_refresh = set()
            def visit_dep(m_path):
                if m_path in visited_refresh or m_path not in cutoff_dependents or m_path not in self.modules:
                    return
                visited_refresh.add(m_path)
                for d in self.modules[m_path].dependencies:
                    visit_dep(d.path)
                ordered_refresh.append(m_path)
            for dep_path in list(cutoff_dependents):
                visit_dep(dep_path)
            for dep_path in ordered_refresh:
                old_dep_unit = self.modules.get(dep_path)
                if old_dep_unit is not None:
                    self._refresh_unchanged_unit(old_dep_unit)

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
            program = self._parse_cached(path, source)
        self.validate_names(program, path)
        interface_digest, nominal_signature = _compute_interface_and_nominals(program, is_entry=not imported)
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
                                        line=sym.line, col=sym.col, code='W1002')
                    names[bound] = bound
            elif isinstance(node, A.ImportDecl):
                if node.namespace in names:
                    raise TypeError('Import namespace conflicts with a declaration', file=path,
                                    line=node.line, col=node.col, code='W1002')
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
                        for item in getattr(error, 'diagnostics', (error,)):
                            item.message = item.message.replace(emitted, str(origin) + '::' + local)
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
        unit = CheckedModule(
            module_id, path, program, checker, exports, self.generation, dependencies,
            is_entry=not imported,
            interface_digest=interface_digest,
            nominal_signature=nominal_signature,
        )
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
            base = Path(get_repo_root()) / 'stdlib' / subpath
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
        emitted_decls = set()
        def collect(current):
            if current.module_id in visited:
                return
            visited.add(current.module_id)
            for dependency in current.dependencies:
                collect(dependency)
            mappings = getattr(current, 'imported_symbol_names', {})
            for node in current.program.body:
                if isinstance(node, A.ImportDecl):
                    continue
                rewritten = rewrite(clone_syntax(node, copies), mappings)
                if isinstance(rewritten, DECLARATIONS):
                    decl_key = (type(rewritten), getattr(rewritten, 'name', None))
                    if decl_key[1] is not None and decl_key in emitted_decls:
                        continue
                    emitted_decls.add(decl_key)
                body.append(rewritten)
        collect(unit)
        linked = A.Program(body)
        linked.interner = self.interner
        linked.call_entrypoint = not any(not isinstance(node, (A.FnDecl, A.StructDecl, A.EnumDecl, A.InterfaceDecl, A.ClassDecl, A.ImportDecl)) for node in program.body)
        linked.session_linked = True
        self._linked_programs[unit.path] = linked
        return linked
