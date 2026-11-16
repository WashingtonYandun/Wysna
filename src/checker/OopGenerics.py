"""Nominal class/interface specialization in the shared type arena."""
from checker.WysnaType import WysnaType
from error.Error import TypeError
from node import NodeType as A
from node.NodeType import clone_ast


class GenericOopTemplate(WysnaType):
    def __init__(self, declaration, checker):
        self.name = declaration.name
        self.ast_decl = declaration
        self.type_params = declaration.type_params
        self.checker = checker
        self.instances = {}
        self.active = 0

    def __str__(self):
        return self.name


def specialize(template, arguments, line, col, *, refresh_session=True):
    checker = template.checker
    if len(arguments) != len(template.type_params):
        raise TypeError('Generic OOP type arity mismatch', file=checker.file, line=line, col=col, code='W2001')
    key = tuple(checker.interner.from_type(argument) for argument in arguments)
    if key in template.instances:
        checker.telemetry.deduplicated_hits += 1
        return template.instances[key]
    if template.active >= 32 or len(template.instances) >= 256:
        raise TypeError('Generic OOP specialization budget exceeded', file=checker.file, line=line, col=col, code='W2011')
    for parameter, argument in zip(template.type_params, arguments):
        checker._check_type_constraint(argument, parameter.constraint, line, col)
    from checker.GenericState import mangle_generic_name
    declaration = clone_ast(template.ast_decl)
    declaration.name = mangle_generic_name(template.name, arguments)
    declaration.type_params = []
    substitution = {parameter.name: argument for parameter, argument in zip(template.type_params, arguments)}

    def replace(node, active_subst=substitution):
        if isinstance(node, A.TypeNode):
            return checker._substitute_type_node(node, active_subst)
        if isinstance(node, A.MethodDecl) and getattr(node, 'type_params', None):
            shadowed = {p.name if hasattr(p, 'name') else str(p) for p in node.type_params}
            inner_subst = {k: v for k, v in active_subst.items() if k not in shadowed}
            if hasattr(node, 'params'):
                node.params = [replace(item, inner_subst) for item in node.params]
            if hasattr(node, 'return_type'):
                node.return_type = replace(node.return_type, inner_subst)
            if hasattr(node, 'requires'):
                node.requires = [replace(item, inner_subst) for item in node.requires]
            if hasattr(node, 'ensures'):
                node.ensures = [replace(item, inner_subst) for item in node.ensures]
            if hasattr(node, 'body'):
                node.body = replace(node.body, inner_subst)
            return node
        if isinstance(node, A.Node):
            for field, child in list(vars(node).items()):
                if isinstance(child, A.Node):
                    setattr(node, field, replace(child, active_subst))
                elif isinstance(child, list):
                    setattr(node, field, [replace(item, active_subst) for item in child])
        return node

    replace(declaration)
    previous = checker.current_scope
    checker.current_scope = checker.global_scope
    template.active += 1
    try:
        # Install the nominal shell first so recursive class references terminate.
        from checker.WysnaType import ClassType, InterfaceType
        type_ = ClassType(declaration.name) if isinstance(declaration, A.ClassDecl) else InterfaceType(declaration.name, {})
        type_.template_name, type_.type_args = template.name, arguments
        template.instances[key] = type_
        checker.global_scope.define_type(declaration.name, type_, file=checker.file)
        declaration.specialization_shell = type_
        if isinstance(declaration, A.ClassDecl):
            checker.class_decls[declaration.name] = declaration
        checker._pass1_collect_declarations(A.Program([declaration]))
        if isinstance(declaration, A.ClassDecl):
            declaration.methods = [m for m in declaration.methods if not getattr(m, 'type_params', None)]
        checker.monomorphized_oop_decls.append(declaration)
        checker.telemetry.specialization_requests += 1
        if isinstance(declaration, A.ClassDecl):
            checker._check_class_decl(declaration)
        unit = checker.session.modules.get(checker.file)
        if unit and refresh_session and not checker._checking_generic_definition:
            checker.session.refresh_specializations(unit)
        return type_
    except Exception:
        template.instances.pop(key, None)
        raise
    finally:
        template.active -= 1
        checker.current_scope = previous
