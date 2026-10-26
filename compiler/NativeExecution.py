"""Native application execution with scoped streams and child-process cleanup."""
from dataclasses import dataclass
import io
import os
import subprocess
from common.ProcessTree import terminate_tree


@dataclass(frozen=True)
class NativeExecutionResult:
    exit_code: int | None
    signal: int | None = None
    timed_out: bool = False
    interrupted: bool = False


def _has_descriptor(stream):
    try:
        stream.fileno()
        return True
    except (AttributeError, ValueError, io.UnsupportedOperation):
        return False


def run_native(executable, context, timeout_ms=None):
    capture_out = not _has_descriptor(context.stdout)
    capture_error = not _has_descriptor(context.stderr)
    capture_input = not _has_descriptor(context.stdin)
    input_text = context.stdin.read() if capture_input else None
    process = subprocess.Popen([os.fspath(executable), *context.argv[1:]], cwd=context.cwd,
                               env=dict(context.env), text=True, start_new_session=True,
                               stdin=subprocess.PIPE if capture_input else context.stdin,
                               stdout=subprocess.PIPE if capture_out else context.stdout,
                               stderr=subprocess.PIPE if capture_error else context.stderr)
    timed_out = interrupted = False
    try:
        stdout, stderr = process.communicate(input_text, timeout=None if timeout_ms is None else timeout_ms / 1000)
    except (subprocess.TimeoutExpired, KeyboardInterrupt) as error:
        timed_out = isinstance(error, subprocess.TimeoutExpired)
        interrupted = not timed_out
        terminate_tree(process.pid)
        stdout, stderr = process.communicate()
    if capture_out and stdout:
        context.stdout.write(stdout)
    if capture_error and stderr:
        context.stderr.write(stderr)
    if timed_out or interrupted:
        return NativeExecutionResult(None, timed_out=timed_out, interrupted=interrupted)
    return NativeExecutionResult(process.returncode if process.returncode >= 0 else None,
                                 -process.returncode if process.returncode < 0 else None)
