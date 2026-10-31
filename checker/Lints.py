"""Non-blocking lints for mistakes a language model makes that still type-check.

Lints return ``WysnaError`` objects with ``severity="warning"``. They never change
whether a program compiles; they make silent behavior visible to the author.
"""

from typing import Iterable, List

from error.Error import WysnaError
from node.NodeType import (
    BinaryExpr, BoolLiteral, Block, CoalesceExpr, Expr, ExprStmt, FloatLiteral,
    IdentExpr, IndexExpr, IntLiteral, MemberExpr, Node, NoneLiteral, RangeExpr,
    StringLiteral, ThisExpr, UnaryExpr,
)

# Expressions whose evaluation has no effect other than producing a value.
_PURE_NODES = (
    IntLiteral, FloatLiteral, StringLiteral, BoolLiteral, NoneLiteral, IdentExpr,
    ThisExpr, UnaryExpr, BinaryExpr, MemberExpr, IndexExpr, RangeExpr, CoalesceExpr,
)


def _children(node: Node) -> Iterable[Node]:
    for value in vars(node).values():
        if isinstance(value, Node):
            yield value
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, Node):
                    yield item


def _is_pure(expr: Node) -> bool:
    if not isinstance(expr, _PURE_NODES):
        return False
    return all(_is_pure(child) for child in _children(expr) if isinstance(child, Expr))


def _walk(node: Node) -> Iterable[Node]:
    stack = [node]
    while stack:
        current = stack.pop()
        yield current
        stack.extend(_children(current))


def lint_discarded_expressions(program, file: str) -> List[WysnaError]:
    """W6001: an expression statement whose value is computed and thrown away.

    The classic trigger is a line that starts with a binary operator, for example
    ``let x = a`` followed by ``-b``: newlines end statements, so ``-b`` is a
    separate statement and ``x`` silently loses the subtraction.
    """
    warnings: List[WysnaError] = []
    seen = set()
    for node in _walk(program):
        if not isinstance(node, Block):
            continue
        for statement in node.statements:
            if not isinstance(statement, ExprStmt) or not _is_pure(statement.expr):
                continue
            expr = statement.expr
            key = (expr.line, expr.col)
            if key in seen:
                continue
            seen.add(key)
            if isinstance(expr, UnaryExpr) and expr.op in ("-", "+"):
                help_text = (f"A line starting with '{expr.op}' begins a new statement. To continue the "
                             "previous expression, put the operator at the end of the previous line.")
            else:
                help_text = "Remove the statement, or use its value (bind it with 'let' or return it)."
            warnings.append(WysnaError(
                "Expression result is discarded; the statement has no effect",
                line=expr.line, col=expr.col, file=file, code="W6001",
                severity="warning", stage="lint", help=help_text,
            ).with_data(kind="discarded_expression"))
    warnings.sort(key=lambda w: (w.line, w.col))
    return warnings
