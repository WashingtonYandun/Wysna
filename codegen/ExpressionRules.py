"""CCodeGen expression rules; mutable state belongs to the invoking context."""
from typing import TYPE_CHECKING
from common.ExpressionDispatch import reject_unknown_node

from typing import List
from node.NodeType import (
    LambdaExpr,
    IntLiteral,
    FloatLiteral,
    StringLiteral,
    BoolLiteral,
    NoneLiteral,
    IdentExpr,
    ThisExpr,
    SuperExpr,
    NewExpr,
    UnaryExpr,
    BinaryExpr,
    CoalesceExpr,
    RangeExpr,
    PostfixTryExpr,
    ElseErrExpr,
    CallExpr,
    MemberExpr,
    IndexExpr,
    AssignmentExpr,
    StructInitExpr,
    ListExpr,
    DictExpr,
    TupleExpr,
    IfExpr,
    MatchExpr,
)

if TYPE_CHECKING:
    from codegen.CCodeGen import CCodeGen


def emit_lambda_expr(emitter: "CCodeGen", expr: LambdaExpr, out: List[str]) -> str:
    environment = emitter.emit_expr(expr.environment, out)
    return emitter.function_value(expr.function_name, len(expr.inferred_type.params), out, emitter._own_expr(environment), True)


def emit_int_literal(emitter: "CCodeGen", expr: IntLiteral, out: List[str]) -> str:
    if expr.value == -9223372036854775808:
        return "wysna_int((-9223372036854775807LL - 1LL))"
    return f"wysna_int({expr.value}LL)"


def emit_float_literal(emitter: "CCodeGen", expr: FloatLiteral, out: List[str]) -> str:
    s = repr(expr.value)
    return f"wysna_float({s})"


def emit_string_literal(emitter: "CCodeGen", expr: StringLiteral, out: List[str]) -> str:
    if len(expr.value) <= 1:
        return f"wysna_str_n({emitter.escape_c_str(expr.value)}, {len(expr.value.encode('utf-8'))})"
    literal = emitter.new_aux("_literal")
    out.append(f"static WStringLiteral {literal} = {{0}};")
    return f"wysna_literal_str_n(&{literal}, {emitter.escape_c_str(expr.value)}, {len(expr.value.encode('utf-8'))})"


def emit_bool_literal(emitter: "CCodeGen", expr: BoolLiteral, out: List[str]) -> str:
    return "wysna_bool(true)" if expr.value else "wysna_bool(false)"


def emit_none_literal(emitter: "CCodeGen", expr: NoneLiteral, out: List[str]) -> str:
    return "wysna_none()"


def emit_this_expr(emitter: "CCodeGen", expr: ThisExpr, out: List[str]) -> str:
    return "v_self"


def emit_new_expr(emitter: "CCodeGen", expr: NewExpr, out: List[str]) -> str:
    arg_vals = [emitter.emit_value(a, out) for a in expr.args]
    return emitter._bind_call(f"wysna_new_{expr.class_name}({', '.join(arg_vals)})", out)


def emit_ident_expr(emitter: "CCodeGen", expr: IdentExpr, out: List[str]) -> str:
    if hasattr(expr, "function_reference"):
        return emitter.function_value(expr.function_reference, len(expr.inferred_type.params), out)
    if expr.name in ("None", "none") and not hasattr(expr, "resolved_symbol"):
        return "wysna_none()"
    if hasattr(expr, "resolved_enum_name"):
        en_name = expr.resolved_enum_name
        return f'wysna_enum("{en_name}", "{expr.name}")'
    return emitter.c_var(expr.name)


def emit_unary_expr(emitter: "CCodeGen", expr: UnaryExpr, out: List[str]) -> str:
    op_val = emitter.emit_expr(expr.operand, out)
    if expr.op == "-":
        return f"wysna_neg({op_val})"
    if expr.op == "not":
        return f"wysna_bool(!({op_val}).as.b)"


def emit_binary_expr(emitter: "CCodeGen", expr: BinaryExpr, out: List[str]) -> str:
    if expr.op == "and":
        lhs = emitter.emit_expr(expr.left, out)
        res = emitter.new_temp()
        emitter._declare_owned_var(res, "wysna_bool(false)", out)
        out.append(f"if (({lhs}).as.b) {{")
        emitter._push_scope("block")
        rhs_stmts: List[str] = []
        rhs = emitter.emit_expr(expr.right, rhs_stmts)
        rhs_stmts.append(f"{res} = wysna_bool(({rhs}).as.b);")
        emitter._pop_scope(rhs_stmts)
        for s in rhs_stmts:
            out.append(f"    {s}")
        out.append("}")
        return res

    if expr.op == "or":
        lhs = emitter.emit_expr(expr.left, out)
        res = emitter.new_temp()
        emitter._declare_owned_var(res, "wysna_bool(true)", out)
        out.append(f"if (!({lhs}).as.b) {{")
        emitter._push_scope("block")
        rhs_stmts = []
        rhs = emitter.emit_expr(expr.right, rhs_stmts)
        rhs_stmts.append(f"{res} = wysna_bool(({rhs}).as.b);")
        emitter._pop_scope(rhs_stmts)
        for s in rhs_stmts:
            out.append(f"    {s}")
        out.append("}")
        return res

    lhs = emitter.emit_value(expr.left, out)
    rhs = emitter.emit_expr(expr.right, out)
    op_map = {
        "+": "wysna_add",
        "-": "wysna_sub",
        "*": "wysna_mul",
        "/": "wysna_div",
        "%": "wysna_mod",
        "<": "wysna_lt",
        "<=": "wysna_le",
        ">": "wysna_gt",
        ">=": "wysna_ge",
    }
    if expr.op in op_map:
        call = f"{op_map[expr.op]}({lhs}, {rhs})"
        if expr.op == "/":
            return emitter._source_call("wysna_div_at", f"{lhs}, {rhs}", expr)
        if expr.op == "%":
            return emitter._source_call("wysna_mod_at", f"{lhs}, {rhs}", expr)
        return emitter._bind_call(call, out) if expr.op == "+" else call
    if expr.op == "==":
        return f"wysna_bool(wysna_eq({lhs}, {rhs}))"
    if expr.op == "!=":
        return f"wysna_bool(!wysna_eq({lhs}, {rhs}))"


def emit_coalesce_expr(emitter: "CCodeGen", expr: CoalesceExpr, out: List[str]) -> str:
    lhs = emitter.emit_expr(expr.left, out)
    res = emitter.new_temp()
    emitter._declare_owned_var(res, emitter._own_expr(lhs), out)
    out.append(f"if ({res}.tag == W_NONE) {{")
    emitter._push_scope("block")
    rhs_stmts = []
    rhs = emitter.emit_expr(expr.right, rhs_stmts)
    rhs_stmts.append(f"wysna_assign(&{res}, {emitter._own_expr(rhs)});")
    emitter._pop_scope(rhs_stmts)
    for s in rhs_stmts:
        out.append(f"    {s}")
    out.append("} else {")
    out.append(f"    wysna_assign(&{res}, wysna_unwrap_optional({res}));")
    out.append("}")
    return res


def emit_range_expr(emitter: "CCodeGen", expr: RangeExpr, out: List[str]) -> str:
    s_val = emitter.emit_value(expr.start, out)
    e_val = emitter.emit_expr(expr.end, out)
    inc = "true" if expr.inclusive else "false"
    return emitter._bind_call(f"wysna_make_range({s_val}, {e_val}, {inc})", out)


def emit_postfix_try_expr(emitter: "CCodeGen", expr: PostfixTryExpr, out: List[str]) -> str:
    op_val = emitter.emit_expr(expr.operand, out)
    tmp = emitter.new_temp()
    emitter._declare_owned_var(tmp, emitter._own_expr(op_val), out)

    none_cleanup: List[str] = []
    emitter._emit_cleanup_until_fn(none_cleanup)
    channel_enabled = expr.propagation_kind == "optional"
    out.append(f"if ({str(channel_enabled).lower()} && ({tmp}.tag == W_NONE)) {{")
    for cl in none_cleanup:
        out.append(f"    {cl}")
    out.append("    return wysna_none();")
    out.append("}")

    err_hold = emitter.new_aux("_err")
    err_cleanup: List[str] = []
    emitter._emit_cleanup_until_fn(err_cleanup)
    channel_enabled = expr.propagation_kind == "result"
    out.append(f"if ({str(channel_enabled).lower()} && ({tmp}.tag == W_ERR)) {{")
    out.append(f"    WVal {err_hold} = wysna_clone({tmp});")
    for cl in err_cleanup:
        out.append(f"    {cl}")
    out.append(f"    return {err_hold};")
    out.append("}")

    unwrapped = emitter.new_temp()
    helper = "wysna_unwrap_optional" if expr.propagation_kind == "optional" else "wysna_unwrap_ok"
    emitter._declare_owned_var(unwrapped, f"{helper}({tmp})", out)
    return unwrapped


def emit_else_err_expr(emitter: "CCodeGen", expr: ElseErrExpr, out: List[str]) -> str:
    op_val = emitter.emit_expr(expr.operand, out)
    tmp = emitter.new_temp()
    emitter._declare_owned_var(tmp, emitter._own_expr(op_val), out)
    res = emitter.new_temp()
    emitter._declare_owned_var(res, "wysna_unit()", out)
    out.append(f"if ({tmp}.tag == W_ERR || {tmp}.tag == W_NONE) {{")
    emitter._push_scope("block")
    handler_stmts: List[str] = []
    if expr.err_name is not None:
        emitter._declare_owned_var(f"v_{expr.err_name}", f"wysna_unwrap_err({tmp})", handler_stmts)
    h_val = emitter.emit_block_value(expr.handler, handler_stmts)
    handler_stmts.append(f"wysna_assign(&{res}, {emitter._own_expr(h_val)});")
    emitter._pop_scope(handler_stmts)
    for s in handler_stmts:
        out.append(f"    {s}")
    out.append("} else {")
    out.append(f"    wysna_assign(&{res}, wysna_unwrap_success({tmp}));")
    out.append("}")
    return res


def emit_assignment_expr(emitter: "CCodeGen", expr: AssignmentExpr, out: List[str]) -> str:
    lref = emitter.prepare_assignment_ref(expr.target, out)
    val = emitter.emit_expr(expr.value, out)
    rhs_hold = emitter.new_aux("_rhs")
    emitter._declare_owned_var(rhs_hold, emitter._own_expr(val), out)
    if isinstance(expr.target, IdentExpr):
        c_target = emitter.c_var(expr.target.name)
        out.append(f"wysna_assign(&{c_target}, wysna_move(&{rhs_hold}));")
        return c_target
    return emitter._bind_call(f"wysna_clone(wysna_assign({lref}, wysna_move(&{rhs_hold})))", out)


def emit_member_expr(emitter: "CCodeGen", expr: MemberExpr, out: List[str]) -> str:
    if isinstance(expr.object, IdentExpr):
        if expr.object.name in emitter.enum_names and not hasattr(expr.object, 'resolved_symbol'):
            if expr.object.name == "Option" and expr.property in ("None", "none"):
                return "wysna_none()"
            return f'wysna_enum("{emitter._enum_name(expr, expr.object.name)}", "{expr.property}")'
        if f"v_{expr.object.name}_{expr.property}" in emitter.global_vars:
            return f"v_{expr.object.name}_{expr.property}"
    obj_val = emitter.emit_expr(expr.object, out)
    return emitter._bind_call(f'wysna_clone(wysna_member_get({obj_val}, "{expr.property}"))', out)


def emit_index_expr(emitter: "CCodeGen", expr: IndexExpr, out: List[str]) -> str:
    obj_val = emitter.emit_value(expr.object, out)
    idx_val = emitter.emit_expr(expr.index, out)
    call = emitter._source_call("wysna_index_get_owned_at", f"{obj_val}, {idx_val}", expr)
    return emitter._bind_call(call, out)


def emit_call_expr(emitter: "CCodeGen", expr: CallExpr, out: List[str]) -> str:
    if getattr(expr, "indirect_call", False):
        return emitter.indirect_call(expr.callee, expr.args, out)
    # 1. Built-in or top-level function call
    if isinstance(expr.callee, IdentExpr):
        fn_name = expr.callee.name
        arg_vals = [emitter.emit_value(a, out) for a in expr.args]

        if fn_name == "Some" and len(arg_vals) == 1:
            return emitter._bind_call(f"wysna_enum_payload(\"Option\", \"Some\", {emitter._own_expr(arg_vals[0])})", out)
        if fn_name == "Ok" and len(arg_vals) == 1:
            return emitter._bind_call(f"wysna_ok({emitter._own_expr(arg_vals[0])})", out)
        if fn_name == "Err" and len(arg_vals) == 1:
            return emitter._bind_call(f"wysna_err({emitter._own_expr(arg_vals[0])})", out)

        if fn_name in emitter.enum_variants and not hasattr(expr.callee, 'resolved_symbol'):
            en_name = emitter.enum_variants[fn_name]
            if len(arg_vals) == 0:
                return f'wysna_enum("{en_name}", "{fn_name}")'
            elif len(arg_vals) == 1:
                return emitter._bind_call(f'wysna_enum_payload("{en_name}", "{fn_name}", {emitter._own_expr(arg_vals[0])})', out)
            else:
                tup_tmp = emitter.new_temp()
                elems_c = ", ".join(arg_vals)
                out.append(f"WVal _elems_{tup_tmp}[{len(arg_vals)}] = {{ {elems_c} }};")
                out.append(f"WVal {tup_tmp} = wysna_make_tuple({len(arg_vals)}, _elems_{tup_tmp});")
                return emitter._bind_call(f'wysna_enum_payload("{en_name}", "{fn_name}", {emitter._own_expr(tup_tmp)})', out)

        if fn_name == "join_strings":
            return emitter._bind_call(f"wysna_builtin_join_strings({arg_vals[0]}, {arg_vals[1]})", out)

        if fn_name == "print":
            if not arg_vals:
                return emitter._bind_call("wysna_builtin_print(0, NULL)", out)
            arr_tmp = emitter.new_aux("_arr")
            elems_c = ", ".join(arg_vals)
            out.append(f"WVal {arr_tmp}[{len(arg_vals)}] = {{ {elems_c} }};")
            return emitter._bind_call(f"wysna_builtin_print({len(arg_vals)}, {arr_tmp})", out)

        if fn_name == "eprint":
            if not arg_vals:
                return emitter._bind_call("wysna_builtin_eprint(0, NULL)", out)
            arr_tmp = emitter.new_aux("_arr")
            elems_c = ", ".join(arg_vals)
            out.append(f"WVal {arr_tmp}[{len(arg_vals)}] = {{ {elems_c} }};")
            return emitter._bind_call(f"wysna_builtin_eprint({len(arg_vals)}, {arr_tmp})", out)

        if fn_name == "weak" and len(arg_vals) == 1:
            return emitter._bind_call(f"wysna_builtin_weak_owned({emitter._own_expr(arg_vals[0])})", out)
        builtin_1 = {
            "weak": "wysna_builtin_weak", "upgrade": "wysna_builtin_upgrade",
            "len": "wysna_builtin_len",
            "str": "wysna_builtin_str",
            "int": "wysna_builtin_int",
            "float": "wysna_builtin_float",
            "ord": "wysna_builtin_ord",
            "chr": "wysna_builtin_chr",
            "bytes": "wysna_builtin_bytes",
            "enumerate": "wysna_builtin_enumerate",
            "dict_items": "wysna_builtin_dict_items",
            "read_file": "wysna_builtin_read_file",
            "env": "wysna_builtin_env",
        }
        if fn_name in builtin_1 and len(arg_vals) == 1:
            return emitter._bind_call(f"{builtin_1[fn_name]}({arg_vals[0]})", out)

        if fn_name == "args" and len(arg_vals) == 0:
            return emitter._bind_call("wysna_builtin_args()", out)

        if fn_name == "unix_time" and len(arg_vals) == 0:
            return emitter._bind_call("wysna_builtin_unix_time()", out)

        if fn_name == "exit" and len(arg_vals) == 1:
            return emitter._bind_call(f"wysna_builtin_exit({arg_vals[0]})", out)

        if fn_name == "write_file" and len(arg_vals) == 2:
            return emitter._bind_call(f"wysna_builtin_write_file({arg_vals[0]}, {arg_vals[1]})", out)

        if fn_name == "slice" and len(arg_vals) == 3:
            return emitter._bind_call(f"wysna_builtin_slice({arg_vals[0]}, {arg_vals[1]}, {arg_vals[2]})", out)

        if fn_name in emitter.classes:
            return emitter._bind_call(f"wysna_new_{fn_name}({', '.join(arg_vals)})", out)

        c_callee = emitter.c_fn(fn_name)
        return emitter._bind_call(f"{c_callee}({', '.join(arg_vals)})", out)

    # 2. Method call or module function call: obj.method(...)
    if isinstance(expr.callee, MemberExpr):
        obj_expr = expr.callee.object
        method = expr.callee.property

        if (isinstance(obj_expr, IdentExpr) and obj_expr.name in emitter.enum_names
                and not hasattr(obj_expr, "resolved_symbol")):
            en_name = obj_expr.name
            arg_vals = [emitter.emit_value(a, out) for a in expr.args]
            if (en_name == "Result" or en_name.startswith("Result__")) and method == "Ok" and len(arg_vals) == 1:
                return emitter._bind_call(f"wysna_ok({emitter._own_expr(arg_vals[0])})", out)
            if (en_name == "Result" or en_name.startswith("Result__")) and method == "Err" and len(arg_vals) == 1:
                return emitter._bind_call(f"wysna_err({emitter._own_expr(arg_vals[0])})", out)
            if (en_name == "Option" or en_name.startswith("Option__")) and method in ("None", "none"):
                return "wysna_none()"
            if len(arg_vals) == 0:
                return f'wysna_enum("{en_name}", "{method}")'
            elif len(arg_vals) == 1:
                return emitter._bind_call(f'wysna_enum_payload("{en_name}", "{method}", {emitter._own_expr(arg_vals[0])})', out)
            else:
                tup_tmp = emitter.new_temp()
                elems_c = ", ".join(arg_vals)
                out.append(f"WVal _elems_{tup_tmp}[{len(arg_vals)}] = {{ {elems_c} }};")
                out.append(f"WVal {tup_tmp} = wysna_make_tuple({len(arg_vals)}, _elems_{tup_tmp});")
                return emitter._bind_call(f'wysna_enum_payload("{en_name}", "{method}", {emitter._own_expr(tup_tmp)})', out)

        # super.init(...) or super.method(...)
        if isinstance(obj_expr, SuperExpr):
            arg_vals = [emitter.emit_value(a, out) for a in expr.args]
            call_args = ["v_self"] + arg_vals
            if emitter.current_class is not None and emitter.current_class.superclass is not None:
                resolved = emitter._find_class_method(emitter.current_class.superclass, method)
                if resolved is not None:
                    def_cls, _ = resolved
                    return emitter._bind_call(f"wysna_method_{def_cls}__{method}({', '.join(call_args)})", out)
            reject_unknown_node(expr, emitter.file, "codegen", "super call")

        # Check if module call: mod.fn(...)
        if isinstance(obj_expr, IdentExpr):
            mod_fn = emitter.c_fn(method, ns=obj_expr.name)
            if mod_fn in emitter.fallible_fns:
                arg_vals = [emitter.emit_value(a, out) for a in expr.args]
                return emitter._bind_call(f"{mod_fn}({', '.join(arg_vals)})", out)

        # Collection methods
        from checker.WysnaType import ListType, DictType
        receiver_type = getattr(obj_expr, "inferred_type", None)
        if method == "append" and isinstance(receiver_type, ListType):
            lref = emitter.prepare_assignment_ref(obj_expr, out)
            arg_v = emitter.emit_value(expr.args[0], out)
            return emitter._bind_call(f"wysna_list_append({lref}, {arg_v})", out)
        if method == "pop" and isinstance(receiver_type, ListType):
            lref = emitter.prepare_assignment_ref(obj_expr, out)
            call = emitter._source_call("wysna_list_pop_at", lref, expr)
            return emitter._bind_call(call, out)
        if method == "clear" and isinstance(receiver_type, ListType):
            lref = emitter.prepare_assignment_ref(obj_expr, out)
            return emitter._bind_call(f"wysna_list_clear({lref})", out)
        if method == "get" and isinstance(receiver_type, DictType):
            obj_v = emitter.emit_value(obj_expr, out)
            arg_v = emitter.emit_value(expr.args[0], out)
            return emitter._bind_call(f"wysna_dict_get({obj_v}, {arg_v})", out)
        if method == "keys" and isinstance(receiver_type, DictType):
            obj_v = emitter.emit_value(obj_expr, out)
            return emitter._bind_call(f"wysna_dict_keys({obj_v})", out)
        if method == "values" and isinstance(receiver_type, DictType):
            obj_v = emitter.emit_value(obj_expr, out)
            return emitter._bind_call(f"wysna_dict_values({obj_v})", out)

        # OOP dynamic method dispatch
        obj_v = emitter.emit_value(obj_expr, out)
        arg_vals = [emitter.emit_value(a, out) for a in expr.args]
        call_args = [obj_v] + arg_vals
        return emitter._bind_call(f"wysna_dispatch__{method}__{len(arg_vals)}({', '.join(call_args)})", out)


def emit_list_expr(emitter: "CCodeGen", expr: ListExpr, out: List[str]) -> str:
    if not expr.elements:
        return emitter._bind_call("wysna_make_list(0, NULL)", out)
    elem_vals = [emitter.emit_value(e, out) for e in expr.elements]
    arr_tmp = emitter.new_aux("_arr")
    out.append(f"WVal {arr_tmp}[{len(elem_vals)}] = {{ {', '.join(elem_vals)} }};")
    return emitter._bind_call(f"wysna_make_list({len(elem_vals)}, {arr_tmp})", out)


def emit_tuple_expr(emitter: "CCodeGen", expr: TupleExpr, out: List[str]) -> str:
    if not expr.elements:
        return emitter._bind_call("wysna_make_tuple(0, NULL)", out)
    elem_vals = [emitter.emit_value(e, out) for e in expr.elements]
    arr_tmp = emitter.new_aux("_arr")
    out.append(f"WVal {arr_tmp}[{len(elem_vals)}] = {{ {', '.join(elem_vals)} }};")
    return emitter._bind_call(f"wysna_make_tuple({len(elem_vals)}, {arr_tmp})", out)


def emit_dict_expr(emitter: "CCodeGen", expr: DictExpr, out: List[str]) -> str:
    if not expr.entries:
        return emitter._bind_call("wysna_make_dict(0, NULL, NULL)", out)
    pairs = [(emitter.emit_value(e.key, out), emitter.emit_value(e.value, out)) for e in expr.entries]
    k_vals = [key for key, _ in pairs]
    v_vals = [value for _, value in pairs]
    k_tmp = emitter.new_aux("_k")
    v_tmp = emitter.new_aux("_v")
    out.append(f"WVal {k_tmp}[{len(k_vals)}] = {{ {', '.join(k_vals)} }};")
    out.append(f"WVal {v_tmp}[{len(v_vals)}] = {{ {', '.join(v_vals)} }};")
    return emitter._bind_call(f"wysna_make_dict({len(k_vals)}, {k_tmp}, {v_tmp})", out)


def emit_struct_init_expr(emitter: "CCodeGen", expr: StructInitExpr, out: List[str]) -> str:
    if not expr.fields:
        return emitter._bind_call(f'wysna_make_struct("{expr.struct_name}", 0, NULL, NULL)', out)
    evaluated = {field.name: emitter.emit_value(field.value, out) for field in expr.fields}
    field_order = list(expr.inferred_type.fields)
    f_names = [f'"{name}"' for name in field_order]
    f_vals = [evaluated[name] for name in field_order]
    k_tmp = emitter.new_aux("_k")
    v_tmp = emitter.new_aux("_v")
    out.append(f"const char* {k_tmp}[{len(f_names)}] = {{ {', '.join(f_names)} }};")
    out.append(f"WVal {v_tmp}[{len(f_vals)}] = {{ {', '.join(f_vals)} }};")
    return emitter._bind_call(f'wysna_make_struct("{expr.struct_name}", {len(f_names)}, {k_tmp}, {v_tmp})', out)


def emit_if_expr(emitter: "CCodeGen", expr: IfExpr, out: List[str]) -> str:
    res = emitter.new_temp()
    emitter._declare_owned_var(res, "wysna_unit()", out)
    cond_v = emitter.emit_expr(expr.condition, out)
    out.append(f"if (({cond_v}).as.b) {{")
    emitter._push_scope("block")
    then_stmts: List[str] = []
    then_v = emitter.emit_block_value(expr.then_branch, then_stmts)
    then_stmts.append(f"wysna_assign(&{res}, {emitter._own_expr(then_v)});")
    emitter._pop_scope(then_stmts)
    for s in then_stmts:
        out.append(f"    {s}")
    out.append("}")

    # Handle elif and else branches with nested else blocks so elif conditions evaluate lazily
    emitter._emit_elif_chain(expr.elif_branches, 0, expr.else_branch, res, out)
    return res


def emit_match_expr(emitter: "CCodeGen", expr: MatchExpr, out: List[str]) -> str:
    subj_v = emitter.emit_expr(expr.subject, out)
    subj_tmp = emitter.new_temp()
    emitter._declare_owned_var(subj_tmp, emitter._own_expr(subj_v), out)
    res = emitter.new_temp()
    emitter._declare_owned_var(res, "wysna_unit()", out)
    emitter._emit_match_arms(expr.arms, 0, subj_tmp, res, out)
    return res


from common.ExpressionDispatch import ExpressionDispatch


# SuperExpr is a receiver marker consumed by emit_call_expr, never a standalone value.
UNSUPPORTED_EXPRESSIONS = frozenset({SuperExpr})

EXPRESSION_RULES = ExpressionDispatch('codegen', {
    LambdaExpr: emit_lambda_expr,
    IntLiteral: emit_int_literal,
    FloatLiteral: emit_float_literal,
    StringLiteral: emit_string_literal,
    BoolLiteral: emit_bool_literal,
    NoneLiteral: emit_none_literal,
    ThisExpr: emit_this_expr,
    NewExpr: emit_new_expr,
    IdentExpr: emit_ident_expr,
    UnaryExpr: emit_unary_expr,
    BinaryExpr: emit_binary_expr,
    CoalesceExpr: emit_coalesce_expr,
    RangeExpr: emit_range_expr,
    PostfixTryExpr: emit_postfix_try_expr,
    ElseErrExpr: emit_else_err_expr,
    AssignmentExpr: emit_assignment_expr,
    MemberExpr: emit_member_expr,
    IndexExpr: emit_index_expr,
    CallExpr: emit_call_expr,
    ListExpr: emit_list_expr,
    TupleExpr: emit_tuple_expr,
    DictExpr: emit_dict_expr,
    StructInitExpr: emit_struct_init_expr,
    IfExpr: emit_if_expr,
    MatchExpr: emit_match_expr,
})
