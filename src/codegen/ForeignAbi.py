"""C scalar/opaque adapters with explicit width checks and no Wysna allocation transfer."""
C_TYPES = {'int': 'int64_t', 'int64': 'int64_t', 'int32': 'int32_t',
           'float': 'double', 'double': 'double', 'bool': 'bool', 'opaque': 'void*', 'unit': 'void'}


def foreign_wrapper(c_name, native_name, signature):
    params, returned = signature
    prototype = 'extern ' + C_TYPES[returned] + ' ' + native_name + '(' + (', '.join(C_TYPES[t] for t in params) or 'void') + ');'
    wrapped_params = ', '.join('WVal v_' + str(i) for i in range(len(params))) or 'void'
    arguments = []
    for index, type_ in enumerate(params):
        value = 'v_' + str(index)
        field = {'int': 'i', 'int64': 'i', 'float': 'f', 'double': 'f', 'bool': 'b', 'opaque': 'pointer'}.get(type_)
        arguments.append('wysna_ffi_int32(' + value + ')' if type_ == 'int32' else value + '.as.' + field)
    call = native_name + '(' + ', '.join(arguments) + ')'
    if returned == 'unit':
        body = call + '; return wysna_unit();'
    else:
        constructor = {'int': 'wysna_int', 'int64': 'wysna_int', 'int32': 'wysna_int', 'float': 'wysna_float',
                       'double': 'wysna_float', 'bool': 'wysna_bool', 'opaque': 'wysna_pointer'}[returned]
        if native_name in ('wysna_host_buffer_new', 'wysna_host_file_open'):
            constructor = 'wysna_host_owned_pointer'
        body = 'return ' + constructor + '(' + call + ');'
    return prototype, 'static WVal ' + c_name + '(' + wrapped_params + ') { ' + body + ' }'
