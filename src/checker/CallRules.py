"""TypeChecker expression rules; mutable state belongs to the invoking context."""
from dataclasses import dataclass
from typing import TYPE_CHECKING, Union

from checker.OopGenerics import GenericOopTemplate, specialize as specialize_oop
from typing import Dict, List, Optional
from node.NodeType import NamedType, GenericType, IdentExpr, CallExpr, MemberExpr, IndexExpr
from checker.WysnaType import (
    WysnaType,
    IntType,
    FloatType,
    BoolType,
    StrType,
    OptionalType,
    WeakType,
    ListType,
    DictType,
    TupleType,
    StructType,
    EnumType,
    ClassType,
    ResultType,
    FunctionType,
    ModuleType,
    AnyType,
    GenericEnumTemplate,
    GenericStructTemplate,
    GenericFnTemplate,
)
from error.Error import TypeError
from checker.Suggestions import suggest_mutable

if TYPE_CHECKING:
    from checker.TypeChecker import TypeChecker



@dataclass(frozen=True)
class CallTarget:
    """Callable type whose arguments still need validation."""
    type: Optional[WysnaType]


@dataclass(frozen=True)
class CheckedCall:
    """Constructor or rewritten call whose result is already checked."""
    type: WysnaType


CallResolution = Union[CallTarget, CheckedCall]


def resolve_async_call_return(
    checker: "TypeChecker",
    inner_type: WysnaType,
    line: int,
    col: int,
) -> WysnaType:
    """Wrap an async function's return type into the canonical Task[T] struct layout."""
    inner_tid = checker.interner.from_type(inner_type)
    has_tp = checker.type_relations._contains_type_parameter(inner_tid)
    if not has_tp:
        task_tmpl = None
        owner_unit = None
        async_sym = checker.current_scope.lookup_symbol("async")
        if async_sym and isinstance(async_sym.type, ModuleType):
            candidate = async_sym.type.scope.lookup_type("Task")
            if isinstance(candidate, GenericStructTemplate):
                task_tmpl = candidate
                owner_unit = getattr(async_sym.type, "checked_module", None)
        if task_tmpl is None:
            candidate = checker.current_scope.lookup_type("Task")
            if isinstance(candidate, GenericStructTemplate):
                task_tmpl = candidate
        if task_tmpl is None and getattr(checker, "session", None) is not None:
            for unit in getattr(checker.session, "modules", {}).values():
                ufile = str(getattr(unit, "file", "")).replace("\\", "/")
                if ufile.endswith("stdlib/async.wysna") and getattr(unit, "checker", None) is not None:
                    candidate = unit.checker.global_scope.lookup_type("Task")
                    if isinstance(candidate, GenericStructTemplate):
                        task_tmpl = candidate
                        owner_unit = unit
                        break
        if isinstance(task_tmpl, GenericStructTemplate):
            if owner_unit is not None and getattr(owner_unit, "checker", None) is not None:
                struct_t = owner_unit.checker._monomorphize_struct(task_tmpl, [inner_type], line, col)
                checker.session.refresh_specializations(owner_unit)
                return struct_t
            return checker._monomorphize_struct(task_tmpl, [inner_type], line, col)

    task_name = f"__WysnaTask__{inner_tid}"
    existing = checker.global_scope.lookup_type(task_name)
    if isinstance(existing, StructType):
        return existing
    task_struct = StructType(
        task_name,
        {
            "id": IntType,
            "handle": IntType,
            "is_ready": BoolType,
            "is_failed": BoolType,
            "value": OptionalType(inner_type),
            "error": OptionalType(StrType),
        },
    )
    task_struct.template_name = "Task"
    task_struct.type_args = [inner_type]
    checker.global_scope.define_type(task_name, task_struct, file=checker.file)
    checker.interner.from_type(task_struct)
    return task_struct


def check_call_expr(checker: "TypeChecker", expr: CallExpr, is_statement_context: bool = False) -> WysnaType:
    expr.callee.is_callee = True
    attribute_symbol = None
    if isinstance(expr.callee, IdentExpr):
        attribute_symbol = checker.current_scope.lookup_symbol(expr.callee.name)
    elif isinstance(expr.callee, MemberExpr):
        module = checker._module_receiver(expr.callee.object)
        if module is not None:
            attribute_symbol = module.scope.lookup_symbol(expr.callee.property)
    expr.static_attributes = (list(getattr(attribute_symbol.type, 'static_attributes', []))
                              if attribute_symbol is not None and attribute_symbol.kind == 'fn' else [])
    if (isinstance(expr.callee, IdentExpr) and expr.type_args
            and checker.current_scope.lookup_symbol(expr.callee.name) is None):
        template = checker.current_scope.lookup_type(expr.callee.name)
        if isinstance(template, GenericOopTemplate):
            type_ = specialize_oop(template, [checker._resolve_type_node(a) for a in expr.type_args], expr.line, expr.col)
            expr.callee.name = type_.name
            expr.type_args = None
    if isinstance(expr.callee, IdentExpr) and checker.current_scope.lookup_symbol(expr.callee.name) is None:
        variants = [t for t in checker.global_scope.types.values()
                    if isinstance(t, EnumType) and not getattr(t, "template_name", None)
                    and expr.callee.name in t.variants]
        if len(variants) > 1:
            raise TypeError("Ambiguous enum constructor; qualify its enum", file=checker.file,
                            line=expr.line, col=expr.col, code="W1001")
        if variants:
            expr.callee = MemberExpr(IdentExpr(variants[0].name, expr.line, expr.col),
                                     expr.callee.name, expr.line, expr.col)
    # Support generic function calls and generic struct constructors
    callee_t: Optional[WysnaType] = None
    if isinstance(expr.callee, MemberExpr):
        resolution = resolve_module_call(checker, expr, callee_t)
        if isinstance(resolution, CheckedCall):
            return resolution.type
        callee_t = resolution.type
    if isinstance(expr.callee, IdentExpr):
        resolution = resolve_named_call(checker, expr, callee_t)
        if isinstance(resolution, CheckedCall):
            return resolution.type
        callee_t = resolution.type

    if isinstance(expr.callee, MemberExpr):
        owner, explicit_args = checker._enum_owner_with_type_args(expr.callee)
        if isinstance(owner, GenericEnumTemplate):
            if explicit_args and not expr.type_args:
                expr.type_args = explicit_args
            if hasattr(expr.callee, "enum_type_args"):
                expr.callee.enum_type_args = None
            return check_generic_variant(checker, expr, owner)
        if isinstance(owner, EnumType):
            return check_enum_variant(checker, expr, owner)

    # Mutating method check on receiver (transitive across value-type paths)
    if callee_t is None:
        if isinstance(expr.callee, MemberExpr):
            from checker.AccessRules import resolve_member_with_receiver_type
            obj_t = checker._check_expr(expr.callee.object, allow_module=True)
            if isinstance(obj_t, ClassType) and expr.callee.property in obj_t.generic_methods:
                gm_template = obj_t.generic_methods[expr.callee.property]
                if (not gm_template.is_public
                        and (checker.current_class is None or checker.current_class.name != gm_template.owner_class)):
                    raise TypeError(
                        message=f"Cannot access private method '{expr.callee.property}' of class '{gm_template.owner_class}'",
                        line=expr.callee.line,
                        col=expr.callee.col,
                        file=checker.file,
                        code="W3002",
                    )
                if expr.type_args:
                    concrete_args = [checker._resolve_type_node(a) for a in expr.type_args]
                else:
                    owner_checker = gm_template.checker or checker
                    concrete_args = infer_type_arguments(
                        checker,
                        gm_template,
                        gm_template.ast_decl.params,
                        expr,
                        "generic method",
                        unify_checker=owner_checker,
                    )
                mangled_method, _ = checker._monomorphize_method(obj_t, gm_template, concrete_args, expr.line, expr.col)
                expr.callee.property = mangled_method
                expr.type_args = []
            callee_t = resolve_member_with_receiver_type(checker, expr.callee, obj_t)
            expr.callee.inferred_type = callee_t
            expr.callee.inferred_type_id = checker.interner.from_type(callee_t)
            expr.callee.session_arena_id = checker.interner.arena_id
            if isinstance(callee_t, FunctionType) and callee_t.is_mutating:
                sym = checker._find_value_root_symbol(expr.callee.object)
                if sym and not sym.is_mut:
                    raise suggest_mutable(TypeError(
                        message=checker._immutable_binding_message(sym, f"call mutating method '{expr.callee.property}'"),
                        line=expr.line,
                        col=expr.col,
                        file=checker.file,
                        code="W3001"
                    ), sym, checker.file)
        else:
            callee_t = checker._check_expr(expr.callee)
    if isinstance(callee_t, AnyType):
        for a in expr.args:
            checker._check_expr(a)
        return AnyType()

    if not isinstance(callee_t, FunctionType):
        alternative = getattr(expr, 'index_alternative', None)
        if alternative is not None and isinstance(callee_t, (ListType, DictType)):
            expr.callee = IndexExpr(expr.callee, alternative, expr.line, expr.col)
            expr.type_args = None
            expr.index_alternative = None
            return checker._check_expr(expr)
        raise TypeError(
            message=f"Cannot call non-function of type '{callee_t}'",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W2005"
        )


    if not isinstance(expr.callee, (IdentExpr, MemberExpr)):
        expr.indirect_call = True
    elif isinstance(expr.callee, IdentExpr):
        symbol = checker.current_scope.lookup_symbol(expr.callee.name)
        expr.indirect_call = symbol is not None and symbol.kind not in ("fn", "builtin")

    if isinstance(expr.callee, MemberExpr):
        receiver_type = getattr(expr.callee.object, 'inferred_type', None)
        if isinstance(receiver_type, StructType) or (isinstance(receiver_type, ClassType) and expr.callee.property in receiver_type.fields):
            expr.indirect_call = True

    # Special built-in checks
    if (isinstance(expr.callee, IdentExpr)
            and getattr(getattr(expr.callee, 'resolved_symbol', None), 'kind', None) == 'builtin'):
        builtin_type = check_builtin_call(checker, expr)
        if builtin_type is not None:
            return builtin_type

    if expr.type_args:
        raise TypeError("Type arguments require a generic function", file=checker.file,
                        line=expr.line, col=expr.col, code="W2003")
    # Standard function argument checking
    if callee_t.params == [AnyType()]:
        # Variadic (like print)
        for a in expr.args:
            checker._check_expr(a)

        return (
            resolve_async_call_return(checker, callee_t.ret, expr.line, expr.col)
            if getattr(callee_t, "is_async", False)
            else callee_t.ret
        )

    if len(expr.args) != len(callee_t.params):
        raise TypeError(
            message=f"Expected {len(callee_t.params)} arguments, found {len(expr.args)}",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W2005"
        )

    for i, (a_expr, p_type) in enumerate(zip(expr.args, callee_t.params)):
        a_type = checker._check_payload(a_expr, p_type)
        if not checker.type_relations.is_assignable(a_type, p_type):
            raise TypeError(
                message=f"Argument {i + 1} type mismatch: expected '{p_type}', found '{a_type}'",
                line=a_expr.line,
                col=a_expr.col,
                file=checker.file,
                code="W2001"
            )
        checker._mark_result_lift(a_expr, p_type, force=True)
    return (
        resolve_async_call_return(checker, callee_t.ret, expr.line, expr.col)
        if getattr(callee_t, "is_async", False)
        else callee_t.ret
    )


def resolve_module_call(checker: "TypeChecker", expr: CallExpr, callee_t: Optional[WysnaType]) -> CallResolution:
    module_type = checker._module_receiver(expr.callee.object)
    if isinstance(module_type, ModuleType):
        type_template = module_type.scope.lookup_type(expr.callee.property)
        if isinstance(type_template, GenericOopTemplate):
            if not expr.type_args:
                raise TypeError("Generic class constructor needs explicit type arguments", file=checker.file, line=expr.line, col=expr.col, code="W2001")
            specialized = specialize_oop(type_template, [checker._resolve_type_node(a) for a in expr.type_args], expr.line, expr.col)
            if checker.global_scope.lookup_type(specialized.name) is None:
                checker.global_scope.define_type(specialized.name, specialized, file=checker.file)
            expr.callee = IdentExpr(specialized.name, expr.line, expr.col)
            expr.type_args = None
            return CheckedCall(checker._check_expr(expr))
        if isinstance(type_template, ClassType):
            if checker.global_scope.lookup_type(type_template.name) is None:
                checker.global_scope.define_type(type_template.name, type_template, file=checker.file)
            expr.callee = IdentExpr(type_template.name, expr.line, expr.col)
            return CheckedCall(checker._check_class_instantiation(type_template, expr.args, expr.line, expr.col))
        symbol = module_type.scope.lookup_symbol(expr.callee.property)
        if symbol and isinstance(symbol.type, GenericFnTemplate):
            callee_t = specialize_imported_function(checker, expr, symbol.type, module_type.checked_module)
    return CallTarget(callee_t)


def specialize_imported_function(checker: "TypeChecker", expr: CallExpr, template: GenericFnTemplate,
                                 imported_from) -> FunctionType:
    source = getattr(template.ast_decl, "source_file", None)
    unit = checker.session.modules.get(source, imported_from)
    if expr.type_args:
        concrete_args = [checker._resolve_type_node(arg) for arg in expr.type_args]
    else:
        concrete_args = infer_type_arguments(checker, template, template.ast_decl.params, expr,
                                             "generic function", unify_checker=unit.checker)
    specialized = unit.checker._monomorphize_fn(template, concrete_args, expr.line, expr.col)
    checker.session.refresh_specializations(unit)
    expr.callee = IdentExpr(specialized.name, line=expr.callee.line, col=expr.callee.col)
    expr.callee.resolved_symbol = specialized
    expr.type_args = []
    return specialized.type


def resolve_named_call(checker: "TypeChecker", expr: CallExpr, callee_t: Optional[WysnaType]) -> CallResolution:
    callee_name = expr.callee.name
    symbol = checker.current_scope.lookup_symbol(callee_name)
    if callee_name in ("weak", "upgrade") and symbol and symbol.kind == "builtin":
        if len(expr.args) != 1 or expr.type_args:
            raise TypeError(f"{callee_name} expects one argument and no type arguments",
                            line=expr.line, col=expr.col, file=checker.file, code="W2005")
        expr.callee.resolved_symbol = symbol
        argument = checker._check_expr(expr.args[0])
        if callee_name == "weak" and isinstance(argument, ClassType):
            return CheckedCall(WeakType(argument))
        if callee_name == "upgrade" and isinstance(argument, WeakType):
            return CheckedCall(OptionalType(argument.target))
        raise TypeError(f"Invalid argument type '{argument}' for {callee_name}",
                        line=expr.line, col=expr.col, file=checker.file, code="W2003")
    if symbol and symbol.kind in ("var", "param"):
        if expr.type_args:
            alternative = getattr(expr, 'index_alternative', None)
            if isinstance(symbol.type, (ListType, DictType)) and alternative is not None:
                expr.callee = IndexExpr(expr.callee, alternative, expr.line, expr.col)
                expr.type_args = None
                expr.index_alternative = None
                return CheckedCall(checker._check_expr(expr))
            raise TypeError(f"Symbol '{callee_name}' is not a generic function or struct",
                            file=checker.file, line=expr.line, col=expr.col, code="W2001")
        callee_t = checker._check_expr(expr.callee)
    elif (symbol and isinstance(symbol.type, GenericFnTemplate)
          and (owner_unit := checker.session.modules.get(getattr(symbol.type.ast_decl, "source_file", None))) is not None
          and owner_unit.checker is not checker):
        callee_t = specialize_imported_function(checker, expr, symbol.type, owner_unit)
    elif callee_name in checker.generic_fn_templates:
        template = checker.generic_fn_templates[callee_name]
        if expr.type_args:
            concrete_args = [checker._resolve_type_node(a) for a in expr.type_args]
        else:
            concrete_args = infer_type_arguments(checker, template, template.ast_decl.params, expr, "generic function")

        fn_sym = checker._monomorphize_fn(template, concrete_args, expr.line, expr.col)
        expr.callee.name = fn_sym.name
        expr.type_args = []
        callee_t = fn_sym.type

    elif callee_name in checker.generic_struct_templates:
        template = checker.generic_struct_templates[callee_name]
        if expr.type_args:
            concrete_args = [checker._resolve_type_node(a) for a in expr.type_args]
        else:
            concrete_args = infer_type_arguments(checker, template, template.ast_decl.fields, expr, "struct constructor")

        struct_t = checker._monomorphize_struct(template, concrete_args, expr.line, expr.col)
        expr.callee.name = struct_t.name
        expr.type_args = []
        ctor_sym = checker.global_scope.lookup_symbol(struct_t.name)
        callee_t = ctor_sym.type if ctor_sym else FunctionType([struct_t.fields[f.name] for f in template.ast_decl.fields], struct_t)

    elif callee_name == "Some":
        if len(expr.args) != 1:
            raise TypeError(
                message=f"Constructor 'Some' expects 1 argument, found {len(expr.args)}",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W2005"
            )
        context = checker._builtin_arguments(checker.expected_type or checker.current_fn_ret_type, "Option")
        arg_t = checker._check_payload(expr.args[0], context[0] if context else None)
        template = checker.generic_enum_templates["Option"]
        opt_t = checker._monomorphize_enum(template, [arg_t], expr.line, expr.col)
        return CheckedCall(opt_t)

    elif callee_name == "Ok":
        if len(expr.args) != 1:
            raise TypeError(
                message=f"Constructor 'Ok' expects 1 argument, found {len(expr.args)}",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W2005"
            )
        ctx_t = checker.expected_type or checker.current_fn_ret_type
        context = checker._builtin_arguments(ctx_t, "Result")
        arg_t = checker._check_payload(expr.args[0], context[0] if context else None)
        err_t = AnyType()
        if isinstance(ctx_t, ResultType):
            err_t = ctx_t.err
        elif isinstance(ctx_t, EnumType) and getattr(ctx_t, "template_name", None) == "Result" and ctx_t.type_args and len(ctx_t.type_args) == 2:
            err_t = ctx_t.type_args[1]
        template = checker.generic_enum_templates["Result"]
        res_t = checker._monomorphize_enum(template, [arg_t, err_t], expr.line, expr.col)
        return CheckedCall(res_t)

    elif callee_name == "Err":
        if len(expr.args) != 1:
            raise TypeError(
                message=f"Constructor 'Err' expects 1 argument, found {len(expr.args)}",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W2005"
            )
        ctx_t = checker.expected_type or checker.current_fn_ret_type
        context = checker._builtin_arguments(ctx_t, "Result")
        err_t = checker._check_payload(expr.args[0], context[1] if context else None)
        ok_t = AnyType()
        if isinstance(ctx_t, ResultType):
            ok_t = ctx_t.ok
        elif isinstance(ctx_t, EnumType) and getattr(ctx_t, "template_name", None) == "Result" and ctx_t.type_args and len(ctx_t.type_args) == 2:
            ok_t = ctx_t.type_args[0]
        template = checker.generic_enum_templates["Result"]
        res_t = checker._monomorphize_enum(template, [ok_t, err_t], expr.line, expr.col)
        return CheckedCall(res_t)

    else:
        found_tmpl = None
        found_var = None
        for tmpl_name, tmpl in (checker.generic_enum_templates.items()
                                if not symbol or symbol.kind not in ("var", "param") else []):
            for v in tmpl.ast_decl.variants:
                v_name = v.name if hasattr(v, "name") else str(v)
                if v_name == callee_name:
                    found_tmpl = tmpl
                    found_var = v
                    break
            if found_tmpl:
                break
        if found_tmpl and found_var:
            payload_types = getattr(found_var, "payload_types", [])
            if len(expr.args) != len(payload_types):
                raise TypeError(
                    message=f"Variant '{callee_name}' expects {len(payload_types)} arguments, found {len(expr.args)}",
                    line=expr.line,
                    col=expr.col,
                    file=checker.file,
                    code="W2005"
                )
            type_params_set = {p.name if hasattr(p, "name") else str(p) for p in found_tmpl.type_params}
            bindings = {}
            for pt, a_expr in zip(payload_types, expr.args):
                a_type = checker._check_expr(a_expr)
                checker._unify_type(pt, a_type, type_params_set, bindings)
            for p in found_tmpl.type_params:
                p_name = p.name if hasattr(p, "name") else str(p)
                if p_name not in bindings:
                    raise TypeError(
                        message=f"Could not infer type parameter '{p_name}' for enum constructor '{callee_name}'",
                        line=expr.line,
                        col=expr.col,
                        file=checker.file,
                        code="W2001"
                    )
            concrete_args = checker._concrete_type_args(found_tmpl, bindings)
            enum_t = checker._monomorphize_enum(found_tmpl, concrete_args, expr.line, expr.col)
            expr.callee = MemberExpr(IdentExpr(enum_t.name, line=expr.line, col=expr.col), callee_name, line=expr.line, col=expr.col)
            expr.type_args = []
            return CheckedCall(enum_t)

        if expr.type_args:
            symbol = checker.current_scope.lookup_symbol(callee_name)
            alternative = getattr(expr, 'index_alternative', None)
            if symbol and isinstance(symbol.type, (ListType, DictType)) and alternative is not None:
                expr.callee = IndexExpr(expr.callee, alternative, expr.line, expr.col)
                expr.type_args = None
                expr.index_alternative = None
                return CheckedCall(checker._check_expr(expr))
            raise TypeError(
                message=f"Symbol '{callee_name}' is not a generic function or struct",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W1001"
            )

        if checker.current_scope.lookup_symbol(expr.callee.name) is None:
            maybe_cls = checker.current_scope.lookup_type(expr.callee.name)
            if isinstance(maybe_cls, ClassType):
                return CheckedCall(checker._check_class_instantiation(maybe_cls, expr.args, expr.line, expr.col))
    return CallTarget(callee_t)


def check_generic_variant(checker: "TypeChecker", expr: CallExpr, template: GenericEnumTemplate) -> WysnaType:
    variant_name = expr.callee.property
    var_decl = next((v for v in template.ast_decl.variants if (v.name if hasattr(v, "name") else str(v)) == variant_name), None)
    if var_decl is None:
        raise TypeError(
            message=f"Generic enum '{template.name}' has no variant '{variant_name}'",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W1001"
        )
    payload_types = getattr(var_decl, "payload_types", [])
    if len(expr.args) != len(payload_types):
        raise TypeError(
            message=f"Variant '{variant_name}' expects {len(payload_types)} arguments, found {len(expr.args)}",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W2005"
        )
    had_explicit_type_args = bool(expr.type_args)
    if had_explicit_type_args:
        concrete_args = [checker._resolve_type_node(a) for a in expr.type_args]
    else:
        type_params_set = {p.name if hasattr(p, "name") else str(p) for p in template.type_params}
        bindings = {}
        ctx_t = checker.expected_type or checker.current_fn_ret_type
        context_args = checker._builtin_arguments(ctx_t, template.name)
        if isinstance(ctx_t, EnumType) and ctx_t.template_name == template.name:
            context_args = ctx_t.type_args or []
            if not payload_types:
                bindings.update(zip([p.name for p in template.type_params], context_args))
        context_types = dict(zip([p.name for p in template.type_params], context_args))
        for pt, a_expr in zip(payload_types, expr.args):
            expected = None
            if isinstance(pt, NamedType):
                expected = context_types.get(pt.name)
            elif isinstance(pt, GenericType):
                subst_pt = checker._substitute_type_node(pt, context_types)
                try:
                    expected = checker._resolve_type_node(subst_pt)
                except Exception:
                    expected = None
            a_type = checker._check_payload(a_expr, expected)
            checker._unify_type(pt, a_type, type_params_set, bindings)
        if template.name == "Result":
            if "E" not in bindings:
                if isinstance(ctx_t, ResultType):
                    bindings["E"] = ctx_t.err
                elif isinstance(ctx_t, EnumType) and getattr(ctx_t, "template_name", None) == "Result" and ctx_t.type_args and len(ctx_t.type_args) == 2:
                    bindings["E"] = ctx_t.type_args[1]
                else:
                    bindings["E"] = AnyType()
            if "T" not in bindings:
                if isinstance(ctx_t, ResultType):
                    bindings["T"] = ctx_t.ok
                elif isinstance(ctx_t, EnumType) and getattr(ctx_t, "template_name", None) == "Result" and ctx_t.type_args and len(ctx_t.type_args) == 2:
                    bindings["T"] = ctx_t.type_args[0]
                else:
                    bindings["T"] = AnyType()
        elif template.name == "Option":
            if "T" not in bindings:
                if isinstance(ctx_t, OptionalType):
                    bindings["T"] = ctx_t.inner
                elif isinstance(ctx_t, EnumType) and getattr(ctx_t, "template_name", None) == "Option" and ctx_t.type_args and len(ctx_t.type_args) == 1:
                    bindings["T"] = ctx_t.type_args[0]
                else:
                    bindings["T"] = AnyType()

        for p in template.type_params:
            p_name = p.name if hasattr(p, "name") else str(p)
            if p_name not in bindings:
                raise TypeError(
                    message=f"Could not infer type parameter '{p_name}' for enum constructor '{template.name}.{variant_name}'",
                    line=expr.line,
                    col=expr.col,
                    file=checker.file,
                    code="W2001"
                )
        concrete_args = checker._concrete_type_args(template, bindings)

    enum_t = checker._monomorphize_enum(template, concrete_args, expr.line, expr.col)
    if had_explicit_type_args:
        expected_payloads = enum_t.variant_payloads.get(variant_name, [])
        for i, (argument, expected) in enumerate(zip(expr.args, expected_payloads)):
            actual = checker._check_payload(argument, expected)
            if not checker.type_relations.is_assignable(actual, expected):
                raise TypeError(
                    f"Argument {i + 1} type mismatch: expected '{expected}', found '{actual}'",
                    line=argument.line, col=argument.col, file=checker.file, code="W2001",
                )
    receiver = expr.callee.object
    expr.callee.object = IdentExpr(enum_t.name, receiver.line, receiver.col)
    expr.type_args = []
    return enum_t


def check_enum_variant(checker: "TypeChecker", expr: CallExpr, enum_t: EnumType) -> WysnaType:
    variant_name = expr.callee.property
    if variant_name not in enum_t.variants:
        raise TypeError(f"Enum '{enum_t.name}' has no variant '{variant_name}'",
                        line=expr.line, col=expr.col, file=checker.file, code="W1001")
    expected_payloads = enum_t.variant_payloads.get(variant_name, [])
    if len(expr.args) != len(expected_payloads):
        raise TypeError(f"Variant '{variant_name}' expects {len(expected_payloads)} arguments, found {len(expr.args)}",
                        line=expr.line, col=expr.col, file=checker.file, code="W2005")
    for i, (argument, expected) in enumerate(zip(expr.args, expected_payloads)):
        actual = checker._check_payload(argument, expected)
        if not checker.type_relations.is_assignable(actual, expected):
            raise TypeError(f"Argument {i + 1} type mismatch: expected '{expected}', found '{actual}'",
                            line=argument.line, col=argument.col, file=checker.file, code="W2001")
    receiver = expr.callee.object
    if isinstance(receiver, MemberExpr):
        expr.callee.object = IdentExpr(enum_t.name, receiver.line, receiver.col)
    return enum_t


def check_builtin_call(checker: "TypeChecker", expr: CallExpr) -> Optional[WysnaType]:
    name = expr.callee.name
    if name == "len":
        if len(expr.args) != 1:
            raise TypeError(message="len() takes exactly 1 argument", line=expr.line, col=expr.col, file=checker.file, code="W2005")
        a_t = checker._check_expr(expr.args[0])
        if not (isinstance(a_t, (ListType, DictType, TupleType)) or a_t == StrType):
            raise TypeError(message=f"len() expects list, dict, or str, found '{a_t}'", line=expr.line, col=expr.col, file=checker.file, code="W2001")
        return IntType

    if name == "str":
        if len(expr.args) != 1:
            raise TypeError(message="str() takes exactly 1 argument", line=expr.line, col=expr.col, file=checker.file, code="W2005")
        checker._check_expr(expr.args[0])
        return StrType

    if name == "int":
        if len(expr.args) != 1:
            raise TypeError(message="int() takes exactly 1 argument", line=expr.line, col=expr.col, file=checker.file, code="W2005")
        a_t = checker._check_expr(expr.args[0])
        if a_t not in (IntType, FloatType, BoolType, StrType):
            raise TypeError(message=f"int() expects int, float, or str, found '{a_t}'", line=expr.line, col=expr.col, file=checker.file, code="W2001")
        return IntType

    if name == "float":
        if len(expr.args) != 1:
            raise TypeError(message="float() takes exactly 1 argument", line=expr.line, col=expr.col, file=checker.file, code="W2005")
        a_t = checker._check_expr(expr.args[0])
        if a_t not in (IntType, FloatType, BoolType, StrType):
            raise TypeError(message=f"float() expects int, float, or str, found '{a_t}'", line=expr.line, col=expr.col, file=checker.file, code="W2001")
        return FloatType

    if name == "slice":
        # SPEC 11.1: slice(x, a, b) works for str and for list[T], and
        # preserves the container kind. Each case is checked explicitly
        # because the result type depends on the argument type.
        if len(expr.args) != 3:
            raise TypeError(message="slice() takes exactly 3 arguments (value, start, end)",
                            line=expr.line, col=expr.col, file=checker.file, code="W2005")
        a_t = checker._check_expr(expr.args[0])
        for index in (1, 2):
            b_t = checker._check_expr(expr.args[index])
            if b_t != IntType:
                raise TypeError(
                    message=f"slice() bound {index} expects int, found '{b_t}'",
                    line=expr.args[index].line, col=expr.args[index].col,
                    file=checker.file, code="W2001")
        if a_t == StrType:
            return StrType
        if isinstance(a_t, ListType):
            return a_t
        raise TypeError(
            message=f"slice() expects str or list, found '{a_t}'",
            line=expr.line, col=expr.col, file=checker.file, code="W2001")

    if name == "enumerate":
        if len(expr.args) != 1:
            raise TypeError(message="enumerate() takes exactly 1 argument", line=expr.line, col=expr.col, file=checker.file, code="W2005")
        a_t = checker._check_expr(expr.args[0])
        if not isinstance(a_t, ListType):
            raise TypeError(message=f"enumerate() expects list, found '{a_t}'", line=expr.line, col=expr.col, file=checker.file, code="W2001")
        return ListType(TupleType([IntType, a_t.element]))

    if name == "dict_items":
        if len(expr.args) != 1:
            raise TypeError(message="dict_items() takes exactly 1 argument", line=expr.line, col=expr.col, file=checker.file, code="W2005")
        a_t = checker._check_expr(expr.args[0])
        if not isinstance(a_t, DictType):
            raise TypeError(message=f"dict_items() expects dict, found '{a_t}'", line=expr.line, col=expr.col, file=checker.file, code="W2001")
        return ListType(TupleType([a_t.key, a_t.value]))
    return None


def _normalize_task_arg_for_inference(
    checker: "TypeChecker",
    arg_t: WysnaType,
    line: int,
    col: int,
) -> WysnaType:
    if isinstance(arg_t, StructType) and arg_t.name.startswith("__WysnaTask__") and getattr(arg_t, "type_args", None):
        return resolve_async_call_return(checker, arg_t.type_args[0], line, col)
    if isinstance(arg_t, ListType):
        return ListType(_normalize_task_arg_for_inference(checker, arg_t.element, line, col))
    if isinstance(arg_t, DictType):
        return DictType(arg_t.key, _normalize_task_arg_for_inference(checker, arg_t.value, line, col))
    if isinstance(arg_t, OptionalType):
        return OptionalType(_normalize_task_arg_for_inference(checker, arg_t.inner, line, col))
    if isinstance(arg_t, TupleType):
        return TupleType([_normalize_task_arg_for_inference(checker, e, line, col) for e in arg_t.elements])
    return arg_t


def infer_type_arguments(checker: "TypeChecker", template, parameters, expr: CallExpr,
                         description: str, *, unify_checker: Optional["TypeChecker"] = None) -> List[WysnaType]:
    """Infer one complete substitution from positional declaration/argument pairs."""
    resolver = unify_checker or checker
    if len(expr.args) != len(parameters):
        raise TypeError(
            message=f"Expected {len(parameters)} arguments for {description} '{template.name}', found {len(expr.args)}",
            line=expr.line, col=expr.col, file=checker.file, code="W2005",
        )
    type_parameters = {parameter.name if hasattr(parameter, "name") else str(parameter)
                       for parameter in template.type_params}
    bindings: Dict[str, WysnaType] = {}
    for parameter, argument in zip(parameters, expr.args):
        argument_type = _normalize_task_arg_for_inference(checker, checker._check_expr(argument), argument.line, argument.col)
        if not resolver._unify_type(parameter.type_ann, argument_type, type_parameters, bindings):
            raise TypeError(
                message=f"Conflicting type inference for {description} '{template.name}': cannot unify '{argument_type}'",
                line=argument.line, col=argument.col, file=checker.file, code="W2001",
            )
    for parameter in template.type_params:
        name = parameter.name if hasattr(parameter, "name") else str(parameter)
        if name not in bindings:
            raise TypeError(
                message=f"Could not infer type parameter '{name}' for {description} '{template.name}'",
                line=expr.line, col=expr.col, file=checker.file, code="W2001",
            )
    return checker._concrete_type_args(template, bindings)
