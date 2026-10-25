from error.Error import WysnaError
from typing import Any, Dict, List, Optional, Tuple, Set
from node.NodeType import (
    ExternFnDecl, LambdaExpr, Program, Stmt, Expr, TypeNode, ResultType as AstResultType,
    ErrorUnionType as AstErrorUnionType,
    NamedType, OptionalType as AstOptionalType, ListType as AstListType, DictType as AstDictType,
    GenericType,
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
from checker.TypeInterner import (
    TypeInterner, TYPE_ERROR, TYPE_UNIT, TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STR, TYPE_NONE
)
from hir.HirNodes import (
    HirClosure, HirFunctionRef, HirIndirectCall, Span, HirNode, HirExpr, HirStmt, HirParam, HirBlock, HirFieldDef,
    HirFnDecl, HirStructDecl, HirEnumDecl, HirInterfaceDecl, HirClassDecl, HirModule,
    HirLet, HirBindTemp, HirAssign, HirWhile, HirForList, HirReturn, HirBreak, HirContinue, HirExprStmt,
    HirConstInt, HirConstFloat, HirConstBool, HirConstStr, HirConstNone, HirConstUnit,
    HirLocalRef, HirThisRef, HirSuperRef, HirUnaryOp, HirBinaryOp, HirSequence, HirAssignment,
    HirConstructStruct, HirConstructEnum, HirConstructObject, HirConstructList, HirConstructDict, HirConstructTuple,
    HirDirectCall, HirBuiltinCall, HirMethodCall, HirFieldRead, HirIndexRead,
    HirTryPropagate, HirCoalesce, HirElseHandle, HirIf, HirMatch, HirMatchArm, HirRange,
    SymbolId, FieldId, VariantId, CallTarget, DirectCallTarget, BuiltinCallTarget, VirtualCallTarget, CollectionCallTarget,
    CowPath, CowProjection, CowFieldProjection, CowIndexProjection,
    HirPattern, HirWildcardPattern, HirBindingPattern, HirLiteralPattern, HirEnumPattern, HirErrorTypePattern, HirTuplePattern,
    HirCoerce
)


BUILTIN_NAMES = {
    "weak", "upgrade", "print", "eprint", "len", "range", "ord", "chr", "slice", "str", "int", "float", "bool",
    "join_strings", "bytes", "args", "env", "unix_time", "exit", "enumerate", "dict_items", "read_file", "write_file",
}


class HirBuilder:
    """
    Lowers a fully type-checked surface AST into a canonical Typed HIR.
    Invariants guaranteed:
    1. Every HirExpr carries an interned TypeId.
    2. Constructors ClassName(...) and new ClassName(...) are normalized to HirConstructObject.
    3. Missing function return expressions are normalized to HirConstUnit.
    4. Method calls and member accesses are categorized.
    5. Native HirPattern hierarchy replaces surface AST patterns.
    6. Unique SymbolIds are resolved for all lexical variables.
    """
    def __init__(self, interner: TypeInterner, file_path: str = "<stdin>"):
        self.interner = interner
        self.file_path = file_path
        self.classes: Set[str] = set()
        self.structs: Set[str] = set()
        self.enums: Set[str] = {"Option", "Result"}
        self.enum_variants: Dict[str, str] = {
            "Some": "Option",
            "None": "Option",
            "Ok": "Result",
            "Err": "Result",
        }
        self.enum_payloads: Dict[str, Dict[str, List[int]]] = {}
        self.modules: Set[str] = set()
        self.imported_files: Set[str] = set()
        self.symbol_counter = 0
        self.scopes: List[Dict[str, SymbolId]] = [{}]
        self.assign_temp_counter = 0

    def _push_scope(self) -> None:
        self.scopes.append({})

    def _pop_scope(self) -> None:
        if len(self.scopes) > 1:
            self.scopes.pop()

    def _declare_symbol(self, name: str) -> SymbolId:
        self.symbol_counter += 1
        sym = SymbolId(self.symbol_counter, name)
        self.scopes[-1][name] = sym
        return sym

    def _resolve_symbol(self, name: str) -> SymbolId:
        for scope in reversed(self.scopes):
            if name in scope:
                return scope[name]
        self.symbol_counter += 1
        sym = SymbolId(self.symbol_counter, name)
        self.scopes[0][name] = sym
        return sym

    def _build_cow_path(self, tgt_hir: HirExpr) -> Optional[CowPath]:
        if isinstance(tgt_hir, HirLocalRef):
            return CowPath(root=tgt_hir, projections=[])
        if isinstance(tgt_hir, HirFieldRead):
            parent_path = self._build_cow_path(tgt_hir.object)
            if parent_path is not None:
                is_class = False
                entry = self.interner.get_entry(tgt_hir.object.type_id)
                if entry and entry.tag == "class":
                    is_class = True
                proj = CowFieldProjection(tgt_hir.field_name, tgt_hir.field_idx, is_class=is_class)
                parent_path.projections.append(proj)
                if is_class:
                    parent_path.is_class_boundary = True
                return parent_path
        if isinstance(tgt_hir, HirIndexRead):
            parent_path = self._build_cow_path(tgt_hir.object)
            if parent_path is not None:
                proj = CowIndexProjection(tgt_hir.index)
                parent_path.projections.append(proj)
                return parent_path
        return None

    def _lower_pattern(self, pat: Any, expected_tid: int) -> HirPattern:
        if isinstance(pat, HirPattern):
            return pat
        span = self._span(pat)
        if isinstance(pat, WildcardPattern):
            return HirWildcardPattern(expected_tid, span)
        if isinstance(pat, IdentifierPattern):
            if pat.name in ("None", "none"):
                return HirLiteralPattern(None, "none", TYPE_NONE, span)
            sym = self._declare_symbol(pat.name)
            return HirBindingPattern(pat.name, expected_tid, span, symbol_id=sym)
        if isinstance(pat, LiteralPattern):
            lit = pat.literal
            if isinstance(lit, IntLiteral):
                return HirLiteralPattern(lit.value, "int", TYPE_INT, span)
            if isinstance(lit, FloatLiteral):
                return HirLiteralPattern(lit.value, "float", TYPE_FLOAT, span)
            if isinstance(lit, BoolLiteral):
                return HirLiteralPattern(lit.value, "bool", TYPE_BOOL, span)
            if isinstance(lit, StringLiteral):
                return HirLiteralPattern(lit.value, "str", TYPE_STR, span)
            if isinstance(lit, NoneLiteral):
                return HirLiteralPattern(None, "none", TYPE_NONE, span)
            return HirLiteralPattern(getattr(lit, "value", None), "unknown", expected_tid, span)
        if isinstance(pat, TuplePattern):
            entry = self.interner.get_entry(expected_tid)
            if entry.tag != "tuple" or len(entry.data) != len(pat.elements):
                self._fail(pat, "Tuple pattern has no matching resolved tuple type")
            subtypes = list(entry.data)
            elems = [self._lower_pattern(p, t) for p, t in zip(pat.elements, subtypes)]
            return HirTuplePattern(elems, expected_tid, span)
        if isinstance(pat, ErrorTypePattern):
            matched_type_id = self.interner.intern(pat.matched_type)
            return HirErrorTypePattern(matched_type_id, self._lower_pattern(pat.binding, matched_type_id),
                                       expected_tid, span, pat.type_name)
        if isinstance(pat, EnumPattern):
            variant = pat.variant
            enum_name = pat.enum_name
            entry = self.interner.get_entry(expected_tid)
            subtypes = []
            if variant in ("Some", "None") and (enum_name is None or enum_name.startswith("Option")):
                enum_name = "Option"
                variant_idx = 0 if variant == "Some" else 1
                if entry.tag == "optional" and variant == "Some":
                    subtypes = [entry.data]
                elif entry.tag != "optional":
                    self._fail(pat, "Option pattern has no matching resolved optional type")
            elif variant in ("Ok", "Err") and (enum_name is None or enum_name.startswith("Result")):
                enum_name = "Result"
                variant_idx = 0 if variant == "Ok" else 1
                if entry.tag == "result":
                    subtypes = [entry.data[0]] if variant == "Ok" else [entry.data[1]]
                else:
                    self._fail(pat, "Result pattern has no matching resolved result type")
            else:
                if enum_name is None and variant in self.enum_variants:
                    enum_name = self.enum_variants[variant]
                if entry.tag != "enum":
                    self._fail(pat, "Enum pattern has no matching resolved enum type")
                enum_def = self.interner.enum_defs.get(entry.data)
                if enum_def is None or variant not in enum_def.variants:
                    self._fail(pat, "Enum pattern variant is missing from the resolved enum definition")
                if enum_name != entry.data:
                    self._fail(pat, "Enum pattern owner does not match its resolved enum type")
                variant_idx = enum_def.variants.index(variant)
                subtypes = enum_def.variant_payloads.get(variant, [])
            args = getattr(pat, "args", getattr(pat, "payload", []))
            if len(args) != len(subtypes):
                self._fail(pat, "Enum pattern payload does not match its resolved variant")
            lowered_subs = []
            for idx, arg_p in enumerate(args):
                lowered_subs.append(self._lower_pattern(arg_p, subtypes[idx]))
            return HirEnumPattern(enum_name, variant, variant_idx, lowered_subs, expected_tid, span,
                                  variant_id=VariantId(variant_idx, variant))
        self._fail(pat, f"Unsupported checked pattern: {type(pat).__name__}")

    def _span(self, node):
        return Span(node.line, node.col, file_path=getattr(node, 'source_file', self.file_path),
                    file_id=getattr(node, 'module_id', None))

    def lower(self, program: Program) -> HirModule:
        if hasattr(program, "session"):
            if self.interner is not program.session.interner:
                raise WysnaError("HIR builder must use the checked module arena", file=self.file_path,
                             code="W9999", stage="hir")
            program = program.session.link(program)
        from codegen.VirtualDispatch import virtual_method_slots
        self.virtual_slots = virtual_method_slots(
            (method.name, len(method.params))
            for declaration in program.body if isinstance(declaration, (ClassDecl, InterfaceDecl))
            for method in declaration.methods if method.name != "init")
        # Pre-scan top-level nominal types to distinguish constructors from calls
        for stmt in program.body:
            if isinstance(stmt, ClassDecl):
                self.classes.add(stmt.name)
            elif isinstance(stmt, StructDecl):
                self.structs.add(stmt.name)
            elif isinstance(stmt, EnumDecl):
                self.enums.add(stmt.name)
                payloads = {variant.name: [self._get_type_id_from_node(type_) for type_ in variant.payload_types]
                            for variant in stmt.variants if hasattr(variant, "payload_types")}
                self.enum_payloads[stmt.name] = payloads
                self.interner.mk_enum(stmt.name,
                    [variant.name for variant in stmt.variants], payloads)
                for v in stmt.variants:
                    v_name = v.name if hasattr(v, "name") else str(v)
                    self.enum_variants[v_name] = stmt.name

        struct_decls: List[HirStructDecl] = []
        enum_decls: List[HirEnumDecl] = []
        intf_decls: List[HirInterfaceDecl] = []
        class_decls: List[HirClassDecl] = []
        fn_decls: List[HirFnDecl] = []
        top_stmts: List[HirStmt] = []

        for stmt in program.body:
            if isinstance(stmt, StructDecl):
                struct_decls.append(self._lower_struct_decl(stmt))
            elif isinstance(stmt, EnumDecl):
                enum_decls.append(self._lower_enum_decl(stmt))
            elif isinstance(stmt, InterfaceDecl):
                intf_decls.append(self._lower_interface_decl(stmt))
            elif isinstance(stmt, ClassDecl):
                class_decls.append(self._lower_class_decl(stmt))
            elif isinstance(stmt, FnDecl):
                fn_decls.append(self._lower_fn_decl(stmt))
            elif isinstance(stmt, ImportDecl):
                self._handle_import(stmt, fn_decls)
            else:
                top_stmts.append(self._lower_stmt(stmt))

        module = HirModule(
            file_path=self.file_path,
            structs=struct_decls,
            enums=enum_decls,
            interfaces=intf_decls,
            classes=class_decls,
            functions=fn_decls,
            top_level_stmts=top_stmts
        )
        module.arena_id = self.interner.arena_id
        module.call_entrypoint = getattr(program, "call_entrypoint", not top_stmts)
        from hir.HirVerifier import HirVerifier
        HirVerifier(self.interner, self.file_path).verify(module)
        return module

    def _handle_import(self, stmt: ImportDecl, fn_decls: List[HirFnDecl]) -> None:
        raise WysnaError("HIR imports require a checked compilation session", file=self.file_path,
                     line=stmt.line, col=stmt.col, code="W9999", stage="hir")

    # -------------------------------------------------------------
    # Nominal Declarations
    # -------------------------------------------------------------

    def _lower_struct_decl(self, decl: StructDecl) -> HirStructDecl:
        tid = self.interner.mk_struct(decl.name)
        fields = [
            HirFieldDef(f.name, self._get_type_id_from_node(getattr(f, "type_ann", getattr(f, "type_annotation", None))), is_public=True, span=self._span(f))
            for f in decl.fields
        ]
        return HirStructDecl(decl.name, tid, fields, self._span(decl))

    def _lower_enum_decl(self, decl: EnumDecl) -> HirEnumDecl:
        variants = [variant.name if hasattr(variant, "name") else variant for variant in decl.variants]
        payloads = {variant.name: [self._get_type_id_from_node(type_) for type_ in variant.payload_types]
                    for variant in decl.variants if hasattr(variant, "payload_types")}
        tid = self.interner.mk_enum(decl.name, variants, payloads)
        return HirEnumDecl(decl.name, tid, variants, self._span(decl), payloads)

    def _lower_interface_decl(self, decl: InterfaceDecl) -> HirInterfaceDecl:
        methods: List[Tuple[str, int]] = []
        for m in decl.methods:
            params = tuple(self._get_type_id_from_node(getattr(p, "type_ann", getattr(p, "type_annotation", None))) for p in m.params)
            ret = self._get_type_id_from_node(m.return_type) if m.return_type else TYPE_UNIT
            fn_tid = self.interner.mk_function(params, ret)
            methods.append((m.name, fn_tid))
        tid = self.interner.mk_interface(decl.name, dict(methods))
        return HirInterfaceDecl(decl.name, tid, methods, self._span(decl))

    def _lower_class_decl(self, decl: ClassDecl) -> HirClassDecl:
        tid = self.interner.mk_class(decl.name)
        fields = [
            HirFieldDef(f.name, self._get_type_id_from_node(getattr(f, "type_ann", getattr(f, "type_annotation", None))), is_public=f.is_public, span=self._span(f))
            for f in decl.fields
        ]
        methods: List[HirFnDecl] = []
        init_method: Optional[HirFnDecl] = None
        for m in decl.methods:
            m_hir = self._lower_method_decl(m)
            if m.name == "init":
                init_method = m_hir
            methods.append(m_hir)

        return HirClassDecl(
            name=decl.name,
            type_id=tid,
            superclass=decl.superclass,
            interfaces=decl.interfaces,
            fields=fields,
            methods=methods,
            init_method=init_method,
            span=self._span(decl)
        )

    def _lower_fn_decl(self, decl: FnDecl) -> HirFnDecl:
        if isinstance(decl, ExternFnDecl):
            self._push_scope()
            params = [HirParam(p.name, self._get_type_id_from_node(p.type_ann), self._span(p),
                               symbol_id=self._declare_symbol(p.name)) for p in decl.params]
            self._pop_scope()
            fn = HirFnDecl(decl.name, params, self._get_type_id_from_node(decl.return_type),
                           HirBlock([], self._span(decl.body)), span=self._span(decl))
            fn.extern_types = ([p.type_ann.name for p in decl.params], decl.return_type.name)
            fn.extern_name = getattr(decl, "source_name", decl.name)
            return fn
        self._push_scope()
        params = [
            HirParam(
                p.name,
                self._get_type_id_from_node(getattr(p, "type_ann", getattr(p, "type_annotation", None))),
                self._span(p),
                symbol_id=self._declare_symbol(p.name)
            )
            for p in decl.params
        ]
        ret_tid = self._get_type_id_from_node(decl.return_type) if decl.return_type else TYPE_UNIT
        body_hir = self._lower_block(decl.body)
        self._pop_scope()
        return_entry = self.interner.get_entry(ret_tid)
        if return_entry.tag == "result" and return_entry.data[0] == TYPE_UNIT:
            self._discard_unit_result_tail(body_hir)
        if ret_tid == TYPE_UNIT:
            if body_hir.tail_expr is not None and body_hir.tail_expr.type_id != TYPE_UNIT:
                body_hir.statements.append(HirExprStmt(body_hir.tail_expr, body_hir.tail_expr.span))
                body_hir.tail_expr = HirConstUnit(TYPE_UNIT, body_hir.tail_expr.span)
            elif body_hir.tail_expr is None:
                body_hir.tail_expr = HirConstUnit(TYPE_UNIT, body_hir.span)

        is_fallible = False
        err_type_name = ""
        if decl.return_type and isinstance(decl.return_type, AstResultType):
            is_fallible = True
            err_type_name = self._get_type_name_from_node(decl.return_type.err_type)

        return HirFnDecl(
            name=decl.name,
            params=params,
            return_type_id=ret_tid,
            body=body_hir,
            is_fallible=is_fallible,
            err_type_name=err_type_name,
            is_method=False,
            is_public=bool(getattr(decl, "is_exported", False)),
            span=self._span(decl)
        )

    def _discard_unit_result_tail(self, body):
        tail = body.tail_expr
        if isinstance(tail, HirIf):
            branches = [tail.then_block] + [branch for _, branch in tail.elifs]
            if tail.else_block is not None:
                branches.append(tail.else_block)
            for branch in branches:
                self._discard_unit_result_tail(branch)
            tail.type_id = TYPE_UNIT
        elif isinstance(tail, HirMatch):
            for arm in tail.arms:
                self._discard_unit_result_tail(arm.body)
            tail.type_id = TYPE_UNIT
        if tail is not None:
            entry = self.interner.get_entry(tail.type_id)
            if entry.tag == "result":
                tail = HirTryPropagate(tail, "result", entry.data[0], tail.span)
            body.statements.append(HirExprStmt(tail, tail.span))
        body.tail_expr = HirConstUnit(TYPE_UNIT, body.span)

    def _lower_method_decl(self, decl: MethodDecl) -> HirFnDecl:
        self._push_scope()
        params = [
            HirParam(
                p.name,
                self._get_type_id_from_node(getattr(p, "type_ann", getattr(p, "type_annotation", None))),
                self._span(p),
                symbol_id=self._declare_symbol(p.name)
            )
            for p in decl.params
        ]
        ret_tid = self._get_type_id_from_node(decl.return_type) if decl.return_type else TYPE_UNIT
        body_hir = self._lower_block(decl.body)
        self._pop_scope()
        return_entry = self.interner.get_entry(ret_tid)
        if return_entry.tag == "result" and return_entry.data[0] == TYPE_UNIT:
            self._discard_unit_result_tail(body_hir)
        if ret_tid == TYPE_UNIT:
            if body_hir.tail_expr is not None and body_hir.tail_expr.type_id != TYPE_UNIT:
                body_hir.statements.append(HirExprStmt(body_hir.tail_expr, body_hir.tail_expr.span))
                body_hir.tail_expr = HirConstUnit(TYPE_UNIT, body_hir.tail_expr.span)
            elif body_hir.tail_expr is None:
                body_hir.tail_expr = HirConstUnit(TYPE_UNIT, body_hir.span)

        is_fallible = False
        err_type_name = ""
        if decl.return_type and isinstance(decl.return_type, AstResultType):
            is_fallible = True
            err_type_name = self._get_type_name_from_node(decl.return_type.err_type)

        return HirFnDecl(
            name=decl.name,
            params=params,
            return_type_id=ret_tid,
            body=body_hir,
            is_fallible=is_fallible,
            err_type_name=err_type_name,
            is_method=True,
            is_public=decl.is_public,
            span=self._span(decl)
        )

    # -------------------------------------------------------------
    # Blocks and Statements
    # -------------------------------------------------------------

    def _lower_block(self, block: Block) -> HirBlock:
        self._push_scope()
        stmts: List[HirStmt] = []
        for statement in block.statements:
            stmts.append(self._lower_stmt(statement))
        tail_hir = self._lower_expr(block.tail_expr) if block.tail_expr is not None else None
        self._pop_scope()
        return HirBlock(stmts, self._span(block), tail_expr=tail_hir)

    def _lower_assignment_expression(self, expr: AssignmentExpr) -> HirExpr:
        target = self._lower_expr(expr.target)
        value = self._lower_expr(expr.value)
        return HirAssignment(target, value, self._get_expr_type_id(expr),
                             self._span(expr), path=self._build_cow_path(target))

    def _lower_stmt(self, stmt: Stmt) -> HirStmt:
        span = self._span(stmt)
        if isinstance(stmt, LetDecl):
            ann_node = getattr(stmt, "type_ann", getattr(stmt, "type_annotation", None))
            tid = self._get_type_id_from_node(ann_node) if ann_node else self._get_expr_type_id(stmt.initializer)
            init_hir = self._lower_expr(stmt.initializer)
            sym = self._declare_symbol(stmt.name)
            return HirLet(stmt.name, tid, stmt.is_mut, init_hir, span, symbol_id=sym)
        elif isinstance(stmt, WhileStmt):
            cond_hir = self._lower_expr(stmt.condition)
            body_hir = self._lower_block(stmt.body)
            return HirWhile(cond_hir, body_hir, span)
        elif isinstance(stmt, ForStmt):
            list_hir = self._lower_expr(stmt.iterable)
            list_tid = list_hir.type_id
            elem_tid = TYPE_INT
            entry = self.interner.get_entry(list_tid)
            if entry.tag == "list":
                elem_tid = entry.data
            self._push_scope()
            pat_hir = self._lower_pattern(stmt.pattern, elem_tid)
            body_hir = self._lower_block(stmt.body)
            self._pop_scope()
            return HirForList(pat_hir, elem_tid, list_hir, body_hir, span)
        elif isinstance(stmt, ReturnStmt):
            if stmt.value is not None:
                val_hir = self._lower_expr(stmt.value)
            else:
                val_hir = HirConstUnit(TYPE_UNIT, span)
            return HirReturn(val_hir, span)
        elif isinstance(stmt, BreakStmt):
            return HirBreak(span)
        elif isinstance(stmt, ContinueStmt):
            return HirContinue(span)
        elif isinstance(stmt, ExprStmt):
            if isinstance(stmt.expr, AssignmentExpr):
                tgt_hir = self._lower_expr(stmt.expr.target)
                val_hir = self._lower_expr(stmt.expr.value)
                path = self._build_cow_path(tgt_hir)
                return HirAssign(tgt_hir, val_hir, span, path=path)
            return HirExprStmt(self._lower_expr(stmt.expr), span)

        # Fallback to expression statement
        raise WysnaError(f"Unsupported AST statement: {type(stmt).__name__}",
                     line=span.line, col=span.col, file=self.file_path,
                     code="W9999", stage="hir")

    # -------------------------------------------------------------
    # Expressions
    # -------------------------------------------------------------

    def _construct_enum(self, name, variant, index, type_id, span, payload=None):
        entry = self.interner.get_entry(type_id)
        if entry.tag in ("optional", "result"):
            name = "Option" if entry.tag == "optional" else "Result"
        if entry.tag == "enum":
            definition = self.interner.enum_defs.get(entry.data)
            if definition and variant in definition.variants:
                index = definition.variants.index(variant)
        return HirConstructEnum(name, variant, index, type_id, span, payload, variant_id=VariantId(index, variant))

    def _lower_expr(self, expr: Expr) -> HirExpr:
        value = self._lower_expr_impl(expr)
        if hasattr(expr, "force_result_wrap_id"):
            return HirCoerce(value, expr.force_result_wrap_id, kind="wrap_ok", span=self._span(expr))
        return value

    def _lower_expr_impl(self, expr: Expr) -> HirExpr:
        span = self._span(expr)
        tid = self._get_expr_type_id(expr)

        if hasattr(expr, "function_reference"):
            return HirFunctionRef(expr.function_reference, tid, span)
        if isinstance(expr, LambdaExpr):
            return HirClosure(expr.function_name, self._lower_expr(expr.environment), tid, span)
        if isinstance(expr, IntLiteral):
            return HirConstInt(expr.value, tid or TYPE_INT, span)
        elif isinstance(expr, FloatLiteral):
            return HirConstFloat(expr.value, tid or TYPE_FLOAT, span)
        elif isinstance(expr, BoolLiteral):
            return HirConstBool(expr.value, tid or TYPE_BOOL, span)
        elif isinstance(expr, StringLiteral):
            return HirConstStr(expr.value, tid or TYPE_STR, span)
        elif isinstance(expr, NoneLiteral):
            return HirConstNone(tid or TYPE_NONE, span)
        elif isinstance(expr, IdentExpr):
            if hasattr(expr, "function_reference"):
                return HirFunctionRef(expr.function_reference, tid, span)
            if expr.name in ("None", "none") and not hasattr(expr, "resolved_symbol"):
                return HirConstNone(tid or TYPE_NONE, span)
            if hasattr(expr, "resolved_enum_name"):
                en_name = expr.resolved_enum_name
                return self._construct_enum(en_name, expr.name, 0, tid or self.interner.mk_enum(en_name), span)
            sym = self._resolve_symbol(expr.name)
            return HirLocalRef(expr.name, tid, span, symbol_id=sym)
        elif isinstance(expr, ThisExpr):
            return HirThisRef(tid, span)
        elif isinstance(expr, SuperExpr):
            return HirSuperRef(tid, span)
        elif isinstance(expr, UnaryExpr):
            operand_hir = self._lower_expr(expr.operand)
            return HirUnaryOp(getattr(expr, "op", getattr(expr, "operator", "-")), operand_hir, tid, span)
        elif isinstance(expr, BinaryExpr):
            left_hir = self._lower_expr(expr.left)
            right_hir = self._lower_expr(expr.right)
            return HirBinaryOp(getattr(expr, "op", getattr(expr, "operator", "+")), left_hir, right_hir, tid, span)
        elif isinstance(expr, AssignmentExpr):
            return self._lower_assignment_expression(expr)
        elif isinstance(expr, NewExpr):
            raw_args = getattr(expr, "arguments", getattr(expr, "args", []))
            args_hir = [self._lower_expr(a) for a in raw_args]
            return HirConstructObject(expr.class_name, args_hir, tid, span)
        elif isinstance(expr, StructInitExpr):
            s_name = getattr(expr, "struct_name", getattr(expr, "name", "Struct"))
            fields_hir = [(f.name, self._lower_expr(f.value)) for f in expr.fields]
            return HirConstructStruct(s_name, fields_hir, tid, span)
        elif isinstance(expr, ListExpr):
            elems_hir = [self._lower_expr(e) for e in expr.elements]
            entry = self.interner.get_entry(tid)
            elem_tid = entry.data if entry.tag == "list" else (elems_hir[0].type_id if elems_hir else TYPE_INT)
            return HirConstructList(elem_tid, elems_hir, tid or self.interner.mk_list(elem_tid), span)
        elif isinstance(expr, DictExpr):
            entries_hir = [
                (self._lower_expr(e.key), self._lower_expr(e.value)) if hasattr(e, "key")
                else (self._lower_expr(e[0]), self._lower_expr(e[1]))
                for e in expr.entries
            ]
            entry = self.interner.get_entry(tid)
            k_tid, v_tid = entry.data if entry.tag == "dict" else (TYPE_STR, TYPE_INT)
            return HirConstructDict(k_tid, v_tid, entries_hir, tid or self.interner.mk_dict(k_tid, v_tid), span)
        elif isinstance(expr, TupleExpr):
            elems_hir = [self._lower_expr(e) for e in expr.elements]
            elem_tids = [e.type_id for e in elems_hir]
            return HirConstructTuple(elem_tids, elems_hir, tid or self.interner.mk_tuple(tuple(elem_tids)), span)
        elif isinstance(expr, CallExpr):
            if getattr(expr, "indirect_call", False):
                return HirIndirectCall(self._lower_expr(expr.callee),
                                       [self._lower_expr(a) for a in expr.args], tid, span)
            raw_args = getattr(expr, "args", getattr(expr, "arguments", []))
            decl_id = getattr(expr.callee, "decl_id", None)
            # Check if this call is an unqualified Enum variant constructor
            if isinstance(expr.callee, IdentExpr):
                c_name = expr.callee.name
                if c_name == "Some" and len(raw_args) == 1:
                    arg_hir = self._lower_expr(raw_args[0])
                    return self._construct_enum("Option", "Some", 0, tid or self.interner.mk_enum("Option"), span, arg_hir)
                if c_name == "Ok" and len(raw_args) == 1:
                    arg_hir = self._lower_expr(raw_args[0])
                    return self._construct_enum("Result", "Ok", 0, tid or self.interner.mk_enum("Result"), span, arg_hir)
                if c_name == "Err" and len(raw_args) == 1:
                    arg_hir = self._lower_expr(raw_args[0])
                    return self._construct_enum("Result", "Err", 1, tid or self.interner.mk_enum("Result"), span, arg_hir)
                if c_name in self.enum_variants and not hasattr(expr.callee, "resolved_symbol"):
                    en_name = self.enum_variants[c_name]
                    args_hir = [self._lower_expr(a) for a in raw_args]
                    payload_hir = args_hir[0] if len(args_hir) == 1 else (HirConstructTuple([a.type_id for a in args_hir], args_hir, self.interner.mk_tuple(tuple(a.type_id for a in args_hir)), span) if len(args_hir) > 1 else None)
                    return self._construct_enum(en_name, c_name, 0, tid or self.interner.mk_enum(en_name), span, payload_hir)

            # Check if this call is a qualified Enum variant constructor: Enum.Variant(...)
            if isinstance(expr.callee, MemberExpr) and isinstance(expr.callee.object, IdentExpr) and expr.callee.object.name in self.enums and not hasattr(expr.callee.object, 'resolved_symbol'):
                en_name = expr.callee.object.name
                v_name = expr.callee.property
                args_hir = [self._lower_expr(a) for a in raw_args]
                payload_hir = args_hir[0] if len(args_hir) == 1 else (HirConstructTuple([a.type_id for a in args_hir], args_hir, self.interner.mk_tuple(tuple(a.type_id for a in args_hir)), span) if len(args_hir) > 1 else None)
                return self._construct_enum(en_name, v_name, 0, tid or self.interner.mk_enum(en_name), span, payload_hir)

            # Check if this call is an object construction: ClassName(...)
            if isinstance(expr.callee, IdentExpr) and expr.callee.name in self.classes:
                args_hir = [self._lower_expr(a) for a in raw_args]
                return HirConstructObject(expr.callee.name, args_hir, tid or self.interner.mk_class(expr.callee.name), span)
            # Check if this call is a struct construction: StructName(...)
            elif isinstance(expr.callee, IdentExpr) and expr.callee.name in self.structs:
                args_hir = [self._lower_expr(a) for a in raw_args]
                sdef = self.interner.struct_defs.get(expr.callee.name)
                field_names = list(sdef.fields.keys()) if sdef else []
                fields = [(field_names[i] if i < len(field_names) else f"f{i}", args_hir[i]) for i in range(len(args_hir))]
                return HirConstructStruct(expr.callee.name, fields, tid or self.interner.mk_struct(expr.callee.name), span)
            # Check if builtin call
            elif isinstance(expr.callee, IdentExpr) and expr.callee.name in BUILTIN_NAMES and getattr(getattr(expr.callee, 'resolved_symbol', None), 'kind', None) == 'builtin':
                args_hir = [self._lower_expr(a) for a in raw_args]
                target = BuiltinCallTarget(expr.callee.name)
                return HirBuiltinCall(expr.callee.name, args_hir, tid, span, target=target)
            # Check if method call or module function call
            elif isinstance(expr.callee, MemberExpr):
                if isinstance(expr.callee.object, IdentExpr) and expr.callee.object.name in self.modules:
                    args_hir = [self._lower_expr(a) for a in raw_args]
                    callee_name = f"{expr.callee.object.name}__{expr.callee.property}"
                    target = DirectCallTarget(callee_name, decl_id)
                    return HirDirectCall(callee_name, args_hir, tid, span, decl_id=decl_id, target=target)
                rec_hir = self._lower_expr(expr.callee.object)
                args_hir = [self._lower_expr(a) for a in raw_args]
                slot = None
                target_cls = None
                rec_entry = self.interner.get_entry(rec_hir.type_id)
                if rec_entry and rec_entry.tag in ("class", "interface"):
                    if not isinstance(rec_hir, HirSuperRef):
                        slot = self.virtual_slots.index((expr.callee.property, len(args_hir)))
                    if rec_entry.tag == "class":
                        target_cls = rec_entry.data
                if rec_entry.tag in ("list", "dict"):
                    target = CollectionCallTarget(rec_entry.tag, expr.callee.property)
                else:
                    target = VirtualCallTarget(expr.callee.property, slot=slot, target_class=target_cls)
                return HirMethodCall(rec_hir, expr.callee.property, args_hir, tid, span, slot=slot, target=target)
            # Direct top-level function call
            elif isinstance(expr.callee, IdentExpr):
                args_hir = [self._lower_expr(a) for a in raw_args]
                target = DirectCallTarget(expr.callee.name, decl_id)
                return HirDirectCall(expr.callee.name, args_hir, tid, span, decl_id=decl_id, target=target)
            else:
                args_hir = [self._lower_expr(a) for a in raw_args]
                callee_name = getattr(expr.callee, "name", "anon_fn")
                target = DirectCallTarget(callee_name, decl_id)
                return HirDirectCall(callee_name, args_hir, tid, span, decl_id=decl_id, target=target)
        elif isinstance(expr, MemberExpr):
            # Check if this is an Enum variant: EnumName.Variant
            if isinstance(expr.object, IdentExpr) and expr.object.name in self.enums and not hasattr(expr.object, 'resolved_symbol'):
                enum_name = expr.object.name
                variant_name = expr.property
                if enum_name == "Option" and variant_name in ("None", "none"):
                    return HirConstNone(tid or TYPE_NONE, span)
                edef = self.interner.enum_defs.get(enum_name)
                v_idx = edef.variants.index(variant_name) if edef and variant_name in edef.variants else 0
                return self._construct_enum(enum_name, variant_name, v_idx, tid or self.interner.mk_enum(enum_name), span)
            obj_hir = self._lower_expr(expr.object)
            field_idx = -1
            entry = self.interner.get_entry(obj_hir.type_id)
            if entry and entry.tag == "struct":
                sdef = self.interner.struct_defs.get(entry.data)
                if sdef and expr.property in sdef.fields:
                    field_idx = list(sdef.fields.keys()).index(expr.property)
            elif entry and entry.tag == "class":
                cdef = self.interner.class_defs.get(entry.data)
                if cdef and expr.property in cdef.fields:
                    field_idx = list(cdef.fields.keys()).index(expr.property)
            field_id = FieldId(field_idx, expr.property) if field_idx >= 0 else None
            return HirFieldRead(obj_hir, expr.property, tid, span, field_idx=field_idx, field_id=field_id)
        elif isinstance(expr, IndexExpr):
            obj_hir = self._lower_expr(expr.object)
            idx_hir = self._lower_expr(expr.index)
            return HirIndexRead(obj_hir, idx_hir, tid, span)
        elif isinstance(expr, PostfixTryExpr):
            op_hir = self._lower_expr(expr.operand)
            return HirTryPropagate(op_hir, expr.propagation_kind, tid, span)
        elif isinstance(expr, CoalesceExpr):
            opt_hir = self._lower_expr(expr.left)
            fb_hir = self._lower_expr(expr.right)
            return HirCoalesce(opt_hir, fb_hir, tid, span)
        elif isinstance(expr, ElseErrExpr):
            op_hir = self._lower_expr(expr.operand)
            h_block = getattr(expr, "handler", getattr(expr, "body", None))
            err_var = getattr(expr, "err_name", getattr(expr, "err_variable", None))
            self._push_scope()
            symbol = self._declare_symbol(err_var) if err_var else None
            handler_hir = self._lower_block(h_block)
            self._pop_scope()
            result = HirElseHandle(op_hir, err_var, handler_hir, tid, span)
            result.symbol_id = symbol
            return result
        elif isinstance(expr, IfExpr):
            cond_hir = self._lower_expr(expr.condition)
            then_hir = self._lower_block(expr.then_branch)
            elifs_hir = [(self._lower_expr(b.condition), self._lower_block(b.body)) for b in expr.elif_branches]
            else_hir = self._lower_block(expr.else_branch) if expr.else_branch else None
            return HirIf(cond_hir, then_hir, elifs_hir, else_hir, tid, span)
        elif isinstance(expr, MatchExpr):
            sub_hir = self._lower_expr(expr.subject)
            arms_hir = []
            for arm in expr.arms:
                self._push_scope()
                pat_hir = self._lower_pattern(arm.pattern, sub_hir.type_id)
                guard_hir = self._lower_expr(arm.guard) if arm.guard is not None else None
                body_hir = self._lower_block(arm.body)
                self._pop_scope()
                arms_hir.append(HirMatchArm(pat_hir, body_hir, self._span(arm), guard=guard_hir))
            return HirMatch(sub_hir, arms_hir, tid, span)
        elif isinstance(expr, RangeExpr):
            s_hir = self._lower_expr(expr.start)
            e_hir = self._lower_expr(expr.end)
            return HirRange(s_hir, e_hir, expr.inclusive, tid, span)

        raise WysnaError(f"Unsupported AST expression: {type(expr).__name__}",
                     line=span.line, col=span.col, file=self.file_path,
                     code="W9999", stage="hir")

    # -------------------------------------------------------------
    # Type Resolution Helpers
    # -------------------------------------------------------------

    def _get_expr_type_id(self, expr: Expr) -> int:
        if getattr(expr, "session_arena_id", None) != self.interner.arena_id:
            self._fail(expr, "Expression type belongs to a different or missing type arena")
        type_id = getattr(expr, "inferred_type_id", None)
        self._require_type_id(type_id, expr, "expression type")
        return type_id

    def _get_type_id_from_node(self, node: Optional[TypeNode]) -> int:
        if node is None:
            return TYPE_UNIT
        if getattr(node, "session_arena_id", None) != self.interner.arena_id:
            self._fail(node, "Type annotation belongs to a different or missing type arena")
        type_id = getattr(node, "resolved_type_id", None)
        self._require_type_id(type_id, node, "resolved type annotation")
        return type_id

    def _get_type_name_from_node(self, node: TypeNode) -> str:
        self._get_type_id_from_node(node)
        if isinstance(node, AstErrorUnionType):
            return " | ".join(self._get_type_name_from_node(member) for member in node.error_types)
        if isinstance(node, NamedType):
            return node.name
        return self.interner.to_string(self._get_type_id_from_node(node))

    def _require_type_id(self, type_id, owner, description):
        if type(type_id) is not int or self.interner.get_entry(type_id).tag == "error":
            self._fail(owner, f"Missing or invalid {description}: {type_id!r}")

    def _fail(self, owner, message):
        span = self._span(owner)
        raise WysnaError(message, line=span.line, col=span.col,
                         file=span.file_path or self.file_path,
                         code="W9999", stage="hir")
