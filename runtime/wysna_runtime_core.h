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

struct WWeakControl {
    int32_t strong;
    int32_t weak; /* Includes one implicit reference while the object is alive. */
    WStructBuf* object;
};

typedef struct WPointerOwner {
    int32_t refcount;
    int64_t (*close)(void*);
    void* address;
} WPointerOwner;

typedef struct WBoxBuf WBoxBuf;
typedef struct WFunctionBuf WFunctionBuf;

typedef struct {
    const char* enum_name;
    const char* variant;
    WBoxBuf* box;
} WEnumVal;

typedef struct {
    int32_t refcount;
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
    const char* source_file;
    int32_t source_line;
    int32_t source_col;
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
    int argc;
    char** argv;
#ifdef _WIN32
    bool argv_allocated;
#endif
} WysnaRuntimeContext;

static WysnaRuntimeContext wysna_runtime_context = {0};
#define wysna_g_string_literals (wysna_runtime_context.string_literals)
#ifdef W_STRING_METRICS
#define wysna_g_string_scan_steps (wysna_runtime_context.string_scan_steps)
#define W_STRING_SCAN_STEP() (++wysna_g_string_scan_steps)
#else
#define W_STRING_SCAN_STEP() ((void)0)
#endif

/* ABI v2 keeps string metadata in its variant rather than in every value. */
#define W_RUNTIME_ABI_VERSION 2
struct WVal {
    WTag tag;
    union {
        int64_t i;
        double f;
        bool b;
        struct {
            const char* data;
            WStringBuf* owner;
            size_t length;
        } string;
        WListBuf* list;
        WDictBuf* dict;
        WTupleBuf* tuple;
        WStructBuf* st;
        WEnumVal en;
        WBoxBuf* box;
        WWeakControl* weak;
        WFunctionBuf* function;
        void* pointer;
        struct { void* address; WPointerOwner* owner; } handle;
    } as;
};

static inline bool wysna_value_is_error_type(WVal value, const char* kind, const char* name) {
    if (strcmp(kind, "int") == 0) return value.tag == W_INT;
    if (strcmp(kind, "float") == 0) return value.tag == W_FLOAT;
    if (strcmp(kind, "bool") == 0) return value.tag == W_BOOL;
    if (strcmp(kind, "str") == 0) return value.tag == W_STR;
    if (strcmp(kind, "struct") == 0) {
        return value.tag == W_STRUCT && value.as.st && (value.as.st->name == name || strcmp(value.as.st->name, name) == 0);
    }
    if (strcmp(kind, "enum") == 0) {
        return value.tag == W_ENUM && value.as.en.enum_name && (value.as.en.enum_name == name || strcmp(value.as.en.enum_name, name) == 0);
    }
    return false;
}

typedef WVal (*WInvoke)(WVal environment, int32_t argc, const WVal* argv);

/* Native runtime failures retain the source operation that last entered Wysna. */
#define wysna_g_source_file (wysna_runtime_context.source_file)
#define wysna_g_source_line (wysna_runtime_context.source_line)
#define wysna_g_source_col (wysna_runtime_context.source_col)

static inline void wysna_runtime_set_position(const char* file, int32_t line, int32_t col) {
    wysna_g_source_file = file;
    wysna_g_source_line = line;
    wysna_g_source_col = col;
}

static inline void wysna_runtime_fail(const char* code, const char* format, ...) {
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
    exit(1);
}

struct WClassVTable {
    int32_t count;
    const WInvoke* methods;
};
static inline WVal wysna_virtual_call(WVal receiver, int32_t slot, int32_t argc, const WVal* argv) {
    const WClassVTable* table = receiver.tag == W_OBJECT ? receiver.as.st->vtable : NULL;
    if (!table || slot < 0 || slot >= table->count || !table->methods[slot]) {
        fprintf(stderr, "[W4003] RuntimeError: Invalid virtual method dispatch\n"); exit(1);
    }
    return table->methods[slot](receiver, argc, argv);
}

struct WFunctionBuf {
    int32_t refcount;
    WInvoke invoke;
    WVal environment;
};

struct WBoxBuf {
    int32_t refcount;
    WVal val;
};

/* Deterministic RC & Memory Instrumentation Counters (v0.1.3) */
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
    wysna_g_alloc_count++;
    wysna_g_live_objects++;
}

static inline void wysna_mem_on_free(void) {
    wysna_g_free_count++;
    wysna_g_live_objects--;
    if (wysna_g_live_objects < 0) {
        fprintf(stderr, "[W4003] RuntimeError: Memory safety violation (live_object_count underflow: %" PRId64 ")\n", wysna_g_live_objects);
        exit(1);
    }
}

static inline void wysna_check_refcount(int32_t rc, const char* op) {
    if (rc <= 0) {
        fprintf(stderr, "[W4003] RuntimeError: Memory safety violation (%s on object with refcount %" PRId32 ")\n", op, rc);
        exit(1);
    }
}

static inline void* wysna_alloc_safe(size_t size) {
    if (size == 0) return NULL;
    void* ptr = malloc(size);
    if (!ptr) {
        fprintf(stderr, "[W4003] RuntimeError: Out of memory (failed to allocate %zu bytes)\n", size);
        exit(1);
    }
    return ptr;
}

static inline void* wysna_realloc_safe(void* old_ptr, size_t new_size) {
    if (new_size == 0) {
        free(old_ptr);
        return NULL;
    }
    void* ptr = realloc(old_ptr, new_size);
    if (!ptr) {
        fprintf(stderr, "[W4003] RuntimeError: Out of memory (failed to reallocate %zu bytes)\n", new_size);
        exit(1);
    }
    return ptr;
}

#define wysna_g_argc (wysna_runtime_context.argc)
#define wysna_g_argv (wysna_runtime_context.argv)
#ifdef _WIN32
#define wysna_g_argv_allocated (wysna_runtime_context.argv_allocated)
#endif

static inline void wysna_runtime_cleanup(void) {
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
    while (wysna_g_string_literals) {
        WStringLiteral* literal = wysna_g_string_literals;
        wysna_g_string_literals = literal->next;
        free(literal->buffer.codepoint_offsets);
        memset(&literal->buffer, 0, sizeof(literal->buffer));
        literal->next = NULL;
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
    if (check_env && strcmp(check_env, "1") == 0) {
        if (wysna_g_live_objects != 0 || wysna_g_alloc_count != wysna_g_free_count) {
            fprintf(stderr, "[W4003] RuntimeError: Memory leak detected at exit (alloc=%" PRId64 ", free=%" PRId64 ", retain=%" PRId64 ", release=%" PRId64 ", live=%" PRId64 ")\n",
                    wysna_g_alloc_count, wysna_g_free_count, wysna_g_retain_count, wysna_g_release_count, wysna_g_live_objects);
            exit(1);
        }
    }
}

#endif /* WYSNA_RUNTIME_CORE_H */
