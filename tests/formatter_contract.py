"""Shared formatter corpus and structural preservation contract."""
from pathlib import Path

from formatter.Formatter import Formatter
from parser.Parser import Parser


ROOT = Path(__file__).resolve().parents[1]


def formatter_paths():
    patterns = ('tests/formatter/*.wysna', 'tests/runtime/*.wysna', 'examples/**/*.wysna',
                'selfhost/**/*.wysna', 'stdlib/**/*.wysna')
    return sorted({path for pattern in patterns for path in ROOT.glob(pattern)})


def _callee_dict(class_name):
    parts = class_name.split('.')
    node = {'kind': 'IdentExpr', 'name': parts[0]}
    for part in parts[1:]:
        node = {'kind': 'MemberExpr', 'object': node, 'property': part}
    return node


def structural_ast(value):
    if type(value).__name__ == 'NewExpr':
        normalized = {
            'kind': 'CallExpr',
            'callee': _callee_dict(value.class_name),
            'args': value.args,
        }
        if getattr(value, 'type_args', None):
            normalized['type_args'] = value.type_args
        return structural_ast(normalized)
    if hasattr(value, 'to_dict'):
        return structural_ast(value.to_dict())
    if isinstance(value, dict):
        if value.get('kind') == 'NewExpr':
            normalized = {
                'kind': 'CallExpr',
                'callee': _callee_dict(value['class_name']),
                'args': value.get('args', []),
            }
            if value.get('type_args'):
                normalized['type_args'] = value['type_args']
            return structural_ast(normalized)
        return {key: structural_ast(item) for key, item in value.items() if key not in ('line', 'col')}
    if isinstance(value, (list, tuple)):
        return [structural_ast(item) for item in value]
    if isinstance(value, float):
        return ('float', value.hex())
    return value


def verify_formatting(source, file='<stdin>'):
    original = Parser(file=file).parse(source)
    formatted = Formatter().format(original)
    reparsed = Parser(file=file).parse(formatted)
    assert structural_ast(original) == structural_ast(reparsed), 'formatter changed the program AST'
    assert formatted == Formatter().format(reparsed), 'formatter is not idempotent'
    return formatted
