"""Foreign ABI adapters share the runtime translation unit, never program bodies."""
from codegen.ForeignAbi import foreign_wrapper


def runtime_adapters(module):
    lines = []
    for name, external in module.externs.items():
        prototype, wrapper = foreign_wrapper(name, external['native'], external['signature'])
        arguments = ', '.join('arguments[' + str(index) + ']' for index in range(len(external['params'])))
        lines.extend([prototype, wrapper,
                      'void wysna_llvm_ffi_' + name + '(void* out, const void* inputs) {',
                      '    const WVal* arguments = inputs;',
                      '    wysna_assign(out, ' + name + '(' + arguments + '));', '}'])
    return '\n'.join(lines)
