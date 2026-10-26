"""Stop a launched process before discovering and terminating its descendants."""
import ctypes
import os
from pathlib import Path
import signal
import sys


def _children(parent):
    if sys.platform == 'darwin':
        library = ctypes.CDLL('/usr/lib/libproc.dylib', use_errno=True)
        query = library.proc_listchildpids
        query.argtypes = (ctypes.c_int, ctypes.c_void_p, ctypes.c_int)
        query.restype = ctypes.c_int
        capacity = max(query(parent, None, 0) + 32, 128)
        values = (ctypes.c_int * capacity)()
        count = query(parent, values, ctypes.sizeof(values))
        if count < 0:
            raise OSError(ctypes.get_errno(), 'Unable to enumerate launched process children')
        return [pid for pid in values[:count] if pid > 0]
    values = []
    for task in (Path('/proc') / str(parent) / 'task').glob('*/children'):
        try:
            values.extend(int(value) for value in task.read_text().split())
        except FileNotFoundError:
            continue
    return values


def terminate_tree(root):
    processes = [root]
    try:
        os.kill(root, signal.SIGSTOP)
    except ProcessLookupError:
        return
    try:
        for parent in processes:
            for child in _children(parent):
                try:
                    os.kill(child, signal.SIGSTOP)
                    processes.append(child)
                except ProcessLookupError:
                    continue
    finally:
        for pid in reversed(processes):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            os.killpg(root, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            # Darwin can report EPERM for a group containing only terminated processes.
            # The individual descendants above have already been killed.
            pass
