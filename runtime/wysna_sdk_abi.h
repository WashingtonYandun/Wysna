/* Thin SDK ABI header for linking against precompiled libwysna_runtime_*.a.
 * Include runtime/wysna_runtime.h rather than this file. */
#ifndef WYSNA_SDK_ABI_H
#define WYSNA_SDK_ABI_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before wysna_sdk_abi.h"
#endif

#if !defined(__wasi__)
#ifndef _WIN32
#include <signal.h>
#else
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#include <windows.h>
#endif
#endif

typedef enum {
    W_UNIT = 0,
    W_NONE,
    W_INT,
    W_FLOAT,
    W_BOOL,
    W_STR,
    W_LIST,
    W_DICT,
    W_TUPLE,
    W_STRUCT,
    W_OBJECT,
    W_ENUM,
    W_OK,
    W_ERR,
    W_WEAK,
    W_FUNCTION,
    W_POINTER
} WTag;

struct WVal;
typedef struct WVal WVal;

typedef struct {
    int32_t refcount;
    int32_t len;
    int32_t cap;
    uint8_t scalar_only;
    WVal* data;
} WListBuf;

typedef struct {
    int32_t refcount;
    int32_t len;
    int32_t cap;
    WVal* keys;
    WVal* vals;
    int32_t* buckets;
    int32_t bucket_count;
} WDictBuf;

static inline size_t wysna_dict_bucket_bytes(int32_t capacity) {
    return (sizeof(int32_t) + sizeof(uint8_t)) * (size_t)capacity;
}

static inline uint8_t* wysna_dict_tags(const WDictBuf* d) {
    return (uint8_t*)(d->buckets + d->bucket_count);
}

static inline uint8_t wysna_dict_ctrl_tag(uint64_t full_hash) {
    return (uint8_t)(0x80u | ((uint32_t)(full_hash >> 57) & 0x7Fu));
}

typedef struct {
    int32_t refcount;
    int32_t len;
    WVal* items;
} WTupleBuf;

typedef struct WWeakControl WWeakControl;
typedef struct WClassVTable WClassVTable;

typedef struct {
    int32_t refcount;
    WWeakControl* control;
    const WClassVTable* vtable;
    const char* name;
    int32_t len;
    const char** keys;
    WVal* vals;
} WStructBuf;

typedef enum {
    W_COLOR_BLACK = 0,
    W_COLOR_PURPLE = 1,
    W_COLOR_GRAY = 2,
    W_COLOR_WHITE = 3
} WCycleColor;

struct WWeakControl {
    uint32_t kind;
    int32_t strong;
    int32_t weak;
    int32_t trial_rc;
    uint8_t color;
    bool buffered;
    WStructBuf* object;
    WWeakControl* cycle_next;
    WWeakControl* cycle_prev;
};

typedef struct WPointerOwner {
    uint32_t kind;
    int32_t refcount;
    int64_t (*close)(void*);
    void* address;
} WPointerOwner;

typedef struct WBoxBuf WBoxBuf;
typedef struct WFunctionBuf WFunctionBuf;
typedef struct WIntBox WIntBox;
typedef struct WEnumBuf WEnumBuf;

typedef struct {
    int32_t refcount;
    uint32_t hash_cache;
    size_t byte_length;
    size_t* codepoint_offsets;
    size_t codepoint_count;
    char* data;
} WStringBuf;

typedef struct WStringLiteral {
    WStringBuf buffer;
    struct WStringLiteral* next;
} WStringLiteral;

typedef struct WStructSlab {
    struct WStructSlab* next;
    int32_t used;
    int32_t cap;
} WStructSlab;

typedef struct WysnaRuntimeContext {
    WStringLiteral* string_literals;
    int64_t alloc_count;
    int64_t free_count;
    int64_t retain_count;
    int64_t release_count;
    int64_t live_objects;
    int64_t cow_detach_list;
    int64_t cow_detach_dict;
    int64_t cow_detach_struct;
    int64_t cow_copied_slots;
    int64_t cow_copied_bytes;
    int64_t cow_alloc_count;
    int64_t cow_allocated_bytes;
    int64_t steps_executed;
    int64_t max_steps;
    int64_t heap_bytes_allocated;
    int64_t max_heap_bytes;
    int64_t active_workers;
    volatile int worker_failure_lock;
    bool quotas_initialized;
    bool telemetry_initialized;
    bool mem_telemetry_disabled;
    bool multi_threaded;
    WWeakControl* purple_head;
    int64_t collected_cycles;
    bool collecting_cycles;
    volatile int runtime_lock;
    void (*host_async_cleanup)(void);
    int argc;
    char** argv;
#ifdef _WIN32
    bool argv_allocated;
#endif
    WBoxBuf* box_pool[16];
    int32_t box_pool_len;
    WEnumBuf* enum_pool[16];
    int32_t enum_pool_len;
    WStructSlab* struct_slabs;
    WStructBuf* struct_freelist;
} WysnaRuntimeContext;

#if defined(__wasi__) && !defined(__wasm_threads__)
#define WYSNA_THREAD_LOCAL
#elif defined(_MSC_VER)
#define WYSNA_THREAD_LOCAL __declspec(thread)
#elif defined(__GNUC__) || defined(__clang__)
#define WYSNA_THREAD_LOCAL __thread
#elif defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L
#define WYSNA_THREAD_LOCAL _Thread_local
#else
#error "Wysna native workers require compiler thread-local storage support"
#endif

typedef struct WysnaThreadDiagnostics {
    const char* source_file;
    int32_t source_line;
    int32_t source_col;
    const char* call_stack_fn[64];
    const char* call_stack_file[64];
    int32_t call_stack_line[64];
    int32_t call_depth;
} WysnaThreadDiagnostics;

extern WysnaRuntimeContext wysna_runtime_context;
extern WYSNA_THREAD_LOCAL WysnaThreadDiagnostics wysna_thread_diagnostics;
extern WYSNA_THREAD_LOCAL int32_t wysna_worker_depth;

#if defined(__GNUC__) || defined(__clang__)
#define W_LIKELY(x)   __builtin_expect(!!(x), 1)
#define W_UNLIKELY(x) __builtin_expect(!!(x), 0)
#else
#define W_LIKELY(x)   (x)
#define W_UNLIKELY(x) (x)
#endif

#if defined(_MSC_VER)
#define WYSNA_NORETURN __declspec(noreturn)
#elif defined(__GNUC__) || defined(__clang__)
#define WYSNA_NORETURN __attribute__((noreturn))
#elif defined(__STDC_VERSION__) && __STDC_VERSION__ >= 201112L
#define WYSNA_NORETURN _Noreturn
#else
#define WYSNA_NORETURN
#endif

#define wysna_g_string_literals (wysna_runtime_context.string_literals)
#define W_STRING_SCAN_STEP() ((void)0)
#define wysna_g_source_file (wysna_thread_diagnostics.source_file)
#define wysna_g_source_line (wysna_thread_diagnostics.source_line)
#define wysna_g_source_col (wysna_thread_diagnostics.source_col)
#define wysna_g_alloc_count (wysna_runtime_context.alloc_count)
#define wysna_g_free_count (wysna_runtime_context.free_count)
#define wysna_g_retain_count (wysna_runtime_context.retain_count)
#define wysna_g_release_count (wysna_runtime_context.release_count)
#define wysna_g_live_objects (wysna_runtime_context.live_objects)
#define wysna_g_cow_detach_list (wysna_runtime_context.cow_detach_list)
#define wysna_g_cow_detach_dict (wysna_runtime_context.cow_detach_dict)
#define wysna_g_cow_detach_struct (wysna_runtime_context.cow_detach_struct)
#define wysna_g_cow_copied_slots (wysna_runtime_context.cow_copied_slots)
#define wysna_g_cow_copied_bytes (wysna_runtime_context.cow_copied_bytes)
#define wysna_g_cow_alloc_count (wysna_runtime_context.cow_alloc_count)
#define wysna_g_cow_allocated_bytes (wysna_runtime_context.cow_allocated_bytes)

#define W_RUNTIME_ABI_VERSION 3
struct WVal {
    uint64_t bits;
};
typedef WVal WVal64;

#define WVAL_DOUBLE_OFFSET    UINT64_C(0x0001000000000000)
#define WVAL_DOUBLE_MAX_ENC   UINT64_C(0xFFF0000000000000)
#define WVAL_HI_SHIFT         48
#define WVAL_PAYLOAD_MASK     UINT64_C(0x0000FFFFFFFFFFFF)
#define WVAL_RAW_PTR_FLAG     UINT64_C(0x0000800000000000)
#define WVAL_RAW_PTR_MASK     UINT64_C(0x00007FFFFFFFFFFF)

#define WVAL_UNIT_BITS        UINT64_C(0x0000000000000000)
#define WVAL_NONE_BITS        UINT64_C(0x0000000000000001)
#define WVAL_FALSE_BITS       UINT64_C(0x0000000000000002)
#define WVAL_TRUE_BITS        UINT64_C(0x0000000000000003)
#define WVAL_NULL_PTR_BITS    WVAL_RAW_PTR_FLAG
#define WVAL_CANON_NAN_ENC    (UINT64_C(0x7FF8000000000000) + WVAL_DOUBLE_OFFSET)

#define WVAL_HI_INT           UINT64_C(0xFFF2)
#define WVAL_HI_SSO_SHORT     UINT64_C(0xFFF3)
#define WVAL_HI_SSO_6         UINT64_C(0xFFF4)
#define WVAL_HI_CSTR          UINT64_C(0xFFF5)
#define WVAL_HI_STR_PTR       UINT64_C(0xFFF6)
#define WVAL_HI_LIST          UINT64_C(0xFFF7)
#define WVAL_HI_DICT          UINT64_C(0xFFF8)
#define WVAL_HI_TUPLE         UINT64_C(0xFFF9)
#define WVAL_HI_STRUCT        UINT64_C(0xFFFA)
#define WVAL_HI_OBJECT        UINT64_C(0xFFFB)
#define WVAL_HI_ENUM          UINT64_C(0xFFFC)
#define WVAL_HI_OK            UINT64_C(0xFFFD)
#define WVAL_HI_ERR           UINT64_C(0xFFFE)
#define WVAL_HI_EXT           UINT64_C(0xFFFF)

#define WVAL64_UNIT_VAL       ((WVal){WVAL_UNIT_BITS})
#define WVAL64_NONE_VAL       ((WVal){WVAL_NONE_BITS})
#define WVAL64_CANON_NAN      ((WVal){WVAL_CANON_NAN_ENC})

typedef WVal (*WInvoke)(WVal environment, int32_t argc, const WVal* argv);

struct WClassVTable {
    int32_t count;
    const WInvoke* methods;
};

struct WIntBox {
    uint32_t kind;
    int32_t refcount;
    int64_t value;
};

struct WEnumBuf {
    int32_t refcount;
    bool has_payload;
    const char* enum_name;
    const char* variant;
    WVal payload;
};

struct WFunctionBuf {
    uint32_t kind;
    int32_t refcount;
    WInvoke invoke;
    WVal environment;
};

struct WBoxBuf {
    int32_t refcount;
    WVal val;
};

enum { WYSNA_LIST_PACKED_U8 = 2 };

#define WYSNA_TIMER_HANDLE_BASE 1000000
#define WYSNA_SPAWN_HANDLE_BASE 2000000LL
#define WYSNA_CORO_HANDLE_BASE 3000000LL

enum {
    WYSNA_CORO_DONE = 0,
    WYSNA_CORO_SUSPENDED = 1
};

typedef WVal (*WysnaCoroStepFn)(
    WVal* frame_slots,
    int64_t slot_count,
    int64_t* state,
    WVal resume_val,
    bool has_resume,
    WVal* out_await_box,
    int64_t* out_await_handle,
    int32_t* out_status
);

typedef void (*WysnaCoroStepOutFn)(
    WVal* out_ret,
    WVal* frame_slots,
    int64_t slot_count,
    int64_t* state,
    const WVal* resume_val,
    bool has_resume,
    WVal* out_await_box,
    int64_t* out_await_handle,
    int32_t* out_status
);

#if !defined(__wasi__)
typedef struct {
#ifdef _WIN32
    volatile LONG requested;
#else
    volatile sig_atomic_t requested;
#endif
} WysnaProcessCancellation;
#endif

/* Exported runtime & host symbols provided by libwysna_runtime_*.a */
extern int64_t wysna_collect_cycles(void);
extern void wysna_runtime_vdiagnostic(bool located, const char* code, const char* format, va_list args);
extern WYSNA_NORETURN void wysna_runtime_fail(const char* code, const char* format, ...);
extern WYSNA_NORETURN void wysna_runtime_fail_unlocated(const char* code, const char* format, ...);
extern void wysna_sandbox_deny(const char* capability);
extern void wysna_runtime_init_telemetry(void);
extern void wysna_init_quotas(void);
extern void wysna_charge_heap_bytes(size_t size);
extern void* wysna_alloc_safe(size_t size);
extern void* wysna_realloc_safe(void* old_ptr, size_t new_size);
extern void wysna_runtime_cleanup_impl(bool verify_live_objects);
extern void wysna_runtime_cleanup(void);
extern void wysna_runtime_cleanup_for_exit(void);

extern WVal wysna_int_box(int64_t x);
extern WVal wysna_pointer(void* pointer);
extern int64_t wysna_as_int_boxed(WVal v);
extern uint8_t wysna_as_u8(WVal value);
extern void wysna_set_vtable(WVal obj, const WClassVTable* table);
extern bool wysna_value_is_error_type(WVal value, const char* kind, const char* name);
extern WVal wysna_virtual_call(WVal receiver, int32_t slot, int32_t argc, const WVal* argv);
extern int32_t wysna_ffi_int32(WVal value);
extern WVal wysna_str_n_cstr(const char* s, size_t length);
extern WVal wysna_copy_str_n(const char* data, size_t length);
extern WVal wysna_literal_str_n(WStringLiteral* literal, const char* data, size_t length);
extern WVal wysna_str(const char* s);
extern WVal wysna_ascii_string(uint8_t codepoint);
extern WVal wysna_owned_str_n(char* data, size_t length);
extern WVal wysna_owned_str(char* data);
extern WVal wysna_copy_str(const char* data);
extern WVal wysna_ok(WVal inner);
extern WVal wysna_err(WVal inner);
extern bool wysna_builtin_name(const char* name, const char* base);
extern WVal wysna_enum_literal(WEnumBuf* literal, const char* enum_name, const char* variant);
extern WVal wysna_enum(const char* enum_name, const char* variant);
extern WVal wysna_enum_payload(const char* enum_name, const char* variant, WVal payload);
extern WVal wysna_make_box(uint64_t hi, WVal inner);
extern WVal wysna_wrap_result(WVal v, const char* err_type);
extern WVal wysna_discard_result_value(WVal value);
extern WVal wysna_managed_pointer(void* address, int64_t (*close)(void*));

extern WVal wysna_clone_heap(WVal v);
extern void wysna_release_heap(WVal v);
extern WVal wysna_function_new(WInvoke invoke, WVal environment);
extern WVal wysna_call(WVal function, int32_t argc, const WVal* argv);
extern void wysna_release_array(int32_t count, WVal* arr);
extern WVal wysna_unwrap_success(WVal value);
extern bool wysna_is_enum_variant(WVal value, const char* enum_name, const char* variant);
extern WVal wysna_peek_enum_payload(WVal value);
extern void wysna_list_detach(WVal* v);
extern bool wysna_eq_slow(WVal a, WVal b);
extern uint32_t wysna_str_hash32(WVal value);
extern uint64_t wysna_key_hash(WVal value);
extern int32_t wysna_dict_probe(WDictBuf* dict, WVal key, uint64_t* out_hash, uint32_t* out_slot);
extern int32_t wysna_dict_find(WDictBuf* dict, WVal key);
extern void wysna_dict_index_entry_hashed(WDictBuf* dict, int32_t index, uint64_t full_hash);
extern void wysna_dict_index_entry(WDictBuf* dict, int32_t index);
extern void wysna_dict_reindex(WDictBuf* dict, int32_t capacity);
extern void wysna_dict_insert_owned(WDictBuf* d, WVal owned_key, WVal owned_val);
extern void wysna_dict_detach(WVal* v);
extern void wysna_struct_detach(WVal* v);
extern bool wysna_val64_eq(WVal64 a, WVal64 b);

extern WVal wysna_make_list(int32_t count, const WVal* elems);
extern WVal wysna_make_u8_list(int32_t count, const WVal* elements);
extern WVal wysna_pack_u8_owned(WVal value);
extern WVal wysna_list_slice(const WListBuf* input, int32_t start, int32_t count);
extern int32_t wysna_range_count(WVal start, WVal end, bool inclusive);
extern WVal wysna_make_range(WVal start, WVal end, bool inclusive);
extern WVal wysna_make_tuple(int32_t count, const WVal* elems);
extern WVal wysna_make_dict(int32_t count, const WVal* keys, const WVal* vals);
extern WVal wysna_make_struct(const char* name, int32_t count, const char** keys, const WVal* vals);
extern WVal wysna_make_object(const char* name, int32_t count, const char** keys, const WVal* vals);
extern WVal wysna_make_struct_static(const char* name, int32_t count, const char* const* keys, const WVal* vals);
extern WVal wysna_make_object_static(const char* name, int32_t count, const char* const* keys, const WVal* vals);
extern WVal wysna_empty_weak(void);
extern WVal wysna_builtin_weak(WVal object);
extern WVal wysna_builtin_weak_owned(WVal object);
extern WVal wysna_builtin_upgrade(WVal weak);

extern WVal wysna_index_get(WVal obj, WVal idx);
extern WVal wysna_index_get_owned(WVal object, WVal index);
extern WVal wysna_index_get_at(WVal object, WVal index, const char* file, int32_t line, int32_t col);
extern WVal wysna_index_get_owned_at(WVal object, WVal index, const char* file, int32_t line, int32_t col);
extern WVal* wysna_index_ref(WVal* obj, WVal idx);
extern WVal* wysna_index_ref_at(WVal* obj, WVal idx, const char* file, int32_t line, int32_t col);
extern WVal wysna_index_store_at(WVal* object, WVal index, WVal value, const char* file, int32_t line, int32_t col);
extern WVal wysna_member_get(WVal obj, const char* prop);
extern WVal* wysna_member_ref(WVal* obj, const char* prop);
extern WVal wysna_tuple_get_at(WVal value, int32_t index, const char* file, int32_t line, int32_t col);
extern WVal wysna_tuple_get_owned_at(WVal value, int32_t index, const char* file, int32_t line, int32_t col);
extern WYSNA_NORETURN void wysna_list_iteration_fail_at(const char* code, const char* message, const char* file, int32_t line, int32_t col);
extern WVal* wysna_field_ref_at(WVal* object, int32_t index);
extern WVal wysna_enum_payload_owned(WVal value);
extern WVal wysna_list_append_slow(WVal* list_val, WVal item);
extern void wysna_list_set_slow_at(WVal* list_val, int64_t idx, WVal owned_val, const char* file, int32_t line, int32_t col);
extern bool wysna_dict_contains_i64_slow(WDictBuf* d, int64_t key);
extern void wysna_dict_set_i64_slow_at(WVal* dict_val, int64_t key, WVal owned_val, const char* file, int32_t line, int32_t col);
extern WVal wysna_list_pop(WVal* list_val);
extern WVal wysna_list_pop_at(WVal* list_val, const char* file, int32_t line, int32_t col);
extern WVal wysna_list_clear(WVal* list_val);
extern WVal wysna_dict_get(WVal dict_val, WVal key);
extern WVal wysna_dict_keys(WVal dict_val);
extern WVal wysna_dict_values(WVal dict_val);
extern WVal wysna_dict_clear(WVal* dict_val);
extern WVal wysna_dict_remove(WVal* dict_val, WVal key);
extern WVal wysna_str_trim(WVal s);
extern WVal wysna_str_to_lower(WVal s);
extern WVal wysna_str_to_upper(WVal s);
extern WVal wysna_str_starts_with(WVal s, WVal prefix);
extern WVal wysna_str_ends_with(WVal s, WVal suffix);
extern WVal wysna_str_split(WVal s, WVal sep);
extern WVal wysna_str_replace(WVal s, WVal old_sub, WVal new_sub);
extern WVal wysna_contains(WVal container, WVal item);

extern WVal wysna_add(WVal a, WVal b);
extern WVal wysna_sub(WVal a, WVal b);
extern WVal wysna_mul(WVal a, WVal b);
extern WVal wysna_div(WVal a, WVal b);
extern WVal wysna_div_at(WVal a, WVal b, const char* file, int32_t line, int32_t col);
extern WVal wysna_mod(WVal a, WVal b);
extern WVal wysna_mod_at(WVal a, WVal b, const char* file, int32_t line, int32_t col);
extern WVal wysna_neg(WVal a);
extern WVal wysna_lt(WVal a, WVal b);
extern WVal wysna_le(WVal a, WVal b);
extern WVal wysna_gt(WVal a, WVal b);
extern WVal wysna_ge(WVal a, WVal b);

extern char* wysna_to_cstr(WVal v, bool quote_str);

extern WVal wysna_builtin_print(int32_t argc, const WVal* argv);
extern WVal wysna_builtin_eprint(int32_t argc, const WVal* argv);
extern WVal wysna_builtin_exit(WVal code);
extern WVal wysna_builtin_len(WVal v);
extern WVal wysna_builtin_str(WVal v);
extern WVal wysna_builtin_u8(WVal value);
extern uint8_t wysna_consume_u8(WVal value);
extern WVal wysna_builtin_int(WVal v);
extern WVal wysna_builtin_float(WVal v);
extern WVal wysna_builtin_ord(WVal v);
extern WVal wysna_builtin_chr(WVal v);
extern WVal wysna_builtin_slice(WVal s, WVal start, WVal end);
extern WVal wysna_builtin_join_strings(WVal parts, WVal separator);
extern WVal wysna_builtin_bytes(WVal s);
extern WVal wysna_builtin_enumerate(WVal list_val);
extern WVal wysna_builtin_dict_items(WVal dict_val);
extern WVal wysna_builtin_read_file(WVal path);
extern WVal wysna_builtin_write_file(WVal path, WVal content);
extern int wysna_entry_status(WVal value);
extern void wysna_init_args(int argc, char** argv);
extern WVal wysna_builtin_args(void);
extern WVal wysna_builtin_env(WVal name);
extern WVal wysna_builtin_unix_time(void);
extern WVal wysna_builtin_contract_assert(WVal cond, WVal msg);
extern WVal wysna_builtin_contract_assert_at(WVal cond, WVal msg, const char* file, int32_t line, int32_t col);

extern WVal wysna_worker_snapshot(WVal value);
extern WVal wysna_builtin_json_parse(WVal source, WVal max_bytes_v, WVal max_depth_v, WVal max_nodes_v, WVal proto_null, WVal proto_err);
extern WVal wysna_builtin_json_parse_int(WVal raw_val);
extern WVal wysna_builtin_json_get_int(WVal obj_val, WVal key);
extern WVal wysna_builtin_json_get_bool(WVal obj_val, WVal key);
extern WVal wysna_builtin_json_get_str(WVal obj_val, WVal key);
extern WVal wysna_builtin_json_get_float(WVal obj_val, WVal key);
extern WVal wysna_builtin_json_quote_str(WVal value);

extern int64_t wysna_host_last_error(void);
extern int64_t wysna_host_last_error_domain(void);
extern int64_t wysna_host_close(void* handle);
extern WVal wysna_host_owned_pointer(void* address);
extern void* wysna_host_borrow(void* address);
extern int64_t wysna_host_active_handles(void);
extern int64_t wysna_host_active_bytes(void);
extern void* wysna_host_claim(void* raw);
extern int64_t wysna_host_claim_valid(void* handle);
extern void* wysna_host_buffer_new(void);
extern int64_t wysna_host_buffer_size(void* handle);
extern int64_t wysna_host_buffer_get(void* handle, int64_t index);
extern int64_t wysna_host_buffer_append(void* handle, int64_t value);
extern void* wysna_host_file_open(void* path_handle, int64_t mode);
extern int64_t wysna_host_file_read(void* file_handle, void* buffer_handle, int64_t count);
extern int64_t wysna_host_file_write(void* file_handle, void* buffer_handle);
extern int64_t wysna_host_is_open(void* handle);
extern int64_t wysna_host_fs(void* first, void* second, int64_t operation);
extern int64_t wysna_host_monotonic_ms(void);
extern void wysna_host_sleep_ms(int64_t ms);
extern int64_t wysna_async_spawn_handle(WVal fn);
extern int64_t wysna_spawn_poll(int64_t handle_id);
extern int64_t wysna_spawn_pending_count(void);
extern WVal wysna_spawn_clone_result(int64_t handle_id);
extern WVal wysna_spawn_take_option(WVal handle_val, WVal fallback);
extern WVal wysna_coro_spawn(WysnaCoroStepFn step_fn, int64_t slot_count, int64_t param_count, const int64_t* param_indices, const WVal* param_vals);
extern WVal wysna_coro_spawn_out(WysnaCoroStepOutFn out_step_fn, int64_t slot_count, int64_t param_count, const int64_t* param_indices, const WVal* param_vals);
extern bool wysna_coro_try_resolve_await(WVal* awaited_box, int64_t* out_handle, WVal* out_immediate);
extern int64_t wysna_coro_poll(int64_t handle_id);
extern int64_t wysna_coro_tick_all(void);
extern WVal wysna_coro_clone_result(int64_t handle_id);
extern int64_t wysna_timer_poll_or_create(int64_t transfer_id);
extern int64_t wysna_timer_next_wait_ms(int64_t* out_pending);
extern int64_t wysna_timer_multi_poll(int64_t timeout_ms, bool allow_sleep);
extern int64_t wysna_host_http_async_start(void* url_handle, void* method_handle, void* request_headers_handle, void* request_body_handle, int64_t timeout_ms, int64_t max_response_bytes);
extern int64_t wysna_host_http_async_poll(int64_t transfer_id);
extern int64_t wysna_host_http_multi_poll(int64_t timeout_ms);
extern int64_t wysna_host_http_async_finish(int64_t transfer_id, void* response_headers_handle, void* response_body_handle);
extern int64_t wysna_host_http_async_cancel(int64_t transfer_id);
extern WVal wysna_await(WVal val);
extern bool wysna_group_cancelled(void);
extern bool wysna_cancel_group(void);
extern WVal wysna_parallel_map(WVal values, WVal transform);
extern WVal wysna_run_task_group(WVal values, int64_t limit, int64_t deadline_ms, bool fail_fast, WVal transform);
extern WVal wysna_parallel_batch(WVal values, int64_t batch_size, WVal transform);
extern WVal wysna_parallel_filter(WVal values, WVal predicate);

extern int64_t wysna_host_wall_ms(void);
extern int64_t wysna_host_sleep(int64_t ms);
extern int64_t wysna_host_stdout_flush(void);
extern int64_t wysna_host_stdin_read_line(void* output_handle);
extern int64_t wysna_host_stdin_read_all(void* output_handle);
extern int64_t wysna_host_getcwd(void* output_handle);
extern int64_t wysna_host_stdin_read_exact(void* output_handle, int64_t count);
extern int64_t wysna_host_stdout_write_raw(void* input_handle);
extern int64_t wysna_host_self_executable(void* output_handle);
extern int64_t wysna_host_unicode_normalize(void* input_handle, int64_t form, void* output_handle);
extern int64_t wysna_host_project_entries(void* path_handle, void* output_handle);
extern int64_t wysna_host_project_path_key(void* input_handle, void* output_handle);
extern int64_t wysna_host_project_file_sha256(void* path_handle, void* output_handle);
extern int64_t wysna_host_fs_canonical(void* path_handle, void* output_handle);
extern int64_t wysna_host_crypto_sha256(void* input_handle, void* output_handle);
extern int64_t wysna_host_crypto_hmac_sha256(void* key_handle, void* msg_handle, void* output_handle);
extern int64_t wysna_host_crypto_sha512(void* input_handle, void* output_handle);
extern int64_t wysna_host_crypto_hmac_sha512(void* key_handle, void* msg_handle, void* output_handle);
extern int64_t wysna_host_crypto_md5(void* input_handle, void* output_handle);
extern int64_t wysna_host_crypto_random_bytes(void* output_handle, int64_t count);
extern int64_t wysna_host_crypto_constant_time_compare(void* a_handle, void* b_handle);
extern int64_t wysna_host_crypto_chacha20_poly1305_seal(void* key_handle, void* nonce_handle, void* aad_handle, void* plain_handle, void* output_handle);
extern int64_t wysna_host_crypto_chacha20_poly1305_open(void* key_handle, void* nonce_handle, void* aad_handle, void* sealed_handle, void* output_handle);

#if !defined(__wasi__)
extern void wysna_host_process_request_cancel(WysnaProcessCancellation* cancellation, int signal);
extern int wysna_host_process_cancelled(WysnaProcessCancellation* cancellation);
extern int64_t wysna_host_process_run_cancellable(void* executable_handle, void* arguments_handle, int64_t timeout_ms, WysnaProcessCancellation* cancellation);
extern int64_t wysna_host_process_capture_cancellable(void* executable_handle, void* arguments_handle, int64_t timeout_ms, int64_t max_output_bytes, void* stdout_handle, void* stderr_handle, WysnaProcessCancellation* cancellation);
extern int64_t wysna_host_process_capture(void* executable, void* arguments, int64_t timeout_ms, int64_t max_output_bytes, void* stdout_handle, void* stderr_handle);
extern int64_t wysna_host_process_run(void* executable, void* arguments, int64_t timeout_ms);
#endif

/* Hot inline operations kept in the generated TU for zero-call fast paths */
#if defined(__GNUC__) || defined(__clang__)
static inline int64_t wysna_atomic_add_i64(int64_t* ptr, int64_t delta) {
    return __atomic_add_fetch(ptr, delta, __ATOMIC_RELAXED);
}
#else
static inline int64_t wysna_atomic_add_i64(int64_t* ptr, int64_t delta) { return (*ptr += delta); }
#endif

static inline int32_t wysna_rc_load(const int32_t* ptr) {
    if (W_LIKELY(!wysna_runtime_context.multi_threaded)) return *ptr;
#if defined(__GNUC__) || defined(__clang__)
    return __atomic_load_n(ptr, __ATOMIC_RELAXED);
#else
    return *ptr;
#endif
}

static inline void wysna_runtime_set_position(const char* file, int32_t line, int32_t col) {
    wysna_g_source_file = file;
    wysna_g_source_line = line;
    wysna_g_source_col = col;
}

static inline void wysna_runtime_push_frame(const char* fn_name, const char* file, int32_t line) {
    int32_t d = wysna_thread_diagnostics.call_depth;
    if (d >= 0 && d < 64) {
        wysna_thread_diagnostics.call_stack_fn[d] = fn_name;
        wysna_thread_diagnostics.call_stack_file[d] = file;
        wysna_thread_diagnostics.call_stack_line[d] = line;
    }
    wysna_thread_diagnostics.call_depth = d + 1;
}

static inline void wysna_runtime_pop_frame(void) {
    if (wysna_thread_diagnostics.call_depth > 0) {
        wysna_thread_diagnostics.call_depth--;
    }
}

static inline void wysna_step_tick(void) {
    if (!wysna_runtime_context.quotas_initialized) wysna_init_quotas();
    if (wysna_runtime_context.max_steps > 0) {
        int64_t steps = wysna_atomic_add_i64(&wysna_runtime_context.steps_executed, 1);
        if (steps > wysna_runtime_context.max_steps) {
            wysna_runtime_fail("W4004", "Execution step quota exceeded (limit: %" PRId64 " steps)",
                               wysna_runtime_context.max_steps);
        }
    }
}

static inline WVal wysna_make_hi(uint64_t hi, uint64_t payload) {
    WVal v;
    v.bits = (hi << WVAL_HI_SHIFT) | (payload & WVAL_PAYLOAD_MASK);
    return v;
}

static inline WVal wysna_make_hi_ptr(uint64_t hi, const void* ptr) {
    uint64_t addr = (uint64_t)(uintptr_t)ptr;
    if ((addr >> WVAL_HI_SHIFT) != 0) {
        wysna_runtime_fail("W4003", "Pointer exceeds 48-bit tagged range");
    }
    return wysna_make_hi(hi, addr);
}

static inline uint32_t wysna_hi(WVal v) {
    return (uint32_t)(v.bits >> WVAL_HI_SHIFT);
}

static inline uint64_t wysna_payload(WVal v) {
    return v.bits & WVAL_PAYLOAD_MASK;
}

static inline bool wysna_is_double(WVal v) {
    return (v.bits - WVAL_DOUBLE_OFFSET) <= WVAL_DOUBLE_MAX_ENC;
}

static inline bool wysna_is_immediate(WVal v) {
    return v.bits < (WVAL_HI_STR_PTR << WVAL_HI_SHIFT);
}

static inline WTag wysna_tag(WVal v) {
    if (wysna_is_double(v)) return W_FLOAT;
    if (v.bits <= WVAL_TRUE_BITS) {
        static const uint8_t low_tags[4] = { W_UNIT, W_NONE, W_BOOL, W_BOOL };
        return (WTag)low_tags[v.bits];
    }
    uint32_t hi = wysna_hi(v);
    if (hi == 0) return W_POINTER;
    switch (hi) {
        case WVAL_HI_INT:       return W_INT;
        case WVAL_HI_SSO_SHORT:
        case WVAL_HI_SSO_6:
        case WVAL_HI_CSTR:
        case WVAL_HI_STR_PTR:   return W_STR;
        case WVAL_HI_LIST:      return W_LIST;
        case WVAL_HI_DICT:      return W_DICT;
        case WVAL_HI_TUPLE:     return W_TUPLE;
        case WVAL_HI_STRUCT:    return W_STRUCT;
        case WVAL_HI_OBJECT:    return W_OBJECT;
        case WVAL_HI_ENUM:      return W_ENUM;
        case WVAL_HI_OK:        return W_OK;
        case WVAL_HI_ERR:       return W_ERR;
        case WVAL_HI_EXT: {
            const uint32_t* kind = (const uint32_t*)(uintptr_t)wysna_payload(v);
            return kind ? (WTag)(*kind) : W_POINTER;
        }
        default:                return W_UNIT;
    }
}

static inline WVal wysna_unit(void) {
    WVal v; v.bits = WVAL_UNIT_BITS; return v;
}

static inline WVal wysna_none(void) {
    WVal v; v.bits = WVAL_NONE_BITS; return v;
}

static inline WVal wysna_bool(bool x) {
    WVal v; v.bits = x ? WVAL_TRUE_BITS : WVAL_FALSE_BITS; return v;
}

static inline bool wysna_int_fits_48(int64_t x) {
    return x >= -(INT64_C(1) << 47) && x <= ((INT64_C(1) << 47) - 1);
}

static inline WVal wysna_int_inline(int64_t x) {
    return wysna_make_hi(WVAL_HI_INT, (uint64_t)x);
}

static inline WVal wysna_int(int64_t x) {
    if (W_LIKELY(wysna_int_fits_48(x))) return wysna_int_inline(x);
    return wysna_int_box(x);
}

static inline WVal wysna_int_borrow_finish(WIntBox* stack_box, int64_t x) {
    if (wysna_int_fits_48(x)) return wysna_int_inline(x);
    stack_box->kind = W_INT;
    stack_box->refcount = -2;
    stack_box->value = x;
    return wysna_make_hi_ptr(WVAL_HI_EXT, stack_box);
}
#define wysna_borrow_int(x) wysna_int_borrow_finish(&(WIntBox){0}, (x))

static inline WVal wysna_float(double x) {
    WVal v;
    if (isnan(x)) {
        v.bits = WVAL_CANON_NAN_ENC;
        return v;
    }
    uint64_t raw = 0;
    memcpy(&raw, &x, sizeof(raw));
    v.bits = raw + WVAL_DOUBLE_OFFSET;
    return v;
}

static inline int64_t wysna_as_int(WVal v) {
    uint32_t hi = wysna_hi(v);
    if (W_LIKELY(hi == WVAL_HI_INT)) {
        uint64_t raw = wysna_payload(v);
        return (int64_t)((raw ^ UINT64_C(0x0000800000000000)) - UINT64_C(0x0000800000000000));
    }
    if (hi == WVAL_HI_EXT) return wysna_as_int_boxed(v);
    return 0;
}

static inline double wysna_as_float(WVal v) {
    if (!wysna_is_double(v)) return 0.0;
    uint64_t raw = v.bits - WVAL_DOUBLE_OFFSET;
    double d = 0.0;
    memcpy(&d, &raw, sizeof(d));
    return d;
}

static inline bool wysna_list_is_u8(const WListBuf* list) {
    return list->scalar_only == WYSNA_LIST_PACKED_U8;
}

static inline size_t wysna_list_element_size(const WListBuf* list) {
    return wysna_list_is_u8(list) ? sizeof(uint8_t) : sizeof(WVal);
}

static inline WVal wysna_list_load(const WListBuf* list, int32_t index) {
    return wysna_list_is_u8(list) ? wysna_int_inline(((const uint8_t*)list->data)[index]) : list->data[index];
}

static inline bool wysna_as_bool(WVal v) {
    return v.bits == WVAL_TRUE_BITS;
}

static inline WListBuf* wysna_as_list(WVal v) {
    return wysna_hi(v) == WVAL_HI_LIST ? (WListBuf*)(uintptr_t)wysna_payload(v) : NULL;
}

static inline WDictBuf* wysna_as_dict(WVal v) {
    return wysna_hi(v) == WVAL_HI_DICT ? (WDictBuf*)(uintptr_t)wysna_payload(v) : NULL;
}

static inline WTupleBuf* wysna_as_tuple(WVal v) {
    return wysna_hi(v) == WVAL_HI_TUPLE ? (WTupleBuf*)(uintptr_t)wysna_payload(v) : NULL;
}

static inline WStructBuf* wysna_as_struct(WVal v) {
    uint32_t hi = wysna_hi(v);
    return (hi == WVAL_HI_STRUCT || hi == WVAL_HI_OBJECT) ? (WStructBuf*)(uintptr_t)wysna_payload(v) : NULL;
}

static inline WEnumBuf* wysna_as_enum(WVal v) {
    return wysna_hi(v) == WVAL_HI_ENUM ? (WEnumBuf*)(uintptr_t)wysna_payload(v) : NULL;
}

static inline WBoxBuf* wysna_as_box(WVal v) {
    uint32_t hi = wysna_hi(v);
    return (hi == WVAL_HI_OK || hi == WVAL_HI_ERR) ? (WBoxBuf*)(uintptr_t)wysna_payload(v) : NULL;
}

static inline WWeakControl* wysna_as_weak(WVal v) {
    if (wysna_hi(v) == WVAL_HI_EXT) {
        WWeakControl* ctrl = (WWeakControl*)(uintptr_t)wysna_payload(v);
        if (ctrl && ctrl->kind == W_WEAK) return ctrl;
    }
    return NULL;
}

static inline WFunctionBuf* wysna_as_function(WVal v) {
    if (wysna_hi(v) == WVAL_HI_EXT) {
        WFunctionBuf* fn = (WFunctionBuf*)(uintptr_t)wysna_payload(v);
        if (fn && fn->kind == W_FUNCTION) return fn;
    }
    return NULL;
}

static inline WPointerOwner* wysna_pointer_owner(WVal v) {
    if (wysna_hi(v) == WVAL_HI_EXT) {
        WPointerOwner* owner = (WPointerOwner*)(uintptr_t)wysna_payload(v);
        if (owner && owner->kind == W_POINTER) return owner;
    }
    return NULL;
}

static inline void* wysna_as_pointer(WVal v) {
    if (wysna_hi(v) == 0 && (v.bits & WVAL_RAW_PTR_FLAG)) {
        return (void*)(uintptr_t)(v.bits & WVAL_RAW_PTR_MASK);
    }
    WPointerOwner* owner = wysna_pointer_owner(v);
    return owner ? owner->address : NULL;
}

static inline bool wysna_is_tuple_len(WVal v, int32_t expected_len) {
    WTupleBuf* t = wysna_as_tuple(v);
    return t != NULL && t->len == expected_len;
}

static inline bool wysna_is_err(WVal v) {
    return wysna_hi(v) == WVAL_HI_ERR;
}

static inline bool wysna_is_ok(WVal v) {
    return wysna_hi(v) == WVAL_HI_OK;
}

static inline WVal wysna_sso_from_n(const char* s, size_t len) {
    if (len <= 5) {
        uint64_t payload = ((uint64_t)len) << 40;
        for (size_t i = 0; i < len; i++) {
            payload |= ((uint64_t)(unsigned char)s[i]) << (8U * i);
        }
        return wysna_make_hi(WVAL_HI_SSO_SHORT, payload);
    }
    uint64_t payload = 0;
    for (size_t i = 0; i < 6; i++) {
        payload |= ((uint64_t)(unsigned char)s[i]) << (8U * i);
    }
    return wysna_make_hi(WVAL_HI_SSO_6, payload);
}

static inline bool wysna_read_sso(WVal v, char out[7], size_t* out_len) {
    uint32_t hi = wysna_hi(v);
    if (hi == WVAL_HI_SSO_SHORT) {
        size_t len = (size_t)((v.bits >> 40) & UINT64_C(0xFF));
        if (len > 5) return false;
        for (size_t i = 0; i < len; i++) {
            out[i] = (char)((v.bits >> (8U * i)) & UINT64_C(0xFF));
        }
        out[len] = '\0';
        if (out_len) *out_len = len;
        return true;
    }
    if (hi == WVAL_HI_SSO_6) {
        for (size_t i = 0; i < 6; i++) {
            out[i] = (char)((v.bits >> (8U * i)) & UINT64_C(0xFF));
        }
        out[6] = '\0';
        if (out_len) *out_len = 6;
        return true;
    }
    return false;
}

static inline WStringBuf* wysna_str_owner(WVal v) {
    return wysna_hi(v) == WVAL_HI_STR_PTR ? (WStringBuf*)(uintptr_t)wysna_payload(v) : NULL;
}

static inline size_t wysna_str_len(WVal v) {
    switch (wysna_hi(v)) {
        case WVAL_HI_SSO_SHORT: return (size_t)((v.bits >> 40) & UINT64_C(0xFF));
        case WVAL_HI_SSO_6:     return 6;
        case WVAL_HI_STR_PTR: {
            const WStringBuf* b = (const WStringBuf*)(uintptr_t)wysna_payload(v);
            return b ? b->byte_length : 0;
        }
        case WVAL_HI_CSTR: {
            const char* s = (const char*)(uintptr_t)wysna_payload(v);
            return s ? strlen(s) : 0;
        }
        default: return 0;
    }
}

static inline const char* wysna_str_bytes(WVal v, char scratch[7], size_t* out_len) {
    uint32_t hi = wysna_hi(v);
    if (hi == WVAL_HI_SSO_SHORT || hi == WVAL_HI_SSO_6) {
        wysna_read_sso(v, scratch, out_len);
        return scratch;
    }
    if (hi == WVAL_HI_STR_PTR) {
        const WStringBuf* b = (const WStringBuf*)(uintptr_t)wysna_payload(v);
        if (out_len) *out_len = b ? b->byte_length : 0;
        return (b && b->data) ? b->data : "";
    }
    if (hi == WVAL_HI_CSTR) {
        const char* s = (const char*)(uintptr_t)wysna_payload(v);
        if (out_len) *out_len = s ? strlen(s) : 0;
        return s ? s : "";
    }
    if (out_len) *out_len = 0;
    return "";
}

static inline WVal wysna_str_n(const char* s, size_t length) {
    if (!s || length == 0) return wysna_sso_from_n("", 0);
    if (length <= 6) return wysna_sso_from_n(s, length);
    return wysna_str_n_cstr(s, length);
}

static inline bool wysna_val64_is_double(WVal64 w) { return wysna_is_double(w); }
static inline bool wysna_val64_is_int(WVal64 w) { return wysna_hi(w) == WVAL_HI_INT; }
static inline int64_t wysna_val64_as_int(WVal64 w) { return wysna_as_int(w); }

static inline bool wysna_val64_can_pack(WVal v) {
    WTag tag = wysna_tag(v);
    switch (tag) {
        case W_UNIT:
        case W_NONE:
        case W_BOOL:
        case W_FLOAT:
            return true;
        case W_INT:
            return wysna_hi(v) == WVAL_HI_INT;
        case W_STR: {
            size_t len = wysna_str_len(v);
            return len <= 6 || wysna_str_owner(v) != NULL || wysna_hi(v) == WVAL_HI_CSTR;
        }
        default:
            return true;
    }
}

static inline WVal64 wysna_val64_pack(WVal v) { return v; }
static inline WVal wysna_val64_unpack(WVal64 w) { return w; }
static inline bool wysna_val64_read_sso(WVal64 w, char out[7], size_t* out_len) {
    return wysna_read_sso(w, out, out_len);
}

static inline WVal wysna_clone(WVal v) {
    if (W_LIKELY(wysna_is_immediate(v))) return v;
    return wysna_clone_heap(v);
}

static inline void wysna_release(WVal v) {
    if (W_LIKELY(wysna_is_immediate(v))) return;
    wysna_release_heap(v);
}

static inline int64_t wysna_consume_int(WVal v) {
    int64_t out = wysna_as_int(v);
    wysna_release(v);
    return out;
}

static inline WVal wysna_move(WVal* src) {
    WVal v = *src;
    *src = wysna_unit();
    return v;
}

static inline WVal wysna_assign(WVal* dst, WVal owned_src) {
    wysna_release(*dst);
    *dst = owned_src;
    return *dst;
}

static inline bool wysna_is_none(WVal value) {
    if (value.bits == WVAL_NONE_BITS) return true;
    WEnumBuf* en = wysna_as_enum(value);
    return en && strcmp(en->enum_name, "Option") == 0 && strcmp(en->variant, "None") == 0;
}

static inline WVal wysna_peek_optional(WVal value) {
    WEnumBuf* en = wysna_as_enum(value);
    if (en && strcmp(en->enum_name, "Option") == 0 &&
        strcmp(en->variant, "Some") == 0 && en->has_payload) return en->payload;
    return value;
}

static inline WVal wysna_peek_ok(WVal v) {
    if (wysna_is_ok(v)) {
        WBoxBuf* bx = wysna_as_box(v);
        if (bx) return bx->val;
    }
    return v;
}

static inline WVal wysna_peek_err(WVal v) {
    if (wysna_is_err(v)) {
        WBoxBuf* bx = wysna_as_box(v);
        if (bx) return bx->val;
    }
    return wysna_none();
}

static inline uint64_t wysna_mix64(uint64_t number) {
    number ^= number >> 30; number *= UINT64_C(0xbf58476d1ce4e5b9);
    number ^= number >> 27; number *= UINT64_C(0x94d049bb133111eb);
    return number ^ (number >> 31);
}

static inline bool wysna_eq(WVal a, WVal b) {
    if (W_LIKELY(a.bits == b.bits && !wysna_is_double(a))) return true;
    if (wysna_hi(a) == WVAL_HI_INT && wysna_hi(b) == WVAL_HI_INT) return false;
    return wysna_eq_slow(a, b);
}

static inline WVal wysna_unwrap_optional(WVal value) {
    WEnumBuf* en = wysna_as_enum(value);
    if (en && en->enum_name && en->variant &&
        strcmp(en->enum_name, "Option") == 0 &&
        strcmp(en->variant, "Some") == 0 && en->has_payload) {
        return wysna_clone(en->payload);
    }
    return wysna_clone(value);
}

static inline WVal wysna_unwrap_ok(WVal v) {
    if (wysna_is_ok(v)) {
        WBoxBuf* bx = wysna_as_box(v);
        if (bx) return wysna_clone(bx->val);
    }
    return wysna_clone(v);
}

static inline WVal wysna_unwrap_err(WVal v) {
    if (wysna_is_err(v)) {
        WBoxBuf* bx = wysna_as_box(v);
        if (bx) return wysna_clone(bx->val);
    }
    return wysna_none();
}

static inline int32_t wysna_range_count_i64(int64_t s, int64_t e, bool inclusive) {
    if (inclusive) {
        if (W_UNLIKELY(e == INT64_MAX)) {
            wysna_runtime_fail("W4003", "Range bound overflow at INT64_MAX");
        }
        e++;
    }
    uint64_t diff = e > s ? (uint64_t)e - (uint64_t)s : 0;
    if (W_UNLIKELY(diff > 100000000ULL)) {
        wysna_runtime_fail("W4003", "Range length %" PRIu64 " exceeds maximum list capacity", diff);
    }
    return (int32_t)diff;
}

static inline WListBuf* wysna_list_iter_begin_at(WVal value, const char* file, int32_t line, int32_t col) {
    WListBuf* b = wysna_as_list(value);
    if (W_UNLIKELY(!b)) wysna_list_iteration_fail_at("W4003", "For loop requires a list value", file, line, col);
    return b;
}

static inline int64_t wysna_list_len_i64(WVal value) {
    WListBuf* b = wysna_as_list(value);
    if (W_UNLIKELY(!b)) wysna_runtime_fail("W4003", "len() expected list value");
    return (int64_t)b->len;
}

static inline int64_t wysna_dict_len_i64(WVal value) {
    WDictBuf* d = wysna_as_dict(value);
    if (W_UNLIKELY(!d)) wysna_runtime_fail("W4003", "len() expected dict value");
    return (int64_t)d->len;
}

static inline int64_t wysna_tuple_len_i64(WVal value) {
    WTupleBuf* t = wysna_as_tuple(value);
    if (W_UNLIKELY(!t)) wysna_runtime_fail("W4003", "len() expected tuple value");
    return (int64_t)t->len;
}

static inline WVal wysna_list_append(WVal* list_val, WVal item) {
    if (W_UNLIKELY(wysna_hi(*list_val) != WVAL_HI_LIST)) {
        wysna_runtime_fail("W4003", "List intrinsic requires list receiver");
    }
    WListBuf* b = wysna_as_list(*list_val);
    if (W_LIKELY(wysna_rc_load(&b->refcount) == 1 && b->len < b->cap && !wysna_list_is_u8(b))) {
        if (W_LIKELY(wysna_hi(item) == WVAL_HI_INT || wysna_is_double(item) ||
                     item.bits == WVAL_TRUE_BITS || item.bits == WVAL_FALSE_BITS)) {
            b->data[b->len++] = item;
            return wysna_unit();
        }
        b->scalar_only = false;
        b->data[b->len++] = wysna_is_immediate(item) ? item : wysna_clone(item);
        return wysna_unit();
    }
    return wysna_list_append_slow(list_val, item);
}

static inline void wysna_list_set_at(WVal* list_val, int64_t idx, WVal owned_val,
                                     const char* file, int32_t line, int32_t col) {
    WListBuf* b = wysna_as_list(*list_val);
    if (W_LIKELY(b && wysna_rc_load(&b->refcount) == 1 && !wysna_list_is_u8(b) &&
                 idx >= 0 && idx < b->len)) {
        if (W_LIKELY(wysna_hi(owned_val) == WVAL_HI_INT || wysna_is_double(owned_val) ||
                     owned_val.bits == WVAL_TRUE_BITS || owned_val.bits == WVAL_FALSE_BITS)) {
            WVal old = b->data[idx];
            b->data[idx] = owned_val;
            if (W_UNLIKELY(!wysna_is_immediate(old))) wysna_release(old);
            return;
        }
        b->scalar_only = false;
        WVal old = b->data[idx];
        b->data[idx] = owned_val;
        wysna_release(old);
        return;
    }
    wysna_list_set_slow_at(list_val, idx, owned_val, file, line, col);
}

static inline bool wysna_dict_contains_i64(WVal dict_val, int64_t key) {
    WDictBuf* d = wysna_as_dict(dict_val);
    if (W_UNLIKELY(!d || d->len == 0 || d->bucket_count == 0)) return false;
    if (W_LIKELY(wysna_int_fits_48(key))) {
        uint64_t full_hash = (UINT64_C(14695981039346656037) ^ (uint64_t)W_INT) ^ wysna_mix64((uint64_t)key);
        uint8_t h2 = wysna_dict_ctrl_tag(full_hash);
        const uint8_t* tags = wysna_dict_tags(d);
        uint32_t mask = (uint32_t)d->bucket_count - 1;
        uint32_t slot = (uint32_t)full_hash & mask;
        WVal target = wysna_int_inline(key);
        for (;;) {
            uint8_t ctrl = tags[slot];
            if (ctrl == 0) return false;
            if (W_LIKELY(ctrl == h2)) {
                int32_t idx = d->buckets[slot];
                WVal k = d->keys[idx];
                if (W_LIKELY(k.bits == target.bits)) return true;
                if (W_UNLIKELY(wysna_hi(k) == WVAL_HI_EXT)) return wysna_dict_contains_i64_slow(d, key);
            }
            slot = (slot + 1) & mask;
        }
    }
    return wysna_dict_contains_i64_slow(d, key);
}

static inline void wysna_dict_set_i64_at(WVal* dict_val, int64_t key, WVal owned_val,
                                         const char* file, int32_t line, int32_t col) {
    WDictBuf* d = wysna_as_dict(*dict_val);
    if (W_LIKELY(d && d->bucket_count > 0 && wysna_rc_load(&d->refcount) == 1 && wysna_int_fits_48(key))) {
        uint64_t full_hash = (UINT64_C(14695981039346656037) ^ (uint64_t)W_INT) ^ wysna_mix64((uint64_t)key);
        uint8_t h2 = wysna_dict_ctrl_tag(full_hash);
        uint8_t* tags = wysna_dict_tags(d);
        uint32_t mask = (uint32_t)d->bucket_count - 1;
        uint32_t slot = (uint32_t)full_hash & mask;
        WVal target = wysna_int_inline(key);
        bool fallback = false;
        for (;;) {
            uint8_t ctrl = tags[slot];
            if (ctrl == 0) break;
            if (W_LIKELY(ctrl == h2)) {
                int32_t idx = d->buckets[slot];
                WVal k = d->keys[idx];
                if (W_LIKELY(k.bits == target.bits)) {
                    WVal old = d->vals[idx];
                    d->vals[idx] = owned_val;
                    if (W_UNLIKELY(!wysna_is_immediate(old))) wysna_release(old);
                    return;
                }
                if (W_UNLIKELY(wysna_hi(k) == WVAL_HI_EXT)) {
                    fallback = true;
                    break;
                }
            }
            slot = (slot + 1) & mask;
        }
        if (!fallback && (int64_t)(d->len + 1) * 2 < d->bucket_count && d->len < d->cap) {
            int32_t idx = d->len++;
            d->keys[idx] = target;
            d->vals[idx] = owned_val;
            d->buckets[slot] = idx;
            tags[slot] = h2;
            return;
        }
    }
    wysna_dict_set_i64_slow_at(dict_val, key, owned_val, file, line, col);
}

static inline WVal wysna_dict_get_scalar_slow_at(WVal dict_val, int64_t key,
                                                 const char* file, int32_t line, int32_t col) {
    WVal boxed_key = wysna_int(key);
    WVal result = wysna_index_get_at(dict_val, boxed_key, file, line, col);
    wysna_release(boxed_key);
    return result;
}

static inline int64_t wysna_dict_get_int_at(WVal dict_val, int64_t key,
                                            const char* file, int32_t line, int32_t col) {
    WDictBuf* d = wysna_as_dict(dict_val);
    if (W_LIKELY(d && d->len > 0 && d->bucket_count > 0 && wysna_int_fits_48(key))) {
        uint64_t full_hash = (UINT64_C(14695981039346656037) ^ (uint64_t)W_INT) ^ wysna_mix64((uint64_t)key);
        uint8_t h2 = wysna_dict_ctrl_tag(full_hash);
        const uint8_t* tags = wysna_dict_tags(d);
        uint32_t mask = (uint32_t)d->bucket_count - 1;
        uint32_t slot = (uint32_t)full_hash & mask;
        WVal target = wysna_int_inline(key);
        for (;;) {
            uint8_t ctrl = tags[slot];
            if (ctrl == 0) break;
            if (W_LIKELY(ctrl == h2)) {
                int32_t idx = d->buckets[slot];
                WVal k = d->keys[idx];
                if (W_LIKELY(k.bits == target.bits)) return wysna_as_int(d->vals[idx]);
                if (W_UNLIKELY(wysna_hi(k) == WVAL_HI_EXT)) break;
            }
            slot = (slot + 1) & mask;
        }
    }
    return wysna_as_int(wysna_dict_get_scalar_slow_at(dict_val, key, file, line, col));
}

static inline double wysna_dict_get_float_at(WVal dict_val, int64_t key,
                                             const char* file, int32_t line, int32_t col) {
    WDictBuf* d = wysna_as_dict(dict_val);
    if (W_LIKELY(d && d->len > 0 && d->bucket_count > 0 && wysna_int_fits_48(key))) {
        uint64_t full_hash = (UINT64_C(14695981039346656037) ^ (uint64_t)W_INT) ^ wysna_mix64((uint64_t)key);
        uint8_t h2 = wysna_dict_ctrl_tag(full_hash);
        const uint8_t* tags = wysna_dict_tags(d);
        uint32_t mask = (uint32_t)d->bucket_count - 1;
        uint32_t slot = (uint32_t)full_hash & mask;
        WVal target = wysna_int_inline(key);
        for (;;) {
            uint8_t ctrl = tags[slot];
            if (ctrl == 0) break;
            if (W_LIKELY(ctrl == h2)) {
                int32_t idx = d->buckets[slot];
                WVal k = d->keys[idx];
                if (W_LIKELY(k.bits == target.bits)) return wysna_as_float(d->vals[idx]);
                if (W_UNLIKELY(wysna_hi(k) == WVAL_HI_EXT)) break;
            }
            slot = (slot + 1) & mask;
        }
    }
    return wysna_as_float(wysna_dict_get_scalar_slow_at(dict_val, key, file, line, col));
}

static inline bool wysna_dict_get_bool_at(WVal dict_val, int64_t key,
                                          const char* file, int32_t line, int32_t col) {
    WDictBuf* d = wysna_as_dict(dict_val);
    if (W_LIKELY(d && d->len > 0 && d->bucket_count > 0 && wysna_int_fits_48(key))) {
        uint64_t full_hash = (UINT64_C(14695981039346656037) ^ (uint64_t)W_INT) ^ wysna_mix64((uint64_t)key);
        uint8_t h2 = wysna_dict_ctrl_tag(full_hash);
        const uint8_t* tags = wysna_dict_tags(d);
        uint32_t mask = (uint32_t)d->bucket_count - 1;
        uint32_t slot = (uint32_t)full_hash & mask;
        WVal target = wysna_int_inline(key);
        for (;;) {
            uint8_t ctrl = tags[slot];
            if (ctrl == 0) break;
            if (W_LIKELY(ctrl == h2)) {
                int32_t idx = d->buckets[slot];
                WVal k = d->keys[idx];
                if (W_LIKELY(k.bits == target.bits)) return wysna_as_bool(d->vals[idx]);
                if (W_UNLIKELY(wysna_hi(k) == WVAL_HI_EXT)) break;
            }
            slot = (slot + 1) & mask;
        }
    }
    return wysna_as_bool(wysna_dict_get_scalar_slow_at(dict_val, key, file, line, col));
}

static inline WVal wysna_field_get_at(WVal object, int32_t index) {
    WStructBuf* st = wysna_as_struct(object);
    if (!st || index < 0 || index >= st->len)
        wysna_runtime_fail("W4003", "Invalid physical field read");
    return st->vals[index];
}

static inline int32_t wysna_list_iteration_length_at(WVal value, const char* file, int32_t line, int32_t col) {
    WListBuf* b = wysna_as_list(value);
    if (W_UNLIKELY(!b)) wysna_list_iteration_fail_at("W4003", "For loop requires a list value", file, line, col);
    return b->len;
}

static inline WVal wysna_list_iteration_get_at(WVal value, int32_t index, const char* file, int32_t line, int32_t col) {
    WListBuf* b = wysna_as_list(value);
    if (W_UNLIKELY(!b)) wysna_list_iteration_fail_at("W4003", "For loop requires a list value", file, line, col);
    if (W_UNLIKELY(index < 0 || index >= b->len)) {
        wysna_runtime_set_position(file, line, col);
        wysna_runtime_fail("W4002", "List index out of bounds (index %" PRId32 ", length %" PRId32 ")", index, b->len);
    }
    return wysna_list_load(b, index);
}

static inline WVal wysna_list_iteration_get_owned_at(WVal value, int32_t index, const char* file, int32_t line, int32_t col) {
    return wysna_clone(wysna_list_iteration_get_at(value, index, file, line, col));
}

static inline int64_t wysna_list_get_int_at(WVal obj, int64_t idx, const char* file, int32_t line, int32_t col) {
    WListBuf* b = wysna_as_list(obj);
    if (W_UNLIKELY(!b || idx < 0 || idx >= b->len)) {
        wysna_runtime_set_position(file, line, col);
        wysna_runtime_fail("W4002", "List index out of bounds (index %" PRId64 ", length %" PRId32 ")", idx, b ? b->len : 0);
    }
    return wysna_as_int(wysna_list_load(b, (int32_t)idx));
}

static inline double wysna_list_get_float_at(WVal obj, int64_t idx, const char* file, int32_t line, int32_t col) {
    WListBuf* b = wysna_as_list(obj);
    if (W_UNLIKELY(!b || idx < 0 || idx >= b->len)) {
        wysna_runtime_set_position(file, line, col);
        wysna_runtime_fail("W4002", "List index out of bounds (index %" PRId64 ", length %" PRId32 ")", idx, b ? b->len : 0);
    }
    return wysna_as_float(wysna_list_load(b, (int32_t)idx));
}

static inline bool wysna_list_get_bool_at(WVal obj, int64_t idx, const char* file, int32_t line, int32_t col) {
    WListBuf* b = wysna_as_list(obj);
    if (W_UNLIKELY(!b || idx < 0 || idx >= b->len)) {
        wysna_runtime_set_position(file, line, col);
        wysna_runtime_fail("W4002", "List index out of bounds (index %" PRId64 ", length %" PRId32 ")", idx, b ? b->len : 0);
    }
    return wysna_as_bool(wysna_list_load(b, (int32_t)idx));
}

static inline int64_t wysna_list_iteration_get_int_at(WVal value, int32_t index, const char* file, int32_t line, int32_t col) {
    return wysna_as_int(wysna_list_iteration_get_at(value, index, file, line, col));
}

static inline double wysna_list_iteration_get_float_at(WVal value, int32_t index, const char* file, int32_t line, int32_t col) {
    return wysna_as_float(wysna_list_iteration_get_at(value, index, file, line, col));
}

static inline bool wysna_list_iteration_get_bool_at(WVal value, int32_t index, const char* file, int32_t line, int32_t col) {
    return wysna_as_bool(wysna_list_iteration_get_at(value, index, file, line, col));
}

static inline WVal wysna_not(WVal value) { return wysna_bool(!wysna_as_bool(value)); }
static inline WVal wysna_equal(WVal left, WVal right) { return wysna_bool(wysna_eq(left, right)); }
static inline WVal wysna_not_equal(WVal left, WVal right) { return wysna_bool(!wysna_eq(left, right)); }

static inline int64_t wysna_div_i64_at(int64_t ai, int64_t bi, const char* file, int32_t line, int32_t col) {
    if (bi == 0) {
        wysna_runtime_set_position(file, line, col);
        wysna_runtime_fail("W4001", "Division by zero");
    }
    if (ai == INT64_MIN && bi == -1) return INT64_MIN;
    return ai / bi;
}

static inline double wysna_div_f64_at(double af, double bf, const char* file, int32_t line, int32_t col) {
    if (bf == 0.0) {
        wysna_runtime_set_position(file, line, col);
        wysna_runtime_fail("W4001", "Division by zero");
    }
    return af / bf;
}

static inline int64_t wysna_mod_i64_at(int64_t ai, int64_t bi, const char* file, int32_t line, int32_t col) {
    if (bi == 0) {
        wysna_runtime_set_position(file, line, col);
        wysna_runtime_fail("W4001", "Modulo by zero");
    }
    if (ai == INT64_MIN && bi == -1) return 0;
    return ai % bi;
}

static inline int wysna_string_compare(WVal a, WVal b) {
    if (a.bits == b.bits) return 0;
    char sba[7], sbb[7];
    size_t la = 0, lb = 0;
    const char* da = wysna_str_bytes(a, sba, &la);
    const char* db = wysna_str_bytes(b, sbb, &lb);
    if (da == db && la == lb) return 0;
    size_t min_len = la < lb ? la : lb;
    int cmp = memcmp(da, db, min_len);
    if (cmp != 0) return cmp;
    return (la < lb) ? -1 : ((la > lb) ? 1 : 0);
}

#endif /* WYSNA_SDK_ABI_H */
