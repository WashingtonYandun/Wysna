"""Generic definition checking and specialization orchestration."""
from copy import copy

from checker.OopGenerics import GenericOopTemplate, specialize as specialize_oop
from checker.WysnaType import FunctionType, TypeParamType, ClassType, StructType
from error.Error import TypeError, WysnaError
from node.NodeType import clone_ast, ClassDecl


class GenericDefinitionChecker:
    """Check generic declarations with symbolic parameters before use."""

    def __init__(self, checker):
        self.checker = checker

    def _run_recovering(self, action):
        known = len(getattr(self.checker, "_collected_errors", ()))
        try:
            action()
        except WysnaError as error:
            if hasattr(self.checker, "_record_error"):
                self.checker._record_error(error, known)
            else:
                raise

    def check_all(self):
        for template in self.checker.generic_fn_templates.values():
            self._run_recovering(lambda t=template: self.check_function(t))
        for template in self.checker.generic_struct_templates.values():
            self._run_recovering(lambda t=template: self.check_structure(t))
        for template in self.checker.generic_enum_templates.values():
            self._run_recovering(lambda t=template: self.check_enumeration(t))
        for template in list(self.checker.global_scope.types.values()):
            if isinstance(template, GenericOopTemplate) and template.checker is self.checker:
                self._run_recovering(lambda t=template: self.check_oop_type(t))
        for cls_name, cls_decl in list(self.checker.class_decls.items()):
            cls_type = self.checker.global_scope.lookup_type(cls_name)
            if isinstance(cls_type, ClassType):
                for gtmpl in list(cls_type.generic_methods.values()):
                    if gtmpl.owner_class == cls_type.name:
                        self._run_recovering(
                            lambda cd=cls_decl, ct=cls_type, gm=gtmpl: self.check_generic_method(cd, ct, gm)
                        )

    def _symbolic_substitutions(self, template):
        checker = self.checker
        substitutions = {}
        for parameter in template.type_params:
            constraint = getattr(parameter, "constraint", None)
            resolved_constraint = (
                checker._resolve_constraint_interface(constraint, parameter.line, parameter.col)
                if constraint is not None else None
            )
            substitutions[parameter.name] = TypeParamType(
                parameter.name, constraint=resolved_constraint, owner=template.name
            )
        return substitutions

    def _check_isolated(self, template, check):
        """Check a symbolic template while discarding all temporary specialization state."""
        checker = self.checker
        substitutions = self._symbolic_substitutions(template)
        saved = {
            "symbols": dict(checker.global_scope.symbols),
            "symbol_types": {name: symbol.type for name, symbol in checker.global_scope.symbols.items()},
            "types": dict(checker.global_scope.types),
            "active_subst": checker._active_type_subst,
            "generic_definition": checker._checking_generic_definition,
            "specializations": dict(checker.specializations),
            "monomorphized_structs": dict(checker.monomorphized_structs),
            "monomorphized_fns": dict(checker.monomorphized_fns),
            "monomorphized_enums": dict(checker.monomorphized_enums),
            "oop_decls": list(checker.monomorphized_oop_decls),
            "class_decls": dict(checker.class_decls),
            "class_method_lists": [
                (cdecl, list(cdecl.methods))
                for cdecl in checker.class_decls.values()
                if isinstance(cdecl, ClassDecl)
            ],
            "class_type_methods": [
                (ctype, dict(ctype.methods), dict(ctype.generic_methods))
                for ctype in checker.global_scope.types.values()
                if isinstance(ctype, ClassType)
            ],
            "generic_method_instances": [
                (gm, dict(gm.instances))
                for ctype in checker.global_scope.types.values()
                if isinstance(ctype, ClassType)
                for gm in ctype.generic_methods.values()
            ],
            "struct_decls": list(checker.monomorphized_struct_decls),
            "fn_decls": list(checker.monomorphized_fn_decls),
            "enum_decls": list(checker.monomorphized_enum_decls),
            "oop_instances": [
                (value, dict(value.instances))
                for value in checker.global_scope.types.values()
                if isinstance(value, GenericOopTemplate)
            ],
            "current_scope": checker.current_scope,
            "current_class": checker.current_class,
            "current_fn_ret_type": checker.current_fn_ret_type,
            "expected_type": checker.expected_type,
            "loop_depth": checker.loop_depth,
            "instantiation_stack": list(checker._instantiation_stack),
            "monomorphize_depth": checker._monomorphize_depth,
            "telemetry": copy(checker.telemetry),
        }

        try:
            checker._active_type_subst = substitutions
            checker._checking_generic_definition = template
            with checker.type_relations.capture_obligations() as obligations:
                check(substitutions)
            template.obligations = tuple(obligations)
        finally:
            checker.global_scope.symbols.clear()
            checker.global_scope.symbols.update(saved["symbols"])
            for name, symbol in saved["symbols"].items():
                symbol.type = saved["symbol_types"][name]
            checker.global_scope.types.clear()
            checker.global_scope.types.update(saved["types"])
            checker._active_type_subst = saved["active_subst"]
            checker._checking_generic_definition = saved["generic_definition"]
            checker.specializations.clear()
            checker.specializations.update(saved["specializations"])
            checker.monomorphized_structs.clear()
            checker.monomorphized_structs.update(saved["monomorphized_structs"])
            checker.monomorphized_fns.clear()
            checker.monomorphized_fns.update(saved["monomorphized_fns"])
            checker.monomorphized_enums.clear()
            checker.monomorphized_enums.update(saved["monomorphized_enums"])
            checker.monomorphized_oop_decls[:] = saved["oop_decls"]
            checker.class_decls.clear()
            checker.class_decls.update(saved["class_decls"])
            for cdecl, methods in saved["class_method_lists"]:
                cdecl.methods[:] = methods
            for ctype, methods, gmethods in saved["class_type_methods"]:
                ctype.methods.clear()
                ctype.methods.update(methods)
                ctype.generic_methods.clear()
                ctype.generic_methods.update(gmethods)
            for gm, instances in saved["generic_method_instances"]:
                gm.instances.clear()
                gm.instances.update(instances)
            checker.monomorphized_struct_decls[:] = saved["struct_decls"]
            checker.monomorphized_fn_decls[:] = saved["fn_decls"]
            checker.monomorphized_enum_decls[:] = saved["enum_decls"]
            for template_instance, instances in saved["oop_instances"]:
                template_instance.instances.clear()
                template_instance.instances.update(instances)
            checker.current_scope = saved["current_scope"]
            checker.current_class = saved["current_class"]
            checker.current_fn_ret_type = saved["current_fn_ret_type"]
            checker.expected_type = saved["expected_type"]
            checker.loop_depth = saved["loop_depth"]
            checker._instantiation_stack[:] = saved["instantiation_stack"]
            checker._monomorphize_depth = saved["monomorphize_depth"]
            checker.telemetry = saved["telemetry"]

    def check_function(self, template):
        checker = self.checker
        symbol = checker.global_scope.lookup_symbol(template.name)
        if symbol is None:
            return

        def check(substitutions):
            clone = checker._clone_generic_definition(template, substitutions)
            parameter_types = [checker._resolve_type_node(parameter.type_ann) for parameter in clone.params]
            return_type = checker._resolve_type_node(clone.return_type)
            symbol.type = FunctionType(parameter_types, return_type)
            checker._check_fn_decl(clone)

        self._check_isolated(template, check)

    def check_structure(self, template):
        checker = self.checker

        def check(_substitutions):
            declaration = clone_ast(template.ast_decl)
            seen_fields = set()
            field_types = {}
            for field in declaration.fields:
                if field.name in seen_fields:
                    raise TypeError(
                        message=f"Duplicate field '{field.name}' in struct '{template.name}'",
                        line=field.line, col=field.col, file=checker.file, code="W1002"
                    )
                seen_fields.add(field.name)
                field_types[field.name] = checker._resolve_type_node(field.type_ann)
            if any(field.requires for field in declaration.fields):
                declaration.type_params = []
                checker.global_scope.types[template.name] = StructType(template.name, field_types)
                checker._check_struct_contracts(declaration)

        self._check_isolated(template, check)

    def check_enumeration(self, template):
        checker = self.checker

        def check(_substitutions):
            declaration = clone_ast(template.ast_decl)
            seen_variants = set()
            for variant in declaration.variants:
                if variant.name in seen_variants:
                    raise TypeError(
                        message=f"Duplicate variant '{variant.name}' in enum '{template.name}'",
                        line=variant.line, col=variant.col, file=checker.file, code="W1002"
                    )
                seen_variants.add(variant.name)
                for payload_type in variant.payload_types:
                    checker._resolve_type_node(payload_type)

        self._check_isolated(template, check)

    def check_generic_method(self, cls_decl, cls_type, template):
        checker = self.checker

        def check(substitutions):
            cloned = clone_ast(template.ast_decl)
            cloned.type_params = []
            prev_subst = checker._active_type_subst
            merged_subst = dict(prev_subst) if prev_subst else {}
            merged_subst.update(substitutions)
            checker._active_type_subst = merged_subst
            try:
                parameter_types = [checker._resolve_type_node(parameter.type_ann) for parameter in cloned.params]
                return_type = checker._resolve_type_node(cloned.return_type)
            finally:
                checker._active_type_subst = prev_subst
            fn_type = FunctionType(parameter_types, return_type)
            checker._check_single_method_decl(cls_decl, cls_type, cloned, fn_type, recovering=False)

        self._check_isolated(template, check)

    def check_oop_type(self, template):
        checker = self.checker

        def check(substitutions):
            symbolic_args = [substitutions[parameter.name] for parameter in template.type_params]
            sym_type = specialize_oop(
                template, symbolic_args,
                template.ast_decl.line, template.ast_decl.col,
                refresh_session=False,
            )
            if isinstance(sym_type, ClassType):
                sym_decl = checker.class_decls.get(sym_type.name)
                if sym_decl is not None:
                    for gtmpl in list(sym_type.generic_methods.values()):
                        if gtmpl.owner_class == sym_type.name:
                            self.check_generic_method(sym_decl, sym_type, gtmpl)

        self._check_isolated(template, check)
