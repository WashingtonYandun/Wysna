/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_CORE_H
#define WYSNA_RUNTIME_CORE_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
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
    bool scalar_only;
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
    int32_t weak; /* Includes one implicit reference while the object is alive. */
    int32_t trial_rc;
    uint8_t color;
    bool buffered;
    WStructBuf* object;
    WWeakControl* cycle_next;
    WWeakControl* cycle_prev;
};

#if defined(__GNUC__) || defined(__clang__)
static inline int32_t wysna_atomic_load_i32(const int32_t* ptr) {
    return __atomic_load_n(ptr, __ATOMIC_ACQUIRE);
}
static inline void wysna_atomic_store_i32(int32_t* ptr, int32_t val) {
    __atomic_store_n(ptr, val, __ATOMIC_RELEASE);
}
static inline int32_t wysna_atomic_inc_i32(int32_t* ptr) {
    return __atomic_add_fetch(ptr, 1, __ATOMIC_RELAXED);
}
static inline int32_t wysna_atomic_dec_i32(int32_t* ptr) {
    return __atomic_sub_fetch(ptr, 1, __ATOMIC_ACQ_REL);
}
static inline bool wysna_atomic_dec_if_gt1_i32(int32_t* ptr) {
    int32_t cur = __atomic_load_n(ptr, __ATOMIC_RELAXED);
    while (cur > 1) {
        if (__atomic_compare_exchange_n(ptr, &cur, cur - 1, true, __ATOMIC_RELEASE, __ATOMIC_RELAXED)) {
            return true;
        }
    }
    return false;
}
static inline uint32_t wysna_atomic_load_u32(const uint32_t* ptr) {
    return __atomic_load_n(ptr, __ATOMIC_ACQUIRE);
}
static inline void wysna_atomic_store_u32(uint32_t* ptr, uint32_t val) {
    __atomic_store_n(ptr, val, __ATOMIC_RELEASE);
}
static inline int64_t wysna_atomic_add_i64(int64_t* ptr, int64_t delta) {
    return __atomic_add_fetch(ptr, delta, __ATOMIC_RELAXED);
}
static inline int64_t wysna_atomic_sub_i64(int64_t* ptr, int64_t delta) {
    return __atomic_sub_fetch(ptr, delta, __ATOMIC_RELAXED);
}
static inline void wysna_spinlock_acquire(volatile int* lock) {
    while (__atomic_test_and_set(lock, __ATOMIC_ACQUIRE)) {}
}
static inline void wysna_spinlock_release(volatile int* lock) {
    __atomic_clear(lock, __ATOMIC_RELEASE);
}
#else
static inline int32_t wysna_atomic_load_i32(const int32_t* ptr) { return *ptr; }
static inline void wysna_atomic_store_i32(int32_t* ptr, int32_t val) { *ptr = val; }
static inline int32_t wysna_atomic_inc_i32(int32_t* ptr) { return ++(*ptr); }
static inline int32_t wysna_atomic_dec_i32(int32_t* ptr) { return --(*ptr); }
static inline bool wysna_atomic_dec_if_gt1_i32(int32_t* ptr) {
    if (*ptr > 1) { --(*ptr); return true; }
    return false;
}
static inline uint32_t wysna_atomic_load_u32(const uint32_t* ptr) { return *ptr; }
static inline void wysna_atomic_store_u32(uint32_t* ptr, uint32_t val) { *ptr = val; }
static inline int64_t wysna_atomic_add_i64(int64_t* ptr, int64_t delta) { return (*ptr += delta); }
static inline int64_t wysna_atomic_sub_i64(int64_t* ptr, int64_t delta) { return (*ptr -= delta); }
static inline void wysna_spinlock_acquire(volatile int* lock) { (void)lock; }
static inline void wysna_spinlock_release(volatile int* lock) { (void)lock; }
#endif

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

/* Literal descriptors have static lifetime; only their lazy index is allocated. */
typedef struct WStringLiteral {
    WStringBuf buffer;
    struct WStringLiteral* next;
} WStringLiteral;

/*
 * Every generated program owns one explicit runtime context. For WebAssembly,
 * the context is stored in the module's linear memory and is therefore isolated
 * for each runtime instance. The compatibility macros keep the runtime ABI and
 * the existing internal call sites stable while state moves behind the context.
 */
typedef struct WysnaRuntimeContext {
    WStringLiteral* string_literals;
#ifdef W_STRING_METRICS
    uint64_t string_scan_steps;
#endif
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
} WysnaRuntimeContext;

static inline int64_t wysna_collect_cycles(void);

#ifdef WYSNA_SHARED_RUNTIME
extern WysnaRuntimeContext wysna_runtime_context;
#else
static WysnaRuntimeContext wysna_runtime_context = {0};
#endif
/* Diagnostics belong to the executing thread; allocation quotas remain program-wide. */
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
#ifdef WYSNA_SHARED_RUNTIME
extern WYSNA_THREAD_LOCAL WysnaThreadDiagnostics wysna_thread_diagnostics;
extern WYSNA_THREAD_LOCAL int32_t wysna_worker_depth;
#else
static WYSNA_THREAD_LOCAL WysnaThreadDiagnostics wysna_thread_diagnostics = {0};
/* Worker callbacks own their value graphs and cannot use the shared host registry. */
static WYSNA_THREAD_LOCAL int32_t wysna_worker_depth = 0;
#endif
#define wysna_g_string_literals (wysna_runtime_context.string_literals)
#ifdef W_STRING_METRICS
#define wysna_g_string_scan_steps (wysna_runtime_context.string_scan_steps)
#if defined(__GNUC__) || defined(__clang__)
#define W_STRING_SCAN_STEP() (__atomic_add_fetch(&wysna_g_string_scan_steps, 1, __ATOMIC_RELAXED))
#else
#define W_STRING_SCAN_STEP() (++wysna_g_string_scan_steps)
#endif
#else
#define W_STRING_SCAN_STEP() ((void)0)
#endif

/*
 * ABI v3 unifies every runtime value into an 8-byte NaN-boxed word (WVal == WVal64):
 * - 0x0000_0000_0000_0000: W_UNIT (zero-initialized globals/locals equal unit)
 * - 0x0000_0000_0000_0001..3: W_NONE, false, true
 * - 0x0000_8000_xxxx_xxxx_xxxx: raw 47-bit opaque W_POINTER (address with bit 47 set)
 * - 0x0001_0000_0000_0000..0xFFF1_0000_0000_0000: IEEE-754 double + WVAL_DOUBLE_OFFSET
 * - 0xFFF2..0xFFFF: 14 distinct 48-bit tag bands (inline int48, SSO 0..6B, strings, heap containers, boxes, ext)
 */
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

/* Backward-compatible constants for callers referencing WVal64 masks directly. */
#define WVAL64_UNIT_VAL       ((WVal){WVAL_UNIT_BITS})
#define WVAL64_NONE_VAL       ((WVal){WVAL_NONE_BITS})
#define WVAL64_CANON_NAN      ((WVal){WVAL_CANON_NAN_ENC})

typedef WVal (*WInvoke)(WVal environment, int32_t argc, const WVal* argv);

/* Native runtime failures retain the source operation that last entered Wysna. */
#define wysna_g_source_file (wysna_thread_diagnostics.source_file)
#define wysna_g_source_line (wysna_thread_diagnostics.source_line)
#define wysna_g_source_col (wysna_thread_diagnostics.source_col)

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

#if defined(__SANITIZE_ADDRESS__)
#define WYSNA_HAS_ASAN 1
#elif defined(__has_feature)
#if __has_feature(address_sanitizer)
#define WYSNA_HAS_ASAN 1
#endif
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

/* Abnormal termination exits before active C frames unwind, leaving NaN-boxed
 * WVal roots in stack/BSS that LSan cannot trace as heap pointers. Skip the
 * atexit leak scan only on those non-unwound exit paths under ASan while keeping
 * full LSan and WYSNA_MEM_CHECK verification on normal main() completion. */
static inline WYSNA_NORETURN void wysna_panic_exit(int status) {
    fflush(NULL);
    /* A worker must never run atexit cleanup that joins its own OS thread. */
    if (wysna_worker_depth > 0 || wysna_atomic_add_i64(&wysna_runtime_context.active_workers, 0) > 0) _Exit(status);
#if defined(WYSNA_HAS_ASAN) && !defined(__EMSCRIPTEN__) && !defined(__wasi__)
    _Exit(status);
#else
    exit(status);
#endif
}

static inline WYSNA_NORETURN void wysna_runtime_fail(const char* code, const char* format, ...) {
    bool worker_failure = wysna_worker_depth > 0 || wysna_atomic_add_i64(&wysna_runtime_context.active_workers, 0) > 0;
    if (worker_failure) wysna_spinlock_acquire(&wysna_runtime_context.worker_failure_lock);
    if (wysna_g_source_file && wysna_g_source_file[0] != '\0' && wysna_g_source_line > 0) {
        fprintf(stderr, "[%s] %s:%" PRId32 ":%" PRId32 " - ",
                code, wysna_g_source_file, wysna_g_source_line, wysna_g_source_col);
    } else {
        fprintf(stderr, "[%s] RuntimeError: ", code);
    }
    va_list args;
    va_start(args, format);
    vfprintf(stderr, format, args);
    va_end(args);
    fputc('\n', stderr);
    if (wysna_thread_diagnostics.call_depth > 1) {
        int32_t top = wysna_thread_diagnostics.call_depth < 64 ? wysna_thread_diagnostics.call_depth : 64;
        for (int32_t i = top - 1; i >= 0; i--) {
            const char* fn_name = wysna_thread_diagnostics.call_stack_fn[i];
            const char* f_file = wysna_thread_diagnostics.call_stack_file[i];
            int32_t f_line = wysna_thread_diagnostics.call_stack_line[i];
            if (fn_name && fn_name[0] != '\0') {
                fprintf(stderr, "  at %s (%s:%" PRId32 ")\n",
                        fn_name, (f_file && f_file[0] != '\0') ? f_file : "<unknown>", f_line);
            }
        }
    }
    if (worker_failure) {
        fflush(NULL);
        _Exit(1);
    }
    wysna_panic_exit(1);
}

static inline void wysna_sandbox_deny(const char* capability) {
    wysna_runtime_fail("W5006", "Sandbox capability '%s' is denied", capability ? capability : "unknown");
}

struct WClassVTable {
    int32_t count;
    const WInvoke* methods;
};

struct WIntBox {
    uint32_t kind;
    int32_t refcount; /* -1 = immortal literal, -2 = stack-scoped borrow, >=1 = heap ARC */
    int64_t value;
};

struct WEnumBuf {
    int32_t refcount; /* -1 = immortal unit literal, >=1 = heap ARC */
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

/* Reference-counting and memory instrumentation counters. */
#define wysna_g_alloc_count (wysna_runtime_context.alloc_count)
#define wysna_g_free_count (wysna_runtime_context.free_count)
#define wysna_g_retain_count (wysna_runtime_context.retain_count)
#define wysna_g_release_count (wysna_runtime_context.release_count)
#define wysna_g_live_objects (wysna_runtime_context.live_objects)

/* Copy-on-Write (CoW) Detach Telemetry Counters (B02) */
#define wysna_g_cow_detach_list (wysna_runtime_context.cow_detach_list)
#define wysna_g_cow_detach_dict (wysna_runtime_context.cow_detach_dict)
#define wysna_g_cow_detach_struct (wysna_runtime_context.cow_detach_struct)
#define wysna_g_cow_copied_slots (wysna_runtime_context.cow_copied_slots)
#define wysna_g_cow_copied_bytes (wysna_runtime_context.cow_copied_bytes)
#define wysna_g_cow_alloc_count (wysna_runtime_context.cow_alloc_count)
#define wysna_g_cow_allocated_bytes (wysna_runtime_context.cow_allocated_bytes)

static inline void wysna_cow_reset_stats(void) {
    wysna_g_cow_detach_list = 0;
    wysna_g_cow_detach_dict = 0;
    wysna_g_cow_detach_struct = 0;
    wysna_g_cow_copied_slots = 0;
    wysna_g_cow_copied_bytes = 0;
    wysna_g_cow_alloc_count = 0;
    wysna_g_cow_allocated_bytes = 0;
}

/*
 * Logical container allocation tracking for Wysna runtime.
 * NOTE on WYSNA_MEM_CHECK vs ASan/LSan:
 * wysna_mem_on_alloc() and wysna_mem_on_free() track high-level container lifecycle
 * (lists, dicts, objects, structs, tuples, strings) as a fast runtime invariant.
 * Comprehensive byte-level leak detection and memory safety are enforced by
 * Clang/GCC AddressSanitizer and LeakSanitizer (ASan/LSan) in CI and test suites.
 */
static inline void wysna_mem_on_alloc(void) {
    wysna_atomic_add_i64(&wysna_g_alloc_count, 1);
    wysna_atomic_add_i64(&wysna_g_live_objects, 1);
}

static inline void wysna_mem_on_free(void) {
    wysna_atomic_add_i64(&wysna_g_free_count, 1);
    int64_t live = wysna_atomic_sub_i64(&wysna_g_live_objects, 1);
    if (live < 0) {
        wysna_runtime_fail("W4003", "Memory safety violation (live_object_count underflow: %" PRId64 ")", live);
    }
}

static inline void wysna_check_refcount(int32_t rc, const char* op) {
    if (rc <= 0) {
        wysna_runtime_fail("W4003", "Memory safety violation (%s on object with refcount %" PRId32 ")", op, rc);
    }
}

static inline void wysna_check_retain_count(int32_t rc, const char* op) {
    if (rc == INT32_MAX) {
        wysna_runtime_fail("W4003", "Memory safety violation (reference count overflow during %s)", op);
    }
    wysna_check_refcount(rc, op);
}

static inline void wysna_init_quotas(void) {
    if (wysna_runtime_context.quotas_initialized) return;
    wysna_runtime_context.quotas_initialized = true;
#ifdef WYSNA_DEFAULT_MAX_STEPS
    wysna_runtime_context.max_steps = (int64_t)(WYSNA_DEFAULT_MAX_STEPS);
#endif
#ifdef WYSNA_DEFAULT_MAX_HEAP_BYTES
    wysna_runtime_context.max_heap_bytes = (int64_t)(WYSNA_DEFAULT_MAX_HEAP_BYTES);
#endif
    const char* steps_env = getenv("WYSNA_MAX_STEPS");
    if (steps_env && steps_env[0] != '\0') {
        int64_t val = (int64_t)strtoll(steps_env, NULL, 10);
        if (val > 0) wysna_runtime_context.max_steps = val;
    }
    const char* heap_env = getenv("WYSNA_MAX_HEAP_BYTES");
    if (heap_env && heap_env[0] != '\0') {
        int64_t val = (int64_t)strtoll(heap_env, NULL, 10);
        if (val > 0) wysna_runtime_context.max_heap_bytes = val;
    } else {
        const char* mb_env = getenv("WYSNA_MAX_HEAP_MB");
        if (mb_env && mb_env[0] != '\0') {
            int64_t val = (int64_t)strtoll(mb_env, NULL, 10);
            if (val > 0) wysna_runtime_context.max_heap_bytes = val * 1024LL * 1024LL;
        }
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

static inline void wysna_charge_heap_bytes(size_t size) {
    if (!wysna_runtime_context.quotas_initialized) wysna_init_quotas();
    if (wysna_runtime_context.max_heap_bytes > 0 && size > 0) {
        int64_t allocated = wysna_atomic_add_i64(&wysna_runtime_context.heap_bytes_allocated, (int64_t)size);
        if (allocated > wysna_runtime_context.max_heap_bytes && wysna_worker_depth == 0 && !wysna_runtime_context.collecting_cycles) {
            wysna_collect_cycles();
        }
        if (wysna_atomic_add_i64(&wysna_runtime_context.heap_bytes_allocated, 0) > wysna_runtime_context.max_heap_bytes) {
            wysna_runtime_fail("W4004", "Execution heap memory quota exceeded (limit: %" PRId64 " bytes)",
                               wysna_runtime_context.max_heap_bytes);
        }
    }
}

static inline void* wysna_alloc_safe(size_t size) {
    if (size == 0) return NULL;
    wysna_charge_heap_bytes(size);
    void* ptr = malloc(size);
    if (!ptr) {
        wysna_runtime_fail("W4003", "Out of memory (failed to allocate %zu bytes)", size);
    }
    return ptr;
}

static inline void* wysna_realloc_safe(void* old_ptr, size_t new_size) {
    if (new_size == 0) {
        free(old_ptr);
        return NULL;
    }
    wysna_charge_heap_bytes(new_size);
    void* ptr = realloc(old_ptr, new_size);
    if (!ptr) {
        wysna_runtime_fail("W4003", "Out of memory (failed to reallocate %zu bytes)", new_size);
    }
    return ptr;
}

#define wysna_g_argc (wysna_runtime_context.argc)
#define wysna_g_argv (wysna_runtime_context.argv)
#ifdef _WIN32
#define wysna_g_argv_allocated (wysna_runtime_context.argv_allocated)
#endif

static inline void wysna_runtime_cleanup_impl(bool verify_live_objects) {
#ifdef _WIN32
    if (wysna_g_argv_allocated && wysna_g_argv) {
        for (int i = 0; i < wysna_g_argc; i++) {
            if (wysna_g_argv[i]) free(wysna_g_argv[i]);
        }
        free(wysna_g_argv);
        wysna_g_argv = NULL;
        wysna_g_argv_allocated = false;
    }
#endif
    if (wysna_runtime_context.host_async_cleanup) {
        wysna_runtime_context.host_async_cleanup();
    }
    while (wysna_g_string_literals) {
        WStringLiteral* literal = wysna_g_string_literals;
        wysna_g_string_literals = literal->next;
        free(literal->buffer.codepoint_offsets);
        memset(&literal->buffer, 0, sizeof(literal->buffer));
        literal->next = NULL;
    }
    const char* collect_env = getenv("WYSNA_COLLECT_CYCLES");
    if (verify_live_objects && collect_env && strcmp(collect_env, "1") == 0) {
        wysna_collect_cycles();
    }
    while (wysna_runtime_context.purple_head) {
        WWeakControl* next = wysna_runtime_context.purple_head->cycle_next;
        wysna_runtime_context.purple_head->cycle_next = NULL;
        wysna_runtime_context.purple_head->cycle_prev = NULL;
        wysna_runtime_context.purple_head->buffered = false;
        wysna_runtime_context.purple_head = next;
    }
    const char* stats_env = getenv("WYSNA_MEM_STATS");
    if (stats_env && strcmp(stats_env, "1") == 0) {
        fprintf(stderr, "[WYSNA_MEM_STATS] alloc=%" PRId64 " free=%" PRId64 " retain=%" PRId64 " release=%" PRId64 " live=%" PRId64 "\n",
                wysna_g_alloc_count, wysna_g_free_count, wysna_g_retain_count, wysna_g_release_count, wysna_g_live_objects);
    }
    const char* cow_env = getenv("WYSNA_COW_METRICS");
    if (cow_env && strcmp(cow_env, "1") == 0) {
        fprintf(stderr, "[WYSNA_COW_METRICS] detach_list=%" PRId64 " detach_dict=%" PRId64 " detach_struct=%" PRId64 " copied_slots=%" PRId64 " copied_bytes=%" PRId64 " allocated_bytes=%" PRId64 " allocs=%" PRId64 "\n",
                wysna_g_cow_detach_list, wysna_g_cow_detach_dict, wysna_g_cow_detach_struct, wysna_g_cow_copied_slots, wysna_g_cow_copied_bytes, wysna_g_cow_allocated_bytes, wysna_g_cow_alloc_count);
    }
    const char* check_env = getenv("WYSNA_MEM_CHECK");
    if (verify_live_objects && check_env && strcmp(check_env, "1") == 0) {
        if (wysna_g_live_objects != 0 || wysna_g_alloc_count != wysna_g_free_count) {
            fprintf(stderr, "[W4003] RuntimeError: Memory leak detected at exit (alloc=%" PRId64 ", free=%" PRId64 ", retain=%" PRId64 ", release=%" PRId64 ", live=%" PRId64 ")\n",
                    wysna_g_alloc_count, wysna_g_free_count, wysna_g_retain_count, wysna_g_release_count, wysna_g_live_objects);
            exit(1);
        }
    }
}

/* Explicit exit ends the process with active frames. Only normal completion can
 * verify that their values have been released. */
static inline void wysna_runtime_cleanup(void) {
    wysna_runtime_cleanup_impl(true);
}

static inline void wysna_runtime_cleanup_for_exit(void) {
    wysna_runtime_cleanup_impl(false);
}

#endif /* WYSNA_RUNTIME_CORE_H */
