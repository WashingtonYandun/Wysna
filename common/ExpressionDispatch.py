"""Closed AST expression dispatch shared as infrastructure, never as semantics."""
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Mapping

from error.Error import WysnaError
from node.NodeType import Expr


@dataclass(frozen=True)
class ExpressionDispatch:
    """Use exact node classes so new syntax cannot inherit unrelated semantics."""

    stage: str
    handlers: Mapping[type, Callable]

    def __post_init__(self):
        if any(not isinstance(node, type) or not issubclass(node, Expr)
               or not callable(handler) for node, handler in self.handlers.items()):
            raise ValueError("Expression handlers require Expr classes and callables")
        object.__setattr__(self, "handlers", MappingProxyType(dict(self.handlers)))

    def handler_for(self, expression: Expr, file: str) -> Callable:
        handler = self.handlers.get(type(expression))
        if handler is None:
            reject_unknown_node(expression, file, self.stage, "expression")
        return handler


def reject_unknown_node(node, file: str, stage: str, kind: str) -> None:
    """Report a compiler invariant without converting unimplemented syntax to unit."""
    raise WysnaError(
        f"Unsupported AST {kind}: {type(node).__name__}",
        file=getattr(node, "source_file", None) or file,
        line=getattr(node, "line", None), col=getattr(node, "col", None),
        code="W9999", stage=stage,
    )
