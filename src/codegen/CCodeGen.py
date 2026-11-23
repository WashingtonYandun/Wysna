from common.ExpressionDispatch import reject_unknown_node
from compiler.StdlibIdentity import stdlib_declaration
from codegen.ExpressionRules import EXPRESSION_RULES
from typing import Dict, List, Optional, Set, Tuple
from node.NodeType import (
    LambdaExpr, Program, Stmt, Expr, TypeNode, ResultType,
    GenericType, NamedType, OptionalType as AstOptionalType, ListType as AstListType, DictType as AstDictType,
    Pattern, IdentifierPattern, TuplePattern, EnumPattern, ErrorTypePattern, LiteralPattern, WildcardPattern, OrPattern, RangePattern, StructPattern,
    Block, ExprStmt, Param, FnDecl, StructDecl, EnumDecl, ImportDecl,
    InterfaceDecl, ClassDecl, ClassField, MethodDecl,
    LetDecl, WhileStmt, ForStmt, ReturnStmt, BreakStmt, ContinueStmt,
    IntLiteral, FloatLiteral, StringLiteral, BoolLiteral, NoneLiteral, IdentExpr,
    ThisExpr, SuperExpr, NewExpr,
    UnaryExpr, BinaryExpr, CoalesceExpr, RangeExpr, PostfixTryExpr, ElseErrExpr,
    CallExpr, MemberExpr, IndexExpr, AssignmentExpr, StructInitExpr,
    ListExpr, DictExpr, TupleExpr, IfExpr, MatchExpr
)
from common.Telemetry import CodegenTelemetry
from checker.WysnaType import DictType, ListType, U8Type


class CleanupScope:
    def __init__(self, kind: str) -> None:
        self.kind = kind  # "fn" | "loop" | "block"
        self.vars: List[str] = []


class CCodeGen:
    """
    Wysna Seed AST-to-C99 reference backend.
    Preserves exact value semantics (Deterministic RC + Copy-on-Write via runtime/wysna_runtime.h),
    reference semantics for OOP classes, dynamic dispatch, fallible error handling,
    and deterministic LIFO lifetime cleanup across all control-flow paths (v0.1.3).
    """
    def __init__(self, file: str = "<stdin>"):
        self.file = file
        self.function_adapters = set()
        self.temp_counter = 0
        self.aux_counter = 0
        self.enum_names: Set[str] = {"Option", "Result"}
        self.enum_variants: Dict[str, str] = {
            "Some": "Option",
            "None": "Option",
            "Ok": "Result",
            "Err": "Result",
        }
        self.classes: Dict[str, ClassDecl] = {}
        self.struct_contracts: Dict[str, StructDecl] = {}
        self.class_order: List[str] = []
        self.dispatch_methods: List[Tuple[str, int]] = []
        self.current_class: Optional[ClassDecl] = None
        self.fallible_fns: Dict[str, bool] = {}
        self.fn_err_types: Dict[str, str] = {}
        self.current_fn_fallible: bool = False
        self.current_fn_postcondition = None
        self.current_fn_err_type: str = ""
        self.current_ns: str = ""
        self.imported_modules: Set[str] = set()
        self.global_vars: List[str] = []
        self.fn_Prototypes: List[str] = []
        self.fn_Definitions: List[str] = []
        self.scope_stack: List[CleanupScope] = []
        self.telemetry: CodegenTelemetry = CodegenTelemetry()

    def new_temp(self) -> str:
        name = f"_t{self.temp_counter}"
        self.temp_counter += 1
        return name

    def new_aux(self, prefix: str = "_aux") -> str:
        name = f"{prefix}{self.aux_counter}"
        self.aux_counter += 1
        return name

    def _push_scope(self, kind: str) -> None:
        self.scope_stack.append(CleanupScope(kind))

    def _register_var(self, var_name: str) -> None:
        if self.scope_stack:
            self.scope_stack[-1].vars.append(var_name)

    def _declare_owned_var(self, var_name: str, init_c_expr: str, out: List[str]) -> str:
        out.append(f"WVal {var_name} = {init_c_expr};")
        self._register_var(var_name)
        return var_name

    def _pop_scope(self, out: List[str]) -> None:
        if not self.scope_stack:
            return
        scope = self.scope_stack.pop()
        for v in reversed(scope.vars):
            out.append(f"wysna_release({v});")

    def _emit_cleanup_until_fn(self, out: List[str]) -> None:
        for scope in reversed(self.scope_stack):
            for v in reversed(scope.vars):
                out.append(f"wysna_release({v});")
            if scope.kind == "fn":
                break

    def _emit_cleanup_until_loop(self, out: List[str]) -> None:
        for scope in reversed(self.scope_stack):
            for v in reversed(scope.vars):
                out.append(f"wysna_release({v});")
            if scope.kind == "loop":
                break

    def _snapshot(self, value: str, out: List[str]) -> str:
        """Protect an evaluated value before later operands can mutate its owner."""
        if value.startswith("_t"):
            return value
        if value.startswith(("wysna_make_", "wysna_new_", "wysna_enum")):
            return self._bind_call(value, out)
        return self._bind_call(f"wysna_clone({value})", out)

    def emit_value(self, expr, out: List[str]) -> str:
        return self._snapshot(self.emit_expr(expr, out), out)

    def _own_expr(self, val_expr: str) -> str:
        if val_expr.startswith("_t"):
            return f"wysna_move(&{val_expr})"
        if val_expr.startswith(("wysna_make_", "wysna_new_", "wysna_clone(", "wysna_enum")):
            return val_expr
        return f"wysna_clone({val_expr})"

    def _is_local_var(self, c_name: str) -> bool:
        for scope in reversed(self.scope_stack):
            if c_name in scope.vars:
                return True
        return False

    def c_var(self, name: str) -> str:
        local_name = f"v_{name}"
        if self._is_local_var(local_name):
            return local_name
        if self.current_ns and f"v_{self.current_ns}_{name}" in self.global_vars:
            return f"v_{self.current_ns}_{name}"
        return local_name

    def c_fn(self, name: str, ns: str = "") -> str:
        prefix = ns if ns else self.current_ns
        if prefix:
            return f"wysna_fn_{prefix}__{name}"
        return f"wysna_fn_{name}"

    def escape_c_str(self, value: str) -> str:
        from codegen.CStrings import quote
        return quote(value)

    def generate(self, program: Program) -> str:
        if hasattr(program, "session"):
            program = program.session.link(program)
        self.interner = getattr(program, "interner", None)
        self._collect_declarations(program, ns="")
        self._prepare_oop_prototypes()
        for declaration in self.struct_contracts.values():
            self._emit_struct_contract(declaration)

        # Emit all top-level functions, class methods, and imported module functions
        main_stmts: List[str] = []
        self._push_scope("fn")
        self._emit_module_body(program, ns="", out_main_stmts=main_stmts)

        # If there are no top-level executable statements and main() exists in Wysna, call it
        non_decls = [s for s in program.body if not isinstance(s, (FnDecl, StructDecl, EnumDecl, InterfaceDecl, ClassDecl, ImportDecl))]
        if getattr(program, "call_entrypoint", not non_decls) and "wysna_fn_main" in self.fallible_fns:
            main_stmts.append("WVal _t_main = wysna_fn_main();")
            self._emit_entrypoint_status(main_stmts)

        self._pop_scope(main_stmts)
        self._emit_oop_dispatchers()

        lines: List[str] = [
            '#include "runtime/wysna_runtime.h"',
            ""
        ]

        if self.global_vars:
            for gv in self.global_vars:
                lines.append(f"static WVal {gv};")
            lines.append("")

        if self.fn_Prototypes:
            lines.extend(self.fn_Prototypes)
            lines.append("")

        if self.fn_Definitions:
            for fd in self.fn_Definitions:
                lines.append(fd)
                lines.append("")

        lines.append("int main(int argc, char** argv) {")
        lines.append("    wysna_init_args(argc, argv);")
        lines.append("    int status = 0;")
        for s in main_stmts:
            lines.append(f"    {s}")
        for gv in reversed(self.global_vars):
            lines.append(f"    wysna_release({gv});")
        lines.append("    wysna_runtime_cleanup();")
        lines.append("    return status;")
        lines.append("}")
        c_code_str = "\n".join(lines) + "\n"
        struct_count = sum(1 for s in program.body if isinstance(s, StructDecl))
        self.telemetry = CodegenTelemetry(
            generated_functions=len(self.fn_Definitions),
            generated_structs=struct_count,
            generated_enums=len(self.enum_names),
            generated_classes=len(self.classes),
            generated_c_lines=len(c_code_str.splitlines()),
            generated_c_bytes=len(c_code_str.encode("utf-8")),
        )
        return c_code_str

    def _collect_declarations(self, program: Program, ns: str) -> None:
        for stmt in program.body:
            if isinstance(stmt, StructDecl) and any(field.requires for field in stmt.fields):
                self.struct_contracts[stmt.name] = stmt
                self.fn_Prototypes.append(f"static WVal wysna_guard_{stmt.name}(WVal record);")
            elif isinstance(stmt, EnumDecl):
                self.enum_names.add(stmt.name)
                for v in stmt.variants:
                    v_name = v.name if hasattr(v, "name") else str(v)
                    self.enum_variants[v_name] = stmt.name
            elif isinstance(stmt, ClassDecl):
                if stmt.name not in self.classes:
                    self.classes[stmt.name] = stmt
                    self.class_order.append(stmt.name)
                for m in stmt.methods:
                    if m.name != "init":
                        key = (m.name, len(m.params))
                        if key not in self.dispatch_methods:
                            self.dispatch_methods.append(key)
            elif isinstance(stmt, FnDecl):
                c_name = self.c_fn(stmt.name, ns=ns)
                is_fallible = isinstance(stmt.return_type, ResultType)
                self.fallible_fns[c_name] = is_fallible
                if is_fallible and isinstance(stmt.return_type, ResultType) and isinstance(stmt.return_type.err_type, NamedType):
                    self.fn_err_types[c_name] = stmt.return_type.err_type.name
                else:
                    self.fn_err_types[c_name] = ""
                params_sig = ", ".join([f"WVal v_{p.name}" for p in stmt.params]) if stmt.params else "void"
                self.fn_Prototypes.append(f"static WVal {c_name}({params_sig});")
            elif isinstance(stmt, LetDecl):
                gv_name = f"v_{ns}_{stmt.name}" if ns else f"v_{stmt.name}"
                if gv_name not in self.global_vars:
                    self.global_vars.append(gv_name)

    def _emit_struct_contract(self, declaration: StructDecl) -> None:
        previous_scopes = self.scope_stack
        self.scope_stack = []
        self._push_scope('fn')
        statements = []
        self._declare_owned_var('v_self', 'wysna_clone(record)', statements)
        for field in declaration.fields:
            self._declare_owned_var('v_' + field.name,
                f'wysna_clone(wysna_member_get(record, {self.escape_c_str(field.name)}))', statements)
        for field in declaration.fields:
            for requirement in field.requires:
                condition = self.emit_value(requirement, statements)
                message_text = f"Struct field contract ('requires') failed on '{declaration.name}.{field.name}'"
                message = self.emit_value(StringLiteral(message_text, requirement.line, requirement.col), statements)
                statements.append(self._source_call('wysna_builtin_contract_assert_at',
                    f'{condition}, {message}', requirement) + ';')
        self._pop_scope(statements)
        statements.append('return wysna_clone(record);')
        self.fn_Definitions.append(f'static WVal wysna_guard_{declaration.name}(WVal record) {{\n' +
            '\n'.join('    ' + statement for statement in statements) + '\n}')
        self.scope_stack = previous_scopes

    def guard_struct(self, name: str, value: str, out: List[str]) -> str:
        if name in self.struct_contracts:
            return self._bind_call(f'wysna_guard_{name}({value})', out)
        return value

    def _emit_contract_predicates(self, requirements, message, out):
        for requirement in requirements:
            condition = self.emit_value(requirement, out)
            text = self.emit_value(StringLiteral(message, requirement.line, requirement.col), out)
            out.append(self._source_call('wysna_builtin_contract_assert_at', f'{condition}, {text}', requirement) + ';')

    def _prepare_postcondition(self, declaration, c_name, parameter_names, body):
        requirements = getattr(declaration, 'ensures', ())
        if not requirements:
            return None
        name = 'wysna_ensure_' + c_name
        previous_scopes = self.scope_stack
        self.scope_stack = []
        statements = ['{']
        self._push_scope('fn')
        if self.current_fn_fallible:
            wrapped = self._bind_call(f'wysna_wrap_result(wysna_clone(returned), "{self.current_fn_err_type}")', statements)
            statements.append(f'if (wysna_tag({wrapped}) != W_ERR) {{')
            self._push_scope('block')
            self._declare_owned_var('v_result', f'wysna_clone(wysna_as_box({wrapped})->val)', statements)
        else:
            self._declare_owned_var('v_result', 'wysna_clone(returned)', statements)
        self._emit_contract_predicates(requirements, 'Function ensures clause failed', statements)
        self._pop_scope(statements)
        if self.current_fn_fallible:
            statements.append('}')
            self._pop_scope(statements)
        statements.append('}')
        self.scope_stack = previous_scopes
        signature = ', '.join(['WVal returned', *['WVal ' + param for param in parameter_names]])
        self.fn_Definitions.append(f'static void {name}({signature}) {{\n' +
            '\n'.join('    ' + statement for statement in statements) + '\n}')
        aliases = []
        for parameter in parameter_names:
            alias = self.new_aux('_ensure_arg')
            body.append(f'WVal {alias} = {parameter};')
            aliases.append(alias)
        return name, aliases

    def _emit_postconditions(self, value, out):
        if self.current_fn_postcondition is not None:
            function, arguments = self.current_fn_postcondition
            out.append(f"{function}({', '.join([value, *arguments])});")

    def _find_class_method(self, class_name: str, method_name: str) -> Optional[Tuple[str, MethodDecl]]:
        curr: Optional[str] = class_name
        while curr is not None and curr in self.classes:
            cdecl = self.classes[curr]
            for m in cdecl.methods:
                if m.name == method_name:
                    return (curr, m)
            curr = cdecl.superclass
        return None

    def _collect_class_fields(self, class_name: str) -> List[ClassField]:
        lineage: List[ClassDecl] = []
        curr: Optional[str] = class_name
        while curr is not None and curr in self.classes:
            lineage.append(self.classes[curr])
            curr = self.classes[curr].superclass
        lineage.reverse()
        fields: List[ClassField] = []
        seen: Set[str] = set()
        for cdecl in lineage:
            for f in cdecl.fields:
                if f.name not in seen:
                    seen.add(f.name)
                    fields.append(f)
        return fields

    def _default_c_expr_for_type(self, type_ann: TypeNode) -> str:
        if isinstance(type_ann, NamedType):
            if type_ann.name in ("int", "u8"):
                return "wysna_int(0LL)"
            if type_ann.name == "float":
                return "wysna_float(0.0)"
            if type_ann.name == "bool":
                return "wysna_bool(false)"
            if type_ann.name == "str":
                return 'wysna_str("")'
        elif isinstance(type_ann, GenericType) and type_ann.name == "Weak":
            return "wysna_empty_weak()"
        elif isinstance(type_ann, AstOptionalType):
            return "wysna_none()"
        elif isinstance(type_ann, AstListType):
            if getattr(type_ann.element_type, 'name', None) == 'u8':
                return "wysna_make_u8_list(0, NULL)"
            return "wysna_make_list(0, NULL)"
        elif isinstance(type_ann, AstDictType):
            return "wysna_make_dict(0, NULL, NULL)"
        return "wysna_none()"

    def _prepare_oop_prototypes(self) -> None:
        for cls_name in self.class_order:
            cdecl = self.classes[cls_name]
            self.fn_Prototypes.append(f"static const WClassVTable wysna_vtable_{cls_name};")
            for m in cdecl.methods:
                params = ["WVal v_self"] + [f"WVal v_{p.name}" for p in m.params]
                self.fn_Prototypes.append(f"static WVal wysna_method_{cls_name}__{m.name}({', '.join(params)});")
            init_res = self._find_class_method(cls_name, "init")
            if init_res is not None:
                _, init_m = init_res
                init_sig = ", ".join([f"WVal v_{p.name}" for p in init_m.params]) if init_m.params else "void"
            else:
                init_sig = "void"
            self.fn_Prototypes.append(f"static WVal wysna_new_{cls_name}({init_sig});")

        for mname, arity in self.dispatch_methods:
            params = ["WVal v_self"] + [f"WVal _a{i}" for i in range(arity)]
            self.fn_Prototypes.append(f"static WVal wysna_dispatch__{mname}__{arity}({', '.join(params)});")

    def _emit_class_decl(self, stmt: ClassDecl) -> None:
        prev_cls = self.current_class
        self.current_class = stmt
        try:
            for m in stmt.methods:
                c_name = f"wysna_method_{stmt.name}__{m.name}"
                params = ["WVal v_self"] + [f"WVal v_{p.name}" for p in m.params]
                prev_fallible = self.current_fn_fallible
                prev_err_type = self.current_fn_err_type
                previous_postcondition = self.current_fn_postcondition
                self.current_fn_fallible = isinstance(m.return_type, ResultType)
                if self.current_fn_fallible and isinstance(m.return_type, ResultType) and isinstance(m.return_type.err_type, NamedType):
                    self.current_fn_err_type = m.return_type.err_type.name
                else:
                    self.current_fn_err_type = ""

                body_stmts: List[str] = []
                self._push_scope("fn")
                self.current_fn_postcondition = self._prepare_postcondition(
                    m, c_name, ['v_self', *['v_' + param.name for param in m.params]], body_stmts)
                self._emit_contract_predicates(getattr(m, 'requires', ()), 'Method requires clause failed', body_stmts)
                ret_expr = self.emit_block_value(m.body, body_stmts)
                if m.return_type is None or isinstance(m.return_type, NamedType) and m.return_type.name == 'unit':
                    ret_expr = 'wysna_unit()'
                ret_hold = self.new_aux("_ret")
                owned_return = self._own_expr(ret_expr)
                if isinstance(m.return_type, ResultType) and isinstance(m.return_type.ok_type, NamedType) and m.return_type.ok_type.name == 'unit':
                    owned_return = f"wysna_discard_result_value({owned_return})"
                body_stmts.append(f"WVal {ret_hold} = {owned_return};")
                self._emit_postconditions(ret_hold, body_stmts)
                self._pop_scope(body_stmts)
                if self.current_fn_fallible:
                    body_stmts.append(f'return wysna_wrap_result({ret_hold}, "{self.current_fn_err_type}");')
                else:
                    body_stmts.append(f"return {ret_hold};")

                fn_lines = [f"static WVal {c_name}({', '.join(params)}) {{"]
                for s in body_stmts:
                    fn_lines.append(f"    {s}")
                fn_lines.append("}")
                self.fn_Definitions.append("\n".join(fn_lines))
                self.current_fn_fallible = prev_fallible
                self.current_fn_err_type = prev_err_type
                self.current_fn_postcondition = previous_postcondition

            # Emit constructor helper wysna_new_<ClassName>
            all_fields = self._collect_class_fields(stmt.name)
            init_res = self._find_class_method(stmt.name, "init")
            if init_res is not None:
                init_cls, init_m = init_res
                init_sig = ", ".join([f"WVal v_{p.name}" for p in init_m.params]) if init_m.params else "void"
                init_args = ["v_self"] + [f"v_{p.name}" for p in init_m.params]
            else:
                init_cls = ""
                init_m = None
                init_sig = "void"
                init_args = []

            new_lines = [f"static WVal wysna_new_{stmt.name}({init_sig}) {{"]
            if all_fields:
                f_names = ", ".join([f'"{f.name}"' for f in all_fields])
                f_defaults = ", ".join([self._default_c_expr_for_type(f.type_ann) for f in all_fields])
                new_lines.append(f"    static const char* const _k[{len(all_fields)}] = {{ {f_names} }};")
                new_lines.append(f"    WVal _v[{len(all_fields)}] = {{ {f_defaults} }};")
                new_lines.append(f'    WVal v_self = wysna_make_object_static("{stmt.name}", {len(all_fields)}, _k, _v);')
                new_lines.append(f"    wysna_release_array({len(all_fields)}, _v);")
            else:
                new_lines.append(f'    WVal v_self = wysna_make_object_static("{stmt.name}", 0, NULL, NULL);')
            new_lines.append(f"    wysna_set_vtable(v_self, &wysna_vtable_{stmt.name});")
            if init_m is not None:
                new_lines.append(f"    wysna_release(wysna_method_{init_cls}__init({', '.join(init_args)}));")
            new_lines.append("    return v_self;")
            new_lines.append("}")
            self.fn_Definitions.append("\n".join(new_lines))
        finally:
            self.current_class = prev_cls

    def _emit_oop_dispatchers(self) -> None:
        from codegen.VirtualDispatch import emit_vtables
        self.fn_Definitions.extend(emit_vtables(self.class_order, self.dispatch_methods, self._find_class_method))

    def _emit_module_body(self, program: Program, ns: str, out_main_stmts: List[str]) -> None:
        prev_ns = self.current_ns
        self.current_ns = ns

        for stmt in program.body:
            if isinstance(stmt, ImportDecl):
                self._handle_import(stmt, out_main_stmts)
            elif isinstance(stmt, ClassDecl):
                self._emit_class_decl(stmt)
            elif isinstance(stmt, FnDecl):
                self._emit_fn_decl(stmt, ns=ns)

        executable = [stmt for stmt in program.body
                      if not isinstance(stmt, (FnDecl, StructDecl, EnumDecl, InterfaceDecl, ClassDecl, ImportDecl))]
        for stmt in executable:
            if isinstance(stmt, LetDecl):
                gv_name = f"v_{ns}_{stmt.name}" if ns else f"v_{stmt.name}"
                if isinstance(stmt.initializer, ListExpr) and not stmt.initializer.elements:
                    # A direct global store consumes the fresh constructor immediately.
                    list_type = getattr(stmt.initializer, 'inferred_type', None)
                    packed = isinstance(list_type, ListType) and list_type.element == U8Type
                    val_expr = "wysna_make_u8_list(0, NULL)" if packed else "wysna_make_list(0, NULL)"
                else:
                    val_expr = self.emit_expr(stmt.initializer, out_main_stmts)
                out_main_stmts.append(f"wysna_assign(&{gv_name}, {self._own_expr(val_expr)});")
            elif ns == "" and stmt is executable[-1] and self._is_entrypoint_call(stmt):
                # The module calls main() explicitly at top level. Its Result must
                # not be discarded, or a failed program would exit 0 silently.
                value = self.emit_expr(stmt.expr, out_main_stmts)
                out_main_stmts.append("WVal _t_main = " + self._own_expr(value) + ";")
                self._emit_entrypoint_status(out_main_stmts)
            else:
                self.emit_stmt(stmt, out_main_stmts)

        self.current_ns = prev_ns

    def _handle_import(self, stmt: ImportDecl, out_main_stmts: List[str]) -> None:
        from error.Error import WysnaError
        raise WysnaError("AST emission imports require a checked compilation session", file=self.file,
                     line=stmt.line, col=stmt.col, code="W9999", stage="module-linking")

    def _emit_fn_decl(self, stmt: FnDecl, ns: str) -> None:
        if hasattr(stmt, "extern_abi"):
            from codegen.ForeignAbi import foreign_wrapper
            signature = ([p.type_ann.name for p in stmt.params], stmt.return_type.name)
            prototype, wrapper = foreign_wrapper(self.c_fn(stmt.name, ns=ns), getattr(stmt, "source_name", stmt.name), signature)
            self.fn_Prototypes.append(prototype)
            self.fn_Definitions.append(wrapper)
            return
        c_name = self.c_fn(stmt.name, ns=ns)
        params_sig = ", ".join([f"WVal v_{p.name}" for p in stmt.params]) if stmt.params else "void"
        for name in ('group_cancelled', 'cancel_group'):
            if stdlib_declaration(stmt, 'async', name, 0):
                self.fn_Definitions.append(f"static WVal {c_name}(void) {{\n    return wysna_bool(wysna_{name}());\n}}")
                return
        if stdlib_declaration(stmt, 'async', '_run_task_group', 5):
            inputs, limit, deadline, fail_fast, work = (f"v_{parameter.name}" for parameter in stmt.params)
            self.fn_Definitions.append(
                f"static WVal {c_name}({params_sig}) {{\n"
                f"    return wysna_run_task_group({inputs}, wysna_as_int({limit}), "
                f"wysna_as_int({deadline}), wysna_as_bool({fail_fast}), {work});\n}}"
            )
            return
        for name, arity in (('parallel_map', 2), ('parallel_batch', 3), ('parallel_filter', 2)):
            if not stdlib_declaration(stmt, 'algorithms', name, arity):
                continue
            parameters = [f'v_{parameter.name}' for parameter in stmt.params]
            if name == 'parallel_batch':
                parameters[1] = f'wysna_as_int({parameters[1]})'
            result = f'wysna_{name}({", ".join(parameters)})'
            if isinstance(stmt.return_type, AstListType) and getattr(stmt.return_type.element_type, 'name', None) == 'u8':
                result = f'wysna_pack_u8_owned({result})'
            self.fn_Definitions.append(f"static WVal {c_name}({params_sig}) {{\n    return {result};\n}}")
            return
        if stdlib_declaration(stmt, 'async', '_spawn_handle', 1) or stdlib_declaration(stmt, 'async', '_take_task_value', 2):
            if stdlib_declaration(stmt, 'async', '_spawn_handle', 1):
                self.fn_Definitions.append(f"static WVal {c_name}({params_sig}) {{\n    return wysna_int(wysna_async_spawn_handle(v_{stmt.params[0].name}));\n}}")
                return
            if stdlib_declaration(stmt, 'async', '_take_task_value', 2):
                self.fn_Definitions.append(f"static WVal {c_name}({params_sig}) {{\n    return wysna_spawn_take_option(v_{stmt.params[0].name}, v_{stmt.params[1].name});\n}}")
                return
        prev_fallible = self.current_fn_fallible
        prev_err_type = self.current_fn_err_type
        previous_postcondition = self.current_fn_postcondition
        self.current_fn_fallible = isinstance(stmt.return_type, ResultType)
        self.current_fn_err_type = self.fn_err_types.get(c_name, "")

        body_stmts: List[str] = []
        self._push_scope("fn")
        self.current_fn_postcondition = self._prepare_postcondition(
            stmt, c_name, ['v_' + param.name for param in stmt.params], body_stmts)
        self._emit_contract_predicates(getattr(stmt, 'requires', ()), 'Function requires clause failed', body_stmts)
        ret_expr = self.emit_block_value(stmt.body, body_stmts)
        if stmt.return_type is None or isinstance(stmt.return_type, NamedType) and stmt.return_type.name == 'unit':
            ret_expr = 'wysna_unit()'
        ret_hold = self.new_aux("_ret")
        owned_return = self._own_expr(ret_expr)
        if isinstance(stmt.return_type, ResultType) and isinstance(stmt.return_type.ok_type, NamedType) and stmt.return_type.ok_type.name == 'unit':
            owned_return = f"wysna_discard_result_value({owned_return})"
        body_stmts.append(f"WVal {ret_hold} = {owned_return};")
        self._emit_postconditions(ret_hold, body_stmts)
        self._pop_scope(body_stmts)
        if self.current_fn_fallible:
            body_stmts.append(f'return wysna_wrap_result({ret_hold}, "{self.current_fn_err_type}");')
        else:
            body_stmts.append(f"return {ret_hold};")

        if getattr(stmt, "is_async", False):
            body_name = f"{c_name}__body"
            fn_lines = [f"static WVal {body_name}({params_sig}) {{"]
            for s in body_stmts:
                fn_lines.append(f"    {s}")
            fn_lines.append("}")
            self.fn_Definitions.append("\n".join(fn_lines))

            step_name = f"wysna_ast_coro_step_{c_name}"
            p_count = len(stmt.params)
            call_args = ", ".join(f"_frame_slots[{i}]" for i in range(p_count))
            step_lines = [
                f"static WVal {step_name}(WVal* _frame_slots, int64_t _slot_count, int64_t* _frame_state, WVal _resume_val, bool _has_resume, WVal* _out_await_box, int64_t* _out_await_handle, int32_t* _out_status) {{",
                "    (void)_slot_count; (void)_frame_state; (void)_resume_val; (void)_has_resume; (void)_out_await_box; (void)_out_await_handle;",
            ]
            if p_count == 0:
                step_lines.append("    (void)_frame_slots;")
            step_lines.append("    *_out_status = WYSNA_CORO_DONE;")
            step_lines.append(f"    return {body_name}({call_args});")
            step_lines.append("}")
            self.fn_Definitions.append("\n".join(step_lines))

            wrap_lines = [f"static WVal {c_name}({params_sig}) {{"]
            if p_count > 0:
                wrap_lines.append(f"    int64_t _p_idx[{p_count}];")
                wrap_lines.append(f"    WVal _p_vals[{p_count}];")
                for i, p in enumerate(stmt.params):
                    wrap_lines.append(f"    _p_idx[{i}] = {i};")
                    wrap_lines.append(f"    _p_vals[{i}] = v_{p.name};")
                wrap_lines.append(f"    return wysna_coro_spawn({step_name}, {p_count}, {p_count}, _p_idx, _p_vals);")
            else:
                wrap_lines.append(f"    return wysna_coro_spawn({step_name}, 0, 0, NULL, NULL);")
            wrap_lines.append("}")
            self.fn_Definitions.append("\n".join(wrap_lines))
        else:
            fn_lines = [f"static WVal {c_name}({params_sig}) {{"]
            for s in body_stmts:
                fn_lines.append(f"    {s}")
            fn_lines.append("}")
            self.fn_Definitions.append("\n".join(fn_lines))
        self.current_fn_fallible = prev_fallible
        self.current_fn_err_type = prev_err_type
        self.current_fn_postcondition = previous_postcondition

    # ==========================================
    # Statement Emission
    # ==========================================

    def emit_block_value(self, block: Block, out: List[str]) -> str:
        res_var = self.new_temp()
        self._declare_owned_var(res_var, "wysna_unit()", out)
        inner: List[str] = []
        self._push_scope("block")
        for s in block.statements:
            self.emit_stmt(s, inner)
        if block.tail_expr is not None:
            tail_val = self.emit_expr(block.tail_expr, inner)
            inner.append(f"wysna_assign(&{res_var}, {self._own_expr(tail_val)});")
        self._pop_scope(inner)
        out.append("{")
        for line in inner:
            out.append(f"    {line}")
        out.append("}")
        return res_var

    def _is_entrypoint_call(self, stmt: Stmt) -> bool:
        """True when `stmt` is the bare `main()` line that closes a Wysna program."""
        return (isinstance(stmt, ExprStmt)
                and isinstance(stmt.expr, CallExpr)
                and isinstance(stmt.expr.callee, IdentExpr)
                and stmt.expr.callee.name == "main"
                and not getattr(stmt.expr, "args", []))

    def _emit_entrypoint_status(self, out: List[str]) -> None:
        out.append("status = wysna_entry_status(_t_main);")
        out.append("wysna_release(_t_main);")

    def emit_stmt(self, stmt: Stmt, out: List[str]) -> None:
        if isinstance(stmt, ExprStmt):
            val = self.emit_expr(stmt.expr, out)
            out.append(f"(void){val};")
            return

        if isinstance(stmt, LetDecl):
            val = self.emit_value(stmt.initializer, out)
            c_name = f"v_{stmt.name}"
            self._declare_owned_var(c_name, self._own_expr(val), out)
            return

        if isinstance(stmt, ReturnStmt):
            if stmt.value is not None:
                val = self.emit_expr(stmt.value, out)
                ret_hold = self.new_aux("_ret")
                out.append(f"WVal {ret_hold} = {self._own_expr(val)};")
                self._emit_postconditions(ret_hold, out)
                self._emit_cleanup_until_fn(out)
                if self.current_fn_fallible:
                    out.append(f'return wysna_wrap_result({ret_hold}, "{self.current_fn_err_type}");')
                else:
                    out.append(f"return {ret_hold};")
            else:
                self._emit_postconditions('wysna_unit()', out)
                self._emit_cleanup_until_fn(out)
                if self.current_fn_fallible:
                    out.append("return wysna_ok(wysna_unit());")
                else:
                    out.append("return wysna_unit();")
            return

        if isinstance(stmt, BreakStmt):
            self._emit_cleanup_until_loop(out)
            out.append("break;")
            return

        if isinstance(stmt, ContinueStmt):
            self._emit_cleanup_until_loop(out)
            out.append("continue;")
            return

        if isinstance(stmt, WhileStmt):
            out.append("while (1) {")
            self._push_scope("loop")
            loop_inner: List[str] = []
            cond_val = self.emit_expr(stmt.condition, loop_inner)
            cond_b = self.new_aux("_cond")
            loop_inner.append(f"bool {cond_b} = wysna_as_bool({cond_val});")
            cond_cleanup: List[str] = []
            self._emit_cleanup_until_loop(cond_cleanup)
            if cond_cleanup:
                loop_inner.append(f"if (!{cond_b}) {{")
                for cl in cond_cleanup:
                    loop_inner.append(f"    {cl}")
                loop_inner.append("    break;")
                loop_inner.append("}")
            else:
                loop_inner.append(f"if (!{cond_b}) break;")
            body_v = self.emit_block_value(stmt.body, loop_inner)
            loop_inner.append(f"(void){body_v};")
            self._pop_scope(loop_inner)
            for line in loop_inner:
                out.append(f"    {line}")
            out.append("}")
            return

        if isinstance(stmt, ForStmt):
            if isinstance(stmt.iterable, RangeExpr):
                from codegen.RangeLoop import emit_range_loop
                emit_range_loop(self, stmt.iterable.start, stmt.iterable.end, stmt.iterable.inclusive,
                                stmt.body, lambda item, lines: self._emit_pattern_bindings(stmt.pattern, item, lines), out)
                return
            out.append("{")
            self._push_scope("block")
            for_outer: List[str] = []
            if isinstance(getattr(stmt.iterable, "inferred_type", None), DictType):
                dict_v = self.emit_value(stmt.iterable, for_outer)
                iter_val = self._bind_call(f"wysna_builtin_dict_items({dict_v})", for_outer)
            else:
                iter_val = self.emit_expr(stmt.iterable, for_outer)
            iter_tmp = self.new_temp()
            idx_tmp = self.new_aux("_idx")
            item_tmp = self.new_aux("_item")
            self._declare_owned_var(iter_tmp, self._own_expr(iter_val), for_outer)
            for_outer.append(f"for (int32_t {idx_tmp} = 0; {idx_tmp} < wysna_list_iteration_length_at({iter_tmp}, {self.escape_c_str(self.file)}, {stmt.line}, {stmt.col}); {idx_tmp}++) {{")
            self._push_scope("loop")
            loop_inner = [f"WVal {item_tmp} = wysna_list_load(wysna_as_list({iter_tmp}), {idx_tmp});"]
            self._emit_pattern_bindings(stmt.pattern, item_tmp, loop_inner)
            body_v = self.emit_block_value(stmt.body, loop_inner)
            loop_inner.append(f"(void){body_v};")
            self._pop_scope(loop_inner)
            for line in loop_inner:
                for_outer.append(f"    {line}")
            for_outer.append("}")
            self._pop_scope(for_outer)
            for line in for_outer:
                out.append(f"    {line}")
            out.append("}")
            return
        if isinstance(stmt, (FnDecl, StructDecl, EnumDecl, InterfaceDecl, ClassDecl, ImportDecl)):
            return
        reject_unknown_node(stmt, self.file, "codegen", "statement")

    def _emit_pattern_bindings(self, pattern: Pattern, val_expr: str, out: List[str]) -> None:
        if isinstance(pattern, OrPattern):
            from checker.PatternTraversal import binding_names
            targets = set(binding_names(pattern.alternatives[0]))
            previous = getattr(self, '_pattern_assignment', set())
            for name in sorted(targets - previous):
                self._declare_owned_var('v_' + name, 'wysna_unit()', out)
            self._pattern_assignment = previous | targets
            try:
                for index, alternative in enumerate(pattern.alternatives):
                    condition = self._emit_pattern_cond(alternative, val_expr, out)
                    out.append(('if' if index == 0 else 'else if') + ' (' + condition + ') {')
                    self._push_scope('block')
                    self._emit_pattern_bindings(alternative, val_expr, out)
                    self._pop_scope(out)
                    out.append('}')
            finally:
                self._pattern_assignment = previous
            return
        if isinstance(pattern, StructPattern):
            for field in pattern.fields:
                self._emit_pattern_bindings(field.pattern, f'wysna_member_get({val_expr}, {self.escape_c_str(field.name)})', out)
            return
        if isinstance(pattern, IdentifierPattern):
            if pattern.name not in ("None", "none"):
                if pattern.name in getattr(self, '_pattern_assignment', set()):
                    out.append(f"wysna_assign(&v_{pattern.name}, wysna_clone({val_expr}));")
                else:
                    self._declare_owned_var(f"v_{pattern.name}", f"wysna_clone({val_expr})", out)
        elif isinstance(pattern, TuplePattern):
            for idx, elem_pat in enumerate(pattern.elements):
                sub_expr = (f"wysna_tuple_get_at({val_expr}, {idx}, {self.escape_c_str(self.file)}, "
                            f"{elem_pat.line}, {elem_pat.col})")
                self._emit_pattern_bindings(elem_pat, sub_expr, out)
        elif isinstance(pattern, ErrorTypePattern):
            self._emit_pattern_bindings(pattern.binding, val_expr, out)
        elif isinstance(pattern, EnumPattern):
            if pattern.variant in ("None", "none") and (pattern.enum_name is None or pattern.enum_name.split("__", 1)[0] == "Option"):
                pass
            elif pattern.variant == "Some" and (pattern.enum_name is None or pattern.enum_name.split("__", 1)[0] == "Option"):
                if pattern.args:
                    sub_val = f"wysna_unwrap_optional({val_expr})"
                    sub_tmp = self.new_aux("_some_val")
                    self._declare_owned_var(sub_tmp, sub_val, out)
                    self._emit_pattern_bindings(pattern.args[0], sub_tmp, out)
            elif pattern.variant == "Ok" and (pattern.enum_name is None or pattern.enum_name.split("__", 1)[0] == "Result"):
                if pattern.args:
                    sub_val = f"wysna_unwrap_ok({val_expr})"
                    sub_tmp = self.new_aux("_ok_val")
                    self._declare_owned_var(sub_tmp, sub_val, out)
                    self._emit_pattern_bindings(pattern.args[0], sub_tmp, out)
            elif pattern.variant == "Err" and (pattern.enum_name is None or pattern.enum_name.split("__", 1)[0] == "Result"):
                if pattern.args:
                    sub_val = f"wysna_unwrap_err({val_expr})"
                    sub_tmp = self.new_aux("_err_val")
                    self._declare_owned_var(sub_tmp, sub_val, out)
                    self._emit_pattern_bindings(pattern.args[0], sub_tmp, out)
            else:
                if len(pattern.args) == 1:
                    sub_val = f"wysna_as_enum({val_expr})->payload"
                    self._emit_pattern_bindings(pattern.args[0], sub_val, out)
                elif len(pattern.args) > 1:
                    for idx, arg_pat in enumerate(pattern.args):
                        sub_val = f"wysna_as_tuple(wysna_as_enum({val_expr})->payload)->items[{idx}]"
                        self._emit_pattern_bindings(arg_pat, sub_val, out)

    def _emit_pattern_cond(self, pattern: Pattern, val_expr: str, out: List[str]) -> str:
        if isinstance(pattern, OrPattern):
            return '(' + ' || '.join(self._emit_pattern_cond(part, val_expr, out) for part in pattern.alternatives) + ')'
        if isinstance(pattern, RangePattern):
            lower = 'INT64_MIN' if pattern.start == -(1 << 63) else str(pattern.start) + 'LL'
            upper = 'INT64_MIN' if pattern.end == -(1 << 63) else str(pattern.end) + 'LL'
            return f'(wysna_as_int({val_expr}) >= {lower} && wysna_as_int({val_expr}) {"<=" if pattern.inclusive else "<"} {upper})'
        if isinstance(pattern, StructPattern):
            return '(' + ' && '.join(self._emit_pattern_cond(field.pattern, f'wysna_member_get({val_expr}, {self.escape_c_str(field.name)})', out) for field in pattern.fields) + ')' if pattern.fields else 'true'
        if isinstance(pattern, WildcardPattern):
            return "true"
        if isinstance(pattern, IdentifierPattern):
            if pattern.name in ("None", "none"):
                return f"wysna_is_none({val_expr})"
            return "true"
        if isinstance(pattern, LiteralPattern):
            lit_v = self.emit_expr(pattern.literal, out)
            return f"wysna_eq({val_expr}, {lit_v})"
        if isinstance(pattern, ErrorTypePattern):
            kind, name = self._error_type_descriptor(pattern.matched_type)
            return f"wysna_value_is_error_type({val_expr}, {self.escape_c_str(kind)}, {self.escape_c_str(name)})"
        if isinstance(pattern, EnumPattern):
            if pattern.variant in ("None", "none") and (pattern.enum_name is None or pattern.enum_name.split("__", 1)[0] == "Option"):
                return f"wysna_is_none({val_expr})"
            if pattern.variant == "Some" and (pattern.enum_name is None or pattern.enum_name.split("__", 1)[0] == "Option"):
                base_cond = f"(!wysna_is_none({val_expr}))"
                if not pattern.args:
                    return base_cond
                sub_cond = self._emit_pattern_cond(pattern.args[0], f"wysna_peek_optional({val_expr})", out)
                if sub_cond == "true":
                    return base_cond
                return f"({base_cond} && {sub_cond})"
            if pattern.variant == "Ok" and (pattern.enum_name is None or pattern.enum_name.split("__", 1)[0] == "Result"):
                base_cond = f"wysna_is_ok({val_expr})"
                if not pattern.args:
                    return base_cond
                sub_cond = self._emit_pattern_cond(pattern.args[0], f"wysna_peek_ok({val_expr})", out)
                if sub_cond == "true":
                    return base_cond
                return f"({base_cond} && {sub_cond})"
            if pattern.variant == "Err" and (pattern.enum_name is None or pattern.enum_name.split("__", 1)[0] == "Result"):
                base_cond = f"wysna_is_err({val_expr})"
                if not pattern.args:
                    return base_cond
                sub_cond = self._emit_pattern_cond(pattern.args[0], f"wysna_peek_err({val_expr})", out)
                if sub_cond == "true":
                    return base_cond
                return f"({base_cond} && {sub_cond})"

            conds: List[str] = [f"(wysna_as_enum({val_expr}) != NULL && strcmp(wysna_as_enum({val_expr})->variant, \"{pattern.variant}\") == 0)"]
            if pattern.enum_name:
                conds.append(f"(wysna_builtin_name(wysna_as_enum({val_expr})->enum_name, \"{pattern.enum_name}\"))")
            if not pattern.args:
                return "(" + " && ".join(conds) + ")"

            conds.append(f"(wysna_as_enum({val_expr})->has_payload)")
            if len(pattern.args) == 1:
                sub_c = self._emit_pattern_cond(pattern.args[0], f"wysna_as_enum({val_expr})->payload", out)
                if sub_c != "true":
                    conds.append(sub_c)
            else:
                conds.append(f"wysna_is_tuple_len(wysna_as_enum({val_expr})->payload, {len(pattern.args)})")
                for idx, arg_pat in enumerate(pattern.args):
                    sub_c = self._emit_pattern_cond(arg_pat, f"wysna_as_tuple(wysna_as_enum({val_expr})->payload)->items[{idx}]", out)
                    if sub_c != "true":
                        conds.append(sub_c)
            return "(" + " && ".join(conds) + ")"

        if isinstance(pattern, TuplePattern):
            conds = [f"wysna_is_tuple_len({val_expr}, {len(pattern.elements)})"]
            for idx, elem_pat in enumerate(pattern.elements):
                sub_c = self._emit_pattern_cond(elem_pat, f"wysna_as_tuple({val_expr})->items[{idx}]", out)
                if sub_c != "true":
                    conds.append(sub_c)
            return "(" + " && ".join(conds) + ")"
        return "true"

    @staticmethod
    def _error_type_descriptor(error_type) -> Tuple[str, str]:
        from checker.WysnaType import IntType, FloatType, BoolType, StrType, StructType, EnumType, ClassType
        if error_type == IntType:
            return "int", "int"
        if error_type == FloatType:
            return "float", "float"
        if error_type == BoolType:
            return "bool", "bool"
        if error_type == StrType:
            return "str", "str"
        if isinstance(error_type, StructType):
            return "struct", error_type.name
        if isinstance(error_type, EnumType):
            return "enum", error_type.name
        if isinstance(error_type, ClassType):
            return "class", error_type.name
        raise ValueError(f"Unsupported error union pattern type: {error_type}")

    # ==========================================
    # Expression Emission
    # ==========================================

    def prepare_assignment_ref(self, target, out):
        if isinstance(target, IndexExpr):
            parent = self.prepare_assignment_ref(target.object, out)
            index = self.emit_value(target.index, out)
            return self._source_call("wysna_index_ref_at", f"{parent}, {index}", target)
        if isinstance(target, MemberExpr):
            from checker.WysnaType import ClassType
            if isinstance(getattr(target.object, 'inferred_type', None), ClassType):
                parent = '&' + self.emit_value(target.object, out)
            else:
                parent = self.prepare_assignment_ref(target.object, out)
            return f'wysna_member_ref({parent}, "{target.property}")'
        return self.emit_lvalue_ref(target, out)

    def emit_lvalue_ref(self, target: Expr, out: List[str]) -> str:
        if isinstance(target, ThisExpr):
            return "&v_self"
        if isinstance(target, IdentExpr):
            return f"&{self.c_var(target.name)}"
        if isinstance(target, IndexExpr):
            obj_ref = self.emit_lvalue_ref(target.object, out)
            idx_val = self.emit_value(target.index, out)
            return self._source_call("wysna_index_ref_at", f"{obj_ref}, {idx_val}", target)
        if isinstance(target, MemberExpr):
            obj_ref = self.emit_lvalue_ref(target.object, out)
            return f'wysna_member_ref({obj_ref}, "{target.property}")'
        # Fallback for temporary receiver
        val = self.emit_expr(target, out)
        if val.startswith("_t"):
            return f"&{val}"
        tmp = self.new_temp()
        self._declare_owned_var(tmp, self._own_expr(val), out)
        return f"&{tmp}"

    def _bind_call(self, c_call: str, out: List[str]) -> str:
        tmp = self.new_temp()
        self._declare_owned_var(tmp, c_call, out)
        return tmp

    def _source_call(self, name: str, args: str, node: Expr) -> str:
        file = getattr(node, "source_file", None) or self.file
        return f"{name}({args}, {self.escape_c_str(file)}, {node.line}, {node.col})"

    def _enum_name(self, expr, fallback):
        if self.interner is not None:
            entry = self.interner.get_entry(getattr(expr, "inferred_type_id", 0))
            if entry.tag in ("optional", "result"):
                return "Option" if entry.tag == "optional" else "Result"
        return fallback

    def function_value(self, name, arity, out, environment="wysna_unit()", captured=False):
        adapter = "wysna_adapter_" + name
        if adapter not in self.function_adapters:
            self.function_adapters.add(adapter)
            self.fn_Prototypes.append(f"static WVal {adapter}(WVal environment, int32_t argc, const WVal* argv);")
            args = ", ".join((["environment"] if captured else []) + [f"argv[{i}]" for i in range(arity)])
            body = f"static WVal {adapter}(WVal environment, int32_t argc, const WVal* argv) {{\n"
            body += "    (void)environment; (void)argv;\n"
            body += f'    if (argc != {arity}) {{ fprintf(stderr, "[W4003] RuntimeError: Call arity mismatch\\n"); exit(1); }}\n'
            body += f"    return wysna_fn_{name}({args});\n}}"
            self.fn_Definitions.append(body)
        return self._bind_call(f"wysna_function_new({adapter}, {environment})", out)

    def indirect_call(self, callee, args, out):
        function = self.emit_value(callee, out)
        values = [self.emit_value(argument, out) for argument in args]
        array = "(WVal[]){ " + ", ".join(values) + " }" if values else "NULL"
        return self._bind_call(f"wysna_call({function}, {len(values)}, {array})", out)

    def emit_expr(self, expr: Expr, out: List[str]) -> str:
        handler = EXPRESSION_RULES.handler_for(expr, self.file)
        if hasattr(expr, "function_reference"):
            value = self.function_value(expr.function_reference, len(expr.inferred_type.params), out)
        else:
            value = handler(self, expr, out)
        if hasattr(expr, "force_result_wrap_id"):
            return self._bind_call(f"wysna_ok({self._own_expr(value)})", out)
        return value

    def _emit_elif_chain(self, elifs: List, idx: int, else_branch: Optional[Block], res_var: str, out: List[str]) -> None:
        if idx < len(elifs):
            eb = elifs[idx]
            out.append("else {")
            self._push_scope("block")
            inner: List[str] = []
            eb_cond = self.emit_expr(eb.condition, inner)
            inner.append(f"if (wysna_as_bool({eb_cond})) {{")
            self._push_scope("block")
            eb_stmts: List[str] = []
            eb_v = self.emit_block_value(eb.body, eb_stmts)
            eb_stmts.append(f"wysna_assign(&{res_var}, {self._own_expr(eb_v)});")
            self._pop_scope(eb_stmts)
            for s in eb_stmts:
                inner.append(f"    {s}")
            inner.append("}")
            self._emit_elif_chain(elifs, idx + 1, else_branch, res_var, inner)
            self._pop_scope(inner)
            for s in inner:
                out.append(f"    {s}")
            out.append("}")
        elif else_branch is not None:
            out.append("else {")
            self._push_scope("block")
            else_stmts: List[str] = []
            else_v = self.emit_block_value(else_branch, else_stmts)
            else_stmts.append(f"wysna_assign(&{res_var}, {self._own_expr(else_v)});")
            self._pop_scope(else_stmts)
            for s in else_stmts:
                out.append(f"    {s}")
            out.append("}")

    def _emit_match_arms(self, arms: List, idx: int, subj_tmp: str, res_var: str, out: List[str]) -> None:
        if idx >= len(arms):
            return
        arm = arms[idx]
        cond_str = self._emit_pattern_cond(arm.pattern, subj_tmp, out)

        if arm.guard is not None:
            matched = self.new_aux("_matched")
            out.append(f"bool {matched} = false;")
            out.append(f"if ({cond_str}) {{")
            self._push_scope("block")
            inner = []
            self._emit_pattern_bindings(arm.pattern, subj_tmp, inner)
            guard = self.emit_expr(arm.guard, inner)
            inner.append(f"if (wysna_as_bool({guard})) {{")
            self._push_scope("block")
            body = []
            result = self.emit_block_value(arm.body, body)
            body.append(f"wysna_assign(&{res_var}, {self._own_expr(result)});")
            body.append(f"{matched} = true;")
            self._pop_scope(body)
            inner.extend("    " + line for line in body)
            inner.append("}")
            self._pop_scope(inner)
            out.extend("    " + line for line in inner)
            out.append("}")
            out.append(f"if (!{matched}) {{")
            self._push_scope("block")
            fallback = []
            self._emit_match_arms(arms, idx + 1, subj_tmp, res_var, fallback)
            self._pop_scope(fallback)
            out.extend("    " + line for line in fallback)
            out.append("}")
            return
        out.append(f"if ({cond_str}) {{")
        self._push_scope("block")
        arm_stmts: List[str] = []
        self._emit_pattern_bindings(arm.pattern, subj_tmp, arm_stmts)
        arm_v = self.emit_block_value(arm.body, arm_stmts)
        arm_stmts.append(f"wysna_assign(&{res_var}, {self._own_expr(arm_v)});")
        self._pop_scope(arm_stmts)
        for s in arm_stmts:
            out.append(f"    {s}")
        out.append("}")
        if idx + 1 < len(arms):
            out.append("else {")
            self._push_scope("block")
            else_inner: List[str] = []
            self._emit_match_arms(arms, idx + 1, subj_tmp, res_var, else_inner)
            self._pop_scope(else_inner)
            for s in else_inner:
                out.append(f"    {s}")
            out.append("}")
