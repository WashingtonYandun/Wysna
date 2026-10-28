"""Interactive compilation and execution with a persistent runtime environment."""

from compiler.CompilerService import CompilerService
from error.Error import WysnaError
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
        self.interpreter = Interpreter(stdout=stdout, file="<repl>")

    def execute(self, source):
        self.pending_source = self.history + source + "\n" if source else self.history
        first_line = self.history.count("\n") + 1
        service = CompilerService(project_root=self.project_root)
        checked = service.check(self.pending_source, "<repl>")
        current = {path: snapshot.content_hash for path, snapshot in checked.sources.items()
                   if path != "<repl>"}
        for path, digest in self.modules.items():
            if current.get(path) != digest:
                raise WysnaError(f"Imported module changed during this session: {path}; use :reload",
                                 file=path, code="W1003", stage="module")
        value = service.interpret_incremental(checked, self.interpreter,
                                              first_line=first_line,
                                              loaded_sources=self.modules)
        self.history = self.pending_source
        self.modules = current
        return value

    def reload(self):
        """Recheck accepted history and execute changed imported modules again."""
        if not self.history.strip():
            return False
        modules = self.modules
        self.modules = {}
        try:
            self.execute("")
        except BaseException:
            self.modules = modules
            raise
        return True
