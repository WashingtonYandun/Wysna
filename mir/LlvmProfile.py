"""Verified native-command LLVM profile and runtime representation contract."""
from error.Error import WysnaError
from mir.Verifier import Verifier

PROFILE = 'llvm-native-v1'
ABI_VERSION = 2
VALUE_BYTES = 32
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
