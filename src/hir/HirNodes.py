from typing import Any, Dict, List, Optional, Tuple, Union


class Span:
    def __init__(self, line: int = 1, col: int = 1, file_path=None, file_id=None):
        self.line = line
        self.col = col
        self.file_path = file_path
        self.file_id = file_id

    def __repr__(self) -> str:
        if self.file_id is not None:
            return f"Span({self.line}:{self.col}, file_id={self.file_id})"
        return f"Span({self.line}:{self.col})"


# ==========================================
# Semantic Identifiers & Targets
# ==========================================

class SymbolId:
    """Unique semantic identifier for a variable or temporary in resolved HIR."""
    def __init__(self, id: int, name: str = ""):
        self.id = id
        self.name = name

    def __repr__(self) -> str:
        return f"sym_{self.id}_{self.name}" if self.name else f"sym_{self.id}"

    def __eq__(self, other) -> bool:
        if isinstance(other, SymbolId):
            return self.id == other.id
        if isinstance(other, int):
            return self.id == other
        return False

    def __hash__(self) -> int:
        return hash(self.id)


class FieldId:
    """Offset index and semantic name for a struct or class field."""
    def __init__(self, index: int, name: str):
        self.index = index
        self.name = name

    def __repr__(self) -> str:
        return f"field_{self.index}_{self.name}"

    def __eq__(self, other) -> bool:
        if isinstance(other, FieldId):
            return self.index == other.index and self.name == other.name
        return False

    def __hash__(self) -> int:
        return hash((self.index, self.name))


class VariantId:
    """Tag index and semantic name for an enum variant."""
    def __init__(self, index: int, name: str):
        self.index = index
        self.name = name

    def __repr__(self) -> str:
        return f"var_{self.index}_{self.name}"

    def __eq__(self, other) -> bool:
        if isinstance(other, VariantId):
            return self.index == other.index and self.name == other.name
        return False

    def __hash__(self) -> int:
        return hash((self.index, self.name))


class CallTarget:
    """Abstract base for a resolved call target."""
    pass


class DirectCallTarget(CallTarget):
    """Direct static call target pointing to a declaration."""
    def __init__(self, fn_name: str, decl_id: Optional[Any] = None):
        self.fn_name = fn_name
        self.decl_id = decl_id

    def __repr__(self) -> str:
        return f"DirectTarget({self.fn_name}, decl={self.decl_id})"


class BuiltinCallTarget(CallTarget):
    """Call target pointing to a runtime compiler builtin."""
    def __init__(self, builtin_name: str):
        self.builtin_name = builtin_name

    def __repr__(self) -> str:
        return f"BuiltinTarget({self.builtin_name})"


class CollectionCallTarget(CallTarget):
    """Resolved collection intrinsic; never inferred from a user method's spelling."""
    def __init__(self, receiver_kind: str, method_name: str):
        self.receiver_kind = receiver_kind
        self.method_name = method_name

    def __repr__(self) -> str:
        return f"CollectionTarget({self.receiver_kind}.{self.method_name})"


class VirtualCallTarget(CallTarget):
    """Virtual call target dispatched dynamically or through a vtable slot."""
    def __init__(self, method_name: str, slot: Optional[int] = None, target_class: Optional[str] = None):
        self.method_name = method_name
        self.slot = slot
        self.target_class = target_class

    def __repr__(self) -> str:
        return f"VirtualTarget({self.method_name}, slot={self.slot})"


# ==========================================
# CoW Path Representation
# ==========================================

class CowProjection:
    pass


class CowFieldProjection(CowProjection):
    def __init__(self, field_name: str, field_idx: int = -1, is_class: bool = False):
        self.field_name = field_name
        self.field_idx = field_idx
        self.is_class = is_class

    def __repr__(self) -> str:
        cls_flag = ", class_boundary=True" if self.is_class else ""
        return f".{self.field_name}[idx={self.field_idx}{cls_flag}]"


class CowIndexProjection(CowProjection):
    def __init__(self, index: Any):
        self.index = index

    def __repr__(self) -> str:
        return f"[{self.index}]"


class CowPath:
    def __init__(self, root: Any, projections: List[CowProjection], is_class_boundary: bool = False):
        self.root = root
        self.projections = projections
        self.is_class_boundary = is_class_boundary

    def __repr__(self) -> str:
        projs = "".join(str(p) for p in self.projections)
        return f"CowPath({self.root}{projs})"


# ==========================================
# HIR Base Nodes
# ==========================================

class HirNode:
    def __init__(self, span: Span):
        self.span = span


class HirExpr(HirNode):
    def __init__(self, type_id: int, span: Span):
        super().__init__(span)
        self.type_id = type_id


class HirStmt(HirNode):
    pass


# ==========================================
# HIR Patterns
# ==========================================

class HirPattern(HirNode):
    def __init__(self, type_id: int, span: Span):
        super().__init__(span)
        self.type_id = type_id


class HirWildcardPattern(HirPattern):
    def __init__(self, type_id: int, span: Span):
        super().__init__(type_id, span)

    def __repr__(self) -> str:
        return f"HirWildcard(type_id={self.type_id})"


class HirBindingPattern(HirPattern):
    def __init__(self, name: str, type_id: int, span: Span, symbol_id: Optional[SymbolId] = None, is_mut: bool = False):
        super().__init__(type_id, span)
        self.name = name
        self.symbol_id = symbol_id or SymbolId(0, name)
        self.is_mut = is_mut

    def __repr__(self) -> str:
        return f"HirBinding({self.name}, sym={self.symbol_id}, type_id={self.type_id})"


class HirLiteralPattern(HirPattern):
    def __init__(self, value: Any, literal_kind: str, type_id: int, span: Span):
        super().__init__(type_id, span)
        self.value = value
        self.literal_kind = literal_kind  # "int", "float", "bool", "str", "none"

    @property
    def expr(self) -> Any:
        from hir.HirNodes import HirConstInt, HirConstFloat, HirConstStr, HirConstBool, HirConstNone, HirConstUnit
        if self.literal_kind == "int":
            return HirConstInt(self.value, self.type_id, self.span)
        elif self.literal_kind == "float":
            return HirConstFloat(self.value, self.type_id, self.span)
        elif self.literal_kind == "str":
            return HirConstStr(self.value, self.type_id, self.span)
        elif self.literal_kind == "bool":
            return HirConstBool(self.value, self.type_id, self.span)
        elif self.literal_kind == "none":
            return HirConstNone(self.type_id, self.span)
        return HirConstUnit(self.type_id, self.span)

    def __repr__(self) -> str:
        return f"HirLiteral({self.value!r}, kind={self.literal_kind}, type_id={self.type_id})"


class HirEnumPattern(HirPattern):
    def __init__(
        self,
        enum_name: Optional[str],
        variant_name: str,
        variant_idx: int,
        subpatterns: List[HirPattern],
        type_id: int,
        span: Span,
        variant_id: Optional[VariantId] = None
    ):
        super().__init__(type_id, span)
        self.enum_name = enum_name
        self.variant_name = variant_name
        self.variant_idx = variant_idx
        self.subpatterns = subpatterns
        self.variant_id = variant_id or VariantId(variant_idx, variant_name)

    @property
    def variant(self) -> str:
        return self.variant_name

    @property
    def args(self) -> List[HirPattern]:
        return self.subpatterns

    def __repr__(self) -> str:
        return f"HirEnumPattern({self.enum_name}::{self.variant_name}, idx={self.variant_idx}, sub={self.subpatterns})"


class HirErrorTypePattern(HirPattern):
    def __init__(self, error_type_id: int, binding: HirPattern, type_id: int, span: Span, type_name: str):
        super().__init__(type_id, span)
        self.error_type_id = error_type_id
        self.binding = binding
        self.type_name = type_name


class HirTuplePattern(HirPattern):
    def __init__(self, elements: List[HirPattern], type_id: int, span: Span):
        super().__init__(type_id, span)
        self.elements = elements

    def __repr__(self) -> str:
        return f"HirTuplePattern({self.elements}, type_id={self.type_id})"




# ==========================================
# HIR Top-Level Declarations
# ==========================================

class HirParam:
    def __init__(self, name: str, type_id: int, span: Span, symbol_id: Optional[SymbolId] = None):
        self.name = name
        self.type_id = type_id
        self.span = span
        self.symbol_id = symbol_id or SymbolId(0, name)


class HirBlock(HirNode):
    def __init__(self, statements: List[HirStmt], span: Span, tail_expr: Optional[HirExpr] = None):
        super().__init__(span)
        self.statements = statements
        self.tail_expr = tail_expr


class HirFnDecl(HirNode):
    def __init__(
        self,
        name: str,
        params: List[HirParam],
        return_type_id: int,
        body: HirBlock,
        is_fallible: bool = False,
        err_type_name: str = "",
        is_method: bool = False,
        is_public: bool = True,
        span: Span = Span()
    ):
        super().__init__(span)
        self.name = name
        self.params = params
        self.return_type_id = return_type_id
        self.body = body
        self.is_fallible = is_fallible
        self.err_type_name = err_type_name
        self.is_method = is_method
        self.is_public = is_public


class HirFieldDef:
    def __init__(self, name: str, type_id: int, is_public: bool = True, span: Span = Span()):
        self.name = name
        self.type_id = type_id
        self.is_public = is_public
        self.span = span


class HirStructDecl(HirNode):
    def __init__(self, name: str, type_id: int, fields: List[HirFieldDef], span: Span):
        super().__init__(span)
        self.name = name
        self.type_id = type_id
        self.fields = fields


class HirEnumDecl(HirNode):
    def __init__(self, name: str, type_id: int, variants: List[str], span: Span, payload_types=None):
        super().__init__(span)
        self.name = name
        self.type_id = type_id
        self.variants = variants
        self.payload_types = payload_types or {}


class HirInterfaceDecl(HirNode):
    def __init__(self, name: str, type_id: int, methods: List[Tuple[str, int]], span: Span):
        super().__init__(span)
        self.name = name
        self.type_id = type_id
        self.methods = methods  # [(name, function_type_id)]


class HirClassDecl(HirNode):
    def __init__(
        self,
        name: str,
        type_id: int,
        superclass: Optional[str],
        interfaces: List[str],
        fields: List[HirFieldDef],
        methods: List[HirFnDecl],
        init_method: Optional[HirFnDecl],
        span: Span
    ):
        super().__init__(span)
        self.name = name
        self.type_id = type_id
        self.superclass = superclass
        self.interfaces = interfaces
        self.fields = fields
        self.methods = methods
        self.init_method = init_method


class HirModule:
    def __init__(
        self,
        file_path: str,
        structs: List[HirStructDecl],
        enums: List[HirEnumDecl],
        interfaces: List[HirInterfaceDecl],
        classes: List[HirClassDecl],
        functions: List[HirFnDecl],
        top_level_stmts: List[HirStmt]
    ):
        self.file_path = file_path
        self.structs = structs
        self.enums = enums
        self.interfaces = interfaces
        self.classes = classes
        self.functions = functions
        self.top_level_stmts = top_level_stmts


# ==========================================
# HIR Statements
# ==========================================

class HirLet(HirStmt):
    def __init__(self, name: str, type_id: int, is_mut: bool, init_expr: HirExpr, span: Span, symbol_id: Optional[SymbolId] = None):
        super().__init__(span)
        self.name = name
        self.type_id = type_id
        self.is_mut = is_mut
        self.init_expr = init_expr
        self.symbol_id = symbol_id or SymbolId(0, name)


class HirBindTemp(HirStmt):
    def __init__(self, temp_name: str, type_id: int, init_expr: HirExpr, span: Span, symbol_id: Optional[SymbolId] = None):
        super().__init__(span)
        self.temp_name = temp_name
        self.type_id = type_id
        self.init_expr = init_expr
        self.symbol_id = symbol_id or SymbolId(0, temp_name)


class HirAssign(HirStmt):
    def __init__(self, target: HirExpr, value: HirExpr, span: Span, path: Optional[CowPath] = None):
        super().__init__(span)
        self.target = target
        self.value = value
        self.path = path


class HirWhile(HirStmt):
    def __init__(self, cond_expr: HirExpr, body: HirBlock, span: Span):
        super().__init__(span)
        self.cond_expr = cond_expr
        self.body = body


class HirForList(HirStmt):
    def __init__(self, pattern: Any, elem_type_id: int, list_expr: HirExpr, body: HirBlock, span: Span):
        super().__init__(span)
        self.pattern = pattern
        self.elem_name = getattr(pattern, "name", str(pattern))
        self.elem_type_id = elem_type_id
        self.list_expr = list_expr
        self.body = body


class HirReturn(HirStmt):
    def __init__(self, value_expr: HirExpr, span: Span):
        super().__init__(span)
        self.value_expr = value_expr


class HirBreak(HirStmt):
    def __init__(self, span: Span):
        super().__init__(span)


class HirContinue(HirStmt):
    def __init__(self, span: Span):
        super().__init__(span)


class HirExprStmt(HirStmt):
    def __init__(self, expr: HirExpr, span: Span):
        super().__init__(span)
        self.expr = expr


# ==========================================
# HIR Expressions
# ==========================================

class HirConstInt(HirExpr):
    def __init__(self, value: int, type_id: int, span: Span):
        super().__init__(type_id, span)
        self.value = value


class HirConstFloat(HirExpr):
    def __init__(self, value: float, type_id: int, span: Span):
        super().__init__(type_id, span)
        self.value = value


class HirConstBool(HirExpr):
    def __init__(self, value: bool, type_id: int, span: Span):
        super().__init__(type_id, span)
        self.value = value


class HirConstStr(HirExpr):
    def __init__(self, value: str, type_id: int, span: Span):
        super().__init__(type_id, span)
        self.value = value


class HirConstNone(HirExpr):
    def __init__(self, type_id: int, span: Span):
        super().__init__(type_id, span)


class HirConstUnit(HirExpr):
    def __init__(self, type_id: int, span: Span):
        super().__init__(type_id, span)


class HirLocalRef(HirExpr):
    def __init__(self, name: str, type_id: int, span: Span, symbol_id: Optional[SymbolId] = None):
        super().__init__(type_id, span)
        self.name = name
        self.symbol_id = symbol_id or SymbolId(0, name)


class HirFunctionRef(HirExpr):
    def __init__(self, name, type_id, span):
        super().__init__(type_id, span)
        self.name = name


class HirIndirectCall(HirExpr):
    def __init__(self, callee, args, type_id, span):
        super().__init__(type_id, span)
        self.callee = callee
        self.args = args


class HirThisRef(HirExpr):
    def __init__(self, type_id: int, span: Span):
        super().__init__(type_id, span)


class HirSuperRef(HirExpr):
    def __init__(self, type_id: int, span: Span):
        super().__init__(type_id, span)


class HirUnaryOp(HirExpr):
    def __init__(self, op: str, operand: HirExpr, type_id: int, span: Span):
        super().__init__(type_id, span)
        self.op = op
        self.operand = operand


class HirBinaryOp(HirExpr):
    def __init__(self, op: str, left: HirExpr, right: HirExpr, type_id: int, span: Span):
        super().__init__(type_id, span)
        self.op = op
        self.left = left
        self.right = right


class HirAssignment(HirExpr):
    """An assignment result with one destination plan and one RHS evaluation."""
    def __init__(self, target: HirExpr, value: HirExpr, type_id: int, span: Span,
                 path: Optional[CowPath] = None):
        super().__init__(type_id, span)
        self.target = target
        self.value = value
        self.path = path


class HirSequence(HirExpr):
    """Ordered statement effects followed by a value, evaluated at the expression site."""
    def __init__(self, statements: List[HirStmt], value: HirExpr, type_id: int, span: Span):
        super().__init__(type_id, span)
        self.statements = statements
        self.value = value


class HirConstructStruct(HirExpr):
    def __init__(self, struct_name: str, fields: List[Tuple[str, HirExpr]], type_id: int, span: Span):
        super().__init__(type_id, span)
        self.struct_name = struct_name
        self.fields = fields


class HirConstructEnum(HirExpr):
    def __init__(
        self,
        enum_name: str,
        variant_name: str,
        variant_idx: int,
        type_id: int,
        span: Span,
        payload: Optional[HirExpr] = None,
        variant_id: Optional[VariantId] = None
    ):
        super().__init__(type_id, span)
        self.enum_name = enum_name
        self.variant_name = variant_name
        self.variant_idx = variant_idx
        self.payload = payload
        self.variant_id = variant_id or VariantId(variant_idx, variant_name)


class HirConstructObject(HirExpr):
    def __init__(self, class_name: str, args: List[HirExpr], type_id: int, span: Span):
        super().__init__(type_id, span)
        self.class_name = class_name
        self.args = args


class HirConstructList(HirExpr):
    def __init__(self, elem_type_id: int, elems: List[HirExpr], type_id: int, span: Span):
        super().__init__(type_id, span)
        self.elem_type_id = elem_type_id
        self.elems = elems


class HirConstructDict(HirExpr):
    def __init__(self, key_type_id: int, val_type_id: int, entries: List[Tuple[HirExpr, HirExpr]], type_id: int, span: Span):
        super().__init__(type_id, span)
        self.key_type_id = key_type_id
        self.val_type_id = val_type_id
        self.entries = entries


class HirConstructTuple(HirExpr):
    def __init__(self, elem_type_ids: List[int], elems: List[HirExpr], type_id: int, span: Span):
        super().__init__(type_id, span)
        self.elem_type_ids = elem_type_ids
        self.elems = elems


class HirDirectCall(HirExpr):
    def __init__(
        self,
        fn_name: str,
        args: List[HirExpr],
        type_id: int,
        span: Span,
        decl_id: Optional[Any] = None,
        target: Optional[CallTarget] = None
    ):
        super().__init__(type_id, span)
        self.fn_name = fn_name
        self.args = args
        self.decl_id = decl_id
        self.target = target or DirectCallTarget(fn_name, decl_id)


class HirBuiltinCall(HirExpr):
    def __init__(
        self,
        builtin_name: str,
        args: List[HirExpr],
        type_id: int,
        span: Span,
        target: Optional[CallTarget] = None
    ):
        super().__init__(type_id, span)
        self.builtin_name = builtin_name
        self.args = args
        self.target = target or BuiltinCallTarget(builtin_name)


class HirMethodCall(HirExpr):
    def __init__(
        self,
        receiver: HirExpr,
        method_name: str,
        args: List[HirExpr],
        type_id: int,
        span: Span,
        slot: Optional[int] = None,
        target: Optional[CallTarget] = None
    ):
        super().__init__(type_id, span)
        self.receiver = receiver
        self.method_name = method_name
        self.args = args
        self.slot = slot
        self.target = target or VirtualCallTarget(method_name, slot)


class HirFieldRead(HirExpr):
    def __init__(
        self,
        object: HirExpr,
        field_name: str,
        type_id: int,
        span: Span,
        field_idx: int = -1,
        field_id: Optional[FieldId] = None
    ):
        super().__init__(type_id, span)
        self.object = object
        self.field_name = field_name
        self.field_idx = field_idx
        self.field_id = field_id or (FieldId(field_idx, field_name) if field_idx >= 0 else None)


class HirIndexRead(HirExpr):
    def __init__(self, object: HirExpr, index: HirExpr, type_id: int, span: Span):
        super().__init__(type_id, span)
        self.object = object
        self.index = index


class HirCoerce(HirExpr):
    def __init__(self, expr: HirExpr, target_type_id: int, kind: str, span: Span):
        super().__init__(target_type_id, span)
        self.expr = expr
        self.kind = kind  # "wrap_ok", "wrap_some", "int_to_float", "upcast", "identity"


class HirTryPropagate(HirExpr):
    def __init__(self, operand: HirExpr, kind: str, type_id: int, span: Span):
        super().__init__(type_id, span)
        self.operand = operand
        self.kind = kind  # "optional" or "result"


class HirCoalesce(HirExpr):
    def __init__(self, opt_expr: HirExpr, fallback_expr: HirExpr, type_id: int, span: Span):
        super().__init__(type_id, span)
        self.opt_expr = opt_expr
        self.fallback_expr = fallback_expr


class HirElseHandle(HirExpr):
    def __init__(self, operand: HirExpr, err_var: Optional[str], handler: HirBlock, type_id: int, span: Span):
        super().__init__(type_id, span)
        self.operand = operand
        self.err_var = err_var
        self.handler = handler


class HirRange(HirExpr):
    def __init__(self, start: HirExpr, end: HirExpr, inclusive: bool, type_id: int, span: Span):
        super().__init__(type_id, span)
        self.start = start
        self.end = end
        self.inclusive = inclusive


class HirIf(HirExpr):
    def __init__(
        self,
        cond: HirExpr,
        then_block: HirBlock,
        elifs: List[Tuple[HirExpr, HirBlock]],
        else_block: Optional[HirBlock],
        type_id: int,
        span: Span
    ):
        super().__init__(type_id, span)
        self.cond = cond
        self.then_block = then_block
        self.elifs = elifs
        self.else_block = else_block


class HirMatchArm:
    def __init__(self, pattern: Any, body: HirBlock, span: Span, guard: Optional[HirExpr] = None):
        self.guard = guard
        self.pattern = pattern
        self.body = body
        self.span = span


class HirMatch(HirExpr):
    def __init__(self, subject: HirExpr, arms: List[HirMatchArm], type_id: int, span: Span):
        super().__init__(type_id, span)
        self.subject = subject
        self.arms = arms


class HirClosure(HirExpr):
    def __init__(self, name, environment, type_id, span):
        super().__init__(type_id, span)
        self.name = name
        self.environment = environment
