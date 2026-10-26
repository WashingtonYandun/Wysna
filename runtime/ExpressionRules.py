"""Interpreter expression rules; mutable state belongs to the invoking context."""
from typing import TYPE_CHECKING

from error.Error import WysnaRuntimeError
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
from runtime.Values import (
    WysnaValue,
    WysnaInt,
    WysnaFloat,
    WysnaBool,
    WysnaStr,
    WysnaUnit,
    WysnaNone,
    WysnaList,
    WysnaDict,
    WysnaTuple,
    WysnaStructInstance,
    WysnaEnumValue,
    WysnaOk,
    WysnaErr,
    WysnaFunction,
    WysnaNativeFunction,
    WysnaBoundMethod,
    WysnaClassDef,
    WysnaObjectInstance,
    WysnaSuperProxy,
    WysnaEnumNamespace,
    WysnaModule,
)
from runtime.Environment import Environment, PropagationSignal

if TYPE_CHECKING:
    from runtime.Interpreter import Interpreter


def evaluate_lambda_expr(interpreter: "Interpreter", expr: LambdaExpr, env: Environment) -> WysnaValue:
    original = interpreter.global_env.lookup(expr.function_name)
    closure = WysnaFunction(original.decl, interpreter.global_env)
    closure.captured_environment = interpreter.eval_expr(expr.environment, env).clone_value()
    return closure


def evaluate_int_literal(interpreter: "Interpreter", expr: IntLiteral, env: Environment) -> WysnaValue:
    return WysnaInt(expr.value)


def evaluate_float_literal(interpreter: "Interpreter", expr: FloatLiteral, env: Environment) -> WysnaValue:
    return WysnaFloat(expr.value)


def evaluate_string_literal(interpreter: "Interpreter", expr: StringLiteral, env: Environment) -> WysnaValue:
    return WysnaStr(expr.value)


def evaluate_bool_literal(interpreter: "Interpreter", expr: BoolLiteral, env: Environment) -> WysnaValue:
    return WysnaBool(expr.value)


def evaluate_none_literal(interpreter: "Interpreter", expr: NoneLiteral, env: Environment) -> WysnaValue:
    return WysnaNone()


def evaluate_ident_expr(interpreter: "Interpreter", expr: IdentExpr, env: Environment) -> WysnaValue:
    if hasattr(expr, "resolved_enum_name"):
        return WysnaEnumValue(expr.resolved_enum_name, expr.name)
    return env.lookup(expr.name)


def evaluate_unary_expr(interpreter: "Interpreter", expr: UnaryExpr, env: Environment) -> WysnaValue:
    operand_val = interpreter.eval_expr(expr.operand, env)
    if expr.op == "-":
        if isinstance(operand_val, WysnaInt):
            return WysnaInt(-operand_val.value)
        if isinstance(operand_val, WysnaFloat):
            return WysnaFloat(-operand_val.value)
    if expr.op == "not":
        if isinstance(operand_val, WysnaBool):
            return WysnaBool(not operand_val.value)
    raise WysnaRuntimeError(
        message=f"Invalid unary operator '{expr.op}' on '{operand_val}'",
        line=expr.line,
        col=expr.col,
        file=interpreter.file
    )


def evaluate_binary_expr(interpreter: "Interpreter", expr: BinaryExpr, env: Environment) -> WysnaValue:
    # Short-circuit logical operators
    if expr.op == "and":
        lhs = interpreter.eval_expr(expr.left, env)
        if not isinstance(lhs, WysnaBool):
            raise WysnaRuntimeError("Expected bool operand for 'and'", line=expr.line, col=expr.col, file=interpreter.file)
        if not lhs.value:
            return WysnaBool(False)
        rhs = interpreter.eval_expr(expr.right, env)
        if not isinstance(rhs, WysnaBool):
            raise WysnaRuntimeError("Expected bool operand for 'and'", line=expr.line, col=expr.col, file=interpreter.file)
        return WysnaBool(rhs.value)

    if expr.op == "or":
        lhs = interpreter.eval_expr(expr.left, env)
        if not isinstance(lhs, WysnaBool):
            raise WysnaRuntimeError("Expected bool operand for 'or'", line=expr.line, col=expr.col, file=interpreter.file)
        if lhs.value:
            return WysnaBool(True)
        rhs = interpreter.eval_expr(expr.right, env)
        if not isinstance(rhs, WysnaBool):
            raise WysnaRuntimeError("Expected bool operand for 'or'", line=expr.line, col=expr.col, file=interpreter.file)
        return WysnaBool(rhs.value)

    lhs = interpreter.eval_value(expr.left, env)
    rhs = interpreter.eval_value(expr.right, env)

    if expr.op == "+":
        if isinstance(lhs, WysnaInt) and isinstance(rhs, WysnaInt):
            return WysnaInt(lhs.value + rhs.value)
        if isinstance(lhs, WysnaFloat) and isinstance(rhs, WysnaFloat):
            return WysnaFloat(lhs.value + rhs.value)
        if isinstance(lhs, WysnaStr) and isinstance(rhs, WysnaStr):
            return WysnaStr(lhs.value + rhs.value)

    if expr.op == "-":
        if isinstance(lhs, WysnaInt) and isinstance(rhs, WysnaInt):
            return WysnaInt(lhs.value - rhs.value)
        if isinstance(lhs, WysnaFloat) and isinstance(rhs, WysnaFloat):
            return WysnaFloat(lhs.value - rhs.value)

    if expr.op == "*":
        if isinstance(lhs, WysnaInt) and isinstance(rhs, WysnaInt):
            return WysnaInt(lhs.value * rhs.value)
        if isinstance(lhs, WysnaFloat) and isinstance(rhs, WysnaFloat):
            return WysnaFloat(lhs.value * rhs.value)

    if expr.op == "/":
        if isinstance(lhs, WysnaInt) and isinstance(rhs, WysnaInt):
            if rhs.value == 0:
                raise WysnaRuntimeError("Division by zero", line=expr.line, col=expr.col, file=interpreter.file, code="W4001")
            if lhs.value == -9223372036854775808 and rhs.value == -1:
                return WysnaInt(-9223372036854775808)
            q = abs(lhs.value) // abs(rhs.value)
            if (lhs.value < 0) ^ (rhs.value < 0):
                q = -q
            return WysnaInt(q)
        if isinstance(lhs, WysnaFloat) and isinstance(rhs, WysnaFloat):
            if rhs.value == 0.0:
                raise WysnaRuntimeError("Division by zero", line=expr.line, col=expr.col, file=interpreter.file, code="W4001")
            return WysnaFloat(lhs.value / rhs.value)

    if expr.op == "%":
        if isinstance(lhs, WysnaInt) and isinstance(rhs, WysnaInt):
            if rhs.value == 0:
                raise WysnaRuntimeError("Modulo by zero", line=expr.line, col=expr.col, file=interpreter.file, code="W4001")
            if lhs.value == -9223372036854775808 and rhs.value == -1:
                return WysnaInt(0)
            q = abs(lhs.value) // abs(rhs.value)
            if (lhs.value < 0) ^ (rhs.value < 0):
                q = -q
            return WysnaInt(lhs.value - q * rhs.value)

    if expr.op == "==":
        return WysnaBool(lhs == rhs)

    if expr.op == "!=":
        return WysnaBool(lhs != rhs)

    if expr.op == "<":
        return WysnaBool(lhs.value < rhs.value)

    if expr.op == "<=":
        return WysnaBool(lhs.value <= rhs.value)

    if expr.op == ">":
        return WysnaBool(lhs.value > rhs.value)

    if expr.op == ">=":
        return WysnaBool(lhs.value >= rhs.value)

    raise WysnaRuntimeError(
        message=f"Binary operator '{expr.op}' not supported for '{lhs}' and '{rhs}'",
        line=expr.line,
        col=expr.col,
        file=interpreter.file
    )


def evaluate_coalesce_expr(interpreter: "Interpreter", expr: CoalesceExpr, env: Environment) -> WysnaValue:
    lhs = interpreter.eval_expr(expr.left, env)
    if isinstance(lhs, WysnaNone) or (isinstance(lhs, WysnaEnumValue) and (lhs.enum_name == "Option" or lhs.enum_name.startswith("Option__")) and lhs.variant == "None"):
        return interpreter.eval_expr(expr.right, env)
    if isinstance(lhs, WysnaEnumValue) and (lhs.enum_name == "Option" or lhs.enum_name.startswith("Option__")) and lhs.variant == "Some":
        return lhs.payload[0] if lhs.payload else WysnaUnit()
    return lhs


def evaluate_range_expr(interpreter: "Interpreter", expr: RangeExpr, env: Environment) -> WysnaValue:
    start, stop = interpreter.range_bounds(expr, env)
    return WysnaList(elements=[WysnaInt(value) for value in range(start, stop)])


def evaluate_postfix_try_expr(interpreter: "Interpreter", expr: PostfixTryExpr, env: Environment) -> WysnaValue:
    val = interpreter.eval_expr(expr.operand, env)
    if expr.propagation_kind == "optional":
        if isinstance(val, WysnaNone):
            raise PropagationSignal(WysnaNone())
        if isinstance(val, WysnaEnumValue) and val.enum_name.startswith("Option"):
            if val.variant == "None":
                raise PropagationSignal(WysnaNone())
            if val.variant == "Some":
                return val.payload[0]
        return val
    if isinstance(val, WysnaNone):
        raise PropagationSignal(WysnaNone())
    if isinstance(val, WysnaEnumValue) and (val.enum_name == "Option" or val.enum_name.startswith("Option__")) and val.variant == "None":
        raise PropagationSignal(WysnaNone())
    if isinstance(val, WysnaErr):
        raise PropagationSignal(val, is_error=True)
    if isinstance(val, WysnaEnumValue) and (val.enum_name == "Result" or val.enum_name.startswith("Result__")) and val.variant == "Err":
        err_val = val.payload[0] if val.payload else WysnaNone()
        raise PropagationSignal(WysnaErr(err_val), is_error=True)
    if isinstance(val, WysnaOk):
        return val.value
    if isinstance(val, WysnaEnumValue) and (val.enum_name == "Result" or val.enum_name.startswith("Result__")) and val.variant == "Ok":
        return val.payload[0] if val.payload else WysnaUnit()
    if isinstance(val, WysnaEnumValue) and (val.enum_name == "Option" or val.enum_name.startswith("Option__")) and val.variant == "Some":
        return val.payload[0] if val.payload else WysnaUnit()
    return val


def evaluate_else_err_expr(interpreter: "Interpreter", expr: ElseErrExpr, env: Environment) -> WysnaValue:
    val = interpreter.eval_expr(expr.operand, env)
    if isinstance(val, WysnaOk):
        return val.value
    if isinstance(val, WysnaEnumValue) and (val.enum_name == "Result" or val.enum_name.startswith("Result__")) and val.variant == "Ok":
        return val.payload[0] if val.payload else WysnaUnit()
    if isinstance(val, WysnaErr):
        handler_env = Environment(parent=env)
        if expr.err_name is not None:
            handler_env.declare(expr.err_name, val.error, is_mut=False)
        return interpreter.eval_block(expr.handler, handler_env)
    if isinstance(val, WysnaEnumValue) and (val.enum_name == "Result" or val.enum_name.startswith("Result__")) and val.variant == "Err":
        handler_env = Environment(parent=env)
        if expr.err_name is not None:
            err_val = val.payload[0] if val.payload else WysnaNone()
            handler_env.declare(expr.err_name, err_val, is_mut=False)
        return interpreter.eval_block(expr.handler, handler_env)
    if isinstance(val, WysnaNone) or (isinstance(val, WysnaEnumValue) and (val.enum_name == "Option" or val.enum_name.startswith("Option__")) and val.variant == "None"):
        handler_env = Environment(parent=env)
        return interpreter.eval_block(expr.handler, handler_env)
    return val


def evaluate_this_expr(interpreter: "Interpreter", expr: ThisExpr, env: Environment) -> WysnaValue:
    return env.lookup("this")


def evaluate_super_expr(interpreter: "Interpreter", expr: SuperExpr, env: Environment) -> WysnaValue:
    return env.lookup("super")


def evaluate_new_expr(interpreter: "Interpreter", expr: NewExpr, env: Environment) -> WysnaValue:
    return interpreter.instantiate_class(expr.class_name, [interpreter.eval_value(a, env) for a in expr.args], env, expr.line, expr.col)


def evaluate_assignment_expr(interpreter: "Interpreter", expr: AssignmentExpr, env: Environment) -> WysnaValue:
    target = expr.target
    prepared, indices = interpreter.prepare_assignment(target, env)
    val = interpreter.eval_value(expr.value, env)
    target_env = Environment(parent=env)
    for name, value in indices.items():
        target_env.declare(name, value)
    target = prepared
    env = target_env

    if isinstance(target, IdentExpr):
        env.assign(target.name, val)
        return val

    if isinstance(target, IndexExpr):
        obj = interpreter.eval_mutable_lvalue(target.object, env)
        idx = interpreter.eval_expr(target.index, env)
        if isinstance(obj, WysnaList) and isinstance(idx, WysnaInt):
            if idx.value < 0 or idx.value >= len(obj):
                raise WysnaRuntimeError(f"List index out of bounds: {idx.value}", line=target.line, col=target.col, file=interpreter.file)
            obj.set(idx.value, val)
            return val
        if isinstance(obj, WysnaDict):
            key_raw = idx
            obj.set(key_raw, val)
            return val
        raise WysnaRuntimeError(f"Cannot assign to index of '{type(obj).__name__}'", line=target.line, col=target.col, file=interpreter.file)

    if isinstance(target, MemberExpr):
        obj = interpreter.eval_mutable_lvalue(target.object, env)
        if isinstance(obj, WysnaObjectInstance):
            obj.set_field(target.property, val)
            return val
        if isinstance(obj, WysnaStructInstance):
            obj.set_field(target.property, val)
            return val
        raise WysnaRuntimeError(f"Cannot assign to property of '{type(obj).__name__}'", line=target.line, col=target.col, file=interpreter.file)

    raise WysnaRuntimeError("Invalid assignment target", line=expr.line, col=expr.col, file=interpreter.file)


def evaluate_member_expr(interpreter: "Interpreter", expr: MemberExpr, env: Environment) -> WysnaValue:
    if expr.property in ("append", "pop", "clear"):
        obj = interpreter.eval_mutable_lvalue(expr.object, env)
    else:
        obj = interpreter.eval_expr(expr.object, env)
    if isinstance(obj, WysnaEnumNamespace):
        return obj.get_variant(expr.property)
    if isinstance(obj, WysnaSuperProxy):
        return WysnaBoundMethod(receiver=obj.receiver, method_name=expr.property, target_class_name=obj.superclass_name)
    if isinstance(obj, WysnaObjectInstance):
        if expr.property in obj.fields:
            return obj.get_field(expr.property)
        return WysnaBoundMethod(receiver=obj, method_name=expr.property, target_class_name=obj.class_name)
    if isinstance(obj, WysnaStructInstance):
        return obj.get_field(expr.property)
    if isinstance(obj, (WysnaList, WysnaDict)):
        return WysnaBoundMethod(receiver=obj, method_name=expr.property)
    if isinstance(obj, WysnaModule):
        return obj.get_member(expr.property)
    raise WysnaRuntimeError(f"Cannot access property '{expr.property}' on '{obj}'", line=expr.line, col=expr.col, file=interpreter.file)


def evaluate_index_expr(interpreter: "Interpreter", expr: IndexExpr, env: Environment) -> WysnaValue:
    obj = interpreter.eval_value(expr.object, env)
    idx = interpreter.eval_value(expr.index, env)

    if isinstance(obj, WysnaList):
        if not isinstance(idx, WysnaInt):
            raise WysnaRuntimeError("List index must be int", line=expr.line, col=expr.col, file=interpreter.file)
        if idx.value < 0 or idx.value >= len(obj):
            raise WysnaRuntimeError(f"List index out of bounds: index {idx.value}, length {len(obj)}", line=expr.line, col=expr.col, file=interpreter.file)
        return obj.get(idx.value)

    if isinstance(obj, WysnaStr):
        if not isinstance(idx, WysnaInt):
            raise WysnaRuntimeError("String index must be int", line=expr.line, col=expr.col, file=interpreter.file)
        if idx.value < 0 or idx.value >= len(obj.value):
            raise WysnaRuntimeError(f"String index out of bounds: index {idx.value}, length {len(obj.value)}", line=expr.line, col=expr.col, file=interpreter.file)
        return WysnaStr(obj.value[idx.value])

    if isinstance(obj, WysnaDict):
        key_raw = idx
        res = obj.get(key_raw)
        # SPEC 10.2: a missing key is W4002. `d.get(k)` is the optional form.
        if res is None:
            raise WysnaRuntimeError(
                f"Dictionary key not found: {idx}",
                line=expr.line, col=expr.col, file=interpreter.file)
        return res

    raise WysnaRuntimeError(f"Cannot index type '{type(obj).__name__}'", line=expr.line, col=expr.col, file=interpreter.file)


def evaluate_call_expr(interpreter: "Interpreter", expr: CallExpr, env: Environment) -> WysnaValue:
    from checker.WysnaType import ListType
    mutable_collection = (isinstance(expr.callee, MemberExpr)
                          and isinstance(getattr(expr.callee.object, 'inferred_type', None), ListType)
                          and expr.callee.property in ('append', 'pop', 'clear'))
    if mutable_collection:
        prepared, held = interpreter.prepare_assignment(expr.callee.object, env)
        target_env = Environment(parent=env)
        for name, value in held.items():
            target_env.declare(name, value)
        args = [interpreter.eval_value(a, env) for a in expr.args]
        receiver = interpreter.eval_mutable_lvalue(prepared, target_env)
        callee = WysnaBoundMethod(receiver, expr.callee.property)
    else:
        callee = interpreter.eval_expr(expr.callee, env)
        args = [interpreter.eval_value(a, env) for a in expr.args]

    if isinstance(callee, WysnaEnumValue):
        return WysnaEnumValue(callee.enum_name, callee.variant, args)
    if isinstance(callee, WysnaClassDef):
        return interpreter.instantiate_class(callee.name, args, env, expr.line, expr.col)
    if isinstance(callee, WysnaNativeFunction):
        return callee.call_fn(args)
    if isinstance(callee, WysnaFunction):
        return interpreter.call_function(callee, args)
    if isinstance(callee, WysnaBoundMethod):
        return interpreter.call_bound_method(callee, args)

    raise WysnaRuntimeError(f"Cannot call non-function '{callee}'", line=expr.line, col=expr.col, file=interpreter.file)


def evaluate_struct_init_expr(interpreter: "Interpreter", expr: StructInitExpr, env: Environment) -> WysnaValue:
    field_dict = {}
    for f in expr.fields:
        field_dict[f.name] = interpreter.eval_value(f.value, env)
    definition = getattr(expr, "inferred_type", None)
    field_order = getattr(definition, "fields", field_dict)
    ordered = {name: field_dict[name] for name in field_order}
    return WysnaStructInstance(expr.struct_name, fields=ordered)


def evaluate_list_expr(interpreter: "Interpreter", expr: ListExpr, env: Environment) -> WysnaValue:
    return WysnaList(elements=[interpreter.eval_value(e, env) for e in expr.elements])


def evaluate_dict_expr(interpreter: "Interpreter", expr: DictExpr, env: Environment) -> WysnaValue:
    pairs = {}
    for entry in expr.entries:
        k = interpreter.eval_value(entry.key, env)
        v = interpreter.eval_value(entry.value, env)
        key_raw = k
        pairs[key_raw] = v
    return WysnaDict(pairs=pairs)


def evaluate_tuple_expr(interpreter: "Interpreter", expr: TupleExpr, env: Environment) -> WysnaValue:
    return WysnaTuple(elements=[interpreter.eval_value(e, env) for e in expr.elements])


def evaluate_if_expr(interpreter: "Interpreter", expr: IfExpr, env: Environment) -> WysnaValue:
    cond_val = interpreter.eval_expr(expr.condition, env)
    if not isinstance(cond_val, WysnaBool):
        raise WysnaRuntimeError("Condition of 'if' must be bool", line=expr.line, col=expr.col, file=interpreter.file)
    if cond_val.value:
        return interpreter.eval_block(expr.then_branch, env)

    for elif_b in expr.elif_branches:
        elif_cond = interpreter.eval_expr(elif_b.condition, env)
        if not isinstance(elif_cond, WysnaBool):
            raise WysnaRuntimeError("Condition of 'elif' must be bool", line=elif_b.line, col=elif_b.col, file=interpreter.file)
        if elif_cond.value:
            return interpreter.eval_block(elif_b.body, env)

    if expr.else_branch is not None:
        return interpreter.eval_block(expr.else_branch, env)
    return WysnaUnit()


def evaluate_match_expr(interpreter: "Interpreter", expr: MatchExpr, env: Environment) -> WysnaValue:
    subj_val = interpreter.eval_expr(expr.subject, env)
    for arm in expr.arms:
        matched, arm_bindings = interpreter.match_pattern(arm.pattern, subj_val)
        if matched:
            arm_env = Environment(parent=env)
            for k, v in arm_bindings.items():
                arm_env.declare(k, v, is_mut=False)
            if arm.guard is not None and not interpreter.eval_expr(arm.guard, arm_env).value:
                continue
            return interpreter.eval_block(arm.body, arm_env)
    # No arm matched. The checker requires a `_ =>` fallback whenever a match
    # produces a value, so reaching this point means it is a statement-position
    # match, where the compiled backends simply fall through. Panicking here made
    # the interpreter disagree with every other engine on valid programs.
    return WysnaUnit()


from common.ExpressionDispatch import ExpressionDispatch


EXPRESSION_RULES = ExpressionDispatch('runtime', {
    LambdaExpr: evaluate_lambda_expr,
    IntLiteral: evaluate_int_literal,
    FloatLiteral: evaluate_float_literal,
    StringLiteral: evaluate_string_literal,
    BoolLiteral: evaluate_bool_literal,
    NoneLiteral: evaluate_none_literal,
    IdentExpr: evaluate_ident_expr,
    UnaryExpr: evaluate_unary_expr,
    BinaryExpr: evaluate_binary_expr,
    CoalesceExpr: evaluate_coalesce_expr,
    RangeExpr: evaluate_range_expr,
    PostfixTryExpr: evaluate_postfix_try_expr,
    ElseErrExpr: evaluate_else_err_expr,
    ThisExpr: evaluate_this_expr,
    SuperExpr: evaluate_super_expr,
    NewExpr: evaluate_new_expr,
    AssignmentExpr: evaluate_assignment_expr,
    MemberExpr: evaluate_member_expr,
    IndexExpr: evaluate_index_expr,
    CallExpr: evaluate_call_expr,
    StructInitExpr: evaluate_struct_init_expr,
    ListExpr: evaluate_list_expr,
    DictExpr: evaluate_dict_expr,
    TupleExpr: evaluate_tuple_expr,
    IfExpr: evaluate_if_expr,
    MatchExpr: evaluate_match_expr,
})
