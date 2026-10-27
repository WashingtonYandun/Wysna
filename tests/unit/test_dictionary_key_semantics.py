"""Dictionary keys must have stable equality and hash behavior."""
from checker.TypeChecker import TypeChecker
from checker.TypeInterner import TYPE_BOOL, TYPE_INT, TYPE_STR, TypeInterner
from checker.WysnaType import GenericEnumTemplate, IntType
from error.Error import WysnaError
from parser.Parser import Parser


def check(source):
    checker = TypeChecker()
    checker.check(Parser().parse(source))
    return checker


def test_invalid_dictionary_key_is_rejected_after_nominal_resolution():
    sources = [
        'struct Holder { value: dict[Key, int] }\nenum Key { Item(list[int]) }',
        'enum Key { Item(list[int]) }\nstruct Holder { value: dict[Key, int] }',
        'enum Outer { Item(Inner) }\nstruct Holder { value: dict[Outer, int] }\nenum Inner { Item(float) }',
        'enum Key { Item(Option[int]) }\nlet x: Option[int] = none\nlet values = {Key.Item(x): 1}',
        'let values: dict[Option[int], int] = {}',
        'let values: dict[int?, int] = {}',
        'enum Key[T] { Item(T) }\nstruct Holder { value: dict[Key[list[int]], int] }',
    ]
    for source in sources:
        try:
            check(source)
        except WysnaError as error:
            assert error.code == 'W2010', (source, error)
        else:
            raise AssertionError('Invalid dictionary key was accepted: ' + source)


def test_valid_nominal_dictionary_keys_do_not_depend_on_declaration_order():
    sources = [
        'struct Holder { value: dict[Key, int] }\nenum Key { Item(int, str, bool), Empty }',
        'enum Outer { Item(Inner) }\nstruct Holder { value: dict[Outer, int] }\nenum Inner { Item(int) }',
        'enum Key[T] { Item(T) }\nstruct Holder { value: dict[Key[int], int] }',
    ]
    for source in sources:
        check(source)


def test_canonical_key_rule_uses_current_payload_graph_and_rejects_cycles():
    interner = TypeInterner()
    inner = interner.mk_enum('Inner', ['Item'], {'Item': [TYPE_INT, TYPE_STR, TYPE_BOOL]})
    outer = interner.mk_enum('Outer', ['Item'], {'Item': [inner]})
    assert interner.is_valid_dictionary_key(outer)
    interner.mk_enum('Inner', ['Item'], {'Item': [interner.mk_list(TYPE_INT)]})
    assert not interner.is_valid_dictionary_key(outer)
    interner.mk_enum('Inner', ['Item'], {'Item': [outer]})
    assert not interner.is_valid_dictionary_key(outer)


def test_any_is_only_a_top_level_dictionary_placeholder():
    interner = TypeInterner()
    any_type = interner.mk_any()
    enum_type = interner.mk_enum('Unsafe', ['Item'], {'Item': [any_type]})
    assert interner.is_valid_dictionary_key(any_type, allow_any=True)
    assert not interner.is_valid_dictionary_key(enum_type, allow_any=True)


def test_shared_enum_payloads_do_not_cause_exponential_key_validation():
    interner = TypeInterner()
    current = interner.mk_enum('E0', ['Item'], {'Item': [TYPE_INT]})
    for depth in range(1, 25):
        current = interner.mk_enum('E' + str(depth), ['Item'], {'Item': [current, current]})
    original_get_entry = interner.get_entry
    calls = []

    def counted_get_entry(type_id):
        calls.append(type_id)
        return original_get_entry(type_id)

    interner.get_entry = counted_get_entry
    assert interner.is_valid_dictionary_key(current)
    assert len(calls) <= 50, len(calls)
    interner.mk_enum('E0', ['Item'], {'Item': [interner.mk_list(TYPE_INT)]})
    assert not interner.is_valid_dictionary_key(current)


def test_deep_nominal_key_graph_supports_snapshot_round_trip():
    interner = TypeInterner()
    current = interner.mk_enum('E0', ['Item'], {'Item': [TYPE_INT]})
    for depth in range(1, 1200):
        current = interner.mk_enum('E' + str(depth), ['Item'], {'Item': [current]})
    assert interner.is_valid_dictionary_key(current)
    restored = TypeInterner.from_snapshot(interner.to_snapshot())
    assert restored.is_valid_dictionary_key(current)


def test_version_one_snapshots_recompute_enum_keys_from_payloads():
    interner = TypeInterner()
    invalid = interner.mk_enum('Invalid', ['Item'], {'Item': [interner.mk_list(TYPE_INT)]})
    valid = interner.mk_enum('Valid', ['Item'], {'Item': [TYPE_INT]})
    snapshot = interner.to_snapshot()
    assert not snapshot['capabilities'][invalid]['is_valid_dict_key']
    assert snapshot['capabilities'][valid]['is_valid_dict_key']

    # Old version-one emitters wrote true for every enum. A false bit on a
    # valid enum is equally untrusted; both are derived after restoration.
    snapshot['capabilities'][invalid]['is_valid_dict_key'] = True
    snapshot['capabilities'][valid]['is_valid_dict_key'] = False
    restored = TypeInterner.from_snapshot(snapshot)
    assert not restored.get_capabilities(invalid).is_valid_dict_key
    assert restored.get_capabilities(valid).is_valid_dict_key
    assert not restored.to_snapshot()['capabilities'][invalid]['is_valid_dict_key']
    assert restored.to_snapshot()['capabilities'][valid]['is_valid_dict_key']


def test_expression_specialization_drains_dictionary_key_obligations():
    checker = check('enum Key { Item(int) }\n'
                    'enum Generic[T] { Value(dict[Key,int], T) }\n'
                    'let item = Generic.Value({}, 1)')
    assert not checker._pending_dictionary_keys


def test_failed_expression_specialization_discards_dictionary_key_obligations():
    checker = check('enum Key { Item(list[int]) }')
    declaration = Parser().parse('enum Generic[T] { Value(dict[Key,int], T) }').body[0]
    template = GenericEnumTemplate(declaration.name, declaration.type_params, declaration)
    try:
        checker._monomorphize_enum(template, [IntType], 1, 1)
    except WysnaError as error:
        assert error.code == 'W2010', error
    else:
        raise AssertionError('Specialization accepted an invalid dictionary key')
    assert not checker._pending_dictionary_keys
    assert not checker.monomorphized_enums
    assert all(entry.state.value == 'failed' for entry in checker.specializations.values())


def run_dictionary_key_tests():
    test_invalid_dictionary_key_is_rejected_after_nominal_resolution()
    test_valid_nominal_dictionary_keys_do_not_depend_on_declaration_order()
    test_canonical_key_rule_uses_current_payload_graph_and_rejects_cycles()
    test_any_is_only_a_top_level_dictionary_placeholder()
    test_shared_enum_payloads_do_not_cause_exponential_key_validation()
    test_deep_nominal_key_graph_supports_snapshot_round_trip()
    test_version_one_snapshots_recompute_enum_keys_from_payloads()
    test_expression_specialization_drains_dictionary_key_obligations()
    test_failed_expression_specialization_discards_dictionary_key_obligations()


if __name__ == '__main__':
    run_dictionary_key_tests()
