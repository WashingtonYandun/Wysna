/* Experimental ABI 1 uses pointers at WVal boundaries, never C aggregate calls. */
#ifndef WYSNA_LLVM_ABI_H
#define WYSNA_LLVM_ABI_H
#define WYSNA_LLVM_RUNTIME_ABI 1
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
#endif
