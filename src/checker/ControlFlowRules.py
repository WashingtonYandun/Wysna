"""TypeChecker expression rules; mutable state belongs to the invoking context."""
from typing import TYPE_CHECKING

from typing import Dict, List, Optional, Set
from node.NodeType import (
    IdentifierPattern,
    TuplePattern,
    EnumPattern,
    ErrorTypePattern,
    LiteralPattern,
    WildcardPattern,
    NoneLiteral,
    IfExpr,
    MatchExpr,
)
from checker.WysnaType import (
    WysnaType,
    BoolType,
    UnitType,
    OptionalType,
    TupleType,
    EnumType,
    ResultType,
    ErrorUnionType,
)
from checker.SymbolTable import Scope, Symbol
from error.Error import TypeError

if TYPE_CHECKING:
    from checker.TypeChecker import TypeChecker


def check_if_expr(checker: "TypeChecker", expr: IfExpr, is_statement_context: bool = False) -> WysnaType:
    cond_t = checker._check_expr(expr.condition)
    if not checker._require_boolean(cond_t, expr.condition, "Condition of 'if'"):
        raise TypeError(
            message=f"Condition of 'if' must be bool, found '{cond_t}'",
            line=expr.condition.line,
            col=expr.condition.col,
            file=checker.file,
            code="W2001"
        )
    then_t = checker._check_block(expr.then_branch, is_statement_context=is_statement_context) or UnitType

    for elif_b in expr.elif_branches:
        e_cond_t = checker._check_expr(elif_b.condition)
        if not checker._require_boolean(e_cond_t, elif_b.condition, "Condition of 'elif'"):
            raise TypeError(message=f"Condition of 'elif' must be bool, found '{e_cond_t}'", line=elif_b.condition.line, col=elif_b.condition.col, file=checker.file, code="W2001")
        e_then_t = checker._check_block(elif_b.body, is_statement_context=is_statement_context) or UnitType
        if not is_statement_context and not checker.type_relations.is_assignable(e_then_t, then_t):
            raise TypeError(
                message=f"Incompatible types in 'elif' branch: '{e_then_t}' vs '{then_t}'",
                line=elif_b.line,
                col=elif_b.col,
                file=checker.file,
                code="W2001"
            )

    if expr.else_branch is not None:
        else_t = checker._check_block(expr.else_branch, is_statement_context=is_statement_context) or UnitType
        if not is_statement_context and not checker.type_relations.is_assignable(else_t, then_t):
            raise TypeError(
                message=f"Incompatible types in 'if' expression branches: '{else_t}' vs '{then_t}'",
                line=expr.else_branch.line,
                col=expr.else_branch.col,
                file=checker.file,
                code="W2001"
            )
        return UnitType if is_statement_context else then_t
    else:
        if not is_statement_context and then_t != UnitType:
            raise TypeError(
                message="If-expression returning a value requires an 'else' branch",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W2001"
            )
        return UnitType


def check_match_expr(checker: "TypeChecker", expr: MatchExpr, is_statement_context: bool = False) -> WysnaType:
    outer_expected = checker.expected_type
    subject_t = checker._check_expr(expr.subject)
    covered_variants: Set[str] = set()
    has_wildcard = False
    arm_types: List[WysnaType] = []

    # Determine expected variants and variant payload types for subject_t
    expected_variants: Optional[Set[str]] = None
    payload_types_map: Dict[str, List[WysnaType]] = {}

    if isinstance(subject_t, OptionalType):
        expected_variants = {"Some", "None"}
        payload_types_map = {"Some": [subject_t.inner], "None": []}
    elif isinstance(subject_t, ResultType):
        expected_variants = {"Ok", "Err"}
        payload_types_map = {"Ok": [subject_t.ok], "Err": [subject_t.err]}
    elif isinstance(subject_t, ErrorUnionType):
        expected_variants = {checker._error_pattern_name(member) for member in subject_t.alternatives}
        payload_types_map = {
            checker._error_pattern_name(member): [member]
            for member in subject_t.alternatives
        }
    elif isinstance(subject_t, EnumType):
        if getattr(subject_t, "template_name", None) == "Option" and getattr(subject_t, "type_args", None):
            expected_variants = {"Some", "None"}
            payload_types_map = {"Some": [subject_t.type_args[0]], "None": []}
        elif getattr(subject_t, "template_name", None) == "Result" and getattr(subject_t, "type_args", None) and len(subject_t.type_args) == 2:
            expected_variants = {"Ok", "Err"}
            payload_types_map = {"Ok": [subject_t.type_args[0]], "Err": [subject_t.type_args[1]]}
        else:
            expected_variants = set(subject_t.variants)
            payload_types_map = subject_t.variant_payloads

    for arm in expr.arms:
        previous_coverage = set(covered_variants)
        previous_wildcard = has_wildcard
        if isinstance(subject_t, ErrorUnionType) and isinstance(arm.pattern, (EnumPattern, ErrorTypePattern)):
            arm.pattern = checker._resolve_error_type_pattern(arm.pattern, subject_t)
        elif expected_variants is not None and isinstance(arm.pattern, IdentifierPattern) and arm.pattern.name in expected_variants:
            owner_name = getattr(subject_t, "name", None)
            if getattr(subject_t, "template_name", None) in ("Option", "Result"):
                owner_name = subject_t.template_name
            arm.pattern = EnumPattern(
                enum_name=owner_name,
                variant=arm.pattern.name,
                args=[],
                line=arm.pattern.line,
                col=arm.pattern.col
            )
        arm.pattern.inferred_type_id = checker.interner.intern(subject_t)
        arm.pattern.session_arena_id = checker.interner.arena_id
        arm_bindings: Dict[str, WysnaType] = {}

        if isinstance(arm.pattern, WildcardPattern):
            has_wildcard = True

        elif isinstance(arm.pattern, IdentifierPattern):
            if arm.pattern.name in ("None", "none") and expected_variants and "None" in expected_variants:
                covered_variants.add("None")
            else:
                has_wildcard = True
                arm_bindings[arm.pattern.name] = subject_t

        elif isinstance(arm.pattern, TuplePattern):
            checker._bind_pattern_type(arm.pattern, subject_t, arm_bindings, arm.line, arm.col)

        elif isinstance(arm.pattern, LiteralPattern):
            if isinstance(arm.pattern.literal, NoneLiteral) and expected_variants and "None" in expected_variants:
                covered_variants.add("None")
            else:
                lit_t = checker._check_expr(arm.pattern.literal)
                if not checker.type_relations.is_assignable(lit_t, subject_t):
                    raise TypeError(
                        message=f"Literal pattern type '{lit_t}' incompatible with subject '{subject_t}'",
                        line=arm.line,
                        col=arm.col,
                        file=checker.file,
                        code="W2001"
                    )

        elif isinstance(arm.pattern, ErrorTypePattern):
            if not isinstance(subject_t, ErrorUnionType):
                raise TypeError(message="Error type pattern requires an error union", line=arm.line,
                                col=arm.col, file=checker.file, code="W2001")
            checker._bind_pattern_type(arm.pattern.binding, arm.pattern.matched_type,
                                    arm_bindings, arm.line, arm.col)
            covered_variants.add(checker._error_pattern_name(arm.pattern.matched_type))

        elif isinstance(arm.pattern, EnumPattern):
            v_name = arm.pattern.variant
            checker._validate_pattern_owner(arm.pattern, subject_t)

            if expected_variants is not None and v_name not in expected_variants:
                raise TypeError(
                    message=f"Pattern variant '{v_name}' is incompatible with match subject '{subject_t}'",
                    line=arm.line,
                    col=arm.col,
                    file=checker.file,
                    code="W2001"
                )
            if isinstance(subject_t, EnumType) and getattr(subject_t, "template_name", None) not in ("Option", "Result"):
                arm.pattern.enum_name = subject_t.name
            covered_variants.add(v_name)
            expected_payloads = payload_types_map.get(v_name, [])
            if len(arm.pattern.args) != len(expected_payloads):
                raise TypeError(
                    message=f"Variant '{v_name}' expects {len(expected_payloads)} arguments, found {len(arm.pattern.args)}",
                    line=arm.line,
                    col=arm.col,
                    file=checker.file,
                    code="W2005"
                )
            for arg_pat, arg_type in zip(arm.pattern.args, expected_payloads):
                checker._bind_pattern_type(arg_pat, arg_type, arm_bindings, arm.line, arm.col)

        if arm.guard is not None:
            guard_scope = Scope(parent=checker.current_scope)
            for bn, bt in arm_bindings.items():
                guard_scope.define_symbol(Symbol(bn, bt, is_mut=False, kind="var", line=arm.line, col=arm.col), file=checker.file)
            old_s = checker.current_scope
            checker.current_scope = guard_scope
            try:
                g_t = checker._check_expr(arm.guard)
                if not checker._require_boolean(g_t, arm.guard, "Match guard"):
                    raise TypeError(
                        message=f"Match guard must be bool, found '{g_t}'",
                        line=arm.guard.line,
                        col=arm.guard.col,
                        file=checker.file,
                        code="W2001"
                    )
            finally:
                checker.current_scope = old_s

        if arm.guard is not None:
            covered_variants = previous_coverage
            has_wildcard = previous_wildcard
        arm_t = checker._check_block(arm.body, initial_bindings=arm_bindings) or UnitType
        arm_types.append(arm_t)

    # Guards do not contribute unconditional coverage. Specialize the
    # pattern matrix to account for nested variants and payload products.
    if expected_variants is not None or subject_t == BoolType or isinstance(subject_t, TupleType):
        rows = [[arm.pattern] for arm in expr.arms if arm.guard is None]
        if not checker._pattern_matrix_exhaustive([subject_t], rows):
            raise TypeError("Non-exhaustive match: missing variant or payload cases",
                            line=expr.line, col=expr.col, file=checker.file, code="W2003")
    else:
        # The subject domain is open (int, str, struct, ...), so the pattern
        # matrix can never be closed. The match is still only well defined when
        # it yields a value: a falling-through arm would return nothing where a
        # value is required, which the backends used to reject with an internal
        # error. A statement-position match may legitimately fall through.
        yields_value = (
            any(at != UnitType for at in arm_types)
            or (not is_statement_context and outer_expected is not None and outer_expected != UnitType)
        )
        if yields_value and not has_wildcard:
            raise TypeError(
                message=f"Non-exhaustive match: '{subject_t}' is an open domain, "
                        f"so a '_ =>' fallback arm is required when the match produces a value",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W2003",
            )

    if arm_types:
        first_t = arm_types[0]
        for at in arm_types[1:]:
            if not checker.type_relations.is_assignable(at, first_t):
                raise TypeError(
                    message=f"Incompatible return types in match arms: '{at}' vs '{first_t}'",
                    line=expr.line,
                    col=expr.col,
                    file=checker.file,
                    code="W2001"
                )
        return first_t
    return UnitType
