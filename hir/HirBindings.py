"""Verify lexical names and direct-call/return contracts in the current HIR."""
from hir import HirNodes as H
from checker.TypeInterner import TYPE_UNIT, TYPE_NONE
from checker.TypeRelations import TypeRelations


class HirBindings:
    def __init__(self, verifier):
        self.verifier = verifier
        self.interner = verifier.interner
        self.relations = TypeRelations(self.interner)
        self.functions = {}
        self.return_type = None
        self.classes = {}
        self.structs = {}
        self.enums = {}

    def compatible(self, source, target):
        return self.relations.is_assignable(source, target)

    def check(self, module):
        from codegen.VirtualDispatch import hir_virtual_method_slots
        self.virtual_slots = hir_virtual_method_slots(module, self.interner)
        self.classes = {class_.name: class_ for class_ in module.classes}
        self.structs = {struct.name: struct for struct in module.structs}
        self.enums = {enum.name: enum for enum in module.enums}
        self.functions = {function.name: function for function in module.functions}
        globals_ = {node.name: node.type_id for node in module.top_level_stmts if isinstance(node, H.HirLet)}
        for node in module.top_level_stmts:
            self.visit(node, globals_)
        for function in module.functions:
            self.function(function, globals_)
        for class_ in module.classes:
            for function in class_.methods:
                self.function(function, globals_)

    def function(self, function, globals_):
        if hasattr(function, "extern_types"):
            return
        previous = self.return_type
        self.return_type = function.return_type_id
        scope = dict(globals_)
        for parameter in function.params:
            scope[parameter.name] = parameter.type_id
            if hasattr(parameter, "symbol_id") and parameter.symbol_id:
                scope[parameter.symbol_id.id] = parameter.type_id
        try:
            self.visit(function.body, scope)
            from common.ControlFlow import exits, NORMAL
            if NORMAL in exits(function.body, self.match_is_exhaustive):
                tail_type = function.body.tail_expr.type_id if function.body.tail_expr is not None else TYPE_UNIT
                if not self.compatible(tail_type, function.return_type_id):
                    self.verifier._fail(function, 'Function has an invalid normal return path')
        finally:
            self.return_type = previous

    def match_is_exhaustive(self, node):
        from checker.PatternCoverage import pattern_matrix_exhaustive
        subject = self.interner.to_type(node.subject.type_id)
        rows = [[arm.pattern] for arm in node.arms if arm.guard is None]
        return pattern_matrix_exhaustive([subject], rows, self.interner)

    def always_returns(self, node):
        from common.ControlFlow import exits, NORMAL, RETURN
        outcomes = exits(node, self.match_is_exhaustive)
        return NORMAL not in outcomes and outcomes <= {RETURN}

    def bind(self, pattern, scope, type_id=None):
        if pattern is None:
            return
        type_id = getattr(pattern, "type_id", getattr(pattern, "inferred_type_id", type_id))
        if isinstance(pattern, H.HirBindingPattern) and pattern.name not in ('None', 'none'):
            scope[pattern.name] = type_id
            if hasattr(pattern, "symbol_id") and pattern.symbol_id:
                scope[pattern.symbol_id.id] = type_id
        elif isinstance(pattern, H.HirTuplePattern):
            entry = self.interner.get_entry(type_id) if type_id is not None else None
            types = entry.data if entry and entry.tag == 'tuple' else [None] * len(pattern.elements)
            for subpattern, subtype in zip(pattern.elements, types):
                self.bind(subpattern, scope, subtype)
        elif isinstance(pattern, H.HirErrorTypePattern):
            self.bind(pattern.binding, scope)
        elif isinstance(pattern, H.HirEnumPattern):
            for subpattern in pattern.subpatterns:
                self.bind(subpattern, scope)
        elif hasattr(pattern, "name") and pattern.name not in ('None', 'none'):
            scope[pattern.name] = type_id
        elif hasattr(pattern, "elements"):
            entry = self.interner.get_entry(type_id) if type_id is not None else None
            types = entry.data if entry and entry.tag == 'tuple' else [None] * len(pattern.elements)
            for subpattern, subtype in zip(pattern.elements, types):
                self.bind(subpattern, scope, subtype)
        elif hasattr(pattern, "args"):
            for subpattern in getattr(pattern, "args", []):
                self.bind(subpattern, scope)

    def class_method(self, name, method):
        visited = set()
        while name in self.classes and name not in visited:
            visited.add(name)
            declaration = self.classes[name]
            for function in declaration.methods:
                if function.name == method:
                    return function
            name = declaration.superclass
        return None

    def signature(self, node, params, returned):
        if len(node.args) != len(params) or node.type_id != returned:
            self.verifier._fail(node, 'Call arity/result does not match signature')
        for argument, parameter in zip(node.args, params):
            if not self.compatible(argument.type_id, parameter):
                self.verifier._fail(node, 'Call argument does not match signature')

    def visit(self, node, scope):
        if isinstance(node, H.HirBlock):
            scope = dict(scope)
            for statement in node.statements:
                self.visit(statement, scope)
            self.visit(node.tail_expr, scope)
            return
        if isinstance(node, H.HirSequence):
            inner = dict(scope)
            for statement in node.statements:
                self.visit(statement, inner)
            self.visit(node.value, inner)
            if not self.compatible(node.value.type_id, node.type_id):
                self.verifier._fail(node, 'Sequence result type does not match its value')
            return
        if isinstance(node, (H.HirLet, H.HirBindTemp)):
            self.visit(node.init_expr, scope)
            if not self.compatible(node.init_expr.type_id, node.type_id):
                self.verifier._fail(node, 'Initializer type does not match binding type')
            scope[getattr(node, 'name', getattr(node, 'temp_name', ''))] = node.type_id
            if hasattr(node, "symbol_id") and node.symbol_id:
                scope[node.symbol_id.id] = node.type_id
            return
        if isinstance(node, H.HirClosure):
            declaration = self.functions.get(node.name)
            signature = self.interner.get_entry(node.type_id)
            if (declaration is None or signature.tag != "function" or not declaration.params
                    or declaration.params[0].type_id != node.environment.type_id
                    or tuple(p.type_id for p in declaration.params[1:]) != signature.data[0]
                    or declaration.return_type_id != signature.data[1]):
                self.verifier._fail(node, "Closure environment/signature mismatch")
        if isinstance(node, H.HirFunctionRef):
            declaration = self.functions.get(node.name)
            expected = self.interner.mk_function(tuple(p.type_id for p in declaration.params), declaration.return_type_id) if declaration else None
            if node.type_id != expected:
                self.verifier._fail(node, "Unresolved HIR function reference")
        if isinstance(node, H.HirIndirectCall):
            signature = self.interner.get_entry(node.callee.type_id)
            if signature.tag != "function":
                self.verifier._fail(node, "Indirect callee must have function type")
            self.signature(node, signature.data[0], signature.data[1])
        if isinstance(node, H.HirLocalRef):
            resolved_by_sym = False
            if hasattr(node, "symbol_id") and node.symbol_id and node.symbol_id.id != 0:
                if node.symbol_id.id in scope:
                    resolved_by_sym = True
                    if scope[node.symbol_id.id] is not None and scope[node.symbol_id.id] != node.type_id:
                        self.verifier._fail(node, f'HIR local type does not match binding: {node.name}')
            if not resolved_by_sym:
                if node.name not in scope:
                    self.verifier._fail(node, f'Unresolved HIR local: {node.name}')
                if scope[node.name] is not None and scope[node.name] != node.type_id:
                    self.verifier._fail(node, f'HIR local type does not match binding: {node.name}')
        if isinstance(node, H.HirReturn) and self.return_type is not None:
            if not self.compatible(node.value_expr.type_id, self.return_type):
                self.verifier._fail(node, 'Returned value type does not match function return type')
        if isinstance(node, H.HirTryPropagate) and self.return_type is not None:
            returned = self.interner.get_entry(self.return_type)
            operand = self.interner.get_entry(node.operand.type_id)
            if returned.tag != operand.tag or (
                operand.tag == "result"
                and not self.relations.is_error_channel_assignable(operand.data[1], returned.data[1])
            ):
                self.verifier._fail(node, "Propagation channel does not match function return type")
        if isinstance(node, H.HirBuiltinCall):
            from hir.HirBuilder import BUILTIN_NAMES
            if node.builtin_name in ("weak", "upgrade"):
                if len(node.args) != 1:
                    self.verifier._fail(node, "Weak builtin arity mismatch")
                argument = self.interner.get_entry(node.args[0].type_id)
                expected_tag = "class" if node.builtin_name == "weak" else "weak"
                if argument.tag != expected_tag:
                    self.verifier._fail(node, "Weak builtin argument type mismatch")
                expected = (self.interner.mk_weak(node.args[0].type_id) if node.builtin_name == "weak"
                            else self.interner.mk_optional(argument.data))
                if node.type_id != expected:
                    self.verifier._fail(node, "Weak builtin result type mismatch")
            if node.builtin_name not in BUILTIN_NAMES:
                self.verifier._fail(node, 'Unresolved HIR builtin')
        if isinstance(node, H.HirConstructEnum):
            entry = self.interner.get_entry(node.type_id)
            if node.enum_name == 'Option' and node.variant_name in ('None', 'none'):
                if node.payload is not None or (entry.tag != 'optional' and node.type_id != TYPE_NONE):
                    self.verifier._fail(node, 'None constructor identity/payload mismatch')
            elif entry.tag in ('optional', 'result'):
                variants = {'Some': [entry.data]} if entry.tag == 'optional' else {'Ok': [entry.data[0]], 'Err': [entry.data[1]]}
                family = 'Option' if entry.tag == 'optional' else 'Result'
                expected = variants.get(node.variant_name)
                if node.enum_name != family or expected is None or node.payload is None:
                    self.verifier._fail(node, 'Builtin constructor identity/payload mismatch')
                if not self.compatible(node.payload.type_id, expected[0]):
                    self.verifier._fail(node, 'Builtin constructor payload type mismatch')
            else:
                declaration = self.enums.get(node.enum_name)
                if declaration is None or declaration.type_id != node.type_id or node.variant_name not in declaration.variants:
                    self.verifier._fail(node, 'Unresolved HIR enum variant')
                expected = declaration.payload_types.get(node.variant_name, [])
                if node.variant_idx != declaration.variants.index(node.variant_name):
                    self.verifier._fail(node, 'Enum variant index mismatch')
                if not expected:
                    if node.payload is not None:
                        self.verifier._fail(node, 'Unexpected enum payload')
                elif node.payload is None:
                    self.verifier._fail(node, 'Missing enum payload')
                elif len(expected) == 1:
                    if not self.compatible(node.payload.type_id, expected[0]):
                        self.verifier._fail(node, 'Enum payload type mismatch')
                elif not isinstance(node.payload, H.HirConstructTuple) or tuple(expected) != tuple(node.payload.elem_type_ids):
                    self.verifier._fail(node, 'Enum payload tuple does not match declaration')
        if isinstance(node, H.HirConstructObject):
            declaration = self.classes.get(node.class_name)
            if declaration is None or declaration.type_id != node.type_id:
                self.verifier._fail(node, 'Unresolved HIR class constructor')
            constructor = self.class_method(node.class_name, 'init')
            params = [parameter.type_id for parameter in constructor.params] if constructor else []
            self.signature(node, params, declaration.type_id)
        if isinstance(node, H.HirConstructStruct):
            declaration = self.structs.get(node.struct_name)
            if declaration is None or declaration.type_id != node.type_id:
                self.verifier._fail(node, 'Unresolved HIR struct constructor')
            expected = {field.name: field.type_id for field in declaration.fields}
            actual = [name for name, _ in node.fields]
            if len(set(actual)) != len(actual) or set(actual) != set(expected):
                self.verifier._fail(node, 'Struct constructor fields do not match declaration')
            for name, expression in node.fields:
                if not self.compatible(expression.type_id, expected[name]):
                    self.verifier._fail(node, 'Struct constructor field type mismatch')
        if isinstance(node, H.HirFieldRead):
            entry = self.interner.get_entry(node.object.type_id)
            declaration = self.classes.get(entry.data) if entry.tag == 'class' else self.structs.get(entry.data)
            fields = {}
            visited = set()
            while declaration is not None and declaration.name not in visited:
                visited.add(declaration.name)
                for field in declaration.fields:
                    fields.setdefault(field.name, field.type_id)
                declaration = self.classes.get(getattr(declaration, 'superclass', None))
            if not fields and entry.tag == 'struct':
                definition = self.interner.struct_defs.get(entry.data)
                fields = definition.fields if definition else {}
            if fields.get(node.field_name) != node.type_id:
                self.verifier._fail(node, 'Unresolved or mistyped HIR field')
        if isinstance(node, H.HirMethodCall):
            entry = self.interner.get_entry(node.receiver.type_id)
            if entry.tag in ('list', 'dict'):
                if (not isinstance(node.target, H.CollectionCallTarget)
                        or node.target.receiver_kind != entry.tag
                        or node.target.method_name != node.method_name):
                    self.verifier._fail(node, 'Collection intrinsic target does not match receiver')
            elif (not isinstance(node.target, H.VirtualCallTarget)
                    or node.target.method_name != node.method_name):
                self.verifier._fail(node, 'Virtual target does not match method receiver')
            if entry.tag in ('class', 'interface'):
                slots = self.virtual_slots
                key = (node.method_name, len(node.args))
                expected_class = entry.data if entry.tag == 'class' else None
                expected_slot = None if isinstance(node.receiver, H.HirSuperRef) else (slots.index(key) if key in slots else -1)
                if (node.target.slot != expected_slot
                        or node.slot != node.target.slot or node.target.target_class != expected_class):
                    self.verifier._fail(node, 'Virtual slot does not match resolved signature')
            if entry.tag == 'class':
                method = self.class_method(entry.data, node.method_name)
                if method is None:
                    self.verifier._fail(node, 'Unresolved HIR class method')
                self.signature(node, [parameter.type_id for parameter in method.params], method.return_type_id)
            elif entry.tag == 'interface':
                definition = self.interner.interface_defs.get(entry.data)
                type_id = definition.methods.get(node.method_name) if definition else None
                if type_id is None:
                    self.verifier._fail(node, 'Unresolved HIR interface method')
                signature = self.interner.get_entry(type_id)
                self.signature(node, list(signature.data[0]), signature.data[1])
            elif entry.tag == 'list':
                signatures = {'append': ([entry.data], TYPE_UNIT), 'pop': ([], entry.data), 'clear': ([], TYPE_UNIT)}
                if node.method_name not in signatures:
                    self.verifier._fail(node, 'Unresolved HIR list method')
                self.signature(node, *signatures[node.method_name])
            elif entry.tag == 'dict':
                key, value = entry.data
                signatures = {'get': ([key], self.interner.mk_optional(value)),
                              'keys': ([], self.interner.mk_list(key)), 'values': ([], self.interner.mk_list(value))}
                if node.method_name not in signatures:
                    self.verifier._fail(node, 'Unresolved HIR dictionary method')
                self.signature(node, *signatures[node.method_name])
            else:
                self.verifier._fail(node, 'HIR method receiver is not dispatchable')
        if isinstance(node, H.HirDirectCall):
            function = self.functions.get(node.fn_name)
            if function is None:
                self.verifier._fail(node, f'Unresolved HIR function: {node.fn_name}')
            if len(node.args) != len(function.params) or node.type_id != function.return_type_id:
                self.verifier._fail(node, 'Direct call arity/result does not match declaration')
            for argument, parameter in zip(node.args, function.params):
                if not self.compatible(argument.type_id, parameter.type_id):
                    self.verifier._fail(node, 'Direct call argument type does not match declaration')
        if isinstance(node, H.HirForList):
            self.visit(node.list_expr, scope)
            inner = dict(scope)
            self.bind(node.pattern, inner, node.elem_type_id)
            self.visit(node.body, inner)
            return
        if isinstance(node, H.HirMatch):
            self.visit(node.subject, scope)
            for arm in node.arms:
                if isinstance(arm.pattern, H.HirErrorTypePattern) and arm.pattern.type_id != node.subject.type_id:
                    self.verifier._fail(arm.pattern, 'Error type pattern subject does not match the match expression')
                if (isinstance(arm.pattern, H.HirEnumPattern)
                        and self.interner.get_entry(node.subject.type_id).tag == 'error_union'):
                    self.verifier._fail(arm.pattern, 'Error union cannot be matched as an enum variant')
                inner = dict(scope)
                self.bind(arm.pattern, inner)
                self.visit(arm.guard, inner)
                self.visit(arm.body, inner)
            return
        if isinstance(node, H.HirElseHandle):
            self.visit(node.operand, scope)
            inner = dict(scope)
            if node.err_var:
                entry = self.interner.get_entry(node.operand.type_id)
                inner[node.err_var] = entry.data[1] if entry.tag == 'result' else None
            self.visit(node.handler, inner)
            return
        if isinstance(node, H.HirAssignment) and node.type_id != node.target.type_id:
            self.verifier._fail(node, 'Assignment result type does not match destination')
        if isinstance(node, (H.HirAssign, H.HirAssignment)) and not self.compatible(node.value.type_id, node.target.type_id):
            self.verifier._fail(node, 'Assigned value type does not match target type')
        if isinstance(node, H.HirNode):
            for child in vars(node).values():
                self.visit(child, scope)
        elif isinstance(node, (list, tuple)):
            for child in node:
                self.visit(child, scope)
