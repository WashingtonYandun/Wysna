"""Convert lexical lambdas into explicit snapshot environments and free functions."""
from node import NodeType as A
from node.NodeType import clone_ast
from checker.WysnaType import FunctionType, StructType, ResultType, UnitType
from checker.SymbolTable import Scope, Symbol
from error.Error import TypeError


def convert_lambda(checker, expression):
    if hasattr(expression, 'function_name') and hasattr(expression, 'inferred_type'):
        checker._check_expr(expression.environment)
        return expression.inferred_type
    expected = checker.expected_type if isinstance(checker.expected_type, FunctionType) else None
    if expected and len(expected.params) != len(expression.params):
        raise TypeError('Lambda arity does not match expected signature', file=checker.file,
                        line=expression.line, col=expression.col, code='W2005')
    parameter_types = []
    for index, parameter in enumerate(expression.params):
        if parameter.type_ann is not None:
            type_ = checker._resolve_type_node(parameter.type_ann)
        elif expected:
            type_ = expected.params[index]
            parameter.type_ann = checker._type_to_type_node(type_)
        else:
            raise TypeError('Lambda parameters require annotations or a function type context', file=checker.file,
                            line=parameter.line, col=parameter.col, code='W2001')
        parameter_types.append(type_)
    returned = checker._resolve_type_node(expression.return_type) if expression.return_type else expected.ret if expected else None
    outer = checker.current_scope
    scope = Scope(parent=outer, is_function_scope=True)
    for parameter, type_ in zip(expression.params, parameter_types):
        scope.define_symbol(Symbol(parameter.name, type_, kind='param'), file=checker.file)
    previous_expected = checker.expected_type
    previous_ret, previous_depth = checker.current_fn_ret_type, checker.loop_depth
    checker.current_scope, checker.current_fn_ret_type, checker.loop_depth = scope, returned, 0
    checker.expected_type = returned
    try:
        discard = returned == UnitType or isinstance(returned, ResultType) and returned.ok == UnitType
        inferred = checker._check_block(expression.body, is_statement_context=discard)
        returned = returned or inferred
        if returned is None:
            raise TypeError('Block lambda needs an explicit return type', file=checker.file,
                            line=expression.line, col=expression.col, code='W2001')
    finally:
        checker.current_scope, checker.current_fn_ret_type, checker.loop_depth = outer, previous_ret, previous_depth
        checker.expected_type = previous_expected
    signature = FunctionType(parameter_types, returned)
    if expected and signature != expected:
        raise TypeError('Lambda signature must match exactly', file=checker.file,
                        line=expression.line, col=expression.col, code='W2001')
    captures = {}
    # Preserve binding identities while copying syntax for the generated function.
    outer_symbols = {}
    current = outer
    while current is not None:
        for symbol in current.symbols.values():
            outer_symbols[id(symbol)] = symbol
        current = current.parent
    body = clone_ast(expression.body, dict(outer_symbols))
    # The counter lives on the compilation session, not on the checker. A checker is
    # created per module, so a per-checker counter restarted at 1 in every module and
    # two importing modules each holding one lambda both produced `_WYSNA_Closure_1_Env`.
    # The linked program then had two structs under that name with different fields,
    # which the interpreter reported as an uncaught KeyError and the C backend as an
    # unresolved HIR field. Keeping one counter per compilation makes it unique.
    session = getattr(checker, 'session', None)
    if session is not None and hasattr(session, 'closure_counter'):
        session.closure_counter += 1
        serial = session.closure_counter
    else:
        checker.closure_counter += 1
        serial = checker.closure_counter
    name = '_WYSNA_Closure_' + str(serial)
    environment_name = name + '_Env'
    environment_parameter = '__wysna_environment'

    def rewrite(node, bound):
        if isinstance(node, A.LambdaExpr) and hasattr(node, 'environment'):
            node.environment = rewrite(node.environment, bound)
            return node
        if isinstance(node, A.ThisExpr) or isinstance(node, A.SuperExpr):
            raise TypeError('Capture this through an explicit local binding', file=checker.file,
                            line=node.line, col=node.col, code='W2001')
        if isinstance(node, A.IdentExpr) and node.name not in bound:
            symbol = getattr(node, 'resolved_symbol', None)
            if symbol and id(symbol) in outer_symbols and symbol.kind in ('var', 'param'):
                captures[node.name] = symbol.type
                return A.MemberExpr(A.IdentExpr(environment_parameter, node.line, node.col),
                                    node.name, node.line, node.col)
        if isinstance(node, A.Block):
            local = set(bound)
            for index, statement in enumerate(node.statements):
                node.statements[index] = rewrite(statement, local)
                if isinstance(statement, A.LetDecl):
                    local.add(statement.name)
            node.tail_expr = rewrite(node.tail_expr, local)
            return node
        if isinstance(node, A.ForStmt):
            node.iterable = rewrite(node.iterable, bound)
            names = pattern_names(node.pattern)
            node.body = rewrite(node.body, bound | names)
            return node
        if isinstance(node, A.ElseErrExpr):
            node.operand = rewrite(node.operand, bound)
            local = bound | ({node.err_name} if node.err_name else set())
            node.handler = rewrite(node.handler, local)
            return node
        if isinstance(node, A.MatchArm):
            local = bound | pattern_names(node.pattern)
            node.guard = rewrite(node.guard, local)
            node.body = rewrite(node.body, local)
            return node
        if isinstance(node, A.Node):
            for field, child in list(vars(node).items()):
                if isinstance(child, A.Node):
                    setattr(node, field, rewrite(child, bound))
                elif isinstance(child, list):
                    setattr(node, field, [rewrite(item, bound) for item in child])
        return node

    def pattern_names(pattern):
        if isinstance(pattern, A.IdentifierPattern):
            return {pattern.name}
        if isinstance(pattern, A.ErrorTypePattern):
            return pattern_names(pattern.binding)
        names = set()
        for field in ('elements', 'args'):
            for child in getattr(pattern, field, []):
                names |= pattern_names(child)
        return names

    body = rewrite(body, {p.name for p in expression.params})
    environment_type = StructType(environment_name, captures)
    checker.global_scope.define_type(environment_name, environment_type, file=checker.file)
    checker.interner.from_type(environment_type)
    fields = [A.StructField(key, checker._type_to_type_node(type_)) for key, type_ in captures.items()]
    environment_decl = A.StructDecl(environment_name, fields)
    # Resolve field syntax metadata before lowering synthetic declarations.
    for field in fields:
        checker._resolve_type_node(field.type_ann)
    checker.monomorphized_struct_decls.append(environment_decl)
    environment_annotation = checker._type_to_type_node(environment_type)
    checker._resolve_type_node(environment_annotation)
    parameters = [A.Param(environment_parameter, environment_annotation)] + expression.params
    for p in expression.params:
        if p.type_ann is not None:
            checker._resolve_type_node(p.type_ann)
    return_annotation = checker._type_to_type_node(returned)
    checker._resolve_type_node(return_annotation)
    declaration = A.FnDecl(name, parameters, return_annotation, body)
    checker.global_scope.define_symbol(Symbol(name, FunctionType([environment_type] + parameter_types, returned), kind='fn'), file=checker.file)
    checker.monomorphized_fn_decls.append(declaration)
    checker.current_scope = checker.global_scope
    try:
        checker._check_fn_decl(declaration)
    finally:
        checker.current_scope = outer
    initializers = []
    for key in captures:
        value = A.IdentExpr(key, expression.line, expression.col)
        checker._check_expr(value)
        initializers.append(A.FieldInit(key, value))
    environment = A.StructInitExpr(environment_name, initializers, line=expression.line, col=expression.col)
    checker._check_expr(environment)
    expression.function_name = name
    expression.environment = environment
    return signature
