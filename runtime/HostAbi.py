"""Reference implementation of the internal, checked scalar/opaque resource ABI."""
import atexit
import errno
import os
from pathlib import Path
import signal
import subprocess
from urllib.parse import urlsplit

_entries = {}
_next_id = 1
_error_code = 0
_error_domain = 0
_process_jobs = {}


def failure(error):
    global _error_code, _error_domain
    _error_code = getattr(error, 'winerror', None) or getattr(error, 'errno', None) or errno.EIO
    _error_domain = 2 if getattr(error, 'winerror', None) else 1
    return -1


def allocate(kind, value):
    global _next_id
    handle = _next_id
    _next_id += 1
    _entries[handle] = (kind, value)
    return handle


def find(handle, kind):
    entry = _entries.get(handle)
    return entry[1] if entry and entry[0] == kind else None


def close(handle):
    entry = _entries.pop(handle, None)
    if entry and entry[0] == 'file':
        try:
            entry[1].close()
        except OSError as error:
            return failure(error)
    return 0


def cleanup():
    for handle in list(_entries):
        close(handle)


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
    path = path_text(handle)
    if path is None or mode not in (0, 1, 2):
        failure(OSError(errno.EINVAL, 'invalid path or mode'))
        return 0
    try:
        return allocate('file', open(path, ('rb', 'wb', 'ab')[mode]))
    except OSError as error:
        failure(error)
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
    file, buffer = find(file_handle, 'file'), find(buffer_handle, 'buffer')
    if file is None or buffer is None:
        return failure(OSError(errno.EBADF, 'closed handle'))
    if not 0 < count <= 16777216:
        return failure(OSError(errno.EINVAL, 'invalid read bound'))
    try:
        buffer[:] = file.read(count)
        return len(buffer)
    except (OSError, ValueError) as error:
        return failure(error)


def file_write(file_handle, buffer_handle):
    file, buffer = find(file_handle, 'file'), find(buffer_handle, 'buffer')
    if file is None or buffer is None:
        return failure(OSError(errno.EBADF, 'closed handle'))
    try:
        written = file.write(buffer)
        if written != len(buffer):
            return failure(OSError(errno.EIO, 'short write'))
        file.flush()
        return 0
    except (OSError, ValueError) as error:
        return failure(error)


def filesystem(first, second, operation):
    text = path_text(first)
    other = path_text(second) if operation == 4 else None
    if text is None or (operation == 4 and other is None):
        return failure(OSError(errno.EINVAL, 'invalid path'))
    path = Path(text)
    try:
        if operation in (0, 1):
            try:
                status = path.stat()
            except FileNotFoundError:
                return 0 if operation == 0 else -1
            return 1 if operation == 0 else status.st_size
        if operation == 2:
            path.mkdir()
        elif operation == 3:
            path.rmdir() if path.is_dir() else path.unlink()
        elif operation == 4:
            path.rename(other)
        else:
            return -1
        return 0
    except (OSError, ValueError) as error:
        return failure(error)


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
        import pycurl
    except ImportError:
        return _http_failure(_HTTP_UNAVAILABLE, getattr(errno, 'ENOSYS', errno.EIO))
    try:
        if int(pycurl.version_info()[2]) < 0x080300:
            return _http_failure(_HTTP_UNAVAILABLE, getattr(errno, 'EPROTONOSUPPORT', errno.EIO))
    except (AttributeError, IndexError, TypeError, ValueError):
        return _http_failure(_HTTP_UNAVAILABLE, getattr(errno, 'EPROTONOSUPPORT', errno.EIO))

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
            return _http_failure(callback_error, code)
        if not 100 <= status <= 599:
            return _http_failure(_HTTP_INVALID_RESPONSE, errno.EPROTO)
        response_headers[:] = response_headers_output
        response_body[:] = response_body_output
        return status
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
        return _http_failure(kind, curl_code)
    except (TypeError, ValueError, UnicodeError):
        return _http_failure(_HTTP_INVALID_REQUEST, errno.EINVAL)
    except OSError as error:
        return _http_failure(_HTTP_TRANSPORT, getattr(error, 'errno', None) or errno.EIO)
    finally:
        if curl is not None:
            curl.close()
    try:
        process.kill()
    except OSError:
        pass


def process_run(executable_handle, arguments_handle, timeout_ms, *, cwd=None, env=None):
    global _error_code, _error_domain
    _error_code = 0
    _error_domain = 0
    executable_bytes = find(executable_handle, 'buffer')
    encoded_arguments = find(arguments_handle, 'buffer')
    if executable_bytes is None or encoded_arguments is None or not 1 <= timeout_ms <= 600000:
        return failure(OSError(errno.EINVAL, 'invalid process request'))
    if not executable_bytes or b'\x00' in executable_bytes:
        return failure(OSError(errno.EINVAL, 'invalid executable path'))
    try:
        executable = executable_bytes.decode('utf-8')
        arguments = []
        offset = 0
        while offset < len(encoded_arguments):
            if len(arguments) >= 256 or offset + 4 > len(encoded_arguments):
                return failure(OSError(errno.E2BIG, 'invalid argument block'))
            length = int.from_bytes(encoded_arguments[offset:offset + 4], 'big')
            offset += 4
            if length > 1048576 or offset + length > len(encoded_arguments):
                return failure(OSError(errno.E2BIG, 'argument exceeds its limit'))
            raw_argument = encoded_arguments[offset:offset + length]
            offset += length
            if b'\x00' in raw_argument:
                return failure(OSError(errno.EINVAL, 'argument contains NUL'))
            arguments.append(raw_argument.decode('utf-8'))
            if offset > 16777216:
                return failure(OSError(errno.E2BIG, 'argument block exceeds its limit'))
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
                return 128 + abs(exit_code) if exit_code < 0 else exit_code
            except subprocess.TimeoutExpired:
                terminate_process_tree(process, force_group=True)
                process.wait()
                return failure(TimeoutError(errno.ETIMEDOUT, 'process timed out'))
        finally:
            release_process_tree(process)
    except (OSError, UnicodeError, ValueError) as error:
        return failure(error)


FUNCTIONS = {
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
    'wysna_host_process_run': process_run,
    'wysna_host_http_request': http_request,
}
