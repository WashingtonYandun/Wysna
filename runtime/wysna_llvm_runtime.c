/* Fixed runtime bridge for llvm-scalar-v1; program logic is emitted as LLVM IR. */
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
typedef char WysnaLlvmPointerSize[(sizeof(void*) == 8) ? 1 : -1];

void wysna_llvm_init(int32_t argc, char** argv) { wysna_init_args(argc, argv); }
void wysna_llvm_cleanup(void) { wysna_runtime_cleanup(); }
void wysna_llvm_box_int(void* out, int64_t value) { wysna_assign(out, wysna_int(value)); }
void wysna_llvm_box_float(void* out, double value) { wysna_assign(out, wysna_float(value)); }
void wysna_llvm_box_bool(void* out, int32_t value) { wysna_assign(out, wysna_bool(value != 0)); }
int64_t wysna_llvm_int(const void* value) { return ((const WVal*)value)->as.i; }
double wysna_llvm_float(const void* value) { return ((const WVal*)value)->as.f; }
int32_t wysna_llvm_bool(const void* value) { return ((const WVal*)value)->as.b ? 1 : 0; }
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
    case 2: return wysna_lt(a, b).as.b;
    case 3: return wysna_le(a, b).as.b;
    case 4: return wysna_gt(a, b).as.b;
    case 5: return wysna_ge(a, b).as.b;
    default: wysna_runtime_fail("W9999", "Invalid LLVM comparison ABI"); return 0;
    }
}
int32_t wysna_llvm_range_count(int64_t start, int64_t end, int32_t inclusive) {
    return wysna_range_count(wysna_int(start), wysna_int(end), inclusive != 0);
}
void wysna_llvm_position(const char* file, int32_t line, int32_t col) { wysna_runtime_set_position(file, line, col); }
void wysna_llvm_fail(const char* code, const char* message, const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col); wysna_runtime_fail(code, message);
}
void wysna_llvm_exit(int64_t status) { wysna_builtin_exit(wysna_int(status)); }
