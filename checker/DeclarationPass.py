"""Declaration collection phase over a session-owned semantic context."""
from typing import TYPE_CHECKING
from checker.OopGenerics import GenericOopTemplate
from typing import Dict, List, Set
from node.NodeType import (
    Program,
    TypeNode,
    FnDecl,
    StructDecl,
    EnumDecl,
    InterfaceDecl,
    ClassDecl,
    ImportDecl,
    UseDecl,
    UseSymbol,
)
from checker.WysnaType import (
    WysnaType,
    ModuleType,
    StructType,
    EnumType,
    InterfaceType,
    ClassFieldInfo,
    ClassMethodInfo,
    ClassType,
    FunctionType,
    GenericStructTemplate,
    GenericFnTemplate,
    GenericEnumTemplate,
)
from checker.SymbolTable import Symbol
from error.Error import TypeError

if TYPE_CHECKING:
    from checker.TypeChecker import TypeChecker


def collect_declarations(checker: "TypeChecker", program: Program) -> None:
    """Publish shells before resolving signatures, then validate inheritance contracts."""
    validate_declaration_names(checker, program)
    register_nominal_shells(checker, program)
    class_decls = resolve_declaration_signatures(checker, program)
    validate_class_contracts(checker, class_decls)


def validate_declaration_names(checker: "TypeChecker", program: Program) -> None:
    for stmt in program.body:
        if hasattr(stmt, "name") and isinstance(stmt.name, str) and not hasattr(stmt, "specialization_shell"):
            if stmt.name.startswith("_WYSNA_") or stmt.name.startswith("__wysna_internal_"):
                raise TypeError(
                    message=f"Identifier '{stmt.name}' is reserved for internal compiler use",
                    line=stmt.line,
                    col=stmt.col,
                    file=checker.file,
                    code="W1002"
                )


def register_nominal_shells(checker: "TypeChecker", program: Program) -> None:
    for stmt in program.body:
        if isinstance(stmt, EnumDecl):
            if stmt.type_params:
                template = GenericEnumTemplate(stmt.name, stmt.type_params, stmt)
                checker.generic_enum_templates[stmt.name] = template
                checker.global_scope.define_type(stmt.name, template, line=stmt.line, col=stmt.col, file=checker.file)
                continue

            seen_variants: Set[str] = set()
            variant_names: List[str] = []
            for v in stmt.variants:
                v_name = v.name if hasattr(v, "name") else str(v)
                if v_name in seen_variants:
                    raise TypeError(
                        message=f"Duplicate variant '{v_name}' in enum '{stmt.name}'",
                        line=stmt.line,
                        col=stmt.col,
                        file=checker.file,
                        code="W1002"
                    )
                seen_variants.add(v_name)
                variant_names.append(v_name)

            enum_type = EnumType(name=stmt.name, variants=variant_names, variant_payloads={})
            checker.global_scope.define_type(stmt.name, enum_type, line=stmt.line, col=stmt.col, file=checker.file)

        elif isinstance(stmt, InterfaceDecl):
            if stmt.type_params:
                checker.global_scope.define_type(stmt.name, GenericOopTemplate(stmt, checker), file=checker.file)
                continue
            if hasattr(stmt, 'specialization_shell'):
                continue
            intf_type = InterfaceType(name=stmt.name, methods={})
            checker.global_scope.define_type(stmt.name, intf_type, line=stmt.line, col=stmt.col, file=checker.file)

        elif isinstance(stmt, ClassDecl):
            if stmt.type_params:
                checker.global_scope.define_type(stmt.name, GenericOopTemplate(stmt, checker), file=checker.file)
                continue
            if hasattr(stmt, 'specialization_shell'):
                continue
            cls_type = ClassType(name=stmt.name)
            checker.global_scope.define_type(stmt.name, cls_type, line=stmt.line, col=stmt.col, file=checker.file)

        elif isinstance(stmt, StructDecl):
            if stmt.type_params:
                template = GenericStructTemplate(name=stmt.name, type_params=stmt.type_params, ast_decl=stmt)
                checker.generic_struct_templates[stmt.name] = template
                checker.global_scope.define_type(stmt.name, template, line=stmt.line, col=stmt.col, file=checker.file)
            else:
                seen_fields: Set[str] = set()
                for f in stmt.fields:
                    if f.name in seen_fields:
                        raise TypeError(
                            message=f"Duplicate field '{f.name}' in struct '{stmt.name}'",
                            line=f.line,
                            col=f.col,
                            file=checker.file,
                            code="W1002"
                        )
                    seen_fields.add(f.name)
                struct_type = StructType(name=stmt.name, fields={})
                checker.global_scope.define_type(stmt.name, struct_type, line=stmt.line, col=stmt.col, file=checker.file)

        elif isinstance(stmt, UseDecl):
            resolve_use_declaration(checker, stmt)

        elif isinstance(stmt, ImportDecl):
            mod_type = checker._resolve_import(stmt)
            checker.global_scope.define_symbol(
                Symbol(stmt.namespace, mod_type, is_mut=False, kind="import", line=stmt.line, col=stmt.col),
                file=checker.file
            )


def _bind_symbol_from_unit(checker: "TypeChecker", unit, name: str, bound_name: str, line: int, col: int, module_path: str) -> None:
    found = False
    if not hasattr(checker, "imported_symbol_names"):
        checker.imported_symbol_names = {}

    if name in unit.exports.symbols:
        sym = unit.exports.symbols[name]
        checker.imported_symbol_names[bound_name] = sym.name
        checker.global_scope.define_symbol(
            Symbol(bound_name, sym.type, is_mut=False, kind=sym.kind, line=line, col=col),
            file=checker.file
        )
        found = True

    if name in unit.exports.types:
        t = unit.exports.types[name]
        mangled_type_name = getattr(t, "name", name)
        checker.imported_symbol_names[bound_name] = mangled_type_name
        checker.global_scope.define_type(bound_name, t, line=line, col=col, file=checker.file)
        if isinstance(t, GenericStructTemplate):
            checker.generic_struct_templates[bound_name] = t
        elif isinstance(t, GenericFnTemplate):
            checker.generic_fn_templates[bound_name] = t
        elif isinstance(t, GenericEnumTemplate):
            checker.generic_enum_templates[bound_name] = t
        elif isinstance(t, GenericOopTemplate):
            checker.generic_oop_templates[bound_name] = t
        found = True

    if not found:
        avail = sorted(set(list(unit.exports.symbols.keys()) + list(unit.exports.types.keys())))
        avail_str = f" Available exports: {', '.join(avail)}" if avail else " The module has no exported symbols."
        raise TypeError(
            f"Module '{module_path}' has no exported symbol '{name}'.{avail_str}",
            file=checker.file, line=line, col=col, code="W1003"
        )


def resolve_use_declaration(checker: "TypeChecker", stmt: UseDecl) -> None:
    if not hasattr(checker, "imported_symbol_names"):
        checker.imported_symbol_names = {}
    checked_modules = []

    base_file = checker.session.find_module_file(stmt.path, checker.file)
    base_unit = checker.session.load_module_by_path(base_file, checker.file, stmt.line, stmt.col) if base_file else None
    if base_unit is not None:
        checked_modules.append(base_unit)
        stmt.checked_module = base_unit

    for sym_spec in stmt.symbols:
        bound_name = sym_spec.bound_name
        name = sym_spec.name

        if sym_spec.subpath is not None:
            submodule_path = f"{stmt.path}.{sym_spec.subpath}"
            sub_file = checker.session.find_module_file(submodule_path, checker.file)
            if sub_file is None:
                raise TypeError(f"Module not found: '{submodule_path}'", file=checker.file,
                                line=sym_spec.line, col=sym_spec.col, code="W1003")
            sub_unit = checker.session.load_module_by_path(sub_file, checker.file, sym_spec.line, sym_spec.col)
            checked_modules.append(sub_unit)
            _bind_symbol_from_unit(checker, sub_unit, name, bound_name, sym_spec.line, sym_spec.col, submodule_path)
        else:
            if base_unit is not None and (name in base_unit.exports.symbols or name in base_unit.exports.types):
                _bind_symbol_from_unit(checker, base_unit, name, bound_name, sym_spec.line, sym_spec.col, stmt.path)
            else:
                submodule_path = f"{stmt.path}.{name}"
                sub_file = checker.session.find_module_file(submodule_path, checker.file)
                if sub_file is not None:
                    sub_unit = checker.session.load_module_by_path(sub_file, checker.file, sym_spec.line, sym_spec.col)
                    checked_modules.append(sub_unit)
                    mod_type = ModuleType(bound_name, sub_unit.exports)
                    mod_type.checked_module = sub_unit
                    checker.global_scope.define_symbol(
                        Symbol(bound_name, mod_type, is_mut=False, kind="import", line=sym_spec.line, col=sym_spec.col),
                        file=checker.file
                    )
                elif base_unit is not None:
                    avail = sorted(set(list(base_unit.exports.symbols.keys()) + list(base_unit.exports.types.keys())))
                    avail_str = f" Available exports: {', '.join(avail)}" if avail else " The module has no exported symbols."
                    raise TypeError(
                        f"Module '{stmt.path}' has no exported symbol '{name}'.{avail_str}",
                        file=checker.file, line=sym_spec.line, col=sym_spec.col, code="W1003"
                    )
                else:
                    raise TypeError(
                        f"Module not found: '{stmt.path}'",
                        file=checker.file, line=stmt.line, col=stmt.col, code="W1003"
                    )

    stmt.checked_modules = checked_modules
    if checked_modules and not getattr(stmt, 'checked_module', None):
        stmt.checked_module = checked_modules[0]


def resolve_declaration_signatures(checker: "TypeChecker", program: Program) -> List[ClassDecl]:
    class_decls: List[ClassDecl] = []
    for stmt in program.body:
        if isinstance(stmt, EnumDecl):
            if stmt.type_params:
                continue
            enum_type = checker.global_scope.lookup_type(stmt.name)
            assert isinstance(enum_type, EnumType)
            variant_payloads: Dict[str, List[WysnaType]] = {}
            for v in stmt.variants:
                v_name = v.name if hasattr(v, "name") else str(v)
                payload_types: List[WysnaType] = []
                if hasattr(v, "payload_types") and v.payload_types:
                    for pt in v.payload_types:
                        payload_types.append(checker._resolve_type_node(pt))
                variant_payloads[v_name] = payload_types
            enum_type.variant_payloads = variant_payloads
            checker.interner.mk_enum(
                enum_type.name,
                enum_type.variants,
                {name: [checker.interner.from_type(payload) for payload in payloads]
                 for name, payloads in variant_payloads.items()},
            )

        elif isinstance(stmt, StructDecl):
            if stmt.type_params:
                continue
            struct_type = checker.global_scope.lookup_type(stmt.name)
            assert isinstance(struct_type, StructType)
            fields_dict: Dict[str, WysnaType] = {}
            for f in stmt.fields:
                f_type = checker._resolve_type_node(f.type_ann)
                fields_dict[f.name] = f_type
            struct_type.fields = fields_dict
            checker.interner.from_type(struct_type)

        elif isinstance(stmt, InterfaceDecl):
            if stmt.type_params:
                continue
            intf_type = checker.global_scope.lookup_type(stmt.name)
            assert isinstance(intf_type, InterfaceType)
            for m in stmt.methods:
                if m.name in intf_type.methods:
                    raise TypeError(
                        message=f"Duplicate method '{m.name}' in interface '{stmt.name}'",
                        line=m.line,
                        col=m.col,
                        file=checker.file,
                        code="W1002"
                    )
                p_types = [checker._resolve_type_node(p.type_ann) for p in m.params]
                r_type = checker._resolve_type_node(m.return_type)
                intf_type.methods[m.name] = FunctionType(params=p_types, ret=r_type)

        elif isinstance(stmt, ClassDecl):
            if stmt.type_params:
                continue
            if isinstance(stmt.superclass, TypeNode):
                stmt.superclass = checker._resolve_type_node(stmt.superclass).name
            stmt.interfaces = [checker._resolve_type_node(interface).name if isinstance(interface, TypeNode) else interface
                               for interface in stmt.interfaces]
            class_decls.append(stmt)
            cls_type = checker.global_scope.lookup_type(stmt.name)
            assert isinstance(cls_type, ClassType)

            if stmt.superclass is not None:
                sup_t = checker.global_scope.lookup_type(stmt.superclass)
                if sup_t is None or not isinstance(sup_t, ClassType):
                    raise TypeError(
                        message=f"Unknown superclass '{stmt.superclass}' for class '{stmt.name}'",
                        line=stmt.line,
                        col=stmt.col,
                        file=checker.file,
                        code="W1001"
                    )
                if sup_t.name == stmt.name or sup_t.is_subclass_of(stmt.name):
                    raise TypeError(
                        message=f"Cyclic class inheritance detected involving '{stmt.name}'",
                        line=stmt.line,
                        col=stmt.col,
                        file=checker.file,
                        code="W3003"
                    )
                cls_type.superclass = sup_t

            for intf_name in stmt.interfaces:
                intf_t = checker.global_scope.lookup_type(intf_name)
                if intf_t is None or not isinstance(intf_t, InterfaceType):
                    raise TypeError(
                        message=f"Unknown interface '{intf_name}' implemented by class '{stmt.name}'",
                        line=stmt.line,
                        col=stmt.col,
                        file=checker.file,
                        code="W1001"
                    )
                cls_type.interfaces.append(intf_t)

            # Own fields & methods
            seen_own_fields: Set[str] = set()
            for f in stmt.fields:
                if f.name in seen_own_fields:
                    raise TypeError(
                        message=f"Duplicate field '{f.name}' in class '{stmt.name}'",
                        line=f.line,
                        col=f.col,
                        file=checker.file,
                        code="W1002"
                    )
                seen_own_fields.add(f.name)
                f_type = checker._resolve_type_node(f.type_ann)
                cls_type.fields[f.name] = ClassFieldInfo(type=f_type, is_public=f.is_public, owner_class=stmt.name)

            seen_own_methods: Set[str] = set()
            for m in stmt.methods:
                if m.name in seen_own_methods:
                    raise TypeError(
                        message=f"Duplicate method '{m.name}' in class '{stmt.name}'",
                        line=m.line,
                        col=m.col,
                        file=checker.file,
                        code="W1002"
                    )
                seen_own_methods.add(m.name)
                p_types = [checker._resolve_type_node(p.type_ann) for p in m.params]
                r_type = checker._resolve_type_node(m.return_type)
                fn_t = FunctionType(params=p_types, ret=r_type)
                if m.name == "init":
                    cls_type.init_type = fn_t
                else:
                    cls_type.methods[m.name] = ClassMethodInfo(fn_type=fn_t, is_public=m.is_public, owner_class=stmt.name)

        elif isinstance(stmt, FnDecl):
            if stmt.type_params:
                template = GenericFnTemplate(name=stmt.name, type_params=stmt.type_params, ast_decl=stmt)
                checker.generic_fn_templates[stmt.name] = template
                checker.global_scope.define_symbol(
                    Symbol(stmt.name, template, is_mut=False, kind="fn", line=stmt.line, col=stmt.col),
                    file=checker.file
                )
            else:
                param_types: List[WysnaType] = [checker._resolve_type_node(p.type_ann) for p in stmt.params]
                ret_type = checker._resolve_type_node(stmt.return_type)
                fn_type = FunctionType(params=param_types, ret=ret_type)
                checker.global_scope.define_symbol(
                    Symbol(stmt.name, fn_type, is_mut=False, kind="fn", line=stmt.line, col=stmt.col),
                    file=checker.file
                )
    return class_decls


def validate_class_contracts(checker: "TypeChecker", class_decls: List[ClassDecl]) -> None:
    for stmt in class_decls:
        cls_type = checker.global_scope.lookup_type(stmt.name)
        assert isinstance(cls_type, ClassType)

        # Collect ancestor chain from root superclass down to direct superclass
        ancestors: List[ClassType] = []
        curr_sup = cls_type.superclass
        while curr_sup is not None:
            ancestors.append(curr_sup)
            curr_sup = curr_sup.superclass
        ancestors.reverse()

        merged_fields: Dict[str, ClassFieldInfo] = {}
        merged_methods: Dict[str, ClassMethodInfo] = {}
        for anc in ancestors:
            for fname, finfo in anc.fields.items():
                merged_fields[fname] = finfo
            for mname, minfo in anc.methods.items():
                merged_methods[mname] = minfo

        # Apply subclass own fields and methods (checking override compatibility)
        for fname, finfo in cls_type.fields.items():
            if fname in merged_fields and finfo.owner_class == cls_type.name:
                raise TypeError(f"Inherited field '{fname}' cannot be redeclared", file=checker.file,
                                line=stmt.line, col=stmt.col, code="W3003")
            merged_fields[fname] = finfo
        for mname, minfo in cls_type.methods.items():
            if mname in merged_methods:
                base_m = merged_methods[mname]
                if not base_m.is_public:
                    raise TypeError(
                        message=f"Cannot override private method '{mname}' of superclass '{base_m.owner_class}'",
                        line=stmt.line,
                        col=stmt.col,
                        file=checker.file,
                        code="W3002"
                    )
                if base_m.is_public and not minfo.is_public:
                    raise TypeError(f"Public method '{mname}' cannot become private", file=checker.file,
                                    line=stmt.line, col=stmt.col, code="W3002")
                if minfo.fn_type.params != base_m.fn_type.params or not checker.type_relations.is_assignable(minfo.fn_type.ret, base_m.fn_type.ret):
                    raise TypeError(
                        message=f"Method '{mname}' in class '{stmt.name}' has incompatible override signature with superclass '{base_m.owner_class}'",
                        line=stmt.line,
                        col=stmt.col,
                        file=checker.file,
                        code="W3003"
                    )
            merged_methods[mname] = minfo

        cls_type.fields = merged_fields
        cls_type.methods = merged_methods

        # Verify all implemented interfaces
        for intf in cls_type.interfaces:
            for req_name, req_sig in intf.methods.items():
                if req_name not in cls_type.methods:
                    raise TypeError(
                        message=f"Class '{stmt.name}' does not implement required method '{req_name}' from interface '{intf.name}'",
                        line=stmt.line,
                        col=stmt.col,
                        file=checker.file,
                        code="W3003"
                    )
                impl_info = cls_type.methods[req_name]
                if not impl_info.is_public:
                    raise TypeError(
                        message=f"Interface method '{req_name}' in class '{stmt.name}' must be public",
                        line=stmt.line,
                        col=stmt.col,
                        file=checker.file,
                        code="W3003"
                    )
                if impl_info.fn_type.params != req_sig.params or not checker.type_relations.is_assignable(impl_info.fn_type.ret, req_sig.ret):
                    raise TypeError(
                        message=f"Method '{req_name}' in class '{stmt.name}' does not match signature required by interface '{intf.name}'",
                        line=stmt.line,
                        col=stmt.col,
                        file=checker.file,
                        code="W3003"
                    )
