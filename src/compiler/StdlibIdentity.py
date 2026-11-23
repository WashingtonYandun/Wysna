"""Native intrinsics are tied to declarations in the configured standard library."""
from pathlib import Path

from common.Paths import get_repo_root


def stdlib_declaration(function, module, name, arity):
    source = getattr(function, 'source_file', None)
    if source is None:
        source = getattr(getattr(function, 'source', None), 'file', None)
    if source is None or Path(source).resolve() != (Path(get_repo_root()) / 'stdlib' / (module + '.wysna')).resolve():
        return False
    declaration = getattr(function, 'declaration_name', '') or getattr(function, 'source_name', None) or function.name
    return declaration == name and len(getattr(function, 'params', ())) == arity
