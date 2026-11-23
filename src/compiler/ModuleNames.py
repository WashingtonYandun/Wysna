"""Qualify module declarations while preserving lexical value/type bindings."""
from node import NodeType as A

DECLARATIONS = (A.FnDecl, A.StructDecl, A.EnumDecl, A.InterfaceDecl, A.ClassDecl, A.LetDecl)
PREFIX = '__wysna_module_'


def pattern_names(pattern):
    if isinstance(pattern, A.IdentifierPattern):
        return {pattern.name} - {'None', 'none'}
    if isinstance(pattern, A.ErrorTypePattern):
        return pattern_names(pattern.binding)
    if isinstance(pattern, A.OrPattern):
        children = pattern.alternatives[:1]
    elif isinstance(pattern, A.StructPattern):
        children = [field.pattern for field in pattern.fields]
    else:
        children = getattr(pattern, 'elements', getattr(pattern, 'args', []))
    return set().union(*(pattern_names(child) for child in children)) if children else set()


def _rename_pattern_idents(pattern, mapping):
    if isinstance(pattern, A.IdentifierPattern):
        if pattern.name in mapping:
            pattern.source_name = pattern.name
            pattern.name = mapping[pattern.name]
        return
    children = getattr(pattern, 'elements', getattr(pattern, 'args', []))
    for child in children:
        _rename_pattern_idents(child, mapping)


class ModuleNames:
    def __init__(self, program, module_id):
        prefix = PREFIX + str(module_id).encode('utf-8').hex() + '__'
        self.names = {}
        for node in program.body:
            if not isinstance(node, DECLARATIONS):
                continue
            if isinstance(node, A.LetDecl) and getattr(node, 'pattern', None) is not None:
                for pname in pattern_names(node.pattern):
                    self.names[pname] = prefix + pname
            else:
                self.names[node.name] = prefix + node.name
        self.nominal_types = {node.name for node in program.body if isinstance(node, (A.StructDecl, A.EnumDecl, A.ClassDecl))}
        self.variants = {variant.name: declaration.name for declaration in program.body
                         if isinstance(declaration, A.EnumDecl) for variant in declaration.variants}

    def qualify(self, program):
        for declaration in program.body:
            if isinstance(declaration, A.LetDecl) and getattr(declaration, 'pattern', None) is not None:
                declaration.source_pattern_names = sorted(pattern_names(declaration.pattern))
                _rename_pattern_idents(declaration.pattern, self.names)
            elif isinstance(declaration, DECLARATIONS):
                declaration.source_name = declaration.name
                declaration.name = self.names[declaration.name]
            self.visit(declaration, set(), set())
        return self.names

    def visit(self, node, values, types):
        if isinstance(node, list):
            return [self.visit(child, values, types) for child in node]
        if not isinstance(node, A.Node):
            return node
        if isinstance(node, A.IdentExpr):
            if node.name not in values:
                if node.name in self.names:
                    node.name = self.names[node.name]
                elif node.name in self.variants:
                    return A.MemberExpr(A.IdentExpr(self.names[self.variants[node.name]], node.line, node.col),
                                        node.name, node.line, node.col)
            return node
        if isinstance(node, (A.NamedType, A.GenericType)) and node.name not in types:
            node.name = self.names.get(node.name, node.name)
        if isinstance(node, (A.FnDecl, A.MethodDecl, A.LambdaExpr)):
            for attribute in getattr(node, 'attributes', []):
                if attribute.name not in ('deprecated', 'must_use'):
                    attribute.schema_name = self.names.get(attribute.name, attribute.name)
            type_scope = types | {parameter.name for parameter in getattr(node, 'type_params', [])}
            for parameter in getattr(node, 'type_params', []):
                self.visit(parameter.constraint, values, type_scope)
            for parameter in node.params:
                self.visit(parameter.type_ann, values, type_scope)
            self.visit(node.return_type, values, type_scope)
            self.visit(node.body, values | {parameter.name for parameter in node.params}, type_scope)
            return node
        if isinstance(node, (A.StructDecl, A.EnumDecl, A.ClassDecl, A.InterfaceDecl)):
            types = types | {parameter.name for parameter in node.type_params}
        if isinstance(node, A.ClassDecl):
            node.superclass = self.names.get(node.superclass, node.superclass) if isinstance(node.superclass, str) else self.visit(node.superclass, values, types)
            node.interfaces = [self.names.get(name, name) if isinstance(name, str) else self.visit(name, values, types) for name in node.interfaces]
        if isinstance(node, A.StructInitExpr):
            node.struct_name = self.names.get(node.struct_name, node.struct_name)
        if isinstance(node, A.StructPattern):
            node.struct_name = self.names.get(node.struct_name, node.struct_name)
        if isinstance(node, A.NewExpr):
            node.class_name = self.names.get(node.class_name, node.class_name)
        if isinstance(node, A.EnumPattern):
            for arg in getattr(node, 'type_args', ()):
                self.visit(arg, values, types)
            name = node.enum_name or self.variants.get(node.variant)
            if name is None and node.variant in self.nominal_types:
                node.source_name = node.variant
                node.variant = self.names[node.variant]
            else:
                node.enum_name = self.names.get(name, name)
        if isinstance(node, A.Block):
            local = set(values)
            for statement in node.statements:
                self.visit(statement, local, types)
                if isinstance(statement, A.LetDecl):
                    if getattr(statement, 'pattern', None) is not None:
                        local.update(pattern_names(statement.pattern))
                    else:
                        local.add(statement.name)
            node.tail_expr = self.visit(node.tail_expr, local, types)
            return node
        if isinstance(node, A.ForStmt):
            self.visit(node.pattern, values, types)
            node.iterable = self.visit(node.iterable, values, types)
            self.visit(node.body, values | pattern_names(node.pattern), types)
            return node
        if isinstance(node, A.MatchArm):
            self.visit(node.pattern, values, types)
            local = values | pattern_names(node.pattern)
            node.guard = self.visit(node.guard, local, types)
            self.visit(node.body, local, types)
            return node
        if isinstance(node, A.ElseErrExpr):
            # Error-handler bindings have their own lexical scope.
            node.operand = self.visit(node.operand, values, types)
            local = values | ({node.err_name} if node.err_name else set())
            node.handler = self.visit(node.handler, local, types)
            return node
        for field, child in list(vars(node).items()):
            if isinstance(child, (A.Node, list)):
                setattr(node, field, self.visit(child, values, types))
        return node
