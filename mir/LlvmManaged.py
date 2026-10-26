"""Managed MIR operations and runtime callbacks for the direct LLVM backend."""
from checker.TypeInterner import TYPE_INT, TYPE_FLOAT, TYPE_BOOL, TYPE_UNIT
from mir.LlvmAbi import OPERATION_CODES, SCALAR_TYPES, is_managed
from error.Error import WysnaError

DECLARATIONS = '''declare void @wysna_llvm_none(ptr)
declare void @wysna_llvm_construct(ptr, i32, ptr, i32, ptr, ptr)
declare void @wysna_llvm_field(ptr, ptr, i32)
declare ptr @wysna_llvm_field_ref(ptr, i32)
declare ptr @wysna_llvm_index_ref(ptr, ptr, ptr, i32, i32)
declare void @wysna_llvm_tuple_get(ptr, ptr, i32, ptr, i32, i32)
declare void @wysna_llvm_mutate(ptr, ptr, ptr, i32, ptr, i32, i32)
declare i32 @wysna_llvm_test(ptr, i32, ptr, ptr, i32)
declare i32 @wysna_llvm_iteration_length(ptr, ptr, i32, i32)
declare void @wysna_llvm_iteration_get(ptr, ptr, i32, ptr, i32, i32)
declare void @wysna_llvm_function(ptr, ptr, ptr)
declare void @wysna_llvm_invoke(ptr, ptr, i32, ptr)
declare void @wysna_llvm_virtual(ptr, ptr, i32, i32, ptr)
declare void @wysna_llvm_set_vtable(ptr, ptr)
declare i32 @wysna_llvm_entry_status(ptr)
''' + '\n'.join('declare void @wysna_llvm_rt_' + name + '(ptr, i32, ptr, ptr, ptr, i32, ptr, i32, i32)' for name in sorted(OPERATION_CODES)) + '\n'


class ManagedEmission:
    def __init__(self, generator):
        self.gen = generator

    def location(self, source):
        return 'ptr ' + self.gen.string(source.file) + ', i32 ' + str(source.line) + ', i32 ' + str(source.col)

    def arguments(self, args, moves=()):
        gen = self.gen
        for offset, index in enumerate(args):
            gen.boxed(index, offset, copy=True)
            if offset in moves and gen.slots[index].storage == 'value':
                gen.emit('store %WysnaValue zeroinitializer, ptr ' + gen.pointer(index))
        return gen.cell(0) if args else 'null'

    def output(self, index):
        gen = self.gen
        if gen.slots[index].storage == 'value':
            return gen.pointer(index)
        pointer = gen.cell(gen.scratch - 1)
        gen.emit('store %WysnaValue zeroinitializer, ptr ' + pointer)
        return pointer

    def complete(self, index, pointer):
        gen = self.gen
        if gen.slots[index].storage != 'value':
            type_id = gen.slots[index].type_id
            name = {TYPE_INT: 'int', TYPE_FLOAT: 'float', TYPE_BOOL: 'bool'}[type_id]
            type_ = 'i32' if type_id == TYPE_BOOL else SCALAR_TYPES[type_id]
            value = gen.result('call ' + type_ + ' @wysna_llvm_' + name + '(ptr ' + pointer + ')')
            if type_id == TYPE_BOOL:
                value = gen.result('icmp ne i32 ' + value + ', 0')
            gen.write(index, value)

    def place(self, place):
        gen = self.gen
        pointer = gen.pointer(place.root)
        for projection in place.projections:
            if projection.kind == 'field':
                pointer = gen.result('call ptr @wysna_llvm_field_ref(ptr ' + pointer + ', i32 ' + str(projection.index) + ')')
            else:
                index = gen.boxed(projection.index)
                pointer = gen.result('call ptr @wysna_llvm_index_ref(ptr ' + pointer + ', ptr ' + index + ', ' + self.location(projection.source) + ')')
        return pointer

    def runtime(self, operation):
        gen = self.gen
        arguments = self.arguments(operation.args, operation.moves)
        output = self.output(operation.result)
        data = operation.data
        first = data.get('enum', data.get('error_type', ''))
        second = data.get('variant', '')
        gen.emit('call void @wysna_llvm_rt_' + data['name'] + '(ptr ' + output + ', i32 ' + str(len(operation.args)) + ', ptr ' + arguments + ', ptr ' + gen.string(first) +
                 ', ptr ' + gen.string(second) + ', i32 ' + str(int(data.get('inclusive', False))) + ', ' + self.location(operation.source) + ')')
        self.complete(operation.result, output)

    def operation(self, operation):
        gen = self.gen
        kind, data, args, result = operation.opcode, operation.data, operation.args, operation.result
        if kind == 'construct':
            category = data['kind']
            ordered = tuple(args[::2]) + tuple(args[1::2]) if category == 'dict' else args
            values = self.arguments(ordered)
            name, keys = 'null', 'null'
            if category in ('struct', 'object'):
                layout = gen.module.layouts[data['layout']]
                name = gen.string(layout.name)
                if layout.fields:
                    keys = '@fields' + str(layout.type_id)
                    if keys not in gen.metadata:
                        gen.metadata.add(keys)
                        fields = ['ptr ' + gen.string(field) for field, _ in layout.fields]
                        gen.pool.append(keys + ' = private constant [' + str(len(fields)) + ' x ptr] [' + ', '.join(fields) + '], align 8')
            gen.emit('call void @wysna_llvm_construct(ptr ' + gen.pointer(result) + ', i32 ' +
                     str(('list', 'tuple', 'dict', 'struct', 'object').index(category)) + ', ptr ' + name +
                     ', i32 ' + str(len(args)) + ', ptr ' + keys + ', ptr ' + values + ')')
        elif kind in ('load_field', 'tuple_get'):
            value = gen.boxed(args[0])
            output = self.output(result)
            suffix = ', ' + self.location(operation.source) if kind == 'tuple_get' else ''
            helper = 'tuple_get' if kind == 'tuple_get' else 'field'
            gen.emit('call void @wysna_llvm_' + helper + '(ptr ' + output + ', ptr ' + value + ', i32 ' + str(data['index']) + suffix + ')')
            self.complete(result, output)
        elif kind == 'mutate':
            pointer = self.place(data['place'])
            value = gen.boxed(args[0]) if args else 'null'
            output = self.output(result)
            gen.emit('call void @wysna_llvm_mutate(ptr ' + output + ', ptr ' + pointer + ', ptr ' + value + ', i32 ' +
                     str(('append', 'pop', 'clear').index(data['method'])) + ', ' + self.location(operation.source) + ')')
            self.complete(result, output)
        elif kind == 'function':
            environment = gen.boxed(args[0]) if args else 'null'
            gen.emit('call void @wysna_llvm_function(ptr ' + gen.pointer(result) + ', ptr @adapter_' + data['name'] + ', ptr ' + environment + ')')
        elif kind in ('indirect', 'virtual'):
            values = self.arguments(args[1:])
            receiver = gen.boxed(args[0], len(args) - 1)
            output = self.output(result)
            extra = ', i32 ' + str(data['slot']) if kind == 'virtual' else ''
            helper = 'virtual' if kind == 'virtual' else 'invoke'
            gen.emit('call void @wysna_llvm_' + helper + '(ptr ' + output + ', ptr ' + receiver + extra + ', i32 ' + str(len(args) - 1) + ', ptr ' + values + ')')
            self.complete(result, output)
        elif kind == 'set_vtable':
            gen.emit('call void @wysna_llvm_set_vtable(ptr ' + gen.boxed(args[0]) + ', ptr @vtable_' + data['class'] + ')')
        elif kind == 'iterate_value':
            output = self.output(result)
            gen.emit('call void @wysna_llvm_iteration_get(ptr ' + output + ', ptr ' + gen.boxed(args[0]) + ', i32 ' + gen.load(args[1]) + ', ' + self.location(operation.source) + ')')
            self.complete(result, output)
        elif kind == 'test':
            test = data['kind']
            first, second = data.get('type_kind', ''), data.get('name', '')
            if test == 'variant':
                first, second = data['enum'] or '', data['variant']
                builtin = first.split('__', 1)[0]
                if builtin == 'Option':
                    test = 'none' if second in ('None', 'none') else 'present'
                elif builtin == 'Result':
                    test = 'ok' if second == 'Ok' else 'err'
            kinds = ('none', 'err', 'failed', 'tuple', 'error_type', 'variant', 'present', 'ok')
            value = gen.result('call i32 @wysna_llvm_test(ptr ' + gen.boxed(args[0]) + ', i32 ' + str(kinds.index(test)) +
                               ', ptr ' + gen.string(first) + ', ptr ' + gen.string(second) + ', i32 ' + str(data.get('length', 0)) + ')')
            gen.write(result, gen.result('icmp ne i32 ' + value + ', 0'))
        else:
            raise WysnaError('Verified LLVM MIR operation has no emitter: ' + kind, code='W9999',
                             stage='llvm-emission', file=operation.source.file,
                             line=operation.source.line, col=operation.source.col)

    def adapter(self, name, function, arity, captured):
        gen = self.gen
        environment = 'ptr byval(%WysnaValue) align 8' if gen.triple.startswith('x86_64-') else 'ptr'
        lines = ['define internal void @adapter_' + name + '(ptr sret(%WysnaValue) align 8 %out, ' + environment +
                 ' %environment, i32 %count, ptr %arguments) {', 'entry:',
                 '  %arity = icmp eq i32 %count, ' + str(arity), '  br i1 %arity, label %invoke, label %invalid',
                 'invalid:', '  call void @wysna_llvm_fail(ptr ' + gen.string('W4003') + ', ptr ' +
                 gen.string('Call arity mismatch') + ', ptr ' + gen.string('') + ', i32 0, i32 0)', '  unreachable', 'invoke:',
                 '  store %WysnaValue zeroinitializer, ptr %out']
        parameters = []
        for position, index in enumerate(function.params):
            type_id = function.slots[index].type_id
            pointer = '%environment'
            if not captured or position != 0:
                ordinal = position - int(captured)
                pointer = '%argument' + str(position)
                lines.append('  ' + pointer + ' = getelementptr %WysnaValue, ptr %arguments, i64 ' + str(ordinal))
            if type_id in SCALAR_TYPES:
                method = {TYPE_INT: 'int', TYPE_FLOAT: 'float', TYPE_BOOL: 'bool'}[type_id]
                type_ = 'i32' if type_id == TYPE_BOOL else SCALAR_TYPES[type_id]
                value = '%value' + str(position)
                lines.append('  ' + value + ' = call ' + type_ + ' @wysna_llvm_' + method + '(ptr ' + pointer + ')')
                if type_id == TYPE_BOOL:
                    lines.append('  %boolean' + str(position) + ' = icmp ne i32 ' + value + ', 0')
                    value = '%boolean' + str(position)
                parameters.append(SCALAR_TYPES[type_id] + ' ' + value)
            else:
                parameters.append('ptr ' + pointer)
        returned = function.return_type
        if is_managed(returned):
            parameters.insert(0, 'ptr %out')
        type_ = SCALAR_TYPES.get(returned, 'void')
        call = 'call ' + type_ + ' @' + name + '(' + ', '.join(parameters) + ')'
        lines.append('  ' + ('%returned = ' if returned in SCALAR_TYPES else '') + call)
        if returned in SCALAR_TYPES:
            method = {TYPE_INT: 'int', TYPE_FLOAT: 'float', TYPE_BOOL: 'bool'}[returned]
            value = '%returned'
            if returned == TYPE_BOOL:
                lines.append('  %wide = zext i1 %returned to i32')
                value = '%wide'
                type_ = 'i32'
            lines.append('  call void @wysna_llvm_box_' + method + '(ptr %out, ' + type_ + ' ' + value + ')')
        return lines + ['  ret void', '}', '']

    def metadata_and_adapters(self):
        gen = self.gen
        adapters = {}
        for function in gen.module.functions:
            for block in function.blocks:
                for operation in block.operations:
                    if operation.opcode == 'function':
                        data = operation.data
                        adapters[data['name']] = (data['arity'], data['captured'])
        for layout in gen.module.layouts.values():
            if layout.kind != 'class':
                continue
            entries = []
            for name, arity in gen.module.virtual_slots:
                method = layout.methods.get(name)
                if method is None or len(method[1]) != arity:
                    entries.append('ptr null')
                else:
                    target = 'wysna_method_' + method[0] + '__' + name
                    adapters[target] = (arity, True)
                    entries.append('ptr @adapter_' + target)
            slots = 'null'
            if entries:
                slots = '@methods_' + layout.name
                gen.pool.append(slots + ' = private constant [' + str(len(entries)) + ' x ptr] [' + ', '.join(entries) + '], align 8')
            gen.pool.append('@vtable_' + layout.name + ' = private constant {i32, ptr} {i32 ' + str(len(entries)) + ', ptr ' + slots + '}, align 8')
        lines = []
        for name, (arity, captured) in adapters.items():
            lines.extend(self.adapter(name, gen.functions[name], arity, captured))
        return lines
