"""C99 emission from verified MIR only; no HIR traversal or semantic resolution."""
import math
from checker.TypeInterner import TYPE_UNIT, TYPE_INT, TYPE_U8, TYPE_BOOL, TYPE_FLOAT, TYPE_STR
from codegen.CStrings import quote
from common.Telemetry import CodegenTelemetry
from mir.AsyncLowering import build_cps_plan
from mir.Nodes import Place
from mir.Verifier import Verifier
from compiler.StdlibIdentity import stdlib_declaration


class CCodeGen:
    def __init__(self, interner, *, emit_line_directives=False):
        self.interner = interner
        self.emit_line_directives = bool(emit_line_directives)
        self.abi_exports = []

    def generate(self, module):
        Verifier(self.interner).verify(module)
        self.literal_counter = 0
        self.module = module
        self.functions = {function.name: function for function in module.functions}
        self.layouts = module.layouts
        classes = [layout for layout in module.layouts.values() if layout.kind == 'class']
        lines = ['#include "runtime/wysna_runtime.h"', '/* Verified MIR: explicit CFG, places and ownership. */', '']
        lines += [f'static WVal {slot.name};' for slot in module.globals]
        self.layout_key_symbols = {}
        for idx, (layout_id, layout) in enumerate(module.layouts.items()):
            if layout.kind in ('struct', 'class') and layout.fields:
                sym = f'wysna_keys_{idx}'
                self.layout_key_symbols[layout_id] = sym
                keys_literal = ', '.join(quote(name) for name, _ in layout.fields)
                lines.append(f'static const char* const {sym}[{len(layout.fields)}] = {{ {keys_literal} }};')
        lines += [f'static const WClassVTable wysna_vtable_{layout.name};' for layout in classes]
        for function in module.functions:
            params = ', '.join(self.ctype(function.slots[index].type_id) + ' ' + function.slots[index].name for index in function.params) or 'void'
            lines.append(f'static {self.ctype(function.return_type)} {function.name}({params});')
        if module.library_mode:
            lines.extend(['static bool wysna_web_initialized = false;',
                          'static bool wysna_web_destroyed = false;'])
        wrappers = []
        for name, external in module.externs.items():
            from codegen.ForeignAbi import foreign_wrapper
            prototype, wrapper = foreign_wrapper(name, external['native'], external['signature'])
            lines.extend([prototype, f'static WVal {name}(' + (', '.join('WVal v_' + str(i) for i in range(len(external['params']))) or 'void') + ');'])
            wrappers.append(wrapper)
        adapters = {}
        for function in module.functions:
            for block in function.blocks:
                for operation in block.operations:
                    if operation.opcode == 'function':
                        adapters[operation.data['name']] = operation.data
        for name, data in adapters.items():
            raw_arguments = (['environment'] if data['captured'] else []) + [f'argv[{index}]' for index in range(data['arity'])]
            arguments, returned, boxed = self.abi_call(name, raw_arguments)
            expression = name + '(' + ', '.join(arguments) + ')'
            if not boxed:
                expression = self.box_expression(expression, returned)
            lines.append(f'static WVal wysna_adapter_{name}(WVal environment, int32_t argc, const WVal* argv) {{')
            lines.append('    (void)environment; (void)argv;')
            lines.append(f'    if (argc != {data["arity"]}) wysna_runtime_fail("W4003", "Call arity mismatch");')
            lines.append(f'    return {expression};\n}}')
        lines.extend(wrappers)
        for function in module.functions:
            lines.extend(self.function(function))
        if module.library_mode:
            lines.extend(self.browser_exports(module))
        emitted = set()
        for layout in classes:
            entries = []
            for name, arity in module.virtual_slots:
                method = layout.methods.get(name)
                if method is None or len(method[1]) != arity:
                    entries.append('NULL')
                    continue
                owner, parameters, returned = method
                adapter = f'wysna_vinvoke_{owner}__{name}'
                entries.append(adapter)
                if adapter not in emitted:
                    emitted.add(adapter)
                    target = f'wysna_method_{owner}__{name}'
                    arguments, returned, boxed = self.abi_call(target, ['receiver'] + [f'argv[{index}]' for index in range(arity)])
                    call = target + '(' + ', '.join(arguments) + ')'
                    if not boxed:
                        call = self.box_expression(call, returned)
                    lines.append(f'static WVal {adapter}(WVal receiver, int32_t argc, const WVal* argv) {{ (void)argc; (void)argv; return {call}; }}')
            if entries:
                lines.append(f'static const WInvoke wysna_slots_{layout.name}[{len(entries)}] = {{ ' + ', '.join(entries) + ' };')
                lines.append(f'static const WClassVTable wysna_vtable_{layout.name} = {{ {len(entries)}, wysna_slots_{layout.name} }};')
            else:
                lines.append(f'static const WClassVTable wysna_vtable_{layout.name} = {{ 0, NULL }};')
        entry_call = self.box_expression('wysna_program()', self.functions['wysna_program'].return_type)
        if module.library_mode:
            lines.extend(['int32_t wysna_web_init(void) {',
                          '    if (wysna_web_destroyed) return 2;',
                          '    if (wysna_web_initialized) return 1;',
                          '    wysna_init_args(0, NULL);',
                          '    WVal _t_init = ' + entry_call + ';',
                          '    if (wysna_is_err(_t_init)) {',
                          '        wysna_release(_t_init);',
                          '        wysna_runtime_cleanup();',
                          '        wysna_web_destroyed = true;',
                          '        return 3;',
                          '    }',
                          '    wysna_release(_t_init);',
                          '    wysna_web_initialized = true;',
                          '    return 0;',
                          '}',
                          'void wysna_web_destroy(void) {',
                          '    if (wysna_web_destroyed) return;'])
            lines.extend(f'    wysna_release({slot.name});' for slot in reversed(module.globals))
            lines.extend(['    wysna_web_initialized = false;',
                          '    wysna_web_destroyed = true;',
                          '    wysna_runtime_cleanup();',
                          '}'])
        else:
            lines.extend(['int main(int argc, char** argv) {', '    wysna_init_args(argc, argv);',
                          '    WVal _t_main = ' + entry_call + ';',
                          '    int status = wysna_entry_status(_t_main);',
                          '    wysna_release(_t_main);'])
            lines.extend(f'    wysna_release({slot.name});' for slot in reversed(module.globals))
            lines.extend(['    wysna_runtime_cleanup();', '    return status;', '}'])
        source = '\n'.join(lines) + '\n'
        self.telemetry = CodegenTelemetry(len(module.functions) + len(wrappers) + len(adapters),
                                          len([layout for layout in module.layouts.values() if layout.kind == 'struct']),
                                          module.enum_count, len(classes), len(source.splitlines()), len(source.encode()))
        return source

    def browser_exports(self, module):
        """Emit a generated C ABI for explicitly public scalar and UTF-8 functions."""
        allowed = {TYPE_INT: 'int', TYPE_FLOAT: 'float', TYPE_BOOL: 'bool',
                   TYPE_UNIT: 'unit', TYPE_STR: 'str'}
        exports = sorted(module.exports, key=lambda item: item['name'])
        lines = ['static bool wysna_web_entered = false;',
                 'static bool wysna_web_enter(void) {',
                 '    if (!wysna_web_initialized || wysna_web_destroyed || wysna_web_entered) return false;',
                 '    wysna_web_entered = true;',
                 '    return true;',
                 '}',
                 'static void wysna_web_leave(void) { wysna_web_entered = false; }',
                 'uint32_t wysna_web_alloc(uint32_t size) {',
                 '    if (size == 0) size = 1;',
                 '    return (uint32_t)(uintptr_t)malloc((size_t)size);',
                 '}',
                 'void wysna_web_free(uint32_t pointer) { free((void*)(uintptr_t)pointer); }']
        for index, export in enumerate(exports):
            function = self.functions.get(export['function'])
            if function is None or export['fallible']:
                continue
            param_types = [function.slots[param_id].type_id for param_id in function.params]
            return_type = function.return_type
            if any(type_id not in allowed for type_id in param_types) or return_type not in allowed:
                continue
            if any(type_id == TYPE_UNIT for type_id in param_types):
                continue

            name = f'wysna_web_call_{index}'
            signature = []
            call_arguments = []
            setup = []
            cleanup = []
            precheck = []
            for param_index, type_id in enumerate(param_types):
                if type_id == TYPE_STR:
                    pointer = f'arg{param_index}_pointer'
                    length = f'arg{param_index}_length'
                    value = f'arg{param_index}'
                    signature.extend([f'uint32_t {pointer}', f'uint32_t {length}'])
                    precheck.extend([
                        f'    if ({pointer} == 0 && {length} != 0) return 1;',
                        f'    if ({length} == UINT32_MAX) return 1;',
                    ])
                    prior_cleanup = ' '.join(cleanup)
                    setup.extend([
                        f'    char* copy{param_index} = (char*)malloc((size_t){length} + 1);',
                        f'    if (!copy{param_index}) {{ {prior_cleanup} wysna_web_leave(); return 2; }}',
                        f'    if ({length}) memcpy(copy{param_index}, (const void*)(uintptr_t){pointer}, (size_t){length});',
                        f'    copy{param_index}[{length}] = 0;',
                        f'    WVal {value} = wysna_owned_str_n(copy{param_index}, (size_t){length});',
                    ])
                    cleanup.append(f'    wysna_release({value});')
                    call_arguments.append(value)
                elif type_id == TYPE_BOOL:
                    value = f'arg{param_index}'
                    signature.append(f'uint32_t {value}')
                    call_arguments.append(f'({value} != 0)')
                elif type_id == TYPE_INT:
                    value = f'arg{param_index}'
                    signature.append(f'int64_t {value}')
                    call_arguments.append(value)
                elif type_id == TYPE_FLOAT:
                    value = f'arg{param_index}'
                    signature.append(f'double {value}')
                    call_arguments.append(value)
            output_type = allowed[return_type]
            if output_type != 'unit':
                signature.append('uint32_t output_pointer')
                precheck.append('    if (output_pointer == 0) return 1;')
            lines.append(f'int32_t {name}({", ".join(signature) if signature else "void"}) {{')
            lines.extend(precheck)
            lines.append('    if (!wysna_web_enter()) return 3;')
            lines.extend(setup)
            call = export['function'] + '(' + ', '.join(call_arguments) + ')'
            if output_type == 'unit':
                lines.append(f'    {call};')
            elif output_type == 'str':
                lines.extend([
                    f'    WVal returned = {call};',
                    '    if (wysna_tag(returned) != W_STR) { wysna_release(returned); ' + ' '.join(cleanup) + ' wysna_web_leave(); return 4; }',
                    '    char ret_scratch[7];',
                    '    size_t ret_len = 0;',
                    '    const char* ret_data = wysna_str_bytes(returned, ret_scratch, &ret_len);',
                    '    if (ret_len > UINT32_MAX) { wysna_release(returned); ' + ' '.join(cleanup) + ' wysna_web_leave(); return 5; }',
                    '    uint32_t length = (uint32_t)ret_len;',
                    '    uint32_t pointer = length ? wysna_web_alloc(length) : 0;',
                    '    if (length && pointer == 0) { wysna_release(returned); ' + ' '.join(cleanup) + ' wysna_web_leave(); return 2; }',
                    '    if (length) memcpy((void*)(uintptr_t)pointer, ret_data, length);',
                    '    ((uint32_t*)(uintptr_t)output_pointer)[0] = pointer;',
                    '    ((uint32_t*)(uintptr_t)output_pointer)[1] = length;',
                    '    wysna_release(returned);',
                ])
            else:
                c_type = {'int': 'int64_t', 'float': 'double', 'bool': 'uint32_t'}[output_type]
                expression = {'int': call, 'float': call,
                              'bool': f'({call} ? 1u : 0u)'}[output_type]
                lines.extend([
                    f'    {c_type} returned = {expression};',
                    f'    *(({c_type}*)(uintptr_t)output_pointer) = returned;',
                ])
            lines.extend(cleanup)
            lines.extend(['    wysna_web_leave();', '    return 0;', '}'])
            self.abi_exports.append({
                'name': export['name'], 'symbol': name,
                'params': [allowed[type_id] for type_id in param_types],
                'return': output_type,
            })
        return lines

    @staticmethod
    def ctype(type_id):
        return {TYPE_INT: 'int64_t', TYPE_U8: 'uint8_t', TYPE_FLOAT: 'double', TYPE_BOOL: 'bool'}.get(type_id, 'WVal')

    @staticmethod
    def box_expression(expression, type_id):
        constructor = {TYPE_INT: 'wysna_int', TYPE_U8: 'wysna_int', TYPE_FLOAT: 'wysna_float', TYPE_BOOL: 'wysna_bool'}.get(type_id)
        return constructor + '(' + expression + ')' if constructor else expression

    @staticmethod
    def unbox_expression(expression, type_id, consume=False):
        if consume and type_id == TYPE_INT:
            return 'wysna_consume_int(' + expression + ')'
        if consume and type_id == TYPE_U8:
            return 'wysna_consume_u8(' + expression + ')'
        extractor = {TYPE_INT: 'wysna_as_int', TYPE_U8: 'wysna_as_u8', TYPE_FLOAT: 'wysna_as_float', TYPE_BOOL: 'wysna_as_bool'}.get(type_id)
        return extractor + '(' + expression + ')' if extractor else expression

    def value(self, index, move=False):
        slot = self.slots[index]
        if slot.storage == 'value':
            return f'wysna_move(&{self.name(index)})' if move else self.name(index)
        if slot.type_id == TYPE_INT and not move:
            return f'wysna_borrow_int({self.name(index)})'
        return self.box_expression(self.name(index), slot.type_id)

    def scalar(self, index):
        slot = self.slots[index]
        return self.unbox_expression(self.name(index), slot.type_id) if slot.storage == 'value' else self.name(index)

    def abi_call(self, name, boxed_arguments, arg_ids=None):
        function = self.functions.get(name)
        if function is None:
            return boxed_arguments, self.module.externs[name]['return_type'], True
        parameters = [function.slots[index].type_id for index in function.params]
        arguments = []
        for i, (boxed_val, param_type) in enumerate(zip(boxed_arguments, parameters)):
            if arg_ids is not None:
                arg_id = arg_ids[i]
                arg_slot = self.slots[arg_id]
                if arg_slot.storage != 'value' and self.ctype(param_type) != 'WVal':
                    arguments.append(self.name(arg_id))
                    continue
            arguments.append(self.unbox_expression(boxed_val, param_type))
        returned = function.return_type
        return arguments, returned, self.ctype(returned) == 'WVal'

    def name(self, index):
        slot = self.slots[index]
        return slot.name if slot.parameter or index < 0 else f'_m{index}'

    def array(self, arguments):
        return '(WVal[]){ ' + ', '.join(arguments) + ' }' if arguments else 'NULL'

    @staticmethod
    def _parallel_runtime_helper(function):
        for name, arity in (('parallel_map', 2), ('parallel_batch', 3), ('parallel_filter', 2)):
            if stdlib_declaration(function, 'algorithms', name, arity):
                return 'wysna_' + name
        return None

    @staticmethod
    def _async_runtime_helper(function):
        if stdlib_declaration(function, 'async', '_spawn_handle', 1):
            return 'wysna_async_spawn_handle'
        if stdlib_declaration(function, 'async', '_take_task_value', 2):
            return 'wysna_spawn_take_option'
        return None

    @staticmethod
    def _display_fn_name(name):
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
        params = ', '.join(self.ctype(function.slots[index].type_id) + ' ' + self.name(index) for index in function.params) or 'void'
        lines = [f'static {self.ctype(function.return_type)} {function.name}({params}) {{']
        for name in ('group_cancelled', 'cancel_group'):
            if stdlib_declaration(function, 'async', name, 0):
                return lines + [f'    return wysna_{name}();', '}', '']
        if stdlib_declaration(function, 'async', '_run_task_group', 5):
            arguments = ', '.join(self.name(index) for index in function.params)
            return lines + [f'    return wysna_run_task_group({arguments});', '}', '']
        async_helper = self._async_runtime_helper(function)
        if async_helper is not None:
            p_names = [self.name(index) for index in function.params]
            if async_helper == 'wysna_async_spawn_handle' and len(p_names) == 1:
                if self.ctype(function.return_type) == 'WVal':
                    lines.append(f'    return wysna_int(wysna_async_spawn_handle({p_names[0]}));')
                else:
                    lines.append(f'    return wysna_async_spawn_handle({p_names[0]});')
                lines.extend(['}', ''])
                return lines
            if async_helper == 'wysna_spawn_take_option' and len(p_names) == 2 and self.ctype(function.return_type) == 'WVal':
                p0_slot = self.slots[function.params[0]]
                p0_boxed = p_names[0] if p0_slot.storage == 'value' else (
                    f'wysna_borrow_int({p_names[0]})' if p0_slot.type_id == TYPE_INT
                    else self.box_expression(p_names[0], p0_slot.type_id)
                )
                lines.append(f'    return wysna_spawn_take_option({p0_boxed}, {p_names[1]});')
                lines.extend(['}', ''])
                return lines
        parallel_helper = self._parallel_runtime_helper(function)
        if parallel_helper is not None and self.ctype(function.return_type) == 'WVal':
            p_names = [self.name(index) for index in function.params]
            if parallel_helper == 'wysna_parallel_batch':
                b_arg = f'wysna_as_int({p_names[1]})' if self.slots[function.params[1]].storage == 'value' else p_names[1]
                call = f'{parallel_helper}({p_names[0]}, {b_arg}, {p_names[2]})'
            else:
                call = f'{parallel_helper}({p_names[0]}, {p_names[1]})'
            if self.interner.get_entry(function.return_type).data == TYPE_U8:
                call = f'wysna_pack_u8_owned({call})'
            lines.append(f'    return {call};')
            lines.extend(['}', ''])
            return lines
        track_frame = function.name.startswith('wysna_fn_')
        for slot in function.slots:
            if slot.parameter:
                continue
            type_ = {'value': 'WVal', 'predicate': 'bool', 'counter': 'int32_t', 'integer': 'int64_t', 'float': 'double', 'boolean': 'bool'}[slot.storage]
            if slot.type_id == TYPE_U8 and slot.storage == 'integer':
                type_ = 'uint8_t'
            initial = 'wysna_unit()' if slot.storage == 'value' else '0'
            lines.append(f'    {type_} {self.name(slot.id)} = {initial};')
        if track_frame:
            fn_src = getattr(function, 'source', None)
            fn_file = getattr(fn_src, 'file', '') or '<unknown>'
            fn_line = int(getattr(fn_src, 'line', 0) or 0)
            lines.append(
                f'    wysna_runtime_push_frame({quote(self._display_fn_name(function.name))}, '
                f'{quote(fn_file)}, {fn_line});'
            )
        lines.append('    goto bb0;')
        back_edge_targets = {
            target
            for b in function.blocks
            for target in b.terminator.targets
            if target <= b.id
        }
        last_source = None
        for block in function.blocks:
            lines.append(f'bb{block.id}:;')
            if block.id == 0 or block.id in back_edge_targets:
                lines.append('    wysna_step_tick();')
            for operation in block.operations:
                if self.emit_line_directives:
                    src = getattr(operation, 'source', None)
                    src_file = getattr(src, 'file', '') if src is not None else ''
                    src_line = int(getattr(src, 'line', 0) or 0) if src is not None else 0
                    if src_line > 0 and src_file and src_file != '<unknown>':
                        key = (src_file, src_line)
                        if key != last_source:
                            lines.append(f'#line {src_line} {quote(src_file)}')
                            last_source = key
                lines.extend('    ' + line for line in self.operation(operation))
            term = block.terminator
            if term.kind == 'goto':
                lines.append(f'    goto bb{term.targets[0]};')
            elif term.kind == 'branch':
                slot = self.slots[term.value]
                condition = self.scalar(term.value)
                lines.append(f'    if ({condition}) goto bb{term.targets[0]}; else goto bb{term.targets[1]};')
            elif term.kind == 'return':
                slot = self.slots[term.value]
                if slot.storage == 'value':
                    expression = f'wysna_move(&{self.name(term.value)})'
                else:
                    expression = self.name(term.value)
                if track_frame:
                    lines.append('    wysna_runtime_pop_frame();')
                lines.append(f'    return {expression};')
            else:
                lines.append('    wysna_runtime_fail("W9999", "Unreachable MIR block executed");')
                default_ret = 'wysna_unit()' if self.ctype(function.return_type) == 'WVal' else '0'
                lines.append(f'    return {default_ret};')
        lines.extend(['}', ''])
        return lines

    def _emit_coro_assign_from_wval(self, lines, dest_slot_id, wval_var, indent='    '):
        slot = self.slots[dest_slot_id]
        dname = self.name(dest_slot_id)
        if slot.storage == 'value':
            lines.append(f'{indent}wysna_release({dname}); {dname} = {wval_var}; {wval_var} = wysna_unit();')
        elif slot.type_id == TYPE_INT:
            cast = '(int32_t)' if slot.storage == 'counter' else ''
            lines.append(f'{indent}{dname} = {cast}wysna_consume_int({wval_var}); {wval_var} = wysna_unit();')
        elif slot.type_id == TYPE_FLOAT:
            lines.append(f'{indent}{dname} = wysna_as_float({wval_var}); wysna_release({wval_var}); {wval_var} = wysna_unit();')
        elif slot.type_id == TYPE_BOOL:
            lines.append(f'{indent}{dname} = wysna_as_bool({wval_var}); wysna_release({wval_var}); {wval_var} = wysna_unit();')

    def _emit_coro_flush_slots(self, lines, function, indent='        '):
        for slot in function.slots:
            sname = self.name(slot.id)
            if slot.storage == 'value':
                lines.append(f'{indent}_frame_slots[{slot.id}] = wysna_move(&{sname});')
            elif slot.type_id == TYPE_INT:
                lines.append(f'{indent}_frame_slots[{slot.id}] = wysna_int((int64_t){sname});')
            elif slot.type_id == TYPE_FLOAT:
                lines.append(f'{indent}_frame_slots[{slot.id}] = wysna_float({sname});')
            elif slot.type_id == TYPE_BOOL:
                lines.append(f'{indent}_frame_slots[{slot.id}] = wysna_bool({sname});')

    def _async_function(self, function):
        self.slots = {slot.id: slot for slot in self.module.globals}
        self.slots.update({slot.id: slot for slot in function.slots})
        plan = build_cps_plan(function)
        step_name = f'wysna_coro_step_{function.name}'
        lines = [
            f'static WVal {step_name}(WVal* _frame_slots, int64_t _slot_count, int64_t* _frame_state, WVal _resume_val, bool _has_resume, WVal* _out_await_box, int64_t* _out_await_handle, int32_t* _out_status) {{',
            '    (void)_slot_count;',
            '    (void)_has_resume;',
            '    (void)_out_await_box;',
            '    (void)_out_await_handle;',
        ]
        if not function.slots:
            lines.append('    (void)_frame_slots;')
        for slot in function.slots:
            sname = self.name(slot.id)
            if slot.storage == 'value':
                lines.append(f'    WVal {sname} = _frame_slots[{slot.id}]; _frame_slots[{slot.id}] = wysna_unit();')
            elif slot.type_id == TYPE_INT:
                ctype = 'int32_t' if slot.storage == 'counter' else 'int64_t'
                lines.append(f'    {ctype} {sname} = (wysna_tag(_frame_slots[{slot.id}]) == W_INT) ? ({ctype})wysna_as_int(_frame_slots[{slot.id}]) : 0;')
                lines.append(f'    _frame_slots[{slot.id}] = wysna_unit();')
            elif slot.type_id == TYPE_FLOAT:
                lines.append(f'    double {sname} = (wysna_tag(_frame_slots[{slot.id}]) == W_FLOAT) ? wysna_as_float(_frame_slots[{slot.id}]) : 0.0;')
                lines.append(f'    _frame_slots[{slot.id}] = wysna_unit();')
            elif slot.type_id == TYPE_BOOL:
                lines.append(f'    bool {sname} = (wysna_tag(_frame_slots[{slot.id}]) == W_BOOL) ? wysna_as_bool(_frame_slots[{slot.id}]) : false;')
                lines.append(f'    _frame_slots[{slot.id}] = wysna_unit();')

        lines.append('    switch (*_frame_state) {')
        lines.append('        case 0: goto bb0;')
        for sp in plan.suspend_points:
            lines.append(f'        case {sp.state_id}: goto {sp.resume_label};')
        lines.append('        default: *_out_status = WYSNA_CORO_DONE; wysna_release(_resume_val); return wysna_unit();')
        lines.append('    }')

        for seg in plan.segments:
            lines.append(f'{seg.label}:;')
            if seg.loop_head:
                lines.append('    wysna_step_tick();')
            for op in seg.operations:
                lines.extend('    ' + line for line in self.operation(op))
            if seg.suspend is not None:
                sp = seg.suspend
                arg_expr = self.value(sp.await_arg, move=True)
                lines.append('    {')
                lines.append(f'        WVal _aw_box_{sp.state_id} = {arg_expr};')
                lines.append(f'        int64_t _aw_handle_{sp.state_id} = 0;')
                lines.append(f'        WVal _aw_imm_{sp.state_id} = wysna_unit();')
                lines.append(f'        if (wysna_coro_try_resolve_await(&_aw_box_{sp.state_id}, &_aw_handle_{sp.state_id}, &_aw_imm_{sp.state_id})) {{')
                self._emit_coro_assign_from_wval(lines, sp.await_res, f'_aw_imm_{sp.state_id}', indent='            ')
                lines.append(f'            goto {sp.continue_label};')
                lines.append('        }')
                lines.append(f'        *_frame_state = {sp.state_id};')
                lines.append(f'        *_out_await_box = _aw_box_{sp.state_id};')
                lines.append(f'        *_out_await_handle = _aw_handle_{sp.state_id};')
                lines.append('        *_out_status = WYSNA_CORO_SUSPENDED;')
                self._emit_coro_flush_slots(lines, function, indent='        ')
                lines.append('        wysna_release(_resume_val);')
                lines.append('        return wysna_unit();')
                lines.append('    }')
                lines.append(f'{sp.resume_label}:;')
                self._emit_coro_assign_from_wval(lines, sp.await_res, '_resume_val', indent='    ')
                lines.append(f'    goto {sp.continue_label};')
            else:
                term = seg.terminator
                if term.kind == 'goto':
                    lines.append(f'    goto bb{term.targets[0]};')
                elif term.kind == 'branch':
                    condition = self.scalar(term.value)
                    lines.append(f'    if ({condition}) goto bb{term.targets[0]}; else goto bb{term.targets[1]};')
                elif term.kind == 'return':
                    rslot = self.slots[term.value]
                    if rslot.storage == 'value':
                        ret_expr = f'wysna_clone({self.name(term.value)})' if rslot.parameter else f'wysna_move(&{self.name(term.value)})'
                    else:
                        ret_expr = self.box_expression(self.name(term.value), rslot.type_id)
                    lines.append('    {')
                    lines.append(f'        WVal _coro_ret = {ret_expr};')
                    for s in function.slots:
                        if s.storage == 'value':
                            lines.append(f'        wysna_release({self.name(s.id)}); {self.name(s.id)} = wysna_unit();')
                    lines.append('        wysna_release(_resume_val);')
                    lines.append('        *_out_status = WYSNA_CORO_DONE;')
                    lines.append('        return _coro_ret;')
                    lines.append('    }')
                else:
                    for s in function.slots:
                        if s.storage == 'value':
                            lines.append(f'    wysna_release({self.name(s.id)}); {self.name(s.id)} = wysna_unit();')
                    lines.append('    wysna_release(_resume_val);')
                    lines.append('    wysna_runtime_fail("W9999", "Unreachable MIR block executed");')
                    lines.append('    *_out_status = WYSNA_CORO_DONE;')
                    lines.append('    return wysna_unit();')

        lines.append('    *_out_status = WYSNA_CORO_DONE;')
        lines.append('    wysna_release(_resume_val);')
        lines.append('    return wysna_unit();')
        lines.extend(['}', ''])

        params = ', '.join(self.ctype(function.slots[index].type_id) + ' ' + self.name(index) for index in function.params) or 'void'
        lines.append(f'static {self.ctype(function.return_type)} {function.name}({params}) {{')
        slot_count = len(function.slots)
        if function.params:
            p_count = len(function.params)
            lines.append(f'    int64_t _p_idx[{p_count}];')
            lines.append(f'    WVal _p_vals[{p_count}];')
            for i, p_id in enumerate(function.params):
                pslot = self.slots[p_id]
                lines.append(f'    _p_idx[{i}] = {p_id};')
                if pslot.storage == 'value':
                    lines.append(f'    _p_vals[{i}] = {self.name(p_id)};')
                else:
                    lines.append(f'    _p_vals[{i}] = {self.box_expression(self.name(p_id), pslot.type_id)};')
            lines.append(f'    return wysna_coro_spawn({step_name}, {slot_count}, {p_count}, _p_idx, _p_vals);')
        else:
            lines.append(f'    return wysna_coro_spawn({step_name}, {slot_count}, 0, NULL, NULL);')
        lines.extend(['}', ''])
        return lines

    def write(self, destination, value, boxed=True, consume=False):
        name = self.name(destination)
        if self.slots[destination].storage == 'value':
            return f'wysna_assign(&{name}, {value});'
        if boxed:
            value = self.unbox_expression(value, self.slots[destination].type_id, consume=consume)
        return f'{name} = {value};'

    def place(self, place, source):
        reference = '&' + self.name(place.root)
        for projection in place.projections:
            if projection.kind == 'field':
                reference = f'wysna_field_ref_at({reference}, {projection.index})'
            else:
                location = projection.source
                reference = f'wysna_index_ref_at({reference}, {self.value(projection.index)}, {quote(location.file)}, {location.line}, {location.col})'
        return reference

    def operation(self, operation):
        kind, data = operation.opcode, operation.data
        args = [self.value(index, move=position in operation.moves) for position, index in enumerate(operation.args)]
        result = operation.result
        source = operation.source
        if kind == 'const':
            value = data['value']
            slot = self.slots[result]
            if slot.storage == 'predicate':
                expression = str(bool(value)).lower()
            elif value is None:
                if slot.type_id == TYPE_U8:
                    return [self.write(result, '0', boxed=False)]
                expression = 'wysna_unit()' if slot.type_id == TYPE_UNIT or data.get('placeholder') else 'wysna_none()'
            elif isinstance(value, bool):
                if slot.storage == 'boolean':
                    return [self.write(result, str(value).lower(), boxed=False)]
                expression = 'wysna_bool(' + str(value).lower() + ')'
            elif isinstance(value, int):
                number = '(-9223372036854775807LL - 1LL)' if value == -9223372036854775808 else str(value) + 'LL'
                if slot.storage in ('integer', 'counter'):
                    return [self.write(result, number, boxed=False)]
                expression = 'wysna_int(' + number + ')'
            elif isinstance(value, float):
                literal = repr(value) if math.isfinite(value) else 'NAN' if math.isnan(value) else 'INFINITY' if value > 0 else '-INFINITY'
                if slot.storage == 'float':
                    return [self.write(result, literal, boxed=False)]
                expression = 'wysna_float(' + literal + ')'
            else:
                utf8_len = len(value.encode('utf-8'))
                if utf8_len > 6:
                    literal = f'_literal_{self.literal_counter}'
                    self.literal_counter += 1
                    expression = f'wysna_literal_str_n(&{literal}, {quote(value)}, {utf8_len})'
                    return [f'static WStringLiteral {literal} = {{0}};', self.write(result, expression)]
                expression = f'wysna_str_n({quote(value)}, {utf8_len})'
            return [self.write(result, expression, boxed=self.slots[result].storage != 'predicate')]
        if kind in ('clone', 'narrow'):
            if self.slots[result].storage != 'value' and self.slots[operation.args[0]].storage != 'value':
                return [self.write(result, self.name(operation.args[0]), boxed=False)]
            value = args[0] if self.slots[result].storage != 'value' else f'wysna_clone({args[0]})'
            return [self.write(result, value)]
        if kind == 'store':
            if self.slots[result].storage != 'value' and self.slots[operation.args[0]].storage != 'value':
                return [self.write(result, self.name(operation.args[0]), boxed=False)]
            if data.get('scalar'):
                return [self.write(result, self.scalar(operation.args[0]), boxed=False)]
            moved_box = 0 in operation.moves and self.slots[operation.args[0]].storage == 'value'
            return [self.write(result, args[0], consume=moved_box)]
        if kind == 'release':
            name = self.name(operation.args[0])
            return [f'wysna_release({name}); {name} = wysna_unit();']
        if kind in ('runtime', 'call'):
            name = data['name']
            if kind == 'call':
                arguments, returned, boxed = self.abi_call(name, args, arg_ids=operation.args)
                return [self.write(result, name + '(' + ', '.join(arguments) + ')', boxed=boxed, consume=boxed)]
            if kind == 'runtime':
                if name in ('wysna_add', 'wysna_sub', 'wysna_mul') and len(operation.args) == 2:
                    t0 = self.slots[operation.args[0]].type_id
                    t1 = self.slots[operation.args[1]].type_id
                    left = self.scalar(operation.args[0])
                    right = self.scalar(operation.args[1])
                    op = {'wysna_add': ' + ', 'wysna_sub': ' - ', 'wysna_mul': ' * '}[name]
                    if t0 == TYPE_INT and t1 == TYPE_INT:
                        return [self.write(result, f'(int64_t)((uint64_t){left}{op}(uint64_t){right})', boxed=False)]
                    if t0 == TYPE_FLOAT and t1 == TYPE_FLOAT:
                        return [self.write(result, f'{left}{op}{right}', boxed=False)]
                elif name in ('wysna_div_at', 'wysna_mod_at') and len(operation.args) == 2:
                    t0 = self.slots[operation.args[0]].type_id
                    t1 = self.slots[operation.args[1]].type_id
                    if t0 == TYPE_INT and t1 == TYPE_INT:
                        left = self.scalar(operation.args[0])
                        right = self.scalar(operation.args[1])
                        helper = 'wysna_div_i64_at' if name == 'wysna_div_at' else 'wysna_mod_i64_at'
                        return [self.write(result, f'{helper}({left}, {right}, {quote(source.file)}, {source.line}, {source.col})', boxed=False)]
                    if name == 'wysna_div_at' and t0 == TYPE_FLOAT and t1 == TYPE_FLOAT:
                        left = self.scalar(operation.args[0])
                        right = self.scalar(operation.args[1])
                        return [self.write(result, f'wysna_div_f64_at({left}, {right}, {quote(source.file)}, {source.line}, {source.col})', boxed=False)]
                elif name == 'wysna_neg' and len(operation.args) == 1:
                    t0 = self.slots[operation.args[0]].type_id
                    operand = self.scalar(operation.args[0])
                    if t0 == TYPE_INT:
                        return [self.write(result, f'(int64_t)(0ULL - (uint64_t){operand})', boxed=False)]
                    if t0 == TYPE_FLOAT:
                        return [self.write(result, f'-{operand}', boxed=False)]
                elif name == 'wysna_not' and len(operation.args) == 1:
                    t0 = self.slots[operation.args[0]].type_id
                    operand = self.scalar(operation.args[0])
                    if t0 == TYPE_BOOL:
                        return [self.write(result, f'!{operand}', boxed=False)]
                elif name in ('wysna_lt', 'wysna_le', 'wysna_gt', 'wysna_ge') and len(operation.args) == 2:
                    t0 = self.slots[operation.args[0]].type_id
                    t1 = self.slots[operation.args[1]].type_id
                    if (t0 == TYPE_INT and t1 == TYPE_INT) or (t0 == TYPE_FLOAT and t1 == TYPE_FLOAT):
                        left = self.scalar(operation.args[0])
                        right = self.scalar(operation.args[1])
                        op = {'wysna_lt': ' < ', 'wysna_le': ' <= ', 'wysna_gt': ' > ', 'wysna_ge': ' >= '}[name]
                        return [self.write(result, f'{left}{op}{right}', boxed=False)]
                elif name in ('wysna_equal', 'wysna_not_equal') and len(operation.args) == 2:
                    t0 = self.slots[operation.args[0]].type_id
                    t1 = self.slots[operation.args[1]].type_id
                    if ((t0 == TYPE_INT and t1 == TYPE_INT) or
                        (t0 == TYPE_FLOAT and t1 == TYPE_FLOAT) or
                        (t0 == TYPE_BOOL and t1 == TYPE_BOOL)):
                        left = self.scalar(operation.args[0])
                        right = self.scalar(operation.args[1])
                        op = ' == ' if name == 'wysna_equal' else ' != '
                        return [self.write(result, f'{left}{op}{right}', boxed=False)]
                elif (name in ('wysna_index_get_at', 'wysna_index_get_owned_at', 'wysna_index_get', 'wysna_index_get_owned')
                      and data.get('source')
                      and len(operation.args) == 2
                      and self.slots[result].storage in ('integer', 'float', 'boolean')
                      and self.interner.get_entry(self.slots[operation.args[0]].type_id).tag == 'list'):
                    idx_expr = self.scalar(operation.args[1])
                    helper = {
                        'integer': 'wysna_list_get_int_at',
                        'float': 'wysna_list_get_float_at',
                        'boolean': 'wysna_list_get_bool_at',
                    }[self.slots[result].storage]
                    return [self.write(result, f'{helper}({args[0]}, {idx_expr}, {quote(source.file)}, {source.line}, {source.col})', boxed=False)]
            if name in ('wysna_builtin_print', 'wysna_builtin_eprint'):
                args = [str(len(args)), self.array(args)]
            elif name in ('wysna_enum', 'wysna_enum_payload'):
                args = [quote(data['enum']), quote(data['variant'])] + args
            elif name == 'wysna_wrap_result':
                args += [quote(data['error_type'])]
            elif name == 'wysna_make_range':
                args += [str(data['inclusive']).lower()]
            if data.get('source'):
                args += [quote(source.file), str(source.line), str(source.col)]
            if name == 'wysna_enum':
                return ['{ static WEnumBuf _enum_lit = {0}; ' + self.write(result, 'wysna_enum_literal(&_enum_lit, ' + ', '.join(args) + ')', consume=True) + ' }']
            return [self.write(result, name + '(' + ', '.join(args) + ')', consume=True)]
        if kind in ('virtual', 'indirect'):
            rest = args[1:]
            prefix = [args[0]] + ([str(data['slot'])] if kind == 'virtual' else [])
            name = 'wysna_virtual_call' if kind == 'virtual' else 'wysna_call'
            return [self.write(result, name + '(' + ', '.join(prefix + [str(len(rest)), self.array(rest)]) + ')', consume=True)]
        if kind == 'construct':
            category = data['kind']
            if category in ('struct', 'object'):
                layout_id = data['layout']
                layout = self.layouts[layout_id]
                keys = self.layout_key_symbols.get(layout_id, 'NULL')
                call = f'wysna_make_{category}_static({quote(layout.name)}, {len(args)}, {keys}, {self.array(args)})'
            elif category == 'dict':
                call = f'wysna_make_dict({len(args) // 2}, {self.array(args[::2])}, {self.array(args[1::2])})'
            elif category == 'list' and self.interner.get_entry(self.slots[result].type_id).data == TYPE_U8:
                call = f'wysna_make_u8_list({len(args)}, {self.array(args)})'
            else:
                call = f'wysna_make_{category}({len(args)}, {self.array(args)})'
            return [self.write(result, call)]
        if kind == 'set_vtable':
            return [f'wysna_set_vtable({args[0]}, &wysna_vtable_{data["class"]});']
        if kind == 'load_field':
            value = f'wysna_field_get_at({args[0]}, {data["index"]})'
            if self.slots[result].storage == 'value':
                value = f'wysna_clone({value})'
            return [self.write(result, value)]
        if kind == 'tuple_get':
            value = (f'wysna_tuple_get_owned_at({args[0]}, {data["index"]}, '
                     f'{quote(source.file)}, {source.line}, {source.col})')
            return [self.write(result, value, consume=True)]
        if kind == 'store_place':
            place = data['place']
            if not place.projections and self.slots[place.root].storage != 'value':
                if self.slots[operation.args[0]].storage != 'value':
                    return [self.write(place.root, self.name(operation.args[0]), boxed=False)]
                moved_box = 0 in operation.moves
                return [self.write(place.root, args[0], consume=moved_box)]
            if place.projections:
                final = place.projections[-1]
                if final.kind == 'index' and final.type_id == TYPE_U8:
                    reference = self.place(Place(place.root, place.projections[:-1]), source)
                    location = final.source
                    return [f'wysna_index_store_at({reference}, {self.value(final.index)}, {args[0]}, '
                            f'{quote(location.file)}, {location.line}, {location.col});']
            return [f'wysna_assign({self.place(place, source)}, {args[0]});']
        if kind == 'mutate':
            place = data['place']
            reference = self.place(place, source)
            method = data['method']
            target_tid = place.projections[-1].type_id if place.projections else self.slots[place.root].type_id
            prefix = 'wysna_dict' if self.interner.get_entry(target_tid).tag == 'dict' else 'wysna_list'
            if prefix == 'wysna_list' and method == 'pop':
                call = f'wysna_list_pop_at({reference}, {quote(source.file)}, {source.line}, {source.col})'
            else:
                call = f'{prefix}_{method}(' + ', '.join([reference] + args) + ')'
            return [self.write(result, call, consume=True)]
        if kind == 'function':
            environment = args[0] if args else 'wysna_unit()'
            return [self.write(result, f'wysna_function_new(wysna_adapter_{data["name"]}, {environment})')]
        if kind == 'counter':
            return [self.write(result, '0', boxed=False)]
        if kind == 'increment':
            return [self.write(result, self.name(operation.args[0]) + ' + 1', boxed=False)]
        if kind == 'range_count':
            return [self.write(result, f'wysna_range_count_i64({self.scalar(operation.args[0])}, {self.scalar(operation.args[1])}, {str(data["inclusive"]).lower()})', boxed=False)]
        if kind == 'iterate_test':
            bound = self.name(operation.args[1]) if data['range'] else (
                f'wysna_list_iteration_length_at({args[1]}, {quote(source.file)}, {source.line}, {source.col})')
            return [self.write(result, self.name(operation.args[0]) + ' < ' + bound, boxed=False)]
        if kind == 'iterate_value':
            index = self.name(operation.args[1])
            if data['range']:
                start_slot = self.slots[operation.args[0]]
                start_name = self.name(operation.args[0])
                start_val = start_name if start_slot.storage != 'value' else f'wysna_as_int({start_name})'
                if self.slots[result].storage != 'value':
                    return [self.write(result, f'{start_val} + (int64_t){index}', boxed=False)]
                return [self.write(result, f'wysna_int({start_val} + (int64_t){index})', boxed=False)]
            res_storage = self.slots[result].storage
            if res_storage in ('integer', 'float', 'boolean'):
                iter_helper = {
                    'integer': 'wysna_list_iteration_get_int_at',
                    'float': 'wysna_list_iteration_get_float_at',
                    'boolean': 'wysna_list_iteration_get_bool_at',
                }[res_storage]
                return [self.write(result, f'{iter_helper}({args[0]}, {index}, {quote(source.file)}, {source.line}, {source.col})', boxed=False)]
            value = f'wysna_list_iteration_get_owned_at({args[0]}, {index}, {quote(source.file)}, {source.line}, {source.col})'
            return [self.write(result, value, consume=True)]
        if kind == 'test':
            test = data['kind']
            value = '(' + args[0] + ')'
            if test == 'truth':
                condition = self.scalar(operation.args[0])
            elif test == 'equal':
                condition = 'wysna_eq(' + ', '.join(args) + ')'
            elif test == 'none':
                condition = f'wysna_is_none({value})'
            elif test == 'err':
                condition = f'wysna_is_err({value})'
            elif test == 'failed':
                condition = f'(wysna_is_err({value}) || wysna_is_none({value}))'
            elif test == 'tuple':
                condition = f'wysna_is_tuple_len({value}, {data["length"]})'
            elif test == 'error_type':
                condition = f'wysna_value_is_error_type({value}, {quote(data["type_kind"])}, {quote(data["name"])})'
            elif test == 'variant':
                name, variant = data['enum'] or '', data['variant']
                builtin = name.split('__', 1)[0]
                if builtin == 'Option':
                    condition = ('' if variant in ('None', 'none') else '!') + f'wysna_is_none({value})'
                elif builtin == 'Result':
                    condition = ('wysna_is_ok' if variant == 'Ok' else 'wysna_is_err') + f'({value})'
                else:
                    condition = f'wysna_is_enum_variant({value}, {quote(name)}, {quote(variant)})'
            return [self.write(result, condition, boxed=False)]
        raise AssertionError('Verified MIR contains unsupported operation: ' + kind)
