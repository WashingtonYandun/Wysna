"""Qualify module declarations while preserving lexical value/type bindings."""
from node import NodeType as A

DECLARATIONS = (A.FnDecl, A.StructDecl, A.EnumDecl, A.InterfaceDecl, A.ClassDecl, A.LetDecl)
PREFIX = '__wysna_module_'


def pattern_names(pattern):
    if isinstance(pattern, A.IdentifierPattern):
        return {pattern.name} - {'None', 'none'}
    if isinstance(pattern, A.ErrorTypePattern):
        return pattern_names(pattern.binding)
    children = getattr(pattern, 'elements', getattr(pattern, 'args', []))
    return set().union(*(pattern_names(child) for child in children)) if children else set()


class ModuleNames:
    def __init__(self, program, module_id):
        prefix = PREFIX + str(module_id).encode('utf-8').hex() + '__'
        self.names = {node.name: prefix + node.name for node in program.body if isinstance(node, DECLARATIONS)}
        self.nominal_types = {node.name for node in program.body if isinstance(node, (A.StructDecl, A.EnumDecl))}
        self.variants = {variant.name: declaration.name for declaration in program.body
                         if isinstance(declaration, A.EnumDecl) for variant in declaration.variants}

    def qualify(self, program):
        for declaration in program.body:
            if isinstance(declaration, DECLARATIONS):
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
        if isinstance(node, A.NewExpr):
            node.class_name = self.names.get(node.class_name, node.class_name)
        if isinstance(node, A.EnumPattern):
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
