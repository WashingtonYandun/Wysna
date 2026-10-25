"""TypeChecker expression rules; mutable state belongs to the invoking context."""
from typing import TYPE_CHECKING

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
)
from checker.WysnaType import (
    WysnaType,
    IntType,
    FloatType,
    BoolType,
    StrType,
    NoneType,
    ListType,
    EnumType,
    FunctionType,
    TypeParamType,
)
from error.Error import TypeError

if TYPE_CHECKING:
    from checker.TypeChecker import TypeChecker


def check_lambda_expr(checker: "TypeChecker", expr: LambdaExpr, is_statement_context: bool = False) -> WysnaType:
    from checker.ClosureConversion import convert_lambda
    return convert_lambda(checker, expr)


def check_int_literal(checker: "TypeChecker", expr: IntLiteral, is_statement_context: bool = False) -> WysnaType:
    if expr.value > 9223372036854775807 or expr.value < -9223372036854775808:
        raise TypeError(
            message=f"Integer literal {expr.value} exceeds 64-bit signed range [-9223372036854775808, 9223372036854775807]",
            line=expr.line, col=expr.col, file=checker.file, code="W0002"
        )
    return IntType


def check_float_literal(checker: "TypeChecker", expr: FloatLiteral, is_statement_context: bool = False) -> WysnaType:
    return FloatType


def check_string_literal(checker: "TypeChecker", expr: StringLiteral, is_statement_context: bool = False) -> WysnaType:
    return StrType


def check_bool_literal(checker: "TypeChecker", expr: BoolLiteral, is_statement_context: bool = False) -> WysnaType:
    return BoolType


def check_none_literal(checker: "TypeChecker", expr: NoneLiteral, is_statement_context: bool = False) -> WysnaType:
    return NoneType


def check_ident_expr(checker: "TypeChecker", expr: IdentExpr, is_statement_context: bool = False) -> WysnaType:
    sym = checker.current_scope.lookup_symbol(expr.name)
    if expr.name in ("None", "none") and sym is None:
        return NoneType
    if sym is not None:
        if sym.kind == 'builtin' and not getattr(expr, 'is_callee', False):
            raise TypeError("Builtin function values require an explicit lambda adapter", file=checker.file, line=expr.line, col=expr.col, code="W2013")
        expr.resolved_symbol = sym
        if sym.kind == "fn" and isinstance(sym.type, FunctionType):
            expr.function_reference = sym.name
        return sym.type
    # Check if referencing an Enum type
    enum_type = checker.current_scope.lookup_type(expr.name)
    if enum_type is not None and isinstance(enum_type, EnumType):
        return enum_type
    # Check if referencing an unqualified enum variant
    candidates = [t for t in checker.global_scope.types.values()
                  if isinstance(t, EnumType) and expr.name in t.variants]
    if len(candidates) > 1:
        raise TypeError(f"Ambiguous enum variant '{expr.name}'; qualify its enum", file=checker.file,
                        line=expr.line, col=expr.col, code="W1001")
    if candidates:
        enum_type = candidates[0]
        if enum_type.variant_payloads.get(expr.name):
            raise TypeError(f"Variant '{expr.name}' requires a payload", file=checker.file,
                            line=expr.line, col=expr.col, code="W2005")
        expr.resolved_enum_name = enum_type.name
        return enum_type
    for tmpl in checker.generic_enum_templates.values():
        var_names = [v.name if hasattr(v, "name") else str(v) for v in tmpl.ast_decl.variants]
        if expr.name in var_names:
            if tmpl.name == "Option" and expr.name in ("None", "none"):
                return NoneType
            concrete_args = [TypeParamType(p.name if hasattr(p, "name") else str(p), owner=tmpl.name)
                             for p in tmpl.type_params]
            return checker._monomorphize_enum(tmpl, concrete_args, expr.line, expr.col)
    raise TypeError(
        message=f"Unknown identifier '{expr.name}'",
        line=expr.line,
        col=expr.col,
        file=checker.file,
        code="W1001"
    )


def check_unary_expr(checker: "TypeChecker", expr: UnaryExpr, is_statement_context: bool = False) -> WysnaType:
    operand_t = checker._check_expr(expr.operand)
    if expr.op == "-":
        if isinstance(operand_t, TypeParamType) and checker._checking_generic_definition:
            checker.type_relations.record_operation(expr.op, (operand_t,), file=checker.file,
                                                 line=expr.line, col=expr.col)
            return operand_t
        if operand_t in (IntType, FloatType):
            return operand_t
        raise TypeError(
            message=f"Unary '-' operator expects number, found '{operand_t}'",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W2001"
        )
    if expr.op == "not":
        if isinstance(operand_t, TypeParamType) and checker._checking_generic_definition:
            checker.type_relations.record_operation(expr.op, (operand_t,), file=checker.file,
                                                 line=expr.line, col=expr.col)
            return BoolType
        if operand_t == BoolType:
            return BoolType
        raise TypeError(
            message=f"Unary 'not' operator expects bool, found '{operand_t}'",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W2001"
        )


def check_binary_expr(checker: "TypeChecker", expr: BinaryExpr, is_statement_context: bool = False) -> WysnaType:
    lhs_t = checker._check_expr(expr.left)
    rhs_t = checker._check_expr(expr.right)
    op = expr.op

    if (checker._checking_generic_definition and
            (isinstance(lhs_t, TypeParamType) or isinstance(rhs_t, TypeParamType)) and
            op not in ("==", "!=")):
        checker.type_relations.record_operation(op, (lhs_t, rhs_t), file=checker.file,
                                             line=expr.line, col=expr.col)
        if op in ("and", "or", "<", "<=", ">", ">="):
            return BoolType
        return lhs_t if isinstance(lhs_t, TypeParamType) else rhs_t

    if op == "+":
        if lhs_t == IntType and rhs_t == IntType:
            return IntType
        if lhs_t == FloatType and rhs_t == FloatType:
            return FloatType
        if lhs_t == StrType and rhs_t == StrType:
            return StrType
        raise TypeError(
            message=f"Binary '+' cannot be applied to '{lhs_t}' and '{rhs_t}'",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W2001"
        )

    if op in ("-", "*", "/"):
        if lhs_t == IntType and rhs_t == IntType:
            return IntType
        if lhs_t == FloatType and rhs_t == FloatType:
            return FloatType
        raise TypeError(
            message=f"Binary '{op}' cannot be applied to '{lhs_t}' and '{rhs_t}'",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W2001"
        )

    if op == "%":
        if lhs_t == IntType and rhs_t == IntType:
            return IntType
        raise TypeError(
            message=f"Operator '%' is only defined for integers, found '{lhs_t}' and '{rhs_t}'",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W2001"
        )

    if op in ("==", "!="):
        left_id = checker.interner.from_type(lhs_t)
        right_id = checker.interner.from_type(rhs_t)
        if not checker.type_relations.equality_compatible(left_id, right_id):
            raise TypeError(
                message=f"Cannot compare equality of different types '{lhs_t}' and '{rhs_t}'",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W2001"
            )
        return BoolType

    if op in ("<", "<=", ">", ">="):
        if lhs_t == IntType and rhs_t == IntType:
            return BoolType
        if lhs_t == FloatType and rhs_t == FloatType:
            return BoolType
        raise TypeError(
            message=f"Relational comparisons are only defined for numbers, found '{lhs_t}' and '{rhs_t}'",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W2001"
        )

    if op in ("and", "or"):
        if lhs_t == BoolType and rhs_t == BoolType:
            return BoolType
        raise TypeError(
            message=f"Logical '{op}' expects bool operands, found '{lhs_t}' and '{rhs_t}'",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W2001"
        )


def check_range_expr(checker: "TypeChecker", expr: RangeExpr, is_statement_context: bool = False) -> WysnaType:
    start_t = checker._check_expr(expr.start)
    end_t = checker._check_expr(expr.end)
    if start_t != IntType or end_t != IntType:
        raise TypeError(
            message=f"Range bounds must be integers, found '{start_t}' and '{end_t}'",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W2001"
        )
    return ListType(IntType)
