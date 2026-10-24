"""Lower resolved HIR to explicit CFG, physical places and ARC operations."""
from checker.TypeInterner import TYPE_UNIT, TYPE_INT, TYPE_BOOL, TYPE_NONE, TYPE_STR, TYPE_FLOAT
from error.Error import WysnaError
from hir import HirNodes as H
from mir.Nodes import (Source, Slot, Projection, Place, Operation, Terminator,
                       BasicBlock, Function, Layout, Module)


class Lowering:
    def __init__(self, interner, file='<stdin>'):
        self.interner = interner
        self.file = file

    def source(self, node):
        span = getattr(node, 'span', None)
        return Source(getattr(span, 'file_path', None) or self.file,
                      getattr(span, 'line', 1), getattr(span, 'col', 1))

    def fail(self, node, message):
        source = self.source(node)
        raise WysnaError(message, file=source.file, line=source.line, col=source.col,
                     code='W9999', stage='mir-lowering')

    def lower(self, hir, optimize=True, *, call_entrypoint=None):
        from hir.HirVerifier import HirVerifier
        from codegen.VirtualDispatch import hir_virtual_method_slots
        HirVerifier(self.interner, self.file).verify(hir)
        self.module = Module(hir.file_path, self.interner.arena_id)
        self.module.virtual_slots = hir_virtual_method_slots(hir, self.interner)
        self.module.enum_count = len(hir.enums)
        self.classes = {declaration.name: declaration for declaration in hir.classes}
        self.global_bindings = {}
        for node in hir.top_level_stmts:
            if isinstance(node, H.HirLet):
                slot = Slot(-len(self.module.globals) - 1, node.type_id, 'v_' + node.name,
                            ownership='global')
                self.module.globals.append(slot)
                self.global_bindings[self.key(node)] = slot
        for declaration in hir.structs:
            self.module.layouts[declaration.type_id] = Layout(
                declaration.name, 'struct', declaration.type_id,
                [(field.name, field.type_id) for field in declaration.fields])
        for name, definition in self.interner.struct_defs.items():
            self.module.layouts.setdefault(definition.def_id, Layout(
                name, 'struct', definition.def_id, list(definition.fields.items())))
        for declaration in hir.classes:
            self.layout_class(declaration)
        for declaration in hir.functions:
            name = 'wysna_fn_' + declaration.name
            if hasattr(declaration, 'extern_types'):
                self.module.externs[name] = {'native': declaration.extern_name,
                                            'signature': declaration.extern_types,
                                            'params': [p.type_id for p in declaration.params],
                                            'return_type': declaration.return_type_id}
            else:
                self.lower_function(declaration, name)
        for declaration in hir.classes:
            for method in declaration.methods:
                self.lower_function(method, f'wysna_method_{declaration.name}__{method.name}', declaration)
            self.lower_constructor(declaration)
        self.begin('wysna_program', TYPE_UNIT, Source(self.file))
        last = hir.top_level_stmts[-1] if hir.top_level_stmts else None
        explicit = (isinstance(last, H.HirExprStmt) and isinstance(last.expr, H.HirDirectCall)
                    and last.expr.fn_name == 'main')
        entry_value = None
        for statement in hir.top_level_stmts:
            if explicit and statement is last:
                entry_value = self.expr(statement.expr)
            else:
                self.stmt(statement, top=True)
        invoke_entrypoint = (getattr(hir, 'call_entrypoint', not hir.top_level_stmts)
                             if call_entrypoint is None else call_entrypoint)
        if not explicit and invoke_entrypoint:
            entry = next((fn for fn in hir.functions if fn.name == 'main'), None)
            if entry:
                entry_value = self.call('wysna_fn_main', [], entry.return_type_id, entry)
        if self.current is not None:
            value = entry_value if entry_value is not None else self.constant(None, TYPE_UNIT)
            self.function.return_type = self.slot_by_id[value].type_id
            self.return_value(value)
        self.finish()
        if optimize:
            from mir.Ownership import optimize_parameter_reads
            return optimize_parameter_reads(self.module, self.interner)
        from mir.Verifier import Verifier
        Verifier(self.interner).verify(self.module)
        return self.module

    @staticmethod
    def key(node):
        symbol = getattr(node, 'symbol_id', None)
        return ('symbol', symbol.id) if symbol else ('name', getattr(node, 'name', getattr(node, 'temp_name', '')))

    def layout_class(self, declaration):
        if declaration.type_id in self.module.layouts:
            return self.module.layouts[declaration.type_id]
        fields = []
        methods = {}
        if declaration.superclass:
            parent = self.layout_class(self.classes[declaration.superclass])
            fields.extend(parent.fields)
            methods.update(parent.methods)
        for field in declaration.fields:
            if not any(name == field.name for name, _ in fields):
                fields.append((field.name, field.type_id))
        for method in declaration.methods:
            methods[method.name] = (declaration.name, [p.type_id for p in method.params], method.return_type_id)
        layout = Layout(declaration.name, 'class', declaration.type_id, fields, declaration.superclass, methods)
        self.module.layouts[declaration.type_id] = layout
        return layout

    def begin(self, name, returned, source):
        self.function = Function(name, returned, source=source)
        self.slot_by_id = {slot.id: slot for slot in self.module.globals}
        self.bindings = [dict(self.global_bindings), {}]
        self.scopes = [[]]
        self.loops = []
        self.constants = {}
        self.pattern_witness = {}
        self.current_class = None
        self.fallible = False
        self.err_type = ''
        self.current = self.new_block()

    def finish(self):
        for block in self.function.blocks:
            if block.terminator is None:
                block.terminator = Terminator('unreachable', source=self.function.source)
        self.module.functions.append(self.function)

    def new_block(self):
        block = BasicBlock(len(self.function.blocks))
        self.function.blocks.append(block)
        return block

    def slot(self, type_id, name='', storage='value', ownership='owned', parameter=False, tracked=True):
        if storage == 'value' and ownership != 'global':
            storage = {TYPE_INT: 'integer', TYPE_FLOAT: 'float', TYPE_BOOL: 'boolean'}.get(type_id, storage)
        slot = Slot(len(self.function.slots), type_id, name, storage, ownership, parameter)
        self.function.slots.append(slot)
        self.slot_by_id[slot.id] = slot
        if parameter:
            self.function.params.append(slot.id)
        elif storage == 'value' and ownership == 'owned' and tracked:
            self.scopes[-1].append(slot.id)
        return slot.id

    def op(self, opcode, result=None, args=(), data=None, moves=(), node=None):
        if self.current is not None:
            self.current.operations.append(Operation(opcode, result, tuple(args), data or {},
                                                     tuple(moves), self.source(node)))
        return result

    def terminate(self, kind, targets=(), value=None, node=None):
        if self.current is not None:
            self.current.terminator = Terminator(kind, tuple(block.id for block in targets), value, self.source(node))
            self.current = None

    def jump(self, block):
        self.terminate('goto', [block])

    def branch(self, condition, yes, no):
        constant = self.constants.get(condition)
        if constant is not None:
            self.jump(yes if constant else no)
        else:
            self.terminate('branch', [yes, no], condition)

    def has_predecessor(self, block):
        return any(item.terminator and block.id in item.terminator.targets for item in self.function.blocks)

    def push(self):
        self.scopes.append([])
        self.bindings.append({})

    def cleanup(self, start=0):
        for scope in reversed(self.scopes[start:]):
            for slot in reversed(scope):
                self.op('release', args=[slot])

    def pop(self):
        self.cleanup(len(self.scopes) - 1)
        self.scopes.pop()
        self.bindings.pop()

    def bind(self, node, slot):
        self.bindings[-1][self.key(node)] = self.slot_by_id[slot]
        # Synthetic handlers have no SymbolId in older checked HIR.
        name = getattr(node, 'name', getattr(node, 'temp_name', ''))
        self.bindings[-1][('name', name)] = self.slot_by_id[slot]

    def lookup(self, node):
        key = self.key(node)
        for scope in reversed(self.bindings):
            if key in scope:
                return scope[key].id
        for scope in reversed(self.bindings):
            if ('name', node.name) in scope:
                return scope[('name', node.name)].id
        self.fail(node, f'Unresolved MIR binding: {node.name}')

    def constant(self, value, type_id, node=None):
        result = self.slot(type_id)
        self.op('const', result, data={'value': value}, node=node)
        if type_id == TYPE_BOOL:
            self.constants[result] = value
        return result

    def runtime(self, name, args, type_id, node=None, moves=(), source=False):
        result = self.slot(type_id)
        return self.op('runtime', result, args, {'name': name, 'source': source}, moves, node)

    def call(self, name, args, type_id, node):
        result = self.slot(type_id)
        return self.op('call', result, args, {'name': name}, node=node)

    def store(self, destination, source, node=None):
        self.constants.pop(destination, None)
        self.op('store', destination, [source], moves=[0], node=node)

    def return_value(self, value, node=None):
        if self.fallible:
            value = self.runtime('wysna_wrap_result', [value], self.function.return_type, node,
                                 moves=[0])
            self.current.operations[-1].data['error_type'] = self.err_type
        escaped = self.slot(self.function.return_type, 'return', tracked=False)
        self.store(escaped, value, node)
        self.cleanup()
        self.terminate('return', value=escaped, node=node)

    def lower_function(self, declaration, name, owner=None):
        self.begin(name, declaration.return_type_id, self.source(declaration))
        self.current_class = owner
        self.fallible, self.err_type = declaration.is_fallible, declaration.err_type_name
        source_file = getattr(declaration.span, 'file_path', None)
        if (owner is None and declaration.is_public and source_file == self.file):
            self.module.exports.append({
                'name': declaration.name,
                'function': name,
                'params': tuple(parameter.type_id for parameter in declaration.params),
                'return_type': declaration.return_type_id,
                'fallible': declaration.is_fallible,
            })
        if owner:
            self.receiver = self.slot(owner.type_id, 'v_self', ownership='borrowed', parameter=True)
        for index, parameter in enumerate(declaration.params):
            slot = self.slot(parameter.type_id, 'v_arg' + str(index), ownership='borrowed', parameter=True)
            self.bind(parameter, slot)
        value = self.block(declaration.body)
        if self.current is not None:
            if declaration.return_type_id == TYPE_UNIT:
                value = self.constant(None, TYPE_UNIT, declaration)
            self.return_value(value, declaration)
        self.finish()

    def default(self, type_id):
        if self.interner.get_entry(type_id).tag == 'weak':
            return self.runtime('wysna_empty_weak', [], type_id)
        value = '' if type_id == TYPE_STR else False if type_id == TYPE_BOOL else 0 if type_id == TYPE_INT else 0.0 if self.interner.get_entry(type_id).data == 'float' else None
        result = self.constant(value, type_id)
        self.current.operations[-1].data['default'] = True
        return result

    def lower_constructor(self, declaration):
        layout = self.module.layouts[declaration.type_id]
        self.begin('wysna_new_' + declaration.name, declaration.type_id, self.source(declaration))
        init = layout.methods.get('init')
        arguments = []
        if init:
            for index, type_id in enumerate(init[1]):
                arguments.append(self.slot(type_id, 'v_arg' + str(index), ownership='borrowed', parameter=True))
        values = [self.default(type_id) for _, type_id in layout.fields]
        result = self.slot(declaration.type_id)
        self.op('construct', result, values, {'kind': 'object', 'layout': declaration.type_id}, node=declaration)
        self.op('set_vtable', args=[result], data={'class': declaration.name})
        if init:
            self.call('wysna_method_' + init[0] + '__init', [result] + arguments, init[2], declaration)
        self.return_value(result, declaration)
        self.finish()

    def block(self, block):
        type_id = block.tail_expr.type_id if block.tail_expr is not None else TYPE_UNIT
        result = self.slot(type_id)
        self.op('const', result, data={'value': None, 'placeholder': type_id != TYPE_UNIT})
        self.push()
        for statement in block.statements:
            self.stmt(statement)
        if self.current is not None and block.tail_expr is not None:
            value = self.expr(block.tail_expr)
            self.store(result, value)
        self.pop()
        return result

    def stmt(self, node, top=False):
        if self.current is None:
            return
        if isinstance(node, H.HirExprStmt):
            self.expr(node.expr)
        elif isinstance(node, (H.HirLet, H.HirBindTemp)):
            value = self.expr(node.init_expr)
            if top:
                slot = self.global_bindings[self.key(node)].id
            else:
                slot = self.slot(node.type_id, getattr(node, 'name', getattr(node, 'temp_name', 'local')))
                self.bind(node, slot)
            self.store(slot, value, node)
        elif isinstance(node, H.HirAssign):
            place = self.place(node.target)
            value = self.expr(node.value)
            self.op('store_place', args=[value], data={'place': place}, moves=[0], node=node)
        elif isinstance(node, H.HirReturn):
            self.return_value(self.expr(node.value_expr), node)
        elif isinstance(node, (H.HirBreak, H.HirContinue)):
            exit_block, continue_block, depth = self.loops[-1]
            self.cleanup(depth)
            self.jump(exit_block if isinstance(node, H.HirBreak) else continue_block)
        elif isinstance(node, (H.HirWhile, H.HirForList)):
            self.loop(node)
        else:
            self.fail(node, 'Unsupported MIR statement: ' + type(node).__name__)

    def loop(self, node):
        self.push()
        header, body, latch, after = [self.new_block() for _ in range(4)]
        is_for = isinstance(node, H.HirForList)
        direct_range = is_for and isinstance(node.list_expr, H.HirRange)
        if is_for:
            counter = self.slot(TYPE_INT, storage='counter')
            self.op('counter', counter)
            if direct_range:
                first, last = self.expr(node.list_expr.start), self.expr(node.list_expr.end)
                count = self.slot(TYPE_INT, storage='counter')
                self.op('range_count', count, [first, last], {'inclusive': node.list_expr.inclusive}, node=node)
                iterable = first
            else:
                iterable = self.expr(node.list_expr)
                count = None
        self.jump(header)
        self.current = header
        self.push()
        if is_for:
            condition = self.slot(TYPE_BOOL, storage='predicate')
            self.op('iterate_test', condition, [counter, count if direct_range else iterable], {'range': direct_range})
        else:
            condition = self.expr(node.cond_expr)
        # Condition temporaries belong to this iteration, not to the whole loop.
        if self.slot_by_id[condition].storage == 'value':
            predicate = self.slot(TYPE_BOOL, storage='predicate')
            self.op('test', predicate, [condition], {'kind': 'truth'})
        else:
            predicate = condition
        if condition in self.constants:
            self.constants[predicate] = self.constants[condition]
        self.pop()
        self.branch(predicate, body, after)
        self.current = body
        self.push()
        depth = len(self.scopes) - 1
        self.loops.append((after, latch if is_for else header, depth))
        if is_for:
            element_type = TYPE_INT if direct_range else self.interner.get_entry(node.list_expr.type_id).data
            item = self.slot(element_type)
            self.op('iterate_value', item, [iterable, counter], {'range': direct_range})
            self.bind_pattern(node.pattern, item)
        self.block(node.body)
        self.loops.pop()
        self.pop()
        if self.current is not None:
            self.jump(latch if is_for else header)
        if is_for:
            self.current = latch
            self.op('increment', counter, [counter])
            self.jump(header)
        self.current = after if self.has_predecessor(after) else None
        self.pop()

    def field_index(self, type_id, name, node):
        layout = self.module.layouts.get(type_id)
        if layout is None:
            self.fail(node, 'Missing physical MIR layout')
        for index, (field_name, field_type) in enumerate(layout.fields):
            if field_name == name:
                return index, field_type
        self.fail(node, 'Unresolved physical MIR field')

    def place(self, node):
        if isinstance(node, H.HirLocalRef):
            return Place(self.lookup(node))
        if isinstance(node, H.HirThisRef):
            return Place(self.receiver)
        if isinstance(node, H.HirIndexRead):
            parent = self.place(node.object)
            index = self.expr(node.index)
            return Place(parent.root, parent.projections + (Projection('index', index, node.object.type_id, node.type_id, self.source(node)),))
        if isinstance(node, H.HirFieldRead):
            entry = self.interner.get_entry(node.object.type_id)
            parent = Place(self.expr(node.object)) if entry.tag == 'class' else self.place(node.object)
            index, field_type = self.field_index(node.object.type_id, node.field_name, node)
            return Place(parent.root, parent.projections + (Projection('field', index, node.object.type_id, field_type),))
        self.fail(node, 'MIR destination is not addressable')

    def expr(self, node):
        if self.current is None:
            return self.slot(getattr(node, 'type_id', TYPE_UNIT))
        type_id = getattr(node, 'type_id', TYPE_UNIT)
        if isinstance(node, (H.HirConstInt, H.HirConstFloat, H.HirConstBool, H.HirConstStr)):
            return self.constant(node.value, type_id, node)
        if isinstance(node, (H.HirConstNone, H.HirConstUnit)):
            return self.constant(None, type_id, node)
        if isinstance(node, (H.HirLocalRef, H.HirThisRef, H.HirSuperRef)):
            source = self.lookup(node) if isinstance(node, H.HirLocalRef) else self.receiver
            return self.op('clone', self.slot(type_id), [source], node=node)
        if isinstance(node, H.HirSequence):
            for statement in node.statements:
                self.stmt(statement)
            return self.expr(node.value)
        if isinstance(node, H.HirAssignment):
            place = self.place(node.target)
            value = self.expr(node.value)
            result = self.op('clone', self.slot(type_id), [value], node=node)
            self.op('store_place', args=[value], data={'place': place}, moves=[0], node=node)
            return result
        if isinstance(node, H.HirBlock):
            return self.block(node)
        if isinstance(node, H.HirUnaryOp):
            return self.runtime('wysna_neg' if node.op == '-' else 'wysna_not', [self.expr(node.operand)], type_id, node)
        if isinstance(node, H.HirBinaryOp):
            if node.op in ('and', 'or'):
                return self.logical(node)
            left, right = self.expr(node.left), self.expr(node.right)
            names = {'+': 'wysna_add', '-': 'wysna_sub', '*': 'wysna_mul', '/': 'wysna_div_at', '%': 'wysna_mod_at',
                     '<': 'wysna_lt', '<=': 'wysna_le', '>': 'wysna_gt', '>=': 'wysna_ge', '==': 'wysna_equal', '!=': 'wysna_not_equal'}
            return self.runtime(names[node.op], [left, right], type_id, node, source=node.op in ('/', '%'))
        if isinstance(node, H.HirCoerce):
            value = self.expr(node.expr)
            if node.kind == 'wrap_ok':
                return self.runtime('wysna_ok', [value], type_id, node, moves=[0])
            if node.kind == 'int_to_float':
                return self.runtime('wysna_builtin_float', [value], type_id, node)
            # Identity/upcast retain physical representation while changing static type.
            result = self.slot(type_id)
            self.store(result, value, node)
            return result
        if isinstance(node, H.HirConstructEnum):
            name, variant = node.enum_name, node.variant_name
            builtin = name.split('__', 1)[0]
            if builtin == 'Option' and variant in ('None', 'none'):
                return self.constant(None, type_id, node)
            if node.payload is not None:
                value = self.expr(node.payload)
                helper = 'wysna_ok' if builtin == 'Result' and variant == 'Ok' else 'wysna_err' if builtin == 'Result' and variant == 'Err' else 'wysna_enum_payload'
                result = self.runtime(helper, [value], type_id, node, moves=[0])
                self.current.operations[-1].data.update(enum=name, variant=variant)
                return result
            result = self.runtime('wysna_enum', [], type_id, node)
            self.current.operations[-1].data.update(enum=name, variant=variant)
            return result
        if isinstance(node, (H.HirConstructList, H.HirConstructTuple, H.HirConstructDict, H.HirConstructStruct)):
            data = {}
            if isinstance(node, H.HirConstructDict):
                values = [self.expr(value) for pair in node.entries for value in pair]
                data['kind'] = 'dict'
            elif isinstance(node, H.HirConstructStruct):
                evaluated = {name: self.expr(value) for name, value in node.fields}
                layout = self.module.layouts[type_id]
                values = [evaluated[name] for name, _ in layout.fields]
                data.update(kind='struct', layout=type_id)
            else:
                values = [self.expr(value) for value in node.elems]
                data['kind'] = 'list' if isinstance(node, H.HirConstructList) else 'tuple'
            return self.op('construct', self.slot(type_id), values, data, node=node)
        if isinstance(node, H.HirConstructObject):
            return self.call('wysna_new_' + node.class_name, [self.expr(arg) for arg in node.args], type_id, node)
        if isinstance(node, H.HirRange):
            result = self.runtime('wysna_make_range', [self.expr(node.start), self.expr(node.end)], type_id, node)
            self.current.operations[-1].data['inclusive'] = node.inclusive
            return result
        if isinstance(node, H.HirFieldRead):
            value = self.expr(node.object)
            index, _ = self.field_index(node.object.type_id, node.field_name, node)
            return self.op('load_field', self.slot(type_id), [value], {'index': index, 'layout': node.object.type_id}, node=node)
        if isinstance(node, H.HirIndexRead):
            return self.runtime('wysna_index_get_owned_at', [self.expr(node.object), self.expr(node.index)], type_id, node, source=True)
        if isinstance(node, H.HirDirectCall):
            return self.call('wysna_fn_' + node.target.fn_name, [self.expr(arg) for arg in node.args], type_id, node)
        if isinstance(node, H.HirBuiltinCall):
            values = [self.expr(arg) for arg in node.args]
            name = 'wysna_builtin_weak_owned' if node.builtin_name == 'weak' else 'wysna_builtin_' + node.builtin_name
            return self.runtime(name, values, type_id, node, moves=[0] if node.builtin_name == 'weak' else [])
        if isinstance(node, H.HirMethodCall):
            if isinstance(node.target, H.CollectionCallTarget):
                method = node.target.method_name
                if method in ('append', 'pop', 'clear'):
                    place = self.place(node.receiver)
                    values = [self.expr(arg) for arg in node.args]
                    return self.op('mutate', self.slot(type_id), values, {'place': place, 'method': method}, node=node)
                values = [self.expr(node.receiver)] + [self.expr(arg) for arg in node.args]
                return self.runtime('wysna_dict_' + method, values, type_id, node)
            if isinstance(node.receiver, H.HirSuperRef):
                parent = self.module.layouts[self.classes[self.current_class.superclass].type_id]
                owner = parent.methods[node.method_name][0]
                values = [self.expr(node.receiver)] + [self.expr(arg) for arg in node.args]
                return self.call(f'wysna_method_{owner}__{node.method_name}', values, type_id, node)
            values = [self.expr(node.receiver)] + [self.expr(arg) for arg in node.args]
            return self.op('virtual', self.slot(type_id), values, {'slot': node.target.slot}, node=node)
        if isinstance(node, (H.HirFunctionRef, H.HirClosure)):
            args = [self.expr(node.environment)] if isinstance(node, H.HirClosure) else []
            arity = len(self.interner.get_entry(type_id).data[0])
            return self.op('function', self.slot(type_id), args,
                           {'name': 'wysna_fn_' + node.name, 'arity': arity, 'captured': bool(args)},
                           moves=[0] if args else [], node=node)
        if isinstance(node, H.HirIndirectCall):
            values = [self.expr(node.callee)] + [self.expr(arg) for arg in node.args]
            return self.op('indirect', self.slot(type_id), values, node=node)
        if isinstance(node, H.HirIf):
            return self.if_expr(node)
        if isinstance(node, H.HirMatch):
            return self.match(node)
        if isinstance(node, (H.HirTryPropagate, H.HirCoalesce, H.HirElseHandle)):
            return self.channel(node)
        self.fail(node, 'Unsupported MIR expression: ' + type(node).__name__)

    def predicate(self, kind, args, node=None, **data):
        result = self.slot(TYPE_BOOL, storage='predicate')
        return self.op('test', result, args, {'kind': kind, **data}, node=node)

    def logical(self, node):
        left = self.expr(node.left)
        result = self.constant(node.op == 'or', TYPE_BOOL, node)
        evaluate, done = self.new_block(), self.new_block()
        self.branch(left, evaluate if node.op == 'and' else done,
                    done if node.op == 'and' else evaluate)
        self.current = evaluate
        self.push()
        right = self.expr(node.right)
        self.store(result, right)
        self.pop()
        if self.current is not None:
            self.jump(done)
        self.current = done
        return result

    def store_result(self, destination, value):
        if self.slot_by_id[destination].type_id != TYPE_UNIT:
            self.store(destination, value)

    def if_expr(self, node):
        result = self.slot(node.type_id)
        self.op('const', result, data={'value': None, 'placeholder': node.type_id != TYPE_UNIT})
        done = self.new_block()
        for condition, block in [(node.cond, node.then_block)] + node.elifs:
            yes, no = self.new_block(), self.new_block()
            value = self.expr(condition)
            self.branch(value, yes, no)
            self.current = yes
            self.push()
            value = self.block(block)
            if self.current is not None:
                self.store_result(result, value)
            self.pop()
            if self.current is not None:
                self.jump(done)
            self.current = no if self.has_predecessor(no) else None
            if self.current is None:
                break
        if self.current is not None:
            if node.else_block:
                self.push()
                value = self.block(node.else_block)
                if self.current is not None:
                    self.store_result(result, value)
                self.pop()
            if self.current is not None:
                self.jump(done)
        self.current = done if self.has_predecessor(done) else None
        return result

    def channel(self, node):
        operand = node.opt_expr if isinstance(node, H.HirCoalesce) else node.operand
        value = self.expr(operand)
        failed, succeeded, done = self.new_block(), self.new_block(), self.new_block()
        kind = 'none' if isinstance(node, H.HirCoalesce) or isinstance(node, H.HirTryPropagate) and node.kind == 'optional' else 'err' if isinstance(node, H.HirTryPropagate) else 'failed'
        condition = self.predicate(kind, [value], node)
        result = self.slot(node.type_id)
        self.branch(condition, failed, succeeded)
        self.current = failed
        self.push()
        if isinstance(node, H.HirTryPropagate):
            if node.kind == 'optional':
                returned = self.constant(None, self.function.return_type)
            else:
                error_type = self.interner.get_entry(operand.type_id).data[1]
                error = self.runtime('wysna_unwrap_err', [value], error_type, node)
                returned = self.runtime('wysna_err', [error], self.function.return_type, node, moves=[0])
            self.return_value(returned, node)
        else:
            if isinstance(node, H.HirElseHandle) and node.err_var:
                entry = self.interner.get_entry(operand.type_id)
                error_type = entry.data[1] if entry.tag == 'result' else TYPE_NONE
                binder = self.runtime('wysna_unwrap_err', [value], error_type, node)
                self.bindings[-1][('name', node.err_var)] = self.slot_by_id[binder]
                symbol = getattr(node, 'symbol_id', None)
                if symbol:
                    self.bindings[-1][('symbol', symbol.id)] = self.slot_by_id[binder]
            fallback = self.expr(node.fallback_expr) if isinstance(node, H.HirCoalesce) else self.block(node.handler)
            if self.current is not None:
                self.store(result, fallback)
        self.pop()
        if self.current is not None:
            self.jump(done)
        self.current = succeeded
        self.push()
        helper = 'wysna_unwrap_optional' if isinstance(node, H.HirCoalesce) or isinstance(node, H.HirTryPropagate) and node.kind == 'optional' else 'wysna_unwrap_ok' if isinstance(node, H.HirTryPropagate) else 'wysna_unwrap_success'
        unwrapped = self.runtime(helper, [value], node.type_id, node)
        self.store(result, unwrapped)
        self.pop()
        self.jump(done)
        self.current = done
        return result

    def pattern_children(self, pattern, value):
        if isinstance(pattern, H.HirTuplePattern):
            return [('tuple', index, child) for index, child in enumerate(pattern.elements)]
        if isinstance(pattern, H.HirEnumPattern):
            builtin = (pattern.enum_name or '').split('__', 1)[0]
            channel = 'optional' if builtin == 'Option' else 'ok' if builtin == 'Result' and pattern.variant_name == 'Ok' else 'err' if builtin == 'Result' else 'enum'
            if len(pattern.subpatterns) == 1:
                return [(channel, None, pattern.subpatterns[0])]
            return [('enum_tuple', index, child) for index, child in enumerate(pattern.subpatterns)]
        if isinstance(pattern, H.HirErrorTypePattern):
            return [('identity', None, pattern.binding)]
        return []

    def payload(self, channel, index, child, value, pattern):
        if channel == 'identity':
            return self.op('clone', self.slot(child.type_id), [value])
        if channel in ('tuple', 'enum_tuple'):
            if channel == 'enum_tuple':
                value = self.runtime('wysna_enum_payload_owned', [value], self.interner.mk_tuple(
                    tuple(part.type_id for _, _, part in self.pattern_children_cache)), child)
                self.current.operations[-1].data['variant'] = pattern.variant_name
            return self.op('tuple_get', self.slot(child.type_id), [value], {'index': index})
        helper = {'optional': 'wysna_unwrap_optional', 'ok': 'wysna_unwrap_ok', 'err': 'wysna_unwrap_err', 'enum': 'wysna_enum_payload_owned'}[channel]
        result = self.runtime(helper, [value], child.type_id, child)
        if channel == 'enum':
            self.current.operations[-1].data['variant'] = pattern.variant_name
        return result

    def pattern_condition(self, pattern, value):
        if isinstance(pattern, H.HirWildcardPattern) or isinstance(pattern, H.HirBindingPattern) and pattern.name not in ('None', 'none'):
            result = self.slot(TYPE_BOOL, storage='predicate')
            self.op('const', result, data={'value': True})
            self.constants[result] = True
            return result
        if isinstance(pattern, H.HirLiteralPattern):
            literal = self.expr(pattern.expr)
            return self.predicate('equal', [value, literal], pattern)
        if isinstance(pattern, H.HirErrorTypePattern):
            entry = self.interner.get_entry(pattern.error_type_id)
            kind = entry.data if entry.tag == 'primitive' else entry.tag
            name = entry.data if entry.tag in ('struct', 'enum') else kind
            condition = self.predicate('error_type', [value], pattern, type_kind=kind, name=name)
            self.pattern_witness[(id(pattern), value)] = condition
            return condition
        if isinstance(pattern, H.HirBindingPattern):
            return self.predicate('none', [value], pattern)
        if isinstance(pattern, H.HirTuplePattern):
            base = self.predicate('tuple', [value], pattern, length=len(pattern.elements))
        elif isinstance(pattern, H.HirEnumPattern):
            base = self.predicate('variant', [value], pattern, enum=pattern.enum_name,
                                  variant=pattern.variant_name)
        else:
            self.fail(pattern, 'Unsupported MIR pattern')
        children = self.pattern_children(pattern, value)
        if not children:
            return base
        result = self.slot(TYPE_BOOL, storage='predicate')
        self.op('const', result, data={'value': False})
        done = self.new_block()
        self.push()
        for index, (channel, payload_index, child) in enumerate(children):
            yes = self.new_block()
            self.branch(base, yes, done)
            self.current = yes
            self.pattern_children_cache = children
            payload = self.payload(channel, payload_index, child, value, pattern)
            base = self.pattern_condition(child, payload)
        self.op('store', result, [base], data={'scalar': True})
        self.jump(done)
        self.current = done
        self.pop()
        return result

    def bind_pattern(self, pattern, value):
        if isinstance(pattern, H.HirErrorTypePattern):
            witness = self.pattern_witness[(id(pattern), value)]
            narrowed = self.op('narrow', self.slot(pattern.error_type_id), [value],
                               {'witness': witness}, node=pattern)
            self.bind_pattern(pattern.binding, narrowed)
            return
        if isinstance(pattern, H.HirBindingPattern):
            if pattern.name not in ('None', 'none'):
                slot = self.op('clone', self.slot(pattern.type_id, pattern.name), [value], node=pattern)
                self.bind(pattern, slot)
            return
        children = self.pattern_children(pattern, value)
        for channel, index, child in children:
            if channel in ('ok', 'err'):
                # A compound pattern predicate may hide the original tag witness.
                # Re-establish a direct guard before binding an owned payload.
                witness = self.predicate('err', [value], pattern)
                accepted, impossible = self.new_block(), self.new_block()
                self.branch(witness, accepted if channel == 'err' else impossible,
                            impossible if channel == 'err' else accepted)
                self.current = impossible
                self.terminate('unreachable', node=pattern)
                self.current = accepted
            self.pattern_children_cache = children
            payload = self.payload(channel, index, child, value, pattern)
            self.bind_pattern(child, payload)

    def match(self, node):
        from checker.PatternCoverage import pattern_matrix_exhaustive
        result = self.slot(node.type_id)
        self.op('const', result, data={'value': None, 'placeholder': node.type_id != TYPE_UNIT})
        subject = self.expr(node.subject)
        done = self.new_block()
        for arm in node.arms:
            yes, no = self.new_block(), self.new_block()
            condition = self.pattern_condition(arm.pattern, subject)
            self.branch(condition, yes, no)
            self.current = yes
            self.push()
            self.bind_pattern(arm.pattern, subject)
            if arm.guard is not None:
                accepted, rejected = self.new_block(), self.new_block()
                self.branch(self.expr(arm.guard), accepted, rejected)
                self.current = rejected
                self.cleanup(len(self.scopes) - 1)
                self.jump(no)
                self.current = accepted
            value = self.block(arm.body)
            if self.current is not None:
                self.store_result(result, value)
            self.pop()
            if self.current is not None:
                self.jump(done)
            self.current = no if self.has_predecessor(no) else None
            if self.current is None:
                break
        if self.current is not None:
            exhaustive = pattern_matrix_exhaustive([self.interner.to_type(node.subject.type_id)],
                                                   [[arm.pattern] for arm in node.arms if arm.guard is None], self.interner)
            if exhaustive:
                self.terminate('unreachable', node=node)
            else:
                self.jump(done)
        self.current = done if self.has_predecessor(done) else None
        return result
