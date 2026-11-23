"""Verified native-command LLVM profile and runtime representation contract."""
from checker.TypeInterner import TYPE_U8
from error.Error import WysnaError
from mir.Exchange import _module_type_ids
from mir.Verifier import Verifier

PROFILE = 'llvm-native-v1'
ABI_VERSION = 3
VALUE_BYTES = 8
LITERAL_BYTES = 48
ALIGNMENT = 8
CONVERSIONS = {'wysna_builtin_str': 0, 'wysna_builtin_int': 1, 'wysna_builtin_float': 2,
               'wysna_builtin_len': 3, 'wysna_builtin_ord': 4, 'wysna_builtin_chr': 5}


def verify_profile(module, interner):
    Verifier(interner).verify(module)
    if module.library_mode:
        source = module.functions[0].source
        raise WysnaError(PROFILE + ' is a native command profile; browser-library exports use their dedicated target',
                         code='W2013', stage='llvm-profile', file=source.file, line=source.line, col=source.col)

    if any(type_id == TYPE_U8 or interner.get_entry(type_id).tag == 'list'
           and interner.get_entry(type_id).data == TYPE_U8 for type_id in _module_type_ids(module)):
        source = module.functions[0].source
        raise WysnaError(PROFILE + ' does not support u8; use the default C99 backend',
                         code='W2013', stage='llvm-profile', file=source.file, line=source.line, col=source.col)
