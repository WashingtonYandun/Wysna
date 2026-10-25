"""C99 emission from verified MIR only; no HIR traversal or semantic resolution."""
import math
from checker.TypeInterner import TYPE_UNIT, TYPE_INT, TYPE_BOOL, TYPE_FLOAT, TYPE_STR
from codegen.CStrings import quote
from common.Telemetry import CodegenTelemetry
from mir.Verifier import Verifier


class CCodeGen:
    def __init__(self, interner):
        self.interner = interner
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
                          '    if (_t_init.tag == W_ERR) {',
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
                          '    WVal _t_main = ' + entry_call + ';', '    int status = 0;',
                          '    if (_t_main.tag == W_ERR) { wysna_report_entrypoint_error(_t_main.as.box->val); status = 1; }',
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
                    '    if (returned.tag != W_STR) { wysna_release(returned); ' + ' '.join(cleanup) + ' wysna_web_leave(); return 4; }',
                    '    if (returned.as.string.length > UINT32_MAX) { wysna_release(returned); ' + ' '.join(cleanup) + ' wysna_web_leave(); return 5; }',
                    '    uint32_t length = (uint32_t)returned.as.string.length;',
                    '    uint32_t pointer = length ? wysna_web_alloc(length) : 0;',
                    '    if (length && pointer == 0) { wysna_release(returned); ' + ' '.join(cleanup) + ' wysna_web_leave(); return 2; }',
                    '    if (length) memcpy((void*)(uintptr_t)pointer, returned.as.string.data, length);',
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
        return {TYPE_INT: 'int64_t', TYPE_FLOAT: 'double', TYPE_BOOL: 'bool'}.get(type_id, 'WVal')

    @staticmethod
    def box_expression(expression, type_id):
        constructor = {TYPE_INT: 'wysna_int', TYPE_FLOAT: 'wysna_float', TYPE_BOOL: 'wysna_bool'}.get(type_id)
        return constructor + '(' + expression + ')' if constructor else expression

    @staticmethod
    def unbox_expression(expression, type_id):
        member = {TYPE_INT: 'i', TYPE_FLOAT: 'f', TYPE_BOOL: 'b'}.get(type_id)
        return '(' + expression + ').as.' + member if member else expression

    def value(self, index, move=False):
        slot = self.slots[index]
        if slot.storage == 'value':
            return f'wysna_move(&{self.name(index)})' if move else self.name(index)
        return self.box_expression(self.name(index), slot.type_id)

    def scalar(self, index):
        slot = self.slots[index]
        return self.unbox_expression(self.name(index), slot.type_id) if slot.storage == 'value' else self.name(index)

    def abi_call(self, name, boxed_arguments):
        function = self.functions.get(name)
        if function is None:
            return boxed_arguments, self.module.externs[name]['return_type'], True
        parameters = [function.slots[index].type_id for index in function.params]
        arguments = [self.unbox_expression(value, type_id) for value, type_id in zip(boxed_arguments, parameters)]
        returned = function.return_type
        return arguments, returned, self.ctype(returned) == 'WVal'

    def name(self, index):
        slot = self.slots[index]
        return slot.name if slot.parameter or index < 0 else f'_m{index}'

    def array(self, arguments):
        return '(WVal[]){ ' + ', '.join(arguments) + ' }' if arguments else 'NULL'

    def function(self, function):
        self.slots = {slot.id: slot for slot in self.module.globals}
        self.slots.update({slot.id: slot for slot in function.slots})
        params = ', '.join(self.ctype(function.slots[index].type_id) + ' ' + self.name(index) for index in function.params) or 'void'
        lines = [f'static {self.ctype(function.return_type)} {function.name}({params}) {{']
        for slot in function.slots:
            if slot.parameter:
                continue
            type_ = {'value': 'WVal', 'predicate': 'bool', 'counter': 'int32_t', 'integer': 'int64_t', 'float': 'double', 'boolean': 'bool'}[slot.storage]
            initial = 'wysna_unit()' if slot.storage == 'value' else '0'
            lines.append(f'    {type_} {self.name(slot.id)} = {initial};')
        lines.append('    goto bb0;')
        for block in function.blocks:
            lines.append(f'bb{block.id}:;')
            for operation in block.operations:
                lines.extend('    ' + line for line in self.operation(operation))
            term = block.terminator
            if term.kind == 'goto':
                lines.append(f'    goto bb{term.targets[0]};')
            elif term.kind == 'branch':
                slot = self.slots[term.value]
                condition = self.scalar(term.value)
                lines.append(f'    if ({condition}) goto bb{term.targets[0]}; else goto bb{term.targets[1]};')
            elif term.kind == 'return':
                value = self.value(term.value, move=True)
                expression = self.unbox_expression(value, function.return_type)
                lines.append(f'    return {expression};')
            else:
                lines.append('    wysna_runtime_fail("W9999", "Unreachable MIR block executed");')
        lines.extend(['}', ''])
        return lines

    def write(self, destination, value, boxed=True):
        name = self.name(destination)
        if self.slots[destination].storage == 'value':
            return f'wysna_assign(&{name}, {value});'
        if boxed:
            value = self.unbox_expression(value, self.slots[destination].type_id)
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
                expression = 'wysna_unit()' if slot.type_id == TYPE_UNIT or data.get('placeholder') else 'wysna_none()'
            elif isinstance(value, bool):
                expression = 'wysna_bool(' + str(value).lower() + ')'
            elif isinstance(value, int):
                number = '(-9223372036854775807LL - 1LL)' if value == -9223372036854775808 else str(value) + 'LL'
                expression = 'wysna_int(' + number + ')'
            elif isinstance(value, float):
                literal = repr(value) if math.isfinite(value) else 'NAN' if math.isnan(value) else 'INFINITY' if value > 0 else '-INFINITY'
                expression = 'wysna_float(' + literal + ')'
            else:
                if len(value) > 1:
                    literal = f'_literal_{self.literal_counter}'
                    self.literal_counter += 1
                    expression = f'wysna_literal_str_n(&{literal}, {quote(value)}, {len(value.encode("utf-8"))})'
                    return [f'static WStringLiteral {literal} = {{0}};', self.write(result, expression)]
                expression = f'wysna_str_n({quote(value)}, {len(value.encode("utf-8"))})'
            return [self.write(result, expression, boxed=self.slots[result].storage != 'predicate')]
        if kind in ('clone', 'narrow'):
            value = args[0] if self.slots[result].storage != 'value' else f'wysna_clone({args[0]})'
            return [self.write(result, value)]
        if kind == 'store':
            if data.get('scalar'):
                return [self.write(result, self.scalar(operation.args[0]), boxed=False)]
            return [self.write(result, args[0])]
        if kind == 'release':
            name = self.name(operation.args[0])
            return [f'wysna_release({name}); {name} = wysna_unit();']
        if kind in ('runtime', 'call'):
            name = data['name']
            if kind == 'call':
                arguments, returned, boxed = self.abi_call(name, args)
                return [self.write(result, name + '(' + ', '.join(arguments) + ')', boxed=boxed)]
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
            return [self.write(result, name + '(' + ', '.join(args) + ')')]
        if kind in ('virtual', 'indirect'):
            rest = args[1:]
            prefix = [args[0]] + ([str(data['slot'])] if kind == 'virtual' else [])
            name = 'wysna_virtual_call' if kind == 'virtual' else 'wysna_call'
            return [self.write(result, name + '(' + ', '.join(prefix + [str(len(rest)), self.array(rest)]) + ')')]
        if kind == 'construct':
            category = data['kind']
            if category in ('struct', 'object'):
                layout = self.layouts[data['layout']]
                keys = '(const char*[]){ ' + ', '.join(quote(name) for name, _ in layout.fields) + ' }' if layout.fields else 'NULL'
                call = f'wysna_make_{category}({quote(layout.name)}, {len(args)}, {keys}, {self.array(args)})'
            elif category == 'dict':
                call = f'wysna_make_dict({len(args) // 2}, {self.array(args[::2])}, {self.array(args[1::2])})'
            else:
                call = f'wysna_make_{category}({len(args)}, {self.array(args)})'
            return [self.write(result, call)]
        if kind == 'set_vtable':
            return [f'{args[0]}.as.st->vtable = &wysna_vtable_{data["class"]};']
        if kind == 'load_field':
            value = f'wysna_field_get_at({args[0]}, {data["index"]})'
            if self.slots[result].storage == 'value':
                value = f'wysna_clone({value})'
            return [self.write(result, value)]
        if kind == 'tuple_get':
            value = f'{args[0]}.as.tuple->items[{data["index"]}]'
            if self.slots[result].storage == 'value':
                value = f'wysna_clone({value})'
            return [self.write(result, value)]
        if kind == 'store_place':
            place = data['place']
            if not place.projections and self.slots[place.root].storage != 'value':
                return [self.write(place.root, args[0])]
            return [f'wysna_assign({self.place(place, source)}, {args[0]});']
        if kind == 'mutate':
            reference = self.place(data['place'], source)
            method = data['method']
            if method == 'pop':
                call = f'wysna_list_pop_at({reference}, {quote(source.file)}, {source.line}, {source.col})'
            else:
                call = f'wysna_list_{method}(' + ', '.join([reference] + args) + ')'
            return [self.write(result, call)]
        if kind == 'function':
            environment = args[0] if args else 'wysna_unit()'
            return [self.write(result, f'wysna_function_new(wysna_adapter_{data["name"]}, {environment})')]
        if kind == 'counter':
            return [self.write(result, '0', boxed=False)]
        if kind == 'increment':
            return [self.write(result, self.name(operation.args[0]) + ' + 1', boxed=False)]
        if kind == 'range_count':
            return [self.write(result, f'wysna_range_count({args[0]}, {args[1]}, {str(data["inclusive"]).lower()})', boxed=False)]
        if kind == 'iterate_test':
            bound = self.name(operation.args[1]) if data['range'] else args[1] + '.as.list->len'
            return [self.write(result, self.name(operation.args[0]) + ' < ' + bound, boxed=False)]
        if kind == 'iterate_value':
            index = self.name(operation.args[1])
            value = f'wysna_int(({args[0]}).as.i + (int64_t){index})' if data['range'] else f'{args[0]}.as.list->data[{index}]'
            if not data['range'] and self.slots[result].storage == 'value':
                value = f'wysna_clone({value})'
            return [self.write(result, value)]
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
                condition = f'{value}.tag == W_ERR'
            elif test == 'failed':
                condition = f'({value}.tag == W_ERR || wysna_is_none({value}))'
            elif test == 'tuple':
                condition = f'({value}.tag == W_TUPLE && {value}.as.tuple->len == {data["length"]})'
            elif test == 'error_type':
                condition = f'wysna_value_is_error_type({value}, {quote(data["type_kind"])}, {quote(data["name"])})'
            elif test == 'variant':
                name, variant = data['enum'] or '', data['variant']
                builtin = name.split('__', 1)[0]
                if builtin == 'Option':
                    condition = ('' if variant in ('None', 'none') else '!') + f'wysna_is_none({value})'
                elif builtin == 'Result':
                    condition = f'{value}.tag == ' + ('W_OK' if variant == 'Ok' else 'W_ERR')
                else:
                    condition = f'wysna_is_enum_variant({value}, {quote(name)}, {quote(variant)})'
            return [self.write(result, condition, boxed=False)]
        raise AssertionError('Verified MIR contains unsupported operation: ' + kind)
