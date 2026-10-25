from common.ExpressionDispatch import reject_unknown_node
from checker.DeclarationPass import collect_declarations
from checker.ExpressionRules import EXPRESSION_RULES
from checker.OopGenerics import GenericOopTemplate, specialize as specialize_oop
import copy
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple, NamedTuple, Sequence
from node.NodeType import (
    clone_ast,
    ExternFnDecl, LambdaExpr, FunctionType as AstFunctionType, Node, Program, Stmt, Expr, TypeNode, Pattern, Block,
    NamedType, OptionalType as AstOptionalType, ListType as AstListType,
    DictType as AstDictType, TupleType as AstTupleType, ResultType as AstResultType,
    ErrorUnionType as AstErrorUnionType,
    GenericType, TypeParam,
    IdentifierPattern, TuplePattern, EnumPattern, ErrorTypePattern, LiteralPattern, WildcardPattern,
    FnDecl, Param, StructDecl, StructField, EnumDecl,
    InterfaceDecl, InterfaceMethod, ClassDecl, ClassField, MethodDecl,
    ImportDecl, LetDecl,
    WhileStmt, ForStmt, ReturnStmt, BreakStmt, ContinueStmt, ExprStmt,
    IntLiteral, FloatLiteral, StringLiteral, BoolLiteral, NoneLiteral,
    IdentExpr, ThisExpr, SuperExpr, NewExpr,
    UnaryExpr, BinaryExpr, CoalesceExpr, RangeExpr, PostfixTryExpr, ElseErrExpr,
    CallExpr, MemberExpr, IndexExpr, AssignmentExpr,
    StructInitExpr, FieldInit, ListExpr, DictExpr, TupleExpr,
    IfExpr, ElifBranch, MatchExpr, MatchArm, VariantDecl
)
from checker.WysnaType import (
    WysnaType, IntType, FloatType, BoolType, StrType, UnitType, NoneType,
    OptionalType, WeakType, ListType, DictType, TupleType, StructType, EnumType,
    InterfaceType, ClassFieldInfo, ClassMethodInfo, ClassType,
    ResultType, ErrorUnionType, FunctionType, ModuleType, AnyType,
    TypeParamType, GenericStructTemplate, GenericFnTemplate, GenericEnumTemplate,
)
from checker.SymbolTable import Scope, Symbol, create_global_scope
from checker.TypeInterner import TypeInterner
from checker.TypeRelations import TypeRelations
from checker.Generics import GenericDefinitionChecker
from checker.Specialization import Instantiator
from checker.GenericState import (
    GenericInstanceKey, InstantiationFrame, SpecializationEntry, SpecializationState, MonomorphizationTelemetry,
    MAX_SPECIALIZATION_COMPLEXITY, _mangle_type_static, mangle_generic_name,
    type_strictly_contains, _type_contains,
)
from error.Error import TypeError
from checker.LayoutGraph import LayoutGraph, build_layout_graph_from_declarations


class TypeChecker:
    """
    Two-pass Semantic Analyzer and Type Checker for Wysna v0.2.
    Pass 1: Collect & register all top-level declarations (Enums, Structs, Interfaces, Classes, Functions, Imports).
    Pass 2: Check function/method bodies, local bindings, assignments, and expressions.
    """
    def __init__(
        self,
        file: str = "<stdin>",
        session=None
    ) -> None:
        self.file = file
        self.global_scope = create_global_scope()
        self.current_scope = self.global_scope
        self.current_fn_ret_type: Optional[WysnaType] = None
        self.expected_type: Optional[WysnaType] = None
        self.current_class: Optional[ClassType] = None
        self.scope_spans = []
        self.closure_counter = 0
        self.loop_depth: int = 0
        from compiler.CompilationSession import CompilationSession
        self.session = session or CompilationSession()
        self.interner: TypeInterner = self.session.interner
        self._type_relations = TypeRelations(self.interner)
        self.monomorphized_oop_decls = []
        self.generic_struct_templates: Dict[str, GenericStructTemplate] = {}
        self.generic_fn_templates: Dict[str, GenericFnTemplate] = {}
        self.generic_enum_templates: Dict[str, GenericEnumTemplate] = {}
        self.specializations: Dict[GenericInstanceKey, SpecializationEntry] = {}
        self.monomorphized_structs: Dict[GenericInstanceKey, StructType] = {}
        self.monomorphized_fns: Dict[GenericInstanceKey, Symbol] = {}
        self.monomorphized_enums: Dict[GenericInstanceKey, EnumType] = {}
        self.monomorphized_struct_decls: List[StructDecl] = []
        self.monomorphized_fn_decls: List[FnDecl] = []
        self.monomorphized_enum_decls: List[EnumDecl] = []
        self._monomorphize_depth: int = 0
        self._instantiation_stack: List[InstantiationFrame] = []
        # Type-parameter substitution in effect while a specialization is being
        # resolved. Unbound parameters keep their own name, so the resolver has to
        # consult this binding before treating a name as a declared type.
        self._active_type_subst: Optional[Dict[str, WysnaType]] = None
        self.telemetry: MonomorphizationTelemetry = MonomorphizationTelemetry()
        self._checking_generic_definition = None

        # Canonical built-in Option[T] template
        opt_decl = EnumDecl(
            name="Option",
            variants=[
                VariantDecl(name="Some", payload_types=[NamedType(name="T")]),
                VariantDecl(name="None", payload_types=[])
            ],
            type_params=[TypeParam(name="T")]
        )
        self.generic_enum_templates["Option"] = GenericEnumTemplate("Option", [TypeParam(name="T")], opt_decl)

        # Canonical built-in Result[T, E] template
        res_decl = EnumDecl(
            name="Result",
            variants=[
                VariantDecl(name="Ok", payload_types=[NamedType(name="T")]),
                VariantDecl(name="Err", payload_types=[NamedType(name="E")])
            ],
            type_params=[TypeParam(name="T"), TypeParam(name="E")]
        )
        self.generic_enum_templates["Result"] = GenericEnumTemplate("Result", [TypeParam(name="T"), TypeParam(name="E")], res_decl)
        self.instantiator = Instantiator(self)

    @property
    def type_relations(self) -> TypeRelations:
        if self._type_relations.interner is not self.interner:
            self._type_relations = TypeRelations(self.interner)
        return self._type_relations

    def check(self, program: Program) -> None:
        self.session.check_program(program, self)

    def _check_program(self, program: Program) -> None:
        # Reserve builtin identities and their specialization namespaces.
        for declaration in program.body:
            if isinstance(declaration, (StructDecl, EnumDecl, ClassDecl, InterfaceDecl)):
                if declaration.name in ("Option", "Result", "Weak") or declaration.name.startswith(("Option__", "Result__")):
                    raise TypeError("Builtin Option/Result/Weak identities are reserved", line=declaration.line,
                                    col=declaration.col, file=self.file, code="W1002")

        # Validate finite layouts via formal LayoutGraph (SCC on inline subgraph)
        pre_graph = build_layout_graph_from_declarations(
            struct_decls=[s for s in program.body if isinstance(s, StructDecl)],
            enum_decls=[s for s in program.body if isinstance(s, EnumDecl)],
            class_decls=[s for s in program.body if isinstance(s, ClassDecl)],
        )
        pre_graph.validate_finite_layouts(file_path=self.file)

        # Pass 1: Collect declarations
        self._pass1_collect_declarations(program)

        # Validate constraints even when the template is never instantiated.
        templates = list(self.generic_struct_templates.values())
        templates.extend(self.generic_fn_templates.values())
        templates.extend(self.generic_enum_templates.values())
        templates.extend(
            template for template in self.global_scope.types.values()
            if isinstance(template, GenericOopTemplate)
        )
        for template in templates:
            for parameter in template.type_params:
                constraint = getattr(parameter, "constraint", None)
                if constraint is not None:
                    self._resolve_constraint_interface(constraint, parameter.line, parameter.col)

        # Pass 2: Typecheck bodies & statements
        GenericDefinitionChecker(self).check_all()
        self._pass2_check_program(program)

        # Filter out uninstantiated generic templates and insert monomorphized declarations
        filtered_body: List[Stmt] = []
        for stmt in program.body:
            if isinstance(stmt, (StructDecl, FnDecl, EnumDecl, ClassDecl, InterfaceDecl)) and stmt.type_params:
                continue
            filtered_body.append(stmt)

        sorted_structs, sorted_enums, sorted_fns = self._sort_monomorphized_decls()
        final_body: List[Stmt] = []
        final_body.extend(self.monomorphized_oop_decls)
        final_body.extend(sorted_structs)
        final_body.extend(sorted_enums)
        final_body.extend(sorted_fns)
        final_body.extend(filtered_body)
        program.body = final_body

        # Post-monomorphization layout verification
        post_graph = build_layout_graph_from_declarations(
            struct_decls=[s for s in program.body if isinstance(s, StructDecl)],
            enum_decls=[s for s in program.body if isinstance(s, EnumDecl)],
            class_decls=[s for s in program.body if isinstance(s, ClassDecl)],
        )
        post_graph.validate_finite_layouts(file_path=self.file)

        program.interner = self.interner

    def _sort_monomorphized_decls(self) -> Tuple[List[StructDecl], List[EnumDecl], List[FnDecl]]:
        struct_map = {s.name: s for s in self.monomorphized_struct_decls}
        struct_deps: Dict[str, Set[str]] = {}
        for s in self.monomorphized_struct_decls:
            deps = set()
            for f in s.fields:
                f_type_name = getattr(f.type_ann, "name", None)
                if f_type_name and f_type_name in struct_map and f_type_name != s.name:
                    deps.add(f_type_name)
            struct_deps[s.name] = deps

        sorted_struct_names: List[str] = []
        visited_structs: Set[str] = set()
        temp_structs: Set[str] = set()

        def visit_struct(name: str):
            if name in temp_structs:
                return
            if name not in visited_structs:
                temp_structs.add(name)
                for dep in sorted(struct_deps.get(name, set())):
                    visit_struct(dep)
                temp_structs.remove(name)
                visited_structs.add(name)
                sorted_struct_names.append(name)

        for name in sorted(struct_map.keys()):
            if name not in visited_structs:
                visit_struct(name)

        sorted_structs = [struct_map[n] for n in sorted_struct_names]
        sorted_enums = sorted(self.monomorphized_enum_decls, key=lambda e: e.name)
        sorted_fns = sorted(self.monomorphized_fn_decls, key=lambda f: f.name)
        return sorted_structs, sorted_enums, sorted_fns

    # ==========================================
    # Pass 1: Collect Top-Level Declarations
    # ==========================================

    def _resolve_import(self, stmt: ImportDecl) -> WysnaType:
        return self.session.import_module(stmt, self.file)

    def _pass1_collect_declarations(self, program: Program) -> None:
        collect_declarations(self, program)


    # ==========================================
    # Pass 2: Typecheck Program Bodies
    # ==========================================

    def _pass2_check_program(self, program: Program) -> None:
        for stmt in program.body:
            self._check_stmt(stmt)

    def _clone_generic_definition(self, template, substitutions):
        declaration = clone_ast(template.ast_decl)
        declaration.type_params = []
        declaration.return_type = self._substitute_type_node(declaration.return_type, substitutions)
        declaration.params = [
            Param(parameter.name, self._substitute_type_node(parameter.type_ann, substitutions),
                  line=parameter.line, col=parameter.col)
            for parameter in declaration.params
        ]
        self._substitute_ast_types(declaration.body, substitutions)
        return declaration

    def _check_stmt(self, stmt: Stmt) -> None:
        if isinstance(stmt, (EnumDecl, StructDecl, InterfaceDecl, ImportDecl)):
            return  # Handled in Pass 1

        elif isinstance(stmt, ClassDecl):
            if stmt.type_params:
                return
            self._check_class_decl(stmt)

        elif isinstance(stmt, FnDecl):
            if stmt.type_params:
                return  # Generic templates are typechecked when monomorphized
            self._check_fn_decl(stmt)

        elif isinstance(stmt, LetDecl):
            self._check_let_decl(stmt)

        elif isinstance(stmt, WhileStmt):
            cond_t = self._check_expr(stmt.condition)
            if not self._require_boolean(cond_t, stmt.condition, "While loop condition"):
                raise TypeError(
                    message=f"While loop condition must be bool, found '{cond_t}'",
                    line=stmt.condition.line,
                    col=stmt.condition.col,
                    file=self.file,
                    code="W2001"
                )
            self.loop_depth += 1
            try:
                self._check_block(stmt.body, is_statement_context=True)
            finally:
                self.loop_depth -= 1

        elif isinstance(stmt, ForStmt):
            self._check_for_stmt(stmt)

        elif isinstance(stmt, ReturnStmt):
            val_t = self._check_payload(stmt.value, self.current_fn_ret_type) if stmt.value else UnitType
            if self.current_fn_ret_type is None:
                raise TypeError(
                    message="Return statement outside of function",
                    line=stmt.line,
                    col=stmt.col,
                    file=self.file,
                    code="W1004"
                )
            if not self.type_relations.is_assignable(val_t, self.current_fn_ret_type):
                raise TypeError(
                    message=f"Return type mismatch: expected '{self.current_fn_ret_type}', found '{val_t}'",
                    line=stmt.line,
                    col=stmt.col,
                    file=self.file,
                    code="W2004"
                )

            self._mark_result_lift(stmt.value, self.current_fn_ret_type)

        elif isinstance(stmt, BreakStmt):
            if self.loop_depth == 0:
                raise TypeError(
                    message="'break' statement outside of loop",
                    line=stmt.line,
                    col=stmt.col,
                    file=self.file,
                    code="W1004"
                )

        elif isinstance(stmt, ContinueStmt):
            if self.loop_depth == 0:
                raise TypeError(
                    message="'continue' statement outside of loop",
                    line=stmt.line,
                    col=stmt.col,
                    file=self.file,
                    code="W1004"
                )

        elif isinstance(stmt, ExprStmt):
            self._check_expr(stmt.expr, is_statement_context=True)
        else:
            reject_unknown_node(stmt, self.file, "type", "statement")

    def _require_boolean(self, type_, node, description: str) -> bool:
        if type_ == BoolType:
            return True
        if isinstance(type_, TypeParamType) and self._checking_generic_definition:
            self.type_relations.record_requirement(
                "boolean", type_, description, file=self.file,
                line=getattr(node, "line", 1), col=getattr(node, "col", 1)
            )
            return True
        return False

    def _check_constructor_rules(self, stmt: ClassDecl, cls_type: ClassType, m: MethodDecl) -> None:
        # 1. Constructor return type check: init cannot return non-unit
        if cls_type.init_type is not None and cls_type.init_type.ret != UnitType:
            raise TypeError(
                message=f"Constructor 'init' of class '{stmt.name}' cannot return a value of type '{cls_type.init_type.ret}', expected 'unit'",
                line=m.line,
                col=m.col,
                file=self.file,
                code="W3004"
            )

        from checker.ConstructorFlow import check_constructor
        check_constructor(stmt, cls_type, m, self.file, self._match_is_exhaustive)

    def _check_class_decl(self, stmt: ClassDecl) -> None:
        cls_type = self.global_scope.lookup_type(stmt.name)
        assert isinstance(cls_type, ClassType)
        old_class = self.current_class
        self.current_class = cls_type
        try:
            for m in stmt.methods:
                fn_type = cls_type.init_type if m.name == "init" else cls_type.methods[m.name].fn_type
                assert fn_type is not None
                fn_scope = Scope(parent=self.current_scope, is_function_scope=True)
                old_scope = self.current_scope
                old_ret_type = self.current_fn_ret_type
                old_loop_depth = self.loop_depth
                old_expected = self.expected_type
                self.current_scope = fn_scope
                self.current_fn_ret_type = fn_type.ret
                self.expected_type = fn_type.ret
                self.loop_depth = 0
                try:
                    # Also bind 'self' as an alias to 'this' inside methods
                    fn_scope.define_symbol(
                        Symbol("self", cls_type, is_mut=False, kind="param", line=m.line, col=m.col),
                        file=self.file
                    )
                    for p, p_type in zip(m.params, fn_type.params):
                        fn_scope.define_symbol(
                            Symbol(p.name, p_type, is_mut=False, kind="param", line=p.line, col=p.col),
                            file=self.file
                        )
                    is_unit_ret = (fn_type.ret == UnitType) or (isinstance(fn_type.ret, ResultType) and fn_type.ret.ok == UnitType)
                    body_ret = self._check_block(m.body, is_statement_context=is_unit_ret)
                    if m.name == "init":
                        self._check_constructor_rules(stmt, cls_type, m)
                    self._mark_tail_result_lift(m.body.tail_expr, fn_type.ret)
                    if not is_unit_ret and not self._block_has_return(m.body):
                        if body_ret is not None and self.type_relations.is_assignable(body_ret, fn_type.ret):
                            pass
                        else:
                            raise TypeError(
                                message=f"Method '{stmt.name}.{m.name}' missing return statement for type '{fn_type.ret}'",
                                line=m.line,
                                col=m.col,
                                file=self.file,
                                code="W2004"
                            )
                finally:
                    self.current_scope = old_scope
                    self.current_fn_ret_type = old_ret_type
                    self.loop_depth = old_loop_depth
                    self.expected_type = old_expected
        finally:
            self.current_class = old_class

    def _check_fn_decl(self, stmt: FnDecl) -> None:
        if isinstance(stmt, ExternFnDecl):
            allowed = {"int", "int32", "int64", "float", "double", "bool", "opaque"}
            if (not all(isinstance(p.type_ann, NamedType) and p.type_ann.name in allowed for p in stmt.params)
                    or not isinstance(stmt.return_type, NamedType) or stmt.return_type.name not in allowed | {"unit"}):
                raise TypeError("C FFI accepts only scalar and opaque ABI types", file=self.file,
                                line=stmt.line, col=stmt.col, code="W2001")
            for p in stmt.params:
                if getattr(p, "type_ann", None) is not None:
                    self._resolve_type_node(p.type_ann)
            if getattr(stmt, "return_type", None) is not None:
                self._resolve_type_node(stmt.return_type)
            return

        for p in stmt.params:
            if getattr(p, "type_ann", None) is not None:
                self._resolve_type_node(p.type_ann)
        if getattr(stmt, "return_type", None) is not None:
            self._resolve_type_node(stmt.return_type)

        sym = self.global_scope.lookup_symbol(stmt.name)
        assert sym is not None and isinstance(sym.type, FunctionType)
        fn_type: FunctionType = sym.type

        fn_scope = Scope(parent=self.current_scope, is_function_scope=True)
        old_scope = self.current_scope
        old_ret_type = self.current_fn_ret_type
        old_loop_depth = self.loop_depth
        old_expected = self.expected_type

        self.current_scope = fn_scope
        self.current_fn_ret_type = fn_type.ret
        self.expected_type = fn_type.ret
        self.loop_depth = 0

        try:
            # Bind parameters in function scope as immutable
            for p, p_type in zip(stmt.params, fn_type.params):
                fn_scope.define_symbol(
                    Symbol(p.name, p_type, is_mut=False, kind="param", line=p.line, col=p.col),
                    file=self.file
                )

            is_unit_ret = (fn_type.ret == UnitType) or (isinstance(fn_type.ret, ResultType) and fn_type.ret.ok == UnitType)
            body_ret = self._check_block(stmt.body, is_statement_context=is_unit_ret)
            self._mark_tail_result_lift(stmt.body.tail_expr, fn_type.ret)

            # If function expects a return value other than unit, verify return presence
            if not is_unit_ret and not self._block_has_return(stmt.body):
                if body_ret is not None and self.type_relations.is_assignable(body_ret, fn_type.ret):
                    pass  # Tail expression satisfies return
                else:
                    raise TypeError(
                        message=f"Function '{stmt.name}' missing return statement for type '{fn_type.ret}'",
                        line=stmt.line,
                        col=stmt.col,
                        file=self.file,
                        code="W2004"
                    )
        finally:
            self.current_scope = old_scope
            self.current_fn_ret_type = old_ret_type
            self.loop_depth = old_loop_depth
            self.expected_type = old_expected

    def _expr_has_return(self, expr: Expr) -> bool:
        from common.ControlFlow import exits, NORMAL, RETURN
        outcomes = exits(expr, self._match_is_exhaustive)
        return NORMAL not in outcomes and outcomes <= {RETURN}

    def _block_has_return(self, block: Block) -> bool:
        return self._expr_has_return(block)

    def _block_diverges(self, block: Block) -> bool:
        from common.ControlFlow import exits, NORMAL
        return NORMAL not in exits(block, self._match_is_exhaustive)

    # The synthetic parameter name introduced by closure conversion. Captured
    # variables are immutable snapshots today, so a mutation attempt is rejected -- but
    # the diagnostic has to name the captured variable, not a compiler internal.
    CLOSURE_ENVIRONMENT_PARAMETER = '__wysna_internal_environment'

    def _binding_error_name(self, symbol) -> str:
        if symbol is not None and getattr(symbol, 'name', None) == self.CLOSURE_ENVIRONMENT_PARAMETER:
            return "a captured variable"
        return symbol.name if symbol is not None else "a captured variable"

    def _immutable_binding_message(self, symbol, action: str) -> str:
        """Explains why a mutation target is not writable.

        A captured variable is a snapshot in the closure environment, so the reason
        differs from a plain `let` binding and the message must say which one applies.
        """
        if symbol is not None and getattr(symbol, 'name', None) == self.CLOSURE_ENVIRONMENT_PARAMETER:
            return (f"Cannot {action} on a captured variable: closures capture values "
                    f"as immutable snapshots")
        return f"Cannot {action} on immutable binding '{self._binding_error_name(symbol)}'"

    def _find_value_root_symbol(self, expr: Expr) -> Optional[Symbol]:
        if isinstance(expr, IdentExpr):
            return self.current_scope.lookup_symbol(expr.name)
        if isinstance(expr, IndexExpr):
            return self._find_value_root_symbol(expr.object)
        if isinstance(expr, MemberExpr):
            obj_t = self._check_expr(expr.object)
            if isinstance(obj_t, ClassType):
                return None
            return self._find_value_root_symbol(expr.object)
        return None

    def _check_class_instantiation(self, class_t: ClassType, args: List[Expr], line: int, col: int) -> ClassType:
        init_fn: Optional[FunctionType] = None
        curr_c: Optional[ClassType] = class_t
        while curr_c is not None:
            if curr_c.init_type is not None:
                init_fn = curr_c.init_type
                break
            curr_c = curr_c.superclass
        if init_fn is not None:
            expected_params = init_fn.params
            if len(args) != len(expected_params):
                raise TypeError(
                    message=f"Constructor of '{class_t.name}' expects {len(expected_params)} arguments, found {len(args)}",
                    line=line,
                    col=col,
                    file=self.file,
                    code="W2005"
                )
            for i, (a_expr, p_type) in enumerate(zip(args, expected_params)):
                a_type = self._check_expr(a_expr)
                if not self.type_relations.is_assignable(a_type, p_type):
                    raise TypeError(
                        message=f"Constructor argument {i + 1} type mismatch: expected '{p_type}', found '{a_type}'",
                        line=a_expr.line,
                        col=a_expr.col,
                        file=self.file,
                        code="W2001"
                    )
        else:
            if len(args) != 0:
                raise TypeError(
                    message=f"Default constructor of '{class_t.name}' takes 0 arguments, found {len(args)}",
                    line=line,
                    col=col,
                    file=self.file,
                    code="W2005"
                )
        return class_t


    def _check_block(self, block: Block, is_statement_context: bool = False, initial_bindings: Optional[Dict[str, WysnaType]] = None) -> Optional[WysnaType]:
        block_scope = Scope(parent=self.current_scope)
        if initial_bindings:
            for b_name, b_type in initial_bindings.items():
                block_scope.define_symbol(
                    Symbol(b_name, b_type, is_mut=False, kind="var", line=block.line, col=block.col),
                    file=self.file
                )
        old_scope = self.current_scope
        self.current_scope = block_scope
        self.scope_spans.append(((block.line, block.col),
                                 (getattr(block, 'end_line', block.line), getattr(block, 'end_col', block.col)), block_scope))

        try:
            for s in block.statements:
                self._check_stmt(s)
            if block.tail_expr:
                return self._check_expr(block.tail_expr, is_statement_context=is_statement_context)
            return None
        finally:
            self.current_scope = old_scope

    def _check_let_decl(self, stmt: LetDecl) -> None:
        if stmt.name.startswith("_WYSNA_") or stmt.name.startswith("__wysna_internal_"):
            raise TypeError(
                message=f"Identifier '{stmt.name}' is reserved for internal compiler use",
                line=stmt.line,
                col=stmt.col,
                file=self.file,
                code="W1002"
            )
        # Check for unannotated empty list or none
        if isinstance(stmt.initializer, ListExpr) and len(stmt.initializer.elements) == 0 and stmt.type_ann is None:
            raise TypeError(
                message="Cannot infer type of empty list literal '[]'; explicit type annotation required",
                line=stmt.line,
                col=stmt.col,
                file=self.file,
                code="W2001"
            )

        if isinstance(stmt.initializer, NoneLiteral) and stmt.type_ann is None:
            raise TypeError(
                message="Cannot infer type of 'none'; explicit optional type annotation required",
                line=stmt.line,
                col=stmt.col,
                file=self.file,
                code="W2001"
            )

        if isinstance(stmt.initializer, DictExpr) and len(stmt.initializer.entries) == 0 and stmt.type_ann is None:
            raise TypeError(
                message="Cannot infer type of empty dictionary literal '{}'; explicit type annotation required",
                line=stmt.line,
                col=stmt.col,
                file=self.file,
                code="W2001"
            )


        expected_type: Optional[WysnaType] = None
        if stmt.type_ann is not None:
            expected_type = self._resolve_type_node(stmt.type_ann)

        old_expected = self.expected_type
        self.expected_type = expected_type
        try:
            init_type = self._check_expr(stmt.initializer)
        finally:
            self.expected_type = old_expected

        if expected_type is not None:
            if not self.type_relations.is_assignable(init_type, expected_type):
                raise TypeError(
                    message=f"Type mismatch: cannot assign value of type '{init_type}' to variable '{stmt.name}' of type '{expected_type}'",
                    line=stmt.line,
                    col=stmt.col,
                    file=self.file,
                    code="W2001"
                )
            var_type = expected_type
        else:
            var_type = init_type

        self._mark_result_lift(stmt.initializer, var_type, force=True)
        self.current_scope.define_symbol(
            Symbol(stmt.name, var_type, is_mut=stmt.is_mut, kind="var", line=stmt.line, col=stmt.col),
            file=self.file
        )

    def _check_for_stmt(self, stmt: ForStmt) -> None:
        iter_t = self._check_expr(stmt.iterable)

        # Iterable must be a list
        elem_t: WysnaType
        if isinstance(iter_t, ListType):
            elem_t = iter_t.element
        else:
            raise TypeError(
                message=f"Cannot iterate over non-list value of type '{iter_t}'",
                line=stmt.iterable.line,
                col=stmt.iterable.col,
                file=self.file,
                code="W2001"
            )

        self._bind_pattern_type(stmt.pattern, elem_t, {}, stmt.line, stmt.col)
        loop_scope = Scope(parent=self.current_scope)
        old_scope = self.current_scope
        self.current_scope = loop_scope
        self.loop_depth += 1

        try:
            if isinstance(stmt.pattern, IdentifierPattern):
                loop_scope.define_symbol(
                    Symbol(stmt.pattern.name, elem_t, is_mut=False, kind="var", line=stmt.pattern.line, col=stmt.pattern.col),
                    file=self.file
                )
            elif isinstance(stmt.pattern, TuplePattern):
                if not isinstance(elem_t, TupleType) or len(elem_t.elements) != len(stmt.pattern.elements):
                    raise TypeError(
                        message=f"Cannot destructure iterable element of type '{elem_t}' into {len(stmt.pattern.elements)} variables",
                        line=stmt.pattern.line,
                        col=stmt.pattern.col,
                        file=self.file,
                        code="W2001"
                    )
                for p_var, p_type in zip(stmt.pattern.elements, elem_t.elements):
                    assert isinstance(p_var, IdentifierPattern)
                    loop_scope.define_symbol(
                        Symbol(p_var.name, p_type, is_mut=False, kind="var", line=p_var.line, col=p_var.col),
                        file=self.file
                    )

            for s in stmt.body.statements:
                self._check_stmt(s)
            if stmt.body.tail_expr is not None:
                self._check_expr(stmt.body.tail_expr, is_statement_context=True)
        finally:
            self.loop_depth -= 1
            self.current_scope = old_scope

    # ==========================================
    # Expression Type Checking
    # ==========================================

    def _mark_tail_result_lift(self, expr: Optional[Expr], target: WysnaType) -> None:
        if expr is None:
            return
        if isinstance(target, ResultType) and target.ok == UnitType:
            if isinstance(expr, IfExpr):
                branches = [expr.then_branch] + [branch.body for branch in expr.elif_branches]
                if expr.else_branch is not None:
                    branches.append(expr.else_branch)
                for branch in branches:
                    self._mark_tail_result_lift(branch.tail_expr, target)
            elif isinstance(expr, MatchExpr):
                for arm in expr.arms:
                    self._mark_tail_result_lift(arm.body.tail_expr, target)
            source = self.interner.get_entry(expr.inferred_type_id)
            if source.tag == 'result' and not self.type_relations.is_error_channel_assignable(
                    source.data[1], self.interner.intern(target.err)):
                raise TypeError("Incompatible error channel in unit result tail", file=self.file,
                                line=expr.line, col=expr.col, code="W2004")
            return
        self._mark_result_lift(expr, target)

    def _mark_result_lift(self, expr: Optional[Expr], target: WysnaType, force: bool = False) -> None:
        if expr is None:
            return
        target_id = self.interner.intern(target)
        source_entry = self.interner.get_entry(expr.inferred_type_id)
        target_entry = self.interner.get_entry(target_id)
        if source_entry.tag == target_entry.tag == "result":
            source_ok, source_error = source_entry.data
            target_ok, target_error = target_entry.data
            same_ok = source_ok == target_ok or self.interner.get_entry(source_ok).tag == "any" or self.interner.get_entry(target_ok).tag == "any"
            same_error = self.type_relations.is_error_channel_assignable(source_error, target_error)
            if same_ok and same_error:
                return
        if self.interner.get_entry(target_id).tag == "result" and expr.inferred_type_id != target_id:
            if force or source_entry.tag == "result" or isinstance(target, EnumType):
                expr.force_result_wrap_id = target_id

    def _check_payload(self, expr: Expr, expected: Optional[WysnaType]) -> WysnaType:
        previous = self.expected_type
        self.expected_type = expected
        try:
            actual = self._check_expr(expr)
        finally:
            self.expected_type = previous
        if expected is not None and self.type_relations.is_assignable(actual, expected):
            self._mark_result_lift(expr, expected, force=True)
            return expected
        return actual

    def _builtin_arguments(self, type_: Optional[WysnaType], name: str) -> List[WysnaType]:
        if name == "Option" and isinstance(type_, OptionalType):
            return [type_.inner]
        if name == "Result" and isinstance(type_, ResultType):
            return [type_.ok, type_.err]
        if isinstance(type_, EnumType) and getattr(type_, "template_name", None) == name:
            return type_.type_args or []
        return []

    def _module_receiver(self, expression):
        if isinstance(expression, IdentExpr):
            symbol = self.current_scope.lookup_symbol(expression.name)
        elif isinstance(expression, MemberExpr):
            parent = self._module_receiver(expression.object)
            symbol = parent.scope.lookup_symbol(expression.property) if parent else None
        else:
            return None
        return symbol.type if symbol and isinstance(symbol.type, ModuleType) else None

    def _check_expr(self, expr: Expr, is_statement_context: bool = False, allow_module: bool = False) -> WysnaType:
        handler = EXPRESSION_RULES.handler_for(expr, self.file)
        inferred_type = handler(self, expr, is_statement_context)
        if isinstance(inferred_type, ModuleType) and not allow_module:
            raise TypeError("Module namespaces cannot be used as runtime values", file=self.file,
                            line=expr.line, col=expr.col, code="W2003")
        if isinstance(expr, MemberExpr) and isinstance(inferred_type, FunctionType) and not getattr(expr, 'is_callee', False) and not hasattr(expr, 'function_reference') and not isinstance(getattr(expr.object, 'inferred_type', None), StructType) and not (isinstance(getattr(expr.object, 'inferred_type', None), ClassType) and expr.property in expr.object.inferred_type.fields):
            raise TypeError("Bound method values require an explicit lambda adapter", file=self.file, line=expr.line, col=expr.col, code="W2013")
        expr.inferred_type = inferred_type
        expr.inferred_type_id = self.interner.from_type(inferred_type)
        expr.session_arena_id = self.interner.arena_id
        return inferred_type

    def _pattern_matrix_exhaustive(self, types: List[WysnaType], rows: List[List[Any]]) -> bool:
        from checker.PatternCoverage import pattern_matrix_exhaustive
        return pattern_matrix_exhaustive(types, rows, self.interner)

    def _match_is_exhaustive(self, expression) -> bool:
        return self._pattern_matrix_exhaustive(
            [expression.subject.inferred_type],
            [[arm.pattern] for arm in expression.arms if arm.guard is None],
        )

    def _resolve_error_type_pattern(self, pattern: Pattern, subject: ErrorUnionType) -> ErrorTypePattern:
        if isinstance(pattern, ErrorTypePattern):
            type_name, bindings = pattern.type_name, [pattern.binding]
            matched = next((member for member in subject.alternatives if member == pattern.matched_type), None)
        else:
            type_name = f"{pattern.enum_name}.{pattern.variant}" if pattern.enum_name else pattern.variant
            bindings = pattern.args
            if "." in type_name:
                resolved = self._lookup_type_name(type_name)
                matched = next((member for member in subject.alternatives if member == resolved), None)
            else:
                matched = next((member for member in subject.alternatives
                                if self._error_pattern_name(member) == type_name), None)
            type_name = getattr(pattern, 'source_name', type_name)
        if matched is None:
            raise TypeError(message=f"Pattern '{type_name}(...)' is not an alternative of error union '{subject}'",
                            line=pattern.line, col=pattern.col, file=self.file, code="W2001")
        if len(bindings) != 1:
            raise TypeError(message=f"Error type pattern '{type_name}(...)' requires exactly one binding pattern",
                            line=pattern.line, col=pattern.col, file=self.file, code="W2005")
        if not isinstance(bindings[0], (IdentifierPattern, WildcardPattern)):
            raise TypeError(message="Error type patterns accept one binding or wildcard, such as 'ParseError(err)'",
                            line=pattern.line, col=pattern.col, file=self.file, code="W2005")
        return ErrorTypePattern(type_name, bindings[0], matched, pattern.line, pattern.col)

    @staticmethod
    def _error_pattern_name(error_type: WysnaType) -> str:
        if error_type in (IntType, FloatType, BoolType, StrType):
            return str(error_type)
        if isinstance(error_type, (StructType, EnumType)):
            return error_type.name
        return str(error_type)

    def _validate_pattern_owner(self, pattern: EnumPattern, expected_type: WysnaType) -> None:
        if not pattern.enum_name:
            return
        owners = set()
        if isinstance(expected_type, EnumType):
            owners.add(expected_type.name)
            owners.add(getattr(expected_type, "template_name", None))
        elif isinstance(expected_type, OptionalType):
            owners.add("Option")
        elif isinstance(expected_type, ResultType):
            owners.add("Result")
        if pattern.enum_name not in owners:
            raise TypeError(
                message=f"Pattern '{pattern.enum_name}.{pattern.variant}' is incompatible with match subject '{expected_type}'",
                line=pattern.line, col=pattern.col, file=self.file, code="W2001")

    def _bind_pattern_type(self, pattern: Pattern, expected_type: WysnaType, bindings: Dict[str, WysnaType], line: int, col: int) -> None:
        pattern.inferred_type_id = self.interner.intern(expected_type)
        pattern.session_arena_id = self.interner.arena_id
        if isinstance(pattern, IdentifierPattern):
            if pattern.name in bindings:
                raise TypeError(message=f"Duplicate pattern binding '{pattern.name}'",
                                line=pattern.line, col=pattern.col, file=self.file, code="W1002")
            bindings[pattern.name] = expected_type
        elif isinstance(pattern, WildcardPattern):
            pass
        elif isinstance(pattern, TuplePattern):
            if not isinstance(expected_type, TupleType) or len(pattern.elements) != len(expected_type.elements):
                raise TypeError(
                    message=f"Tuple pattern cannot match type '{expected_type}'",
                    line=line,
                    col=col,
                    file=self.file,
                    code="W2001"
                )
            for sub_p, sub_t in zip(pattern.elements, expected_type.elements):
                self._bind_pattern_type(sub_p, sub_t, bindings, line, col)
        elif isinstance(pattern, EnumPattern):
            self._validate_pattern_owner(pattern, expected_type)
            optional = self._builtin_arguments(expected_type, "Option")
            result = self._builtin_arguments(expected_type, "Result")
            if optional:
                payloads = {"Some": optional, "None": []}
            elif result:
                payloads = {"Ok": [result[0]], "Err": [result[1]]}
            elif isinstance(expected_type, EnumType):
                payloads = expected_type.variant_payloads
                pattern.enum_name = expected_type.name
            else:
                raise TypeError("Enum pattern requires enum type", line=line, col=col, file=self.file, code="W2001")
            types = payloads.get(pattern.variant)
            if types is None or len(types) != len(pattern.args):
                raise TypeError("Enum pattern payload does not match variant", line=line, col=col, file=self.file, code="W2001")
            for child, child_type in zip(pattern.args, types):
                self._bind_pattern_type(child, child_type, bindings, line, col)
        elif isinstance(pattern, LiteralPattern):
            lit_t = self._check_expr(pattern.literal)
            if not self.type_relations.is_assignable(lit_t, expected_type):
                raise TypeError(
                    message=f"Literal pattern type '{lit_t}' incompatible with '{expected_type}'",
                    line=line,
                    col=col,
                    file=self.file,
                    code="W2001"
                )

    # ==========================================
    # Generics and Monomorphization Helpers
    # ==========================================

    def _concrete_type_args(self, template, bindings: Dict[str, WysnaType]) -> List[WysnaType]:
        """Builds the specialization arguments for a generic template.

        A type parameter with no binding keeps its own identity instead of collapsing
        to the `any` placeholder. `any` erases identity, so `Ok(42)` and `Err("bad")`
        would specialize to `Result__int__any` and `Result__any__int`, two distinct
        types that then fail to compare even though both are `Result[int, str]`.
        """
        arguments: List[WysnaType] = []
        for parameter in template.type_params:
            name = parameter.name if hasattr(parameter, "name") else str(parameter)
            bound = bindings.get(name)
            if bound is None or isinstance(bound, AnyType):
                arguments.append(TypeParamType(name, owner=template.name))
            else:
                arguments.append(bound)
        return arguments

    def _mangle_type_name(self, t: WysnaType) -> str:
        return _mangle_type_static(t)

    def _type_to_type_node(self, t: WysnaType) -> TypeNode:
        node = self._type_to_type_node_impl(t)
        if isinstance(t, (StructType, EnumType, ClassType, InterfaceType, TypeParamType)) and self.current_scope.lookup_type(getattr(t, "name", "")) is not t:
            node.session_resolved_type = t
        node.resolved_type_id = self.interner.from_type(t)
        node.session_arena_id = self.interner.arena_id
        return node

    def _type_to_type_node_impl(self, t: WysnaType) -> TypeNode:
        if isinstance(t, AnyType):
            # Synthesized, never written by the user: this is the placeholder that
            # monomorphization binds to an uninferred type parameter. Mark it so the
            # resolver can tell it apart from a user-written `any`, which is rejected.
            synthesized = NamedType("any")
            synthesized.is_internal_any = True
            return synthesized
        if t == IntType:
            return NamedType("int")
        if t == FloatType:
            return NamedType("float")
        if t == BoolType:
            return NamedType("bool")
        if t == StrType:
            return NamedType("str")
        if t == UnitType:
            return NamedType("unit")
        if t == NoneType:
            return NamedType("none")
        if isinstance(t, StructType):
            return NamedType(t.name)
        if isinstance(t, EnumType):
            return NamedType(t.name)
        if isinstance(t, ClassType):
            return NamedType(t.name)
        if isinstance(t, InterfaceType):
            return NamedType(t.name)
        if isinstance(t, WeakType):
            return GenericType("Weak", [self._type_to_type_node(t.target)])
        if isinstance(t, OptionalType):
            return AstOptionalType(self._type_to_type_node(t.inner))
        if isinstance(t, ListType):
            return AstListType(self._type_to_type_node(t.element))
        if isinstance(t, DictType):
            return AstDictType(self._type_to_type_node(t.key), self._type_to_type_node(t.value))
        if isinstance(t, TupleType):
            return AstTupleType([self._type_to_type_node(e) for e in t.elements])
        if isinstance(t, ResultType):
            return AstResultType(self._type_to_type_node(t.ok), self._type_to_type_node(t.err))
        if isinstance(t, ErrorUnionType):
            return AstErrorUnionType([self._type_to_type_node(member) for member in t.alternatives])
        if isinstance(t, FunctionType):
            return AstFunctionType([self._type_to_type_node(p) for p in t.params], self._type_to_type_node(t.ret))
        if isinstance(t, TypeParamType):
            return NamedType(t.name)
        return NamedType(str(t))

    def _substitute_type_node(self, node: TypeNode, subst: Dict[str, WysnaType]) -> TypeNode:
        if isinstance(node, AstFunctionType):
            return AstFunctionType([self._substitute_type_node(p, subst) for p in node.params],
                                   self._substitute_type_node(node.return_type, subst), node.line, node.col)
        if isinstance(node, NamedType):
            if node.name in subst:
                return self._type_to_type_node(subst[node.name])
            return NamedType(node.name, line=node.line, col=node.col)
        if isinstance(node, GenericType):
            new_args = [self._substitute_type_node(a, subst) for a in node.type_args]
            return GenericType(node.name, new_args, line=node.line, col=node.col)
        if isinstance(node, AstOptionalType):
            return AstOptionalType(self._substitute_type_node(node.inner, subst), line=node.line, col=node.col)
        if isinstance(node, AstListType):
            return AstListType(self._substitute_type_node(node.element_type, subst), line=node.line, col=node.col)
        if isinstance(node, AstDictType):
            return AstDictType(
                self._substitute_type_node(node.key_type, subst),
                self._substitute_type_node(node.value_type, subst),
                line=node.line,
                col=node.col
            )
        if isinstance(node, AstTupleType):
            return AstTupleType([self._substitute_type_node(e, subst) for e in node.element_types], line=node.line, col=node.col)
        if isinstance(node, AstResultType):
            return AstResultType(
                self._substitute_type_node(node.ok_type, subst),
                self._substitute_type_node(node.err_type, subst),
                line=node.line,
                col=node.col
            )
        if isinstance(node, AstErrorUnionType):
            return AstErrorUnionType(
                [self._substitute_type_node(member, subst) for member in node.error_types],
                line=node.line,
                col=node.col
            )
        return node

    def _substitute_ast_types(self, node: Any, subst: Dict[str, WysnaType]) -> None:
        if node is None:
            return
        if isinstance(node, LambdaExpr):
            for parameter in node.params:
                if parameter.type_ann is not None:
                    parameter.type_ann = self._substitute_type_node(parameter.type_ann, subst)
            if node.return_type is not None:
                node.return_type = self._substitute_type_node(node.return_type, subst)
            self._substitute_ast_types(node.body, subst)
        elif isinstance(node, NewExpr):
            if getattr(node, 'type_args', None):
                node.type_args = [self._substitute_type_node(a, subst) for a in node.type_args]
            for argument in node.args:
                self._substitute_ast_types(argument, subst)
        elif isinstance(node, LetDecl):
            if node.type_ann is not None:
                node.type_ann = self._substitute_type_node(node.type_ann, subst)
            init_val = getattr(node, "initializer", getattr(node, "init", None))
            if init_val is not None:
                self._substitute_ast_types(init_val, subst)
        elif isinstance(node, StructInitExpr):
            if node.type_args:
                node.type_args = [self._substitute_type_node(a, subst) for a in node.type_args]
            for f in node.fields:
                self._substitute_ast_types(f.value, subst)
        elif isinstance(node, CallExpr):
            if node.type_args:
                node.type_args = [self._substitute_type_node(a, subst) for a in node.type_args]
            self._substitute_ast_types(node.callee, subst)
            for a in node.args:
                self._substitute_ast_types(a, subst)
        elif isinstance(node, Block):
            for s in node.statements:
                self._substitute_ast_types(s, subst)
            if node.tail_expr:
                self._substitute_ast_types(node.tail_expr, subst)
        elif isinstance(node, ExprStmt):
            self._substitute_ast_types(node.expr, subst)
        elif isinstance(node, ReturnStmt):
            if node.value:
                self._substitute_ast_types(node.value, subst)
        elif isinstance(node, WhileStmt):
            self._substitute_ast_types(node.condition, subst)
            self._substitute_ast_types(node.body, subst)
        elif isinstance(node, ForStmt):
            self._substitute_ast_types(node.iterable, subst)
            self._substitute_ast_types(node.body, subst)
        elif isinstance(node, IfExpr):
            self._substitute_ast_types(node.condition, subst)
            self._substitute_ast_types(node.then_branch, subst)
            for eb in node.elif_branches:
                self._substitute_ast_types(eb.condition, subst)
                self._substitute_ast_types(eb.body, subst)
            if node.else_branch:
                self._substitute_ast_types(node.else_branch, subst)
        elif isinstance(node, MatchExpr):
            self._substitute_ast_types(node.subject, subst)
            for arm in node.arms:
                if arm.guard:
                    self._substitute_ast_types(arm.guard, subst)
                self._substitute_ast_types(arm.body, subst)
        elif isinstance(node, BinaryExpr):
            self._substitute_ast_types(node.left, subst)
            self._substitute_ast_types(node.right, subst)
        elif isinstance(node, UnaryExpr):
            self._substitute_ast_types(node.operand, subst)
        elif isinstance(node, MemberExpr):
            self._substitute_ast_types(node.object, subst)
        elif isinstance(node, IndexExpr):
            self._substitute_ast_types(node.object, subst)
            self._substitute_ast_types(node.index, subst)
        elif isinstance(node, AssignmentExpr):
            self._substitute_ast_types(node.target, subst)
            self._substitute_ast_types(node.value, subst)
        elif isinstance(node, ListExpr):
            for e in node.elements:
                self._substitute_ast_types(e, subst)
        elif isinstance(node, DictExpr):
            for e in node.entries:
                if hasattr(e, "key"):
                    self._substitute_ast_types(e.key, subst)
                    self._substitute_ast_types(e.value, subst)
                elif isinstance(e, (list, tuple)) and len(e) == 2:
                    self._substitute_ast_types(e[0], subst)
                    self._substitute_ast_types(e[1], subst)
        elif isinstance(node, TupleExpr):
            for e in node.elements:
                self._substitute_ast_types(e, subst)
        elif isinstance(node, RangeExpr):
            self._substitute_ast_types(node.start, subst)
            self._substitute_ast_types(node.end, subst)
        elif isinstance(node, PostfixTryExpr):
            self._substitute_ast_types(node.operand, subst)
        elif isinstance(node, ElseErrExpr):
            self._substitute_ast_types(node.operand, subst)
            self._substitute_ast_types(node.handler, subst)
        elif isinstance(node, CoalesceExpr):
            self._substitute_ast_types(node.left, subst)
            self._substitute_ast_types(node.right, subst)

    def _unify_type(
        self,
        param_type_node: TypeNode,
        arg_type: WysnaType,
        type_params: Set[str],
        bindings: Dict[str, WysnaType]
    ) -> bool:
        if isinstance(param_type_node, NamedType) and param_type_node.name in type_params:
            p_name = param_type_node.name
            if p_name in bindings:
                return bindings[p_name] == arg_type
            bindings[p_name] = arg_type
            return True

        if isinstance(param_type_node, AstFunctionType) and isinstance(arg_type, FunctionType):
            return (len(param_type_node.params) == len(arg_type.params) and
                    all(self._unify_type(p, a, type_params, bindings)
                        for p, a in zip(param_type_node.params, arg_type.params)) and
                    self._unify_type(param_type_node.return_type, arg_type.ret, type_params, bindings))

        if isinstance(param_type_node, AstOptionalType) and isinstance(arg_type, OptionalType):
            return self._unify_type(param_type_node.inner, arg_type.inner, type_params, bindings)

        if isinstance(param_type_node, AstListType) and isinstance(arg_type, ListType):
            return self._unify_type(param_type_node.element_type, arg_type.element, type_params, bindings)

        if isinstance(param_type_node, AstDictType) and isinstance(arg_type, DictType):
            return (self._unify_type(param_type_node.key_type, arg_type.key, type_params, bindings) and
                    self._unify_type(param_type_node.value_type, arg_type.value, type_params, bindings))

        if isinstance(param_type_node, AstTupleType) and isinstance(arg_type, TupleType):
            if len(param_type_node.element_types) != len(arg_type.elements):
                return False
            return all(self._unify_type(pt, at, type_params, bindings)
                       for pt, at in zip(param_type_node.element_types, arg_type.elements))

        if isinstance(param_type_node, AstResultType) and isinstance(arg_type, ResultType):
            return (self._unify_type(param_type_node.ok_type, arg_type.ok, type_params, bindings) and
                    self._unify_type(param_type_node.err_type, arg_type.err, type_params, bindings))

        if isinstance(param_type_node, AstErrorUnionType) and isinstance(arg_type, ErrorUnionType):
            try:
                declared = ErrorUnionType([self._resolve_type_node(member) for member in param_type_node.error_types])
            except Exception:
                return False
            return declared == arg_type

        if isinstance(param_type_node, GenericType) and isinstance(arg_type, (StructType, EnumType, ClassType, InterfaceType)):
            tmpl_name = getattr(arg_type, "template_name", None)
            tmpl_args = getattr(arg_type, "type_args", None)
            if tmpl_name == param_type_node.name and tmpl_args and len(tmpl_args) == len(param_type_node.type_args):
                return all(self._unify_type(pt, at, type_params, bindings)
                           for pt, at in zip(param_type_node.type_args, tmpl_args))

        # The declared payload type mentions no type parameter, so it must be checked
        # against the argument. Returning True here (the previous behaviour) let
        # `enum Pair[T] { P(T, int) }` accept `P(1, "wrong")`: the checker certified a
        # program that both backends then rejected with an internal error.
        try:
            declared = self._resolve_type_node(param_type_node)
        except Exception:
            # Unresolvable here means "not a declared concrete type"; leave the
            # verdict to the dedicated resolution paths that report diagnostics.
            return True
        if isinstance(declared, (GenericStructTemplate, GenericEnumTemplate, GenericFnTemplate)):
            return True
        return self.type_relations.is_assignable(arg_type, declared)
    def _resolve_constraint_interface(self, constraint: TypeNode, line: int, col: int) -> InterfaceType:
        if not isinstance(constraint, NamedType):
            raise TypeError("Generic constraints must name an interface", line=line,
                            col=col, file=self.file, code="W2012")
        resolved = self.global_scope.lookup_type(constraint.name)
        if not isinstance(resolved, InterfaceType):
            raise TypeError(f"Generic constraint '{constraint.name}' is not a declared interface",
                            line=line, col=col, file=self.file, code="W2012")
        return resolved

    def _check_type_constraint(self, type_arg: WysnaType, constraint: Optional[TypeNode], line: int, col: int) -> None:
        if constraint is None:
            return
        c_name = constraint.name if isinstance(constraint, NamedType) else str(constraint)
        c_type = self._resolve_constraint_interface(constraint, line, col)
        if isinstance(type_arg, TypeParamType) and self._checking_generic_definition:
            self.type_relations.record_requirement(
                "constraint", type_arg, c_name, file=self.file, line=line, col=col
            )
            return
        if isinstance(c_type, InterfaceType):
            if isinstance(type_arg, ClassType):
                if not type_arg.implements_interface(c_name):
                    raise TypeError(
                        message=f"Type '{type_arg}' does not implement interface constraint '{c_name}'",
                        line=line,
                        col=col,
                        file=self.file,
                        code="W2001"
                    )
            else:
                raise TypeError(
                    message=f"Type '{type_arg}' does not satisfy interface constraint '{c_name}'",
                    line=line,
                    col=col,
                    file=self.file,
                    code="W2001"
                )

    def _type_complexity(self, t: WysnaType) -> int:
        """Structural size of a type: 1 for a scalar, plus the size of every component.

        Used to bound generic specializations by *size* rather than only by recursion
        depth. A depth limit alone lets a chain of shallow but ever-growing instances
        (each level adding one list wrapper) allocate until it exhausts memory, because
        every level is a distinct specialization that is never rebuilt.
        """
        if isinstance(t, ListType):
            return 1 + self._type_complexity(t.element)
        if isinstance(t, DictType):
            return 1 + self._type_complexity(t.key) + self._type_complexity(t.value)
        if isinstance(t, OptionalType):
            return 1 + self._type_complexity(t.inner)
        if isinstance(t, ResultType):
            return 1 + self._type_complexity(t.ok) + self._type_complexity(t.err)
        if isinstance(t, ErrorUnionType):
            return 1 + sum(self._type_complexity(member) for member in t.alternatives)
        if isinstance(t, (StructType, EnumType)) and getattr(t, "type_args", None):
            return 1 + sum(self._type_complexity(a) for a in t.type_args)
        if isinstance(t, TupleType):
            return 1 + sum(self._type_complexity(a) for a in t.elements)
        if isinstance(t, FunctionType):
            return 1 + sum(self._type_complexity(p) for p in t.params) + self._type_complexity(t.ret)
        return 1

    def _check_specialization_size(
        self,
        template_name: str,
        concrete_args: Sequence[WysnaType],
        line: int,
        col: int
    ) -> None:
        total = sum(self._type_complexity(a) for a in concrete_args)
        if total > MAX_SPECIALIZATION_COMPLEXITY:
            raise TypeError(
                message=f"Generic instantiation of '{template_name}' exceeds the maximum "
                        f"type size ({total} > {MAX_SPECIALIZATION_COMPLEXITY}); the "
                        f"specialization chain grows without a structural bound",
                line=line,
                col=col,
                file=self.file,
                code="W2011"
            )

    def _check_specialization_cycle(
        self,
        template_name: str,
        concrete_args: Sequence[WysnaType],
        line: int,
        col: int
    ) -> None:
        matching_frames = [f for f in self._instantiation_stack if f.template_name == template_name]
        if len(matching_frames) >= 2:
            f0 = matching_frames[-2]
            f1 = matching_frames[-1]
            if len(f0.arguments) == len(concrete_args) and len(f1.arguments) == len(concrete_args):
                step1_sub = all(_type_contains(fa, fb) for fa, fb in zip(f0.arguments, f1.arguments))
                step1_strict = any(type_strictly_contains(fa, fb) for fa, fb in zip(f0.arguments, f1.arguments))
                step2_sub = all(_type_contains(fb, ca) for fb, ca in zip(f1.arguments, concrete_args))
                step2_strict = any(type_strictly_contains(fb, ca) for fb, ca in zip(f1.arguments, concrete_args))
                if step1_sub and step1_strict and step2_sub and step2_strict:
                    start_idx = self._instantiation_stack.index(f0)
                    chain_frames = self._instantiation_stack[start_idx:]
                    chain = [f"{frame.template_name}[{', '.join(str(a) for a in frame.arguments)}]" for frame in chain_frames]
                    chain.append(f"{template_name}[{', '.join(str(a) for a in concrete_args)}]")
                    chain_str = " -> ".join(chain)
                    raise TypeError(
                        message=f"Monomorphization recursion depth limit exceeded (64) / structural generic expansion cycle detected for '{template_name}': instantiation chain {chain_str} expands argument types indefinitely",
                        line=line,
                        col=col,
                        file=self.file,
                        code="W2011"
                    )

        if self._monomorphize_depth >= 64:
            raise TypeError(
                message=f"Monomorphization recursion depth limit exceeded for '{template_name}': maximum instantiation depth reached without proven termination",
                line=line,
                col=col,
                file=self.file,
                code="W2011"
            )

    def _monomorphize_struct(self, template, concrete_args, line, col):
        return self.instantiator.structure(template, concrete_args, line, col)


    def _monomorphize_fn(self, template, concrete_args, line, col):
        return self.instantiator.function(template, concrete_args, line, col)


    def _monomorphize_enum(self, template, concrete_args, line, col):
        return self.instantiator.enumeration(template, concrete_args, line, col)


    # ==========================================
    # Type Node Resolution
    # ==========================================

    def _is_valid_dict_key_type(self, t: WysnaType) -> bool:
        if isinstance(t, AnyType):
            return True
        if t in (IntType, StrType, BoolType):
            return True
        if isinstance(t, EnumType):
            return all(self._is_valid_dict_key_type(payload)
                       for payloads in t.variant_payloads.values() for payload in payloads)
        return False

    def _resolve_type_node(self, node: TypeNode) -> WysnaType:
        if hasattr(node, 'session_resolved_type'):
            if node.session_arena_id != self.interner.arena_id:
                raise TypeError("Synthesized type belongs to another arena", file=self.file,
                                line=node.line, col=node.col, code="W9999")
            resolved = node.session_resolved_type
        else:
            # Inside a specialization a type-parameter name is bound by `subst`, so it
            # must be resolved from that binding rather than looked up as a declared
            # type: an unbound parameter keeps its own name (see _concrete_type_args)
            # and there is nothing declared to find.
            subst = self._active_type_subst
            if subst and isinstance(node, NamedType) and node.name in subst:
                resolved = subst[node.name]
            else:
                resolved = self._resolve_type_node_impl(node)
        if node is not None:
            node.resolved_type_id = self.interner.from_type(resolved)
            node.session_arena_id = self.interner.arena_id
        return resolved

    def _lookup_type_name(self, name):
        if '.' not in name:
            return self.current_scope.lookup_type(name)
        parts = name.split('.')
        symbol = self.current_scope.lookup_symbol(parts[0])
        module = symbol.type if symbol else None
        for part in parts[1:-1]:
            symbol = module.scope.lookup_symbol(part) if isinstance(module, ModuleType) else None
            module = symbol.type if symbol else None
        return module.scope.lookup_type(parts[-1]) if isinstance(module, ModuleType) else None

    def _resolve_type_node_impl(self, node: TypeNode) -> WysnaType:
        if isinstance(node, AstFunctionType):
            return FunctionType([self._resolve_type_node(p) for p in node.params],
                                self._resolve_type_node(node.return_type))
        if isinstance(node, NamedType):
            if node.name == "any":
                if getattr(node, "is_internal_any", False):
                    return AnyType()
                # `any` is reserved for internal variadic builtin signatures and is
                # deliberately unreachable from source. See checker/WysnaType.py:AnyType.
                raise TypeError(
                    message="Unknown type 'any' (reserved for compiler builtins)",
                    line=node.line,
                    col=node.col,
                    file=self.file,
                    code="W1001",
                )
            resolved = self._lookup_type_name(node.name)
            if resolved is None:
                if node.name in self.generic_enum_templates:
                    raise TypeError(
                        message=f"Generic enum '{node.name}' requires type arguments",
                        line=node.line,
                        col=node.col,
                        file=self.file,
                        code="W2001"
                    )
                raise TypeError(
                    message=f"Unknown type '{node.name}'",
                    line=node.line,
                    col=node.col,
                    file=self.file,
                    code="W1001"
                )
            if isinstance(resolved, (GenericStructTemplate, GenericEnumTemplate, GenericOopTemplate)):
                raise TypeError(
                    message=f"Generic type '{node.name}' requires type arguments",
                    line=node.line,
                    col=node.col,
                    file=self.file,
                    code="W2001"
                )
            return resolved

        if isinstance(node, GenericType):
            if node.name == "Weak":
                if len(node.type_args) != 1:
                    raise TypeError("Weak requires exactly one class type", line=node.line,
                                    col=node.col, file=self.file, code="W2003")
                target = self._resolve_type_node(node.type_args[0])
                if not isinstance(target, ClassType):
                    raise TypeError("Weak target must be a concrete class", line=node.line,
                                    col=node.col, file=self.file, code="W2003")
                return WeakType(target)
            lookup = self._lookup_type_name(node.name)
            if lookup is None:
                lookup = self.generic_struct_templates.get(node.name)
            if lookup is None:
                lookup = self.generic_enum_templates.get(node.name)
            if isinstance(lookup, GenericOopTemplate):
                specialized = specialize_oop(lookup, [self._resolve_type_node(a) for a in node.type_args], node.line, node.col)
                if self.global_scope.lookup_type(specialized.name) is None:
                    self.global_scope.define_type(specialized.name, specialized, file=self.file)
                return specialized
            if isinstance(lookup, GenericStructTemplate):
                resolved_args = [self._resolve_type_node(a) for a in node.type_args]
                return self._monomorphize_struct(lookup, resolved_args, node.line, node.col)
            if isinstance(lookup, GenericEnumTemplate):
                resolved_args = [self._resolve_type_node(a) for a in node.type_args]
                return self._monomorphize_enum(lookup, resolved_args, node.line, node.col)
            raise TypeError(
                message=f"Unknown generic type '{node.name}'",
                line=node.line,
                col=node.col,
                file=self.file,
                code="W1001"
            )

        if isinstance(node, AstOptionalType):
            inner = self._resolve_type_node(node.inner)
            return OptionalType(inner)

        if isinstance(node, AstListType):
            elem = self._resolve_type_node(node.element_type)
            return ListType(elem)

        if isinstance(node, AstDictType):
            key_t = self._resolve_type_node(node.key_type)
            if not self._is_valid_dict_key_type(key_t):
                raise TypeError(
                    message=f"Invalid dictionary key type '{key_t}'; allowed key types are int, str, bool, or enum",
                    line=node.key_type.line,
                    col=node.key_type.col,
                    file=self.file,
                    code="W2010"
                )
            val_t = self._resolve_type_node(node.value_type)
            return DictType(key_t, val_t)

        if isinstance(node, AstTupleType):
            elems = [self._resolve_type_node(t) for t in node.element_types]
            return TupleType(elems)

        if isinstance(node, AstResultType):
            ok_t = self._resolve_type_node(node.ok_type)
            err_t = self._resolve_type_node(node.err_type)
            return ResultType(ok_t, err_t)

        # Type substitution in the canonical Result[T, E] enum can place the
        # error-set node inside a generic payload annotation, even though source
        # syntax only introduces unions after `!`.
        if isinstance(node, AstErrorUnionType):
            return self._resolve_error_union_node(node)

        raise TypeError(
            message=f"Unsupported type construct '{node.__class__.__name__}'",
            line=node.line,
            col=node.col,
            file=self.file,
            code="W1001"
        )

    def _resolve_error_union_node(self, node: AstErrorUnionType) -> ErrorUnionType:
        if len(node.error_types) < 2:
            raise TypeError("An error union requires at least two alternatives", line=node.line,
                            col=node.col, file=self.file, code="W2001")

        alternatives = [self._resolve_type_node(member) for member in node.error_types]
        for alternative, source_node in zip(alternatives, node.error_types):
            is_scalar = alternative in (IntType, FloatType, BoolType, StrType)
            is_nominal = isinstance(alternative, (StructType, EnumType))
            is_generic_nominal = is_nominal and bool(getattr(alternative, "type_args", ()))
            if not (is_scalar or (is_nominal and not is_generic_nominal)):
                raise TypeError(
                    message=f"Error union alternative '{alternative}' has no distinct runtime type identity; use int, float, bool, str, a non-generic struct, or a non-generic enum",
                    line=source_node.line,
                    col=source_node.col,
                    file=self.file,
                    code="W2001"
                )

        union = ErrorUnionType(alternatives)
        if len(union.alternatives) < 2:
            raise TypeError("An error union must contain at least two distinct alternatives",
                            line=node.line, col=node.col, file=self.file, code="W2001")
        node.resolved_type_id = self.interner.from_type(union)
        node.session_arena_id = self.interner.arena_id
        return union
