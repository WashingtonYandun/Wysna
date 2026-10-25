/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_MEMORY_H
#define WYSNA_RUNTIME_MEMORY_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
#endif

/* Deterministic RC + Copy-on-Write */
static inline WVal wysna_clone(WVal v) {
    if (v.tag == W_POINTER && v.as.handle.owner) {
        wysna_check_refcount(v.as.handle.owner->refcount, "retain/resource");
        v.as.handle.owner->refcount++;
        wysna_g_retain_count++;
    } else if (v.tag == W_FUNCTION) {
        wysna_check_refcount(v.as.function->refcount, "retain/function");
        v.as.function->refcount++;
        wysna_g_retain_count++;
    } else if (v.tag == W_STR && v.as.string.owner) {
        if (v.as.string.owner->refcount == -1) return v;
        wysna_check_refcount(v.as.string.owner->refcount, "retain/string");
        v.as.string.owner->refcount++;
        wysna_g_retain_count++;
    } else if (v.tag == W_LIST && v.as.list) {
        wysna_check_refcount(v.as.list->refcount, "retain/list");
        v.as.list->refcount++;
        wysna_g_retain_count++;
    } else if (v.tag == W_DICT && v.as.dict) {
        wysna_check_refcount(v.as.dict->refcount, "retain/dict");
        v.as.dict->refcount++;
        wysna_g_retain_count++;
    } else if (v.tag == W_WEAK && v.as.weak) {
        wysna_check_refcount(v.as.weak->weak, "retain/weak");
        v.as.weak->weak++;
        wysna_g_retain_count++;
    } else if (v.tag == W_OBJECT && v.as.st) {
        wysna_check_refcount(v.as.st->control->strong, "retain/object");
        v.as.st->control->strong++;
        wysna_g_retain_count++;
    } else if (v.tag == W_STRUCT && v.as.st) {
        wysna_check_refcount(v.as.st->refcount, "retain/struct_or_object");
        v.as.st->refcount++;
        wysna_g_retain_count++;
    } else if (v.tag == W_TUPLE && v.as.tuple) {
        wysna_check_refcount(v.as.tuple->refcount, "retain/tuple");
        v.as.tuple->refcount++;
        wysna_g_retain_count++;
    } else if ((v.tag == W_OK || v.tag == W_ERR) && v.as.box) {
        wysna_check_refcount(v.as.box->refcount, "retain/box");
        v.as.box->refcount++;
        wysna_g_retain_count++;
    } else if (v.tag == W_ENUM && v.as.en.box) {
        wysna_check_refcount(v.as.en.box->refcount, "retain/enum_box");
        v.as.en.box->refcount++;
        wysna_g_retain_count++;
    }
    return v;
}

/*
 * Releasing a deeply nested value must not recurse: a 100k-deep list, tuple, enum
 * payload chain or struct overflowed the C stack and died with SIGSEGV while
 * freeing the program's result. `wysna_release` therefore frees one container and
 * defers its children onto an explicit work list (wysna_release_deferred), which is
 * drained iteratively in wysna_release.
 */
static WVal* wysna_release_deferred(WVal* slot, int32_t* produced);

#if defined(_MSC_VER)
#define W_RUNTIME_NOINLINE __declspec(noinline)
#elif defined(__GNUC__) || defined(__clang__)
#define W_RUNTIME_NOINLINE __attribute__((noinline))
#else
#define W_RUNTIME_NOINLINE
#endif

/* Keep the graph work list out of leaf callers, even under automatic inlining. */
static W_RUNTIME_NOINLINE void wysna_release_graph(WVal v);

/* Leaf values never need the graph work list, including strings owned by a container. */
static inline void wysna_release_string(WStringBuf* buffer) {
    if (!buffer || buffer->refcount == -1) return;
    wysna_check_refcount(buffer->refcount, "release/string");
    wysna_g_release_count++;
    if (--buffer->refcount == 0) {
        free(buffer->data);
        free(buffer->codepoint_offsets);
        free(buffer);
        wysna_mem_on_free();
    }
}

static inline bool wysna_is_leaf(WVal value) {
    return value.tag <= W_BOOL || value.tag == W_STR || (value.tag == W_POINTER && !value.as.handle.owner);
}

static inline void wysna_release(WVal v) {
    if (wysna_is_leaf(v)) {
        if (v.tag == W_STR) wysna_release_string(v.as.string.owner);
        return;
    }
    if ((v.tag == W_OK || v.tag == W_ERR) && v.as.box) {
        WBoxBuf* box = v.as.box;
        /* Shared boxes and leaf payloads need neither a work list nor child storage. */
        if (box->refcount != 1 || wysna_is_leaf(box->val)) {
            wysna_check_refcount(box->refcount, "release/box");
            wysna_g_release_count++;
            if (--box->refcount == 0) {
                WVal inner = box->val;
                free(box);
                wysna_mem_on_free();
                if (inner.tag == W_STR) wysna_release_string(inner.as.string.owner);
            }
            return;
        }
    }
    wysna_release_graph(v);
}

static void wysna_release_graph(WVal v) {
    WVal stack_work[64];
    WVal* work = stack_work;
    int32_t count = 0;
    int32_t capacity = 64;
    bool heap_work = false;

    work[count++] = v;
    while (count > 0) {
        WVal current = work[--count];
        int32_t produced = 0;
        WVal* children = wysna_release_deferred(&current, &produced);
        if (produced > 0) {
            if (count + produced > capacity) {
                int32_t new_capacity = capacity;
                while (new_capacity < count + produced) new_capacity *= 2;
                WVal* grown = (WVal*)malloc(sizeof(WVal) * (size_t)new_capacity);
                if (!grown) { fprintf(stderr, "[W4003] RuntimeError: out of memory\n"); exit(1); }
                memcpy(grown, work, sizeof(WVal) * (size_t)count);
                if (heap_work) free(work);
                work = grown;
                capacity = new_capacity;
                heap_work = true;
            }
            for (int32_t i = 0; i < produced; i++) work[count + i] = children[i];
            count += produced;
            free(children);
        }
    }
    if (heap_work) free(work);
}

/*
 * Frees one container level and returns a malloc'd array of the children that still
 * need releasing. The array is drained by the caller's work list, so no C stack is
 * consumed per nesting level.
 */
static WVal* wysna_release_deferred(WVal* slot, int32_t* produced) {
    /*
     * Frees exactly one container level and returns a malloc'd array of the children
     * that still have to be released, draining nothing itself. Callers append those to
     * their work list, which is what keeps `wysna_release` off the C stack: without this a
     * 100k-deep list, tuple, enum payload chain or struct overflowed the stack and
     * died with SIGSEGV while freeing the program result.
     *
     * `produced` is the number of entries written to the returned array.
     */
    WVal v = *slot;
    *produced = 0;
    if (v.tag == W_POINTER && v.as.handle.owner) {
        WPointerOwner* owner = v.as.handle.owner;
        wysna_check_refcount(owner->refcount, "release/resource");
        wysna_g_release_count++;
        if (--owner->refcount == 0) {
            owner->close(owner->address);
            free(owner);
            wysna_mem_on_free();
        }
    } else if (v.tag == W_FUNCTION) {
        wysna_check_refcount(v.as.function->refcount, "release/function");
        wysna_g_release_count++;
        if (--v.as.function->refcount == 0) {
            WVal environment = v.as.function->environment;
            free(v.as.function);
            wysna_mem_on_free();
            if (environment.tag != W_UNIT) {
                WVal* out = (WVal*)malloc(sizeof(WVal));
                if (!out) { fprintf(stderr, "[W4003] RuntimeError: out of memory\n"); exit(1); }
                out[0] = environment;
                *produced = 1;
                return out;
            }
        }
    } else if (v.tag == W_STR && v.as.string.owner) {
        wysna_release_string(v.as.string.owner);
    } else if (v.tag == W_LIST && v.as.list) {
        WListBuf* b = v.as.list;
        wysna_check_refcount(b->refcount, "release/list");
        wysna_g_release_count++;
        b->refcount--;
        if (b->refcount == 0) {
            b->refcount = -1;
            WVal* out = b->len > 0 ? (WVal*)malloc(sizeof(WVal) * (size_t)b->len) : NULL;
            int32_t len = b->len;
            if (len > 0 && !out) { fprintf(stderr, "[W4003] RuntimeError: out of memory\n"); exit(1); }
            for (int32_t i = 0; i < len; i++) out[i] = b->data[i];
            free(b->data);
            free(b);
            wysna_mem_on_free();
            *produced = len;
            return out;
        }
    } else if (v.tag == W_DICT && v.as.dict) {
        WDictBuf* d = v.as.dict;
        wysna_check_refcount(d->refcount, "release/dict");
        wysna_g_release_count++;
        d->refcount--;
        if (d->refcount == 0) {
            d->refcount = -1;
            int32_t total = d->len * 2;
            WVal* out = total > 0 ? (WVal*)malloc(sizeof(WVal) * (size_t)total) : NULL;
            if (total > 0 && !out) { fprintf(stderr, "[W4003] RuntimeError: out of memory\n"); exit(1); }
            for (int32_t i = 0; i < d->len; i++) {
                out[i * 2] = d->keys[i];
                out[i * 2 + 1] = d->vals[i];
            }
            free(d->keys);
            free(d->vals);
            free(d->buckets);
            free(d);
            wysna_mem_on_free();
            *produced = total;
            return out;
        }
    } else if (v.tag == W_WEAK && v.as.weak) {
        WWeakControl* control = v.as.weak;
        wysna_check_refcount(control->weak, "release/weak");
        wysna_g_release_count++;
        if (--control->weak == 0) {
            free(control);
            wysna_mem_on_free();
        }
    } else if (v.tag == W_OBJECT && v.as.st) {
        WStructBuf* object = v.as.st;
        WWeakControl* control = object->control;
        wysna_check_refcount(control->strong, "release/object");
        wysna_g_release_count++;
        if (--control->strong == 0) {
            control->object = NULL;
            int32_t len = object->len;
            WVal* out = len > 0 ? (WVal*)malloc(sizeof(WVal) * (size_t)len) : NULL;
            if (len > 0 && !out) { fprintf(stderr, "[W4003] RuntimeError: out of memory\n"); exit(1); }
            for (int32_t i = 0; i < len; i++) out[i] = object->vals[i];
            free(object->keys);
            free(object->vals);
            free(object);
            wysna_mem_on_free();
            if (--control->weak == 0) {
                free(control);
                wysna_mem_on_free();
            }
            *produced = len;
            return out;
        }
    } else if (v.tag == W_STRUCT && v.as.st) {
        WStructBuf* st = v.as.st;
        wysna_check_refcount(st->refcount, "release/struct_or_object");
        wysna_g_release_count++;
        st->refcount--;
        if (st->refcount == 0) {
            st->refcount = -1;
            int32_t len = st->len;
            WVal* out = len > 0 ? (WVal*)malloc(sizeof(WVal) * (size_t)len) : NULL;
            if (len > 0 && !out) { fprintf(stderr, "[W4003] RuntimeError: out of memory\n"); exit(1); }
            for (int32_t i = 0; i < len; i++) out[i] = st->vals[i];
            free(st->keys);
            free(st->vals);
            free(st);
            wysna_mem_on_free();
            *produced = len;
            return out;
        }
    } else if (v.tag == W_TUPLE && v.as.tuple) {
        WTupleBuf* t = v.as.tuple;
        wysna_check_refcount(t->refcount, "release/tuple");
        wysna_g_release_count++;
        t->refcount--;
        if (t->refcount == 0) {
            t->refcount = -1;
            int32_t len = t->len;
            WVal* out = len > 0 ? (WVal*)malloc(sizeof(WVal) * (size_t)len) : NULL;
            if (len > 0 && !out) { fprintf(stderr, "[W4003] RuntimeError: out of memory\n"); exit(1); }
            for (int32_t i = 0; i < len; i++) out[i] = t->items[i];
            free(t->items);
            free(t);
            wysna_mem_on_free();
            *produced = len;
            return out;
        }
    } else if ((v.tag == W_OK || v.tag == W_ERR) && v.as.box) {
        WBoxBuf* bx = v.as.box;
        wysna_check_refcount(bx->refcount, "release/box");
        wysna_g_release_count++;
        bx->refcount--;
        if (bx->refcount == 0) {
            bx->refcount = -1;
            WVal inner = bx->val;
            free(bx);
            wysna_mem_on_free();
            if (wysna_is_leaf(inner)) {
                if (inner.tag == W_STR) wysna_release_string(inner.as.string.owner);
                return NULL;
            }
            WVal* out = (WVal*)malloc(sizeof(WVal));
            if (!out) { fprintf(stderr, "[W4003] RuntimeError: out of memory\n"); exit(1); }
            out[0] = inner;
            *produced = 1;
            return out;
        }
    } else if (v.tag == W_ENUM && v.as.en.box) {
        WBoxBuf* bx = v.as.en.box;
        wysna_check_refcount(bx->refcount, "release/enum_box");
        wysna_g_release_count++;
        bx->refcount--;
        if (bx->refcount == 0) {
            bx->refcount = -1;
            WVal inner = bx->val;
            free(bx);
            wysna_mem_on_free();
            WVal* out = (WVal*)malloc(sizeof(WVal));
            if (!out) { fprintf(stderr, "[W4003] RuntimeError: out of memory\n"); exit(1); }
            out[0] = inner;
            *produced = 1;
            return out;
        }
    }
    return NULL;
}

/* Consumes the environment; invocation borrows function, environment and arguments. */
static inline WVal wysna_function_new(WInvoke invoke, WVal environment) {
    WFunctionBuf* function = (WFunctionBuf*)wysna_alloc_safe(sizeof(WFunctionBuf));
    function->refcount = 1; function->invoke = invoke; function->environment = environment;
    wysna_mem_on_alloc();
    WVal value = wysna_unit(); value.tag = W_FUNCTION; value.as.function = function; return value;
}

static inline WVal wysna_call(WVal function, int32_t argc, const WVal* argv) {
    if (function.tag != W_FUNCTION) {
        fprintf(stderr, "[W4003] RuntimeError: Indirect call requires function\n"); exit(1);
    }
    return function.as.function->invoke(function.as.function->environment, argc, argv);
}

static WVal wysna_discard_result_value(WVal value) {
    if (value.tag == W_ERR) return value;
    wysna_release(value);
    return wysna_ok(wysna_unit());
}

static inline void wysna_release_array(int32_t count, WVal* arr) {
    for (int32_t i = 0; i < count; i++) {
        wysna_release(arr[i]);
    }
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
    return value.tag == W_NONE || (value.tag == W_ENUM &&
        strcmp(value.as.en.enum_name, "Option") == 0 && strcmp(value.as.en.variant, "None") == 0);
}

static inline WVal wysna_peek_optional(WVal value) {
    if (value.tag == W_ENUM && strcmp(value.as.en.enum_name, "Option") == 0 &&
        strcmp(value.as.en.variant, "Some") == 0 && value.as.en.box) return value.as.en.box->val;
    return value;
}

static inline WVal wysna_unwrap_optional(WVal value) {
    if (value.tag == W_ENUM && strcmp(value.as.en.enum_name, "Option") == 0 &&
        strcmp(value.as.en.variant, "Some") == 0 && value.as.en.box) {
        return wysna_clone(value.as.en.box->val);
    }
    return wysna_clone(value);
}

static inline WVal wysna_unwrap_ok(WVal v) {
    if (v.tag == W_OK && v.as.box) {
        return wysna_clone(v.as.box->val);
    }
    return wysna_clone(v);
}

static inline WVal wysna_unwrap_success(WVal value) {
    if (value.tag == W_ENUM && strcmp(value.as.en.enum_name, "Option") == 0)
        return wysna_unwrap_optional(value);
    return wysna_unwrap_ok(value);
}

static inline WVal wysna_unwrap_err(WVal v) {
    if (v.tag == W_ERR && v.as.box) {
        return wysna_clone(v.as.box->val);
    }
    return wysna_none();
}

static inline WVal wysna_peek_ok(WVal v) {
    if (v.tag == W_OK && v.as.box) {
        return v.as.box->val;
    }
    return v;
}

static inline WVal wysna_peek_err(WVal v) {
    if (v.tag == W_ERR && v.as.box) {
        return v.as.box->val;
    }
    return wysna_none();
}

static inline bool wysna_is_enum_variant(WVal value, const char* enum_name, const char* variant) {
    return value.tag == W_ENUM && value.as.en.enum_name && value.as.en.variant &&
        strcmp(value.as.en.enum_name, enum_name) == 0 && strcmp(value.as.en.variant, variant) == 0;
}

static inline WVal wysna_peek_enum_payload(WVal value) {
    if (value.tag == W_ENUM && value.as.en.box) return value.as.en.box->val;
    fprintf(stderr, "[W4003] RuntimeError: enum payload access on invalid value\n");
    exit(1);
}

static inline void wysna_list_detach(WVal* v) {
    if (v->tag != W_LIST || !v->as.list) return;
    WListBuf* old_b = v->as.list;
    wysna_check_refcount(old_b->refcount, "detach/list");
    if (old_b->refcount <= 1) return;
    old_b->refcount--;
    wysna_g_release_count++;
    WListBuf* new_b = (WListBuf*)wysna_alloc_safe(sizeof(WListBuf));
    new_b->refcount = 1;
    new_b->len = old_b->len;
    new_b->cap = old_b->cap > 0 ? old_b->cap : 4;
    new_b->data = (WVal*)wysna_alloc_safe(sizeof(WVal) * new_b->cap);
    wysna_mem_on_alloc();

    /* B02: CoW detach telemetry */
    wysna_g_cow_detach_list++;
    wysna_g_cow_copied_slots += old_b->len;
    wysna_g_cow_copied_bytes += (int64_t)(sizeof(WVal) * old_b->len);
    wysna_g_cow_allocated_bytes += (int64_t)(sizeof(WListBuf) + (sizeof(WVal) * new_b->cap));
    wysna_g_cow_alloc_count += 2;

    for (int32_t i = 0; i < old_b->len; i++) {
        new_b->data[i] = wysna_clone(old_b->data[i]);
    }
    v->as.list = new_b;
}

/* Dense insertion-order entries with an open-addressed lookup index. */
static inline bool wysna_eq(WVal a, WVal b);
static inline uint64_t wysna_key_hash_bytes(const unsigned char* data, size_t length, uint64_t hash) {
    for (size_t i = 0; i < length; i++) { hash ^= data[i]; hash *= UINT64_C(1099511628211); }
    return hash;
}
static inline uint64_t wysna_key_hash(WVal value) {
    uint64_t hash = UINT64_C(14695981039346656037) ^ (uint64_t)value.tag;
    if (value.tag == W_STR) return wysna_key_hash_bytes((const unsigned char*)value.as.string.data, value.as.string.length, hash);
    if (value.tag == W_INT || value.tag == W_BOOL) {
        uint64_t number = value.tag == W_INT ? (uint64_t)value.as.i : (uint64_t)value.as.b;
        number ^= number >> 30; number *= UINT64_C(0xbf58476d1ce4e5b9);
        number ^= number >> 27; number *= UINT64_C(0x94d049bb133111eb);
        return hash ^ number ^ (number >> 31);
    }
    if (value.tag == W_ENUM) {
        hash = wysna_key_hash_bytes((const unsigned char*)value.as.en.enum_name, strlen(value.as.en.enum_name), hash);
        hash = wysna_key_hash_bytes((const unsigned char*)value.as.en.variant, strlen(value.as.en.variant), hash);
        if (value.as.en.box) {
            WVal payload = value.as.en.box->val;
            if (payload.tag == W_TUPLE) {
                for (int32_t i = 0; i < payload.as.tuple->len; i++) hash = (hash ^ wysna_key_hash(payload.as.tuple->items[i])) * UINT64_C(1099511628211);
            } else hash ^= wysna_key_hash(payload);
        }
    }
    return hash;
}
static inline int32_t wysna_dict_find(WDictBuf* dict, WVal key) {
    uint32_t mask = (uint32_t)dict->bucket_count - 1;
    uint32_t slot = (uint32_t)wysna_key_hash(key) & mask;
    while (dict->buckets[slot] >= 0) {
        int32_t index = dict->buckets[slot];
        if (wysna_eq(dict->keys[index], key)) return index;
        slot = (slot + 1) & mask;
    }
    return -1;
}
static inline void wysna_dict_index_entry(WDictBuf* dict, int32_t index) {
    uint32_t mask = (uint32_t)dict->bucket_count - 1;
    uint32_t slot = (uint32_t)wysna_key_hash(dict->keys[index]) & mask;
    while (dict->buckets[slot] >= 0) slot = (slot + 1) & mask;
    dict->buckets[slot] = index;
}
static inline void wysna_dict_reindex(WDictBuf* dict, int32_t capacity) {
    free(dict->buckets);
    dict->bucket_count = capacity;
    dict->buckets = (int32_t*)wysna_alloc_safe(sizeof(int32_t) * (size_t)capacity);
    for (int32_t i = 0; i < capacity; i++) dict->buckets[i] = -1;
    for (int32_t i = 0; i < dict->len; i++) wysna_dict_index_entry(dict, i);
}

static inline void wysna_dict_detach(WVal* v) {
    if (v->tag != W_DICT || !v->as.dict) return;
    WDictBuf* old_b = v->as.dict;
    wysna_check_refcount(old_b->refcount, "detach/dict");
    if (old_b->refcount <= 1) return;
    old_b->refcount--;
    wysna_g_release_count++;
    WDictBuf* new_b = (WDictBuf*)wysna_alloc_safe(sizeof(WDictBuf));
    new_b->refcount = 1;
    new_b->len = old_b->len;
    new_b->cap = old_b->cap > 0 ? old_b->cap : 4;
    new_b->keys = (WVal*)wysna_alloc_safe(sizeof(WVal) * new_b->cap);
    new_b->vals = (WVal*)wysna_alloc_safe(sizeof(WVal) * new_b->cap);
    new_b->bucket_count = old_b->bucket_count;
    new_b->buckets = (int32_t*)wysna_alloc_safe(sizeof(int32_t) * (size_t)new_b->bucket_count);
    memcpy(new_b->buckets, old_b->buckets, sizeof(int32_t) * (size_t)new_b->bucket_count);
    wysna_mem_on_alloc();

    /* B02: CoW detach telemetry */
    wysna_g_cow_detach_dict++;
    wysna_g_cow_copied_slots += ((int64_t)old_b->len * 2);
    wysna_g_cow_copied_bytes += (int64_t)(sizeof(WVal) * old_b->len * 2 + sizeof(int32_t) * old_b->bucket_count);
    wysna_g_cow_allocated_bytes += (int64_t)(sizeof(WDictBuf) + (sizeof(WVal) * new_b->cap * 2) + sizeof(int32_t) * new_b->bucket_count);
    wysna_g_cow_alloc_count += 4;

    for (int32_t i = 0; i < old_b->len; i++) {
        new_b->keys[i] = wysna_clone(old_b->keys[i]);
        new_b->vals[i] = wysna_clone(old_b->vals[i]);
    }
    v->as.dict = new_b;
}

static inline void wysna_struct_detach(WVal* v) {
    if (v->tag != W_STRUCT || !v->as.st) return;
    WStructBuf* old_b = v->as.st;
    wysna_check_refcount(old_b->refcount, "detach/struct");
    if (old_b->refcount <= 1) return;
    old_b->refcount--;
    wysna_g_release_count++;
    WStructBuf* new_b = (WStructBuf*)wysna_alloc_safe(sizeof(WStructBuf));
    new_b->refcount = 1;
    new_b->control = NULL;
    new_b->vtable = NULL;
    new_b->name = old_b->name;
    new_b->len = old_b->len;
    int32_t cap = old_b->len > 0 ? old_b->len : 1;
    new_b->keys = (const char**)wysna_alloc_safe(sizeof(const char*) * cap);
    new_b->vals = (WVal*)wysna_alloc_safe(sizeof(WVal) * cap);
    wysna_mem_on_alloc();

    /* B02: CoW detach telemetry */
    wysna_g_cow_detach_struct++;
    wysna_g_cow_copied_slots += old_b->len;
    wysna_g_cow_copied_bytes += (int64_t)((sizeof(const char*) + sizeof(WVal)) * old_b->len);
    wysna_g_cow_allocated_bytes += (int64_t)(sizeof(WStructBuf) + (sizeof(const char*) * cap) + (sizeof(WVal) * cap));
    wysna_g_cow_alloc_count += 3;

    for (int32_t i = 0; i < old_b->len; i++) {
        new_b->keys[i] = old_b->keys[i];
        new_b->vals[i] = wysna_clone(old_b->vals[i]);
    }
    v->as.st = new_b;
}

#endif /* WYSNA_RUNTIME_MEMORY_H */
