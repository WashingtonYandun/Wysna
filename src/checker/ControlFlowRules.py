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
    outer_expected = checker.expected_type
    try:
        checker.expected_type = None
        cond_t = checker._check_expr(expr.condition)
        if not checker._require_boolean(cond_t, expr.condition, "Condition of 'if'"):
            raise TypeError(
                message=f"Condition of 'if' must be bool, found '{cond_t}'",
                line=expr.condition.line,
                col=expr.condition.col,
                file=checker.file,
                code="W2001"
            )
        checker.expected_type = None if is_statement_context else outer_expected
        then_t = checker._check_block(expr.then_branch, is_statement_context=is_statement_context) or UnitType
        branch_expected = None
        if not is_statement_context:
            if outer_expected is not None and outer_expected != UnitType:
                branch_expected = outer_expected
            elif then_t != UnitType:
                branch_expected = then_t

        for elif_b in expr.elif_branches:
            checker.expected_type = None
            e_cond_t = checker._check_expr(elif_b.condition)
            if not checker._require_boolean(e_cond_t, elif_b.condition, "Condition of 'elif'"):
                raise TypeError(message=f"Condition of 'elif' must be bool, found '{e_cond_t}'", line=elif_b.condition.line, col=elif_b.condition.col, file=checker.file, code="W2001")
            checker.expected_type = branch_expected
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
            checker.expected_type = branch_expected
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
    finally:
        checker.expected_type = outer_expected


def check_match_expr(checker: "TypeChecker", expr: MatchExpr, is_statement_context: bool = False) -> WysnaType:
    outer_expected = checker.expected_type
    checker.expected_type = None
    subject_t = checker._check_expr(expr.subject)
    arm_expected = (
        outer_expected
        if (not is_statement_context and outer_expected is not None and outer_expected != UnitType)
        else None
    )
    covered_variants: Set[str] = set()
    has_wildcard = False
    arm_types: List[WysnaType] = []
    expr.discard_result = is_statement_context

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
        from checker.PatternRules import resolve_pattern
        arm.pattern = resolve_pattern(checker, arm.pattern, subject_t)
        arm_bindings: Dict[str, WysnaType] = {}
        checker._bind_pattern_type(arm.pattern, subject_t, arm_bindings, arm.line, arm.col)
        if checker._pattern_matrix_exhaustive([subject_t], [[arm.pattern]]):
            has_wildcard = True

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
        checker.expected_type = None if is_statement_context else arm_expected
        try:
            arm_t = checker._check_block(arm.body, is_statement_context=is_statement_context,
                                         initial_bindings=arm_bindings) or UnitType
        finally:
            checker.expected_type = outer_expected
        if not is_statement_context and arm_expected is None and arm_t != UnitType:
            arm_expected = arm_t
        if not is_statement_context:
            arm_types.append(arm_t)

    # Guards do not contribute unconditional coverage. Specialize the
    # pattern matrix to account for nested variants and payload products.
    if expected_variants is not None or subject_t == BoolType or isinstance(subject_t, TupleType):
        rows = [[arm.pattern] for arm in expr.arms if arm.guard is None]
        if not checker._pattern_matrix_exhaustive([subject_t], rows):
            from checker.PatternCoverage import pattern_matrix_exhaustive
            families = []
            pattern_matrix_exhaustive([subject_t], rows, missing_families=families)
            families.sort()
            raise TypeError("Non-exhaustive match; add cases for: " + ', '.join(families),
                            line=expr.line, col=expr.col, file=checker.file, code="W2003").with_data(
                                kind="non_exhaustive_match",
                                subject_type=str(subject_t),
                                open_domain=False,
                                uncovered_pattern_families=families,
                                missing_variants=families)
    else:
        # The subject domain is open (int, str, struct, ...), so the pattern
        # matrix can never be closed. The match is still only well defined when
        # it yields a value: a falling-through arm would return nothing where a
        # value is required, which the backends used to reject with an internal
        # error. A statement-position match may legitimately fall through.
        yields_value = not is_statement_context and (
            any(at != UnitType for at in arm_types)
            or (outer_expected is not None and outer_expected != UnitType)
        )
        complete = checker._pattern_matrix_exhaustive([subject_t], [[arm.pattern] for arm in expr.arms if arm.guard is None])
        if yields_value and not complete:
            raise TypeError(
                message=f"Non-exhaustive match: '{subject_t}' is an open domain, "
                        f"so a '_ =>' fallback arm is required when the match produces a value",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W2003",
            ).with_data(
                kind="non_exhaustive_match",
                subject_type=str(subject_t),
                open_domain=True,
                uncovered_pattern_families=["_"],
                missing_variants=[],
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
