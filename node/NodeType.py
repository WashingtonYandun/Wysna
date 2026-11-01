import os
from typing import Any, List, Optional, Union

class Node:
    """Base class for all Wysna AST nodes with line/col tracking."""
    def __init__(self, line: int = 1, col: int = 1):
        self.line = line
        self.col = col

    def to_dict(self) -> dict:
        raise NotImplementedError(f"{self.__class__.__name__}.to_dict() not implemented")

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.to_dict()})"


# ==========================================
# Type Annotation AST Nodes
# ==========================================

class TypeNode(Node):
    pass

class NamedType(TypeNode):
    def __init__(self, name: str, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.name = name

    def to_dict(self) -> dict:
        return {"kind": "NamedType", "name": self.name, "line": self.line, "col": self.col}

    def __str__(self) -> str:
        return self.name

class GenericType(TypeNode):
    def __init__(self, name: str, type_args: List[TypeNode], line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.name = name
        self.type_args = type_args

    def to_dict(self) -> dict:
        return {
            "kind": "GenericType",
            "name": self.name,
            "type_args": [t.to_dict() for t in self.type_args],
            "line": self.line,
            "col": self.col
        }

    def __str__(self) -> str:
        args_str = ", ".join(str(a) for a in self.type_args)
        return f"{self.name}[{args_str}]"

class FunctionType(TypeNode):
    def __init__(self, params, return_type, line=1, col=1):
        super().__init__(line, col)
        self.params = params
        self.return_type = return_type

    def to_dict(self):
        return {"kind": "FunctionType", "params": [p.to_dict() for p in self.params],
                "return_type": self.return_type.to_dict(), "line": self.line, "col": self.col}


class TypeParam(Node):
    def __init__(self, name: str, constraint: Optional[TypeNode] = None, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.name = name
        self.constraint = constraint

    def to_dict(self) -> dict:
        d = {"kind": "TypeParam", "name": self.name, "line": self.line, "col": self.col}
        if self.constraint is not None:
            d["constraint"] = self.constraint.to_dict()
        return d

    def __str__(self) -> str:
        if self.constraint:
            return f"{self.name}: {self.constraint}"
        return self.name

class OptionalType(TypeNode):
    def __init__(self, inner: TypeNode, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.inner = inner

    def to_dict(self) -> dict:
        return {"kind": "OptionalType", "inner": self.inner.to_dict(), "line": self.line, "col": self.col}

    def __str__(self) -> str:
        return f"{self.inner}?"

class ListType(TypeNode):
    def __init__(self, element_type: TypeNode, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.element_type = element_type

    def to_dict(self) -> dict:
        return {"kind": "ListType", "element_type": self.element_type.to_dict(), "line": self.line, "col": self.col}

    def __str__(self) -> str:
        return f"list[{self.element_type}]"

class DictType(TypeNode):
    def __init__(self, key_type: TypeNode, value_type: TypeNode, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.key_type = key_type
        self.value_type = value_type

    def to_dict(self) -> dict:
        return {
            "kind": "DictType",
            "key_type": self.key_type.to_dict(),
            "value_type": self.value_type.to_dict(),
            "line": self.line,
            "col": self.col
        }

class TupleType(TypeNode):
    def __init__(self, element_types: List[TypeNode], line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.element_types = element_types

    def to_dict(self) -> dict:
        return {
            "kind": "TupleType",
            "element_types": [t.to_dict() for t in self.element_types],
            "line": self.line,
            "col": self.col
        }

class ResultType(TypeNode):
    """Represents fallible return types: T ! E"""
    def __init__(self, ok_type: TypeNode, err_type: TypeNode, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.ok_type = ok_type
        self.err_type = err_type

    def to_dict(self) -> dict:
        return {
            "kind": "ResultType",
            "ok_type": self.ok_type.to_dict(),
            "err_type": self.err_type.to_dict(),
            "line": self.line,
            "col": self.col
        }

class ErrorUnionType(TypeNode):
    """Represents a closed set of error payload types inside a Result type."""
    def __init__(self, error_types: List[TypeNode], line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.error_types = error_types

    def to_dict(self) -> dict:
        return {
            "kind": "ErrorUnionType",
            "error_types": [t.to_dict() for t in self.error_types],
            "line": self.line,
            "col": self.col
        }


# ==========================================
# Pattern Matching AST Nodes
# ==========================================

class Pattern(Node):
    pass

class IdentifierPattern(Pattern):
    def __init__(self, name: str, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.name = name

    def to_dict(self) -> dict:
        return {"kind": "IdentifierPattern", "name": self.name, "line": self.line, "col": self.col}

class TuplePattern(Pattern):
    def __init__(self, elements: List[Pattern], line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.elements = elements

    def to_dict(self) -> dict:
        return {
            "kind": "TuplePattern",
            "elements": [p.to_dict() for p in self.elements],
            "line": self.line,
            "col": self.col
        }

class EnumPattern(Pattern):
    def __init__(self, enum_name: Optional[str], variant: str, args: Optional[List[Pattern]] = None, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.enum_name = enum_name
        self.variant = variant
        self.args = args or []

    def to_dict(self) -> dict:
        d = {
            "kind": "EnumPattern",
            "variant": self.variant,
            "line": self.line,
            "col": self.col
        }
        if self.enum_name is not None:
            d["enum_name"] = self.enum_name
        if self.args:
            d["args"] = [a.to_dict() for a in self.args]
        return d

class ErrorTypePattern(Pattern):
    """A checked error-union alternative, resolved to nominal type identity."""
    def __init__(self, type_name: str, binding: Pattern, matched_type: Any, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.type_name = type_name
        self.binding = binding
        self.matched_type = matched_type

    def to_dict(self) -> dict:
        return {"kind": "ErrorTypePattern", "type_name": self.type_name,
                "binding": self.binding.to_dict(), "matched_type": str(self.matched_type),
                "line": self.line, "col": self.col}

class LiteralPattern(Pattern):
    def __init__(self, literal: 'Expr', line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.literal = literal

    def to_dict(self) -> dict:
        return {"kind": "LiteralPattern", "literal": self.literal.to_dict(), "line": self.line, "col": self.col}

class WildcardPattern(Pattern):
    def to_dict(self) -> dict:
        return {"kind": "WildcardPattern", "line": self.line, "col": self.col}


# ==========================================
# Block Node (with optional tail expression)
# ==========================================

class Block(Node):
    def __init__(self, statements: List['Stmt'], tail_expr: Optional['Expr'] = None, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.statements = statements
        self.tail_expr = tail_expr

    def to_dict(self) -> dict:
        return {
            "kind": "Block",
            "statements": [s.to_dict() for s in self.statements],
            "tail_expr": self.tail_expr.to_dict() if self.tail_expr else None,
            "line": self.line,
            "col": self.col
        }


# ==========================================
# Statement & Declaration Nodes
# ==========================================

class Stmt(Node):
    pass

class ExprStmt(Stmt):
    def __init__(self, expr: 'Expr', line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.expr = expr

    def to_dict(self) -> dict:
        return {"kind": "ExprStmt", "expr": self.expr.to_dict(), "line": self.line, "col": self.col}

class Param(Node):
    def __init__(self, name: str, type_ann: Optional[TypeNode], line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.name = name
        self.type_ann = type_ann

    def to_dict(self) -> dict:
        return {
            "kind": "Param",
            "name": self.name,
            "type_ann": self.type_ann.to_dict() if self.type_ann else None,
            "line": self.line,
            "col": self.col,
        }

class FnDecl(Stmt):
    def __init__(
        self,
        name: str,
        params: List[Param],
        return_type: TypeNode,
        body: Block,
        type_params: Optional[List[Any]] = None,
        line: int = 1,
        col: int = 1
    ):
        super().__init__(line, col)
        self.name = name
        self.params = params
        self.return_type = return_type
        self.body = body
        self.type_params = type_params or []

    def to_dict(self) -> dict:
        d = {
            "kind": "FnDecl",
            "name": self.name,
            "params": [p.to_dict() for p in self.params],
            "return_type": self.return_type.to_dict(),
            "body": self.body.to_dict(),
            "line": self.line,
            "col": self.col
        }
        if self.type_params:
            d["type_params"] = [p.to_dict() if hasattr(p, "to_dict") else p for p in self.type_params]
        return d

class StructField(Node):
    def __init__(self, name: str, type_ann: TypeNode, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.name = name
        self.type_ann = type_ann

    def to_dict(self) -> dict:
        return {"kind": "StructField", "name": self.name, "type_ann": self.type_ann.to_dict(), "line": self.line, "col": self.col}

class StructDecl(Stmt):
    def __init__(
        self,
        name: str,
        fields: List[StructField],
        type_params: Optional[List[Any]] = None,
        line: int = 1,
        col: int = 1
    ):
        super().__init__(line, col)
        self.name = name
        self.fields = fields
        self.type_params = type_params or []

    def to_dict(self) -> dict:
        d = {
            "kind": "StructDecl",
            "name": self.name,
            "fields": [f.to_dict() for f in self.fields],
            "line": self.line,
            "col": self.col
        }
        if self.type_params:
            d["type_params"] = [p.to_dict() if hasattr(p, "to_dict") else p for p in self.type_params]
        return d

class VariantDecl(Node):
    def __init__(self, name: str, payload_types: Optional[List[TypeNode]] = None, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.name = name
        self.payload_types = payload_types or []

    def to_dict(self) -> dict:
        d = {"kind": "VariantDecl", "name": self.name, "line": self.line, "col": self.col}
        if self.payload_types:
            d["payload_types"] = [t.to_dict() for t in self.payload_types]
        return d

    def __str__(self) -> str:
        if self.payload_types:
            types_str = ", ".join(str(p) for p in self.payload_types)
            return f"{self.name}({types_str})"
        return self.name

class EnumDecl(Stmt):
    def __init__(
        self,
        name: str,
        variants: List[Any],
        type_params: Optional[List[Any]] = None,
        line: int = 1,
        col: int = 1
    ):
        super().__init__(line, col)
        self.name = name
        self.variants: List[VariantDecl] = [
            VariantDecl(v, line=line, col=col) if isinstance(v, str) else v
            for v in variants
        ]
        self.type_params = type_params or []

    def to_dict(self) -> dict:
        d = {
            "kind": "EnumDecl",
            "name": self.name,
            "variants": [v.to_dict() if hasattr(v, "to_dict") else v for v in self.variants],
            "line": self.line,
            "col": self.col
        }
        if self.type_params:
            d["type_params"] = [p.to_dict() if hasattr(p, "to_dict") else p for p in self.type_params]
        return d

class InterfaceMethod(Node):
    def __init__(self, name: str, params: List[Param], return_type: TypeNode, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.name = name
        self.params = params
        self.return_type = return_type

    def to_dict(self) -> dict:
        return {
            "kind": "InterfaceMethod",
            "name": self.name,
            "params": [p.to_dict() for p in self.params],
            "return_type": self.return_type.to_dict(),
            "line": self.line,
            "col": self.col
        }

class InterfaceDecl(Stmt):
    def __init__(self, name: str, methods: List[InterfaceMethod], line: int = 1, col: int = 1, type_params=None):
        super().__init__(line, col)
        self.name = name
        self.methods = methods
        self.type_params = type_params or []

    def to_dict(self) -> dict:
        return {
            "kind": "InterfaceDecl",
            "name": self.name,
            "type_params": [p.to_dict() for p in self.type_params],
            "methods": [m.to_dict() for m in self.methods],
            "line": self.line,
            "col": self.col
        }

class ClassField(Node):
    def __init__(self, name: str, type_ann: TypeNode, is_public: bool = True, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.name = name
        self.type_ann = type_ann
        self.is_public = is_public

    def to_dict(self) -> dict:
        return {
            "kind": "ClassField",
            "name": self.name,
            "type_ann": self.type_ann.to_dict(),
            "is_public": self.is_public,
            "line": self.line,
            "col": self.col
        }

class MethodDecl(Node):
    def __init__(self, name: str, params: List[Param], return_type: TypeNode, body: Block, is_public: bool = True, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.name = name
        self.params = params
        self.return_type = return_type
        self.body = body
        self.is_public = is_public

    def to_dict(self) -> dict:
        return {
            "kind": "MethodDecl",
            "name": self.name,
            "params": [p.to_dict() for p in self.params],
            "return_type": self.return_type.to_dict(),
            "body": self.body.to_dict(),
            "is_public": self.is_public,
            "line": self.line,
            "col": self.col
        }

class ClassDecl(Stmt):
    def __init__(
        self,
        name: str,
        superclass: Optional[str],
        interfaces: List[str],
        fields: List[ClassField],
        methods: List[MethodDecl],
        line: int = 1,
        col: int = 1,
        type_params=None
    ):
        super().__init__(line, col)
        self.name = name
        self.superclass = superclass
        self.interfaces = interfaces
        self.fields = fields
        self.methods = methods
        self.type_params = type_params or []

    def to_dict(self) -> dict:
        return {
            "kind": "ClassDecl",
            "name": self.name,
            "superclass": self.superclass.to_dict() if isinstance(self.superclass, TypeNode) else self.superclass,
            "interfaces": [t.to_dict() if isinstance(t, TypeNode) else t for t in self.interfaces],
            "type_params": [p.to_dict() for p in self.type_params],
            "fields": [f.to_dict() for f in self.fields],
            "methods": [m.to_dict() for m in self.methods],
            "line": self.line,
            "col": self.col
        }


class ImportDecl(Stmt):
    def __init__(self, path: str, namespace: str, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.path = path
        self.namespace = namespace

    def to_dict(self) -> dict:
        return {
            "kind": "ImportDecl",
            "path": self.path,
            "namespace": self.namespace,
            "line": self.line,
            "col": self.col
        }


class UseSymbol(Node):
    def __init__(self, name: str, alias: Optional[str] = None, subpath: Optional[str] = None, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.name = name
        self.alias = alias
        self.subpath = subpath

    @property
    def bound_name(self) -> str:
        return self.alias if self.alias else self.name

    def to_dict(self) -> dict:
        return {
            "kind": "UseSymbol",
            "name": self.name,
            "alias": self.alias,
            "subpath": self.subpath,
            "line": self.line,
            "col": self.col
        }


class UseDecl(ImportDecl):
    def __init__(self, path: str, symbols: List[UseSymbol], is_namespace: bool = False, namespace_alias: Optional[str] = None, is_braced: bool = True, line: int = 1, col: int = 1):
        base = path.split(".")[-1] if "." in path else os.path.basename(path).split(".")[0]
        super().__init__(path=path, namespace=namespace_alias or base, line=line, col=col)
        self.symbols = symbols
        self.is_namespace = is_namespace
        self.namespace_alias = namespace_alias
        self.is_braced = is_braced

    def to_dict(self) -> dict:
        return {
            "kind": "UseDecl",
            "path": self.path,
            "symbols": [s.to_dict() for s in self.symbols],
            "is_namespace": self.is_namespace,
            "namespace_alias": self.namespace_alias,
            "is_braced": self.is_braced,
            "line": self.line,
            "col": self.col
        }

class LetDecl(Stmt):
    def __init__(self, name: str, is_mut: bool, type_ann: Optional[TypeNode], initializer: 'Expr', line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.name = name
        self.is_mut = is_mut
        self.type_ann = type_ann
        self.initializer = initializer

    def to_dict(self) -> dict:
        return {
            "kind": "LetDecl",
            "name": self.name,
            "is_mut": self.is_mut,
            "type_ann": self.type_ann.to_dict() if self.type_ann else None,
            "initializer": self.initializer.to_dict(),
            "line": self.line,
            "col": self.col
        }

class WhileStmt(Stmt):
    def __init__(self, condition: 'Expr', body: Block, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.condition = condition
        self.body = body

    def to_dict(self) -> dict:
        return {
            "kind": "WhileStmt",
            "condition": self.condition.to_dict(),
            "body": self.body.to_dict(),
            "line": self.line,
            "col": self.col
        }

class ForStmt(Stmt):
    def __init__(self, pattern: Pattern, iterable: 'Expr', body: Block, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.pattern = pattern
        self.iterable = iterable
        self.body = body

    def to_dict(self) -> dict:
        return {
            "kind": "ForStmt",
            "pattern": self.pattern.to_dict(),
            "iterable": self.iterable.to_dict(),
            "body": self.body.to_dict(),
            "line": self.line,
            "col": self.col
        }

class ReturnStmt(Stmt):
    def __init__(self, value: Optional['Expr'] = None, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.value = value

    def to_dict(self) -> dict:
        return {
            "kind": "ReturnStmt",
            "value": self.value.to_dict() if self.value else None,
            "line": self.line,
            "col": self.col
        }

class BreakStmt(Stmt):
    def to_dict(self) -> dict:
        return {"kind": "BreakStmt", "line": self.line, "col": self.col}

class ContinueStmt(Stmt):
    def to_dict(self) -> dict:
        return {"kind": "ContinueStmt", "line": self.line, "col": self.col}


# ==========================================
# Expression Nodes
# ==========================================

class Expr(Node):
    pass

class IntLiteral(Expr):
    def __init__(self, value: int, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.value = value

    def to_dict(self) -> dict:
        return {"kind": "IntLiteral", "value": self.value, "line": self.line, "col": self.col}

class FloatLiteral(Expr):
    def __init__(self, value: float, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.value = value

    def to_dict(self) -> dict:
        return {"kind": "FloatLiteral", "value": self.value, "line": self.line, "col": self.col}

class StringLiteral(Expr):
    def __init__(self, value: str, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.value = value

    def to_dict(self) -> dict:
        return {"kind": "StringLiteral", "value": self.value, "line": self.line, "col": self.col}

class BoolLiteral(Expr):
    def __init__(self, value: bool, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.value = value

    def to_dict(self) -> dict:
        return {"kind": "BoolLiteral", "value": self.value, "line": self.line, "col": self.col}

class NoneLiteral(Expr):
    def to_dict(self) -> dict:
        return {"kind": "NoneLiteral", "line": self.line, "col": self.col}

class IdentExpr(Expr):
    def __init__(self, name: str, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.name = name

    def to_dict(self) -> dict:
        return {"kind": "IdentExpr", "name": self.name, "line": self.line, "col": self.col}

class ThisExpr(Expr):
    def to_dict(self) -> dict:
        return {"kind": "ThisExpr", "line": self.line, "col": self.col}

class SuperExpr(Expr):
    def to_dict(self) -> dict:
        return {"kind": "SuperExpr", "line": self.line, "col": self.col}

class NewExpr(Expr):
    def __init__(self, class_name: str, args: List[Expr], line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.class_name = class_name
        self.args = args

    def to_dict(self) -> dict:
        return {
            "kind": "NewExpr",
            "class_name": self.class_name,
            "args": [a.to_dict() for a in self.args],
            "line": self.line,
            "col": self.col
        }

class UnaryExpr(Expr):
    def __init__(self, op: str, operand: Expr, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.op = op
        self.operand = operand

    def to_dict(self) -> dict:
        return {
            "kind": "UnaryExpr",
            "op": self.op,
            "operand": self.operand.to_dict(),
            "line": self.line,
            "col": self.col
        }

class BinaryExpr(Expr):
    def __init__(self, left: Expr, op: str, right: Expr, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.left = left
        self.op = op
        self.right = right

    def to_dict(self) -> dict:
        return {
            "kind": "BinaryExpr",
            "left": self.left.to_dict(),
            "op": self.op,
            "right": self.right.to_dict(),
            "line": self.line,
            "col": self.col
        }

class CoalesceExpr(Expr):
    """Represents null-coalescing: a ?? fallback"""
    def __init__(self, left: Expr, right: Expr, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.left = left
        self.right = right

    def to_dict(self) -> dict:
        return {
            "kind": "CoalesceExpr",
            "left": self.left.to_dict(),
            "right": self.right.to_dict(),
            "line": self.line,
            "col": self.col
        }

class RangeExpr(Expr):
    """Represents numeric range: a..b or a..=b"""
    def __init__(self, start: Expr, end: Expr, inclusive: bool, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.start = start
        self.end = end
        self.inclusive = inclusive

    def to_dict(self) -> dict:
        return {
            "kind": "RangeExpr",
            "start": self.start.to_dict(),
            "end": self.end.to_dict(),
            "inclusive": self.inclusive,
            "line": self.line,
            "col": self.col
        }

class PostfixTryExpr(Expr):
    """Represents postfix early propagation: expr?"""
    def __init__(self, operand: Expr, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.operand = operand

    def to_dict(self) -> dict:
        return {"kind": "PostfixTryExpr", "operand": self.operand.to_dict(), "line": self.line, "col": self.col}

class ElseErrExpr(Expr):
    """Represents in-place fallible/optional handling: expr else err { ... }"""
    def __init__(self, operand: Expr, err_name: Optional[str], handler: Block, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.operand = operand
        self.err_name = err_name
        self.handler = handler

    def to_dict(self) -> dict:
        return {
            "kind": "ElseErrExpr",
            "operand": self.operand.to_dict(),
            "err_name": self.err_name,
            "handler": self.handler.to_dict(),
            "line": self.line,
            "col": self.col
        }


class CallExpr(Expr):
    def __init__(self, callee: Expr, args: List[Expr], type_args: Optional[List[TypeNode]] = None, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.callee = callee
        self.args = args
        self.type_args = type_args or []

    def to_dict(self) -> dict:
        d = {
            "kind": "CallExpr",
            "callee": self.callee.to_dict(),
            "args": [a.to_dict() for a in self.args],
            "line": self.line,
            "col": self.col
        }
        if self.type_args:
            d["type_args"] = [t.to_dict() for t in self.type_args]
        return d

class MemberExpr(Expr):
    def __init__(self, object: Expr, property: str, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.object = object
        self.property = property

    def to_dict(self) -> dict:
        return {
            "kind": "MemberExpr",
            "object": self.object.to_dict(),
            "property": self.property,
            "line": self.line,
            "col": self.col
        }

class IndexExpr(Expr):
    def __init__(self, object: Expr, index: Expr, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.object = object
        self.index = index

    def to_dict(self) -> dict:
        return {
            "kind": "IndexExpr",
            "object": self.object.to_dict(),
            "index": self.index.to_dict(),
            "line": self.line,
            "col": self.col
        }

class AssignmentExpr(Expr):
    def __init__(self, target: Expr, value: Expr, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.target = target
        self.value = value

    def to_dict(self) -> dict:
        return {
            "kind": "AssignmentExpr",
            "target": self.target.to_dict(),
            "value": self.value.to_dict(),
            "line": self.line,
            "col": self.col
        }

class FieldInit(Node):
    def __init__(self, name: str, value: Expr, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.name = name
        self.value = value

    def to_dict(self) -> dict:
        return {"kind": "FieldInit", "name": self.name, "value": self.value.to_dict(), "line": self.line, "col": self.col}

class StructInitExpr(Expr):
    def __init__(self, struct_name: str, fields: List[FieldInit], type_args: Optional[List[TypeNode]] = None, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.struct_name = struct_name
        self.fields = fields
        self.type_args = type_args or []

    def to_dict(self) -> dict:
        d = {
            "kind": "StructInitExpr",
            "struct_name": self.struct_name,
            "fields": [f.to_dict() for f in self.fields],
            "line": self.line,
            "col": self.col
        }
        if self.type_args:
            d["type_args"] = [t.to_dict() for t in self.type_args]
        return d

class ListExpr(Expr):
    def __init__(self, elements: List[Expr], line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.elements = elements

    def to_dict(self) -> dict:
        return {
            "kind": "ListExpr",
            "elements": [e.to_dict() for e in self.elements],
            "line": self.line,
            "col": self.col
        }

class DictEntry(Node):
    def __init__(self, key: Expr, value: Expr, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.key = key
        self.value = value

    def to_dict(self) -> dict:
        return {"kind": "DictEntry", "key": self.key.to_dict(), "value": self.value.to_dict(), "line": self.line, "col": self.col}

class DictExpr(Expr):
    def __init__(self, entries: List[DictEntry], line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.entries = entries

    def to_dict(self) -> dict:
        return {
            "kind": "DictExpr",
            "entries": [e.to_dict() for e in self.entries],
            "line": self.line,
            "col": self.col
        }

class TupleExpr(Expr):
    def __init__(self, elements: List[Expr], line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.elements = elements

    def to_dict(self) -> dict:
        return {
            "kind": "TupleExpr",
            "elements": [e.to_dict() for e in self.elements],
            "line": self.line,
            "col": self.col
        }

class ElifBranch(Node):
    def __init__(self, condition: Expr, body: Block, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.condition = condition
        self.body = body

    def to_dict(self) -> dict:
        return {
            "kind": "ElifBranch",
            "condition": self.condition.to_dict(),
            "body": self.body.to_dict(),
            "line": self.line,
            "col": self.col
        }

class IfExpr(Expr):
    def __init__(self, condition: Expr, then_branch: Block, elif_branches: List[ElifBranch], else_branch: Optional[Block], line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.condition = condition
        self.then_branch = then_branch
        self.elif_branches = elif_branches
        self.else_branch = else_branch

    def to_dict(self) -> dict:
        return {
            "kind": "IfExpr",
            "condition": self.condition.to_dict(),
            "then_branch": self.then_branch.to_dict(),
            "elif_branches": [b.to_dict() for b in self.elif_branches],
            "else_branch": self.else_branch.to_dict() if self.else_branch else None,
            "line": self.line,
            "col": self.col
        }

class MatchArm(Node):
    def __init__(self, pattern: Pattern, body: Block, guard: Optional[Expr] = None, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.pattern = pattern
        self.body = body
        self.guard = guard

    def to_dict(self) -> dict:
        return {
            "kind": "MatchArm",
            "pattern": self.pattern.to_dict(),
            "guard": self.guard.to_dict() if self.guard else None,
            "body": self.body.to_dict(),
            "line": self.line,
            "col": self.col
        }

class MatchExpr(Expr):
    def __init__(self, subject: Expr, arms: List[MatchArm], line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.subject = subject
        self.arms = arms

    def to_dict(self) -> dict:
        return {
            "kind": "MatchExpr",
            "subject": self.subject.to_dict(),
            "arms": [a.to_dict() for a in self.arms],
            "line": self.line,
            "col": self.col
        }


# ==========================================
# Root Program Node
# ==========================================

class Program(Node):
    def __init__(self, body: List[Stmt] = None, line: int = 1, col: int = 1):
        super().__init__(line, col)
        self.body = body if body is not None else []

    def to_dict(self) -> dict:
        return {
            "wysna_ast_version": "0.1",
            "kind": "Program",
            "body": [s.to_dict() for s in self.body],
            "line": self.line,
            "col": self.col
        }

    # Backward compatibility helpers
    @property
    def kind(self) -> str:
        return "Program"

class LambdaExpr(Expr):
    def __init__(self, params, body, return_type=None, line=1, col=1):
        super().__init__(line, col)
        self.params = params
        self.body = body
        self.return_type = return_type

    def to_dict(self):
        return {"kind": "LambdaExpr", "params": [p.to_dict() for p in self.params],
                "body": self.body.to_dict(), "return_type": self.return_type.to_dict() if self.return_type else None,
                "line": self.line, "col": self.col}


class ExternFnDecl(FnDecl):
    def __init__(self, name, params, return_type, line=1, col=1):
        super().__init__(name, params, return_type, Block([]), line=line, col=col)
        self.extern_abi = "C"

    def to_dict(self):
        value = super().to_dict()
        value["kind"] = "ExternFnDecl"
        value["abi"] = self.extern_abi
        value.pop("body", None)
        return value


def clone_ast(node: Any, memo: Optional[dict] = None) -> Any:
    """
    High-performance specialized AST cloner.
    Replaces generic copy.deepcopy by avoiding pickle dispatch, reflection,
    and unnecessary memoization on immutable scalars.
    Preserves node identities registered in `memo` (e.g. outer symbols).
    """
    if node is None:
        return None
    if memo is not None and id(node) in memo:
        return memo[id(node)]
    if isinstance(node, (int, float, str, bool)):
        return node
    if isinstance(node, list):
        res = [clone_ast(item, memo) for item in node]
        if memo is not None:
            memo[id(node)] = res
        return res
    if isinstance(node, tuple):
        res = tuple(clone_ast(item, memo) for item in node)
        if memo is not None:
            memo[id(node)] = res
        return res
    if isinstance(node, dict):
        res = {k: clone_ast(v, memo) for k, v in node.items()}
        if memo is not None:
            memo[id(node)] = res
        return res
    if isinstance(node, Node):
        cls = node.__class__
        new_node = cls.__new__(cls)
        if memo is not None:
            memo[id(node)] = new_node
        for k, v in node.__dict__.items():
            new_node.__dict__[k] = clone_ast(v, memo)
        return new_node
    import copy
    return copy.deepcopy(node, memo)

