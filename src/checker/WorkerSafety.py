"""Closed worker transfer and transitive callback checks over linked, typed syntax."""
from pathlib import Path

from checker import WysnaType as T
from common.Paths import get_repo_root
from compiler.StdlibIdentity import stdlib_declaration
from error.Error import TypeError
from node import NodeType as A


PURE_BUILTINS = frozenset(('len', 'int', 'float', 'str', 'bytes', 'ord', 'chr',
    'slice', 'join_strings', 'enumerate', 'dict_items', 'u8', 'assert', 'contract_assert',
    '__wysna_contract_assert', '__wysna_json_parse', '__wysna_json_parse_int',
    '__wysna_json_get_int', '__wysna_json_get_bool', '__wysna_json_get_str',
    '__wysna_json_get_float', '__wysna_json_quote_str', 'Some', 'Ok', 'Err'))
WORKER_ARGUMENTS = {'parallel_map': 1, 'parallel_filter': 1, 'parallel_batch': 2,
                    'parallel_reduce': 2, 'spawn': 0, '_spawn_handle': 0,
                    'run_group': 4, '_run_task_group': 4}
ASYNC_WORKER_ENTRIES = frozenset(('spawn', '_spawn_handle', 'run_group', '_run_task_group'))


def syntax_children(node):
    # Closure conversion retains original syntax for inspection only.
    if isinstance(node, A.LambdaExpr):
        return [node.environment]
    result = []
    for value in vars(node).values():
        if isinstance(value, A.Node):
            result.append(value)
        elif isinstance(value, list):
            result.extend(child for child in value if isinstance(child, A.Node))
    return result


def walk(node, parent=None):
    yield node, parent
    for child in syntax_children(node):
        yield from walk(child, node)


def nontransferable(type_, path, seen=None):
    seen = set() if seen is None else seen
    if id(type_) in seen:
        return None
    seen.add(id(type_))
    children = []
    if isinstance(type_, T.PrimitiveType):
        return None if type_.name in ('int', 'u8', 'float', 'bool', 'str', 'unit', 'none') else (path, type_)
    if isinstance(type_, T.OptionalType):
        children = [('value', type_.inner)]
    elif isinstance(type_, T.ListType):
        children = [('item', type_.element)]
    elif isinstance(type_, T.DictType):
        children = [('key', type_.key), ('value', type_.value)]
    elif isinstance(type_, T.TupleType):
        children = [(str(i), child) for i, child in enumerate(type_.elements)]
    elif isinstance(type_, T.StructType):
        children = list(type_.fields.items())
    elif isinstance(type_, T.EnumType):
        for name, payloads in type_.variant_payloads.items():
            children.extend((name + '.' + str(i), child) for i, child in enumerate(payloads))
    elif isinstance(type_, T.ResultType):
        children = [('ok', type_.ok), ('error', type_.err)]
    elif isinstance(type_, T.ErrorUnionType):
        children = [(str(i), child) for i, child in enumerate(type_.alternatives)]
    else:
        return path, type_
    for name, child in children:
        failure = nontransferable(child, path + '.' + name, seen)
        if failure:
            return failure
    return None


class WorkerSafety:
    def __init__(self, program):
        self.program = program
        self.functions = {node.name: node for node in program.body if isinstance(node, A.FnDecl)}
        self.structs = {node.name: node for node in program.body if isinstance(node, A.StructDecl)}
        self.aliases = {}
        self.closures = {}
        self.stdlib = (Path(get_repo_root()) / 'stdlib').resolve()
        for node, _ in walk(program):
            if isinstance(node, A.LambdaExpr):
                self.closures[node.function_name] = node.environment.inferred_type
            if isinstance(node, A.LetDecl) and not node.is_mut:
                key = (getattr(node, 'source_file', ''), node.line, node.col, node.name)
                self.aliases[key] = node.initializer

    def resolve(self, expression, seen=None):
        seen = set() if seen is None else seen
        if id(expression) in seen:
            return None
        seen.add(id(expression))
        if isinstance(expression, A.LambdaExpr):
            return self.functions.get(expression.function_name)
        if isinstance(expression, A.IdentExpr):
            symbol = getattr(expression, 'resolved_symbol', None)
            if symbol is not None and symbol.kind in ('var', 'param'):
                if symbol.is_mut:
                    return None
                key = (getattr(symbol, 'file', ''), symbol.line, symbol.col, symbol.name)
                alias = self.aliases.get(key)
                return self.resolve(alias, seen) if alias is not None else None
            function = self.functions.get(getattr(expression, 'function_reference', expression.name))
            if function is None and symbol is not None and symbol.kind == 'fn':
                function = getattr(symbol.type, 'ast_decl', None)
            return function
        return None

    def entry(self, function):
        if function is None:
            return None
        name = getattr(function, 'source_name', None) or function.name
        module = 'async' if name in ASYNC_WORKER_ENTRIES else 'algorithms'
        if name not in WORKER_ARGUMENTS:
            return None
        path = Path(getattr(function, 'source_file', '')).resolve()
        if path != self.stdlib / (module + '.wysna'):
            return None
        return name

    def reject(self, code, node, boundary, message, **data):
        error = TypeError(message, code=code, file=getattr(node, 'source_file', None),
                          line=node.line, col=node.col)
        error.with_data(kind='worker_transfer' if code == 'W2014' else 'worker_effect',
                        boundary_file=getattr(boundary, 'source_file', ''),
                        boundary_line=boundary.line, boundary_col=boundary.col, **data)
        error.with_related('Worker boundary is here', file=getattr(boundary, 'source_file', None),
                           line=boundary.line, col=boundary.col)
        raise error

    def transfer(self, type_, path, node, boundary):
        failure = nontransferable(type_, path)
        if failure:
            at, invalid = failure
            self.reject('W2014', node, boundary, 'Worker transfer rejects ' + at + ' of type ' + str(invalid),
                        path=at, value_type=str(invalid))

    def callback(self, expression, boundary):
        function = self.resolve(expression)
        if function is None:
            self.reject('W2015', expression, boundary, 'Worker callback must resolve to a named function or immutable closure',
                        reason='unresolved_callback')
        signature = getattr(expression, 'inferred_type', None)
        if not isinstance(signature, T.FunctionType):
            self.reject('W2015', expression, boundary, 'Worker callback signature is unavailable', reason='unresolved_callback')
        for i, type_ in enumerate(signature.params):
            self.transfer(type_, 'argument.' + str(i), expression, boundary)
        self.transfer(signature.ret, 'result', expression, boundary)
        if isinstance(expression, A.LambdaExpr):
            self.transfer(expression.environment.inferred_type, 'capture', expression, boundary)
        elif function.name.startswith('_WYSNA_Closure_'):
            self.transfer(self.closures.get(function.name), 'capture', expression, boundary)
        self.effects(function, boundary, set())

    def effects(self, function, boundary, seen):
        identity = ('struct', function.name) if isinstance(function, A.StructDecl) else function.name
        if identity in seen:
            return
        seen.add(identity)
        if stdlib_declaration(function, 'async', '_take_task_value', 2):
            self.reject('W2015', function, boundary, 'Worker callback cannot access the shared task registry', reason='scheduler_effect')
        if getattr(function, 'extern_abi', None) or getattr(function, 'is_async', False):
            self.reject('W2015', function, boundary, 'Worker callback reaches FFI or async code', reason='external_effect')
        for node, parent in walk(function):
            if node is function:
                continue
            if isinstance(node, (A.ImportDecl, A.UseDecl, A.ExternFnDecl)):
                self.reject('W2015', node, boundary, 'Worker callback cannot load modules or foreign bindings', reason='external_effect')
            if isinstance(node, A.StructInitExpr) and node.struct_name in self.structs:
                self.effects(self.structs[node.struct_name], boundary, seen)
            type_ = getattr(node, 'inferred_type', None)
            if type_ is not None and not isinstance(type_, T.FunctionType):
                self.transfer(type_, 'local', node, boundary)
            if isinstance(node, A.AwaitExpr):
                self.reject('W2015', node, boundary, 'Worker callback cannot await shared scheduler state', reason='scheduler_effect')
            if isinstance(node, A.IdentExpr):
                symbol = getattr(node, 'resolved_symbol', None)
                if symbol is not None and symbol.kind == 'var' and getattr(symbol, 'global_binding', False):
                    self.reject('W2015', node, boundary, 'Worker callback reads an uncaptured global: ' + node.name,
                                reason='global_access', symbol=node.name)
            if not isinstance(node, A.CallExpr):
                continue
            target = self.resolve(node.callee)
            if target is not None:
                if self.entry(target):
                    self.reject('W2015', node, boundary, 'Worker callbacks cannot dispatch nested workers', reason='scheduler_effect')
                self.effects(target, boundary, seen)
            elif isinstance(node.callee, A.IdentExpr):
                symbol = getattr(node.callee, 'resolved_symbol', None)
                if symbol is None and node.callee.name in ('Some', 'Ok', 'Err') and isinstance(type_, (T.EnumType, T.ResultType, T.OptionalType)):
                    continue
                if symbol is None or symbol.kind != 'builtin' or node.callee.name not in PURE_BUILTINS:
                    self.reject('W2015', node, boundary, 'Worker callback calls an unverified or effectful function: ' + node.callee.name,
                                reason='unverified_call', symbol=node.callee.name)
            elif isinstance(node.callee, A.MemberExpr) and not getattr(node, 'indirect_call', False):
                receiver = getattr(node.callee.object, 'inferred_type', None)
                self.transfer(receiver, 'receiver', node.callee.object, boundary)
            else:
                self.reject('W2015', node, boundary, 'Worker callback has an unresolved indirect call', reason='unverified_call')

    def check(self):
        for owner in self.program.body:
            # These standard wrappers carry their callback to the native boundary unchanged.
            if isinstance(owner, A.FnDecl) and self.entry(owner):
                continue
            for node, parent in walk(owner):
                if isinstance(node, A.CallExpr):
                    name = self.entry(self.resolve(node.callee))
                    if name:
                        self.callback(node.args[WORKER_ARGUMENTS[name]], node)
                elif isinstance(node, A.IdentExpr) and self.entry(self.resolve(node)):
                    if not isinstance(parent, A.CallExpr) or parent.callee is not node:
                        self.reject('W2015', node, node, 'Worker entry points require a direct checked call', reason='escaping_worker_entry')


def check_worker_boundaries(program):
    WorkerSafety(program).check()
