"""Concrete generic specialization algorithms and failure cleanup."""
from typing import Dict, List
from node.NodeType import (
    clone_ast,
    TypeNode,
    Block,
    NamedType,
    GenericType,
    FnDecl,
    Param,
    MethodDecl,
    ClassDecl,
    StructDecl,
    StructField,
    EnumDecl,
    ReturnStmt,
    IdentExpr,
    StructInitExpr,
    FieldInit,
    VariantDecl,
)
from checker.WysnaType import (
    WysnaType,
    StructType,
    EnumType,
    ClassType,
    ClassMethodInfo,
    FunctionType,
    GenericStructTemplate,
    GenericFnTemplate,
    GenericMethodTemplate,
    GenericEnumTemplate,
)
from checker.SymbolTable import Symbol
from error.Error import TypeError
from checker.GenericState import (
    GenericInstanceKey,
    InstantiationFrame,
    SpecializationEntry,
    SpecializationState,
    mangle_generic_name,
)


class Instantiator:
    """Specialize templates inside the invoking checker's session and type arena."""

    def __init__(self, checker):
        self.checker = checker

    def function(
        self,
        template: GenericFnTemplate,
        concrete_args: List[WysnaType],
        line: int,
        col: int
    ) -> Symbol:
        checker = self.checker
        if len(concrete_args) != len(template.type_params):
            raise TypeError(
                message=f"Generic function '{template.name}' expects {len(template.type_params)} type arguments, found {len(concrete_args)}",
                line=line,
                col=col,
                file=checker.file,
                code="W2001"
            )

        type_ids = tuple(checker.interner.intern(a) for a in concrete_args)
        decl_id = getattr(template, "decl_id", None)
        module_id = getattr(template, "module_id", getattr(checker.session, "current_module_id", None))
        key = GenericInstanceKey(template.name, type_ids, module_id=module_id, decl_id=decl_id)
        checker.telemetry.specialization_requests += 1

        if key in checker.specializations:
            entry = checker.specializations[key]
            if entry.state in (SpecializationState.COMPLETE, SpecializationState.SPECIALIZING):
                checker.telemetry.deduplicated_hits += 1
                assert entry.specialized_symbol is not None
                return entry.specialized_symbol
            elif entry.state == SpecializationState.FAILED:
                raise TypeError(
                    message=f"Previous specialization failed for '{template.name}'",
                    line=line, col=col, file=checker.file, code="W2011"
                )

        if key in checker.monomorphized_fns:
            checker.telemetry.deduplicated_hits += 1
            return checker.monomorphized_fns[key]

        checker._check_specialization_cycle(template.name, concrete_args, line, col)

        checker._check_specialization_size(template.name, concrete_args, line, col)

        subst: Dict[str, WysnaType] = {}
        for p, arg in zip(template.type_params, concrete_args):
            p_name = p.name if hasattr(p, "name") else str(p)
            p_constraint = getattr(p, "constraint", None)
            if p_constraint:
                checker._check_type_constraint(arg, p_constraint, line, col)
            subst[p_name] = arg

        mangled = mangle_generic_name(template.name, concrete_args, module_id)

        cloned_fn = clone_ast(template.ast_decl)
        cloned_fn.name = mangled
        cloned_fn.type_params = []
        previous_subst = checker._active_type_subst
        checker._active_type_subst = subst
        try:
            cloned_fn.return_type = checker._substitute_type_node(cloned_fn.return_type, subst)
            cloned_fn.params = [
                Param(p.name, checker._substitute_type_node(p.type_ann, subst), line=p.line, col=p.col)
                for p in cloned_fn.params
            ]
            for req in getattr(cloned_fn, "requires", []):
                checker._substitute_ast_types(req, subst)
            for ens in getattr(cloned_fn, "ensures", []):
                checker._substitute_ast_types(ens, subst)
            checker._substitute_ast_types(cloned_fn.body, subst)

            p_types = [checker._resolve_type_node(p.type_ann) for p in cloned_fn.params]
            ret_t = checker._resolve_type_node(cloned_fn.return_type)
        finally:
            checker._active_type_subst = previous_subst
        fn_type = FunctionType(params=p_types, ret=ret_t)
        fn_sym = Symbol(mangled, fn_type, is_mut=False, kind="fn", line=line, col=col)
        entry = SpecializationEntry(key=key, state=SpecializationState.SPECIALIZING, specialized_symbol=fn_sym, ast_decl=cloned_fn)
        checker.specializations[key] = entry
        checker.monomorphized_fns[key] = fn_sym
        checker.global_scope.define_symbol(fn_sym, file=checker.file)
        checker.monomorphized_fn_decls.append(cloned_fn)
        checker.telemetry.specialized_functions += 1

        frame = InstantiationFrame(template.name, tuple(concrete_args))
        checker._instantiation_stack.append(frame)
        checker._monomorphize_depth += 1
        pending_keys_start = len(checker._pending_dictionary_keys)
        try:
            checker._check_fn_decl(cloned_fn)
            if checker._monomorphize_depth == 1 and not checker._collecting_declarations:
                checker._validate_pending_dictionary_keys()
            entry.state = SpecializationState.COMPLETE
            return fn_sym
        except Exception:
            del checker._pending_dictionary_keys[pending_keys_start:]
            entry.state = SpecializationState.FAILED
            checker.monomorphized_fns.pop(key, None)
            if cloned_fn in checker.monomorphized_fn_decls:
                checker.monomorphized_fn_decls.remove(cloned_fn)
            checker.global_scope.symbols.pop(mangled, None)
            raise
        finally:
            checker._monomorphize_depth -= 1
            checker._instantiation_stack.pop()

    def method(
        self,
        receiver_cls_type: ClassType,
        template: GenericMethodTemplate,
        concrete_args: List[WysnaType],
        line: int,
        col: int,
    ):
        owner_checker = template.checker or self.checker
        if len(concrete_args) != len(template.type_params):
            raise TypeError(
                message=f"Generic method '{template.owner_class}.{template.name}' expects {len(template.type_params)} type arguments, found {len(concrete_args)}",
                line=line,
                col=col,
                file=self.checker.file,
                code="W2001",
            )

        type_ids = tuple(owner_checker.interner.intern(arg) for arg in concrete_args)
        if type_ids in template.instances:
            owner_checker.telemetry.deduplicated_hits += 1
            mangled_cached, info_cached = template.instances[type_ids]
            if mangled_cached not in receiver_cls_type.methods:
                receiver_cls_type.methods[mangled_cached] = info_cached
            return mangled_cached, info_cached

        template_key = f"{template.owner_class}.{template.name}"
        owner_checker._check_specialization_cycle(template_key, concrete_args, line, col)
        owner_checker._check_specialization_size(template_key, concrete_args, line, col)

        subst: Dict[str, WysnaType] = {}
        for param, arg in zip(template.type_params, concrete_args):
            p_name = param.name if hasattr(param, "name") else str(param)
            p_constraint = getattr(param, "constraint", None)
            if p_constraint:
                owner_checker._check_type_constraint(arg, p_constraint, line, col)
            subst[p_name] = arg

        mangled_name = mangle_generic_name(template.name, concrete_args)
        cloned = clone_ast(template.ast_decl)
        cloned.name = mangled_name
        cloned.type_params = []

        previous_subst = owner_checker._active_type_subst
        previous_scope = owner_checker.current_scope
        owner_checker._active_type_subst = subst
        owner_checker.current_scope = owner_checker.global_scope
        try:
            cloned.return_type = owner_checker._substitute_type_node(cloned.return_type, subst)
            cloned.params = [
                Param(p.name, owner_checker._substitute_type_node(p.type_ann, subst), line=p.line, col=p.col)
                for p in cloned.params
            ]
            for req in getattr(cloned, "requires", []):
                owner_checker._substitute_ast_types(req, subst)
            for ens in getattr(cloned, "ensures", []):
                owner_checker._substitute_ast_types(ens, subst)
            owner_checker._substitute_ast_types(cloned.body, subst)

            p_types = [owner_checker._resolve_type_node(p.type_ann) for p in cloned.params]
            ret_t = owner_checker._resolve_type_node(cloned.return_type)
        finally:
            owner_checker._active_type_subst = previous_subst
            owner_checker.current_scope = previous_scope

        fn_type = FunctionType(params=p_types, ret=ret_t)
        info = ClassMethodInfo(fn_type=fn_type, is_public=template.is_public, owner_class=template.owner_class)
        owner_cls_type = owner_checker.global_scope.lookup_type(template.owner_class)
        if not isinstance(owner_cls_type, ClassType):
            owner_cls_type = receiver_cls_type
        owner_cls_decl = owner_checker.class_decls.get(template.owner_class)

        template.instances[type_ids] = (mangled_name, info)
        owner_cls_type.methods[mangled_name] = info
        if receiver_cls_type is not owner_cls_type:
            receiver_cls_type.methods[mangled_name] = info
        for candidate_type in list(owner_checker.global_scope.types.values()) + list(self.checker.global_scope.types.values()):
            if isinstance(candidate_type, ClassType) and candidate_type.is_subclass_of(owner_cls_type.name):
                if mangled_name not in candidate_type.methods:
                    candidate_type.methods[mangled_name] = info
        if owner_cls_decl is not None:
            owner_cls_decl.methods.append(cloned)
        owner_checker.telemetry.specialized_functions += 1

        frame = InstantiationFrame(template_key, tuple(concrete_args))
        owner_checker._instantiation_stack.append(frame)
        owner_checker._monomorphize_depth += 1
        pending_keys_start = len(owner_checker._pending_dictionary_keys)
        check_scope_prev = owner_checker.current_scope
        owner_checker.current_scope = owner_checker.global_scope
        try:
            check_decl = owner_cls_decl if owner_cls_decl is not None else ClassDecl(
                name=owner_cls_type.name, fields=[], methods=[cloned], line=line, col=col
            )
            owner_checker._check_single_method_decl(check_decl, owner_cls_type, cloned, fn_type, recovering=False)
            if owner_checker._monomorphize_depth == 1 and not owner_checker._collecting_declarations:
                owner_checker._validate_pending_dictionary_keys()
            unit = owner_checker.session.modules.get(owner_checker.file)
            if unit is not None:
                owner_checker.session.refresh_specializations(unit)
                owner_checker.session.decorate(cloned, unit.path, unit.module_id)
                owner_checker.session._linked_programs.clear()
            return mangled_name, info
        except Exception:
            del owner_checker._pending_dictionary_keys[pending_keys_start:]
            template.instances.pop(type_ids, None)
            owner_cls_type.methods.pop(mangled_name, None)
            if receiver_cls_type is not owner_cls_type:
                receiver_cls_type.methods.pop(mangled_name, None)
            if owner_cls_decl is not None and cloned in owner_cls_decl.methods:
                owner_cls_decl.methods.remove(cloned)
            raise
        finally:
            owner_checker.current_scope = check_scope_prev
            owner_checker._monomorphize_depth -= 1
            owner_checker._instantiation_stack.pop()

    def structure(
        self,
        template: GenericStructTemplate,
        concrete_args: List[WysnaType],
        line: int,
        col: int
    ) -> StructType:
        checker = self.checker
        if len(concrete_args) != len(template.type_params):
            raise TypeError(
                message=f"Generic struct '{template.name}' expects {len(template.type_params)} type arguments, found {len(concrete_args)}",
                line=line,
                col=col,
                file=checker.file,
                code="W2001"
            )

        type_ids = tuple(checker.interner.intern(a) for a in concrete_args)
        decl_id = getattr(template, "decl_id", None)
        module_id = getattr(template, "module_id", getattr(checker.session, "current_module_id", None))
        key = GenericInstanceKey(template.name, type_ids, module_id=module_id, decl_id=decl_id)
        checker.telemetry.specialization_requests += 1

        if key in checker.specializations:
            entry = checker.specializations[key]
            if entry.state in (SpecializationState.COMPLETE, SpecializationState.SPECIALIZING):
                checker.telemetry.deduplicated_hits += 1
                assert entry.specialized_type is not None
                return entry.specialized_type
            elif entry.state == SpecializationState.FAILED:
                raise TypeError(
                    message=f"Previous specialization failed for '{template.name}'",
                    line=line, col=col, file=checker.file, code="W2011"
                )

        if key in checker.monomorphized_structs:
            checker.telemetry.deduplicated_hits += 1
            return checker.monomorphized_structs[key]

        checker._check_specialization_cycle(template.name, concrete_args, line, col)

        checker._check_specialization_size(template.name, concrete_args, line, col)

        # Direct layout recursion detection
        for f in template.ast_decl.fields:
            if isinstance(f.type_ann, (NamedType, GenericType)) and f.type_ann.name == template.name:
                raise TypeError(
                    message=f"Recursive generic struct cycle detected for '{template.name}' without indirection",
                    line=f.line,
                    col=f.col,
                    file=checker.file,
                    code="W2011"
                )

        subst: Dict[str, WysnaType] = {}
        for p, arg in zip(template.type_params, concrete_args):
            p_name = p.name if hasattr(p, "name") else str(p)
            p_constraint = getattr(p, "constraint", None)
            if p_constraint:
                checker._check_type_constraint(arg, p_constraint, line, col)
            subst[p_name] = arg

        mangled = mangle_generic_name(template.name, concrete_args, module_id)

        struct_t = StructType(name=mangled, fields={})
        struct_t.template_name = template.name
        struct_t.type_args = concrete_args
        entry = SpecializationEntry(key=key, state=SpecializationState.SPECIALIZING, specialized_type=struct_t)
        checker.specializations[key] = entry
        checker.monomorphized_structs[key] = struct_t
        checker.global_scope.define_type(mangled, struct_t, line=line, col=col, file=checker.file)

        frame = InstantiationFrame(template.name, tuple(concrete_args))
        checker._instantiation_stack.append(frame)
        checker._monomorphize_depth += 1
        previous_subst = checker._active_type_subst
        checker._active_type_subst = subst
        concrete_decl = None
        pending_keys_start = len(checker._pending_dictionary_keys)
        try:
            new_fields: List[StructField] = []
            fields_dict: Dict[str, WysnaType] = {}
            for f in template.ast_decl.fields:
                subst_ann = checker._substitute_type_node(f.type_ann, subst)
                f_type = checker._resolve_type_node(subst_ann)
                fields_dict[f.name] = f_type
                f_reqs = [clone_ast(r) for r in getattr(f, "requires", [])]
                for r in f_reqs:
                    checker._substitute_ast_types(r, subst)
                new_fields.append(StructField(name=f.name, type_ann=subst_ann, line=f.line, col=f.col, requires=f_reqs))
            struct_t.fields = fields_dict

            concrete_decl = StructDecl(name=mangled, fields=new_fields, type_params=[], line=template.ast_decl.line, col=template.ast_decl.col)
            checker._check_struct_contracts(concrete_decl)
            checker.monomorphized_struct_decls.append(concrete_decl)

            # Synthesize constructor function: fn mangled(f1, f2, ...) -> mangled
            ctor_params = [Param(f.name, f.type_ann, line=f.line, col=f.col) for f in new_fields]
            ctor_inits = [FieldInit(f.name, IdentExpr(f.name, line=f.line, col=f.col), line=f.line, col=f.col) for f in new_fields]
            ctor_ret = StructInitExpr(struct_name=mangled, fields=ctor_inits, line=template.ast_decl.line, col=template.ast_decl.col)
            ctor_body = Block(statements=[ReturnStmt(value=ctor_ret, line=template.ast_decl.line, col=template.ast_decl.col)], line=template.ast_decl.line, col=template.ast_decl.col)
            ctor_fn = FnDecl(
                name=mangled,
                params=ctor_params,
                return_type=NamedType(mangled, line=template.ast_decl.line, col=template.ast_decl.col),
                body=ctor_body,
                type_params=[],
                line=template.ast_decl.line,
                col=template.ast_decl.col
            )
            ctor_p_types = [fields_dict[f.name] for f in new_fields]
            ctor_type = FunctionType(params=ctor_p_types, ret=struct_t)
            ctor_sym = Symbol(mangled, ctor_type, is_mut=False, kind="fn", line=template.ast_decl.line, col=template.ast_decl.col)
            checker.global_scope.define_symbol(ctor_sym, file=checker.file)
            checker._check_fn_decl(ctor_fn)
            if checker._monomorphize_depth == 1 and not checker._collecting_declarations:
                checker._validate_pending_dictionary_keys()
            checker.monomorphized_fn_decls.append(ctor_fn)
            checker.telemetry.specialized_structs += 1
            entry.state = SpecializationState.COMPLETE
            entry.ast_decl = concrete_decl

            return struct_t
        except Exception:
            del checker._pending_dictionary_keys[pending_keys_start:]
            entry.state = SpecializationState.FAILED
            checker.monomorphized_structs.pop(key, None)
            if concrete_decl is not None and concrete_decl in checker.monomorphized_struct_decls:
                checker.monomorphized_struct_decls.remove(concrete_decl)
            checker.global_scope.types.pop(mangled, None)
            checker.global_scope.symbols.pop(mangled, None)
            raise
        finally:
            checker._monomorphize_depth -= 1
            checker._instantiation_stack.pop()

            checker._active_type_subst = previous_subst

    def enumeration(
        self,
        template: GenericEnumTemplate,
        concrete_args: List[WysnaType],
        line: int,
        col: int
    ) -> EnumType:
        checker = self.checker
        if len(concrete_args) != len(template.type_params):
            raise TypeError(
                message=f"Generic enum '{template.name}' expects {len(template.type_params)} type arguments, found {len(concrete_args)}",
                line=line,
                col=col,
                file=checker.file,
                code="W2001"
            )

        type_ids = tuple(checker.interner.intern(a) for a in concrete_args)
        decl_id = getattr(template, "decl_id", None)
        module_id = getattr(template, "module_id", getattr(checker.session, "current_module_id", None))
        key = GenericInstanceKey(template.name, type_ids, module_id=module_id, decl_id=decl_id)
        checker.telemetry.specialization_requests += 1

        if key in checker.specializations:
            entry = checker.specializations[key]
            if entry.state in (SpecializationState.COMPLETE, SpecializationState.SPECIALIZING):
                checker.telemetry.deduplicated_hits += 1
                assert entry.specialized_type is not None
                return entry.specialized_type
            elif entry.state == SpecializationState.FAILED:
                raise TypeError(
                    message=f"Previous specialization failed for '{template.name}'",
                    line=line, col=col, file=checker.file, code="W2011"
                )

        if key in checker.monomorphized_enums:
            checker.telemetry.deduplicated_hits += 1
            return checker.monomorphized_enums[key]

        checker._check_specialization_cycle(template.name, concrete_args, line, col)

        checker._check_specialization_size(template.name, concrete_args, line, col)

        subst: Dict[str, WysnaType] = {}
        for p, arg in zip(template.type_params, concrete_args):
            p_name = p.name if hasattr(p, "name") else str(p)
            p_constraint = getattr(p, "constraint", None)
            if p_constraint:
                checker._check_type_constraint(arg, p_constraint, line, col)
            subst[p_name] = arg

        mangled = mangle_generic_name(template.name, concrete_args, module_id)

        enum_t = EnumType(name=mangled, variants=[], variant_payloads={})
        enum_t.template_name = template.name
        enum_t.type_args = concrete_args
        entry = SpecializationEntry(key=key, state=SpecializationState.SPECIALIZING, specialized_type=enum_t)
        checker.specializations[key] = entry
        checker.monomorphized_enums[key] = enum_t
        checker.global_scope.define_type(mangled, enum_t, line=line, col=col, file=checker.file)

        frame = InstantiationFrame(template.name, tuple(concrete_args))
        checker._instantiation_stack.append(frame)
        checker._monomorphize_depth += 1
        previous_subst = checker._active_type_subst
        checker._active_type_subst = subst
        concrete_decl = None
        pending_keys_start = len(checker._pending_dictionary_keys)
        try:
            variant_names: List[str] = []
            variant_payloads: Dict[str, List[WysnaType]] = {}
            new_variants: List[VariantDecl] = []

            for v in template.ast_decl.variants:
                v_name = v.name if hasattr(v, "name") else str(v)
                variant_names.append(v_name)
                payload_types: List[WysnaType] = []
                new_payload_types: List[TypeNode] = []
                if hasattr(v, "payload_types") and v.payload_types:
                    for pt in v.payload_types:
                        subst_pt = checker._substitute_type_node(pt, subst)
                        resolved_pt = checker._resolve_type_node(subst_pt)
                        payload_types.append(resolved_pt)
                        new_payload_types.append(subst_pt)
                variant_payloads[v_name] = payload_types
                new_variants.append(VariantDecl(name=v_name, payload_types=new_payload_types, line=v.line if hasattr(v, "line") else line, col=v.col if hasattr(v, "col") else col))

            enum_t.variants = variant_names
            enum_t.variant_payloads = variant_payloads
            checker.interner.mk_enum(mangled, variant_names,
                {name: [checker.interner.from_type(payload) for payload in payloads]
                 for name, payloads in variant_payloads.items()},
                type_args=type_ids, template_name=template.name)

            if checker._monomorphize_depth == 1 and not checker._collecting_declarations:
                checker._validate_pending_dictionary_keys()
            concrete_decl = EnumDecl(name=mangled, variants=new_variants, type_params=[], line=template.ast_decl.line, col=template.ast_decl.col)
            checker.monomorphized_enum_decls.append(concrete_decl)
            checker.telemetry.specialized_enums += 1
            entry.state = SpecializationState.COMPLETE
            entry.ast_decl = concrete_decl

            return enum_t
        except Exception:
            del checker._pending_dictionary_keys[pending_keys_start:]
            entry.state = SpecializationState.FAILED
            checker.monomorphized_enums.pop(key, None)
            if concrete_decl is not None and concrete_decl in checker.monomorphized_enum_decls:
                checker.monomorphized_enum_decls.remove(concrete_decl)
            checker.global_scope.types.pop(mangled, None)
            raise
        finally:
            checker._monomorphize_depth -= 1
            checker._instantiation_stack.pop()
            checker._active_type_subst = previous_subst
