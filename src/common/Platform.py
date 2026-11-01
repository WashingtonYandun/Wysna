"""Small host adapters for compiler processes, cache locks and RSS telemetry."""
from contextlib import contextmanager
import os
import shlex
import shutil
import sys
import time


def compiler_command():
    configured = os.environ.get('CC')
    from error.Error import ToolchainError
    try:
        bundled = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                               'toolchain', 'zig', 'zig.exe')
        if configured:
            command = [configured] if os.path.isfile(configured) else shlex.split(configured)
        elif os.name == 'nt' and os.path.isfile(bundled):
            command = [bundled, 'cc', '-target', 'x86_64-windows-gnu', '-mcpu=baseline']
        else:
            command = ['clang' if os.name == 'nt' else 'cc']
    except ValueError as error:
        raise ToolchainError('Invalid CC command: ' + str(error)) from error
    if not command or shutil.which(command[0]) is None:
        requested = configured if configured else command[0]
        raise ToolchainError('C compiler is unavailable or not executable: ' + repr(requested) +
                             '. Install a C99 toolchain or set CC to its compiler path.')
    return command


WINDOWS_NATIVE_STACK_BYTES = 8 * 1024 * 1024


def native_stack_link_args(command):
    """Keep native compiler/application stack reserves consistent across Windows linkers."""
    if os.name != 'nt':
        return []
    compiler = command[0].replace('\\', '/').rsplit('/', 1)[-1].lower()
    gnu = (compiler in ('zig', 'zig.exe', 'gcc', 'gcc.exe') or 'mingw' in compiler
           or any('windows-gnu' in argument.lower() or 'mingw' in argument.lower() for argument in command))
    prefix = '-Wl,--stack,' if gnu else '-Wl,/STACK:'
    return [prefix + str(WINDOWS_NATIVE_STACK_BYTES)]


def executable_name(name):
    return name + '.exe' if os.name == 'nt' and not name.lower().endswith('.exe') else name


@contextmanager
def cache_lock(path, timeout=600):
    with open(path, 'a+b') as lock:
        if os.name == 'nt':
            import msvcrt
            lock.seek(0, os.SEEK_END)
            if lock.tell() == 0:
                lock.write(b'\0')
                lock.flush()
            deadline = time.monotonic() + timeout
            while True:
                lock.seek(0)
                try:
                    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Timed out waiting for the Wysna bootstrap cache lock')
                    time.sleep(0.05)
            try:
                yield
            finally:
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def peak_rss_kb():
    if os.name != 'nt':
        import resource
        value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return value // 1024 if sys.platform == 'darwin' else value
    import ctypes
    from ctypes import wintypes

    class MemoryCounters(ctypes.Structure):
        _fields_ = [('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD)] + [
            (field, ctypes.c_size_t) for field in ('PeakWorkingSetSize', 'WorkingSetSize',
             'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
             'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    psapi = ctypes.WinDLL('psapi', use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(MemoryCounters), wintypes.DWORD]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    counters = MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.PeakWorkingSetSize // 1024
