from common.ExpressionDispatch import reject_unknown_node
from runtime.ExpressionRules import EXPRESSION_RULES
import sys
from typing import Any, Dict, List, Optional, TextIO, Tuple
from error.Error import WysnaRuntimeError
from checker.WysnaType import IntType, FloatType, BoolType, StrType, StructType, EnumType
from node.NodeType import (
    LambdaExpr, Program, Stmt, Expr, ExprStmt, LetDecl, FnDecl, StructDecl, EnumDecl, ImportDecl,
    InterfaceDecl, ClassDecl, MethodDecl,
    WhileStmt, ForStmt, ReturnStmt, BreakStmt, ContinueStmt, Block,
    IntLiteral, FloatLiteral, StringLiteral, BoolLiteral, NoneLiteral, IdentExpr,
    ThisExpr, SuperExpr, NewExpr,
    UnaryExpr, BinaryExpr, CoalesceExpr, RangeExpr, PostfixTryExpr, ElseErrExpr, CallExpr,
    MemberExpr, IndexExpr, AssignmentExpr, StructInitExpr, ListExpr, DictExpr, TupleExpr,
    IfExpr, MatchExpr, Pattern, IdentifierPattern, TuplePattern, EnumPattern, ErrorTypePattern,
    LiteralPattern, WildcardPattern,
    GenericType, NamedType, OptionalType as AstOptionalType, ListType as AstListType, DictType as AstDictType
)
from runtime.Values import (
    WysnaValue, WysnaInt, WysnaFloat, WysnaBool, WysnaStr, WysnaUnit, WysnaNone,
    WysnaList, WysnaDict, WysnaTuple, WysnaRange, WysnaStructInstance, WysnaEnumValue,
    WysnaResult, WysnaOk, WysnaErr, WysnaFunction, WysnaNativeFunction, WysnaBoundMethod,
    WysnaClassDef, WysnaObjectInstance, WysnaSuperProxy,
    WysnaEnumNamespace, WysnaModule, WysnaWeak
)

from runtime.Environment import (
    Environment, ReturnSignal, BreakSignal, ContinueSignal,
    PropagationSignal, create_global_environment
)
from runtime.ExecutionContext import ExecutionContext

class Interpreter:
    """
    Canonical Tree-Walking Interpreter for Wysna Seed.
    Executes AST programs preserving strict value semantics (ARC + CoW)
    for structs/collections and reference semantics for class instances.
    """
    def __init__(self, stdout: Optional[TextIO] = None, file: str = "<stdin>",
                 execution_context: Optional[ExecutionContext] = None):
        self.file = file
        self.execution_context = execution_context or ExecutionContext.from_process()
        self.stdout = stdout if stdout is not None else self.execution_context.stdout
        if stdout is not None and execution_context is not None:
            from dataclasses import replace
            self.execution_context = replace(execution_context, stdout=stdout)
        elif stdout is not None:
            self.execution_context = ExecutionContext.from_process(stdout=stdout)
        self.global_env = create_global_environment(
            stdout=self.stdout,
            execution_context=self.execution_context,
        )

    def interpret(self, program: Program) -> WysnaValue:
        if not hasattr(program, "session") and any(isinstance(node, ImportDecl) for node in program.body):
            from checker.TypeChecker import TypeChecker
            TypeChecker(file=self.file).check(program)
        if hasattr(program, "session"):
            program = program.session.link(program)
        # Pass 1: Register top-level enums, classes, structs, and functions
        for stmt in program.body:
            if isinstance(stmt, EnumDecl):
                self.global_env.declare(
                    stmt.name,
                    WysnaEnumNamespace(stmt.name, stmt.variants),
                    is_mut=False
                )
                if not (stmt.name.startswith("Option__") or stmt.name.startswith("Result__")):
                    for v in stmt.variants:
                        v_name = v.name if hasattr(v, "name") else str(v)
                        self.global_env.declare(
                            v_name,
                            WysnaEnumValue(stmt.name, v_name),
                            is_mut=False
                        )
            elif isinstance(stmt, ClassDecl):
                methods_map = {m.name: m for m in stmt.methods}
                self.global_env.declare(
                    stmt.name,
                    WysnaClassDef(
                        name=stmt.name,
                        superclass_name=stmt.superclass,
                        fields=stmt.fields,
                        methods=methods_map,
                        closure_env=self.global_env
                    ),
                    is_mut=False
                )
            elif isinstance(stmt, FnDecl):
                if hasattr(stmt, "extern_abi"):
                    from runtime.ForeignAbi import bind_foreign
                    self.global_env.declare(
                        stmt.name,
                        WysnaNativeFunction(
                            stmt.name,
                            bind_foreign(stmt, getattr(self, "foreign_libraries", ()),
                                         execution_context=self.execution_context),
                        ),
                    )
                    continue
                self.global_env.declare(
                    stmt.name,
                    WysnaFunction(decl=stmt, closure_env=self.global_env),
                    is_mut=False
                )
            elif isinstance(stmt, ImportDecl):
                self.eval_import_decl(stmt, self.global_env)

        # Pass 2: Execute statements
        non_decls = [s for s in program.body if not isinstance(s, (FnDecl, StructDecl, EnumDecl, InterfaceDecl, ClassDecl, ImportDecl))]
        last_val: WysnaValue = WysnaUnit()

        if non_decls:
            for stmt in program.body:
                if not isinstance(stmt, (FnDecl, StructDecl, EnumDecl, InterfaceDecl, ClassDecl)):
                    last_val = self.eval_stmt(stmt, self.global_env)
        if getattr(program, "call_entrypoint", not non_decls) and self.global_env.exists("main"):
            main_fn = self.global_env.lookup("main")
            if isinstance(main_fn, WysnaFunction):
                last_val = self.call_function(main_fn, [])

        # A program whose module body ends in `main()` returns the entrypoint
        # Result through that expression statement. Wysna has no stack unwinding, so
        # an unhandled `Err` must still reach the process exit status: discarding
        # it would make a failing program indistinguishable from a success.
        if isinstance(last_val, WysnaErr):
            raise WysnaRuntimeError(
                message=f"unhandled error returned by main: {self._entrypoint_error_text(last_val.error)}",
                file=self.file,
                code="W4003",
            )

        return last_val

    @staticmethod
    def _entrypoint_error_text(error: WysnaValue) -> str:
        """Renders an unhandled entrypoint error the way the native runtime does."""
        if isinstance(error, WysnaStructInstance):
            inner = error.storage.data.get("message")
            if isinstance(inner, WysnaStr):
                return inner.value
        return str(error)

    # ==========================================
    # Statement Dispatch
    # ==========================================

    def eval_stmt(self, stmt: Stmt, env: Environment) -> WysnaValue:
        if isinstance(stmt, ExprStmt):
            return self.eval_expr(stmt.expr, env)

        if isinstance(stmt, LetDecl):
            val = self.eval_expr(stmt.initializer, env)
            env.declare(stmt.name, val, is_mut=stmt.is_mut)
            return val

        if isinstance(stmt, WhileStmt):
            while True:
                cond_val = self.eval_expr(stmt.condition, env)
                if not isinstance(cond_val, WysnaBool) or not cond_val.value:
                    break
                try:
                    self.eval_block(stmt.body, env)
                except ContinueSignal:
                    continue
                except BreakSignal:
                    break
            return WysnaUnit()

        if isinstance(stmt, ForStmt):
            if isinstance(stmt.iterable, RangeExpr):
                start, stop = self.range_bounds(stmt.iterable, env)
                elements = (WysnaInt(value) for value in range(start, stop))
                iter_val = None
            else:
                iter_val = self.eval_expr(stmt.iterable, env)
            if isinstance(iter_val, WysnaList):
                elements = list(iter_val.storage.data)
            elif isinstance(iter_val, WysnaRange):
                elements = iter(iter_val)
            elif iter_val is not None:
                raise WysnaRuntimeError(
                    message=f"Cannot iterate over type '{type(iter_val).__name__}'",
                    line=stmt.line,
                    col=stmt.col,
                    file=self.file
                )

            for item in elements:
                loop_env = Environment(parent=env)
                self.bind_pattern(stmt.pattern, item, loop_env)
                try:
                    self.eval_block(stmt.body, loop_env)
                except ContinueSignal:
                    continue
                except BreakSignal:
                    break
            return WysnaUnit()

        if isinstance(stmt, ReturnStmt):
            ret_val = self.eval_expr(stmt.value, env) if stmt.value is not None else WysnaUnit()
            raise ReturnSignal(ret_val)

        if isinstance(stmt, BreakStmt):
            raise BreakSignal()

        if isinstance(stmt, ContinueStmt):
            raise ContinueSignal()

        if isinstance(stmt, ImportDecl):
            return self.eval_import_decl(stmt, env)

        if isinstance(stmt, (FnDecl, StructDecl, EnumDecl, InterfaceDecl, ClassDecl)):
            return WysnaUnit()
        reject_unknown_node(stmt, self.file, "runtime", "statement")

    def eval_block(self, block: Block, env: Environment) -> WysnaValue:
        block_env = Environment(parent=env)
        for s in block.statements:
            self.eval_stmt(s, block_env)
        if block.tail_expr is not None:
            return self.eval_expr(block.tail_expr, block_env)
        return WysnaUnit()

    def eval_import_decl(self, stmt: ImportDecl, env: Environment) -> WysnaValue:
        raise WysnaRuntimeError("Imports require a checked compilation session", file=self.file,
                            line=stmt.line, col=stmt.col)

    def bind_pattern(self, pattern: Pattern, value: WysnaValue, env: Environment) -> None:
        if isinstance(pattern, IdentifierPattern):
            env.declare(pattern.name, value, is_mut=False)
        elif isinstance(pattern, TuplePattern):
            if not isinstance(value, WysnaTuple) or len(pattern.elements) != len(value.elements):
                raise WysnaRuntimeError(
                    message="Cannot destructure tuple: length mismatch",
                    line=pattern.line,
                    col=pattern.col,
                    file=self.file
                )
            for p, elem in zip(pattern.elements, value.elements):
                self.bind_pattern(p, elem, env)
        elif isinstance(pattern, WildcardPattern):
            pass

    # ==========================================
    # Expression Dispatch
    # ==========================================

    def eval_expr(self, expr: Expr, env: Environment) -> WysnaValue:
        handler = EXPRESSION_RULES.handler_for(expr, self.file)
        value = handler(self, expr, env)
        return WysnaOk(value) if hasattr(expr, "force_result_wrap_id") else value

    def range_bounds(self, expression, env):
        start_val = self.eval_expr(expression.start, env)
        end_val = self.eval_expr(expression.end, env)
        if not isinstance(start_val, WysnaInt) or not isinstance(end_val, WysnaInt):
            raise WysnaRuntimeError("Range bounds must be integers", line=expression.line, col=expression.col, file=self.file)
        if expression.inclusive and end_val.value == 9223372036854775807:
            raise WysnaRuntimeError('Range bound overflow at INT64_MAX', file=self.file, line=expression.line, col=expression.col)
        stop = end_val.value + 1 if expression.inclusive else end_val.value
        if max(0, stop - start_val.value) > 100000000:
            raise WysnaRuntimeError('Range length exceeds maximum list capacity', file=self.file, line=expression.line, col=expression.col)
        return start_val.value, stop

    def eval_value(self, expr: Expr, env: Environment) -> WysnaValue:
        """Freeze an evaluated value before later expressions can mutate its owner."""
        return self.eval_expr(expr, env).clone_value()

    # ==========================================
    # Helper Call & OOP Methods
    # ==========================================

    def prepare_assignment(self, target, env):
        indices = {}
        def prepare(node):
            if isinstance(node, IndexExpr):
                parent = prepare(node.object)
                name = "__assignment_index_" + str(len(indices))
                indices[name] = self.eval_expr(node.index, env).clone_value()
                return IndexExpr(parent, IdentExpr(name), node.line, node.col)
            if isinstance(node, MemberExpr):
                from checker.WysnaType import ClassType
                if isinstance(getattr(node.object, 'inferred_type', None), ClassType):
                    name = "__assignment_receiver_" + str(len(indices))
                    indices[name] = self.eval_expr(node.object, env).clone_value()
                    return MemberExpr(IdentExpr(name, node.line, node.col), node.property, node.line, node.col)
                return MemberExpr(prepare(node.object), node.property, node.line, node.col)
            if isinstance(node, (IdentExpr, ThisExpr)):
                return node
            name = "__assignment_receiver_" + str(len(indices))
            indices[name] = self.eval_expr(node, env).clone_value()
            return IdentExpr(name, node.line, node.col)
        prepared = prepare(target)
        del prepare
        return prepared, indices

    def eval_mutable_lvalue(self, expr: Expr, env: Environment) -> WysnaValue:
        if isinstance(expr, IdentExpr):
            val = env.lookup(expr.name)
            if isinstance(val, (WysnaList, WysnaDict, WysnaStructInstance)):
                val.ensure_unique()
            return val

        if isinstance(expr, ThisExpr):
            return env.lookup("this")

        if isinstance(expr, IndexExpr):
            parent = self.eval_mutable_lvalue(expr.object, env)
            idx = self.eval_expr(expr.index, env)
            if isinstance(parent, WysnaList):
                if not isinstance(idx, WysnaInt):
                    raise WysnaRuntimeError("List index must be int", line=expr.line, col=expr.col, file=self.file)
                if idx.value < 0 or idx.value >= len(parent):
                    raise WysnaRuntimeError(f"List index out of bounds: index {idx.value}, length {len(parent)}", line=expr.line, col=expr.col, file=self.file)
                elem = parent.get(idx.value)
                if isinstance(elem, (WysnaList, WysnaDict, WysnaStructInstance)):
                    elem.ensure_unique()
                return elem
            if isinstance(parent, WysnaDict):
                key_raw = idx
                elem = parent.get(key_raw)
                if elem is not None and isinstance(elem, (WysnaList, WysnaDict, WysnaStructInstance)):
                    elem.ensure_unique()
                return elem if elem is not None else WysnaNone()

        if isinstance(expr, MemberExpr):
            parent = self.eval_mutable_lvalue(expr.object, env)
            if isinstance(parent, (WysnaStructInstance, WysnaObjectInstance)):
                elem = parent.get_field(expr.property)
                if isinstance(elem, (WysnaList, WysnaDict, WysnaStructInstance)):
                    elem.ensure_unique()
                return elem

        return self.eval_expr(expr, env)

    def _default_value_for_type(self, type_ann: Any) -> WysnaValue:
        if isinstance(type_ann, NamedType):
            if type_ann.name == "int":
                return WysnaInt(0)
            if type_ann.name == "float":
                return WysnaFloat(0.0)
            if type_ann.name == "bool":
                return WysnaBool(False)
            if type_ann.name == "str":
                return WysnaStr("")
        elif isinstance(type_ann, GenericType) and type_ann.name == "Weak":
            return WysnaWeak()
        elif isinstance(type_ann, AstOptionalType):
            return WysnaNone()
        elif isinstance(type_ann, AstListType):
            return WysnaList(elements=[])
        elif isinstance(type_ann, AstDictType):
            return WysnaDict(pairs={})
        return WysnaNone()

    def _resolve_class_def(self, class_name: str, env: Environment) -> WysnaClassDef:
        val = env.lookup(class_name) if env.exists(class_name) else self.global_env.lookup(class_name)
        if not isinstance(val, WysnaClassDef):
            raise WysnaRuntimeError(f"Unknown class '{class_name}'", file=self.file)
        return val

    def _find_method_in_hierarchy(self, start_class_name: str, method_name: str, env: Environment) -> Tuple[WysnaClassDef, MethodDecl]:
        curr_name: Optional[str] = start_class_name
        while curr_name is not None:
            cls_def = self._resolve_class_def(curr_name, env)
            if method_name in cls_def.methods:
                return cls_def, cls_def.methods[method_name]
            curr_name = cls_def.superclass_name
        raise WysnaRuntimeError(f"Method '{method_name}' not found in class hierarchy starting at '{start_class_name}'", file=self.file)

    def instantiate_class(self, class_name: str, args: List[WysnaValue], env: Environment, line: int = 0, col: int = 0) -> WysnaObjectInstance:
        cls_def = self._resolve_class_def(class_name, env)
        # Collect hierarchy from root ancestor down to target class
        lineage: List[WysnaClassDef] = []
        curr: Optional[WysnaClassDef] = cls_def
        while curr is not None:
            lineage.append(curr)
            curr = self._resolve_class_def(curr.superclass_name, env) if curr.superclass_name is not None else None
        lineage.reverse()

        fields_dict: Dict[str, WysnaValue] = {}
        for ancestor in lineage:
            for f in ancestor.fields:
                fields_dict[f.name] = self._default_value_for_type(f.type_ann)

        instance = WysnaObjectInstance(class_name=class_name, fields=fields_dict)

        # Invoke constructor 'init' if present in hierarchy
        curr_init_cls: Optional[WysnaClassDef] = cls_def
        while curr_init_cls is not None:
            if "init" in curr_init_cls.methods:
                self.call_class_method(curr_init_cls, curr_init_cls.methods["init"], instance, args)
                break
            curr_init_cls = self._resolve_class_def(curr_init_cls.superclass_name, env) if curr_init_cls.superclass_name is not None else None

        return instance

    def call_class_method(self, defining_cls: WysnaClassDef, method_decl: MethodDecl, receiver: WysnaObjectInstance, args: List[WysnaValue]) -> WysnaValue:
        if len(args) != len(method_decl.params):
            raise WysnaRuntimeError(
                message=f"Method '{defining_cls.name}.{method_decl.name}' expected {len(method_decl.params)} arguments, got {len(args)}",
                line=method_decl.line,
                col=method_decl.col,
                file=self.file
            )

        call_env = Environment(parent=defining_cls.closure_env, is_function_boundary=True)
        call_env.declare("this", receiver, is_mut=False)
        call_env.declare("self", receiver, is_mut=False)
        if defining_cls.superclass_name is not None:
            call_env.declare("super", WysnaSuperProxy(receiver=receiver, superclass_name=defining_cls.superclass_name), is_mut=False)

        for param, arg in zip(method_decl.params, args):
            call_env.declare(param.name, arg, is_mut=False)

        ret_val: WysnaValue
        try:
            ret_val = self.eval_block(method_decl.body, call_env)
        except ReturnSignal as ret:
            ret_val = ret.value
        except PropagationSignal as prop:
            ret_val = prop.value

        return self._wrap_result_value(ret_val, method_decl.return_type)

    def _wrap_result_value(self, ret_val: WysnaValue, ret_type: Any) -> WysnaValue:
        from node.NodeType import ResultType as AstResultType
        if ret_type is None or isinstance(ret_type, NamedType) and ret_type.name == 'unit':
            return WysnaUnit()
        if isinstance(ret_type, AstResultType) and isinstance(ret_type.ok_type, NamedType) and ret_type.ok_type.name == 'unit':
            return ret_val if isinstance(ret_val, WysnaErr) else WysnaOk(WysnaUnit())
        if not isinstance(ret_type, AstResultType) or isinstance(ret_val, WysnaResult):
            return ret_val
        return WysnaOk(ret_val)

    def call_function(self, func: WysnaFunction, args: List[WysnaValue]) -> WysnaValue:
        if hasattr(func, "captured_environment"):
            args = [func.captured_environment] + args
        if len(args) != len(func.decl.params):
            raise WysnaRuntimeError(
                message=f"Function '{func.decl.name}' expected {len(func.decl.params)} arguments, got {len(args)}",
                line=func.decl.line,
                col=func.decl.col,
                file=self.file
            )

        call_env = Environment(parent=func.closure_env, is_function_boundary=True)
        for param, arg in zip(func.decl.params, args):
            call_env.declare(param.name, arg, is_mut=False)

        ret_val: WysnaValue
        try:
            ret_val = self.eval_block(func.decl.body, call_env)
        except ReturnSignal as ret:
            ret_val = ret.value
        except PropagationSignal as prop:
            ret_val = prop.value

        return self._wrap_result_value(ret_val, func.decl.return_type)


    def call_bound_method(self, bound: WysnaBoundMethod, args: List[WysnaValue]) -> WysnaValue:
        receiver = bound.receiver
        method = bound.method_name

        if isinstance(receiver, WysnaObjectInstance):
            start_cls = bound.target_class_name if bound.target_class_name is not None else receiver.class_name
            defining_cls, method_decl = self._find_method_in_hierarchy(start_cls, method, self.global_env)
            return self.call_class_method(defining_cls, method_decl, receiver, args)

        if isinstance(receiver, WysnaList):
            if method == "append":
                if len(args) != 1:
                    raise WysnaRuntimeError("append() takes 1 argument", file=self.file)
                receiver.append(args[0])
                return WysnaUnit()
            if method == "pop":
                if len(args) == 0:
                    return receiver.pop()
                if len(args) == 1 and isinstance(args[0], WysnaInt):
                    return receiver.pop(args[0].value)
                raise WysnaRuntimeError("pop() invalid argument", file=self.file)
            if method == "clear":
                receiver.clear()
                return WysnaUnit()

        if isinstance(receiver, WysnaDict):
            if method == "get":
                if len(args) != 1:
                    raise WysnaRuntimeError("get() takes 1 argument", file=self.file)
                key_raw = args[0]
                val = receiver.get(key_raw)
                return WysnaEnumValue("Option", "Some", [val]) if val is not None else WysnaNone()
            if method == "keys":
                return WysnaList(elements=receiver.keys())
            if method == "values":
                return WysnaList(elements=receiver.values())

        raise WysnaRuntimeError(f"Method '{method}' not found on '{receiver}'", file=self.file)

    def match_pattern(self, pattern: Pattern, value: WysnaValue) -> Tuple[bool, Dict[str, WysnaValue]]:
        if isinstance(pattern, WildcardPattern):
            return True, {}

        if isinstance(pattern, LiteralPattern):
            if isinstance(pattern.literal, NoneLiteral) and (
                isinstance(value, WysnaNone) or
                (isinstance(value, WysnaEnumValue) and (value.enum_name == "Option" or value.enum_name.startswith("Option__")) and value.variant == "None")
            ):
                return True, {}
            lit_val = self.eval_expr(pattern.literal, self.global_env)
            return lit_val == value, {}

        if isinstance(pattern, ErrorTypePattern):
            if not self._matches_error_type(value, pattern.matched_type):
                return False, {}
            return self.match_pattern(pattern.binding, value)

        if isinstance(pattern, EnumPattern):
            # 1. Option cases: None and Some
            if pattern.variant in ("None", "none") and (pattern.enum_name is None or pattern.enum_name.split("__", 1)[0] == "Option"):
                if isinstance(value, WysnaNone) or (
                    isinstance(value, WysnaEnumValue) and
                    (value.enum_name == "Option" or value.enum_name.startswith("Option__")) and
                    value.variant == "None"
                ):
                    return True, {}
                return False, {}
            if pattern.variant == "Some" and (pattern.enum_name is None or pattern.enum_name.split("__", 1)[0] == "Option"):
                if isinstance(value, WysnaNone) or (
                    isinstance(value, WysnaEnumValue) and
                    (value.enum_name == "Option" or value.enum_name.startswith("Option__")) and
                    value.variant == "None"
                ):
                    return False, {}
                inner = value.payload[0] if (
                    isinstance(value, WysnaEnumValue) and
                    (value.enum_name == "Option" or value.enum_name.startswith("Option__")) and
                    value.variant == "Some" and value.payload
                ) else value
                if not pattern.args:
                    return True, {}
                if len(pattern.args) == 1:
                    return self.match_pattern(pattern.args[0], inner)
                return False, {}

            # 2. Result cases: Ok and Err
            if pattern.variant == "Ok" and (pattern.enum_name is None or pattern.enum_name.split("__", 1)[0] == "Result"):
                if isinstance(value, WysnaOk):
                    if not pattern.args:
                        return True, {}
                    return self.match_pattern(pattern.args[0], value.value)
                if isinstance(value, WysnaEnumValue) and (value.enum_name == "Result" or value.enum_name.startswith("Result__")) and value.variant == "Ok":
                    if not pattern.args:
                        return True, {}
                    if value.payload:
                        return self.match_pattern(pattern.args[0], value.payload[0])
                    return True, {}
                return False, {}
            if pattern.variant == "Err" and (pattern.enum_name is None or pattern.enum_name.split("__", 1)[0] == "Result"):
                if isinstance(value, WysnaErr):
                    if not pattern.args:
                        return True, {}
                    return self.match_pattern(pattern.args[0], value.error)
                if isinstance(value, WysnaEnumValue) and (value.enum_name == "Result" or value.enum_name.startswith("Result__")) and value.variant == "Err":
                    if not pattern.args:
                        return True, {}
                    if value.payload:
                        return self.match_pattern(pattern.args[0], value.payload[0])
                    return True, {}
                return False, {}

            # 3. Custom / Generic Enums
            if isinstance(value, WysnaEnumValue):
                if pattern.enum_name and value.enum_name != pattern.enum_name and not value.enum_name.startswith(f"{pattern.enum_name}__"):
                    return False, {}
                if value.variant != pattern.variant:
                    return False, {}
                if not pattern.args:
                    return True, {}
                if len(pattern.args) != len(value.payload):
                    return False, {}
                all_bindings: Dict[str, WysnaValue] = {}
                for p, arg_val in zip(pattern.args, value.payload):
                    matched, sub_b = self.match_pattern(p, arg_val)
                    if not matched:
                        return False, {}
                    all_bindings.update(sub_b)
                return True, all_bindings

            return False, {}

        if isinstance(pattern, IdentifierPattern):
            if pattern.name in ("None", "none") and (
                isinstance(value, WysnaNone) or
                (isinstance(value, WysnaEnumValue) and (value.enum_name == "Option" or value.enum_name.startswith("Option__")) and value.variant == "None")
            ):
                return True, {}
            return True, {pattern.name: value}

        if isinstance(pattern, TuplePattern):
            if not isinstance(value, WysnaTuple) or len(pattern.elements) != len(value.elements):
                return False, {}
            all_bindings: Dict[str, WysnaValue] = {}
            for p, elem in zip(pattern.elements, value.elements):
                matched, sub_b = self.match_pattern(p, elem)
                if not matched:
                    return False, {}
                all_bindings.update(sub_b)
            return True, all_bindings

        return False, {}

    @staticmethod
    def _matches_error_type(value: WysnaValue, error_type) -> bool:
        if error_type == IntType:
            return isinstance(value, WysnaInt)
        if error_type == FloatType:
            return isinstance(value, WysnaFloat)
        if error_type == BoolType:
            return isinstance(value, WysnaBool)
        if error_type == StrType:
            return isinstance(value, WysnaStr)
        if isinstance(error_type, StructType):
            return isinstance(value, WysnaStructInstance) and value.struct_name == error_type.name
        if isinstance(error_type, EnumType):
            return isinstance(value, WysnaEnumValue) and value.enum_name == error_type.name
        return False


def evaluate(node: Any, env: Environment) -> WysnaValue:
    """Evaluate a supported syntax fragment with an explicit environment."""
    interpreter = Interpreter()
    if isinstance(node, Program):
        return interpreter.interpret(node)
    if isinstance(node, Stmt):
        return interpreter.eval_stmt(node, env)
    if isinstance(node, Expr):
        return interpreter.eval_expr(node, env)
    reject_unknown_node(node, interpreter.file, "runtime", "node")
