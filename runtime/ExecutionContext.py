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
    max_steps: Optional[int] = None
    max_heap_bytes: Optional[int] = None
    effect_trace: Optional[object] = None

    def __post_init__(self):
        object.__setattr__(self, 'argv', tuple(os.fspath(value) for value in self.argv))
        object.__setattr__(self, 'cwd', os.path.abspath(os.fspath(self.cwd)))
        env_dict = dict(self.env)
        object.__setattr__(self, 'env', MappingProxyType(env_dict))
        if self.max_steps is None and env_dict.get('WYSNA_MAX_STEPS', '').isdigit():
            val = int(env_dict['WYSNA_MAX_STEPS'])
            if val > 0:
                object.__setattr__(self, 'max_steps', val)
        if self.max_heap_bytes is None:
            if env_dict.get('WYSNA_MAX_HEAP_BYTES', '').isdigit():
                val = int(env_dict['WYSNA_MAX_HEAP_BYTES'])
                if val > 0:
                    object.__setattr__(self, 'max_heap_bytes', val)
            elif env_dict.get('WYSNA_MAX_HEAP_MB', '').isdigit():
                val = int(env_dict['WYSNA_MAX_HEAP_MB'])
                if val > 0:
                    object.__setattr__(self, 'max_heap_bytes', val * 1024 * 1024)
        if self.effect_trace is None:
            from runtime.EffectTrace import EffectTrace
            if env_dict.get('WYSNA_REPLAY_TRACE'):
                object.__setattr__(self, 'effect_trace', EffectTrace.replayer(env_dict['WYSNA_REPLAY_TRACE']))
            elif env_dict.get('WYSNA_RECORD_TRACE'):
                object.__setattr__(self, 'effect_trace', EffectTrace.recorder(env_dict['WYSNA_RECORD_TRACE']))

    @classmethod
    def from_process(cls, *, argv=None, cwd=None, env=None, stdin=None, stdout=None,
                     stderr=None, terminate=_exit_process, max_steps=None,
                     max_heap_bytes=None, effect_trace=None):
        """Capture the current host state unless the caller supplies an override."""
        return cls(
            argv=tuple(sys.argv if argv is None else argv),
            cwd=os.getcwd() if cwd is None else cwd,
            env=os.environ.copy() if env is None else env,
            stdin=sys.stdin if stdin is None else stdin,
            stdout=sys.stdout if stdout is None else stdout,
            stderr=sys.stderr if stderr is None else stderr,
            terminate=terminate,
            max_steps=max_steps,
            max_heap_bytes=max_heap_bytes,
            effect_trace=effect_trace,
        )

    @classmethod
    def for_program(cls, program_name, arguments=(), **host):
        """Create runner argv without exposing the compiler's own process arguments."""
        return cls.from_process(argv=(program_name, *arguments), **host)

    def resolve_path(self, path):
        path = os.fspath(path)
        return path if os.path.isabs(path) else os.path.join(self.cwd, path)
