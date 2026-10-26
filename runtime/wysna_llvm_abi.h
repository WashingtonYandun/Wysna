/* Native ABI 2 uses pointer-based WVal boundaries and target-specific callback adapters. */
#ifndef WYSNA_LLVM_ABI_H
#define WYSNA_LLVM_ABI_H
#define WYSNA_LLVM_RUNTIME_ABI 2
#include <stdint.h>
void wysna_llvm_init(int32_t argc, char** argv);
void wysna_llvm_cleanup(void);
void wysna_llvm_box_int(void* out, int64_t value);
void wysna_llvm_box_float(void* out, double value);
void wysna_llvm_box_bool(void* out, int32_t value);
int64_t wysna_llvm_int(const void* value);
double wysna_llvm_float(const void* value);
int32_t wysna_llvm_bool(const void* value);
void wysna_llvm_clone(void* out, const void* value);
void wysna_llvm_move(void* out, void* value);
void wysna_llvm_release(void* value);
void wysna_llvm_literal(void* out, void* cache, const char* bytes, int64_t length);
void wysna_llvm_print(int32_t count, const void* values, int32_t error);
void wysna_llvm_convert(void* out, const void* value, int32_t kind);
void wysna_llvm_concat(void* out, const void* left, const void* right);
int32_t wysna_llvm_compare(const void* left, const void* right, int32_t kind);
int32_t wysna_llvm_range_count(int64_t start, int64_t end, int32_t inclusive);
void wysna_llvm_position(const char* file, int32_t line, int32_t col);
void wysna_llvm_fail(const char* code, const char* message, const char* file, int32_t line, int32_t col);
void wysna_llvm_exit(int64_t status);
void wysna_llvm_none(void* out);
#define WYSNA_LLVM_OPERATION(name, code, ...) \
void wysna_llvm_rt_##name(void* out, int32_t count, const void* arguments, \
                          const char* first, const char* second, int32_t flag, \
                          const char* file, int32_t line, int32_t col);
#include "wysna_llvm_operations.def"
#undef WYSNA_LLVM_OPERATION
void wysna_llvm_construct(void* out, int32_t kind, const char* name, int32_t count,
                          const char** keys, const void* arguments);
void wysna_llvm_field(void* out, const void* value, int32_t index);
void* wysna_llvm_field_ref(void* value, int32_t index);
void* wysna_llvm_index_ref(void* value, const void* index, const char* file, int32_t line, int32_t col);
void wysna_llvm_tuple_get(void* out, const void* value, int32_t index, const char* file, int32_t line, int32_t col);
void wysna_llvm_mutate(void* out, void* value, const void* argument, int32_t operation, const char* file, int32_t line, int32_t col);
int32_t wysna_llvm_test(const void* value, int32_t kind, const char* first, const char* second, int32_t count);
int32_t wysna_llvm_iteration_length(const void* value, const char* file, int32_t line, int32_t col);
void wysna_llvm_iteration_get(void* out, const void* value, int32_t index, const char* file, int32_t line, int32_t col);
void wysna_llvm_function(void* out, void* invoke, void* environment);
void wysna_llvm_invoke(void* out, const void* function, int32_t count, const void* arguments);
void wysna_llvm_virtual(void* out, const void* receiver, int32_t slot, int32_t count, const void* arguments);
void wysna_llvm_set_vtable(void* value, const void* table);
int32_t wysna_llvm_entry_status(const void* value);
#endif
