"""TypeChecker expression rules; mutable state belongs to the invoking context."""
from typing import TYPE_CHECKING

from typing import Optional
from node.NodeType import (
    NamedType,
    GenericType,
    IdentExpr,
    ThisExpr,
    SuperExpr,
    NewExpr,
    MemberExpr,
    IndexExpr,
    AssignmentExpr,
)
from checker.WysnaType import (
    WysnaType,
    IntType,
    StrType,
    UnitType,
    NoneType,
    OptionalType,
    ListType,
    DictType,
    StructType,
    EnumType,
    InterfaceType,
    ClassType,
    FunctionType,
    ModuleType,
    AnyType,
    TypeParamType,
)
from error.Error import TypeError

if TYPE_CHECKING:
    from checker.TypeChecker import TypeChecker


def check_this_expr(checker: "TypeChecker", expr: ThisExpr, is_statement_context: bool = False) -> WysnaType:
    if checker.current_class is None:
        raise TypeError(
            message="'this' can only be used inside a class method",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W1004"
        )
    return checker.current_class


def check_super_expr(checker: "TypeChecker", expr: SuperExpr, is_statement_context: bool = False) -> WysnaType:
    if checker.current_class is None:
        raise TypeError(
            message="'super' can only be used inside a class method",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W1004"
        )
    if checker.current_class.superclass is None:
        raise TypeError(
            message=f"Class '{checker.current_class.name}' has no superclass",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W1004"
        )
    return checker.current_class.superclass


def check_new_expr(checker: "TypeChecker", expr: NewExpr, is_statement_context: bool = False) -> WysnaType:
    class_t = None
    if "." in expr.class_name and not getattr(expr, "type_args", None):
        class_t = checker._resolve_type_node(NamedType(expr.class_name, expr.line, expr.col))
        expr.class_name = class_t.name
    if getattr(expr, 'type_args', None):
        expression_type = checker._resolve_type_node(GenericType(expr.class_name, expr.type_args, expr.line, expr.col))
        expr.class_name = expression_type.name
        expr.type_args = None
    class_t = class_t or checker.current_scope.lookup_type(expr.class_name)
    if class_t is None or not isinstance(class_t, ClassType):
        raise TypeError(
            message=f"Unknown class type '{expr.class_name}'",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W1001"
        )
    return checker._check_class_instantiation(class_t, expr.args, expr.line, expr.col)


def check_assignment_expr(checker: "TypeChecker", expr: AssignmentExpr, is_statement_context: bool = False) -> WysnaType:
    # Constructors on the RHS need the binding's existing type context.
    old_expected = checker.expected_type
    if isinstance(expr.target, IdentExpr):
        destination = checker.current_scope.lookup_symbol(expr.target.name)
        if destination is not None:
            checker.expected_type = destination.type
    try:
        val_t = checker._check_expr(expr.value)
    finally:
        checker.expected_type = old_expected

    if isinstance(expr.target, IdentExpr):
        sym = checker.current_scope.lookup_symbol(expr.target.name)
        if sym is None:
            raise TypeError(
                message=f"Unknown identifier '{expr.target.name}'",
                line=expr.target.line,
                col=expr.target.col,
                file=checker.file,
                code="W1001"
            )
        if not sym.is_mut:
            raise TypeError(
                message=f"Cannot assign to immutable binding '{sym.name}'",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W3001"
            )
        if not checker.type_relations.is_assignable(val_t, sym.type):
            raise TypeError(
                message=f"Type mismatch: cannot assign value of type '{val_t}' to variable '{sym.name}' of type '{sym.type}'",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W2001"
            )
        checker._mark_result_lift(expr.value, sym.type, force=True)
        expr.target.inferred_type = sym.type
        expr.target.inferred_type_id = checker.interner.from_type(sym.type)
        expr.target.session_arena_id = checker.interner.arena_id
        return sym.type

    if isinstance(expr.target, IndexExpr):
        # Target value root must be a mutable binding (unless rooted in a reference-type class)
        sym = checker._find_value_root_symbol(expr.target.object)
        if sym and not sym.is_mut:
            raise TypeError(
                message=f"Cannot mutate elements of immutable binding '{sym.name}'",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W3001"
            )
        obj_t = checker._check_expr(expr.target.object)
        idx_t = checker._check_expr(expr.target.index)
        if isinstance(obj_t, ListType):
            if idx_t != IntType:
                raise TypeError(
                    message=f"List index must be int, found '{idx_t}'",
                    line=expr.target.index.line,
                    col=expr.target.index.col,
                    file=checker.file,
                    code="W2001"
                )
            if not checker.type_relations.is_assignable(val_t, obj_t.element):
                raise TypeError(
                    message=f"Cannot assign '{val_t}' to list of '{obj_t.element}'",
                    line=expr.line,
                    col=expr.col,
                    file=checker.file,
                    code="W2001"
                )
            checker._mark_result_lift(expr.value, obj_t.element, force=True)
            expr.target.inferred_type = obj_t.element
            expr.target.inferred_type_id = checker.interner.from_type(obj_t.element)
            expr.target.session_arena_id = checker.interner.arena_id
            return obj_t.element

        if isinstance(obj_t, DictType):
            if not checker.type_relations.is_assignable(idx_t, obj_t.key):
                raise TypeError(
                    message=f"Dict key type mismatch: expected '{obj_t.key}', found '{idx_t}'",
                    line=expr.target.index.line,
                    col=expr.target.index.col,
                    file=checker.file,
                    code="W2001"
                )
            if not checker.type_relations.is_assignable(val_t, obj_t.value):
                raise TypeError(
                    message=f"Dict value type mismatch: expected '{obj_t.value}', found '{val_t}'",
                    line=expr.line,
                    col=expr.col,
                    file=checker.file,
                    code="W2001"
                )
            checker._mark_result_lift(expr.value, obj_t.value, force=True)
            expr.target.inferred_type = obj_t.value
            expr.target.inferred_type_id = checker.interner.from_type(obj_t.value)
            expr.target.session_arena_id = checker.interner.arena_id
            return obj_t.value

        raise TypeError(
            message=f"Cannot assign to index of type '{obj_t}'",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W2001"
        )


    if isinstance(expr.target, MemberExpr):
        obj_t = checker._check_expr(expr.target.object)
        if isinstance(obj_t, ClassType):
            f_info = obj_t.fields.get(expr.target.property)
            if f_info is None:
                raise TypeError(
                    message=f"Class '{obj_t.name}' has no field '{expr.target.property}'",
                    line=expr.target.line,
                    col=expr.target.col,
                    file=checker.file,
                    code="W3002"
                )
            if not f_info.is_public and (checker.current_class is None or checker.current_class.name != f_info.owner_class):
                raise TypeError(
                    message=f"Cannot access private field '{expr.target.property}' of class '{f_info.owner_class}'",
                    line=expr.target.line,
                    col=expr.target.col,
                    file=checker.file,
                    code="W3002"
                )
            if not checker.type_relations.is_assignable(val_t, f_info.type):
                raise TypeError(
                    message=f"Type mismatch on field '{expr.target.property}': expected '{f_info.type}', found '{val_t}'",
                    line=expr.line,
                    col=expr.col,
                    file=checker.file,
                    code="W2001"
                )
            checker._mark_result_lift(expr.value, f_info.type, force=True)
            expr.target.inferred_type = f_info.type
            expr.target.inferred_type_id = checker.interner.from_type(f_info.type)
            expr.target.session_arena_id = checker.interner.arena_id
            return f_info.type

        sym = checker._find_value_root_symbol(expr.target.object)
        if sym and not sym.is_mut:
            raise TypeError(
                message=checker._immutable_binding_message(sym, "mutate a field"),
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W3001"
            )
        if not isinstance(obj_t, StructType):
            raise TypeError(
                message=f"Cannot assign to field of non-struct '{obj_t}'",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W3002"
            )
        if expr.target.property not in obj_t.fields:
            raise TypeError(
                message=f"Struct '{obj_t.name}' has no field '{expr.target.property}'",
                line=expr.target.line,
                col=expr.target.col,
                file=checker.file,
                code="W3002"
            )
        field_t = obj_t.fields[expr.target.property]
        if not checker.type_relations.is_assignable(val_t, field_t):
            raise TypeError(
                message=f"Type mismatch on field '{expr.target.property}': expected '{field_t}', found '{val_t}'",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W2001"
            )
        checker._mark_result_lift(expr.value, field_t, force=True)
        expr.target.inferred_type = field_t
        expr.target.inferred_type_id = checker.interner.from_type(field_t)
        expr.target.session_arena_id = checker.interner.arena_id
        return field_t

    raise TypeError(
        message="Invalid assignment target",
        line=expr.line,
        col=expr.col,
        file=checker.file,
        code="W2002"
    )


def check_member_expr(checker: "TypeChecker", expr: MemberExpr, is_statement_context: bool = False) -> WysnaType:
    # Enum variant access: Enum.Variant
    if (isinstance(expr.object, IdentExpr)
            and getattr(checker.current_scope.lookup_symbol(expr.object.name), 'kind', None) not in ('var', 'param')):
        if expr.object.name == "Option" and expr.property in ("None", "none"):
            return NoneType
        enum_t = checker.current_scope.lookup_type(expr.object.name)
        if isinstance(enum_t, EnumType):
            if expr.property not in enum_t.variants:
                raise TypeError(
                    message=f"Enum '{enum_t.name}' has no variant '{expr.property}'",
                    line=expr.line,
                    col=expr.col,
                    file=checker.file,
                    code="W1001"
                )
            if enum_t.variant_payloads.get(expr.property):
                raise TypeError(f"Variant '{expr.property}' requires a payload", file=checker.file,
                                line=expr.line, col=expr.col, code="W2005")
            return enum_t
        if expr.object.name in checker.generic_enum_templates:
            tmpl = checker.generic_enum_templates[expr.object.name]
            var_names = [v.name if hasattr(v, "name") else str(v) for v in tmpl.ast_decl.variants]
            if expr.property not in var_names:
                raise TypeError(
                    message=f"Generic enum '{tmpl.name}' has no variant '{expr.property}'",
                    line=expr.line,
                    col=expr.col,
                    file=checker.file,
                    code="W1001"
                )
            if tmpl.name == "Option" and expr.property in ("None", "none"):
                return NoneType
            ctx_t = checker.expected_type
            if isinstance(ctx_t, EnumType) and getattr(ctx_t, "template_name", None) == tmpl.name and ctx_t.type_args:
                concrete_args = list(ctx_t.type_args)
            else:
                concrete_args = [TypeParamType(p.name if hasattr(p, "name") else str(p), owner=tmpl.name)
                                 for p in tmpl.type_params]
            enum_t = checker._monomorphize_enum(tmpl, concrete_args, expr.line, expr.col)
            expr.object.name = enum_t.name
            return enum_t

    obj_t = checker._check_expr(expr.object, allow_module=True)
    if isinstance(obj_t, ClassType):
        if expr.property == "init" and isinstance(expr.object, SuperExpr):
            curr_s: Optional[ClassType] = obj_t
            while curr_s is not None:
                if curr_s.init_type is not None:
                    return curr_s.init_type
                curr_s = curr_s.superclass
            raise TypeError("Superclass has no declared constructor", file=checker.file,
                            line=expr.line, col=expr.col, code="W3002")

        f_info = obj_t.fields.get(expr.property)
        if f_info is not None:
            if not f_info.is_public and (checker.current_class is None or checker.current_class.name != f_info.owner_class):
                raise TypeError(
                    message=f"Cannot access private field '{expr.property}' of class '{f_info.owner_class}'",
                    line=expr.line,
                    col=expr.col,
                    file=checker.file,
                    code="W3002"
                )
            return f_info.type

        m_info = obj_t.methods.get(expr.property)
        if m_info is not None:
            if not m_info.is_public and (checker.current_class is None or checker.current_class.name != m_info.owner_class):
                raise TypeError(
                    message=f"Cannot access private method '{expr.property}' of class '{m_info.owner_class}'",
                    line=expr.line,
                    col=expr.col,
                    file=checker.file,
                    code="W3002"
                )
            return m_info.fn_type

        raise TypeError(
            message=f"Class '{obj_t.name}' has no member '{expr.property}'",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W3002"
        )

    if isinstance(obj_t, InterfaceType):
        if expr.property in obj_t.methods:
            return obj_t.methods[expr.property]
        raise TypeError(
            message=f"Interface '{obj_t.name}' has no method '{expr.property}'",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W3002"
        )

    if isinstance(obj_t, TypeParamType):
        constraint = obj_t.constraint
        if isinstance(constraint, InterfaceType) and expr.property in constraint.methods:
            checker.type_relations.record_requirement(
                "interface_member", obj_t, (constraint.name, expr.property),
                file=checker.file, line=expr.line, col=expr.col
            )
            return constraint.methods[expr.property]
        raise TypeError(
            message=f"Generic type parameter '{obj_t.name}' has no declared member '{expr.property}'",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W3002"
        )

    if isinstance(obj_t, StructType):
        if expr.property not in obj_t.fields:
            raise TypeError(
                message=f"Struct '{obj_t.name}' has no field '{expr.property}'",
                line=expr.line,
                col=expr.col,
                file=checker.file,
                code="W3002"
            )
        return obj_t.fields[expr.property]

    if isinstance(obj_t, ListType):
        if expr.property == "append":
            return FunctionType([obj_t.element], UnitType, is_mutating=True)
        if expr.property == "pop":
            return FunctionType([], obj_t.element, is_mutating=True)
        if expr.property == "clear":
            return FunctionType([], UnitType, is_mutating=True)

    if isinstance(obj_t, DictType):
        if expr.property == "get":
            return FunctionType([obj_t.key], OptionalType(obj_t.value))
        if expr.property == "keys":
            return FunctionType([], ListType(obj_t.key))
        if expr.property == "values":
            return FunctionType([], ListType(obj_t.value))

    if isinstance(obj_t, ModuleType):
        sym = obj_t.scope.lookup_symbol(expr.property)
        if sym is not None:
            expr.resolved_module_member = sym.name
            expr.resolved_symbol = sym
            if sym.kind == 'fn' and isinstance(sym.type, FunctionType):
                expr.function_reference = sym.name
            return sym.type
        t_def = obj_t.scope.lookup_type(expr.property)
        if t_def is not None:
            return t_def
        raise TypeError(
            message=f"Module '{obj_t.name}' has no exported member '{expr.property}'",
            line=expr.line,
            col=expr.col,
            file=checker.file,
            code="W1001"
        )

    if isinstance(obj_t, AnyType):
        return AnyType()

    raise TypeError(
        message=f"Cannot access property '{expr.property}' on type '{obj_t}'",
        line=expr.line,
        col=expr.col,
        file=checker.file,
        code="W3002"
    )


def check_index_expr(checker: "TypeChecker", expr: IndexExpr, is_statement_context: bool = False) -> WysnaType:
    obj_t = checker._check_expr(expr.object)
    idx_t = checker._check_expr(expr.index)
    if isinstance(obj_t, ListType):
        if idx_t != IntType:
            raise TypeError(
                message=f"List index must be int, found '{idx_t}'",
                line=expr.index.line,
                col=expr.index.col,
                file=checker.file,
                code="W2001"
            )
        return obj_t.element

    if obj_t == StrType:
        if idx_t != IntType:
            raise TypeError(
                message=f"String index must be int, found '{idx_t}'",
                line=expr.index.line,
                col=expr.index.col,
                file=checker.file,
                code="W2001"
            )
        return StrType
    if isinstance(obj_t, DictType):
        if not checker.type_relations.is_assignable(idx_t, obj_t.key):
            raise TypeError(
                message=f"Dict key type mismatch: expected '{obj_t.key}', found '{idx_t}'",
                line=expr.index.line,
                col=expr.index.col,
                file=checker.file,
                code="W2001"
            )
        # `d[k]` yields V and panics with W4002 when the key is absent (SPEC
        # 10.2). The optional form is `d.get(k)`, which returns V?. Returning
        # V? here forced every dictionary read through unwrapping ceremony and
        # made `d[k].append(x)` impossible to write.
        return obj_t.value
    raise TypeError(
        message=f"Cannot index type '{obj_t}'",
        line=expr.line,
        col=expr.col,
        file=checker.file,
        code="W2001"
    )
