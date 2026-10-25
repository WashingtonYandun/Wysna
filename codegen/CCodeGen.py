from common.ExpressionDispatch import reject_unknown_node
from codegen.ExpressionRules import EXPRESSION_RULES
from typing import Dict, List, Optional, Set, Tuple
from node.NodeType import (
    LambdaExpr, Program, Stmt, Expr, TypeNode, ResultType,
    GenericType, NamedType, OptionalType as AstOptionalType, ListType as AstListType, DictType as AstDictType,
    Pattern, IdentifierPattern, TuplePattern, EnumPattern, ErrorTypePattern, LiteralPattern, WildcardPattern,
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
        self.class_order: List[str] = []
        self.dispatch_methods: List[Tuple[str, int]] = []
        self.current_class: Optional[ClassDecl] = None
        self.fallible_fns: Dict[str, bool] = {}
        self.fn_err_types: Dict[str, str] = {}
        self.current_fn_fallible: bool = False
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
        if value.startswith(("wysna_make_", "wysna_new_")):
            return self._bind_call(value, out)
        return self._bind_call(f"wysna_clone({value})", out)

    def emit_value(self, expr, out: List[str]) -> str:
        return self._snapshot(self.emit_expr(expr, out), out)

    def _own_expr(self, val_expr: str) -> str:
        if val_expr.startswith("_t"):
            return f"wysna_move(&{val_expr})"
        if val_expr.startswith("wysna_make_") or val_expr.startswith("wysna_new_") or val_expr.startswith("wysna_clone("):
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

        # Emit all top-level functions, class methods, and imported module functions
        main_stmts: List[str] = []
        self._push_scope("fn")
        self._emit_module_body(program, ns="", out_main_stmts=main_stmts)

        # If there are no top-level executable statements and main() exists in Wysna, call it
        non_decls = [s for s in program.body if not isinstance(s, (FnDecl, StructDecl, EnumDecl, InterfaceDecl, ClassDecl, ImportDecl))]
        if getattr(program, "call_entrypoint", not non_decls) and "wysna_fn_main" in self.fallible_fns:
            main_stmts.append("WVal _t_main = wysna_fn_main();")
            self._emit_entrypoint_guard(main_stmts)

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
        for s in main_stmts:
            lines.append(f"    {s}")
        for gv in reversed(self.global_vars):
            lines.append(f"    wysna_release({gv});")
        lines.append("    wysna_runtime_cleanup();")
        lines.append("    return 0;")
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
            if isinstance(stmt, EnumDecl):
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
            if type_ann.name == "int":
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
                self.current_fn_fallible = isinstance(m.return_type, ResultType)
                if self.current_fn_fallible and isinstance(m.return_type, ResultType) and isinstance(m.return_type.err_type, NamedType):
                    self.current_fn_err_type = m.return_type.err_type.name
                else:
                    self.current_fn_err_type = ""

                body_stmts: List[str] = []
                self._push_scope("fn")
                ret_expr = self.emit_block_value(m.body, body_stmts)
                if m.return_type is None or isinstance(m.return_type, NamedType) and m.return_type.name == 'unit':
                    ret_expr = 'wysna_unit()'
                ret_hold = self.new_aux("_ret")
                owned_return = self._own_expr(ret_expr)
                if isinstance(m.return_type, ResultType) and isinstance(m.return_type.ok_type, NamedType) and m.return_type.ok_type.name == 'unit':
                    owned_return = f"wysna_discard_result_value({owned_return})"
                body_stmts.append(f"WVal {ret_hold} = {owned_return};")
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
                new_lines.append(f"    const char* _k[{len(all_fields)}] = {{ {f_names} }};")
                new_lines.append(f"    WVal _v[{len(all_fields)}] = {{ {f_defaults} }};")
                new_lines.append(f'    WVal v_self = wysna_make_object("{stmt.name}", {len(all_fields)}, _k, _v);')
                new_lines.append(f"    wysna_release_array({len(all_fields)}, _v);")
            else:
                new_lines.append(f'    WVal v_self = wysna_make_object("{stmt.name}", 0, NULL, NULL);')
            new_lines.append(f"    v_self.as.st->vtable = &wysna_vtable_{stmt.name};")
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

        for stmt in program.body:
            if not isinstance(stmt, (FnDecl, StructDecl, EnumDecl, InterfaceDecl, ClassDecl, ImportDecl)):
                if isinstance(stmt, LetDecl):
                    gv_name = f"v_{ns}_{stmt.name}" if ns else f"v_{stmt.name}"
                    if isinstance(stmt.initializer, ListExpr) and not stmt.initializer.elements:
                        # A direct global store consumes the fresh constructor immediately.
                        val_expr = "wysna_make_list(0, NULL)"
                    else:
                        val_expr = self.emit_expr(stmt.initializer, out_main_stmts)
                    out_main_stmts.append(f"wysna_assign(&{gv_name}, {self._own_expr(val_expr)});")
                elif ns == "" and self._is_entrypoint_call(stmt):
                    # The module calls main() explicitly at top level. Its Result must
                    # not be discarded, or a failed program would exit 0 silently.
                    value = self.emit_expr(stmt.expr, out_main_stmts)
                    out_main_stmts.append("WVal _t_main = " + self._own_expr(value) + ";")
                    self._emit_entrypoint_guard(out_main_stmts)
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
        prev_fallible = self.current_fn_fallible
        prev_err_type = self.current_fn_err_type
        self.current_fn_fallible = isinstance(stmt.return_type, ResultType)
        self.current_fn_err_type = self.fn_err_types.get(c_name, "")

        body_stmts: List[str] = []
        self._push_scope("fn")
        ret_expr = self.emit_block_value(stmt.body, body_stmts)
        if stmt.return_type is None or isinstance(stmt.return_type, NamedType) and stmt.return_type.name == 'unit':
            ret_expr = 'wysna_unit()'
        ret_hold = self.new_aux("_ret")
        owned_return = self._own_expr(ret_expr)
        if isinstance(stmt.return_type, ResultType) and isinstance(stmt.return_type.ok_type, NamedType) and stmt.return_type.ok_type.name == 'unit':
            owned_return = f"wysna_discard_result_value({owned_return})"
        body_stmts.append(f"WVal {ret_hold} = {owned_return};")
        self._pop_scope(body_stmts)
        if self.current_fn_fallible:
            body_stmts.append(f'return wysna_wrap_result({ret_hold}, "{self.current_fn_err_type}");')
        else:
            body_stmts.append(f"return {ret_hold};")

        fn_lines = [f"static WVal {c_name}({params_sig}) {{"]
        for s in body_stmts:
            fn_lines.append(f"    {s}")
        fn_lines.append("}")
        self.fn_Definitions.append("\n".join(fn_lines))
        self.current_fn_fallible = prev_fallible
        self.current_fn_err_type = prev_err_type

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

    def _emit_entrypoint_guard(self, out: List[str]) -> None:
        """Emits the entrypoint Result check and the mandatory release of `_t_main`.

        Wysna has no stack unwinding: a fallible entrypoint returns its error as a
        value. Discarding it would make a failing program exit 0 with no output,
        which is indistinguishable from success for make/CI/shell.
        """
        out.append(
            "if (_t_main.tag == W_ERR) { wysna_report_entrypoint_error(_t_main.as.box->val); "
            "wysna_release(_t_main); wysna_runtime_cleanup(); return 1; }"
        )
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
                self._emit_cleanup_until_fn(out)
                if self.current_fn_fallible:
                    out.append(f'return wysna_wrap_result({ret_hold}, "{self.current_fn_err_type}");')
                else:
                    out.append(f"return {ret_hold};")
            else:
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
            loop_inner.append(f"bool {cond_b} = ({cond_val}).as.b;")
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
            iter_val = self.emit_expr(stmt.iterable, for_outer)
            iter_tmp = self.new_temp()
            idx_tmp = self.new_aux("_idx")
            item_tmp = self.new_aux("_item")
            self._declare_owned_var(iter_tmp, self._own_expr(iter_val), for_outer)
            for_outer.append(f"for (int32_t {idx_tmp} = 0; {idx_tmp} < {iter_tmp}.as.list->len; {idx_tmp}++) {{")
            self._push_scope("loop")
            loop_inner = [f"WVal {item_tmp} = {iter_tmp}.as.list->data[{idx_tmp}];"]
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
        if isinstance(pattern, IdentifierPattern):
            if pattern.name not in ("None", "none"):
                self._declare_owned_var(f"v_{pattern.name}", f"wysna_clone({val_expr})", out)
        elif isinstance(pattern, TuplePattern):
            for idx, elem_pat in enumerate(pattern.elements):
                sub_expr = f"({val_expr}).as.tuple->items[{idx}]"
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
                    sub_val = f"({val_expr}).as.en.box->val"
                    self._emit_pattern_bindings(pattern.args[0], sub_val, out)
                elif len(pattern.args) > 1:
                    for idx, arg_pat in enumerate(pattern.args):
                        sub_val = f"({val_expr}).as.en.box->val.as.tuple->items[{idx}]"
                        self._emit_pattern_bindings(arg_pat, sub_val, out)

    def _emit_pattern_cond(self, pattern: Pattern, val_expr: str, out: List[str]) -> str:
        if isinstance(pattern, WildcardPattern):
            return "true"
        if isinstance(pattern, IdentifierPattern):
            if pattern.name in ("None", "none"):
                return f"(({val_expr}).tag == W_NONE || (({val_expr}).tag == W_ENUM && strcmp(({val_expr}).as.en.enum_name, \"Option\") == 0 && strcmp(({val_expr}).as.en.variant, \"None\") == 0))"
            return "true"
        if isinstance(pattern, LiteralPattern):
            lit_v = self.emit_expr(pattern.literal, out)
            return f"wysna_eq({val_expr}, {lit_v})"
        if isinstance(pattern, ErrorTypePattern):
            kind, name = self._error_type_descriptor(pattern.matched_type)
            return f"wysna_value_is_error_type({val_expr}, {self.escape_c_str(kind)}, {self.escape_c_str(name)})"
        if isinstance(pattern, EnumPattern):
            if pattern.variant in ("None", "none") and (pattern.enum_name is None or pattern.enum_name.split("__", 1)[0] == "Option"):
                return f"(({val_expr}).tag == W_NONE || (({val_expr}).tag == W_ENUM && strcmp(({val_expr}).as.en.enum_name, \"Option\") == 0 && strcmp(({val_expr}).as.en.variant, \"None\") == 0))"
            if pattern.variant == "Some" and (pattern.enum_name is None or pattern.enum_name.split("__", 1)[0] == "Option"):
                base_cond = f"(!(({val_expr}).tag == W_NONE || (({val_expr}).tag == W_ENUM && strcmp(({val_expr}).as.en.enum_name, \"Option\") == 0 && strcmp(({val_expr}).as.en.variant, \"None\") == 0)))"
                if not pattern.args:
                    return base_cond
                sub_cond = self._emit_pattern_cond(pattern.args[0], f"wysna_peek_optional({val_expr})", out)
                if sub_cond == "true":
                    return base_cond
                return f"({base_cond} && {sub_cond})"
            if pattern.variant == "Ok" and (pattern.enum_name is None or pattern.enum_name.split("__", 1)[0] == "Result"):
                base_cond = f"(({val_expr}).tag == W_OK || (({val_expr}).tag == W_ENUM && strcmp(({val_expr}).as.en.enum_name, \"Result\") == 0 && strcmp(({val_expr}).as.en.variant, \"Ok\") == 0))"
                if not pattern.args:
                    return base_cond
                sub_cond = self._emit_pattern_cond(pattern.args[0], f"wysna_peek_ok({val_expr})", out)
                if sub_cond == "true":
                    return base_cond
                return f"({base_cond} && {sub_cond})"
            if pattern.variant == "Err" and (pattern.enum_name is None or pattern.enum_name.split("__", 1)[0] == "Result"):
                base_cond = f"(({val_expr}).tag == W_ERR || (({val_expr}).tag == W_ENUM && strcmp(({val_expr}).as.en.enum_name, \"Result\") == 0 && strcmp(({val_expr}).as.en.variant, \"Err\") == 0))"
                if not pattern.args:
                    return base_cond
                sub_cond = self._emit_pattern_cond(pattern.args[0], f"wysna_peek_err({val_expr})", out)
                if sub_cond == "true":
                    return base_cond
                return f"({base_cond} && {sub_cond})"

            conds: List[str] = [f"(({val_expr}).tag == W_ENUM && strcmp(({val_expr}).as.en.variant, \"{pattern.variant}\") == 0)"]
            if pattern.enum_name:
                conds.append(f"(wysna_builtin_name(({val_expr}).as.en.enum_name, \"{pattern.enum_name}\"))")
            if not pattern.args:
                return "(" + " && ".join(conds) + ")"

            conds.append(f"(({val_expr}).as.en.box != NULL)")
            if len(pattern.args) == 1:
                sub_c = self._emit_pattern_cond(pattern.args[0], f"({val_expr}).as.en.box->val", out)
                if sub_c != "true":
                    conds.append(sub_c)
            else:
                conds.append(f"(({val_expr}).as.en.box->val.tag == W_TUPLE && ({val_expr}).as.en.box->val.as.tuple->len == {len(pattern.args)})")
                for idx, arg_pat in enumerate(pattern.args):
                    sub_c = self._emit_pattern_cond(arg_pat, f"({val_expr}).as.en.box->val.as.tuple->items[{idx}]", out)
                    if sub_c != "true":
                        conds.append(sub_c)
            return "(" + " && ".join(conds) + ")"

        if isinstance(pattern, TuplePattern):
            conds = [f"(({val_expr}).tag == W_TUPLE && ({val_expr}).as.tuple->len == {len(pattern.elements)})"]
            for idx, elem_pat in enumerate(pattern.elements):
                sub_c = self._emit_pattern_cond(elem_pat, f"({val_expr}).as.tuple->items[{idx}]", out)
                if sub_c != "true":
                    conds.append(sub_c)
            return "(" + " && ".join(conds) + ")"
        return "true"

    @staticmethod
    def _error_type_descriptor(error_type) -> Tuple[str, str]:
        from checker.WysnaType import IntType, FloatType, BoolType, StrType, StructType, EnumType
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
            inner.append(f"if (({eb_cond}).as.b) {{")
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
            inner.append(f"if (({guard}).as.b) {{")
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


def compile_to_c(source: str, file: str = "<stdin>") -> str:
    """Compile source through the checked service boundary using the AST oracle."""
    from compiler.CompilerService import CompilerService
    service = CompilerService()
    checked = service.check(source, file)
    return service.emit_c(checked, backend="ast").source
