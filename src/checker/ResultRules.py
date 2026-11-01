"""TypeChecker expression rules; mutable state belongs to the invoking context."""
from typing import TYPE_CHECKING

from typing import Optional
from node.NodeType import CoalesceExpr, PostfixTryExpr, ElseErrExpr
from checker.WysnaType import WysnaType, UnitType, NoneType, OptionalType, EnumType, ResultType
from checker.SymbolTable import Scope, Symbol
from error.Error import TypeError

if TYPE_CHECKING:
    from checker.TypeChecker import TypeChecker


def check_coalesce_expr(checker: "TypeChecker", expr: CoalesceExpr, is_statement_context: bool = False) -> WysnaType:
    lhs_t = checker._check_expr(expr.left)
    opt_inner = None
    if isinstance(lhs_t, OptionalType):
        opt_inner = lhs_t.inner
    elif isinstance(lhs_t, EnumType) and getattr(lhs_t, "template_name", None) == "Option" and getattr(lhs_t, "type_args", None):
        opt_inner = lhs_t.type_args[0]
    elif lhs_t == NoneType:
        rhs_t = checker._check_expr(expr.right)
        return rhs_t
    if opt_inner is None:
        raise TypeError(
            message=f"Left operand of '??' must be an optional type (T?), found '{lhs_t}'",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W2006"
        )
    rhs_t = checker._check_payload(expr.right, opt_inner)
    if not checker.type_relations.is_assignable(rhs_t, opt_inner):
        raise TypeError(
            message=f"Right operand of '??' type mismatch: expected '{opt_inner}', found '{rhs_t}'",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W2001"
        )

    return opt_inner


def check_postfix_try_expr(checker: "TypeChecker", expr: PostfixTryExpr, is_statement_context: bool = False) -> WysnaType:
    operand_t = checker._check_expr(expr.operand)
    if isinstance(operand_t, OptionalType) or (isinstance(operand_t, EnumType) and getattr(operand_t, "template_name", None) == "Option" and getattr(operand_t, "type_args", None)):
        opt_inner = operand_t.inner if isinstance(operand_t, OptionalType) else operand_t.type_args[0]
        is_ret_opt = isinstance(checker.current_fn_ret_type, OptionalType) or (isinstance(checker.current_fn_ret_type, EnumType) and getattr(checker.current_fn_ret_type, "template_name", None) == "Option")
        if not is_ret_opt:
            raise TypeError(
                message=f"Cannot use '?' on optional in function returning non-optional type '{checker.current_fn_ret_type}'",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W2006"
            )
        expr.propagation_kind = "optional"
        return opt_inner

    if isinstance(operand_t, ResultType) or (isinstance(operand_t, EnumType) and getattr(operand_t, "template_name", None) == "Result" and getattr(operand_t, "type_args", None) and len(operand_t.type_args) == 2):
        res_ok = operand_t.ok if isinstance(operand_t, ResultType) else operand_t.type_args[0]
        res_err = operand_t.err if isinstance(operand_t, ResultType) else operand_t.type_args[1]
        is_ret_res = isinstance(checker.current_fn_ret_type, ResultType) or (isinstance(checker.current_fn_ret_type, EnumType) and getattr(checker.current_fn_ret_type, "template_name", None) == "Result")
        if not is_ret_res:
            raise TypeError(
                message=f"Cannot use '?' on result in function returning non-result type '{checker.current_fn_ret_type}'",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W2006"
            )
        cur_err = checker.current_fn_ret_type.err if isinstance(checker.current_fn_ret_type, ResultType) else checker.current_fn_ret_type.type_args[1]
        if not checker.type_relations.is_error_channel_assignable(res_err, cur_err):
            raise TypeError(
                message=f"Error type mismatch in '?' propagation: '{res_err}' is not covered by declared '{cur_err}'",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W2006"
            )
        expr.propagation_kind = "result"
        return res_ok

    raise TypeError(
        message=f"Operator '?' requires optional (T?) or result (T ! E), found '{operand_t}'",
        line=expr.line,
        col=expr.col,
        file=checker.file,
        code="W2006"
    )


def check_else_err_expr(checker: "TypeChecker", expr: ElseErrExpr, is_statement_context: bool = False) -> WysnaType:
    operand_t = checker._check_expr(expr.operand)
    unwrapped_t: WysnaType
    bound_err_t: Optional[WysnaType] = None

    if isinstance(operand_t, ResultType) or (isinstance(operand_t, EnumType) and getattr(operand_t, "template_name", None) == "Result" and getattr(operand_t, "type_args", None) and len(operand_t.type_args) == 2):
        unwrapped_t = operand_t.ok if isinstance(operand_t, ResultType) else operand_t.type_args[0]
        bound_err_t = operand_t.err if isinstance(operand_t, ResultType) else operand_t.type_args[1]
    elif isinstance(operand_t, OptionalType) or (isinstance(operand_t, EnumType) and getattr(operand_t, "template_name", None) == "Option" and getattr(operand_t, "type_args", None)):
        if expr.err_name is not None:
            raise TypeError(
                message=f"Cannot bind error variable '{expr.err_name}' on optional type '{operand_t}'; use 'else {{ ... }}' or '??'",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W2006"
            )
        unwrapped_t = operand_t.inner if isinstance(operand_t, OptionalType) else operand_t.type_args[0]
    else:
        raise TypeError(
            message=f"'else' handler requires result (T ! E) or optional (T?), found '{operand_t}'",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W2006"
        )

    handler_scope = Scope(parent=checker.current_scope)
    if expr.err_name is not None and bound_err_t is not None:
        handler_scope.define_symbol(
            Symbol(expr.err_name, bound_err_t, is_mut=False, kind="var", line=expr.line, col=expr.col),
            file=checker.file
        )

    old_scope = checker.current_scope
    checker.current_scope = handler_scope
    try:
        for s in expr.handler.statements:
            checker._check_stmt(s)
        tail_t = checker._check_expr(expr.handler.tail_expr) if expr.handler.tail_expr else None
    finally:
        checker.current_scope = old_scope

    if not checker._block_diverges(expr.handler):
        actual_handler_t = tail_t if tail_t is not None else UnitType
        if not checker.type_relations.is_assignable(actual_handler_t, unwrapped_t):
            raise TypeError(
                message=f"'else' handler must diverge (return/break/continue) or evaluate to '{unwrapped_t}', found '{actual_handler_t}'",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W2001"
            )

    return unwrapped_t
