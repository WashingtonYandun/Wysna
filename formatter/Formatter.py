from decimal import Decimal
import math
from typing import List, Optional
from node.NodeType import (
    ExternFnDecl, LambdaExpr, FunctionType as AstFunctionType, Program, Stmt, Expr, TypeNode, NamedType, GenericType, TypeParam, OptionalType, ListType, DictType, TupleType, ResultType, ErrorUnionType,
    Pattern, IdentifierPattern, TuplePattern, EnumPattern, ErrorTypePattern, LiteralPattern, WildcardPattern,
    Block, ExprStmt, Param, FnDecl, StructField, StructDecl, EnumDecl,
    InterfaceMethod, InterfaceDecl, ClassField, MethodDecl, ClassDecl,
    ImportDecl, UseDecl, UseSymbol, LetDecl, WhileStmt, ForStmt, ReturnStmt, BreakStmt, ContinueStmt,
    IntLiteral, FloatLiteral, StringLiteral, BoolLiteral, NoneLiteral,
    IdentExpr, ThisExpr, SuperExpr, NewExpr,
    UnaryExpr, BinaryExpr, CoalesceExpr, RangeExpr, PostfixTryExpr, ElseErrExpr,
    CallExpr, MemberExpr, IndexExpr, AssignmentExpr, FieldInit, StructInitExpr,
    ListExpr, DictEntry, DictExpr, TupleExpr, ElifBranch, IfExpr, MatchArm, MatchExpr
)
from parser.Parser import Parser


PRECEDENCE_MAP = {
    "=": 1,
    "??": 2,
    "or": 3,
    "and": 4,
    "==": 5,
    "!=": 5,
    "<": 6,
    "<=": 6,
    ">": 6,
    ">=": 6,
    "..": 7,
    "..=": 7,
    "+": 8,
    "-": 8,
    "*": 9,
    "/": 9,
    "%": 9,
}


class Formatter:
    """
    Canonical Wysna v0.1 Formatter ("One True Format").
    Guarantees idempotence: fmt(fmt(src)) == fmt(src).
    """
    def __init__(self, indent_width: int = 4):
        self.indent_width = indent_width

    def _indent(self, level: int) -> str:
        return " " * (self.indent_width * level)

    def format(self, program: Program) -> str:
        lines: List[str] = []
        prev_was_decl = False
        for idx, stmt in enumerate(program.body):
            is_decl = isinstance(stmt, (FnDecl, StructDecl, EnumDecl, InterfaceDecl, ClassDecl))
            if idx > 0 and (is_decl or prev_was_decl):
                lines.append("")
            lines.append(self.format_stmt(stmt, 0))
            prev_was_decl = is_decl
        formatted = "\n".join(lines) + "\n"
        from formatter.Trivia import preserve_comments
        return preserve_comments(getattr(program, "source_text", ""), formatted)

    # ==========================================
    # Type Formatting
    # ==========================================

    def format_type(self, t: TypeNode) -> str:
        if isinstance(t, AstFunctionType):
            return "fn(" + ", ".join(self.format_type(p) for p in t.params) + ") -> " + self.format_type(t.return_type)
        if isinstance(t, NamedType):
            return t.name
        if isinstance(t, GenericType):
            args = ", ".join([self.format_type(e) for e in t.type_args])
            return f"{t.name}[{args}]"
        if isinstance(t, OptionalType):
            return f"({self.format_type(t.inner)})?" if isinstance(t.inner, (ResultType, OptionalType, AstFunctionType)) else f"{self.format_type(t.inner)}?"
        if isinstance(t, ListType):
            return f"list[{self.format_type(t.element_type)}]"
        if isinstance(t, DictType):
            return f"dict[{self.format_type(t.key_type)}, {self.format_type(t.value_type)}]"
        if isinstance(t, TupleType):
            inner = ", ".join([self.format_type(e) for e in t.element_types])
            return f"tuple[{inner}]"
        if isinstance(t, ResultType):
            ok = self.format_type(t.ok_type)
            if isinstance(t.ok_type, ResultType):
                ok = f"({ok})"
            return f"{ok} ! {self.format_type(t.err_type)}"
        if isinstance(t, ErrorUnionType):
            return " | ".join(self.format_type(error_type) for error_type in t.error_types)
        return "unit"

    # ==========================================
    # Pattern Formatting
    # ==========================================

    def format_pattern(self, p: Pattern) -> str:
        if isinstance(p, WildcardPattern):
            return "_"
        if isinstance(p, IdentifierPattern):
            return p.name
        if isinstance(p, ErrorTypePattern):
            return f"{p.type_name}({self.format_pattern(p.binding)})"
        if isinstance(p, EnumPattern):
            args_str = f"({', '.join([self.format_pattern(a) for a in p.args])})" if p.args else ""
            if p.enum_name:
                return f"{p.enum_name}.{p.variant}{args_str}"
            return f"{p.variant}{args_str}"
        if isinstance(p, LiteralPattern):
            return self.format_expr(p.literal, 0)
        if isinstance(p, TuplePattern):
            return "(" + ", ".join(self.format_pattern(e) for e in p.elements) + ")"
        return "else"

    # ==========================================
    # Statement Formatting
    # ==========================================

    def format_stmt(self, stmt: Stmt, level: int) -> str:
        rendered = self._format_stmt(stmt, level)
        if getattr(stmt, "is_exported", False):
            indent = self._indent(level)
            rendered = indent + "pub " + rendered[len(indent):]
        return rendered

    def _format_stmt(self, stmt: Stmt, level: int) -> str:
        ind = self._indent(level)
        if isinstance(stmt, ExternFnDecl):
            params = ", ".join(p.name + ": " + self.format_type(p.type_ann) for p in stmt.params)
            return ind + 'extern "C" fn ' + stmt.name + "(" + params + ") -> " + self.format_type(stmt.return_type)

        if isinstance(stmt, UseDecl):
            path_str = self._escape_str(stmt.path) if ('/' in stmt.path or stmt.path.endswith('.wysna')) else stmt.path
            if len(stmt.symbols) == 1 and not stmt.is_braced:
                sym = stmt.symbols[0]
                sub = f"{sym.subpath}." if sym.subpath else ""
                alias = f" as {sym.alias}" if sym.alias else ""
                return f"{ind}from {path_str} use {sub}{sym.name}{alias}"
            elif len(stmt.symbols) == 0:
                alias = f" as {stmt.namespace_alias}" if stmt.namespace_alias else ""
                return f"{ind}from {path_str} use {stmt.namespace}{alias}"
            else:
                sym_lines = []
                inner_ind = self._indent(level + 1)
                for sym in stmt.symbols:
                    sub = f"{sym.subpath}." if sym.subpath else ""
                    alias = f" as {sym.alias}" if sym.alias else ""
                    sym_lines.append(f"{inner_ind}{sub}{sym.name}{alias},")
                inner = "\n".join(sym_lines)
                return f"{ind}from {path_str} use {{\n{inner}\n{ind}}}"

        if isinstance(stmt, ImportDecl):
            import os
            default = os.path.basename(stmt.path).split(".")[0]
            alias = " as " + stmt.namespace if stmt.namespace != default else ""
            return f'{ind}import {self._escape_str(stmt.path)}{alias}'

        if isinstance(stmt, EnumDecl):
            tparams = f"[{', '.join(str(p) for p in stmt.type_params)}]" if stmt.type_params else ""
            var_strs = []
            for v in stmt.variants:
                if hasattr(v, "payload_types") and v.payload_types:
                    types_s = ", ".join(self.format_type(t) for t in v.payload_types)
                    var_strs.append(f"{v.name}({types_s})")
                elif hasattr(v, "name"):
                    var_strs.append(v.name)
                else:
                    var_strs.append(str(v))
            variants = ", ".join(var_strs)
            return f"{ind}enum {stmt.name}{tparams} {{ {variants} }}"

        if isinstance(stmt, StructDecl):
            tparams = f"[{', '.join(str(p) for p in stmt.type_params)}]" if stmt.type_params else ""
            if not stmt.fields:
                return f"{ind}struct {stmt.name}{tparams} {{}}"
            field_lines: List[str] = []
            for f in stmt.fields:
                field_lines.append(f"{self._indent(level + 1)}{f.name}: {self.format_type(f.type_ann)},")
            body = "\n".join(field_lines)
            return f"{ind}struct {stmt.name}{tparams} {{\n{body}\n{ind}}}"

        if isinstance(stmt, InterfaceDecl):
            tparams = f"[{', '.join(str(p) for p in stmt.type_params)}]" if stmt.type_params else ""
            if not stmt.methods:
                return f"{ind}interface {stmt.name}{tparams} {{}}"
            method_lines: List[str] = []
            for m in stmt.methods:
                params_str = ", ".join([f"{p.name}: {self.format_type(p.type_ann)}" for p in m.params])
                ret_str = self.format_type(m.return_type)
                method_lines.append(f"{self._indent(level + 1)}fn {m.name}({params_str}) -> {ret_str}")
            body = "\n".join(method_lines)
            return f"{ind}interface {stmt.name}{tparams} {{\n{body}\n{ind}}}"

        if isinstance(stmt, ClassDecl):
            tparams = f"[{', '.join(str(p) for p in stmt.type_params)}]" if stmt.type_params else ""
            header = f"{ind}class {stmt.name}{tparams}"
            if stmt.superclass:
                header += f" extends {stmt.superclass}"
            if stmt.interfaces:
                header += f" implements {', '.join(str(interface) for interface in stmt.interfaces)}"
            if not stmt.fields and not stmt.methods:
                return f"{header} {{}}"
            member_lines: List[str] = []
            for f in stmt.fields:
                vis = "public " if f.is_public else "private "
                member_lines.append(f"{self._indent(level + 1)}{vis}{f.name}: {self.format_type(f.type_ann)}")
            for m in stmt.methods:
                vis = "public " if m.is_public else "private "
                params_str = ", ".join([f"{p.name}: {self.format_type(p.type_ann)}" for p in m.params])
                ret_str = self.format_type(m.return_type)
                blk_str = self.format_block(m.body, level + 1)
                member_lines.append(f"{self._indent(level + 1)}{vis}fn {m.name}({params_str}) -> {ret_str} {blk_str}")
            body = "\n".join(member_lines)
            return f"{header} {{\n{body}\n{ind}}}"

        if isinstance(stmt, FnDecl):
            tparams = f"[{', '.join(str(p) for p in stmt.type_params)}]" if stmt.type_params else ""
            params_str = ", ".join([f"{p.name}: {self.format_type(p.type_ann)}" for p in stmt.params])
            ret_str = self.format_type(stmt.return_type)
            block_str = self.format_block(stmt.body, level)
            return f"{ind}fn {stmt.name}{tparams}({params_str}) -> {ret_str} {block_str}"

        if isinstance(stmt, LetDecl):
            mut_str = "mut " if stmt.is_mut else ""
            type_str = f": {self.format_type(stmt.type_ann)}" if stmt.type_ann is not None else ""
            val_str = self.format_expr(stmt.initializer, level)
            return f"{ind}let {mut_str}{stmt.name}{type_str} = {val_str}"

        if isinstance(stmt, WhileStmt):
            cond_str = self.format_expr(stmt.condition, level)
            body_str = self.format_block(stmt.body, level)
            return f"{ind}while {cond_str} {body_str}"

        if isinstance(stmt, ForStmt):
            pat_str = self.format_pattern(stmt.pattern)
            iter_str = self.format_expr(stmt.iterable, level)
            body_str = self.format_block(stmt.body, level)
            return f"{ind}for {pat_str} in {iter_str} {body_str}"

        if isinstance(stmt, ReturnStmt):
            if stmt.value is not None:
                return f"{ind}return {self.format_expr(stmt.value, level)}"
            return f"{ind}return"

        if isinstance(stmt, BreakStmt):
            return f"{ind}break"

        if isinstance(stmt, ContinueStmt):
            return f"{ind}continue"

        if isinstance(stmt, ExprStmt):
            return f"{ind}{self.format_expr(stmt.expr, level)}"

        return ""

    def format_block(self, block: Block, level: int) -> str:
        if not block.statements and block.tail_expr is None:
            return "{}"
        lines: List[str] = ["{"]
        for s in block.statements:
            lines.append(self.format_stmt(s, level + 1))
        if block.tail_expr is not None:
            lines.append(f"{self._indent(level + 1)}{self.format_expr(block.tail_expr, level + 1)}")
        lines.append(f"{self._indent(level)}}}")
        return "\n".join(lines)

    # ==========================================
    # Expression Formatting
    # ==========================================

    def _escape_str(self, s: str) -> str:
        out: List[str] = ['"']
        for ch in s:
            if ch == "\\":
                out.append("\\\\")
            elif ch == '"':
                out.append('\\"')
            elif ch == "\n":
                out.append("\\n")
            elif ch == "\r":
                out.append("\\r")
            elif ch == "\t":
                out.append("\\t")
            elif ch == "\0":
                out.append("\\0")
            else:
                out.append(ch)
        out.append('"')
        return "".join(out)

    def _expr_prec(self, expr: Expr) -> int:
        if isinstance(expr, AssignmentExpr):
            return 1
        if isinstance(expr, CoalesceExpr):
            return 2
        if isinstance(expr, BinaryExpr):
            return PRECEDENCE_MAP.get(expr.op, 10)
        if isinstance(expr, RangeExpr):
            return 7
        if isinstance(expr, UnaryExpr):
            return 10
        return 20

    def format_expr(self, expr: Expr, level: int, parent_prec: int = 0) -> str:
        if isinstance(expr, LambdaExpr):
            params = ", ".join(p.name + (": " + self.format_type(p.type_ann) if p.type_ann else "") for p in expr.params)
            returned = " -> " + self.format_type(expr.return_type) if expr.return_type else ""
            result = "|" + params + "|" + returned + " " + self.format_block(expr.body, level)
            return "(" + result + ")" if parent_prec else result
        if isinstance(expr, IntLiteral):
            return str(expr.value)

        if isinstance(expr, FloatLiteral):
            if not math.isfinite(expr.value):
                return 'float("' + str(expr.value) + '")'
            s = format(Decimal(str(expr.value)), "f")
            return s if "." in s else s + ".0"

        if isinstance(expr, StringLiteral):
            return self._escape_str(expr.value)

        if isinstance(expr, BoolLiteral):
            return "true" if expr.value else "false"

        if isinstance(expr, NoneLiteral):
            return "none"

        if isinstance(expr, IdentExpr):
            return expr.name

        if isinstance(expr, ThisExpr):
            return "this"

        if isinstance(expr, SuperExpr):
            return "super"

        if isinstance(expr, NewExpr):
            args_str = ", ".join([self.format_expr(a, level) for a in expr.args])
            tparams = "[" + ", ".join(self.format_type(t) for t in expr.type_args) + "]" if getattr(expr, "type_args", None) else ""
            return f"new {expr.class_name}{tparams}({args_str})"

        if isinstance(expr, UnaryExpr):
            inner = self.format_expr(expr.operand, level, parent_prec=10)
            if expr.op == "not":
                res = f"not {inner}"
            else:
                res = f"-{inner}"
            return f"({res})" if parent_prec > 10 else res

        if isinstance(expr, BinaryExpr):
            my_prec = PRECEDENCE_MAP.get(expr.op, 8)
            left_str = self.format_expr(expr.left, level, parent_prec=my_prec)
            right_str = self.format_expr(expr.right, level, parent_prec=my_prec + 1)
            res = f"{left_str} {expr.op} {right_str}"
            return f"({res})" if my_prec < parent_prec else res

        if isinstance(expr, CoalesceExpr):
            my_prec = 2
            left_str = self.format_expr(expr.left, level, parent_prec=my_prec + 1)
            right_str = self.format_expr(expr.right, level, parent_prec=my_prec)
            res = f"{left_str} ?? {right_str}"
            return f"({res})" if my_prec < parent_prec else res

        if isinstance(expr, RangeExpr):
            my_prec = 7
            op = "..=" if expr.inclusive else ".."
            start_str = self.format_expr(expr.start, level, parent_prec=my_prec + 1)
            end_str = self.format_expr(expr.end, level, parent_prec=my_prec + 1)
            res = f"{start_str}{op}{end_str}"
            return f"({res})" if my_prec < parent_prec else res

        if isinstance(expr, AssignmentExpr):
            target_str = self.format_expr(expr.target, level, parent_prec=2)
            val_str = self.format_expr(expr.value, level, parent_prec=1)
            result = f"{target_str} = {val_str}"
            return f"({result})" if parent_prec > 1 else result

        if isinstance(expr, PostfixTryExpr):
            return f"{self.format_expr(expr.operand, level, parent_prec=15)}?"

        if isinstance(expr, ElseErrExpr):
            op_str = self.format_expr(expr.operand, level, parent_prec=15)
            err_part = f" {expr.err_name}" if expr.err_name else ""
            blk_str = self.format_block(expr.handler, level)
            return f"{op_str} else{err_part} {blk_str}"

        if isinstance(expr, CallExpr):
            callee_str = self.format_expr(expr.callee, level, parent_prec=15)
            targs = f"[{', '.join([self.format_type(a) for a in expr.type_args])}]" if getattr(expr, "type_args", None) else ""
            args_str = ", ".join([self.format_expr(a, level) for a in expr.args])
            return f"{callee_str}{targs}({args_str})"

        if isinstance(expr, MemberExpr):
            obj_str = self.format_expr(expr.object, level, parent_prec=15)
            return f"{obj_str}.{expr.property}"

        if isinstance(expr, IndexExpr):
            obj_str = self.format_expr(expr.object, level, parent_prec=15)
            idx_str = self.format_expr(expr.index, level)
            return f"{obj_str}[{idx_str}]"

        if isinstance(expr, StructInitExpr):
            targs = f"[{', '.join([self.format_type(a) for a in expr.type_args])}]" if getattr(expr, "type_args", None) else ""
            if not expr.fields:
                return f"{expr.struct_name}{targs} {{}}"
            fields_str = ", ".join([f"{f.name}: {self.format_expr(f.value, level)}" for f in expr.fields])
            return f"{expr.struct_name}{targs} {{ {fields_str} }}"

        if isinstance(expr, ListExpr):
            elems_str = ", ".join([self.format_expr(e, level) for e in expr.elements])
            return f"[{elems_str}]"

        if isinstance(expr, DictExpr):
            if not expr.entries:
                return "{}"
            entries_str = ", ".join([f"{self.format_expr(e.key, level)}: {self.format_expr(e.value, level)}" for e in expr.entries])
            return f"{{{entries_str}}}"

        if isinstance(expr, TupleExpr):
            elems_str = ", ".join([self.format_expr(e, level) for e in expr.elements])
            return f"({elems_str}{',' if len(expr.elements) == 1 else ''})"

        if isinstance(expr, IfExpr):
            cond_str = self.format_expr(expr.condition, level)
            then_str = self.format_block(expr.then_branch, level)
            res = f"if {cond_str} {then_str}"
            for eb in expr.elif_branches:
                eb_cond = self.format_expr(eb.condition, level)
                eb_body = self.format_block(eb.body, level)
                res += f" elif {eb_cond} {eb_body}"
            if expr.else_branch is not None:
                else_str = self.format_block(expr.else_branch, level)
                res += f" else {else_str}"
            return res

        if isinstance(expr, MatchExpr):
            subj_str = self.format_expr(expr.subject, level)
            lines: List[str] = [f"match {subj_str} {{"]
            for arm in expr.arms:
                pat_str = "else" if isinstance(arm.pattern, WildcardPattern) else self.format_pattern(arm.pattern)
                if arm.guard is not None:
                    pat_str += ' if ' + self.format_expr(arm.guard, level + 1)
                # If single tail_expr with no statements, can format inline or block
                if not arm.body.statements and arm.body.tail_expr is not None:
                    arm_expr_str = self.format_expr(arm.body.tail_expr, level + 1)
                    lines.append(f"{self._indent(level + 1)}{pat_str} => {arm_expr_str},")
                else:
                    blk_str = self.format_block(arm.body, level + 1)
                    lines.append(f"{self._indent(level + 1)}{pat_str} => {blk_str},")
            lines.append(f"{self._indent(level)}}}")
            return "\n".join(lines)

        return ""


def format_source(source: str, file: str = "<stdin>") -> str:
    ast = Parser(file=file).parse(source)
    return Formatter().format(ast)
