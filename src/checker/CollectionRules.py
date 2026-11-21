"""TypeChecker expression rules; mutable state belongs to the invoking context."""
from typing import TYPE_CHECKING

from typing import Dict, Optional, Set
from node.NodeType import StructInitExpr, FieldInit, IdentExpr, MemberExpr, ListExpr, DictExpr, TupleExpr, clone_ast
from checker.SymbolTable import Scope, Symbol
from checker.WysnaType import (
    WysnaType,
    ListType,
    DictType,
    TupleType,
    StructType,
    ClassType,
    AnyType,
)
from error.Error import TypeError
from checker.Suggestions import suggest

if TYPE_CHECKING:
    from checker.TypeChecker import TypeChecker


def check_struct_init_expr(checker: "TypeChecker", expr: StructInitExpr, is_statement_context: bool = False) -> WysnaType:
    spread_t = checker._check_payload(expr.spread, None) if expr.spread is not None else None
    template = checker.generic_struct_templates.get(expr.struct_name)
    if template is not None:
        if expr.type_args:
            concrete_args = [checker._resolve_type_node(a) for a in expr.type_args]
        else:
            type_params_set = {p.name if hasattr(p, "name") else str(p) for p in template.type_params}
            bindings: Dict[str, WysnaType] = {}
            template_fields = {f.name: f.type_ann for f in template.ast_decl.fields}
            if isinstance(spread_t, StructType):
                for f_name, f_ann in template_fields.items():
                    if f_name in spread_t.fields:
                        checker._unify_type(f_ann, spread_t.fields[f_name], type_params_set, bindings)
            for f in expr.fields:
                if f.name in template_fields:
                    a_t = checker._check_expr(f.value)
                    if not checker._unify_type(template_fields[f.name], a_t, type_params_set, bindings):
                        raise TypeError(
                            message=f"Conflicting type inference for field '{f.name}' in struct '{template.name}': cannot unify '{a_t}'",
                            line=f.line,
                            col=f.col,
                            file=checker.file,
                            code="W2001"
                        )

            for p in template.type_params:
                p_name = p.name if hasattr(p, "name") else str(p)
                if p_name not in bindings:
                    raise TypeError(
                        message=f"Cannot infer type argument for type parameter '{p_name}' in struct '{template.name}'",
                        line=expr.line,
                        col=expr.col,
                        file=checker.file,
                        code="W2001"
                    )
            concrete_args = checker._concrete_type_args(template, bindings)

        struct_t = checker._monomorphize_struct(template, concrete_args, expr.line, expr.col)
        expr.struct_name = struct_t.name
        expr.type_args = []
    else:
        if expr.type_args:
            raise TypeError(
                message=f"Struct '{expr.struct_name}' is not generic",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W1001"
            )
        struct_t = checker.current_scope.lookup_type(expr.struct_name)
        if struct_t is None or not isinstance(struct_t, StructType):
            raise TypeError(
                message=f"Unknown struct type '{expr.struct_name}'",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W1001"
            )

    seen_fields: Set[str] = set()
    for f in expr.fields:
        if f.name in seen_fields:
            raise TypeError(
                message=f"Duplicate field '{f.name}' in struct initialization of '{expr.struct_name}'",
                line=f.line,
                col=f.col,
                file=checker.file,
                code="W1002"
            )
        if f.name not in struct_t.fields:
            raise suggest(TypeError(
                message=f"Struct '{expr.struct_name}' has no field '{f.name}'",
                line=f.line,
                col=f.col,
                file=checker.file,
                code="W3002"
            ), f.name, struct_t.fields)
        seen_fields.add(f.name)

    spread_temp_scope = None
    if expr.spread is not None:
        if spread_t is None or not checker.type_relations.is_assignable(spread_t, struct_t):
            raise TypeError(
                message=f"Struct spread type mismatch: expected '{struct_t}', found '{spread_t}'",
                line=expr.spread.line,
                col=expr.spread.col,
                file=checker.file,
                code="W2001"
            )
        spread_end_line = getattr(expr.spread, "end_line", expr.spread.line)
        spread_end_col = getattr(expr.spread, "end_col", expr.spread.col)
        missing_fields = [req_field for req_field in struct_t.fields if req_field not in seen_fields]
        use_single_eval = len(missing_fields) > 1 and not isinstance(expr.spread, IdentExpr)
        if use_single_eval:
            tmp_name = f"__spread_tmp_{id(expr)}"
            tmp_sym = Symbol(tmp_name, struct_t, is_mut=False, kind="var", line=expr.spread.line, col=expr.spread.col)
            expr._spread_once_expr = expr.spread
            expr._spread_once_name = tmp_name
            spread_temp_scope = Scope(parent=checker.current_scope)
            spread_temp_scope.define_symbol(tmp_sym, file=checker.file)
        for req_field in missing_fields:
            if use_single_eval:
                spread_base = IdentExpr(tmp_name, line=expr.spread.line, col=expr.spread.col)
                spread_base.resolved_symbol = tmp_sym
                spread_base.inferred_type = struct_t
                spread_base.inferred_type_id = checker.interner.from_type(struct_t)
                spread_base.session_arena_id = checker.interner.arena_id
            else:
                spread_base = clone_ast(expr.spread)
            member = MemberExpr(
                spread_base,
                req_field,
                line=expr.spread.line,
                col=expr.spread.col,
            )
            member.end_line = spread_end_line
            member.end_col = spread_end_col
            f_init = FieldInit(
                req_field,
                member,
                line=expr.spread.line,
                col=expr.spread.col,
            )
            f_init.end_line = spread_end_line
            f_init.end_col = spread_end_col
            if use_single_eval:
                f_init._spread_synthesized = True
            expr.fields.append(f_init)
            seen_fields.add(req_field)
        expr.spread = None

    for req_field in struct_t.fields:
        if req_field not in seen_fields:
            raise TypeError(
                message=f"Missing required field '{req_field}' in struct initialization of '{expr.struct_name}'",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W2007"
            )

    for f in expr.fields:
        if f.name not in struct_t.fields:
            raise suggest(TypeError(
                message=f"Struct '{expr.struct_name}' has no field '{f.name}'",
                line=f.line,
                col=f.col,
                file=checker.file,
                code="W3002"
            ), f.name, struct_t.fields)
        expected_f_type = struct_t.fields[f.name]
        old_scope = checker.current_scope
        if getattr(f, "_spread_synthesized", False) and spread_temp_scope is not None:
            checker.current_scope = spread_temp_scope
        try:
            actual_f_type = checker._check_payload(f.value, expected_f_type)
        finally:
            checker.current_scope = old_scope
        if not checker.type_relations.is_assignable(actual_f_type, expected_f_type):
            raise TypeError(
                message=f"Field '{f.name}' type mismatch: expected '{expected_f_type}', found '{actual_f_type}'",
                line=f.line,
                col=f.col,
                file=checker.file,
                code="W2001"
            )
        checker._mark_result_lift(f.value, expected_f_type, force=True)
    return struct_t


def _unwrap_expected_payload(expected):
    from checker.WysnaType import ResultType, OptionalType
    curr = expected
    while isinstance(curr, (ResultType, OptionalType)):
        curr = curr.ok if isinstance(curr, ResultType) else curr.inner
    return curr


def check_list_expr(checker: "TypeChecker", expr: ListExpr, is_statement_context: bool = False) -> WysnaType:
    unwrapped = _unwrap_expected_payload(checker.expected_type)
    context = unwrapped if isinstance(unwrapped, ListType) else None
    if len(expr.elements) == 0:
        return context or ListType(AnyType())
    elem_types = [checker._check_payload(e, context.element if context else None) for e in expr.elements]
    common_t = elem_types[0]
    for t in elem_types[1:]:
        if checker.type_relations.is_assignable(t, common_t):
            continue
        if checker.type_relations.is_assignable(common_t, t):
            common_t = t
            continue
        found_super: Optional[WysnaType] = None
        if isinstance(common_t, ClassType) and isinstance(t, ClassType):
            curr = common_t.superclass
            while curr is not None:
                if checker.type_relations.is_assignable(t, curr):
                    found_super = curr
                    break
                curr = curr.superclass
        if found_super is not None:
            common_t = found_super
            continue
        raise TypeError(
            message=f"List elements must be homogeneous: found '{t}' and '{common_t}'",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W2001"
        )
    return ListType(common_t)


def check_tuple_expr(checker: "TypeChecker", expr: TupleExpr, is_statement_context: bool = False) -> WysnaType:
    unwrapped = _unwrap_expected_payload(checker.expected_type)
    expected = unwrapped if isinstance(unwrapped, TupleType) else None
    if expected is not None and len(expected.elements) == len(expr.elements):
        elem_types = [
            checker._check_payload(elem, exp_t)
            for elem, exp_t in zip(expr.elements, expected.elements)
        ]
    else:
        elem_types = [checker._check_payload(e, None) for e in expr.elements]
    return TupleType(elem_types)


def check_dict_expr(checker: "TypeChecker", expr: DictExpr, is_statement_context: bool = False) -> WysnaType:
    unwrapped = _unwrap_expected_payload(checker.expected_type)
    context = unwrapped if isinstance(unwrapped, DictType) else None
    if len(expr.entries) == 0:
        return context or DictType(AnyType(), AnyType())
    first_key_t = checker._check_payload(expr.entries[0].key, context.key if context else None)
    if not checker._is_valid_dict_key_type(first_key_t):
        raise TypeError(
            message=f"Invalid dictionary key type '{first_key_t}'; allowed key types are int, str, bool, or enum",
            line=expr.entries[0].key.line,
            col=expr.entries[0].key.col,
            file=checker.file,
            code="W2010"
        )
    first_val_t = checker._check_payload(expr.entries[0].value, context.value if context else None)
    for entry in expr.entries[1:]:
        kt = checker._check_payload(entry.key, context.key if context else None)
        vt = checker._check_payload(entry.value, context.value if context else None)
        if not checker._is_valid_dict_key_type(kt):
            raise TypeError(
                message=f"Invalid dictionary key type '{kt}'; allowed key types are int, str, bool, or enum",
                line=entry.key.line,
                col=entry.key.col,
                file=checker.file,
                code="W2010"
            )
        if not checker.type_relations.is_assignable(kt, first_key_t):
            raise TypeError(
                message=f"Dict keys must be homogeneous: found '{kt}' and '{first_key_t}'",
                line=entry.key.line,
                col=entry.key.col,
                file=checker.file,
                code="W2001"
            )
        if not checker.type_relations.is_assignable(vt, first_val_t):
            raise TypeError(
                message=f"Dict values must be homogeneous: found '{vt}' and '{first_val_t}'",
                line=entry.value.line,
                col=entry.value.col,
                file=checker.file,
                code="W2001"
            )
    return DictType(first_key_t, first_val_t)
