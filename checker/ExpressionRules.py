"""Closed dispatch for independently testable semantic expression families."""
from common.ExpressionDispatch import ExpressionDispatch
from node.NodeType import (
    LambdaExpr,
    IntLiteral,
    FloatLiteral,
    StringLiteral,
    BoolLiteral,
    NoneLiteral,
    IdentExpr,
    UnaryExpr,
    BinaryExpr,
    RangeExpr,
    CoalesceExpr,
    PostfixTryExpr,
    ElseErrExpr,
    ThisExpr,
    SuperExpr,
    NewExpr,
    AssignmentExpr,
    MemberExpr,
    IndexExpr,
    CallExpr,
    StructInitExpr,
    ListExpr,
    TupleExpr,
    DictExpr,
    IfExpr,
    MatchExpr,
)
from checker.ScalarRules import (
    check_lambda_expr,
    check_int_literal,
    check_float_literal,
    check_string_literal,
    check_bool_literal,
    check_none_literal,
    check_ident_expr,
    check_unary_expr,
    check_binary_expr,
    check_range_expr,
)
from checker.ResultRules import (
    check_coalesce_expr,
    check_postfix_try_expr,
    check_else_err_expr,
)
from checker.AccessRules import (
    check_this_expr,
    check_super_expr,
    check_new_expr,
    check_assignment_expr,
    check_member_expr,
    check_index_expr,
)
from checker.CallRules import (
    check_call_expr,
)
from checker.CollectionRules import (
    check_struct_init_expr,
    check_list_expr,
    check_tuple_expr,
    check_dict_expr,
)
from checker.ControlFlowRules import (
    check_if_expr,
    check_match_expr,
)


EXPRESSION_RULES = ExpressionDispatch("type", {
    LambdaExpr: check_lambda_expr,
    IntLiteral: check_int_literal,
    FloatLiteral: check_float_literal,
    StringLiteral: check_string_literal,
    BoolLiteral: check_bool_literal,
    NoneLiteral: check_none_literal,
    IdentExpr: check_ident_expr,
    UnaryExpr: check_unary_expr,
    BinaryExpr: check_binary_expr,
    RangeExpr: check_range_expr,
    CoalesceExpr: check_coalesce_expr,
    PostfixTryExpr: check_postfix_try_expr,
    ElseErrExpr: check_else_err_expr,
    ThisExpr: check_this_expr,
    SuperExpr: check_super_expr,
    NewExpr: check_new_expr,
    AssignmentExpr: check_assignment_expr,
    MemberExpr: check_member_expr,
    IndexExpr: check_index_expr,
    CallExpr: check_call_expr,
    StructInitExpr: check_struct_init_expr,
    ListExpr: check_list_expr,
    TupleExpr: check_tuple_expr,
    DictExpr: check_dict_expr,
    IfExpr: check_if_expr,
    MatchExpr: check_match_expr,
})
