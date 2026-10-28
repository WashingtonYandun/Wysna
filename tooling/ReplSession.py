"""Interactive compilation and execution with a persistent runtime environment."""

from compiler.CompilerService import CompilerService
from error.Error import WysnaError
from runtime.Interpreter import Interpreter


class ReplSession:
    def __init__(self, *, stdout=None, project_root=None):
        self.history = ""
        self.pending_source = ""
        self.project_root = project_root
        self.modules = {}
        self.interpreter = Interpreter(stdout=stdout, file="<repl>")

    def execute(self, source):
        self.pending_source = self.history + source + "\n"
        first_line = self.history.count("\n") + 1
        service = CompilerService(project_root=self.project_root)
        checked = service.check(self.pending_source, "<repl>")
        current = {path: snapshot.content_hash for path, snapshot in checked.sources.items()
                   if path != "<repl>"}
        for path, digest in self.modules.items():
            if current.get(path) != digest:
                raise WysnaError(f"Imported module changed during this session: {path}; restart the REPL",
                                 file=path, code="W1003", stage="module")
        value = service.interpret_incremental(checked, self.interpreter,
                                              first_line=first_line,
                                              loaded_sources=self.modules)
        self.history = self.pending_source
        self.modules = current
        return value
