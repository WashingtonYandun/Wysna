/* Fixed runtime bridge for llvm-native-v1; program logic is emitted as LLVM IR. */
#include "wysna_runtime.h"
#include "wysna_llvm_abi.h"
#include <stddef.h>
#ifndef WYSNA_LLVM_VALUE_BYTES
#error "The LLVM toolchain must declare its runtime layout contract"
#endif
typedef char WysnaLlvmAbiVersion[(WYSNA_LLVM_RUNTIME_ABI == WYSNA_LLVM_EXPECTED_ABI) ? 1 : -1];
typedef char WysnaLlvmValueSize[(sizeof(WVal) == WYSNA_LLVM_VALUE_BYTES) ? 1 : -1];
typedef char WysnaLlvmLiteralSize[(sizeof(WStringLiteral) == WYSNA_LLVM_LITERAL_BYTES) ? 1 : -1];
typedef char WysnaLlvmValueAlignment[(offsetof(struct { char prefix; WVal value; }, value) == WYSNA_LLVM_ALIGNMENT) ? 1 : -1];
typedef char WysnaLlvmBitsOffset[(offsetof(WVal, bits) == 0) ? 1 : -1];
typedef char WysnaLlvmVTableSize[(sizeof(WClassVTable) == 16) ? 1 : -1];
typedef char WysnaLlvmVTableMethods[(offsetof(WClassVTable, methods) == 8) ? 1 : -1];
typedef char WysnaLlvmInvokeSize[(sizeof(WInvoke) == sizeof(void*)) ? 1 : -1];
typedef char WysnaLlvmPointerSize[(sizeof(void*) == 8) ? 1 : -1];

void wysna_llvm_init(int32_t argc, char** argv) { wysna_init_args(argc, argv); }
void wysna_llvm_step_tick(void) { wysna_step_tick(); }
void wysna_llvm_cleanup(void) { wysna_runtime_cleanup(); }
void wysna_llvm_box_int(void* out, int64_t value) { wysna_assign(out, wysna_int(value)); }
void wysna_llvm_box_float(void* out, double value) { wysna_assign(out, wysna_float(value)); }
void wysna_llvm_box_bool(void* out, int32_t value) { wysna_assign(out, wysna_bool(value != 0)); }
int64_t wysna_llvm_int(const void* value) { return wysna_as_int(*(const WVal*)value); }
double wysna_llvm_float(const void* value) { return wysna_as_float(*(const WVal*)value); }
int32_t wysna_llvm_bool(const void* value) { return wysna_as_bool(*(const WVal*)value) ? 1 : 0; }
void wysna_llvm_clone(void* out, const void* value) { wysna_assign(out, wysna_clone(*(const WVal*)value)); }
void wysna_llvm_move(void* out, void* value) {
    if (out != value) wysna_assign(out, wysna_move(value));
}
void wysna_llvm_release(void* value) { wysna_release(*(WVal*)value); *(WVal*)value = wysna_unit(); }
void wysna_llvm_literal(void* out, void* cache, const char* bytes, int64_t length) {
    WVal value = cache ? wysna_literal_str_n(cache, bytes, (size_t)length) : wysna_str_n(bytes, (size_t)length);
    wysna_assign(out, value);
}
void wysna_llvm_print(int32_t count, const void* values, int32_t error) {
    if (error) wysna_builtin_eprint(count, values); else wysna_builtin_print(count, values);
}
void wysna_llvm_convert(void* out, const void* value, int32_t kind) {
    WVal input = *(const WVal*)value;
    WVal result;
    switch (kind) {
    case 0: result = wysna_builtin_str(input); break;
    case 1: result = wysna_builtin_int(input); break;
    case 2: result = wysna_builtin_float(input); break;
    case 3: result = wysna_builtin_len(input); break;
    case 4: result = wysna_builtin_ord(input); break;
    case 5: result = wysna_builtin_chr(input); break;
    default: wysna_runtime_fail("W9999", "Invalid LLVM conversion ABI"); result = wysna_unit();
    }
    wysna_assign(out, result);
}
void wysna_llvm_concat(void* out, const void* left, const void* right) {
    wysna_assign(out, wysna_add(*(const WVal*)left, *(const WVal*)right));
}
int32_t wysna_llvm_compare(const void* left, const void* right, int32_t kind) {
    WVal a = *(const WVal*)left, b = *(const WVal*)right;
    switch (kind) {
    case 0: return wysna_eq(a, b);
    case 1: return !wysna_eq(a, b);
    case 2: return wysna_as_bool(wysna_lt(a, b));
    case 3: return wysna_as_bool(wysna_le(a, b));
    case 4: return wysna_as_bool(wysna_gt(a, b));
    case 5: return wysna_as_bool(wysna_ge(a, b));
    default: wysna_runtime_fail("W9999", "Invalid LLVM comparison ABI"); return 0;
    }
}
int32_t wysna_llvm_range_count(int64_t start, int64_t end, int32_t inclusive) {
    return wysna_range_count_i64(start, end, inclusive != 0);
}
void wysna_llvm_position(const char* file, int32_t line, int32_t col) { wysna_runtime_set_position(file, line, col); }
void wysna_llvm_fail(const char* code, const char* message, const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col); wysna_runtime_fail(code, message);
}
void wysna_llvm_exit(int64_t status) { wysna_builtin_exit(wysna_borrow_int(status)); }

void wysna_llvm_none(void* out) { wysna_assign(out, wysna_none()); }
#define WYSNA_LLVM_OPERATION(name, code, ...) \
void wysna_llvm_rt_##name(void* out, int32_t count, const void* arguments, \
                          const char* first, const char* second, int32_t flag, \
                          const char* file, int32_t line, int32_t col) { \
    const WVal* a = arguments; WVal result = (__VA_ARGS__); wysna_assign(out, result); \
}
#include "wysna_llvm_operations.def"
#undef WYSNA_LLVM_OPERATION
void wysna_llvm_construct(void* out, int32_t kind, const char* name, int32_t count,
                          const char** keys, const void* arguments) {
    const WVal* values = arguments;
    WVal result = wysna_unit();
    switch (kind) {
    case 0: result = wysna_make_list(count, values); break;
    case 1: result = wysna_make_tuple(count, values); break;
    case 2: result = wysna_make_dict(count / 2, values, count ? values + count / 2 : NULL); break;
    case 3: result = wysna_make_struct(name, count, keys, values); break;
    case 4: result = wysna_make_object(name, count, keys, values); break;
    default: wysna_runtime_fail("W9999", "Invalid LLVM construction kind");
    }
    wysna_assign(out, result);
}
void wysna_llvm_field(void* out, const void* value, int32_t index) {
    wysna_assign(out, wysna_clone(wysna_field_get_at(*(const WVal*)value, index)));
}
void* wysna_llvm_field_ref(void* value, int32_t index) { return wysna_field_ref_at(value, index); }
void* wysna_llvm_index_ref(void* value, const void* index, const char* file, int32_t line, int32_t col) {
    return wysna_index_ref_at(value, *(const WVal*)index, file, line, col);
}
void wysna_llvm_tuple_get(void* out, const void* value, int32_t index, const char* file, int32_t line, int32_t col) {
    wysna_assign(out, wysna_tuple_get_owned_at(*(const WVal*)value, index, file, line, col));
}
void wysna_llvm_mutate(void* out, void* value, const void* argument, int32_t operation, const char* file, int32_t line, int32_t col) {
    WVal result = wysna_unit();
    switch (operation) {
    case 0: result = wysna_list_append(value, *(const WVal*)argument); break;
    case 1: result = wysna_list_pop_at(value, file, line, col); break;
    case 2: result = (wysna_hi(*(const WVal*)value) == WVAL_HI_DICT) ? wysna_dict_clear(value) : wysna_list_clear(value); break;
    case 3: result = wysna_dict_remove(value, *(const WVal*)argument); break;
    default: wysna_runtime_fail("W9999", "Invalid LLVM mutation kind");
    }
    wysna_assign(out, result);
}
int32_t wysna_llvm_test(const void* input, int32_t kind, const char* first, const char* second, int32_t count) {
    WVal value = *(const WVal*)input;
    switch (kind) {
    case 0: return wysna_is_none(value);
    case 1: return wysna_is_err(value);
    case 2: return wysna_is_err(value) || wysna_is_none(value);
    case 3: return wysna_is_tuple_len(value, count);
    case 4: return wysna_value_is_error_type(value, first, second);
    case 5: return wysna_is_enum_variant(value, first, second);
    case 6: return !wysna_is_none(value);
    case 7: return wysna_is_ok(value);
    default: wysna_runtime_fail("W9999", "Invalid LLVM predicate kind"); return 0;
    }
}
int32_t wysna_llvm_iteration_length(const void* value, const char* file, int32_t line, int32_t col) {
    return wysna_list_iteration_length_at(*(const WVal*)value, file, line, col);
}
void wysna_llvm_iteration_get(void* out, const void* value, int32_t index, const char* file, int32_t line, int32_t col) {
    wysna_assign(out, wysna_list_iteration_get_owned_at(*(const WVal*)value, index, file, line, col));
}
void wysna_llvm_function(void* out, void* invoke, void* environment) {
    wysna_assign(out, wysna_function_new((WInvoke)invoke, environment ? wysna_move(environment) : wysna_unit()));
}
void wysna_llvm_invoke(void* out, const void* function, int32_t count, const void* arguments) {
    wysna_assign(out, wysna_call(*(const WVal*)function, count, arguments));
}
void wysna_llvm_virtual(void* out, const void* receiver, int32_t slot, int32_t count, const void* arguments) {
    wysna_assign(out, wysna_virtual_call(*(const WVal*)receiver, slot, count, arguments));
}
void wysna_llvm_set_vtable(void* value, const void* table) {
    wysna_set_vtable(*(WVal*)value, (const WClassVTable*)table);
}
int32_t wysna_llvm_entry_status(const void* input) {
    return wysna_entry_status(*(const WVal*)input);
}
int32_t wysna_llvm_integer_entry_status(int64_t value) {
    return wysna_integer_entry_status(value);
}
int64_t wysna_llvm_async_spawn(const void* fn) {
    return wysna_async_spawn_handle(*(const WVal*)fn);
}
void wysna_llvm_async_take_option(void* out, int64_t handle, const void* fallback) {
    wysna_assign(out, wysna_spawn_take_option(wysna_borrow_int(handle), *(const WVal*)fallback));
}
void wysna_llvm_coro_spawn(void* out, void* step_fn, int64_t slot_count, int64_t param_count, const int64_t* param_indices, const void* param_vals) {
    wysna_assign(out, wysna_coro_spawn_out((WysnaCoroStepOutFn)step_fn, slot_count, param_count, param_indices, (const WVal*)param_vals));
}
int32_t wysna_llvm_coro_try_resolve_await(void* awaited_box, int64_t* out_handle, void* out_immediate) {
    return wysna_coro_try_resolve_await((WVal*)awaited_box, out_handle, (WVal*)out_immediate) ? 1 : 0;
}
int64_t wysna_llvm_coro_restore_int(void* slot_ptr) {
    WVal* s = (WVal*)slot_ptr;
    int64_t v = (wysna_tag(*s) == W_INT) ? wysna_as_int(*s) : 0;
    *s = wysna_unit();
    return v;
}
double wysna_llvm_coro_restore_float(void* slot_ptr) {
    WVal* s = (WVal*)slot_ptr;
    double v = (wysna_tag(*s) == W_FLOAT) ? wysna_as_float(*s) : 0.0;
    *s = wysna_unit();
    return v;
}
int32_t wysna_llvm_coro_restore_bool(void* slot_ptr) {
    WVal* s = (WVal*)slot_ptr;
    int32_t v = (wysna_tag(*s) == W_BOOL && wysna_as_bool(*s)) ? 1 : 0;
    *s = wysna_unit();
    return v;
}
