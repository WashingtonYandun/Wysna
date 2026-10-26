"""Declared first LLVM source profile and checked runtime representation contract."""
from checker.TypeInterner import TYPE_UNIT, TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STR
from error.Error import WysnaError
from mir.Verifier import Verifier

PROFILE = 'llvm-scalar-v1'
ABI_VERSION = 1
VALUE_BYTES = 32
LITERAL_BYTES = 48
ALIGNMENT = 8
SCALARS = {TYPE_UNIT, TYPE_BOOL, TYPE_INT, TYPE_FLOAT, TYPE_STR}
BINARY = {'wysna_add', 'wysna_sub', 'wysna_mul', 'wysna_div_at', 'wysna_mod_at',
          'wysna_lt', 'wysna_le', 'wysna_gt', 'wysna_ge', 'wysna_equal', 'wysna_not_equal'}
CONVERSIONS = {'wysna_builtin_str': 0, 'wysna_builtin_int': 1, 'wysna_builtin_float': 2,
               'wysna_builtin_len': 3, 'wysna_builtin_ord': 4, 'wysna_builtin_chr': 5}
RUNTIME = BINARY | set(CONVERSIONS) | {'wysna_neg', 'wysna_not', 'wysna_builtin_print',
                                      'wysna_builtin_eprint', 'wysna_builtin_exit'}
OPERATIONS = {'const', 'clone', 'store', 'release', 'call', 'runtime', 'test',
              'store_place', 'range_count', 'counter', 'increment', 'iterate_test', 'iterate_value'}


def unsupported(message, source):
    raise WysnaError(PROFILE + ': ' + message, code='W2013', stage='llvm-profile',
                     file=source.file, line=source.line, col=source.col)


def verify_profile(module, interner):
    """Reject malformed MIR first, then report explicit valid-but-unsupported features."""
    Verifier(interner).verify(module)
    source = module.functions[0].source
    if module.library_mode or module.layouts or module.externs:
        unsupported('library exports, nominal layouts and foreign declarations are outside this profile', source)
    for slot in module.globals:
        if slot.type_id not in SCALARS:
            unsupported('global values must be scalar or str', source)
    for function in module.functions:
        if function.return_type not in SCALARS or any(slot.type_id not in SCALARS for slot in function.slots):
            unsupported('function values must be scalar or str', function.source)
        for block in function.blocks:
            for operation in block.operations:
                if operation.opcode not in OPERATIONS:
                    unsupported('unsupported MIR operation: ' + operation.opcode, operation.source)
                if operation.opcode == 'runtime' and operation.data['name'] not in RUNTIME:
                    unsupported('unsupported runtime operation: ' + operation.data['name'], operation.source)
                if operation.opcode == 'test' and operation.data['kind'] not in ('truth', 'equal'):
                    unsupported('only scalar truth and equality patterns are supported', operation.source)
                if operation.opcode == 'store_place' and operation.data['place'].projections:
                    unsupported('aggregate mutation is outside this profile', operation.source)
                if operation.opcode in ('iterate_test', 'iterate_value') and not operation.data['range']:
                    unsupported('only direct integer-range iteration is supported', operation.source)
