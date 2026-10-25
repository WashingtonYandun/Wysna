"""Execute explicitly declared scalar/opaque C functions in the reference engine."""
import ctypes
import ctypes.util
import os
import sys
from error.Error import WysnaRuntimeError
from runtime.Values import WysnaInt, WysnaFloat, WysnaBool, WysnaUnit, WysnaPointer

C_TYPES = {'int': ctypes.c_int64, 'int64': ctypes.c_int64, 'int32': ctypes.c_int32,
           'float': ctypes.c_double, 'double': ctypes.c_double, 'bool': ctypes.c_bool,
           'opaque': ctypes.c_void_p, 'unit': None}


def bind_foreign(declaration, libraries=(), execution_context=None):
    signature = [p.type_ann.name for p in declaration.params]
    returned = declaration.return_type.name
    name = getattr(declaration, 'source_name', declaration.name)
    from runtime.HostAbi import FUNCTIONS
    internal = FUNCTIONS.get(name)
    handles = [] if internal else ([ctypes.CDLL(None)] if os.name != 'nt' else [])
    for library in ([] if internal else list(libraries) + ([] if os.name == 'nt' else ['m'])):
        path = library if os.path.isfile(library) else ctypes.util.find_library(library)
        if path:
            try:
                abs_path = os.path.abspath(path)
                kwargs = {'winmode': 0} if (os.name == 'nt' and sys.version_info >= (3, 8)) else {}
                handles.append(ctypes.CDLL(abs_path, **kwargs))
            except Exception:
                try:
                    kwargs = {'winmode': 0} if (os.name == 'nt' and sys.version_info >= (3, 8)) else {}
                    handles.append(ctypes.CDLL(path, **kwargs))
                except Exception:
                    pass
    function = internal or next((getattr(handle, name) for handle in handles if hasattr(handle, name)), None)
    if function is None:
        raise WysnaRuntimeError("Unresolved C symbol: " + name, code='W4003')
    if name == 'wysna_host_process_run':
        from runtime.HostAbi import process_run
        function = lambda executable, arguments, timeout_ms: process_run(
            executable, arguments, timeout_ms,
            cwd=execution_context.cwd if execution_context is not None else None,
            env=execution_context.env if execution_context is not None else None,
        )
    if not internal:
        function.argtypes = [C_TYPES[t] for t in signature]
        function.restype = C_TYPES[returned]

    def invoke(arguments):
        raw = []
        for argument, type_ in zip(arguments, signature):
            value = argument.address if type_ == 'opaque' else argument.value
            if type_ == 'int32' and not -2147483648 <= value <= 2147483647:
                raise WysnaRuntimeError('int32 FFI argument out of range', code='W4003')
            raw.append(value)
        result = function(*raw)
        if returned == 'unit':
            return WysnaUnit()
        constructor = {'int': WysnaInt, 'int32': WysnaInt, 'int64': WysnaInt, 'float': WysnaFloat,
                       'double': WysnaFloat, 'bool': WysnaBool, 'opaque': WysnaPointer}[returned]
        if name in ('wysna_host_buffer_new', 'wysna_host_file_open'):
            from runtime.HostAbi import close
            return WysnaPointer(result, close=close)
        return constructor(result)
    return invoke
