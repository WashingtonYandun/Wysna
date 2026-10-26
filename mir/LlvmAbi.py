"""Versioned runtime operation codes shared with the fixed C bridge."""
from pathlib import Path
import re
from common.Paths import get_repo_root
from mir.Verifier import RUNTIME
from checker.TypeInterner import TYPE_UNIT, TYPE_BOOL, TYPE_INT, TYPE_FLOAT


def operation_codes():
    source = (Path(get_repo_root()) / 'runtime/wysna_llvm_operations.def').read_text(encoding='utf-8')
    rows = re.findall(r'^WYSNA_LLVM_OPERATION\((\w+), (\d+),', source, re.MULTILINE)
    codes = {name: int(code) for name, code in rows}
    if set(codes) != RUNTIME or len(set(codes.values())) != len(codes):
        raise RuntimeError('LLVM runtime operation table does not match the verified MIR contract')
    return codes


OPERATION_CODES = operation_codes()
SCALAR_TYPES = {TYPE_BOOL: 'i1', TYPE_INT: 'i64', TYPE_FLOAT: 'double'}


def is_managed(type_id):
    return type_id != TYPE_UNIT and type_id not in SCALAR_TYPES
