"""
hir/HirDump.py - Canonical and deterministic text dump of resolved HIR.

Provides stable, human-readable representations of fully lowered HIR modules,
declarations, expressions, patterns, and resolved semantic identifiers.
Used for compiler inspection, debugging, and snapshot verification.
"""

from typing import Any, List
from hir.HirNodes import (
    HirModule, HirFnDecl, HirStructDecl, HirEnumDecl, HirInterfaceDecl, HirClassDecl,
    HirStmt, HirExpr, HirBlock, HirParam, HirFieldDef,
    HirLet, HirBindTemp, HirAssign, HirWhile, HirForList, HirReturn, HirBreak, HirContinue, HirExprStmt,
    HirConstInt, HirConstFloat, HirConstBool, HirConstStr, HirConstNone, HirConstUnit,
    HirLocalRef, HirThisRef, HirSuperRef, HirUnaryOp, HirBinaryOp, HirSequence, HirAssignment,
    HirConstructStruct, HirConstructEnum, HirConstructObject, HirConstructList, HirConstructDict, HirConstructTuple,
    HirFunctionRef, HirClosure, HirIndirectCall, HirDirectCall, HirBuiltinCall, HirMethodCall, HirFieldRead, HirIndexRead,
    HirCoerce, HirTryPropagate, HirCoalesce, HirElseHandle, HirRange, HirIf, HirMatch, HirMatchArm,
    HirPattern, HirWildcardPattern, HirBindingPattern, HirLiteralPattern, HirEnumPattern, HirErrorTypePattern, HirTuplePattern
)


class HirDumper:
    def __init__(self, indent_width: int = 2):
        self.indent_width = indent_width
        self.lines: List[str] = []

    def dump_module(self, module: HirModule) -> str:
        self.lines = [f"Module: {module.file_path}"]
        
        for s in module.structs:
            self._dump_struct(s, 1)
        for e in module.enums:
            self._dump_enum(e, 1)
        for intf in module.interfaces:
            self._dump_interface(intf, 1)
        for c in module.classes:
            self._dump_class(c, 1)
        for fn in module.functions:
            self._dump_fn(fn, 1)
            
        if module.top_level_stmts:
            self._emit("Top-level Statements:", 1)
            for stmt in module.top_level_stmts:
                self._dump_stmt(stmt, 2)
                
        return "\n".join(self.lines)

    def _indent(self, level: int) -> str:
        return " " * (level * self.indent_width)

    def _emit(self, text: str, level: int) -> None:
        self.lines.append(f"{self._indent(level)}{text}")

    def _dump_struct(self, decl: HirStructDecl, level: int) -> None:
        self._emit(f"struct {decl.name} [type_id={decl.type_id}] @ {decl.span} {{", level)
        for f in decl.fields:
            pub = "pub " if f.is_public else ""
            self._emit(f"{pub}{f.name}: type_id={f.type_id} @ {f.span}", level + 1)
        self._emit("}", level)

    def _dump_enum(self, decl: HirEnumDecl, level: int) -> None:
        self._emit(f"enum {decl.name} [type_id={decl.type_id}] @ {decl.span} {{", level)
        for idx, v in enumerate(decl.variants):
            payload = decl.payload_types.get(v, [])
            payload_str = f"({', '.join(f'type_id={t}' for t in payload)})" if payload else ""
            self._emit(f"variant [{idx}] {v}{payload_str}", level + 1)
        self._emit("}", level)

    def _dump_interface(self, decl: HirInterfaceDecl, level: int) -> None:
        self._emit(f"interface {decl.name} [type_id={decl.type_id}] @ {decl.span} {{", level)
        for m_name, fn_tid in decl.methods:
            self._emit(f"fn {m_name}: type_id={fn_tid}", level + 1)
        self._emit("}", level)

    def _dump_class(self, decl: HirClassDecl, level: int) -> None:
        super_str = f" extends {decl.superclass}" if decl.superclass else ""
        intf_str = f" implements {', '.join(decl.interfaces)}" if decl.interfaces else ""
        self._emit(f"class {decl.name}{super_str}{intf_str} [type_id={decl.type_id}] @ {decl.span} {{", level)
        for f in decl.fields:
            pub = "pub " if f.is_public else ""
            self._emit(f"field {pub}{f.name}: type_id={f.type_id} @ {f.span}", level + 1)
        for m in decl.methods:
            self._dump_fn(m, level + 1)
        self._emit("}", level)

    def _dump_fn(self, fn: HirFnDecl, level: int) -> None:
        method_str = "method " if fn.is_method else "fn "
        params_str = ", ".join(f"{p.name} [{p.symbol_id}]: type_id={p.type_id}" for p in fn.params)
        fallible_str = f" ! {fn.err_type_name}" if fn.is_fallible else ""
        self._emit(f"{method_str}{fn.name}({params_str}) -> type_id={fn.return_type_id}{fallible_str} @ {fn.span} {{", level)
        self._dump_block(fn.body, level + 1)
        self._emit("}", level)

    def _dump_block(self, block: HirBlock, level: int) -> None:
        for stmt in block.statements:
            self._dump_stmt(stmt, level)
        if block.tail_expr is not None:
            self._emit(f"tail: {self.format_expr(block.tail_expr)}", level)

    def _dump_stmt(self, stmt: HirStmt, level: int) -> None:
        if isinstance(stmt, HirLet):
            mut = "mut " if stmt.is_mut else ""
            init_str = self.format_expr(stmt.init_expr)
            self._emit(f"let {mut}{stmt.name} [{stmt.symbol_id}]: type_id={stmt.type_id} = {init_str} @ {stmt.span}", level)
        elif isinstance(stmt, HirBindTemp):
            init_str = self.format_expr(stmt.init_expr)
            self._emit(f"bind_temp {stmt.temp_name} [{stmt.symbol_id}]: type_id={stmt.type_id} = {init_str} @ {stmt.span}", level)
        elif isinstance(stmt, HirAssign):
            path_str = f" [path={stmt.path}]" if stmt.path else ""
            self._emit(f"assign {self.format_expr(stmt.target)}{path_str} = {self.format_expr(stmt.value)} @ {stmt.span}", level)
        elif isinstance(stmt, HirWhile):
            self._emit(f"while {self.format_expr(stmt.cond_expr)} @ {stmt.span} {{", level)
            self._dump_block(stmt.body, level + 1)
            self._emit("}", level)
        elif isinstance(stmt, HirForList):
            pat_str = self.format_pattern(stmt.pattern)
            self._emit(f"for {pat_str} in {self.format_expr(stmt.list_expr)} [elem_type={stmt.elem_type_id}] @ {stmt.span} {{", level)
            self._dump_block(stmt.body, level + 1)
            self._emit("}", level)
        elif isinstance(stmt, HirReturn):
            self._emit(f"return {self.format_expr(stmt.value_expr)} @ {stmt.span}", level)
        elif isinstance(stmt, HirBreak):
            self._emit(f"break @ {stmt.span}", level)
        elif isinstance(stmt, HirContinue):
            self._emit(f"continue @ {stmt.span}", level)
        elif isinstance(stmt, HirExprStmt):
            self._emit(f"expr_stmt {self.format_expr(stmt.expr)} @ {stmt.span}", level)
        else:
            self._emit(f"unknown_stmt {type(stmt).__name__} @ {stmt.span}", level)

    def format_pattern(self, pat: Any) -> str:
        if isinstance(pat, HirWildcardPattern):
            return f"_(type_id={pat.type_id})"
        if isinstance(pat, HirBindingPattern):
            mut = "mut " if pat.is_mut else ""
            return f"{mut}{pat.name}[{pat.symbol_id}]:type_id={pat.type_id}"
        if isinstance(pat, HirLiteralPattern):
            return f"lit({pat.value!r}, kind={pat.literal_kind}, type_id={pat.type_id})"
        if isinstance(pat, HirErrorTypePattern):
            return (f"error_type({pat.type_name}, type_id={pat.error_type_id}, "
                    f"{self.format_pattern(pat.binding)}):type_id={pat.type_id}")
        if isinstance(pat, HirEnumPattern):
            sub_str = f"({', '.join(self.format_pattern(sp) for sp in pat.subpatterns)})" if pat.subpatterns else ""
            return f"{pat.enum_name}::{pat.variant_name}[{pat.variant_id}]{sub_str}:type_id={pat.type_id}"
        if isinstance(pat, HirTuplePattern):
            return f"({', '.join(self.format_pattern(ep) for ep in pat.elements)}):type_id={pat.type_id}"
        return str(pat)

    def format_expr(self, expr: HirExpr) -> str:
        if isinstance(expr, HirConstInt):
            return f"{expr.value}:type_id={expr.type_id}"
        if isinstance(expr, HirConstFloat):
            return f"{expr.value}:type_id={expr.type_id}"
        if isinstance(expr, HirConstBool):
            return f"{str(expr.value).lower()}:type_id={expr.type_id}"
        if isinstance(expr, HirConstStr):
            return f"{expr.value!r}:type_id={expr.type_id}"
        if isinstance(expr, HirConstNone):
            return f"none:type_id={expr.type_id}"
        if isinstance(expr, HirConstUnit):
            return f"unit:type_id={expr.type_id}"
        if isinstance(expr, HirLocalRef):
            return f"{expr.name}[{expr.symbol_id}]:type_id={expr.type_id}"
        if isinstance(expr, HirThisRef):
            return f"this:type_id={expr.type_id}"
        if isinstance(expr, HirSuperRef):
            return f"super:type_id={expr.type_id}"
        if isinstance(expr, HirUnaryOp):
            return f"({expr.op} {self.format_expr(expr.operand)}):type_id={expr.type_id}"
        if isinstance(expr, HirBinaryOp):
            return f"({self.format_expr(expr.left)} {expr.op} {self.format_expr(expr.right)}):type_id={expr.type_id}"
        if isinstance(expr, HirAssignment):
            return f"assign_value {self.format_expr(expr.target)} = {self.format_expr(expr.value)}:type_id={expr.type_id}"
        if isinstance(expr, HirSequence):
            effects = "; ".join(type(statement).__name__ for statement in expr.statements)
            return f"sequence[{effects}] => {self.format_expr(expr.value)}:type_id={expr.type_id}"
        if isinstance(expr, HirConstructStruct):
            f_str = ", ".join(f"{name}: {self.format_expr(val)}" for name, val in expr.fields)
            return f"struct_init {expr.struct_name}{{{f_str}}}:type_id={expr.type_id}"
        if isinstance(expr, HirConstructEnum):
            p_str = f"({self.format_expr(expr.payload)})" if expr.payload is not None else ""
            return f"{expr.enum_name}::{expr.variant_name}[{expr.variant_id}]{p_str}:type_id={expr.type_id}"
        if isinstance(expr, HirConstructObject):
            a_str = ", ".join(self.format_expr(a) for a in expr.args)
            return f"new {expr.class_name}({a_str}):type_id={expr.type_id}"
        if isinstance(expr, HirConstructList):
            e_str = ", ".join(self.format_expr(e) for e in expr.elems)
            return f"[{e_str}]:type_id={expr.type_id}"
        if isinstance(expr, HirConstructDict):
            e_str = ", ".join(f"{self.format_expr(k)}: {self.format_expr(v)}" for k, v in expr.entries)
            return f"{{{e_str}}}:type_id={expr.type_id}"
        if isinstance(expr, HirConstructTuple):
            e_str = ", ".join(self.format_expr(e) for e in expr.elems)
            return f"({e_str}):type_id={expr.type_id}"
        if isinstance(expr, HirFunctionRef):
            return f"FunctionRef({expr.name}) : {expr.type_id}"
        if isinstance(expr, HirClosure):
            return f"Closure({expr.name}, {self.format_expr(expr.environment)}) : {expr.type_id}"
        if isinstance(expr, HirIndirectCall):
            return f"IndirectCall({self.format_expr(expr.callee)}, [{', '.join(self.format_expr(a) for a in expr.args)}]) : {expr.type_id}"
        if isinstance(expr, HirDirectCall):
            a_str = ", ".join(self.format_expr(a) for a in expr.args)
            return f"call {expr.fn_name}[{expr.target}]({a_str}):type_id={expr.type_id}"
        if isinstance(expr, HirBuiltinCall):
            a_str = ", ".join(self.format_expr(a) for a in expr.args)
            return f"builtin {expr.builtin_name}[{expr.target}]({a_str}):type_id={expr.type_id}"
        if isinstance(expr, HirMethodCall):
            a_str = ", ".join(self.format_expr(a) for a in expr.args)
            return f"method_call {self.format_expr(expr.receiver)}.{expr.method_name}[{expr.target}]({a_str}):type_id={expr.type_id}"
        if isinstance(expr, HirFieldRead):
            fid_str = f"[{expr.field_id}]" if expr.field_id else f"[idx={expr.field_idx}]"
            return f"{self.format_expr(expr.object)}.{expr.field_name}{fid_str}:type_id={expr.type_id}"
        if isinstance(expr, HirIndexRead):
            return f"{self.format_expr(expr.object)}[{self.format_expr(expr.index)}]:type_id={expr.type_id}"
        if isinstance(expr, HirCoerce):
            return f"coerce<{expr.kind}>({self.format_expr(expr.expr)}):type_id={expr.type_id}"
        if isinstance(expr, HirTryPropagate):
            return f"try?<{expr.kind}>({self.format_expr(expr.operand)}):type_id={expr.type_id}"
        if isinstance(expr, HirCoalesce):
            return f"({self.format_expr(expr.opt_expr)} ?? {self.format_expr(expr.fallback_expr)}):type_id={expr.type_id}"
        if isinstance(expr, HirElseHandle):
            return f"({self.format_expr(expr.operand)} else {expr.err_var}):type_id={expr.type_id}"
        if isinstance(expr, HirRange):
            op = "..=" if expr.inclusive else ".."
            return f"({self.format_expr(expr.start)} {op} {self.format_expr(expr.end)}):type_id={expr.type_id}"
        if isinstance(expr, HirIf):
            return f"if-expr:type_id={expr.type_id}"
        if isinstance(expr, HirMatch):
            return f"match-expr:type_id={expr.type_id}"
        return f"expr({type(expr).__name__}):type_id={getattr(expr, 'type_id', None)}"


def dump_hir(module: HirModule) -> str:
    """Helper entry point to dump a HirModule into canonical string form."""
    return HirDumper().dump_module(module)
