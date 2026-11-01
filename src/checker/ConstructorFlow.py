"""Definite initialization and escape checks for class constructors."""
from node import NodeType as A
from error.Error import TypeError


def check_constructor(declaration, class_type, method, file, match_is_exhaustive):
    required = {field.name for field in declaration.fields}

    def fail(node, message):
        raise TypeError(message, file=file, line=node.line, col=node.col, code='W3004')

    def is_self(node):
        return isinstance(node, A.ThisExpr) or isinstance(node, A.IdentExpr) and node.name == 'self'

    def super_call(node):
        return (isinstance(node, A.CallExpr) and isinstance(node.callee, A.MemberExpr)
                and isinstance(node.callee.object, A.SuperExpr) and node.callee.property == 'init')

    statements = list(method.body.statements)
    if method.body.tail_expr is not None:
        statements.append(A.ExprStmt(method.body.tail_expr))
    parent = class_type.superclass
    needs_super = False
    while parent is not None:
        needs_super = needs_super or parent.init_type is not None
        parent = parent.superclass
    first = statements[0].expr if statements and isinstance(statements[0], A.ExprStmt) else None
    if needs_super and not super_call(first):
        fail(method, "super.init must be the first statement in a derived constructor")
    calls = []

    def collect(node):
        if super_call(node):
            calls.append(node)
        if isinstance(node, A.Node):
            for child in vars(node).values():
                if isinstance(child, A.Node):
                    collect(child)
                elif isinstance(child, list):
                    for item in child:
                        if isinstance(item, A.Node):
                            collect(item)
    collect(method.body)
    if len(calls) > 1 or calls and not super_call(first):
        fail(method, "super.init must occur exactly once, as the first statement")

    def complete(node, initialized):
        missing = required - initialized
        if missing:
            fail(node, 'Constructor leaves uninitialized fields: ' + ', '.join(sorted(missing)))

    def constant_bool(node):
        if isinstance(node, A.BoolLiteral):
            return node.value
        return None

    def expression(node, initialized):
        if node is None or initialized is None:
            return initialized
        if isinstance(node, A.AssignmentExpr):
            target = node.target
            if isinstance(target, A.MemberExpr) and is_self(target.object):
                initialized = expression(node.value, initialized)
                if initialized is not None:
                    initialized = initialized | {target.property}
                return initialized
        if isinstance(node, A.BinaryExpr) and node.op in ('and', 'or'):
            after_left = expression(node.left, initialized)
            if after_left is None:
                return None
            left_constant = constant_bool(node.left)
            skips_right = (node.op == 'and' and left_constant is False) or (
                node.op == 'or' and left_constant is True)
            executes_right = (node.op == 'and' and left_constant is True) or (
                node.op == 'or' and left_constant is False)
            if skips_right:
                return after_left
            after_right = expression(node.right, set(after_left))
            if executes_right:
                return after_right
            if after_right is None:
                return after_left
            return after_left & after_right
        if isinstance(node, A.CoalesceExpr):
            after_left = expression(node.left, initialized)
            if after_left is None:
                return None
            after_right = expression(node.right, set(after_left))
            if isinstance(node.left, A.NoneLiteral):
                return after_right
            if after_right is None:
                return after_left
            return after_left & after_right
        if isinstance(node, A.MemberExpr) and is_self(node.object):
            if node.property in required and node.property not in initialized:
                fail(node, "Read before initialization: this." + node.property)
            if node.property in class_type.methods:
                fail(node, "Virtual method calls are forbidden during construction")
            return initialized
        if is_self(node):
            fail(node, "The receiver cannot escape during construction")
        if super_call(node):
            for argument in node.args:
                initialized = expression(argument, initialized)
            return initialized
        if isinstance(node, A.IfExpr):
            branches = []
            condition = node.condition
            branch = node.then_branch
            fallthrough = initialized
            alternatives = [(condition, branch)] + [
                (alternative.condition, alternative.body) for alternative in node.elif_branches
            ]
            for condition, branch in alternatives:
                if fallthrough is None:
                    break
                after_condition = expression(condition, fallthrough)
                if after_condition is None:
                    fallthrough = None
                    break
                known = constant_bool(condition)
                if known is not False:
                    branches.append(block(branch, set(after_condition)))
                if known is True:
                    fallthrough = None
                    break
                fallthrough = after_condition
            if fallthrough is not None:
                if node.else_branch:
                    branches.append(block(node.else_branch, set(fallthrough)))
                else:
                    branches.append(fallthrough)
            live = [branch for branch in branches if branch is not None]
            return set.intersection(*live) if live else None
        if isinstance(node, A.MatchExpr):
            after_subject = expression(node.subject, initialized)
            if after_subject is None:
                return None
            branches = []
            for arm in node.arms:
                if arm.guard is not None:
                    # A guard may be skipped because its pattern did not match, and may
                    # fail and continue to another arm. Its writes are not definite.
                    expression(arm.guard, set(after_subject))
                branches.append(block(arm.body, set(after_subject)))
            if not match_is_exhaustive(node):
                branches.append(after_subject)
            live = [branch for branch in branches if branch is not None]
            return set.intersection(*live) if live else None
        if isinstance(node, A.Node):
            for child in vars(node).values():
                if isinstance(child, A.Node):
                    initialized = expression(child, initialized)
                elif isinstance(child, list):
                    for item in child:
                        if isinstance(item, A.Node):
                            initialized = expression(item, initialized)
        return initialized

    def block(node, initialized):
        if node is None:
            return initialized
        for statement in node.statements:
            if initialized is None:
                break
            if isinstance(statement, A.ReturnStmt):
                returned = expression(statement.value, initialized)
                complete(statement, returned if returned is not None else initialized)
                initialized = None
            elif isinstance(statement, (A.ForStmt, A.WhileStmt)):
                after_condition = expression(
                    getattr(statement, 'condition', getattr(statement, 'iterable', None)), initialized
                )
                if after_condition is None:
                    initialized = None
                else:
                    block(statement.body, set(after_condition))
                    initialized = after_condition
            else:
                initialized = expression(getattr(statement, 'expr', getattr(statement, 'initializer', None)), initialized)
        return expression(node.tail_expr, initialized)

    final = block(method.body, set())
    if final is not None:
        complete(method, final)
