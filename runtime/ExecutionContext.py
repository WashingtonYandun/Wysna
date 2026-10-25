"""Host state supplied to one Wysna program execution."""
from dataclasses import dataclass
import os
from types import MappingProxyType
import sys
from typing import Callable, Mapping, NoReturn, Optional, TextIO


def _exit_process(code: int) -> NoReturn:
    sys.exit(code)


@dataclass(frozen=True)
class ExecutionContext:
    """A stable snapshot of arguments, environment, working directory and streams."""
    argv: tuple[str, ...]
    cwd: str
    env: Mapping[str, str]
    stdin: TextIO
    stdout: TextIO
    stderr: TextIO
    terminate: Callable[[int], NoReturn] = _exit_process

    def __post_init__(self):
        object.__setattr__(self, 'argv', tuple(os.fspath(value) for value in self.argv))
        object.__setattr__(self, 'cwd', os.path.abspath(os.fspath(self.cwd)))
        object.__setattr__(self, 'env', MappingProxyType(dict(self.env)))

    @classmethod
    def from_process(cls, *, argv=None, cwd=None, env=None, stdin=None, stdout=None,
                     stderr=None, terminate=_exit_process):
        """Capture the current host state unless the caller supplies an override."""
        return cls(
            argv=tuple(sys.argv if argv is None else argv),
            cwd=os.getcwd() if cwd is None else cwd,
            env=os.environ.copy() if env is None else env,
            stdin=sys.stdin if stdin is None else stdin,
            stdout=sys.stdout if stdout is None else stdout,
            stderr=sys.stderr if stderr is None else stderr,
            terminate=terminate,
        )

    @classmethod
    def for_program(cls, program_name, arguments=(), **host):
        """Create runner argv without exposing the compiler's own process arguments."""
        return cls.from_process(argv=(program_name, *arguments), **host)

    def resolve_path(self, path):
        path = os.fspath(path)
        return path if os.path.isabs(path) else os.path.join(self.cwd, path)
