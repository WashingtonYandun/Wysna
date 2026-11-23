"""Textual LLVM IR from verified MIR; no syntax traversal or C program emission."""
import struct
from compiler.StdlibIdentity import stdlib_declaration
from checker.TypeInterner import TYPE_UNIT, TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STR
from mir.LlvmDebug import DebugInfo
from mir.LlvmText import quote_bytes
from mir.LlvmManaged import ManagedEmission, DECLARATIONS as MANAGED_DECLARATIONS
from mir.LlvmAbi import SCALAR_TYPES, is_managed
from mir.AsyncLowering import build_cps_plan
from mir.LlvmProfile import (verify_profile, CONVERSIONS, ABI_VERSION, VALUE_BYTES,
                             LITERAL_BYTES, ALIGNMENT, PROFILE)

TYPES = {TYPE_INT: 'i64', TYPE_FLOAT: 'double', TYPE_BOOL: 'i1', TYPE_UNIT: 'void', TYPE_STR: 'ptr'}
DECLARATIONS = '''declare void @llvm.dbg.declare(metadata, metadata, metadata)
declare void @wysna_llvm_step_tick()
declare void @wysna_llvm_init(i32, ptr)
declare void @wysna_llvm_cleanup()
declare void @wysna_llvm_box_int(ptr nocapture, i64)
declare void @wysna_llvm_box_float(ptr nocapture, double)
declare void @wysna_llvm_box_bool(ptr nocapture, i32)
declare i64 @wysna_llvm_int(ptr nocapture readonly) readonly
declare double @wysna_llvm_float(ptr nocapture readonly) readonly
declare i32 @wysna_llvm_bool(ptr nocapture readonly) readonly
declare void @wysna_llvm_clone(ptr nocapture, ptr nocapture readonly)
declare void @wysna_llvm_move(ptr nocapture, ptr nocapture)
declare void @wysna_llvm_release(ptr nocapture)
declare void @wysna_llvm_literal(ptr nocapture, ptr, ptr nocapture readonly, i64)
declare void @wysna_llvm_print(i32, ptr nocapture readonly, i32)
declare void @wysna_llvm_convert(ptr nocapture, ptr nocapture readonly, i32)
declare void @wysna_llvm_concat(ptr nocapture, ptr nocapture readonly, ptr nocapture readonly)
declare i32 @wysna_llvm_compare(ptr nocapture readonly, ptr nocapture readonly, i32) readonly
declare i32 @wysna_llvm_range_count(i64, i64, i32) readnone
declare void @wysna_llvm_position(ptr nocapture readonly, i32, i32)
declare void @wysna_llvm_push_frame(ptr, ptr, i32)
declare void @wysna_llvm_pop_frame()
declare void @wysna_llvm_fail(ptr nocapture readonly, ptr nocapture readonly, ptr nocapture readonly, i32, i32) cold noreturn
declare void @wysna_llvm_exit(i64) cold noreturn
'''




class LlvmCodeGen:
    def __init__(self, interner):
        self.interner = interner

    def generate(self, module, *, triple, data_layout, profile='dev', debug_info=None):
        verify_profile(module, self.interner)
        if debug_info is not None:
            debug_info.require_owner(module)
        self.debug_info = debug_info
        self.module = module
        self.triple = triple
        self.managed = ManagedEmission(self)
        self.metadata = set()
        self.functions = {function.name: function for function in module.functions}
        self.pool = []
        self.strings = {}
        self.debug = DebugInfo(module.file, profile != 'dev')
        self.value_id = 0
        self.label_id = 0
        bodies = []
        for function in module.functions:
            bodies.extend(self.function(function))
        bodies.extend(self.managed.metadata_and_adapters())
        entry = self.functions['wysna_program']
        returned = TYPES.get(entry.return_type, 'ptr')
        bodies.extend(['define i32 @main(i32 %argc, ptr %argv) {', 'entry:',
                       '  call void @wysna_llvm_init(i32 %argc, ptr %argv)'])
        if is_managed(entry.return_type):
            bodies.extend(['  %result = alloca %WysnaValue, align ' + str(ALIGNMENT),
                           '  store %WysnaValue zeroinitializer, ptr %result',
                           '  call void @wysna_program(ptr %result)',
                           '  %status = call i32 @wysna_llvm_entry_status(ptr %result)',
                           '  call void @wysna_llvm_release(ptr %result)'])
        else:
            bodies.append('  ' + ('' if returned == 'void' else '%result = ') +
                          'call ' + returned + ' @wysna_program()')
        if entry.return_type == TYPE_INT:
            bodies.append('  %status = call i32 @wysna_llvm_integer_entry_status(i64 %result)')
        for slot in reversed(module.globals):
            bodies.append('  call void @wysna_llvm_release(ptr ' + self.pointer(slot.id) + ')')
        status = '%status' if is_managed(entry.return_type) or entry.return_type == TYPE_INT else '0'
        bodies.extend(['  call void @wysna_llvm_cleanup()', '  ret i32 ' + status, '}', ''])
        global_debug = {binding.slot: binding for binding in debug_info.globals} if debug_info is not None else {}
        globals_ = []
        for slot in module.globals:
            metadata = ''
            if slot.id in global_debug:
                metadata = ', !dbg !' + str(self.debug.global_binding(global_debug[slot.id], slot, self.interner))
            globals_.append(self.pointer(slot.id) + ' = internal global %WysnaValue zeroinitializer, align ' + str(ALIGNMENT) + metadata)
        return '\n'.join(['; Wysna ' + PROFILE + ', runtime ABI ' + str(ABI_VERSION),
                          'source_filename = ' + quote_bytes(module.file.encode()),
                          'target triple = "' + triple + '"', 'target datalayout = "' + data_layout + '"',
                          '%WysnaValue = type { [' + str(VALUE_BYTES) + ' x i8] }',
                          DECLARATIONS, MANAGED_DECLARATIONS] + ['declare void @wysna_llvm_ffi_' + name + '(ptr, ptr)' for name in module.externs] + globals_ + self.pool + [''] + bodies + self.debug.render()) + '\n'

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

    def alignment_for(self, slot):
        if slot.storage in ('integer', 'float', 'value'):
            return ALIGNMENT
        if slot.storage == 'counter':
            return 4
        return 1

    def load(self, index):
        slot = self.slots[index]
        pointer = self.pointer(index)
        if slot.storage == 'value':
            name = {TYPE_INT: 'int', TYPE_BOOL: 'bool', TYPE_FLOAT: 'float'}.get(slot.type_id)
            if name is None:
                return pointer
            type_ = 'i32' if slot.type_id == TYPE_BOOL else TYPES.get(slot.type_id, 'ptr')
            value = self.result('call ' + type_ + ' @wysna_llvm_' + name + '(ptr ' + pointer + ')')
            return self.result('icmp ne i32 ' + value + ', 0') if slot.type_id == TYPE_BOOL else value
        return self.result('load ' + self.physical(index) + ', ptr ' + pointer + ', align ' + str(self.alignment_for(slot)))

    def write(self, index, value, type_=None):
        slot = self.slots[index]
        type_ = type_ or TYPES.get(slot.type_id, 'ptr')
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
            self.emit('store ' + physical + ' ' + value + ', ptr ' + self.pointer(index) + ', align ' + str(self.alignment_for(slot)))

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
            type_ = TYPES.get(slot.type_id, 'ptr')
            name = {TYPE_INT: 'int', TYPE_FLOAT: 'float', TYPE_BOOL: 'bool'}[slot.type_id]
            if slot.type_id == TYPE_BOOL:
                value = self.result('zext i1 ' + value + ' to i32')
                type_ = 'i32'
            self.emit('call void @wysna_llvm_box_' + name + '(ptr ' + pointer + ', ' + type_ + ' ' + value + ')')
        return pointer

    def release_boxed(self, index, offset=0, moved=False):
        if moved:
            return
        slot = self.slots[index]
        if slot.storage != 'value' and slot.type_id == TYPE_INT:
            self.emit('call void @wysna_llvm_release(ptr ' + self.cell(offset) + ')')

    @staticmethod
    def _async_runtime_helper(function):
        for name, arity in (('_spawn_handle', 1), ('_take_task_value', 2)):
            if stdlib_declaration(function, 'async', name, arity):
                return name
        return None

    @staticmethod
    def _runtime_frame_name(name):
        raw = name[9:] if name.startswith('wysna_fn_') else name
        if raw.startswith('__wysna_module_'):
            end = raw.find('__', 15)
            if end >= 0:
                raw = raw[end + 2:]
        return raw

    def function(self, function):
        if getattr(function, 'is_async', False):
            return self._async_function(function)
        self.slots = {slot.id: slot for slot in self.module.globals}
        self.slots.update({slot.id: slot for slot in function.slots})
        self.scope = self.debug.function(function)
        self.scope_file = function.source.file
        function_debug = self.debug_info.functions.get(function.name) if self.debug_info is not None else None
        scopes = self.debug.scopes(function_debug, self.scope)
        self.location = ''
        parameters = []
        if is_managed(function.return_type):
            parameters.append('ptr %out')
        for index in function.params:
            type_ = SCALAR_TYPES.get(function.slots[index].type_id, 'ptr')
            parameters.append(type_ + ' %arg' + str(index))
        returned = SCALAR_TYPES.get(function.return_type, 'void')
        cc = 'fastcc ' if function.name != 'wysna_program' else ''
        attrs = ' nounwind mustprogress'
        self.lines = ['define internal ' + cc + returned + ' @' + function.name + '(' + ', '.join(parameters) +
                      ')' + attrs + ' !dbg !' + str(self.scope) + ' {', 'entry:']
        async_helper = self._async_runtime_helper(function)
        if async_helper is not None:
            loc = self.debug.location(function.source, self.scope, self.scope_file)
            if async_helper == '_spawn_handle' and returned == 'i64' and self.slots[function.params[0]].storage == 'value':
                self.lines.append('  %r0 = call i64 @wysna_llvm_async_spawn(ptr %arg' + str(function.params[0]) + ')' + loc)
                self.lines.append('  ret i64 %r0' + loc)
                return self.lines + ['}', '']
            if async_helper == '_take_task_value' and is_managed(function.return_type) and self.slots[function.params[0]].type_id == TYPE_INT and self.slots[function.params[1]].storage == 'value':
                self.lines.append('  call void @wysna_llvm_async_take_option(ptr %out, i64 %arg' + str(function.params[0]) + ', ptr %arg' + str(function.params[1]) + ')' + loc)
                self.lines.append('  ret void' + loc)
                return self.lines + ['}', '']
        for slot in function.slots:
            if slot.parameter and slot.storage == 'value':
                self.lines.append('  ' + self.pointer(slot.id) + ' = getelementptr i8, ptr %arg' + str(slot.id) + ', i64 0')
            else:
                type_ = self.physical(slot.id)
                align = str(self.alignment_for(slot))
                self.lines.append('  ' + self.pointer(slot.id) + ' = alloca ' + type_ + ', align ' + align)
                initial = '%arg' + str(slot.id) if slot.parameter else 'zeroinitializer'
                self.lines.append('  store ' + type_ + ' ' + initial + ', ptr ' + self.pointer(slot.id) + ', align ' + align)
        if function_debug is not None:
            for binding in function_debug.bindings:
                variable = self.debug.binding(binding, self.slots[binding.slot], scopes, self.interner)
                location = self.debug.location(binding.source, scopes[binding.scope], self.scope_file)
                self.lines.append('  call void @llvm.dbg.declare(metadata ptr ' + self.pointer(binding.slot) + ', metadata !' + str(variable) + ', metadata !DIExpression())' + location)
        self.scratch = max([2] + [len(operation.args) + 1 for block in function.blocks for operation in block.operations])
        self.lines.append('  %scratch = alloca [' + str(self.scratch) + ' x %WysnaValue], align ' + str(ALIGNMENT))
        track_frame = function.name.startswith('wysna_fn_')
        if track_frame:
            self.emit('call void @wysna_llvm_push_frame(ptr ' + self.string(self._runtime_frame_name(function.name)) +
                      ', ptr ' + self.string(function.source.file or '<unknown>') +
                      ', i32 ' + str(function.source.line or 0) + ')')
        self.lines.append('  br label %bb0')
        back_edge_targets = {target for block in function.blocks if block.terminator is not None
                             for target in block.terminator.targets if target <= block.id}
        for block in function.blocks:
            self.lines.append('bb' + str(block.id) + ':')
            if block.id == 0 or block.id in back_edge_targets:
                self.emit('call void @wysna_llvm_step_tick()')
            for operation in block.operations:
                self.location = self.debug.location(operation.source, scopes[function_debug.scope_for(operation)] if function_debug else self.scope, self.scope_file)
                self.operation(operation)
            term = block.terminator
            self.location = self.debug.location(term.source, scopes[function_debug.scope_for(term)] if function_debug else self.scope, self.scope_file)
            if term.kind == 'goto':
                self.emit('br label %bb' + str(term.targets[0]))
            elif term.kind == 'branch':
                value = self.load(term.value)
                self.emit('br i1 ' + value + ', label %bb' + str(term.targets[0]) + ', label %bb' + str(term.targets[1]))
            elif term.kind == 'return':
                if track_frame:
                    self.emit('call void @wysna_llvm_pop_frame()')
                if is_managed(function.return_type):
                    self.emit('call void @wysna_llvm_move(ptr %out, ptr ' + self.pointer(term.value) + ')')
                    self.emit('ret void')
                elif function.return_type == TYPE_UNIT:
                    self.emit('ret void')
                else:
                    self.emit('ret ' + returned + ' ' + self.load(term.value))
            else:
                self.fail('W9999', 'Unreachable MIR block executed', term.source)
        return self.lines + ['}', '']

    def _emit_llvm_coro_assign_from_ptr(self, dest_slot_id, src_ptr, move_src):
        slot = self.slots[dest_slot_id]
        if slot.storage == 'value':
            op = 'move' if move_src else 'clone'
            self.emit(f'call void @wysna_llvm_{op}(ptr {self.pointer(dest_slot_id)}, ptr {src_ptr})')
            return
        if slot.type_id == TYPE_INT:
            val = self.result(f'call i64 @wysna_llvm_int(ptr {src_ptr})')
            if move_src:
                self.emit(f'call void @wysna_llvm_release(ptr {src_ptr})')
            self.write(dest_slot_id, val, 'i64')
        elif slot.type_id == TYPE_FLOAT:
            val = self.result(f'call double @wysna_llvm_float(ptr {src_ptr})')
            if move_src:
                self.emit(f'call void @wysna_llvm_release(ptr {src_ptr})')
            self.write(dest_slot_id, val, 'double')
        elif slot.type_id == TYPE_BOOL:
            val32 = self.result(f'call i32 @wysna_llvm_bool(ptr {src_ptr})')
            if move_src:
                self.emit(f'call void @wysna_llvm_release(ptr {src_ptr})')
            val = self.result(f'icmp ne i32 {val32}, 0')
            self.write(dest_slot_id, val, 'i1')

    def _emit_llvm_coro_flush_slots(self, function):
        for slot in function.slots:
            fs = self.result(f'getelementptr %WysnaValue, ptr %_frame_slots, i64 {slot.id}')
            if slot.storage == 'value':
                self.emit(f'call void @wysna_llvm_move(ptr {fs}, ptr {self.pointer(slot.id)})')
            elif slot.type_id == TYPE_INT:
                val = self.load(slot.id)
                if slot.storage == 'counter':
                    val = self.result(f'sext i32 {val} to i64')
                self.emit(f'call void @wysna_llvm_box_int(ptr {fs}, i64 {val})')
            elif slot.type_id == TYPE_FLOAT:
                self.emit(f'call void @wysna_llvm_box_float(ptr {fs}, double {self.load(slot.id)})')
            elif slot.type_id == TYPE_BOOL:
                b32 = self.result(f'zext i1 {self.load(slot.id)} to i32')
                self.emit(f'call void @wysna_llvm_box_bool(ptr {fs}, i32 {b32})')

    def _async_function(self, function):
        self.slots = {slot.id: slot for slot in self.module.globals}
        self.slots.update({slot.id: slot for slot in function.slots})
        self.scope = self.debug.function(function)
        self.scope_file = function.source.file
        self.location = ''
        plan = build_cps_plan(function)
        step_fn = f'{function.name}__coro_step'

        self.lines = [
            f'define internal void @{step_fn}(ptr %_out_ret, ptr %_frame_slots, i64 %_slot_count, ptr %_frame_state, ptr %_resume_val, i32 %_has_resume, ptr %_out_await_box, ptr %_out_await_handle, ptr %_out_status) nounwind {{',
            'entry:',
        ]
        for slot in function.slots:
            type_ = self.physical(slot.id)
            align = str(self.alignment_for(slot))
            self.lines.append(f'  {self.pointer(slot.id)} = alloca {type_}, align {align}')
            self.lines.append(f'  store {type_} zeroinitializer, ptr {self.pointer(slot.id)}, align {align}')
        self.scratch = max([4] + [len(op.args) + 2 for seg in plan.segments for op in seg.operations])
        self.lines.append(f'  %scratch = alloca [{self.scratch} x %WysnaValue], align {ALIGNMENT}')
        for slot in function.slots:
            fs = self.result(f'getelementptr %WysnaValue, ptr %_frame_slots, i64 {slot.id}')
            if slot.storage == 'value':
                self.emit(f'call void @wysna_llvm_move(ptr {self.pointer(slot.id)}, ptr {fs})')
            elif slot.type_id == TYPE_INT:
                iv = self.result(f'call i64 @wysna_llvm_coro_restore_int(ptr {fs})')
                if slot.storage == 'counter':
                    iv = self.result(f'trunc i64 {iv} to i32')
                self.emit(f'store {self.physical(slot.id)} {iv}, ptr {self.pointer(slot.id)}, align {self.alignment_for(slot)}')
            elif slot.type_id == TYPE_FLOAT:
                fv = self.result(f'call double @wysna_llvm_coro_restore_float(ptr {fs})')
                self.emit(f'store double {fv}, ptr {self.pointer(slot.id)}, align {ALIGNMENT}')
            elif slot.type_id == TYPE_BOOL:
                bv32 = self.result(f'call i32 @wysna_llvm_coro_restore_bool(ptr {fs})')
                bv = self.result(f'icmp ne i32 {bv32}, 0')
                self.emit(f'store i1 {bv}, ptr {self.pointer(slot.id)}, align 1')

        st_val = self.result('load i64, ptr %_frame_state, align 8')
        cases = ' '.join(['i64 0, label %bb0'] + [f'i64 {sp.state_id}, label %{sp.resume_label}' for sp in plan.suspend_points])
        self.emit(f'switch i64 {st_val}, label %cps_default [ {cases} ]')

        for seg in plan.segments:
            self.lines.append(f'{seg.label}:')
            if seg.loop_head:
                self.emit('call void @wysna_llvm_step_tick()')
            for op in seg.operations:
                self.operation(op)
            if seg.suspend is not None:
                sp = seg.suspend
                arg_slot = self.slots[sp.await_arg]
                if arg_slot.storage == 'value':
                    aw_box = self.cell(0)
                    self.emit(f'store %WysnaValue zeroinitializer, ptr {aw_box}')
                    self.emit(f'call void @wysna_llvm_move(ptr {aw_box}, ptr {self.pointer(sp.await_arg)})')
                else:
                    aw_box = self.boxed(sp.await_arg, 0, copy=True)
                aw_imm = self.cell(1)
                self.emit(f'store %WysnaValue zeroinitializer, ptr {aw_imm}')
                resolved = self.result(f'call i32 @wysna_llvm_coro_try_resolve_await(ptr {aw_box}, ptr %_out_await_handle, ptr {aw_imm})')
                is_ready = self.result(f'icmp ne i32 {resolved}, 0')
                imm_lbl = f'cps_imm_{sp.state_id}'
                susp_lbl = f'cps_susp_{sp.state_id}'
                self.emit(f'br i1 {is_ready}, label %{imm_lbl}, label %{susp_lbl}')

                self.lines.append(f'{imm_lbl}:')
                self._emit_llvm_coro_assign_from_ptr(sp.await_res, aw_imm, move_src=True)
                self.emit(f'br label %{sp.continue_label}')

                self.lines.append(f'{susp_lbl}:')
                self.emit(f'store i64 {sp.state_id}, ptr %_frame_state, align 8')
                self.emit(f'call void @wysna_llvm_move(ptr %_out_await_box, ptr {aw_box})')
                self.emit('store i32 1, ptr %_out_status, align 4')
                self._emit_llvm_coro_flush_slots(function)
                self.emit('ret void')

                self.lines.append(f'{sp.resume_label}:')
                self._emit_llvm_coro_assign_from_ptr(sp.await_res, '%_resume_val', move_src=False)
                self.emit(f'br label %{sp.continue_label}')
            else:
                term = seg.terminator
                if term.kind == 'goto':
                    self.emit('br label %bb' + str(term.targets[0]))
                elif term.kind == 'branch':
                    value = self.load(term.value)
                    self.emit('br i1 ' + value + ', label %bb' + str(term.targets[0]) + ', label %bb' + str(term.targets[1]))
                elif term.kind == 'return':
                    rslot = self.slots[term.value]
                    if rslot.storage == 'value':
                        op = 'clone' if rslot.parameter else 'move'
                        self.emit(f'call void @wysna_llvm_{op}(ptr %_out_ret, ptr {self.pointer(term.value)})')
                    elif rslot.type_id == TYPE_INT:
                        val = self.load(term.value)
                        if rslot.storage == 'counter':
                            val = self.result(f'sext i32 {val} to i64')
                        self.emit(f'call void @wysna_llvm_box_int(ptr %_out_ret, i64 {val})')
                    elif rslot.type_id == TYPE_FLOAT:
                        self.emit(f'call void @wysna_llvm_box_float(ptr %_out_ret, double {self.load(term.value)})')
                    elif rslot.type_id == TYPE_BOOL:
                        b32 = self.result(f'zext i1 {self.load(term.value)} to i32')
                        self.emit(f'call void @wysna_llvm_box_bool(ptr %_out_ret, i32 {b32})')
                    for slot in function.slots:
                        if slot.storage == 'value':
                            self.emit(f'call void @wysna_llvm_release(ptr {self.pointer(slot.id)})')
                    self.emit('store i32 0, ptr %_out_status, align 4')
                    self.emit('ret void')
                else:
                    for slot in function.slots:
                        if slot.storage == 'value':
                            self.emit(f'call void @wysna_llvm_release(ptr {self.pointer(slot.id)})')
                    self.fail('W9999', 'Unreachable MIR block executed', term.source)

        self.lines.extend(['cps_default:', '  store i32 0, ptr %_out_status, align 4', '  ret void', '}', ''])
        step_lines = self.lines

        parameters = ['ptr %out'] + [SCALAR_TYPES.get(function.slots[i].type_id, 'ptr') + f' %arg{i}' for i in function.params]
        cc = 'fastcc ' if function.name != 'wysna_program' else ''
        loc = self.debug.location(function.source, self.scope, self.scope_file)
        self.lines = [
            f'define internal {cc}void @{function.name}(' + ', '.join(parameters) + f') nounwind mustprogress !dbg !{self.scope} {{',
            'entry:',
        ]
        if function.params:
            p_count = len(function.params)
            self.emit(f'%_p_idx = alloca [{p_count} x i64], align 8')
            self.emit(f'%_p_vals = alloca [{p_count} x %WysnaValue], align {ALIGNMENT}')
            for idx, p_id in enumerate(function.params):
                pslot = self.slots[p_id]
                i_ptr = self.result(f'getelementptr [{p_count} x i64], ptr %_p_idx, i32 0, i32 {idx}')
                self.emit(f'store i64 {p_id}, ptr {i_ptr}, align 8')
                v_ptr = self.result(f'getelementptr [{p_count} x %WysnaValue], ptr %_p_vals, i32 0, i32 {idx}')
                if pslot.storage == 'value':
                    val = self.result(f'load %WysnaValue, ptr %arg{p_id}')
                    self.emit(f'store %WysnaValue {val}, ptr {v_ptr}, align {ALIGNMENT}')
                elif pslot.type_id == TYPE_INT:
                    self.emit(f'store %WysnaValue zeroinitializer, ptr {v_ptr}')
                    self.emit(f'call void @wysna_llvm_box_int(ptr {v_ptr}, i64 %arg{p_id})')
                elif pslot.type_id == TYPE_FLOAT:
                    self.emit(f'store %WysnaValue zeroinitializer, ptr {v_ptr}')
                    self.emit(f'call void @wysna_llvm_box_float(ptr {v_ptr}, double %arg{p_id})')
                elif pslot.type_id == TYPE_BOOL:
                    self.emit(f'store %WysnaValue zeroinitializer, ptr {v_ptr}')
                    b32 = self.result(f'zext i1 %arg{p_id} to i32')
                    self.emit(f'call void @wysna_llvm_box_bool(ptr {v_ptr}, i32 {b32})')
            self.emit(f'call void @wysna_llvm_coro_spawn(ptr %out, ptr @{step_fn}, i64 {len(function.slots)}, i64 {p_count}, ptr %_p_idx, ptr %_p_vals)')
        else:
            self.emit(f'call void @wysna_llvm_coro_spawn(ptr %out, ptr @{step_fn}, i64 {len(function.slots)}, i64 0, ptr null, ptr null)')
        self.lines.append('  ret void' + loc)
        self.lines.extend(['}', ''])
        return step_lines + self.lines

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
                utf8_len = len(value.encode('utf-8'))
                if utf8_len > 6:
                    cache = '@literal' + str(len(self.pool))
                    self.pool.append(cache + ' = internal global [' + str(LITERAL_BYTES) + ' x i8] zeroinitializer, align ' + str(ALIGNMENT))
                self.emit('call void @wysna_llvm_literal(ptr ' + self.pointer(result) + ', ptr ' + cache +
                          ', ptr ' + self.string(value) + ', i64 ' + str(utf8_len) + ')')
            elif value is None and type_ != TYPE_UNIT and not data.get('placeholder'):
                self.emit('call void @wysna_llvm_none(ptr ' + self.pointer(result) + ')')
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
        elif kind in ('clone', 'narrow', 'store'):
            if self.slots[result].storage == 'value':
                method = 'move' if operation.moves else 'clone'
                self.emit('call void @wysna_llvm_' + method + '(ptr ' + self.pointer(result) + ', ptr ' + self.boxed(args[0]) + ')')
                self.release_boxed(args[0], moved=bool(operation.moves))
            else:
                if self.slots[args[0]].storage == 'value' and self.slots[args[0]].type_id != self.slots[result].type_id:
                    self.managed.complete(result, self.pointer(args[0]))
                    return
                source_type = self.physical(args[0]) if self.slots[args[0]].storage != 'value' else TYPES[self.slots[args[0]].type_id]
                self.write(result, self.load(args[0]), source_type)
        elif kind == 'release':
            self.emit('call void @wysna_llvm_release(ptr ' + self.pointer(args[0]) + ')')
        elif kind == 'store_place':
            destination = data['place'].root
            if not data['place'].projections and self.slots[destination].storage != 'value':
                self.write(destination, self.load(args[0]))
            else:
                reference = self.managed.place(data['place'])
                self.emit('call void @wysna_llvm_move(ptr ' + reference + ', ptr ' + self.boxed(args[0]) + ')')
        elif kind == 'call':
            if data['name'] in self.module.externs:
                values = self.managed.arguments(args)
                output = self.managed.output(result)
                self.emit('call void @wysna_llvm_ffi_' + data['name'] + '(ptr ' + output + ', ptr ' + values + ')')
                self.managed.release_arguments(args)
                self.managed.complete(result, output)
                return
            function = self.functions[data['name']]
            parameters = []
            boxed_args = []
            if is_managed(function.return_type):
                parameters.append('ptr ' + self.pointer(result))
            for position, (index, parameter) in enumerate(zip(args, function.params)):
                type_ = function.slots[parameter].type_id
                if type_ in SCALAR_TYPES:
                    if self.slots[index].type_id == type_:
                        value = self.load(index)
                    else:
                        method = {TYPE_INT: 'int', TYPE_FLOAT: 'float', TYPE_BOOL: 'bool'}[type_]
                        loaded_type = 'i32' if type_ == TYPE_BOOL else SCALAR_TYPES[type_]
                        value = self.result('call ' + loaded_type + ' @wysna_llvm_' + method + '(ptr ' + self.boxed(index, position) + ')')
                        boxed_args.append((index, position))
                        if type_ == TYPE_BOOL:
                            value = self.result('icmp ne i32 ' + value + ', 0')
                else:
                    value = self.boxed(index, position)
                    boxed_args.append((index, position))
                parameters.append(SCALAR_TYPES.get(type_, 'ptr') + ' ' + value)
            returned = SCALAR_TYPES.get(function.return_type, 'void')
            cc = 'fastcc ' if function.name != 'wysna_program' else ''
            call = 'call ' + cc + returned + ' @' + function.name + '(' + ', '.join(parameters) + ')'
            if returned == 'void':
                self.emit(call)
            else:
                self.write(result, self.result(call))
            for index, position in boxed_args:
                self.release_boxed(index, position)
        elif kind == 'runtime':
            self.runtime(operation)
        elif kind == 'test':
            if data['kind'] == 'truth':
                self.write(result, self.load(args[0]))
            elif data['kind'] == 'equal':
                self.compare(operation, 'wysna_equal')
            else:
                self.managed.operation(operation)
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
            left = self.load(args[0])
            right = self.load(args[1]) if data['range'] else self.result('call i32 @wysna_llvm_iteration_length(ptr ' + self.boxed(args[1]) + ', ' + self.managed.location(operation.source) + ')')
            self.write(result, self.result('icmp slt i32 ' + left + ', ' + right))
        elif kind == 'iterate_value' and data['range']:
            start = self.load(args[0])
            counter = self.result('sext i32 ' + self.load(args[1]) + ' to i64')
            self.write(result, self.result('add i64 ' + start + ', ' + counter))
        else:
            self.managed.operation(operation)

    def compare(self, operation, name):
        args = operation.args
        type_ = self.slots[args[0]].type_id
        codes = {'wysna_equal': 0, 'wysna_not_equal': 1, 'wysna_lt': 2, 'wysna_le': 3, 'wysna_gt': 4, 'wysna_ge': 5}
        if type_ not in SCALAR_TYPES or type_ != self.slots[args[1]].type_id:
            left, right = self.boxed(args[0], 0), self.boxed(args[1], 1)
            value = self.result('call i32 @wysna_llvm_compare(ptr ' + left + ', ptr ' + right + ', i32 ' + str(codes[name]) + ')')
            self.release_boxed(args[0], 0)
            self.release_boxed(args[1], 1)
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
            for offset, index in enumerate(args):
                self.release_boxed(index, offset)
        elif name == 'wysna_builtin_exit':
            self.emit('call void @wysna_llvm_exit(i64 ' + self.load(args[0]) + ')')
        elif name in CONVERSIONS:
            arg_type = self.slots[args[0]].type_id
            if name == 'wysna_builtin_float' and arg_type == TYPE_INT:
                value = self.result('sitofp i64 ' + self.load(args[0]) + ' to double')
                self.write(result, value)
            elif name == 'wysna_builtin_int' and arg_type == TYPE_FLOAT:
                value = self.result('fptosi double ' + self.load(args[0]) + ' to i64')
                self.write(result, value)
            elif name == 'wysna_builtin_int' and arg_type == TYPE_INT:
                self.write(result, self.load(args[0]))
            elif name == 'wysna_builtin_float' and arg_type == TYPE_FLOAT:
                self.write(result, self.load(args[0]))
            else:
                source = self.boxed(args[0])
                out = self.pointer(result)
                if self.slots[result].storage != 'value':
                    out = self.cell(1)
                    self.emit('store %WysnaValue zeroinitializer, ptr ' + out)
                self.emit('call void @wysna_llvm_convert(ptr ' + out + ', ptr ' + source + ', i32 ' + str(CONVERSIONS[name]) + ')')
                self.release_boxed(args[0], 0)
                if self.slots[result].storage != 'value':
                    method = 'int' if self.slots[result].type_id == TYPE_INT else 'float'
                    value = self.result('call ' + TYPES[self.slots[result].type_id] + ' @wysna_llvm_' + method + '(ptr ' + out + ')')
                    if self.slots[result].type_id == TYPE_INT:
                        self.emit('call void @wysna_llvm_release(ptr ' + out + ')')
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
        elif name not in ('wysna_add', 'wysna_sub', 'wysna_mul', 'wysna_div_at', 'wysna_mod_at'):
            self.managed.runtime(operation)
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
        weights = ', !prof !' + str(self.debug.cold_branch_weights())
        self.emit('br i1 ' + zero + ', label %' + fault + ', label %' + resume + weights)
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
