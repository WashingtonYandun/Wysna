"""Versioned, conservative effects of verified MIR and trusted host contracts."""

MODEL_VERSION = 'wysna.effects-model/1'

# Allocation, reads and failures intentionally overapproximate dynamic values.
RUNTIME_EFFECTS = {name: frozenset({
    'memory.read', 'memory.write', 'memory.allocate', 'memory.retain', 'memory.release', 'failure.raise'
}) for name in {
    'wysna_add',
    'wysna_builtin_bytes',
    'wysna_builtin_chr',
    'wysna_builtin_dict_items',
    'wysna_builtin_enumerate',
    'wysna_builtin_float',
    'wysna_builtin_int',
    'wysna_builtin_join_strings',
    'wysna_builtin_len',
    'wysna_builtin_ord',
    'wysna_builtin_slice',
    'wysna_builtin_str',
    'wysna_builtin_upgrade',
    'wysna_builtin_weak_owned',
    'wysna_dict_get',
    'wysna_dict_keys',
    'wysna_dict_values',
    'wysna_div_at',
    'wysna_empty_weak',
    'wysna_enum',
    'wysna_enum_payload',
    'wysna_enum_payload_owned',
    'wysna_equal',
    'wysna_err',
    'wysna_ge',
    'wysna_gt',
    'wysna_index_get_owned_at',
    'wysna_le',
    'wysna_lt',
    'wysna_make_range',
    'wysna_mod_at',
    'wysna_mul',
    'wysna_neg',
    'wysna_not',
    'wysna_not_equal',
    'wysna_ok',
    'wysna_sub',
    'wysna_unwrap_err',
    'wysna_unwrap_ok',
    'wysna_unwrap_optional',
    'wysna_unwrap_success',
    'wysna_wrap_result',
}}
RUNTIME_EFFECTS.update({
    'wysna_builtin_print': frozenset({'stdio.write'}),
    'wysna_builtin_eprint': frozenset({'stdio.write'}),
    'wysna_builtin_args': frozenset({'arguments.read', 'memory.allocate'}),
    'wysna_builtin_env': frozenset({'environment.read', 'memory.allocate'}),
    'wysna_builtin_unix_time': frozenset({'clock.read'}),
    'wysna_builtin_exit': frozenset({'process.exit'}),
    'wysna_builtin_read_file': frozenset({'filesystem.read', 'memory.allocate', 'failure.raise'}),
    'wysna_builtin_write_file': frozenset({'filesystem.write', 'failure.raise'}),
})

HOST_EFFECTS = {
    'wysna_host_buffer_new': {'memory.allocate', 'resource.acquire'},
    'wysna_host_buffer_size': {'memory.read'},
    'wysna_host_buffer_get': {'memory.read'},
    'wysna_host_buffer_append': {'memory.write', 'memory.allocate'},
    'wysna_host_claim': {'resource.acquire'},
    'wysna_host_claim_valid': {'resource.inspect'},
    'wysna_host_borrow': {'resource.inspect'},
    'wysna_host_close': {'resource.close', 'filesystem.write'},
    'wysna_host_is_open': {'resource.inspect'},
    'wysna_host_last_error': {'host-state.read'},
    'wysna_host_last_error_domain': {'host-state.read'},
    'wysna_host_fs': {'filesystem.read', 'filesystem.write'},
    'wysna_host_file_open': {'filesystem.read', 'filesystem.write', 'resource.acquire'},
    'wysna_host_file_read': {'filesystem.read', 'memory.write'},
    'wysna_host_file_write': {'filesystem.write', 'memory.read'},
    'wysna_host_http_request': {'network.http', 'memory.write'},
    'wysna_host_process_run': {'process.spawn', 'process.wait', 'memory.write'},
}

OPERATION_EFFECTS = {
    'const': set(), 'clone': {'memory.retain'}, 'narrow': {'memory.retain'},
    'store': {'memory.write'}, 'release': {'memory.release'},
    'construct': {'memory.allocate'}, 'function': {'memory.allocate'},
    'load_field': {'memory.read', 'memory.retain'},
    'store_place': {'memory.write', 'memory.allocate', 'failure.raise'},
    'mutate': {'memory.write', 'memory.allocate', 'failure.raise'},
    'test': {'memory.read'}, 'tuple_get': {'memory.read', 'memory.retain'},
    'range_count': {'failure.raise'}, 'counter': set(), 'increment': set(),
    'iterate_test': {'memory.read'}, 'iterate_value': {'memory.read', 'memory.retain'},
    'set_vtable': {'memory.write'},
}

CAPABILITIES = {
    'stdio.write': 'stdio', 'arguments.read': 'arguments',
    'environment.read': 'environment', 'clock.read': 'clock',
    'filesystem.read': 'filesystem.read', 'filesystem.write': 'filesystem.write',
    'network.http': 'network', 'process.spawn': 'processes',
    'process.wait': 'processes', 'process.exit': 'process-control',
    'ffi.native': 'native-ffi', 'unknown': 'unknown',
}
