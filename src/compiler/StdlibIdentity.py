"""Native intrinsics are tied to declarations in the configured standard library."""
from pathlib import Path

from common.Paths import get_repo_root


def stdlib_declaration(function, module, name, arity):
    declaration = getattr(function, 'declaration_name', '') or getattr(function, 'source_name', None) or function.name
    if declaration != name or len(getattr(function, 'params', ())) != arity:
        return False
    source = getattr(function, 'source_file', None)
    if source is None:
        source = getattr(getattr(function, 'source', None), 'file', None)
    return source is not None and Path(source).resolve() == (Path(get_repo_root()) / 'stdlib' / (module + '.wysna')).resolve()


_WORKER_INTRINSICS = {
    'parallel_map': ('algorithms', 2), 'parallel_filter': ('algorithms', 2),
    'parallel_batch': ('algorithms', 3), 'parallel_reduce': ('algorithms', 4),
    '_spawn_handle': ('async', 1), '_take_task_value': ('async', 2),
}


def worker_intrinsic(function):
    name = getattr(function, 'source_name', None) or function.name
    identity = _WORKER_INTRINSICS.get(name)
    if identity and stdlib_declaration(function, identity[0], name, identity[1]):
        return name
    return None
