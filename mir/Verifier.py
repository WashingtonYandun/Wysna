"""Independent MIR shape, binding, layout, CFG and ARC dataflow verification."""
from collections import deque
import re
from checker.TypeInterner import TYPE_BOOL, TYPE_INT, TYPE_UNIT, TYPE_NONE, TYPE_FLOAT, TYPE_STR
from checker.TypeRelations import TypeRelations
from error.Error import WysnaError
from mir.Nodes import EFFECTS, Place

RUNTIME = {
    'wysna_neg', 'wysna_not', 'wysna_add', 'wysna_sub', 'wysna_mul', 'wysna_div_at', 'wysna_mod_at', 'wysna_lt', 'wysna_le', 'wysna_gt', 'wysna_ge',
    'wysna_equal', 'wysna_not_equal', 'wysna_ok', 'wysna_err', 'wysna_enum', 'wysna_enum_payload', 'wysna_wrap_result',
    'wysna_make_range', 'wysna_empty_weak', 'wysna_index_get_owned_at', 'wysna_unwrap_optional', 'wysna_unwrap_ok',
    'wysna_unwrap_err', 'wysna_unwrap_success', 'wysna_enum_payload_owned', 'wysna_dict_get', 'wysna_dict_keys', 'wysna_dict_values',
    'wysna_builtin_print', 'wysna_builtin_eprint', 'wysna_builtin_len', 'wysna_builtin_str', 'wysna_builtin_int',
    'wysna_builtin_float', 'wysna_builtin_ord', 'wysna_builtin_chr', 'wysna_builtin_slice', 'wysna_builtin_bytes',
    'wysna_builtin_args', 'wysna_builtin_env', 'wysna_builtin_unix_time', 'wysna_builtin_exit', 'wysna_builtin_enumerate', 'wysna_builtin_dict_items',
    'wysna_builtin_read_file', 'wysna_builtin_write_file', 'wysna_builtin_join_strings', 'wysna_builtin_weak_owned',
    'wysna_builtin_upgrade',
}
RUNTIME_ARITIES = {
    **dict.fromkeys(('wysna_neg', 'wysna_not', 'wysna_ok', 'wysna_err', 'wysna_enum_payload', 'wysna_wrap_result',
                     'wysna_unwrap_optional', 'wysna_unwrap_ok', 'wysna_unwrap_err', 'wysna_unwrap_success',
                     'wysna_enum_payload_owned', 'wysna_dict_keys', 'wysna_dict_values', 'wysna_builtin_len',
                     'wysna_builtin_str', 'wysna_builtin_int', 'wysna_builtin_float', 'wysna_builtin_ord',
                     'wysna_builtin_chr', 'wysna_builtin_bytes', 'wysna_builtin_exit', 'wysna_builtin_enumerate',
                     'wysna_builtin_dict_items', 'wysna_builtin_read_file', 'wysna_builtin_weak_owned',
                     'wysna_builtin_upgrade', 'wysna_builtin_env'), 1),
    **dict.fromkeys(('wysna_add', 'wysna_sub', 'wysna_mul', 'wysna_div_at', 'wysna_mod_at', 'wysna_lt', 'wysna_le',
                     'wysna_gt', 'wysna_ge', 'wysna_equal', 'wysna_not_equal', 'wysna_make_range',
                     'wysna_index_get_owned_at', 'wysna_dict_get', 'wysna_builtin_write_file',
                     'wysna_builtin_join_strings'), 2),
    'wysna_builtin_slice': 3, 'wysna_builtin_args': 0, 'wysna_builtin_unix_time': 0, 'wysna_enum': 0, 'wysna_empty_weak': 0,
    'wysna_builtin_print': None, 'wysna_builtin_eprint': None,
}
assert set(RUNTIME_ARITIES) == RUNTIME, 'Every MIR runtime helper requires a signature'

CONSUMING = {'wysna_ok': (0,), 'wysna_err': (0,), 'wysna_enum_payload': (0,),
             'wysna_wrap_result': (0,), 'wysna_builtin_weak_owned': (0,)}


class Verifier:
    def __init__(self, interner):
        self.interner = interner
        self.relations = TypeRelations(interner)

    def fail(self, message, source=None):
        raise WysnaError(message, file=getattr(source, 'file', '<mir>'), line=getattr(source, 'line', 1),
                     col=getattr(source, 'col', 1), code='W9999', stage='mir-verification')

    def compatible(self, source, target):
        return self.relations.is_assignable(source, target)

    def verify(self, module):
        if module.arena_id != self.interner.arena_id:
            self.fail('MIR module belongs to a different type arena')
        self.module = module
        names = [function.name for function in module.functions] + list(module.externs) + [slot.name for slot in module.globals]
        if any(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name) is None for name in names):
            self.fail('Invalid MIR ABI identifier')
        self.functions = {function.name: function for function in module.functions}
        if len(self.functions) != len(module.functions):
            self.fail('Duplicate MIR function')
        self.global_slots = {slot.id: slot for slot in module.globals}
        if (len(self.global_slots) != len(module.globals) or
                any(slot.id >= 0 or slot.ownership != 'global' or slot.storage != 'value'
                    or slot.parameter for slot in module.globals)):
            self.fail('Invalid MIR global identities/ownership')
        if 'wysna_program' not in self.functions:
            self.fail('MIR module requires a program entry function')
        for function in module.functions:
            self.function(function)
        return module

    def signature(self, name):
        if name in self.functions:
            fn = self.functions[name]
            return [fn.slots[index].type_id for index in fn.params], fn.return_type
        if name in self.module.externs:
            ext = self.module.externs[name]
            return ext['params'], ext['return_type']
        self.fail('Unresolved MIR call: ' + name)

    def place(self, place, slots):
        if not isinstance(place, Place) or place.root not in slots:
            self.fail('Invalid MIR place root')
        current = slots[place.root].type_id
        reads = [place.root]
        for projection in place.projections:
            if projection.owner_type != current:
                self.fail('MIR projection does not match its owner')
            if projection.kind == 'field':
                layout = self.module.layouts.get(current)
                if layout is None or not 0 <= projection.index < len(layout.fields):
                    self.fail('Invalid MIR field offset')
                current = layout.fields[projection.index][1]
            elif projection.kind == 'index':
                if projection.index not in slots:
                    self.fail('Unresolved MIR index operand')
                entry = self.interner.get_entry(current)
                if entry.tag not in ('list', 'dict'):
                    self.fail('MIR indexed place requires list or dict')
                expected = TYPE_INT if entry.tag == 'list' else entry.data[0]
                if not self.compatible(slots[projection.index].type_id, expected):
                    self.fail('Invalid MIR index type')
                current = entry.data if entry.tag == 'list' else entry.data[1]
                reads.append(projection.index)
            else:
                self.fail('Unknown MIR place projection')
            if projection.type_id != current:
                self.fail('MIR projection result type mismatch')
        if slots[place.root].ownership == 'borrowed':
            self.fail('MIR mutable places must protect borrowed receivers first')
        return current, reads

    def runtime_types(self, operation, args, result):
        name = operation.data['name']
        types = [arg.type_id for arg in args]
        returned = self.interner.get_entry(result.type_id)

        def require(condition):
            if not condition:
                self.fail('MIR runtime operand/result types mismatch: ' + name, operation.source)

        def entry(index):
            return self.interner.get_entry(types[index])

        def list_of(type_id):
            return returned.tag == 'list' and returned.data == type_id

        arity = RUNTIME_ARITIES[name]
        require(arity is None or len(args) == arity)
        if name in ('wysna_neg', 'wysna_not'):
            require(types[0] == result.type_id and types[0] in
                    ((TYPE_BOOL,) if name == 'wysna_not' else (TYPE_INT, TYPE_FLOAT)))
        elif name in ('wysna_add', 'wysna_sub', 'wysna_mul', 'wysna_div_at', 'wysna_mod_at', 'wysna_lt', 'wysna_le', 'wysna_gt', 'wysna_ge'):
            allowed = (TYPE_INT, TYPE_FLOAT, TYPE_STR) if name == 'wysna_add' else (TYPE_INT,) if name == 'wysna_mod_at' else (TYPE_INT, TYPE_FLOAT)
            comparison = name in ('wysna_lt', 'wysna_le', 'wysna_gt', 'wysna_ge')
            require(types[0] == types[1] and types[0] in allowed and
                    result.type_id == (TYPE_BOOL if comparison else types[0]))
        elif name in ('wysna_equal', 'wysna_not_equal'):
            require(result.type_id == TYPE_BOOL and
                    self.relations.equality_compatible(types[0], types[1]))
        elif name == 'wysna_make_range':
            require(types == [TYPE_INT, TYPE_INT] and list_of(TYPE_INT) and
                    isinstance(operation.data.get('inclusive'), bool))
        elif name == 'wysna_builtin_len':
            require(result.type_id == TYPE_INT and (types[0] == TYPE_STR or entry(0).tag in ('list', 'dict', 'tuple')))
        elif name in ('wysna_builtin_int', 'wysna_builtin_float'):
            require(types[0] in (TYPE_INT, TYPE_FLOAT, TYPE_BOOL, TYPE_STR) and
                    result.type_id == (TYPE_INT if name == 'wysna_builtin_int' else TYPE_FLOAT))
        elif name == 'wysna_builtin_ord':
            require(types == [TYPE_STR] and result.type_id == TYPE_INT)
        elif name == 'wysna_builtin_chr':
            require(types == [TYPE_INT] and result.type_id == TYPE_STR)
        elif name == 'wysna_builtin_str':
            require(result.type_id == TYPE_STR)
        elif name == 'wysna_builtin_bytes':
            require(types == [TYPE_STR] and list_of(TYPE_INT))
        elif name == 'wysna_builtin_args':
            require(list_of(TYPE_STR))
        elif name == 'wysna_builtin_env':
            require(types == [TYPE_STR] and returned.tag == 'optional' and returned.data == TYPE_STR)
        elif name == 'wysna_builtin_unix_time':
            require(result.type_id == TYPE_INT)
        elif name == 'wysna_builtin_exit':
            require(types == [TYPE_INT] and result.type_id == TYPE_UNIT)
        elif name == 'wysna_builtin_slice':
            require(types[1:] == [TYPE_INT, TYPE_INT] and types[0] == result.type_id and
                    (types[0] == TYPE_STR or entry(0).tag == 'list'))
        elif name == 'wysna_builtin_join_strings':
            require(entry(0).tag == 'list' and entry(0).data == TYPE_STR and
                    types[1] == TYPE_STR and result.type_id == TYPE_STR)
        elif name in ('wysna_builtin_print', 'wysna_builtin_eprint'):
            require(result.type_id == TYPE_UNIT)
        elif name in ('wysna_dict_get', 'wysna_dict_keys', 'wysna_dict_values', 'wysna_builtin_dict_items'):
            dictionary = entry(0)
            require(dictionary.tag == 'dict')
            if name == 'wysna_dict_get':
                require(self.compatible(types[1], dictionary.data[0]) and returned.tag == 'optional' and returned.data == dictionary.data[1])
            elif name == 'wysna_builtin_dict_items':
                require(returned.tag == 'list')
                pair = self.interner.get_entry(returned.data)
                require(pair.tag == 'tuple' and tuple(pair.data) == tuple(dictionary.data))
            else:
                require(list_of(dictionary.data[0 if name == 'wysna_dict_keys' else 1]))
        elif name == 'wysna_builtin_enumerate':
            require(entry(0).tag == 'list' and returned.tag == 'list')
            pair = self.interner.get_entry(returned.data)
            require(pair.tag == 'tuple' and tuple(pair.data) == (TYPE_INT, entry(0).data))
        elif name == 'wysna_index_get_owned_at':
            container = entry(0)
            require(container.tag in ('list', 'dict') or types[0] == TYPE_STR)
            expected = container.data[0] if container.tag == 'dict' else TYPE_INT
            value = container.data[1] if container.tag == 'dict' else container.data if container.tag == 'list' else TYPE_STR
            require(self.compatible(types[1], expected) and result.type_id == value)
        elif name in ('wysna_builtin_read_file', 'wysna_builtin_write_file'):
            require(types == ([TYPE_STR] if name == 'wysna_builtin_read_file' else [TYPE_STR, TYPE_STR]))
            require(returned.tag == 'result' and returned.data[0] ==
                    (TYPE_STR if name == 'wysna_builtin_read_file' else TYPE_UNIT))
            error = self.interner.get_entry(returned.data[1])
            require(error.tag == 'struct' and error.data == 'IoError')
        elif name == 'wysna_builtin_weak_owned':
            require(entry(0).tag == 'class' and returned.tag == 'weak' and returned.data == types[0])
        elif name == 'wysna_empty_weak':
            require(returned.tag == 'weak' and self.interner.get_entry(returned.data).tag == 'class')
        elif name == 'wysna_builtin_upgrade':
            require(entry(0).tag == 'weak' and returned.tag == 'optional' and returned.data == entry(0).data)
        elif name in ('wysna_ok', 'wysna_err'):
            require(returned.tag == 'result' and self.compatible(types[0], returned.data[0 if name == 'wysna_ok' else 1]))
        elif name == 'wysna_wrap_result':
            require(returned.tag == 'result' and self.compatible(types[0], result.type_id) and
                    isinstance(operation.data.get('error_type'), str))
        elif name in ('wysna_unwrap_optional', 'wysna_unwrap_ok', 'wysna_unwrap_err', 'wysna_unwrap_success'):
            source = entry(0)
            allowed = ('optional',) if name == 'wysna_unwrap_optional' else ('result',) if name in ('wysna_unwrap_ok', 'wysna_unwrap_err') else ('optional', 'result')
            require(source.tag in allowed)
            expected = source.data if source.tag == 'optional' else source.data[1 if name == 'wysna_unwrap_err' else 0]
            require(result.type_id == expected)
        elif name in ('wysna_enum', 'wysna_enum_payload', 'wysna_enum_payload_owned'):
            source = entry(0) if name == 'wysna_enum_payload_owned' else returned
            variant = operation.data.get('variant')
            if source.tag == 'optional':
                require(name == 'wysna_enum_payload' and operation.data.get('enum', '').split('__', 1)[0] == 'Option' and variant == 'Some' and self.compatible(types[0], source.data))
            else:
                require(source.tag == 'enum')
                definition = self.interner.enum_defs.get(source.data)
                require(definition is not None and variant in definition.variants)
                payload = definition.variant_payloads.get(variant, [])
                if name == 'wysna_enum':
                    require(not payload and operation.data.get('enum') == source.data)
                else:
                    expected = payload[0] if len(payload) == 1 else self.interner.mk_tuple(tuple(payload))
                    require(bool(payload))
                    actual = result.type_id if name == 'wysna_enum_payload_owned' else types[0]
                    require(self.compatible(actual, expected))
                    if name == 'wysna_enum_payload':
                        require(operation.data.get('enum') == source.data)
        else:
            self.fail('Missing MIR runtime signature: ' + name, operation.source)

    def operation(self, operation, slots):
        if operation.opcode not in EFFECTS or operation.effect != EFFECTS[operation.opcode]:
            self.fail('Unknown or misclassified MIR effect', operation.source)
        if any(arg not in slots for arg in operation.args) or operation.result is not None and operation.result not in slots:
            self.fail('Unresolved MIR operand', operation.source)
        if len(set(operation.moves)) != len(operation.moves) or any(index < 0 or index >= len(operation.args) for index in operation.moves):
            self.fail('Invalid MIR ownership transfer', operation.source)
        if any(slots[operation.args[index]].ownership != 'owned' for index in operation.moves):
            self.fail('MIR cannot move a borrowed/global value', operation.source)
        result = slots.get(operation.result)
        if result and result.ownership in ('borrowed', 'global') and operation.opcode != 'store':
            self.fail('MIR cannot overwrite borrowed or external storage', operation.source)
        args = [slots[arg] for arg in operation.args]
        kind, data = operation.opcode, operation.data
        no_result = {'release', 'store_place', 'set_vtable'}
        if (kind in no_result) != (result is None):
            self.fail('Invalid MIR result contract', operation.source)
        arities = {'const': 0, 'clone': 1, 'narrow': 1, 'store': 1, 'release': 1,
                   'load_field': 1, 'tuple_get': 1, 'store_place': 1,
                   'range_count': 2, 'counter': 0, 'increment': 1,
                   'iterate_test': 2, 'iterate_value': 2, 'set_vtable': 1}
        if kind in arities and len(args) != arities[kind] or kind in ('indirect', 'virtual') and not args:
            self.fail('Invalid MIR operand count', operation.source)
        if kind == 'runtime':
            if data.get('name') not in RUNTIME or operation.moves != CONSUMING.get(data.get('name'), ()):
                self.fail('Invalid MIR runtime call contract', operation.source)
            self.runtime_types(operation, args, result)
        if kind == 'const':
            value = data.get('value')
            if data.get('placeholder'):
                valid = value is None
            elif data.get('default'):
                valid = value is None or type(value) in (bool, int, float, str)
            elif value is None:
                valid = result.type_id in (TYPE_UNIT, TYPE_NONE) or self.interner.get_entry(result.type_id).tag == 'optional'
            else:
                valid = {bool: TYPE_BOOL, int: TYPE_INT, float: TYPE_FLOAT, str: TYPE_STR}.get(type(value)) == result.type_id
            if not valid:
                self.fail('Invalid MIR constant type', operation.source)
        if kind == 'construct':
            entry = self.interner.get_entry(result.type_id)
            category = data.get('kind')
            if category in ('struct', 'object'):
                layout = self.module.layouts.get(data.get('layout'))
                if layout is None or layout.kind != ('class' if category == 'object' else 'struct') or layout.type_id != result.type_id or len(layout.fields) != len(args) or any(
                        not self.compatible(arg.type_id, expected) for arg, (_, expected) in zip(args, layout.fields)):
                    self.fail('MIR constructor layout/type mismatch', operation.source)
            elif category == 'list':
                if entry.tag != 'list' or any(not self.compatible(arg.type_id, entry.data) for arg in args):
                    self.fail('MIR list element type mismatch', operation.source)
            elif category == 'tuple':
                if entry.tag != 'tuple' or [arg.type_id for arg in args] != list(entry.data):
                    self.fail('MIR tuple element type mismatch', operation.source)
            elif category == 'dict':
                if entry.tag != 'dict' or len(args) % 2 or any(
                        not self.compatible(arg.type_id, entry.data[index % 2]) for index, arg in enumerate(args)):
                    self.fail('MIR dictionary entry type mismatch', operation.source)
            else:
                self.fail('Unknown MIR constructor', operation.source)
        value_results = {'clone', 'narrow', 'runtime', 'call', 'virtual', 'indirect', 'construct',
                         'function', 'load_field', 'tuple_get', 'mutate', 'iterate_value'}
        if kind in value_results and (result is None or result.storage not in ('value', 'integer', 'float', 'boolean') or result.ownership != 'owned'):
            self.fail('MIR value operation requires owned value storage', operation.source)
        if kind in ('counter', 'increment', 'range_count') and (result is None or result.storage != 'counter'):
            self.fail('MIR loop arithmetic requires counter storage', operation.source)
        if kind in ('test', 'iterate_test') and (result is None or result.storage != 'predicate'):
            self.fail('MIR test requires predicate storage', operation.source)
        if kind == 'tuple_get':
            entry = self.interner.get_entry(args[0].type_id)
            index = data.get('index', -1)
            if entry.tag != 'tuple' or not 0 <= index < len(entry.data) or result.type_id != entry.data[index]:
                self.fail('Invalid MIR tuple offset', operation.source)
        if kind in ('iterate_test', 'iterate_value'):
            if kind == 'iterate_test':
                counter, iterable = args
            else:
                iterable, counter = args
            if counter.storage != 'counter':
                self.fail('Invalid MIR iteration counter', operation.source)
            entry = self.interner.get_entry(iterable.type_id)
            if data.get('range'):
                if iterable.type_id != TYPE_INT:
                    self.fail('MIR range iteration requires integers', operation.source)
            elif entry.tag != 'list':
                self.fail('MIR list iteration requires list', operation.source)
            if kind == 'iterate_value' and result.type_id != (TYPE_INT if data.get('range') else entry.data):
                self.fail('MIR iterator element type mismatch', operation.source)
        if kind == 'set_vtable':
            layout = self.module.layouts.get(args[0].type_id)
            if layout is None or layout.kind != 'class' or layout.name != data.get('class'):
                self.fail('MIR vtable does not match object layout', operation.source)
        if kind == 'call':
            params, returned = self.signature(data['name'])
            if len(params) != len(args) or returned != result.type_id or any(
                    not self.compatible(arg.type_id, parameter) for arg, parameter in zip(args, params)):
                self.fail('MIR direct call signature mismatch', operation.source)
        if kind == 'function':
            if len(args) != (1 if data.get('captured') else 0) or operation.moves != ((0,) if data.get('captured') else ()):
                self.fail('MIR closure environment ownership mismatch', operation.source)
            params, returned = self.signature(data['name'])
            signature = self.interner.get_entry(result.type_id)
            expected = list(signature.data[0])
            if data['captured']:
                expected = [args[0].type_id] + expected
            if params != expected or returned != signature.data[1] or data['arity'] != len(signature.data[0]):
                self.fail('MIR function adapter signature mismatch', operation.source)
        if kind == 'indirect':
            signature = self.interner.get_entry(args[0].type_id)
            if (signature.tag != 'function' or len(args) - 1 != len(signature.data[0]) or
                    result.type_id != signature.data[1] or any(not self.compatible(arg.type_id, expected)
                    for arg, expected in zip(args[1:], signature.data[0]))):
                self.fail('MIR indirect call signature mismatch', operation.source)
        if kind == 'virtual':
            index = data.get('slot', -1)
            if not 0 <= index < len(self.module.virtual_slots):
                self.fail('Invalid MIR virtual slot', operation.source)
            name, arity = self.module.virtual_slots[index]
            receiver = self.interner.get_entry(args[0].type_id)
            if arity != len(args) - 1 or receiver.tag not in ('class', 'interface'):
                self.fail('Invalid MIR virtual receiver or arity', operation.source)
            if receiver.tag == 'class':
                method = self.module.layouts[args[0].type_id].methods.get(name)
                if method is None or method[2] != result.type_id or any(
                        not self.compatible(arg.type_id, parameter) for arg, parameter in zip(args[1:], method[1])):
                    self.fail('MIR virtual signature mismatch', operation.source)
            else:
                interface = self.interner.interface_defs[receiver.data]
                method_type = interface.methods.get(name)
                signature = self.interner.get_entry(method_type) if method_type else None
                if (signature is None or signature.tag != 'function' or
                        len(signature.data[0]) != len(args) - 1 or signature.data[1] != result.type_id or
                        any(not self.compatible(arg.type_id, expected)
                            for arg, expected in zip(args[1:], signature.data[0]))):
                    self.fail('MIR interface signature mismatch', operation.source)
        if kind in ('clone', 'store') and not data.get('scalar'):
            if len(args) != 1 or not self.compatible(args[0].type_id, result.type_id):
                self.fail('Invalid MIR copy/move type', operation.source)
            if kind == 'store' and (operation.moves != (0,) or result.ownership == 'borrowed'):
                self.fail('Invalid MIR store ownership', operation.source)
        if kind == 'store' and data.get('scalar') and (
                result.storage != 'predicate' or args[0].type_id != TYPE_BOOL or operation.moves):
            self.fail('Invalid MIR scalar predicate store', operation.source)
        if kind == 'increment' and (args[0].storage != 'counter' or args[0].id != result.id):
            self.fail('Invalid MIR counter increment', operation.source)
        if kind == 'release' and (len(args) != 1 or args[0].ownership != 'owned' or args[0].storage != 'value'):
            self.fail('Invalid MIR release', operation.source)
        if kind in ('store_place', 'mutate'):
            target_type, _ = self.place(data.get('place'), slots)
            if kind == 'store_place' and (operation.moves != (0,) or not self.compatible(args[0].type_id, target_type)):
                self.fail('MIR place store type mismatch', operation.source)
            if kind == 'mutate':
                entry = self.interner.get_entry(target_type)
                if entry.tag != 'list' or data.get('method') not in ('append', 'pop', 'clear'):
                    self.fail('Invalid MIR mutating intrinsic', operation.source)
                method = data['method']
                expected = entry.data if method == 'pop' else TYPE_UNIT
                if len(args) != (1 if method == 'append' else 0) or result.type_id != expected or args and not self.compatible(args[0].type_id, entry.data):
                    self.fail('MIR mutating intrinsic signature mismatch', operation.source)
        if kind == 'load_field':
            layout = self.module.layouts.get(args[0].type_id)
            index = data.get('index', -1)
            if layout is None or data.get('layout') != layout.type_id or not 0 <= index < len(layout.fields) or result.type_id != layout.fields[index][1]:
                self.fail('Invalid MIR physical field read', operation.source)
        if kind == 'range_count' and (any(arg.type_id != TYPE_INT for arg in args) or
                                      not isinstance(data.get('inclusive'), bool)):
            self.fail('Invalid MIR range bounds', operation.source)
        if kind == 'narrow':
            entry = self.interner.get_entry(args[0].type_id)
            if entry.tag != 'error_union' or result.type_id not in entry.data or data.get('witness') not in slots:
                self.fail('Invalid MIR error narrowing', operation.source)
        if kind == 'test':
            tests = {'truth', 'none', 'err', 'failed', 'tuple', 'error_type', 'variant', 'equal'}
            if data.get('kind') not in tests or len(args) != (2 if data.get('kind') == 'equal' else 1):
                self.fail('Invalid MIR predicate', operation.source)
            if data['kind'] == 'truth' and args[0].type_id != TYPE_BOOL:
                self.fail('MIR truth test requires bool', operation.source)
        if kind == 'test' and result.type_id != TYPE_BOOL:
            self.fail('MIR predicate must be bool', operation.source)
        return [arg.id for arg in args]

    def function(self, function):
        slots = dict(self.global_slots)
        for index, slot in enumerate(function.slots):
            if slot.id != index or self.interner.get_entry(slot.type_id) is None or slot.type_id == 0:
                self.fail('Invalid MIR slot identity/type', function.source)
            allowed = {TYPE_INT: ('integer', 'counter'), TYPE_FLOAT: ('float',),
                       TYPE_BOOL: ('boolean', 'predicate')}.get(slot.type_id, ('value',))
            if slot.storage not in allowed or slot.ownership not in ('owned', 'borrowed'):
                self.fail('Invalid MIR physical storage/type contract', function.source)
            if slot.parameter != (slot.id in function.params) or slot.parameter != (slot.ownership == 'borrowed'):
                self.fail('Invalid MIR parameter ownership', function.source)
            slots[slot.id] = slot
        if len(set(function.params)) != len(function.params) or any(index not in slots or index < 0 for index in function.params):
            self.fail('Invalid MIR parameter identity', function.source)
        blocks = {block.id: block for block in function.blocks}
        if not blocks or set(blocks) != set(range(len(blocks))):
            self.fail('Invalid MIR block identities', function.source)
        for block in function.blocks:
            term = block.terminator
            if term is None or term.kind not in ('goto', 'branch', 'return', 'unreachable'):
                self.fail('MIR block requires an explicit terminator', function.source)
            if (term.kind in ('branch', 'return') and term.value not in slots or
                    term.kind in ('goto', 'unreachable') and term.value is not None):
                self.fail('Invalid MIR terminator value', term.source)
            arity = {'goto': 1, 'branch': 2, 'return': 0, 'unreachable': 0}[term.kind]
            if len(term.targets) != arity or any(target not in blocks for target in term.targets):
                self.fail('Invalid MIR successor', term.source)
            for operation in block.operations:
                self.operation(operation, slots)
        # Definite initialization intersects at joins; potentially live ownership unions.
        initial = frozenset(function.params) | frozenset(self.global_slots)
        tests = {op.result: op for block in function.blocks for op in block.operations if op.opcode == 'test'}
        definitions = {}
        for block in function.blocks:
            for operation in block.operations:
                if operation.result is not None:
                    definitions[operation.result] = definitions.get(operation.result, 0) + 1
        channel_tests = {slot: proof for slot, proof in tests.items() if definitions.get(slot) == 1}

        def forget(facts, value):
            facts.discard(value)
            facts.discard(('false', value))
            for witness, proof in tests.items():
                if value in proof.args:
                    facts.discard(witness)
                    facts.discard(('false', witness))

        def channel_proven(operation, facts):
            error = operation.data['name'] == 'wysna_unwrap_err'
            for witness, proof in channel_tests.items():
                if proof.args != operation.args:
                    continue
                kind = proof.data.get('kind')
                if kind in ('err', 'failed'):
                    truth = error
                elif (kind == 'variant' and
                      (proof.data.get('enum') or '').split('__', 1)[0] == 'Result' and
                      proof.data.get('variant') in ('Ok', 'Err')):
                    truth = (proof.data['variant'] == 'Err') == error
                else:
                    continue
                if (witness if truth else ('false', witness)) in facts:
                    return True
            return False

        incoming = {0: (initial, frozenset(), frozenset())}
        pending = deque([0])
        while pending:
            block = blocks[pending.popleft()]
            defined, live, facts = map(set, incoming[block.id])
            for operation in block.operations:
                kind = operation.opcode
                reads = list(operation.args)
                if 'place' in operation.data:
                    _, place_reads = self.place(operation.data['place'], slots)
                    reads.extend(place_reads)
                if kind != 'release' and any(operand not in defined for operand in reads):
                    self.fail('MIR reads an uninitialized or consumed value: ' + str([operand for operand in reads if operand not in defined]), operation.source)
                if (kind == 'runtime' and operation.data['name'] in
                        ('wysna_unwrap_ok', 'wysna_unwrap_err', 'wysna_unwrap_success') and
                        self.interner.get_entry(slots[operation.args[0]].type_id).tag == 'result' and
                        not channel_proven(operation, facts)):
                    self.fail('MIR payload lacks a dominating channel test', operation.source)
                if kind == 'narrow':
                    witness = operation.data['witness']
                    proof = tests.get(witness)
                    narrowed = self.interner.get_entry(slots[operation.result].type_id)
                    name = narrowed.data if narrowed.tag in ('primitive', 'struct', 'enum') else narrowed.tag
                    type_kind = narrowed.data if narrowed.tag == 'primitive' else narrowed.tag
                    if (witness not in facts or proof is None or proof.args != operation.args or
                            proof.data.get('kind') != 'error_type' or proof.data.get('name') != name or
                            proof.data.get('type_kind') != type_kind):
                        self.fail('MIR narrowing lacks a dominating successful type test', operation.source)
                if kind == 'release':
                    defined.discard(reads[0]); live.discard(reads[0])
                    forget(facts, reads[0])
                for index in operation.moves:
                    source = operation.args[index]
                    defined.discard(source); live.discard(source)
                if operation.result is not None:
                    result = operation.result
                    forget(facts, result)
                    if operation.data.get('placeholder'):
                        defined.discard(result); live.discard(result)
                    else:
                        defined.add(result)
                        if slots[result].ownership == 'owned' and slots[result].storage == 'value':
                            live.add(result)
            term = block.terminator
            if term.value is not None:
                if term.value not in defined:
                    self.fail('MIR terminator reads an uninitialized or consumed value', term.source)
                slot = slots[term.value]
                if term.kind == 'branch' and slot.type_id != TYPE_BOOL:
                    self.fail('MIR branch requires bool', term.source)
                if term.kind == 'return':
                    if slot.ownership != 'owned' or not self.compatible(slot.type_id, function.return_type):
                        self.fail('MIR return type/ownership mismatch', term.source)
                    live.discard(slot.id)
                    if live:
                        self.fail('MIR return leaks owned values', term.source)
            for index, target in enumerate(term.targets):
                edge_facts = set(facts)
                if term.kind == 'branch':
                    if index == 0:
                        edge_facts.add(term.value)
                        edge_facts.discard(('false', term.value))
                    else:
                        edge_facts.discard(term.value)
                        edge_facts.add(('false', term.value))
                outgoing = (frozenset(defined), frozenset(live), frozenset(edge_facts))
                previous = incoming.get(target)
                merged = outgoing if previous is None else (previous[0] & outgoing[0], previous[1] | outgoing[1], previous[2] & outgoing[2])
                if previous != merged:
                    incoming[target] = merged
                    pending.append(target)
