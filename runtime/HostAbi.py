"""Reference implementation of the internal, checked scalar/opaque resource ABI."""
import atexit
import concurrent.futures
import errno
import os
from pathlib import Path
import signal
import stat
import subprocess
import threading
import time
from urllib.parse import urlsplit

_entries = {}
_next_id = 1
_error_code = 0
_error_domain = 0
_process_jobs = {}
_active_trace = None
_host_lock = threading.RLock()


def parallel_worker_dispatch(worker_callable, items, max_workers=None):
    """Deterministic, thread-safe parallel worker dispatch over items."""
    if not items:
        return []
    if len(items) == 1:
        return [worker_callable(items[0])]
    worker_count = max_workers or min(os.cpu_count() or 4, len(items), 8)
    with concurrent.futures.ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = [executor.submit(worker_callable, item) for item in items]
        return [f.result() for f in futures]


def set_effect_trace(trace):
    global _active_trace
    with _host_lock:
        _active_trace = trace


def failure(error):
    global _error_code, _error_domain
    with _host_lock:
        _error_code = getattr(error, 'winerror', None) or getattr(error, 'errno', None) or errno.EIO
        _error_domain = 2 if getattr(error, 'winerror', None) else 1
    return -1


def allocate(kind, value):
    global _next_id
    with _host_lock:
        handle = _next_id
        _next_id += 1
        _entries[handle] = (kind, value)
        return handle


def find(handle, kind):
    with _host_lock:
        entry = _entries.get(handle)
        return entry[1] if entry and entry[0] == kind else None


def close(handle):
    with _host_lock:
        entry = _entries.pop(handle, None)
    if entry and entry[0] == 'file':
        if isinstance(entry[1], dict):
            return 0
        try:
            entry[1].close()
        except OSError as error:
            return failure(error)
    return 0


def cleanup():
    with _host_lock:
        handles = list(_entries)
    for handle in handles:
        close(handle)
    with _async_coro_cond:
        for task in _async_coro_tasks.values():
            task.done = True
            task.active = False
        _async_coro_tasks.clear()
        _async_coro_active_stack.clear()
        _async_coro_cond.notify_all()


atexit.register(cleanup)


def path_text(handle):
    data = find(handle, 'buffer')
    if not data or 0 in data:
        return None
    try:
        return data.decode('utf-8')
    except UnicodeError:
        return None


def file_open(handle, mode):
    global _error_code, _error_domain
    path = path_text(handle)
    if path is None or mode not in (0, 1, 2, 3):
        failure(OSError(errno.EINVAL, 'invalid path or mode'))
        return 0
    if _active_trace is not None and _active_trace.is_replaying:
        ok, payload = _active_trace.replay('host.file_open', [path, mode])
        if not ok:
            if isinstance(payload, dict):
                _error_code = int(payload.get('error_code', errno.ENOENT))
                _error_domain = int(payload.get('error_domain', 1))
            else:
                failure(OSError(errno.ENOENT, str(payload)))
            return 0
        return allocate('file', {'replayed_path': path, 'mode': mode})
    try:
        opened = open(path, ('rb', 'wb', 'ab', 'xb')[mode])
        if _active_trace is not None and _active_trace.is_recording:
            _active_trace.record('host.file_open', [path, mode], ok=True, result={'status': 'opened'})
        return allocate('file', opened)
    except OSError as error:
        failure(error)
        if _active_trace is not None and _active_trace.is_recording:
            _active_trace.record(
                'host.file_open',
                [path, mode],
                ok=False,
                result={'error_code': _error_code, 'error_domain': _error_domain},
                error=str(error),
            )
        return 0


def buffer_get(handle, index):
    data = find(handle, 'buffer')
    return data[index] if data is not None and 0 <= index < len(data) else -1


def buffer_size(handle):
    data = find(handle, 'buffer')
    return len(data) if data is not None else -1


def buffer_append(handle, value):
    data = find(handle, 'buffer')
    if data is None or not 0 <= value <= 255:
        return -1
    data.append(value)
    return 0


def file_read(file_handle, buffer_handle, count):
    global _error_code, _error_domain
    file, buffer = find(file_handle, 'file'), find(buffer_handle, 'buffer')
    if file is None or buffer is None:
        return failure(OSError(errno.EBADF, 'closed handle'))
    if not 0 < count <= 16777216:
        return failure(OSError(errno.EINVAL, 'invalid read bound'))
    file_path = file.get('replayed_path', '') if isinstance(file, dict) else getattr(file, 'name', '')
    if _active_trace is not None and _active_trace.is_replaying:
        ok, payload = _active_trace.replay('host.file_read', [file_path, count])
        if not ok:
            if isinstance(payload, dict):
                _error_code = int(payload.get('error_code', errno.EIO))
                _error_domain = int(payload.get('error_domain', 1))
                return -1
            return failure(OSError(errno.EIO, str(payload)))
        raw_bytes = bytes.fromhex(payload['hex']) if isinstance(payload, dict) else str(payload).encode('utf-8')
        buffer[:] = raw_bytes
        return len(buffer)
    try:
        buffer[:] = file.read(count)
        if _active_trace is not None and _active_trace.is_recording:
            _active_trace.record('host.file_read', [file_path, count], ok=True, result={'hex': bytes(buffer).hex()})
        return len(buffer)
    except (OSError, ValueError) as error:
        res = failure(error)
        if _active_trace is not None and _active_trace.is_recording:
            _active_trace.record('host.file_read', [file_path, count], ok=False, error=str(error))
        return res


def file_write(file_handle, buffer_handle):
    global _error_code, _error_domain
    file, buffer = find(file_handle, 'file'), find(buffer_handle, 'buffer')
    if file is None or buffer is None:
        return failure(OSError(errno.EBADF, 'closed handle'))
    file_path = file.get('replayed_path', '') if isinstance(file, dict) else getattr(file, 'name', '')
    hex_data = bytes(buffer).hex()
    if _active_trace is not None and _active_trace.is_replaying:
        ok, payload = _active_trace.replay('host.file_write', [file_path, hex_data])
        if not ok:
            if isinstance(payload, dict):
                _error_code = int(payload.get('error_code', errno.EIO))
                _error_domain = int(payload.get('error_domain', 1))
                return -1
            return failure(OSError(errno.EIO, str(payload)))
        return 0
    try:
        written = file.write(buffer)
        if written != len(buffer):
            return failure(OSError(errno.EIO, 'short write'))
        file.flush()
        if _active_trace is not None and _active_trace.is_recording:
            _active_trace.record('host.file_write', [file_path, hex_data], ok=True, result=0)
        return 0
    except (OSError, ValueError) as error:
        res = failure(error)
        if _active_trace is not None and _active_trace.is_recording:
            _active_trace.record('host.file_write', [file_path, hex_data], ok=False, error=str(error))
        return res


def filesystem(first, second, operation):
    text = path_text(first)
    other = path_text(second) if operation == 4 else None
    if text is None or (operation == 4 and other is None):
        return failure(OSError(errno.EINVAL, 'invalid path'))
    if _active_trace is not None and _active_trace.is_replaying:
        ok, payload = _active_trace.replay('host.filesystem', [text, other, operation])
        if not ok:
            return failure(OSError(errno.ENOENT, str(payload)))
        return int(payload)
    path = Path(text)
    try:
        if operation in (0, 1, 5, 6):
            try:
                status = path.stat()
            except (FileNotFoundError, NotADirectoryError) as error:
                if operation in (0, 5, 6):
                    if _active_trace is not None and _active_trace.is_recording:
                        _active_trace.record('host.filesystem', [text, other, operation], ok=True, result=0)
                    return 0
                res = failure(error)
                if _active_trace is not None and _active_trace.is_recording:
                    _active_trace.record('host.filesystem', [text, other, operation], ok=False, error=str(error))
                return res
            if operation == 0:
                ret = 1
            elif operation == 1:
                ret = status.st_size
            elif operation == 5:
                ret = 1 if stat.S_ISDIR(status.st_mode) else 0
            else:
                ret = 1 if stat.S_ISREG(status.st_mode) else 0
            if _active_trace is not None and _active_trace.is_recording:
                _active_trace.record('host.filesystem', [text, other, operation], ok=True, result=ret)
            return ret
        if operation == 2:
            path.mkdir()
        elif operation == 3:
            path.rmdir() if path.is_dir() else path.unlink()
        elif operation == 4:
            os.replace(path, other)
        else:
            return -1
        if _active_trace is not None and _active_trace.is_recording:
            _active_trace.record('host.filesystem', [text, other, operation], ok=True, result=0)
        return 0
    except (OSError, ValueError) as error:
        res = failure(error)
        if _active_trace is not None and _active_trace.is_recording:
            _active_trace.record('host.filesystem', [text, other, operation], ok=False, error=str(error))
        return res


def canonical_path(path_handle, output_handle):
    global _error_code, _error_domain
    _error_code = 0
    _error_domain = 0
    path = path_text(path_handle)
    output = find(output_handle, 'buffer')
    if path is None or output is None or len(output):
        return failure(OSError(errno.EINVAL, 'invalid canonical path buffers'))
    if _active_trace is not None and _active_trace.is_replaying:
        ok, payload = _active_trace.replay('host.canonical_path', [path])
        if not ok:
            return failure(OSError(errno.ENOENT, str(payload)))
        encoded = str(payload).encode('utf-8')
        output.extend(encoded)
        return len(encoded)
    try:
        resolved = str(Path(path).resolve(strict=True))
        encoded = resolved.encode('utf-8')
        output.extend(encoded)
        if _active_trace is not None and _active_trace.is_recording:
            _active_trace.record('host.canonical_path', [path], ok=True, result=resolved)
        return len(encoded)
    except UnicodeError:
        return failure(OSError(errno.EILSEQ, 'canonical path is not UTF-8'))
    except (OSError, ValueError) as error:
        res = failure(error)
        if _active_trace is not None and _active_trace.is_recording:
            _active_trace.record('host.canonical_path', [path], ok=False, error=str(error))
        return res


def claim(raw):
    if any(kind == 'claim' and value == raw for kind, value in _entries.values()):
        return 0
    return allocate('claim', raw)


def attach_process_tree(process):
    """Place a Windows child in a kill-on-close job when the host permits it."""
    if os.name != 'nt':
        return
    import ctypes
    from ctypes import wintypes

    class BasicLimitInformation(ctypes.Structure):
        _fields_ = [
            ('PerProcessUserTimeLimit', ctypes.c_longlong),
            ('PerJobUserTimeLimit', ctypes.c_longlong),
            ('LimitFlags', wintypes.DWORD),
            ('MinimumWorkingSetSize', ctypes.c_size_t),
            ('MaximumWorkingSetSize', ctypes.c_size_t),
            ('ActiveProcessLimit', wintypes.DWORD),
            ('Affinity', ctypes.c_size_t),
            ('PriorityClass', wintypes.DWORD),
            ('SchedulingClass', wintypes.DWORD),
        ]

    class IoCounters(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in (
            'ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
            'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]

    class ExtendedLimitInformation(ctypes.Structure):
        _fields_ = [
            ('BasicLimitInformation', BasicLimitInformation),
            ('IoInfo', IoCounters),
            ('ProcessMemoryLimit', ctypes.c_size_t),
            ('JobMemoryLimit', ctypes.c_size_t),
            ('PeakProcessMemoryUsed', ctypes.c_size_t),
            ('PeakJobMemoryUsed', ctypes.c_size_t),
        ]

    kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.SetInformationJobObject.argtypes = [
        wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel32.SetInformationJobObject.restype = wintypes.BOOL
    kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    job = kernel32.CreateJobObjectW(None, None)
    if not job:
        return
    limits = ExtendedLimitInformation()
    limits.BasicLimitInformation.LimitFlags = 0x00002000
    if not kernel32.SetInformationJobObject(
            job, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        kernel32.CloseHandle(job)
        return
    if not kernel32.AssignProcessToJobObject(job, wintypes.HANDLE(process._handle)):
        kernel32.CloseHandle(job)
        return
    _process_jobs[process.pid] = (job, kernel32)


def release_process_tree(process):
    """Close the child job so any remaining descendants are stopped."""
    record = _process_jobs.pop(process.pid, None)
    if record is not None:
        job, kernel32 = record
        kernel32.CloseHandle(job)


def terminate_process_tree(process, *, force_group=False):
    """Stop a child and its descendants after a timeout or capture limit."""
    if process.poll() is not None and not force_group:
        return
    record = _process_jobs.get(process.pid)
    if record is not None:
        job, kernel32 = record
        kernel32.TerminateJobObject(job, 1)
    if os.name == 'nt':
        system_root = os.environ.get('SystemRoot', r'C:\Windows')
        taskkill = Path(system_root) / 'System32' / 'taskkill.exe'
        if taskkill.is_file():
            try:
                subprocess.run([str(taskkill), '/PID', str(process.pid), '/T', '/F'],
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               check=False, timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except OSError:
            pass


_HTTP_INVALID_REQUEST = 1
_HTTP_UNAVAILABLE = 2
_HTTP_TRANSPORT = 3
_HTTP_TLS = 4
_HTTP_TIMEOUT = 5
_HTTP_LIMIT = 6
_HTTP_INVALID_RESPONSE = 7
_HTTP_MAX_BODY = 1024 * 1024
_HTTP_MAX_HEADERS = 32 * 1024
_HTTP_MAX_HEADER_FIELDS = 100
_HTTP_MAX_URL = 8192
_HTTP_MAX_TIMEOUT_MS = 30000
_HTTP_CONNECT_TIMEOUT_MS = 5000


def _http_failure(kind, code):
    global _error_code, _error_domain
    _error_code = int(code or errno.EIO)
    _error_domain = 3
    return -kind


def _http_request_headers(data):
    if len(data) > _HTTP_MAX_HEADERS:
        raise ValueError('request headers exceed the limit')
    if not data:
        return []
    if data[-1] != 10 or b'\r' in data or b'\0' in data:
        raise ValueError('request headers have invalid framing')
    lines = data[:-1].split(b'\n')
    if len(lines) > _HTTP_MAX_HEADER_FIELDS:
        raise ValueError('request has too many headers')
    headers = []
    token = b"!#$%&'*+-.^_`|~"
    forbidden = {b'host', b'content-length', b'transfer-encoding', b'connection',
                 b'proxy-authorization'}
    for line in lines:
        name, separator, value = line.partition(b':')
        if (not separator or not name or any(byte not in token and not 48 <= byte <= 57
                                              and not 65 <= byte <= 90 and not 97 <= byte <= 122
                                              for byte in name)
                or name.lower() in forbidden
                or any((byte < 32 and byte != 9) or byte > 126 for byte in value)):
            raise ValueError('request contains an invalid or reserved header')
        if value.startswith(b' '):
            value = value[1:]
        headers.append((name.decode('ascii'), value.decode('ascii')))
    return headers


def _execute_http_transfer(url, method, headers, request_body, timeout_ms, max_response_bytes,
                           cancelled=None):
    try:
        import pycurl
    except ImportError:
        return (0, b'', b'', _HTTP_UNAVAILABLE, getattr(errno, 'ENOSYS', errno.EIO))
    try:
        if int(pycurl.version_info()[2]) < 0x080300:
            return (0, b'', b'', _HTTP_UNAVAILABLE, getattr(errno, 'EPROTONOSUPPORT', errno.EIO))
    except (AttributeError, IndexError, TypeError, ValueError):
        return (0, b'', b'', _HTTP_UNAVAILABLE, getattr(errno, 'EPROTONOSUPPORT', errno.EIO))

    response_headers_output = bytearray()
    response_body_output = bytearray()
    response_field_count = 0
    response_header_bytes = 0
    callback_error = 0

    def receive_headers(line):
        nonlocal response_field_count, response_header_bytes, callback_error
        if line.startswith(b'HTTP/'):
            if len(line) > _HTTP_MAX_HEADERS:
                callback_error = _HTTP_LIMIT
                return 0
            response_headers_output.clear()
            response_field_count = 0
            response_header_bytes = len(line)
            return len(line)
        if line in (b'\r\n', b'\n', b''):
            if response_header_bytes + len(line) > _HTTP_MAX_HEADERS:
                callback_error = _HTTP_LIMIT
                return 0
            response_header_bytes += len(line)
            return len(line)
        if (response_field_count >= _HTTP_MAX_HEADER_FIELDS
                or response_header_bytes + len(line) > _HTTP_MAX_HEADERS):
            callback_error = _HTTP_LIMIT
            return 0
        content = line.rstrip(b'\r\n')
        name, separator, value = content.partition(b':')
        token = b"!#$%&'*+-.^_`|~"
        if (not separator or not name
                or any(byte < 32 and byte != 9 or byte > 126 for byte in content)
                or any(byte not in token and not 48 <= byte <= 57
                       and not 65 <= byte <= 90 and not 97 <= byte <= 122 for byte in name)):
            callback_error = _HTTP_INVALID_RESPONSE
            return 0
        response_headers_output.extend(content + b'\n')
        response_field_count += 1
        response_header_bytes += len(line)
        return len(line)

    def receive_body(chunk):
        nonlocal callback_error
        if len(response_body_output) + len(chunk) > max_response_bytes:
            callback_error = _HTTP_LIMIT
            return 0
        response_body_output.extend(chunk)
        return len(chunk)

    curl = None
    try:
        curl = pycurl.Curl()
        curl.setopt(pycurl.URL, url)
        curl.setopt(pycurl.CUSTOMREQUEST, method)
        curl.setopt(pycurl.HTTPHEADER, [name + ': ' + value for name, value in headers])
        curl.setopt(pycurl.WRITEFUNCTION, receive_body)
        curl.setopt(pycurl.HEADERFUNCTION, receive_headers)
        curl.setopt(pycurl.FOLLOWLOCATION, 0)
        curl.setopt(pycurl.MAXREDIRS, 0)
        curl.setopt(pycurl.SSL_VERIFYPEER, 1)
        curl.setopt(pycurl.SSL_VERIFYHOST, 2)
        curl.setopt(pycurl.TIMEOUT_MS, timeout_ms)
        curl.setopt(pycurl.CONNECTTIMEOUT_MS, min(timeout_ms, _HTTP_CONNECT_TIMEOUT_MS))
        curl.setopt(pycurl.NOSIGNAL, 1)
        if cancelled is not None:
            curl.setopt(pycurl.NOPROGRESS, 0)
            curl.setopt(pycurl.XFERINFOFUNCTION, lambda *_: int(cancelled.is_set()))
        curl.setopt(pycurl.NETRC, pycurl.NETRC_IGNORED)
        curl.setopt(pycurl.PROTOCOLS, pycurl.PROTO_HTTP | pycurl.PROTO_HTTPS)
        curl.setopt(pycurl.HTTP_VERSION, pycurl.CURL_HTTP_VERSION_1_1)
        if method == 'HEAD':
            curl.setopt(pycurl.NOBODY, 1)
        if request_body or method in ('POST', 'PUT', 'PATCH'):
            curl.setopt(pycurl.POSTFIELDS, bytes(request_body))
            curl.setopt(pycurl.POSTFIELDSIZE_LARGE, len(request_body))
        curl.perform()
        status = int(curl.getinfo(pycurl.RESPONSE_CODE))
        if callback_error:
            code = getattr(errno, 'EOVERFLOW', errno.EINVAL) if callback_error == _HTTP_LIMIT else errno.EPROTO
            return (0, b'', b'', callback_error, code)
        if not 100 <= status <= 599:
            return (0, b'', b'', _HTTP_INVALID_RESPONSE, errno.EPROTO)
        return (status, bytes(response_headers_output), bytes(response_body_output), 0, 0)
    except pycurl.error as error:
        curl_code = error.args[0] if error.args and isinstance(error.args[0], int) else errno.EIO
        if curl is None:
            kind = _HTTP_UNAVAILABLE
        elif callback_error:
            kind = callback_error
        elif curl_code == getattr(pycurl, 'E_OPERATION_TIMEDOUT', -1):
            kind = _HTTP_TIMEOUT
        elif curl_code in (getattr(pycurl, 'E_PEER_FAILED_VERIFICATION', -1),
                           getattr(pycurl, 'E_SSL_CONNECT_ERROR', -1),
                           getattr(pycurl, 'E_SSL_CERTPROBLEM', -1)):
            kind = _HTTP_TLS
        elif curl_code in (getattr(pycurl, 'E_URL_MALFORMAT', -1),
                           getattr(pycurl, 'E_UNSUPPORTED_PROTOCOL', -1),
                           getattr(pycurl, 'E_BAD_FUNCTION_ARGUMENT', -1)):
            kind = _HTTP_INVALID_REQUEST
        else:
            kind = _HTTP_TRANSPORT
        return (0, b'', b'', kind, curl_code)
    except (TypeError, ValueError, UnicodeError):
        return (0, b'', b'', _HTTP_INVALID_REQUEST, errno.EINVAL)
    except OSError as error:
        return (0, b'', b'', _HTTP_TRANSPORT, getattr(error, 'errno', None) or errno.EIO)
    finally:
        if curl is not None:
            try:
                curl.close()
            except Exception:
                pass


_async_http_transfers = {}
_async_http_cancellations = {}
_async_http_next_id = 1
_TIMER_HANDLE_BASE = 1000000
_SPAWN_HANDLE_BASE = 2000000
_CORO_HANDLE_BASE = 3000000
_async_timers = {}
_async_timer_next_id = _TIMER_HANDLE_BASE
_async_spawn_tasks = {}
_async_spawn_next_id = _SPAWN_HANDLE_BASE
_async_coro_tasks = {}
_async_coro_next_id = _CORO_HANDLE_BASE
_async_coro_cond = threading.Condition()
_async_coro_active_stack = []
_async_coro_tls = threading.local()
_async_http_executor = concurrent.futures.ThreadPoolExecutor(max_workers=16)


class _CoroTask:
    def __init__(self, handle_id, thunk):
        self.handle_id = handle_id
        self.thunk = thunk
        self.started = False
        self.done = False
        self.active = False
        self.suspended_on = None
        self.result = None
        self.error = None
        self.thread = None


def coro_current_id():
    return getattr(_async_coro_tls, 'current_coro_id', None)


def coro_spawn(thunk):
    global _async_coro_next_id
    with _async_coro_cond:
        tid = _async_coro_next_id
        _async_coro_next_id += 1
        _async_coro_tasks[tid] = _CoroTask(tid, thunk)
        return tid


def _poll_child_noblock(handle_id):
    if handle_id >= _CORO_HANDLE_BASE:
        return coro_poll(handle_id)
    if handle_id >= _SPAWN_HANDLE_BASE:
        with _host_lock:
            future = _async_spawn_tasks.get(handle_id)
        if future is None:
            return -1
        return 1 if future.done() else 0
    if handle_id >= _TIMER_HANDLE_BASE:
        now = time.monotonic()
        with _host_lock:
            entry = _async_timers.get(handle_id)
            if entry is None:
                return -1
            deadline, done = entry
            if not done and now >= deadline:
                _async_timers[handle_id] = (deadline, True)
                done = True
            return 1 if done else 0
    with _host_lock:
        future = _async_http_transfers.get(handle_id)
    if future is None:
        return -1
    return 1 if future.done() else 0


def _coro_worker(task):
    with _async_coro_cond:
        while not _async_coro_active_stack or _async_coro_active_stack[-1] != task.handle_id:
            if task.done:
                return
            _async_coro_cond.wait(timeout=0.05)
    prev_id = getattr(_async_coro_tls, 'current_coro_id', None)
    _async_coro_tls.current_coro_id = task.handle_id
    res = None
    err = None
    try:
        res = task.thunk()
    except BaseException as exc:
        err = exc
    finally:
        _async_coro_tls.current_coro_id = prev_id
        with _async_coro_cond:
            task.result = res
            task.error = err
            task.done = True
            task.suspended_on = None
            task.active = False
            if _async_coro_active_stack and _async_coro_active_stack[-1] == task.handle_id:
                _async_coro_active_stack.pop()
            _async_coro_cond.notify_all()


def coro_yield_until_ready(handle_id):
    cur_id = coro_current_id()
    if cur_id is None or handle_id is None or handle_id <= 0:
        return
    if handle_id < _CORO_HANDLE_BASE and _poll_child_noblock(handle_id) != 0:
        return
    with _async_coro_cond:
        task = _async_coro_tasks.get(cur_id)
        if task is None:
            return
        task.suspended_on = handle_id
        task.active = False
        if _async_coro_active_stack and _async_coro_active_stack[-1] == cur_id:
            _async_coro_active_stack.pop()
        _async_coro_cond.notify_all()
        while not task.done:
            if _async_coro_active_stack and _async_coro_active_stack[-1] == cur_id:
                task.active = True
                break
            _async_coro_cond.wait(timeout=0.05)


def coro_poll(handle_id):
    with _async_coro_cond:
        task = _async_coro_tasks.get(handle_id)
        if task is None:
            return -1
        if task.done:
            return 1
        if task.active or task.handle_id in _async_coro_active_stack:
            return 0
        suspended_on = task.suspended_on if task.started else None
    if suspended_on is not None:
        if suspended_on < _CORO_HANDLE_BASE:
            if _poll_child_noblock(suspended_on) == 0:
                return 0
        else:
            child_st = coro_poll(suspended_on)
            if child_st == 0:
                return 0
    with _async_coro_cond:
        if task.done:
            return 1
        if task.active or task.handle_id in _async_coro_active_stack:
            return 0
        if not task.started:
            task.started = True
            task.thread = threading.Thread(target=_coro_worker, args=(task,), daemon=True)
            task.thread.start()
        task.suspended_on = None
        task.active = True
        _async_coro_active_stack.append(task.handle_id)
        _async_coro_cond.notify_all()
        while not task.done and task.suspended_on is None and task.handle_id in _async_coro_active_stack:
            _async_coro_cond.wait(timeout=0.05)
        return 1 if task.done else 0


def coro_tick_all():
    while True:
        with _async_coro_cond:
            pending_ids = [
                tid for tid, t in _async_coro_tasks.items()
                if not t.done and not t.active and tid not in _async_coro_active_stack
            ]
        if not pending_ids:
            return 0
        progressed = False
        remaining = 0
        for tid in pending_ids:
            with _async_coro_cond:
                t = _async_coro_tasks.get(tid)
                if t is None or t.done:
                    continue
                if t.active or tid in _async_coro_active_stack:
                    remaining += 1
                    continue
                was_started = t.started
            st = coro_poll(tid)
            if st == 1 or not was_started:
                progressed = True
            if st == 0:
                remaining += 1
        if not progressed or remaining == 0:
            return remaining


def coro_get_result(handle_id):
    while True:
        st = coro_poll(handle_id)
        if st < 0:
            raise RuntimeError(f"Invalid coroutine task handle: {handle_id}")
        if st == 1:
            break
        http_multi_poll(10)
    with _async_coro_cond:
        task = _async_coro_tasks.get(handle_id)
    if task is None:
        return None
    if task.error is not None:
        raise task.error
    return task.result


def spawn_async_task(worker_callable):
    global _async_spawn_next_id
    with _host_lock:
        tid = _async_spawn_next_id
        _async_spawn_next_id += 1
        future = _async_http_executor.submit(worker_callable)
        _async_spawn_tasks[tid] = future
    return tid


def spawn_get_result(handle_id):
    with _host_lock:
        future = _async_spawn_tasks.get(handle_id)
    if future is None:
        raise RuntimeError(f"Invalid spawned task handle {handle_id}")
    return future.result()


def http_async_start(url_handle, method_handle, request_headers_handle, request_body_handle,
                     timeout_ms, max_response_bytes):
    global _error_code, _error_domain, _async_http_next_id
    _error_code = 0
    _error_domain = 0
    url_bytes = find(url_handle, 'buffer')
    method_bytes = find(method_handle, 'buffer')
    request_headers = find(request_headers_handle, 'buffer')
    request_body = find(request_body_handle, 'buffer')
    handles = (url_handle, method_handle, request_headers_handle, request_body_handle)
    if any(value is None for value in (url_bytes, method_bytes, request_headers, request_body)) or len(set(handles)) != len(handles):
        return _http_failure(_HTTP_INVALID_REQUEST, errno.EBADF)
    if (not 1 <= timeout_ms <= _HTTP_MAX_TIMEOUT_MS
            or not 1 <= max_response_bytes <= _HTTP_MAX_BODY
            or len(url_bytes) > _HTTP_MAX_URL or len(request_body) > _HTTP_MAX_BODY):
        return _http_failure(_HTTP_INVALID_REQUEST, errno.EINVAL)

    try:
        url = bytes(url_bytes).decode('ascii')
        method = bytes(method_bytes).decode('ascii')
        parsed = urlsplit(url)
        _ = parsed.port
        if (parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None or parsed.fragment
                or any(ord(character) <= 32 or ord(character) > 126 for character in url)):
            raise ValueError('URL must be an ASCII HTTP or HTTPS URL without user info or fragment')
        if method not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD'):
            raise ValueError('unsupported HTTP method')
        if method in ('GET', 'HEAD') and request_body:
            raise ValueError('GET and HEAD requests cannot include a body')
        headers = _http_request_headers(bytes(request_headers))
    except (UnicodeError, ValueError, OverflowError):
        return _http_failure(_HTTP_INVALID_REQUEST, errno.EINVAL)

    with _host_lock:
        transfer_id = _async_http_next_id
        _async_http_next_id += 1
        cancelled = threading.Event()
        future = _async_http_executor.submit(
            _execute_http_transfer,
            url, method, headers, bytes(request_body), timeout_ms, max_response_bytes, cancelled
        )
        _async_http_transfers[transfer_id] = future
        _async_http_cancellations[transfer_id] = cancelled
    return transfer_id


def http_async_poll(transfer_id):
    global _async_timer_next_id
    if transfer_id <= -1000:
        duration_ms = max(0, -(transfer_id + 1000))
        now = time.monotonic()
        with _host_lock:
            tid = _async_timer_next_id
            _async_timer_next_id += 1
            _async_timers[tid] = (now + duration_ms / 1000.0, duration_ms == 0)
        return tid
    if transfer_id >= _CORO_HANDLE_BASE:
        return coro_poll(transfer_id)
    if transfer_id >= _SPAWN_HANDLE_BASE:
        with _host_lock:
            future = _async_spawn_tasks.get(transfer_id)
        if future is None:
            return -1
        return 1 if future.done() else 0
    if transfer_id >= _TIMER_HANDLE_BASE:
        now = time.monotonic()
        with _host_lock:
            entry = _async_timers.get(transfer_id)
            if entry is None:
                return -1
            deadline, done = entry
            if not done and now >= deadline:
                done = True
                _async_timers[transfer_id] = (deadline, True)
            return 1 if done else 0
    with _host_lock:
        future = _async_http_transfers.get(transfer_id)
    if future is None:
        return -1
    if not future.done():
        return 0
    try:
        status, _, _, err_kind, _ = future.result()
        return -1 if err_kind != 0 else 1
    except Exception:
        return -1


def http_multi_poll(timeout_ms):
    coro_tick_all()
    now = time.monotonic()
    with _host_lock:
        pending_http = [f for f in _async_http_transfers.values() if not f.done()]
        pending_spawns = [f for f in _async_spawn_tasks.values() if not f.done()]
        pending = pending_http + pending_spawns
        pending_deadlines = []
        for tid, (deadline, done) in list(_async_timers.items()):
            if not done:
                if now >= deadline:
                    _async_timers[tid] = (deadline, True)
                else:
                    pending_deadlines.append(deadline)
    if not pending and not pending_deadlines:
        return 0
    if not pending:
        min_rem = min(max(0.001, d - now) for d in pending_deadlines)
        time.sleep(min(max(0.001, timeout_ms / 1000.0), min_rem))
        now2 = time.monotonic()
        rem_count = 0
        with _host_lock:
            for tid, (deadline, done) in list(_async_timers.items()):
                if not done:
                    if now2 >= deadline:
                        _async_timers[tid] = (deadline, True)
                    else:
                        rem_count += 1
        coro_rem = coro_tick_all()
        return rem_count + coro_rem
    timeout_sec = max(0.001, timeout_ms / 1000.0)
    if pending_deadlines:
        timeout_sec = min(timeout_sec, min(max(0.001, d - now) for d in pending_deadlines))
    done, not_done = concurrent.futures.wait(pending, timeout=timeout_sec, return_when=concurrent.futures.FIRST_COMPLETED)
    now2 = time.monotonic()
    with _host_lock:
        rem_timers = 0
        for tid, (deadline, t_done) in list(_async_timers.items()):
            if not t_done:
                if now2 >= deadline:
                    _async_timers[tid] = (deadline, True)
                else:
                    rem_timers += 1
    coro_rem = coro_tick_all()
    return len(not_done) + rem_timers + coro_rem


def http_async_finish(transfer_id, response_headers_handle, response_body_handle):
    with _host_lock:
        future = _async_http_transfers.pop(transfer_id, None)
        _async_http_cancellations.pop(transfer_id, None)
    if future is None:
        return _http_failure(_HTTP_INVALID_REQUEST, errno.EINVAL)
    response_headers = find(response_headers_handle, 'buffer')
    response_body = find(response_body_handle, 'buffer')
    if response_headers is None or response_body is None:
        return _http_failure(_HTTP_INVALID_REQUEST, errno.EBADF)
    try:
        status, h_out, b_out, err_kind, err_code = future.result()
    except Exception:
        return _http_failure(_HTTP_TRANSPORT, errno.EIO)
    if err_kind != 0:
        return _http_failure(err_kind, err_code)
    response_headers[:] = h_out
    response_body[:] = b_out
    return status


def http_async_cancel(transfer_id):
    with _host_lock:
        future = _async_http_transfers.pop(transfer_id, None)
        cancelled = _async_http_cancellations.pop(transfer_id, None)
    if future is None:
        return 0
    cancelled.set()
    if not future.cancel():
        # The next libcurl progress callback interrupts a running transfer.
        # Wait outside the registry lock until its curl handle is closed.
        concurrent.futures.wait([future])
    return 1


def http_request(url_handle, method_handle, request_headers_handle, request_body_handle,
                 response_headers_handle, response_body_handle, timeout_ms, max_response_bytes):
    """Perform one synchronous HTTP(S) request with bounded input and output."""
    global _error_code, _error_domain
    _error_code = 0
    _error_domain = 0
    url_bytes = find(url_handle, 'buffer')
    method_bytes = find(method_handle, 'buffer')
    request_headers = find(request_headers_handle, 'buffer')
    request_body = find(request_body_handle, 'buffer')
    response_headers = find(response_headers_handle, 'buffer')
    response_body = find(response_body_handle, 'buffer')
    handles = (url_handle, method_handle, request_headers_handle, request_body_handle,
               response_headers_handle, response_body_handle)
    if (any(value is None for value in (url_bytes, method_bytes, request_headers, request_body,
                                        response_headers, response_body))
            or len(set(handles)) != len(handles)):
        return _http_failure(_HTTP_INVALID_REQUEST, errno.EBADF)
    if (not 1 <= timeout_ms <= _HTTP_MAX_TIMEOUT_MS
            or not 1 <= max_response_bytes <= _HTTP_MAX_BODY
            or len(url_bytes) > _HTTP_MAX_URL or len(request_body) > _HTTP_MAX_BODY):
        return _http_failure(_HTTP_INVALID_REQUEST, errno.EINVAL)

    try:
        url = bytes(url_bytes).decode('ascii')
        method = bytes(method_bytes).decode('ascii')
        parsed = urlsplit(url)
        _ = parsed.port
        if (parsed.scheme.lower() not in ('http', 'https') or not parsed.hostname
                or parsed.username is not None or parsed.password is not None or parsed.fragment
                or any(ord(character) <= 32 or ord(character) > 126 for character in url)):
            raise ValueError('URL must be an ASCII HTTP or HTTPS URL without user info or fragment')
        if method not in ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD'):
            raise ValueError('unsupported HTTP method')
        if method in ('GET', 'HEAD') and request_body:
            raise ValueError('GET and HEAD requests cannot include a body')
        headers = _http_request_headers(bytes(request_headers))
    except (UnicodeError, ValueError, OverflowError):
        return _http_failure(_HTTP_INVALID_REQUEST, errno.EINVAL)

    trace_args = [
        method,
        url,
        bytes(request_headers).hex(),
        bytes(request_body).hex(),
        timeout_ms,
        max_response_bytes,
    ]
    if _active_trace is not None and _active_trace.is_replaying:
        ok, payload = _active_trace.replay('host.http_request', trace_args)
        if not ok:
            if isinstance(payload, dict):
                return _http_failure(
                    int(payload.get('kind', _HTTP_TRANSPORT)),
                    int(payload.get('error_code', errno.EIO)),
                )
            return _http_failure(_HTTP_TRANSPORT, errno.EIO)
        response_headers[:] = bytes.fromhex(payload.get('headers_hex', ''))
        response_body[:] = bytes.fromhex(payload.get('body_hex', ''))
        return int(payload['status'])

    status, h_out, b_out, err_kind, err_code = _execute_http_transfer(
        url, method, headers, request_body, timeout_ms, max_response_bytes
    )
    if err_kind != 0:
        return _http_failure(err_kind, err_code)
    response_headers[:] = h_out
    response_body[:] = b_out
    if _active_trace is not None and _active_trace.is_recording:
        _active_trace.record(
            'host.http_request',
            trace_args,
            ok=True,
            result={
                'status': status,
                'headers_hex': bytes(h_out).hex(),
                'body_hex': bytes(b_out).hex(),
            },
        )
    return status


def _decode_process_request(executable_handle, arguments_handle, timeout_ms):
    executable_bytes = find(executable_handle, 'buffer')
    encoded_arguments = find(arguments_handle, 'buffer')
    if executable_bytes is None or encoded_arguments is None or not 1 <= timeout_ms <= 600000:
        return None, None, failure(OSError(errno.EINVAL, 'invalid process request'))
    if not executable_bytes or b'\x00' in executable_bytes:
        return None, None, failure(OSError(errno.EINVAL, 'invalid executable path'))
    try:
        executable = executable_bytes.decode('utf-8')
        arguments = []
        offset = 0
        while offset < len(encoded_arguments):
            if len(arguments) >= 256 or offset + 4 > len(encoded_arguments):
                return None, None, failure(OSError(errno.E2BIG, 'invalid argument block'))
            length = int.from_bytes(encoded_arguments[offset:offset + 4], 'big')
            offset += 4
            if length > 1048576 or offset + length > len(encoded_arguments):
                return None, None, failure(OSError(errno.E2BIG, 'argument exceeds its limit'))
            raw_argument = encoded_arguments[offset:offset + length]
            offset += length
            if b'\x00' in raw_argument:
                return None, None, failure(OSError(errno.EINVAL, 'argument contains NUL'))
            arguments.append(raw_argument.decode('utf-8'))
            if offset > 16777216:
                return None, None, failure(OSError(errno.E2BIG, 'argument block exceeds its limit'))
        return executable, arguments, 0
    except (OSError, UnicodeError, ValueError) as error:
        return None, None, failure(error)


def process_run(executable_handle, arguments_handle, timeout_ms, *, cwd=None, env=None):
    global _error_code, _error_domain
    _error_code = 0
    _error_domain = 0
    executable, arguments, status = _decode_process_request(executable_handle, arguments_handle, timeout_ms)
    if status != 0:
        return status
    try:
        if _active_trace is not None and _active_trace.is_replaying:
            ok, payload = _active_trace.replay('host.process_run', [executable, arguments, timeout_ms])
            if not ok:
                return failure(OSError(errno.EIO, str(payload)))
            return int(payload)
        process = subprocess.Popen(
            [executable, *arguments], cwd=cwd,
            env=dict(env) if env is not None else None,
            start_new_session=(os.name != 'nt'),
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0),
        )
        attach_process_tree(process)
        try:
            try:
                exit_code = int(process.wait(timeout=timeout_ms / 1000))
                final_code = 128 + abs(exit_code) if exit_code < 0 else exit_code
                if _active_trace is not None and _active_trace.is_recording:
                    _active_trace.record('host.process_run', [executable, arguments, timeout_ms], ok=True, result=final_code)
                return final_code
            except subprocess.TimeoutExpired:
                terminate_process_tree(process, force_group=True)
                process.wait()
                res = failure(TimeoutError(errno.ETIMEDOUT, 'process timed out'))
                if _active_trace is not None and _active_trace.is_recording:
                    _active_trace.record('host.process_run', [executable, arguments, timeout_ms], ok=False, error='process timed out')
                return res
        finally:
            release_process_tree(process)
    except (OSError, UnicodeError, ValueError) as error:
        res = failure(error)
        if _active_trace is not None and _active_trace.is_recording:
            _active_trace.record('host.process_run', [executable, arguments, timeout_ms], ok=False, error=str(error))
        return res


def process_capture(executable_handle, arguments_handle, timeout_ms, max_output_bytes,
                    stdout_handle, stderr_handle, *, cwd=None, env=None):
    global _error_code, _error_domain
    _error_code = 0
    _error_domain = 0
    stdout_buf = find(stdout_handle, 'buffer')
    stderr_buf = find(stderr_handle, 'buffer')
    if (stdout_buf is None or stderr_buf is None or len(stdout_buf) or len(stderr_buf)
            or not 1 <= max_output_bytes <= 16777216):
        return failure(OSError(errno.EINVAL, 'invalid process capture request'))
    executable, arguments, status = _decode_process_request(executable_handle, arguments_handle, timeout_ms)
    if status != 0:
        return status
    try:
        if _active_trace is not None and _active_trace.is_replaying:
            ok, payload = _active_trace.replay('host.process_capture', [executable, arguments, timeout_ms, max_output_bytes])
            if not ok:
                return failure(OSError(errno.EIO, str(payload)))
            stdout_buf.extend(bytes.fromhex(payload.get('stdout_hex', '')))
            stderr_buf.extend(bytes.fromhex(payload.get('stderr_hex', '')))
            return int(payload.get('status', 0))
        process = subprocess.Popen(
            [executable, *arguments], cwd=cwd,
            env=dict(env) if env is not None else None,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=(os.name != 'nt'),
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0),
        )
        attach_process_tree(process)
        try:
            try:
                stdout_data, stderr_data = process.communicate(timeout=timeout_ms / 1000)
            except subprocess.TimeoutExpired:
                terminate_process_tree(process, force_group=True)
                process.communicate()
                res = failure(TimeoutError(errno.ETIMEDOUT, 'process timed out'))
                if _active_trace is not None and _active_trace.is_recording:
                    _active_trace.record('host.process_capture', [executable, arguments, timeout_ms, max_output_bytes],
                                         ok=False, error='process timed out')
                return res
            if len(stdout_data) > max_output_bytes or len(stderr_data) > max_output_bytes:
                res = failure(OSError(errno.E2BIG, 'process output exceeded limit'))
                if _active_trace is not None and _active_trace.is_recording:
                    _active_trace.record('host.process_capture', [executable, arguments, timeout_ms, max_output_bytes],
                                         ok=False, error='process output exceeded limit')
                return res
            stdout_buf.extend(stdout_data)
            stderr_buf.extend(stderr_data)
            exit_code = int(process.returncode)
            final_code = 128 + abs(exit_code) if exit_code < 0 else exit_code
            if _active_trace is not None and _active_trace.is_recording:
                _active_trace.record(
                    'host.process_capture', [executable, arguments, timeout_ms, max_output_bytes],
                    ok=True,
                    result={'status': final_code, 'stdout_hex': stdout_data.hex(), 'stderr_hex': stderr_data.hex()},
                )
            return final_code
        finally:
            release_process_tree(process)
    except (OSError, UnicodeError, ValueError) as error:
        res = failure(error)
        if _active_trace is not None and _active_trace.is_recording:
            _active_trace.record('host.process_capture', [executable, arguments, timeout_ms, max_output_bytes],
                                 ok=False, error=str(error))
        return res


def crypto_sha256(input_handle, output_handle):
    import hashlib
    inp = find(input_handle, 'buffer')
    out = find(output_handle, 'buffer')
    if inp is None or out is None:
        return failure(ValueError("Invalid buffer handle"))
    digest = hashlib.sha256(bytes(inp)).digest()
    out.clear()
    out.extend(digest)
    return 0


def crypto_hmac_sha256(key_handle, message_handle, output_handle):
    import hmac, hashlib
    k = find(key_handle, 'buffer')
    m = find(message_handle, 'buffer')
    out = find(output_handle, 'buffer')
    if k is None or m is None or out is None:
        return failure(ValueError("Invalid buffer handle"))
    digest = hmac.new(bytes(k), bytes(m), hashlib.sha256).digest()
    out.clear()
    out.extend(digest)
    return 0


def crypto_sha512(input_handle, output_handle):
    import hashlib
    inp = find(input_handle, 'buffer')
    out = find(output_handle, 'buffer')
    if inp is None or out is None:
        return failure(ValueError("Invalid buffer handle"))
    digest = hashlib.sha512(bytes(inp)).digest()
    out.clear()
    out.extend(digest)
    return 0


def crypto_hmac_sha512(key_handle, message_handle, output_handle):
    import hmac, hashlib
    k = find(key_handle, 'buffer')
    m = find(message_handle, 'buffer')
    out = find(output_handle, 'buffer')
    if k is None or m is None or out is None:
        return failure(ValueError("Invalid buffer handle"))
    digest = hmac.new(bytes(k), bytes(m), hashlib.sha512).digest()
    out.clear()
    out.extend(digest)
    return 0


def crypto_md5(input_handle, output_handle):
    import hashlib
    inp = find(input_handle, 'buffer')
    out = find(output_handle, 'buffer')
    if inp is None or out is None:
        return failure(ValueError("Invalid buffer handle"))
    digest = hashlib.md5(bytes(inp)).digest()
    out.clear()
    out.extend(digest)
    return 0


def crypto_random_bytes(output_handle, count):
    import os
    if count <= 0 or count > (1024 * 1024 * 64):
        return failure(ValueError("Invalid count"))
    out = find(output_handle, 'buffer')
    if out is None:
        return failure(ValueError("Invalid buffer handle"))
    rnd = os.urandom(int(count))
    out.clear()
    out.extend(rnd)
    return 0


def crypto_constant_time_compare(a_handle, b_handle):
    import hmac
    a = find(a_handle, 'buffer')
    b = find(b_handle, 'buffer')
    if a is None or b is None:
        return failure(ValueError("Invalid buffer handle"))
    return 1 if hmac.compare_digest(bytes(a), bytes(b)) else 0


def monotonic_ms():
    return int(time.monotonic() * 1000)


def wall_ms():
    return int(time.time() * 1000)


def sleep(duration_ms):
    if duration_ms < 0:
        return -1
    time.sleep(duration_ms / 1000.0)
    return 0


def stdout_flush():
    import sys
    try:
        sys.stdout.flush()
    except (OSError, ValueError):
        return -1
    return 0


def stdin_read_line(output_handle):
    import sys
    out = find(output_handle, 'buffer')
    if out is None:
        return failure(ValueError("Invalid buffer handle"))
    out.clear()
    stream = getattr(sys.stdin, 'buffer', None)
    if stream is None:
        return -1
    line = stream.readline()
    if not line:
        return 0
    if line.endswith(b'\n'):
        line = line[:-1]
    if line.endswith(b'\r'):
        line = line[:-1]
    out.extend(line)
    return 1


def stdin_read_all(output_handle):
    import sys
    out = find(output_handle, 'buffer')
    if out is None:
        return failure(ValueError("Invalid buffer handle"))
    out.clear()
    stream = getattr(sys.stdin, 'buffer', None)
    if stream is None:
        return -1
    out.extend(stream.read())
    return len(out)


def getcwd(output_handle):
    out = find(output_handle, 'buffer')
    if out is None:
        return failure(ValueError("Invalid buffer handle"))
    data = os.getcwd().encode('utf-8')
    out.clear()
    out.extend(data)
    return len(data)


def stdin_read_exact(output_handle, count):
    import sys
    if count < 0 or count > 16777216:
        return failure(ValueError("Invalid read byte count"))
    out = find(output_handle, 'buffer')
    if out is None:
        return failure(ValueError("Invalid buffer handle"))
    out.clear()
    if count == 0:
        return 0
    stream = getattr(sys.stdin, 'buffer', None)
    try:
        chunk = stream.read(count) if stream is not None else sys.stdin.read(count).encode('utf-8')
    except (OSError, ValueError) as error:
        return failure(error)
    if not chunk:
        return 0
    if len(chunk) < count:
        return -1
    out.extend(chunk)
    return len(chunk)


def stdout_write_raw(input_handle):
    import sys
    data = find(input_handle, 'buffer')
    if data is None:
        return failure(ValueError("Invalid buffer handle"))
    stream = getattr(sys.stdout, 'buffer', None)
    try:
        if stream is not None:
            stream.write(bytes(data))
            stream.flush()
        else:
            sys.stdout.write(bytes(data).decode('utf-8', errors='replace'))
            sys.stdout.flush()
    except (OSError, ValueError) as error:
        return failure(error)
    return 0


def self_executable(output_handle):
    import sys
    out = find(output_handle, 'buffer')
    if out is None:
        return failure(ValueError("Invalid buffer handle"))
    out.clear()
    raw = (sys.executable or "").encode('utf-8')
    out.extend(raw)
    return len(raw)


def _rotl32(value, count):
    return ((value << count) & 0xFFFFFFFF) | (value >> (32 - count))


def _chacha20_block(key, counter, nonce):
    import struct
    constants = (0x61707865, 0x3320646E, 0x79622D32, 0x6B206574)
    state = list(constants) + list(struct.unpack('<8I', key)) + [counter & 0xFFFFFFFF] + list(struct.unpack('<3I', nonce))
    x = list(state)

    def quarter(a, b, c, d):
        x[a] = (x[a] + x[b]) & 0xFFFFFFFF; x[d] = _rotl32(x[d] ^ x[a], 16)
        x[c] = (x[c] + x[d]) & 0xFFFFFFFF; x[b] = _rotl32(x[b] ^ x[c], 12)
        x[a] = (x[a] + x[b]) & 0xFFFFFFFF; x[d] = _rotl32(x[d] ^ x[a], 8)
        x[c] = (x[c] + x[d]) & 0xFFFFFFFF; x[b] = _rotl32(x[b] ^ x[c], 7)

    for _ in range(10):
        quarter(0, 4, 8, 12); quarter(1, 5, 9, 13); quarter(2, 6, 10, 14); quarter(3, 7, 11, 15)
        quarter(0, 5, 10, 15); quarter(1, 6, 11, 12); quarter(2, 7, 8, 13); quarter(3, 4, 9, 14)
    return struct.pack('<16I', *[(x[i] + state[i]) & 0xFFFFFFFF for i in range(16)])


def _chacha20_xor(key, counter, nonce, data):
    out = bytearray()
    for offset in range(0, len(data), 64):
        block = _chacha20_block(key, counter + offset // 64, nonce)
        chunk = data[offset:offset + 64]
        out.extend(byte ^ mask for byte, mask in zip(chunk, block))
    return bytes(out)


def _poly1305(key, message):
    r = int.from_bytes(key[:16], 'little') & 0x0FFFFFFC0FFFFFFC0FFFFFFC0FFFFFFF
    s = int.from_bytes(key[16:32], 'little')
    modulus = (1 << 130) - 5
    accumulator = 0
    for offset in range(0, len(message), 16):
        block = message[offset:offset + 16]
        accumulator = ((accumulator + int.from_bytes(block + b'\x01', 'little')) * r) % modulus
    return ((accumulator + s) & ((1 << 128) - 1)).to_bytes(16, 'little')


def _aead_tag(otk, aad, ciphertext):
    import struct
    pad = lambda data: b'\x00' * (-len(data) % 16)
    mac_data = aad + pad(aad) + ciphertext + pad(ciphertext) + struct.pack('<QQ', len(aad), len(ciphertext))
    return _poly1305(otk, mac_data)


def crypto_chacha20_poly1305_seal(key_handle, nonce_handle, aad_handle, plain_handle, output_handle):
    key, nonce, aad = find(key_handle, 'buffer'), find(nonce_handle, 'buffer'), find(aad_handle, 'buffer')
    plain, out = find(plain_handle, 'buffer'), find(output_handle, 'buffer')
    if None in (key, nonce, aad, plain, out) or len(key) != 32 or len(nonce) != 12:
        return -1
    key, nonce = bytes(key), bytes(nonce)
    otk = _chacha20_block(key, 0, nonce)[:32]
    ciphertext = _chacha20_xor(key, 1, nonce, bytes(plain))
    tag = _aead_tag(otk, bytes(aad), ciphertext)
    out.clear()
    out.extend(ciphertext + tag)
    return 0


def crypto_chacha20_poly1305_open(key_handle, nonce_handle, aad_handle, sealed_handle, output_handle):
    import hmac
    key, nonce, aad = find(key_handle, 'buffer'), find(nonce_handle, 'buffer'), find(aad_handle, 'buffer')
    sealed, out = find(sealed_handle, 'buffer'), find(output_handle, 'buffer')
    if None in (key, nonce, aad, sealed, out) or len(key) != 32 or len(nonce) != 12 or len(sealed) < 16:
        return -1
    key, nonce, sealed = bytes(key), bytes(nonce), bytes(sealed)
    ciphertext, tag = sealed[:-16], sealed[-16:]
    otk = _chacha20_block(key, 0, nonce)[:32]
    if not hmac.compare_digest(_aead_tag(otk, bytes(aad), ciphertext), tag):
        return -2
    out.clear()
    out.extend(_chacha20_xor(key, 1, nonce, ciphertext))
    return 0


def unicode_normalize(input_handle, form, output_handle):
    import unicodedata
    source = find(input_handle, 'buffer')
    out = find(output_handle, 'buffer')
    if source is None or out is None or form not in (0, 1):
        return -1
    try:
        text = bytes(source).decode('utf-8')
    except UnicodeDecodeError:
        return -1
    normalized = unicodedata.normalize('NFC' if form == 0 else 'NFD', text)
    out.clear()
    out.extend(normalized.encode('utf-8'))
    return 0


def project_entries(path_handle, output_handle):
    path = path_text(path_handle)
    out = find(output_handle, 'buffer')
    if path is None or out is None or len(out):
        return failure(OSError(errno.EINVAL, 'invalid project entry buffers'))
    try:
        with os.scandir(path) as entries:
            for entry in entries:
                name = entry.name.encode('utf-8')
                kind = (b'l' if entry.is_symlink() else
                        b'd' if entry.is_dir(follow_symlinks=False) else
                        b'f' if entry.is_file(follow_symlinks=False) else b'o')
                out.extend(kind + name + b'\x00')
        return len(out)
    except (OSError, UnicodeError) as error:
        return failure(error if isinstance(error, OSError) else OSError(errno.EILSEQ, 'invalid UTF-8'))


def project_path_key(input_handle, output_handle):
    import unicodedata
    source = find(input_handle, 'buffer')
    out = find(output_handle, 'buffer')
    if source is None or out is None or len(out):
        return failure(OSError(errno.EINVAL, 'invalid path-key buffers'))
    try:
        text = bytes(source).decode('utf-8')
    except UnicodeDecodeError:
        return -1
    out.extend(unicodedata.normalize('NFC', text).casefold().encode('utf-8'))
    return len(out)


def project_file_sha256(path_handle, output_handle):
    import hashlib
    path = path_text(path_handle)
    out = find(output_handle, 'buffer')
    if path is None or out is None or len(out):
        return failure(OSError(errno.EINVAL, 'invalid sha256 buffers'))
    try:
        digest = hashlib.sha256()
        with Path(path).open('rb') as stream:
            for chunk in iter(lambda: stream.read(65536), b''):
                digest.update(chunk)
        out.extend(digest.digest())
        return 0
    except OSError as error:
        return failure(error)


FUNCTIONS = {
    'wysna_host_monotonic_ms': monotonic_ms,
    'wysna_host_wall_ms': wall_ms,
    'wysna_host_sleep': sleep,
    'wysna_host_stdout_flush': stdout_flush,
    'wysna_host_stdin_read_line': stdin_read_line,
    'wysna_host_stdin_read_all': stdin_read_all,
    'wysna_host_stdin_read_exact': stdin_read_exact,
    'wysna_host_stdout_write_raw': stdout_write_raw,
    'wysna_host_self_executable': self_executable,
    'wysna_host_getcwd': getcwd,
    'wysna_host_project_entries': project_entries,
    'wysna_host_project_path_key': project_path_key,
    'wysna_host_project_file_sha256': project_file_sha256,
    'wysna_host_unicode_normalize': unicode_normalize,
    'wysna_host_last_error': lambda: _error_code,
    'wysna_host_last_error_domain': lambda: _error_domain,
    'wysna_host_borrow': lambda handle: handle,
    'wysna_host_active_handles': lambda: len(_entries),
    'wysna_host_active_bytes': lambda: sum(len(value) for kind, value in _entries.values() if kind == 'buffer'),
    'wysna_host_claim': claim,
    'wysna_host_claim_valid': lambda handle: int(find(handle, 'claim') is not None),
    'wysna_host_buffer_new': lambda: allocate('buffer', bytearray()),
    'wysna_host_buffer_size': buffer_size,
    'wysna_host_buffer_get': buffer_get,
    'wysna_host_buffer_append': buffer_append,
    'wysna_host_close': close,
    'wysna_host_file_open': file_open,
    'wysna_host_file_read': file_read,
    'wysna_host_file_write': file_write,
    'wysna_host_is_open': lambda handle: int(find(handle, 'file') is not None),
    'wysna_host_fs': filesystem,
    'wysna_host_fs_canonical': canonical_path,
    'wysna_host_process_run': process_run,
    'wysna_host_process_capture': process_capture,
    'wysna_host_http_request': http_request,
    'wysna_host_http_async_start': http_async_start,
    'wysna_host_http_async_poll': http_async_poll,
    'wysna_host_http_async_finish': http_async_finish,
    'wysna_host_http_multi_poll': http_multi_poll,
    'wysna_host_http_async_cancel': http_async_cancel,
    'wysna_host_crypto_sha256': crypto_sha256,
    'wysna_host_crypto_hmac_sha256': crypto_hmac_sha256,
    'wysna_host_crypto_sha512': crypto_sha512,
    'wysna_host_crypto_hmac_sha512': crypto_hmac_sha512,
    'wysna_host_crypto_md5': crypto_md5,
    'wysna_host_crypto_random_bytes': crypto_random_bytes,
    'wysna_host_crypto_constant_time_compare': crypto_constant_time_compare,
    'wysna_host_crypto_chacha20_poly1305_seal': crypto_chacha20_poly1305_seal,
    'wysna_host_crypto_chacha20_poly1305_open': crypto_chacha20_poly1305_open,
}
