"""Interactive compilation and execution with a persistent runtime environment."""

import copy

from compiler.CompilerService import CompilerService
from error.Error import WysnaError
from formatter.Formatter import Formatter
from node.NodeType import ClassDecl, EnumDecl, FnDecl, ImportDecl, InterfaceDecl, LetDecl, StructDecl
from parser.Parser import Parser
from runtime.Interpreter import Interpreter


def needs_continuation(source):
    """Recognize open delimiters outside single-line strings and comments."""
    pairs = {')': '(', ']': '[', '}': '{'}
    stack = []
    in_string = False
    escaped = False
    in_comment = False
    for char in source:
        if char == '\n':
            if in_string:
                return False
            in_comment = False
            continue
        if in_comment:
            continue
        if in_string:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                in_string = False
        elif char == '#':
            in_comment = True
        elif char == '"':
            in_string = True
        elif char in '([{':
            stack.append(char)
        elif char in pairs:
            if not stack or stack[-1] != pairs[char]:
                return False
            stack.pop()
    return bool(stack) and not in_string


class ReplSession:
    def __init__(self, *, stdout=None, project_root=None):
        self.history = ""
        self.pending_source = ""
        self.project_root = project_root
        self.modules = {}
        self.service = CompilerService(project_root=self.project_root)
        self.interpreter = Interpreter(stdout=stdout, file="<repl>")

    def snapshot(self):
        """Capture a restorable checkpoint of session source history and runtime bindings."""
        env = self.interpreter.global_env
        return {
            "history": self.history,
            "pending_source": self.pending_source,
            "modules": dict(self.modules),
            "bindings": copy.deepcopy(env.bindings),
            "mutables": set(env.mutables),
        }

    def rollback(self, checkpoint):
        """Restore session history and runtime environment to a prior ``snapshot()``."""
        self.history = checkpoint["history"]
        self.pending_source = checkpoint["pending_source"]
        self.modules = dict(checkpoint["modules"])
        env = self.interpreter.global_env
        env.bindings.clear()
        env.bindings.update(copy.deepcopy(checkpoint["bindings"]))
        env.mutables.clear()
        env.mutables.update(checkpoint["mutables"])

    def execute(self, source, *, transactional=False):
        checkpoint = self.snapshot() if transactional else None
        self.pending_source = self.history + source + "\n" if source else self.history
        first_line = self.history.count("\n") + 1
        checked = self.service.check(self.pending_source, "<repl>")
        current = {path: snapshot.content_hash for path, snapshot in checked.sources.items()
                   if path != "<repl>"}
        for path, digest in self.modules.items():
            if current.get(path) != digest:
                raise WysnaError(f"Imported module changed during this session: {path}; use :reload",
                                 file=path, code="W1003", stage="module")
        try:
            value = self.service.interpret_incremental(checked, self.interpreter,
                                                       first_line=first_line,
                                                       loaded_sources=self.modules)
        except WysnaError:
            if checkpoint is not None:
                self.rollback(checkpoint)
            raise
        self.history = self.pending_source
        self.modules = current
        return value

    def reload(self):
        """Reload imports in a fresh environment without replaying prior input."""
        if not self.modules:
            return False
        program = Parser(file='<repl>').parse(self.history)
        declarations = (ClassDecl, EnumDecl, FnDecl, InterfaceDecl, LetDecl, StructDecl)
        if any(isinstance(statement, declarations) for statement in program.body):
            raise WysnaError(
                'Cannot reload imports while session declarations are present; use :reset and re-import',
                file='<repl>', code='W5002', stage='interactive',
            )
        formatter = Formatter()
        imports = [formatter.format_stmt(statement, 0) for statement in program.body
                   if isinstance(statement, ImportDecl)]
        candidate = ReplSession(stdout=self.interpreter.stdout, project_root=self.project_root)
        candidate.interpreter = Interpreter(stdout=self.interpreter.stdout, file='<repl>',
                                            execution_context=self.interpreter.execution_context)
        candidate.execute('\n'.join(imports))
        self.history = candidate.history
        self.pending_source = candidate.pending_source
        self.modules = candidate.modules
        self.service = candidate.service
        self.interpreter = candidate.interpreter
        return True
