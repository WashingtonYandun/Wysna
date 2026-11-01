"""Compositional control-flow exits for both checked syntax and typed HIR.

NORMAL is an explicit fallthrough edge, including an unmatched partial match.
Returns in nested function declarations never terminate their enclosing scope.
"""
from node import NodeType as A
from hir import HirNodes as H

NORMAL = 'normal'
RETURN = 'return'
BREAK = 'break'
CONTINUE = 'continue'


def sequence(nodes, match_is_exhaustive):
    outcomes = {NORMAL}
    for node in nodes:
        if NORMAL not in outcomes:
            break
        outcomes = (outcomes - {NORMAL}) | exits(node, match_is_exhaustive)
    return outcomes


def exits(node, match_is_exhaustive):
    if node is None:
        return {NORMAL}
    if isinstance(node, (A.FnDecl, A.MethodDecl, A.LambdaExpr)):
        return {NORMAL}
    if isinstance(node, (A.ReturnStmt, H.HirReturn)):
        value = node.value if isinstance(node, A.ReturnStmt) else node.value_expr
        evaluated = exits(value, match_is_exhaustive)
        return (evaluated - {NORMAL}) | ({RETURN} if NORMAL in evaluated else set())
    if isinstance(node, (A.BreakStmt, H.HirBreak)):
        return {BREAK}
    if isinstance(node, (A.ContinueStmt, H.HirContinue)):
        return {CONTINUE}
    if isinstance(node, (A.Block, H.HirBlock)):
        return sequence(node.statements + [node.tail_expr], match_is_exhaustive)
    if isinstance(node, (A.IfExpr, H.HirIf)):
        condition = node.condition if isinstance(node, A.IfExpr) else node.cond
        initial = exits(condition, match_is_exhaustive)
        if NORMAL not in initial:
            return initial
        if isinstance(node, A.IfExpr):
            branches = [(condition, node.then_branch)] + [(b.condition, b.body) for b in node.elif_branches]
            fallback = node.else_branch
        else:
            branches = [(condition, node.then_block)] + node.elifs
            fallback = node.else_block
        outcomes = initial - {NORMAL}
        for index, (test, body) in enumerate(branches):
            if index:
                tested = exits(test, match_is_exhaustive)
                outcomes |= tested - {NORMAL}
                if NORMAL not in tested:
                    return outcomes
            constant = test.value if isinstance(test, (A.BoolLiteral, H.HirConstBool)) else None
            if constant is not False:
                outcomes |= exits(body, match_is_exhaustive)
            if constant is True:
                return outcomes
        return outcomes | exits(fallback, match_is_exhaustive)
    if isinstance(node, (A.MatchExpr, H.HirMatch)):
        initial = exits(node.subject, match_is_exhaustive)
        if NORMAL not in initial:
            return initial
        outcomes = initial - {NORMAL}
        for arm in node.arms:
            outcomes |= exits(arm.guard, match_is_exhaustive) - {NORMAL}
            outcomes |= exits(arm.body, match_is_exhaustive)
        if not match_is_exhaustive(node):
            outcomes.add(NORMAL)
        return outcomes
    if isinstance(node, (A.ForStmt, A.WhileStmt, H.HirForList, H.HirWhile)):
        condition = getattr(node, 'condition', getattr(node, 'cond_expr', None))
        iterable = getattr(node, 'iterable', getattr(node, 'list_expr', None))
        initial = exits(condition if condition is not None else iterable, match_is_exhaustive)
        if NORMAL not in initial:
            return initial
        body = exits(node.body, match_is_exhaustive)
        outcomes = (initial - {NORMAL}) | (body - {NORMAL, BREAK, CONTINUE})
        if not isinstance(condition, (A.BoolLiteral, H.HirConstBool)) or not condition.value or BREAK in body:
            outcomes.add(NORMAL)
        return outcomes
    if isinstance(node, (A.BinaryExpr, H.HirBinaryOp)) and node.op in ('and', 'or'):
        initial = exits(node.left, match_is_exhaustive)
        if NORMAL not in initial:
            return initial
        constant = node.left.value if isinstance(node.left, (A.BoolLiteral, H.HirConstBool)) else None
        skip = constant is (node.op == 'or')
        required = constant is (node.op == 'and')
        if skip:
            return initial
        right = exits(node.right, match_is_exhaustive)
        return (initial - {NORMAL}) | right | (set() if required else {NORMAL})
    if isinstance(node, (A.CoalesceExpr, H.HirCoalesce)):
        left = node.left if isinstance(node, A.CoalesceExpr) else node.opt_expr
        right = node.right if isinstance(node, A.CoalesceExpr) else node.fallback_expr
        initial = exits(left, match_is_exhaustive)
        if NORMAL not in initial:
            return initial
        return initial | exits(right, match_is_exhaustive)
    if isinstance(node, (A.ElseErrExpr, H.HirElseHandle)):
        initial = exits(node.operand, match_is_exhaustive)
        if NORMAL not in initial:
            return initial
        return initial | exits(node.handler, match_is_exhaustive)
    if isinstance(node, (A.PostfixTryExpr, H.HirTryPropagate)):
        initial = exits(node.operand, match_is_exhaustive)
        return initial | ({RETURN} if NORMAL in initial else set())
    if isinstance(node, (list, tuple)):
        return sequence(node, match_is_exhaustive)
    if isinstance(node, (A.Node, H.HirNode)):
        children = []
        for child in vars(node).values():
            if isinstance(child, (A.Node, H.HirNode)):
                children.append(child)
            elif isinstance(child, (list, tuple)):
                children.append(child)
        return sequence(children, match_is_exhaustive)
    return {NORMAL}
