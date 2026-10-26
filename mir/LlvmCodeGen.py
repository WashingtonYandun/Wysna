"""Textual LLVM IR from verified MIR; no syntax traversal or C program emission."""
import struct
from pathlib import Path
from checker.TypeInterner import TYPE_UNIT, TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STR
from mir.LlvmProfile import (verify_profile, CONVERSIONS, ABI_VERSION, VALUE_BYTES,
                             LITERAL_BYTES, ALIGNMENT, PROFILE)

TYPES = {TYPE_INT: 'i64', TYPE_FLOAT: 'double', TYPE_BOOL: 'i1', TYPE_UNIT: 'void', TYPE_STR: 'ptr'}
DECLARATIONS = '''declare void @wysna_llvm_init(i32, ptr)
declare void @wysna_llvm_cleanup()
declare void @wysna_llvm_box_int(ptr, i64)
declare void @wysna_llvm_box_float(ptr, double)
declare void @wysna_llvm_box_bool(ptr, i32)
declare i64 @wysna_llvm_int(ptr)
declare double @wysna_llvm_float(ptr)
declare i32 @wysna_llvm_bool(ptr)
declare void @wysna_llvm_clone(ptr, ptr)
declare void @wysna_llvm_move(ptr, ptr)
declare void @wysna_llvm_release(ptr)
declare void @wysna_llvm_literal(ptr, ptr, ptr, i64)
declare void @wysna_llvm_print(i32, ptr, i32)
declare void @wysna_llvm_convert(ptr, ptr, i32)
declare void @wysna_llvm_concat(ptr, ptr, ptr)
declare i32 @wysna_llvm_compare(ptr, ptr, i32)
declare i32 @wysna_llvm_range_count(i64, i64, i32)
declare void @wysna_llvm_position(ptr, i32, i32)
declare void @wysna_llvm_fail(ptr, ptr, ptr, i32, i32) noreturn
declare void @wysna_llvm_exit(i64) noreturn
'''


def quote_bytes(data):
    return '"' + ''.join(chr(byte) if 32 <= byte < 127 and byte not in (34, 92)
                         else '\\' + format(byte, '02X') for byte in data) + '"'


class DebugInfo:
    """Source lines and function identities; local-variable tracking is a later cut."""
    def __init__(self, file, optimized):
        self.nodes = []
        self.files = {}
        self.locations = {}
        root = self.file(file)
        self.types = self.add('!DISubroutineType(types: !{})')
        self.unit = self.add('distinct !DICompileUnit(language: DW_LANG_C, file: !' + str(root) +
                             ', producer: "Wysna ' + PROFILE + '", isOptimized: ' +
                             str(optimized).lower() + ', runtimeVersion: 0, emissionKind: FullDebug)')
        self.version = self.add('!{i32 2, !"Debug Info Version", i32 3}')
        self.dwarf = self.add('!{i32 2, !"Dwarf Version", i32 4}')

    def add(self, value):
        self.nodes.append(value)
        return len(self.nodes) - 1

    def file(self, path):
        if path not in self.files:
            p = Path(path)
            self.files[path] = self.add('!DIFile(filename: ' + quote_bytes(p.name.encode()) +
                                        ', directory: ' + quote_bytes(str(p.parent).encode()) + ')')
        return self.files[path]

    def function(self, function):
        file = self.file(function.source.file)
        name = function.name.removeprefix('wysna_fn_')
        return self.add('distinct !DISubprogram(name: ' + quote_bytes(name.encode()) +
                        ', linkageName: ' + quote_bytes(function.name.encode()) + ', scope: !' + str(file) +
                        ', file: !' + str(file) + ', line: ' + str(function.source.line) +
                        ', type: !' + str(self.types) + ', scopeLine: ' + str(function.source.line) +
                        ', spFlags: DISPFlagDefinition, unit: !' + str(self.unit) + ')')

    def location(self, source, scope, scope_file):
        key = (source.file, source.line, source.col, scope)
        if key not in self.locations:
            actual = scope
            if source.file != scope_file:
                actual = self.add('!DILexicalBlockFile(scope: !' + str(scope) + ', file: !' +
                                  str(self.file(source.file)) + ', discriminator: 0)')
            self.locations[key] = self.add('!DILocation(line: ' + str(source.line) + ', column: ' +
                                           str(source.col) + ', scope: !' + str(actual) + ')')
        return ', !dbg !' + str(self.locations[key])

    def render(self):
        return ['!llvm.dbg.cu = !{!' + str(self.unit) + '}',
                '!llvm.module.flags = !{!' + str(self.version) + ', !' + str(self.dwarf) + '}'] + [
                    '!' + str(index) + ' = ' + value for index, value in enumerate(self.nodes)]


class LlvmCodeGen:
    def __init__(self, interner):
        self.interner = interner

    def generate(self, module, *, triple, data_layout, profile='dev'):
        verify_profile(module, self.interner)
        self.module = module
        self.functions = {function.name: function for function in module.functions}
        self.pool = []
        self.strings = {}
        self.debug = DebugInfo(module.file, profile != 'dev')
        self.value_id = 0
        self.label_id = 0
        bodies = []
        for function in module.functions:
            bodies.extend(self.function(function))
        entry = self.functions['wysna_program']
        returned = TYPES[entry.return_type]
        bodies.extend(['define i32 @main(i32 %argc, ptr %argv) {', 'entry:',
                       '  call void @wysna_llvm_init(i32 %argc, ptr %argv)'])
        if entry.return_type == TYPE_STR:
            bodies.extend(['  %result = alloca %WysnaValue, align ' + str(ALIGNMENT),
                           '  store %WysnaValue zeroinitializer, ptr %result',
                           '  call void @wysna_program(ptr %result)',
                           '  call void @wysna_llvm_release(ptr %result)'])
        else:
            bodies.append('  ' + ('' if returned == 'void' else '%result = ') +
                          'call ' + returned + ' @wysna_program()')
        for slot in reversed(module.globals):
            bodies.append('  call void @wysna_llvm_release(ptr ' + self.pointer(slot.id) + ')')
        bodies.extend(['  call void @wysna_llvm_cleanup()', '  ret i32 0', '}', ''])
        globals_ = [self.pointer(slot.id) + ' = internal global %WysnaValue zeroinitializer, align ' +
                    str(ALIGNMENT) for slot in module.globals]
        return '\n'.join(['; Wysna ' + PROFILE + ', runtime ABI ' + str(ABI_VERSION),
                          'source_filename = ' + quote_bytes(module.file.encode()),
                          'target triple = "' + triple + '"', 'target datalayout = "' + data_layout + '"',
                          '%WysnaValue = type { [' + str(VALUE_BYTES) + ' x i8] }',
                          DECLARATIONS] + globals_ + self.pool + [''] + bodies + self.debug.render()) + '\n'

    def string(self, text):
        data = text.encode('utf-8')
        if data not in self.strings:
            name = '@bytes' + str(len(self.strings))
            self.strings[data] = name
            self.pool.append(name + ' = private unnamed_addr constant [' + str(len(data) + 1) +
                             ' x i8] c' + quote_bytes(data + b'\0') + ', align 1')
        return self.strings[data]

    def emit(self, line):
        self.lines.append('  ' + line + self.location)

    def result(self, expression):
        name = '%r' + str(self.value_id)
        self.value_id += 1
        self.emit(name + ' = ' + expression)
        return name

    def label(self, prefix):
        name = prefix + str(self.label_id)
        self.label_id += 1
        return name

    def pointer(self, index):
        return '@global' + str(-index) if index < 0 else '%slot' + str(index)

    def physical(self, index):
        slot = self.slots[index]
        return {'integer': 'i64', 'float': 'double', 'boolean': 'i1', 'counter': 'i32',
                'predicate': 'i1', 'value': '%WysnaValue'}[slot.storage]

    def load(self, index):
        slot = self.slots[index]
        pointer = self.pointer(index)
        if slot.storage == 'value':
            name = {TYPE_INT: 'int', TYPE_BOOL: 'bool', TYPE_FLOAT: 'float'}.get(slot.type_id)
            if name is None:
                return pointer
            type_ = 'i32' if slot.type_id == TYPE_BOOL else TYPES[slot.type_id]
            value = self.result('call ' + type_ + ' @wysna_llvm_' + name + '(ptr ' + pointer + ')')
            return self.result('icmp ne i32 ' + value + ', 0') if slot.type_id == TYPE_BOOL else value
        return self.result('load ' + self.physical(index) + ', ptr ' + pointer)

    def write(self, index, value, type_=None):
        slot = self.slots[index]
        type_ = type_ or TYPES[slot.type_id]
        if slot.storage == 'value':
            name = {TYPE_INT: 'int', TYPE_FLOAT: 'float', TYPE_BOOL: 'bool'}[slot.type_id]
            if slot.type_id == TYPE_BOOL:
                value = self.result('zext i1 ' + value + ' to i32')
                type_ = 'i32'
            self.emit('call void @wysna_llvm_box_' + name + '(ptr ' + self.pointer(index) + ', ' + type_ + ' ' + value + ')')
        else:
            physical = self.physical(index)
            if type_ != physical:
                operation = 'sext' if physical == 'i64' else 'trunc'
                value = self.result(operation + ' ' + type_ + ' ' + value + ' to ' + physical)
            self.emit('store ' + physical + ' ' + value + ', ptr ' + self.pointer(index))

    def cell(self, offset):
        return self.result('getelementptr [' + str(self.scratch) + ' x %WysnaValue], ptr %scratch, i32 0, i32 ' + str(offset))

    def boxed(self, index, offset=0, copy=False):
        slot = self.slots[index]
        if slot.storage == 'value' and not copy:
            return self.pointer(index)
        pointer = self.cell(offset)
        if slot.storage == 'value':
            value = self.result('load %WysnaValue, ptr ' + self.pointer(index))
            self.emit('store %WysnaValue ' + value + ', ptr ' + pointer)
        else:
            self.emit('store %WysnaValue zeroinitializer, ptr ' + pointer)
            value = self.load(index)
            type_ = TYPES[slot.type_id]
            name = {TYPE_INT: 'int', TYPE_FLOAT: 'float', TYPE_BOOL: 'bool'}[slot.type_id]
            if slot.type_id == TYPE_BOOL:
                value = self.result('zext i1 ' + value + ' to i32')
                type_ = 'i32'
            self.emit('call void @wysna_llvm_box_' + name + '(ptr ' + pointer + ', ' + type_ + ' ' + value + ')')
        return pointer

    def function(self, function):
        self.slots = {slot.id: slot for slot in self.module.globals}
        self.slots.update({slot.id: slot for slot in function.slots})
        self.scope = self.debug.function(function)
        self.scope_file = function.source.file
        self.location = ''
        parameters = []
        if function.return_type == TYPE_STR:
            parameters.append('ptr %out')
        for index in function.params:
            type_ = 'ptr' if function.slots[index].type_id in (TYPE_STR, TYPE_UNIT) else TYPES[function.slots[index].type_id]
            parameters.append(type_ + ' %arg' + str(index))
        returned = 'void' if function.return_type == TYPE_STR else TYPES[function.return_type]
        self.lines = ['define internal ' + returned + ' @' + function.name + '(' + ', '.join(parameters) +
                      ') !dbg !' + str(self.scope) + ' {', 'entry:']
        for slot in function.slots:
            if slot.parameter and slot.storage == 'value':
                self.lines.append('  ' + self.pointer(slot.id) + ' = getelementptr i8, ptr %arg' + str(slot.id) + ', i64 0')
            else:
                type_ = self.physical(slot.id)
                self.lines.append('  ' + self.pointer(slot.id) + ' = alloca ' + type_ + ', align ' + str(ALIGNMENT))
                initial = '%arg' + str(slot.id) if slot.parameter else 'zeroinitializer'
                self.lines.append('  store ' + type_ + ' ' + initial + ', ptr ' + self.pointer(slot.id))
        self.scratch = max([2] + [len(operation.args) + 1 for block in function.blocks for operation in block.operations])
        self.lines.append('  %scratch = alloca [' + str(self.scratch) + ' x %WysnaValue], align ' + str(ALIGNMENT))
        self.lines.append('  br label %bb0')
        for block in function.blocks:
            self.lines.append('bb' + str(block.id) + ':')
            for operation in block.operations:
                self.location = self.debug.location(operation.source, self.scope, self.scope_file)
                self.operation(operation)
            term = block.terminator
            self.location = self.debug.location(term.source, self.scope, self.scope_file)
            if term.kind == 'goto':
                self.emit('br label %bb' + str(term.targets[0]))
            elif term.kind == 'branch':
                value = self.load(term.value)
                self.emit('br i1 ' + value + ', label %bb' + str(term.targets[0]) + ', label %bb' + str(term.targets[1]))
            elif term.kind == 'return':
                if function.return_type == TYPE_STR:
                    self.emit('call void @wysna_llvm_move(ptr %out, ptr ' + self.pointer(term.value) + ')')
                    self.emit('ret void')
                elif function.return_type == TYPE_UNIT:
                    self.emit('ret void')
                else:
                    self.emit('ret ' + returned + ' ' + self.load(term.value))
            else:
                self.fail('W9999', 'Unreachable MIR block executed', term.source)
        return self.lines + ['}', '']

    def fail(self, code, message, source):
        self.emit('call void @wysna_llvm_fail(ptr ' + self.string(code) + ', ptr ' + self.string(message) +
                  ', ptr ' + self.string(source.file) + ', i32 ' + str(source.line) + ', i32 ' + str(source.col) + ')')
        self.emit('unreachable')

    def operation(self, operation):
        kind, data, result = operation.opcode, operation.data, operation.result
        args = operation.args
        if kind == 'const':
            type_ = self.slots[result].type_id
            value = data['value']
            if type_ == TYPE_STR and value is not None:
                cache = 'null'
                if len(value) > 1:
                    cache = '@literal' + str(len(self.pool))
                    self.pool.append(cache + ' = internal global [' + str(LITERAL_BYTES) + ' x i8] zeroinitializer, align ' + str(ALIGNMENT))
                self.emit('call void @wysna_llvm_literal(ptr ' + self.pointer(result) + ', ptr ' + cache +
                          ', ptr ' + self.string(value) + ', i64 ' + str(len(value.encode('utf-8'))) + ')')
            elif type_ == TYPE_UNIT or value is None:
                if self.slots[result].storage == 'value':
                    self.emit('call void @wysna_llvm_release(ptr ' + self.pointer(result) + ')')
                else:
                    self.emit('store ' + self.physical(result) + ' zeroinitializer, ptr ' + self.pointer(result))
            else:
                literal = ('true' if value else 'false') if isinstance(value, bool) else str(value)
                if isinstance(value, float):
                    literal = '0x' + format(struct.unpack('>Q', struct.pack('>d', value))[0], '016X')
                self.write(result, literal)
        elif kind in ('clone', 'store'):
            if self.slots[result].type_id in (TYPE_STR, TYPE_UNIT):
                method = 'move' if operation.moves else 'clone'
                self.emit('call void @wysna_llvm_' + method + '(ptr ' + self.pointer(result) + ', ptr ' + self.pointer(args[0]) + ')')
            else:
                source_type = self.physical(args[0]) if self.slots[args[0]].storage != 'value' else TYPES[self.slots[args[0]].type_id]
                self.write(result, self.load(args[0]), source_type)
        elif kind == 'release':
            self.emit('call void @wysna_llvm_release(ptr ' + self.pointer(args[0]) + ')')
        elif kind == 'store_place':
            destination = data['place'].root
            if self.slots[destination].type_id in (TYPE_STR, TYPE_UNIT):
                self.emit('call void @wysna_llvm_move(ptr ' + self.pointer(destination) + ', ptr ' + self.pointer(args[0]) + ')')
            else:
                self.write(destination, self.load(args[0]))
        elif kind == 'call':
            function = self.functions[data['name']]
            parameters = []
            if function.return_type == TYPE_STR:
                parameters.append('ptr ' + self.pointer(result))
            for index in args:
                type_ = self.slots[index].type_id
                parameters.append(('ptr' if type_ in (TYPE_STR, TYPE_UNIT) else TYPES[type_]) + ' ' + self.load(index))
            returned = 'void' if function.return_type == TYPE_STR else TYPES[function.return_type]
            call = 'call ' + returned + ' @' + function.name + '(' + ', '.join(parameters) + ')'
            if returned == 'void':
                self.emit(call)
            else:
                self.write(result, self.result(call))
        elif kind == 'runtime':
            self.runtime(operation)
        elif kind == 'test':
            if data['kind'] == 'truth':
                self.write(result, self.load(args[0]))
            else:
                self.compare(operation, 'wysna_equal')
        elif kind == 'counter':
            self.write(result, '0', 'i32')
        elif kind == 'increment':
            value = self.result('add i32 ' + self.load(args[0]) + ', 1')
            self.write(result, value, 'i32')
        elif kind == 'range_count':
            left, right = self.load(args[0]), self.load(args[1])
            value = self.result('call i32 @wysna_llvm_range_count(i64 ' + left + ', i64 ' + right +
                                ', i32 ' + str(int(data['inclusive'])) + ')')
            self.write(result, value, 'i32')
        elif kind == 'iterate_test':
            left, right = self.load(args[0]), self.load(args[1])
            self.write(result, self.result('icmp slt i32 ' + left + ', ' + right))
        elif kind == 'iterate_value':
            start = self.load(args[0])
            counter = self.result('sext i32 ' + self.load(args[1]) + ' to i64')
            self.write(result, self.result('add i64 ' + start + ', ' + counter))

    def compare(self, operation, name):
        args = operation.args
        type_ = self.slots[args[0]].type_id
        codes = {'wysna_equal': 0, 'wysna_not_equal': 1, 'wysna_lt': 2, 'wysna_le': 3, 'wysna_gt': 4, 'wysna_ge': 5}
        if type_ in (TYPE_STR, TYPE_UNIT):
            left, right = self.boxed(args[0], 0), self.boxed(args[1], 1)
            value = self.result('call i32 @wysna_llvm_compare(ptr ' + left + ', ptr ' + right + ', i32 ' + str(codes[name]) + ')')
            compared = self.result('icmp ne i32 ' + value + ', 0')
        else:
            left, right = self.load(args[0]), self.load(args[1])
            predicates = ('oeq', 'une', 'olt', 'ole', 'ogt', 'oge') if type_ == TYPE_FLOAT else ('eq', 'ne', 'slt', 'sle', 'sgt', 'sge')
            instruction = 'fcmp' if type_ == TYPE_FLOAT else 'icmp'
            compared = self.result(instruction + ' ' + predicates[codes[name]] + ' ' + TYPES[type_] + ' ' + left + ', ' + right)
        self.write(operation.result, compared)

    def runtime(self, operation):
        name, args, result = operation.data['name'], operation.args, operation.result
        type_ = self.slots[args[0]].type_id if args else TYPE_UNIT
        if name in ('wysna_builtin_print', 'wysna_builtin_eprint'):
            for offset, index in enumerate(args):
                self.boxed(index, offset, copy=True)
            pointer = self.cell(0) if args else 'null'
            self.emit('call void @wysna_llvm_print(i32 ' + str(len(args)) + ', ptr ' + pointer +
                      ', i32 ' + str(int(name == 'wysna_builtin_eprint')) + ')')
        elif name == 'wysna_builtin_exit':
            self.emit('call void @wysna_llvm_exit(i64 ' + self.load(args[0]) + ')')
        elif name in CONVERSIONS:
            source = self.boxed(args[0])
            out = self.pointer(result)
            if self.slots[result].storage != 'value':
                out = self.cell(1)
                self.emit('store %WysnaValue zeroinitializer, ptr ' + out)
            self.emit('call void @wysna_llvm_convert(ptr ' + out + ', ptr ' + source + ', i32 ' + str(CONVERSIONS[name]) + ')')
            if self.slots[result].storage != 'value':
                method = 'int' if self.slots[result].type_id == TYPE_INT else 'float'
                value = self.result('call ' + TYPES[self.slots[result].type_id] + ' @wysna_llvm_' + method + '(ptr ' + out + ')')
                self.write(result, value)
        elif name in ('wysna_equal', 'wysna_not_equal', 'wysna_lt', 'wysna_le', 'wysna_gt', 'wysna_ge'):
            self.compare(operation, name)
        elif name == 'wysna_add' and type_ == TYPE_STR:
            self.emit('call void @wysna_llvm_concat(ptr ' + self.pointer(result) + ', ptr ' + self.load(args[0]) +
                      ', ptr ' + self.load(args[1]) + ')')
        elif name == 'wysna_not':
            self.write(result, self.result('xor i1 ' + self.load(args[0]) + ', true'))
        elif name == 'wysna_neg':
            value = self.load(args[0])
            self.write(result, self.result(('fneg double ' + value) if type_ == TYPE_FLOAT else ('sub i64 0, ' + value)))
        else:
            left, right = self.load(args[0]), self.load(args[1])
            if name in ('wysna_div_at', 'wysna_mod_at'):
                self.divide(operation, left, right, type_)
            else:
                instruction = {'wysna_add': 'add', 'wysna_sub': 'sub', 'wysna_mul': 'mul'}[name]
                if type_ == TYPE_FLOAT:
                    instruction = 'f' + instruction
                self.write(result, self.result(instruction + ' ' + TYPES[type_] + ' ' + left + ', ' + right))

    def divide(self, operation, left, right, type_):
        source = operation.source
        self.emit('call void @wysna_llvm_position(ptr ' + self.string(source.file) + ', i32 ' + str(source.line) + ', i32 ' + str(source.col) + ')')
        float_ = type_ == TYPE_FLOAT
        zero = self.result(('fcmp oeq double ' + right + ', 0.0') if float_ else ('icmp eq i64 ' + right + ', 0'))
        fault, resume = self.label('division_fault'), self.label('division_resume')
        self.emit('br i1 ' + zero + ', label %' + fault + ', label %' + resume)
        self.lines.append(fault + ':')
        modulo = operation.data['name'] == 'wysna_mod_at'
        self.fail('W4001', 'Modulo by zero' if modulo else 'Division by zero', operation.source)
        self.lines.append(resume + ':')
        if float_:
            value = self.result('fdiv double ' + left + ', ' + right)
        else:
            minimum = self.result('icmp eq i64 ' + left + ', -9223372036854775808')
            negative = self.result('icmp eq i64 ' + right + ', -1')
            overflow = self.result('and i1 ' + minimum + ', ' + negative)
            safe = self.result('select i1 ' + overflow + ', i64 1, i64 ' + right)
            value = self.result(('srem' if modulo else 'sdiv') + ' i64 ' + left + ', ' + safe)
            value = self.result('select i1 ' + overflow + ', i64 ' + ('0' if modulo else '-9223372036854775808') + ', i64 ' + value)
        self.write(operation.result, value)
