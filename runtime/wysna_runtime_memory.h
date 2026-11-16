/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_MEMORY_H
#define WYSNA_RUNTIME_MEMORY_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
#endif

/* Deterministic RC + Copy-on-Write */
static inline void wysna_cycle_unlink(WWeakControl* ctrl) {
    if (!ctrl || !ctrl->buffered) return;
    if (ctrl->cycle_prev) {
        ctrl->cycle_prev->cycle_next = ctrl->cycle_next;
    } else if (wysna_runtime_context.purple_head == ctrl) {
        wysna_runtime_context.purple_head = ctrl->cycle_next;
    }
    if (ctrl->cycle_next) {
        ctrl->cycle_next->cycle_prev = ctrl->cycle_prev;
    }
    ctrl->cycle_next = NULL;
    ctrl->cycle_prev = NULL;
    ctrl->buffered = false;
}

static inline void wysna_cycle_buffer_purple(WWeakControl* ctrl) {
    if (!ctrl || !ctrl->object || ctrl->strong <= 0) return;
    ctrl->color = W_COLOR_PURPLE;
    if (!ctrl->buffered) {
        ctrl->cycle_prev = NULL;
        ctrl->cycle_next = wysna_runtime_context.purple_head;
        if (wysna_runtime_context.purple_head) {
            wysna_runtime_context.purple_head->cycle_prev = ctrl;
        }
        wysna_runtime_context.purple_head = ctrl;
        ctrl->buffered = true;
    }
}

static inline WVal wysna_clone(WVal v) {
    if (v.tag <= W_BOOL) return v;
    if (v.tag == W_STR) {
        if (!v.as.string.owner || v.as.string.owner->refcount == -1) return v;
        wysna_check_retain_count(v.as.string.owner->refcount, "retain/string");
        wysna_atomic_inc_i32(&v.as.string.owner->refcount);
        wysna_atomic_add_i64(&wysna_g_retain_count, 1);
        return v;
    }
    if (v.tag == W_POINTER && v.as.handle.owner) {
        wysna_check_retain_count(v.as.handle.owner->refcount, "retain/resource");
        wysna_atomic_inc_i32(&v.as.handle.owner->refcount);
        wysna_atomic_add_i64(&wysna_g_retain_count, 1);
    } else if (v.tag == W_FUNCTION) {
        wysna_check_retain_count(v.as.function->refcount, "retain/function");
        wysna_atomic_inc_i32(&v.as.function->refcount);
        wysna_atomic_add_i64(&wysna_g_retain_count, 1);
    } else if (v.tag == W_LIST && v.as.list) {
        wysna_check_retain_count(v.as.list->refcount, "retain/list");
        wysna_atomic_inc_i32(&v.as.list->refcount);
        wysna_atomic_add_i64(&wysna_g_retain_count, 1);
    } else if (v.tag == W_DICT && v.as.dict) {
        wysna_check_retain_count(v.as.dict->refcount, "retain/dict");
        wysna_atomic_inc_i32(&v.as.dict->refcount);
        wysna_atomic_add_i64(&wysna_g_retain_count, 1);
    } else if (v.tag == W_WEAK && v.as.weak) {
        wysna_check_retain_count(v.as.weak->weak, "retain/weak");
        wysna_atomic_inc_i32(&v.as.weak->weak);
        wysna_atomic_add_i64(&wysna_g_retain_count, 1);
    } else if (v.tag == W_OBJECT && v.as.st) {
        wysna_check_retain_count(v.as.st->control->strong, "retain/object");
        wysna_atomic_inc_i32(&v.as.st->control->strong);
        v.as.st->control->color = W_COLOR_BLACK;
        wysna_atomic_add_i64(&wysna_g_retain_count, 1);
    } else if (v.tag == W_STRUCT && v.as.st) {
        wysna_check_retain_count(v.as.st->refcount, "retain/struct_or_object");
        wysna_atomic_inc_i32(&v.as.st->refcount);
        wysna_atomic_add_i64(&wysna_g_retain_count, 1);
    } else if (v.tag == W_TUPLE && v.as.tuple) {
        wysna_check_retain_count(v.as.tuple->refcount, "retain/tuple");
        wysna_atomic_inc_i32(&v.as.tuple->refcount);
        wysna_atomic_add_i64(&wysna_g_retain_count, 1);
    } else if ((v.tag == W_OK || v.tag == W_ERR) && v.as.box) {
        wysna_check_retain_count(v.as.box->refcount, "retain/box");
        wysna_atomic_inc_i32(&v.as.box->refcount);
        wysna_atomic_add_i64(&wysna_g_retain_count, 1);
    } else if (v.tag == W_ENUM && v.as.en.box) {
        wysna_check_retain_count(v.as.en.box->refcount, "retain/enum_box");
        wysna_atomic_inc_i32(&v.as.en.box->refcount);
        wysna_atomic_add_i64(&wysna_g_retain_count, 1);
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
    switch (v.tag) {
        case W_UNIT:
        case W_NONE:
        case W_INT:
        case W_FLOAT:
        case W_BOOL:
            return;
        case W_STR:
            wysna_release_string(v.as.string.owner);
            return;
        case W_LIST:
            if (!v.as.list) return;
            if (v.as.list->refcount > 1) {
                wysna_check_refcount(v.as.list->refcount, "release/list");
                wysna_g_release_count++;
                v.as.list->refcount--;
                return;
            }
            if (v.as.list->scalar_only) {
                WListBuf* b = v.as.list;
                wysna_check_refcount(b->refcount, "release/list");
                wysna_g_release_count++;
                b->refcount = -1;
                free(b->data);
                free(b);
                wysna_mem_on_free();
                return;
            }
            break;
        case W_TUPLE:
            if (!v.as.tuple) return;
            if (v.as.tuple->refcount > 1) {
                wysna_check_refcount(v.as.tuple->refcount, "release/tuple");
                wysna_g_release_count++;
                v.as.tuple->refcount--;
                return;
            }
            break;
        case W_STRUCT:
            if (!v.as.st) return;
            if (v.as.st->refcount > 1) {
                wysna_check_refcount(v.as.st->refcount, "release/struct_or_object");
                wysna_g_release_count++;
                v.as.st->refcount--;
                return;
            }
            break;
        case W_DICT:
            if (!v.as.dict) return;
            if (v.as.dict->refcount > 1) {
                wysna_check_refcount(v.as.dict->refcount, "release/dict");
                wysna_g_release_count++;
                v.as.dict->refcount--;
                return;
            }
            break;
        case W_OBJECT:
            if (!v.as.st) return;
            if (v.as.st->control && v.as.st->control->strong > 1) {
                wysna_check_refcount(v.as.st->control->strong, "release/object");
                wysna_atomic_add_i64(&wysna_g_release_count, 1);
                wysna_atomic_dec_i32(&v.as.st->control->strong);
                wysna_cycle_buffer_purple(v.as.st->control);
                return;
            }
            break;
        case W_POINTER:
            if (v.as.handle.owner) {
                WPointerOwner* owner = v.as.handle.owner;
                wysna_check_refcount(owner->refcount, "release/resource");
                wysna_g_release_count++;
                if (--owner->refcount == 0) {
                    owner->close(owner->address);
                    free(owner);
                    wysna_mem_on_free();
                }
            }
            return;
        case W_FUNCTION:
            if (!v.as.function) return;
            if (v.as.function->refcount > 1 || v.as.function->environment.tag == W_UNIT) {
                wysna_check_refcount(v.as.function->refcount, "release/function");
                wysna_g_release_count++;
                if (--v.as.function->refcount == 0) {
                    free(v.as.function);
                    wysna_mem_on_free();
                }
                return;
            }
            break;
        case W_WEAK:
            if (v.as.weak) {
                WWeakControl* control = v.as.weak;
                wysna_check_refcount(control->weak, "release/weak");
                wysna_g_release_count++;
                if (--control->weak == 0) {
                    free(control);
                    wysna_mem_on_free();
                }
            }
            return;
        case W_OK:
        case W_ERR:
            if (!v.as.box) return;
            if (v.as.box->refcount != 1 || wysna_is_leaf(v.as.box->val)) {
                wysna_check_refcount(v.as.box->refcount, "release/box");
                wysna_g_release_count++;
                if (--v.as.box->refcount == 0) {
                    WVal inner = v.as.box->val;
                    free(v.as.box);
                    wysna_mem_on_free();
                    if (inner.tag == W_STR) wysna_release_string(inner.as.string.owner);
                }
                return;
            }
            break;
        case W_ENUM:
            if (!v.as.en.box) return;
            if (v.as.en.box->refcount > 1) {
                wysna_check_refcount(v.as.en.box->refcount, "release/enum_box");
                wysna_g_release_count++;
                v.as.en.box->refcount--;
                return;
            }
            break;
        default:
            break;
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
    if (v.tag <= W_BOOL) return NULL;
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
            if (b->scalar_only) {
                free(b->data);
                free(b);
                wysna_mem_on_free();
                *produced = 0;
                return NULL;
            }
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
        wysna_atomic_add_i64(&wysna_g_release_count, 1);
        if (wysna_atomic_dec_i32(&control->strong) == 0) {
            wysna_cycle_unlink(control);
            control->object = NULL;
            int32_t len = object->len;
            WVal* out = len > 0 ? (WVal*)malloc(sizeof(WVal) * (size_t)len) : NULL;
            if (len > 0 && !out) { fprintf(stderr, "[W4003] RuntimeError: out of memory\n"); exit(1); }
            for (int32_t i = 0; i < len; i++) out[i] = object->vals[i];
            free(object);
            wysna_mem_on_free();
            if (wysna_atomic_dec_i32(&control->weak) == 0) {
                free(control);
                wysna_mem_on_free();
            }
            *produced = len;
            return out;
        }
        wysna_cycle_buffer_purple(control);
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
    new_b->scalar_only = old_b->scalar_only;
    new_b->data = (WVal*)wysna_alloc_safe(sizeof(WVal) * new_b->cap);
    wysna_mem_on_alloc();

    /* B02: CoW detach telemetry */
    wysna_g_cow_detach_list++;
    wysna_g_cow_copied_slots += old_b->len;
    wysna_g_cow_copied_bytes += (int64_t)(sizeof(WVal) * old_b->len);
    wysna_g_cow_allocated_bytes += (int64_t)(sizeof(WListBuf) + (sizeof(WVal) * new_b->cap));
    wysna_g_cow_alloc_count += 2;

    if (old_b->scalar_only) {
        if (old_b->len > 0) {
            memcpy(new_b->data, old_b->data, sizeof(WVal) * (size_t)old_b->len);
        }
    } else {
        for (int32_t i = 0; i < old_b->len; i++) {
            new_b->data[i] = wysna_clone(old_b->data[i]);
        }
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
    if (!dict || dict->len == 0 || dict->bucket_count == 0) return -1;
    uint32_t mask = (uint32_t)dict->bucket_count - 1;
    uint32_t slot = (uint32_t)wysna_key_hash(key) & mask;
    while (dict->buckets[slot] >= 0) {
        int32_t index = dict->buckets[slot];
        WVal candidate = dict->keys[index];
        if (candidate.tag == key.tag) {
            if (key.tag == W_INT) {
                if (candidate.as.i == key.as.i) return index;
            } else if (key.tag == W_STR) {
                if (candidate.as.string.length == key.as.string.length &&
                    (candidate.as.string.data == key.as.string.data ||
                     memcmp(candidate.as.string.data, key.as.string.data, key.as.string.length) == 0)) {
                    return index;
                }
            } else if (key.tag == W_BOOL) {
                if (candidate.as.b == key.as.b) return index;
            } else if (wysna_eq(candidate, key)) {
                return index;
            }
        }
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
    int32_t cap = old_b->len > 0 ? old_b->len : 1;
    size_t keys_size = sizeof(const char*) * (size_t)cap;
    size_t vals_size = sizeof(WVal) * (size_t)cap;
    size_t total = sizeof(WStructBuf) + keys_size + vals_size;
    WStructBuf* new_b = (WStructBuf*)wysna_alloc_safe(total);
    new_b->refcount = 1;
    new_b->control = NULL;
    new_b->vtable = NULL;
    new_b->name = old_b->name;
    new_b->len = old_b->len;
    new_b->keys = (const char**)((char*)new_b + sizeof(WStructBuf));
    new_b->vals = (WVal*)((char*)new_b + sizeof(WStructBuf) + keys_size);
    wysna_mem_on_alloc();

    /* B02: CoW detach telemetry */
    wysna_g_cow_detach_struct++;
    wysna_g_cow_copied_slots += old_b->len;
    wysna_g_cow_copied_bytes += (int64_t)((sizeof(const char*) + sizeof(WVal)) * old_b->len);
    wysna_g_cow_allocated_bytes += (int64_t)(sizeof(WStructBuf) + (sizeof(const char*) * cap) + (sizeof(WVal) * cap));
    wysna_g_cow_alloc_count += 1;

    for (int32_t i = 0; i < old_b->len; i++) {
        new_b->keys[i] = old_b->keys[i];
        new_b->vals[i] = wysna_clone(old_b->vals[i]);
    }
    v->as.st = new_b;
}

typedef struct {
    void* ptr;
    WVal val;
    int32_t refcount;
    int32_t seen;
    bool visited_p1;
    bool walked_p2;
    bool rescued;
    bool scrubbed;
} WCycleContainerEntry;

static inline void* wysna_cycle_container_key(WVal v, int32_t* out_rc) {
    switch (v.tag) {
        case W_LIST:
            if (v.as.list && !v.as.list->scalar_only && v.as.list->len > 0) {
                *out_rc = v.as.list->refcount;
                return (void*)v.as.list;
            }
            return NULL;
        case W_TUPLE:
            if (v.as.tuple && v.as.tuple->len > 0) {
                *out_rc = v.as.tuple->refcount;
                return (void*)v.as.tuple;
            }
            return NULL;
        case W_DICT:
            if (v.as.dict && v.as.dict->len > 0) {
                *out_rc = v.as.dict->refcount;
                return (void*)v.as.dict;
            }
            return NULL;
        case W_STRUCT:
            if (v.as.st && v.as.st->len > 0) {
                *out_rc = v.as.st->refcount;
                return (void*)v.as.st;
            }
            return NULL;
        case W_OK:
        case W_ERR:
            if (v.as.box) {
                *out_rc = v.as.box->refcount;
                return (void*)v.as.box;
            }
            return NULL;
        case W_ENUM:
            if (v.as.en.box) {
                *out_rc = v.as.en.box->refcount;
                return (void*)v.as.en.box;
            }
            return NULL;
        case W_FUNCTION:
            if (v.as.function && v.as.function->environment.tag != W_UNIT) {
                *out_rc = v.as.function->refcount;
                return (void*)v.as.function;
            }
            return NULL;
        default:
            return NULL;
    }
}

static inline int32_t wysna_cycle_container_slots(WVal v, WVal*** out_a, WVal*** out_b, int32_t* len_b) {
    *out_a = NULL;
    *out_b = NULL;
    *len_b = 0;
    switch (v.tag) {
        case W_LIST:
            if (v.as.list && !v.as.list->scalar_only) {
                *out_a = &v.as.list->data;
                return v.as.list->len;
            }
            return 0;
        case W_TUPLE:
            if (v.as.tuple) {
                *out_a = &v.as.tuple->items;
                return v.as.tuple->len;
            }
            return 0;
        case W_DICT:
            if (v.as.dict) {
                *out_a = &v.as.dict->keys;
                *out_b = &v.as.dict->vals;
                *len_b = v.as.dict->len;
                return v.as.dict->len;
            }
            return 0;
        case W_STRUCT:
            if (v.as.st) {
                *out_a = &v.as.st->vals;
                return v.as.st->len;
            }
            return 0;
        case W_OK:
        case W_ERR:
            if (v.as.box) {
                return -1; /* Single inline slot: &v.as.box->val */
            }
            return 0;
        case W_ENUM:
            if (v.as.en.box) {
                return -2; /* Single inline slot: &v.as.en.box->val */
            }
            return 0;
        case W_FUNCTION:
            if (v.as.function && v.as.function->environment.tag != W_UNIT) {
                return -3; /* Single inline slot: &v.as.function->environment */
            }
            return 0;
        default:
            return 0;
    }
}

static inline void wysna_cycle_container_iter_slots(WVal v, void (*cb)(WVal*, void*), void* ctx) {
    WVal** arr_a = NULL;
    WVal** arr_b = NULL;
    int32_t len_b = 0;
    int32_t n = wysna_cycle_container_slots(v, &arr_a, &arr_b, &len_b);
    if (n == -1 && v.as.box) { cb(&v.as.box->val, ctx); return; }
    if (n == -2 && v.as.en.box) { cb(&v.as.en.box->val, ctx); return; }
    if (n == -3 && v.as.function) { cb(&v.as.function->environment, ctx); return; }
    if (arr_a && *arr_a) {
        for (int32_t i = 0; i < n; i++) cb(&(*arr_a)[i], ctx);
    }
    if (arr_b && *arr_b) {
        for (int32_t i = 0; i < len_b; i++) cb(&(*arr_b)[i], ctx);
    }
}

typedef struct {
    WWeakControl** gray;
    int32_t gray_count;
    int32_t gray_cap;
    WCycleContainerEntry* containers;
    int32_t cont_count;
    int32_t cont_cap;
    WVal* cstack;
    int32_t cstack_len;
    int32_t cstack_cap;
} WCycleScanState;

static inline WCycleContainerEntry* wysna_cycle_find_container(WCycleScanState* st, void* ptr) {
    for (int32_t i = 0; i < st->cont_count; i++) {
        if (st->containers[i].ptr == ptr) return &st->containers[i];
    }
    return NULL;
}

static inline WCycleContainerEntry* wysna_cycle_reg_container(WCycleScanState* st, void* ptr, WVal v, int32_t rc) {
    WCycleContainerEntry* found = wysna_cycle_find_container(st, ptr);
    if (found) return found;
    if (st->cont_count == st->cont_cap) {
        st->cont_cap = st->cont_cap ? st->cont_cap * 2 : 32;
        st->containers = (WCycleContainerEntry*)realloc(st->containers, sizeof(WCycleContainerEntry) * (size_t)st->cont_cap);
        if (!st->containers) exit(1);
    }
    WCycleContainerEntry* entry = &st->containers[st->cont_count++];
    memset(entry, 0, sizeof(*entry));
    entry->ptr = ptr;
    entry->val = v;
    entry->refcount = rc;
    return entry;
}

static inline void wysna_cycle_push_gray(WCycleScanState* st, WWeakControl* ctrl) {
    if (!ctrl || !ctrl->object || ctrl->strong <= 0) return;
    if (ctrl->color == W_COLOR_GRAY) return;
    ctrl->color = W_COLOR_GRAY;
    ctrl->trial_rc = ctrl->strong;
    if (st->gray_count == st->gray_cap) {
        st->gray_cap = st->gray_cap ? st->gray_cap * 2 : 32;
        st->gray = (WWeakControl**)realloc(st->gray, sizeof(WWeakControl*) * (size_t)st->gray_cap);
        if (!st->gray) exit(1);
    }
    st->gray[st->gray_count++] = ctrl;
}

static inline void wysna_cycle_push_cstack(WCycleScanState* st, WVal v) {
    if (st->cstack_len == st->cstack_cap) {
        st->cstack_cap = st->cstack_cap ? st->cstack_cap * 2 : 64;
        st->cstack = (WVal*)realloc(st->cstack, sizeof(WVal) * (size_t)st->cstack_cap);
        if (!st->cstack) exit(1);
    }
    st->cstack[st->cstack_len++] = v;
}

static void wysna_cycle_p1_slot(WVal* slot, void* ctx) {
    WCycleScanState* st = (WCycleScanState*)ctx;
    WVal v = *slot;
    if (v.tag == W_OBJECT && v.as.st && v.as.st->control) {
        wysna_cycle_push_gray(st, v.as.st->control);
        return;
    }
    int32_t rc = 0;
    void* ptr = wysna_cycle_container_key(v, &rc);
    if (ptr) {
        WCycleContainerEntry* ce = wysna_cycle_reg_container(st, ptr, v, rc);
        ce->seen++;
        if (!ce->visited_p1) {
            ce->visited_p1 = true;
            wysna_cycle_push_cstack(st, v);
        }
    }
}

static void wysna_cycle_p2_slot(WVal* slot, void* ctx) {
    WCycleScanState* st = (WCycleScanState*)ctx;
    WVal v = *slot;
    if (v.tag == W_OBJECT && v.as.st && v.as.st->control) {
        WWeakControl* ch = v.as.st->control;
        if (ch->color == W_COLOR_GRAY) ch->trial_rc--;
        return;
    }
    int32_t rc = 0;
    void* ptr = wysna_cycle_container_key(v, &rc);
    if (ptr) {
        WCycleContainerEntry* ce = wysna_cycle_find_container(st, ptr);
        if (ce && !ce->walked_p2 && ce->seen >= ce->refcount) {
            ce->walked_p2 = true;
            wysna_cycle_push_cstack(st, v);
        }
    }
}

static void wysna_cycle_rescue_slot(WVal* slot, void* ctx) {
    WCycleScanState* st = (WCycleScanState*)ctx;
    WVal v = *slot;
    if (v.tag == W_OBJECT && v.as.st && v.as.st->control) {
        WWeakControl* ch = v.as.st->control;
        if (ch->color == W_COLOR_GRAY) {
            ch->color = W_COLOR_BLACK;
            wysna_cycle_push_cstack(st, v);
        }
        return;
    }
    int32_t rc = 0;
    void* ptr = wysna_cycle_container_key(v, &rc);
    if (ptr) {
        WCycleContainerEntry* ce = wysna_cycle_reg_container(st, ptr, v, rc);
        if (!ce->rescued) {
            ce->rescued = true;
            wysna_cycle_push_cstack(st, v);
        }
    }
}

static void wysna_cycle_scrub_slot(WVal* slot, void* ctx) {
    WCycleScanState* st = (WCycleScanState*)ctx;
    WVal v = *slot;
    if (v.tag == W_OBJECT && v.as.st && v.as.st->control) {
        if (v.as.st->control->color == W_COLOR_WHITE || v.as.st->control->object == NULL) {
            *slot = wysna_unit();
        }
        return;
    }
    int32_t rc = 0;
    void* ptr = wysna_cycle_container_key(v, &rc);
    if (ptr) {
        WCycleContainerEntry* ce = wysna_cycle_find_container(st, ptr);
        if (ce && !ce->scrubbed && ce->seen >= ce->refcount) {
            ce->scrubbed = true;
            wysna_cycle_push_cstack(st, v);
        }
    }
}

static inline int64_t wysna_collect_cycles(void) {
    if (wysna_runtime_context.collecting_cycles || !wysna_runtime_context.purple_head) return 0;
    wysna_runtime_context.collecting_cycles = true;

    WCycleScanState st;
    memset(&st, 0, sizeof(st));

    /* Gather candidate purple roots and prune stale/dead entries. */
    WWeakControl* cur = wysna_runtime_context.purple_head;
    while (cur) {
        WWeakControl* next = cur->cycle_next;
        if (!cur->object || cur->strong <= 0 || cur->color == W_COLOR_BLACK) {
            wysna_cycle_unlink(cur);
        } else if (cur->color == W_COLOR_PURPLE) {
            wysna_cycle_push_gray(&st, cur);
        }
        cur = next;
    }

    if (st.gray_count == 0) {
        wysna_runtime_context.collecting_cycles = false;
        return 0;
    }

    /* Pass 1: Discover all objects & containers reachable from candidates. */
    for (int32_t i = 0; i < st.gray_count; i++) {
        WStructBuf* obj = st.gray[i]->object;
        for (int32_t f = 0; f < obj->len; f++) {
            wysna_cycle_p1_slot(&obj->vals[f], &st);
        }
        while (st.cstack_len > 0) {
            WVal c = st.cstack[--st.cstack_len];
            wysna_cycle_container_iter_slots(c, wysna_cycle_p1_slot, &st);
        }
    }

    /* Pass 2: Trial-decrement references owned within the closed candidate subgraph. */
    for (int32_t i = 0; i < st.gray_count; i++) {
        WStructBuf* obj = st.gray[i]->object;
        for (int32_t f = 0; f < obj->len; f++) {
            wysna_cycle_p2_slot(&obj->vals[f], &st);
        }
        while (st.cstack_len > 0) {
            WVal c = st.cstack[--st.cstack_len];
            wysna_cycle_container_iter_slots(c, wysna_cycle_p2_slot, &st);
        }
    }

    /* Pass 3: Rescue externally reachable objects (trial_rc > 0) back to BLACK. */
    st.cstack_len = 0;
    for (int32_t i = 0; i < st.gray_count; i++) {
        WWeakControl* ctrl = st.gray[i];
        if (ctrl->trial_rc > 0 && ctrl->color == W_COLOR_GRAY) {
            ctrl->color = W_COLOR_BLACK;
            WVal root_v; root_v.tag = W_OBJECT; root_v.as.st = ctrl->object;
            wysna_cycle_push_cstack(&st, root_v);
        }
    }
    while (st.cstack_len > 0) {
        WVal item = st.cstack[--st.cstack_len];
        if (item.tag == W_OBJECT && item.as.st) {
            for (int32_t f = 0; f < item.as.st->len; f++) {
                wysna_cycle_rescue_slot(&item.as.st->vals[f], &st);
            }
        } else {
            wysna_cycle_container_iter_slots(item, wysna_cycle_rescue_slot, &st);
        }
    }

    /* Pass 4: Gather confirmed unreachable WHITE cycle nodes. */
    WWeakControl** whites = (WWeakControl**)malloc(sizeof(WWeakControl*) * (size_t)st.gray_count);
    WStructBuf** white_objs = (WStructBuf**)malloc(sizeof(WStructBuf*) * (size_t)st.gray_count);
    if (!whites || !white_objs) exit(1);
    int32_t white_count = 0;
    for (int32_t i = 0; i < st.gray_count; i++) {
        WWeakControl* ctrl = st.gray[i];
        if (ctrl->color == W_COLOR_GRAY && ctrl->object) {
            ctrl->color = W_COLOR_WHITE;
            whites[white_count] = ctrl;
            white_objs[white_count] = ctrl->object;
            white_count++;
        } else if (ctrl->color == W_COLOR_BLACK) {
            wysna_cycle_unlink(ctrl);
        }
    }

    /* Pass 5a: Sever object links & unlink from purple buffer. */
    for (int32_t i = 0; i < white_count; i++) {
        wysna_cycle_unlink(whites[i]);
        whites[i]->object = NULL;
    }

    /* Pass 5b: Scrub any references to WHITE objects inside white objects and enclosed containers. */
    st.cstack_len = 0;
    for (int32_t i = 0; i < white_count; i++) {
        WStructBuf* obj = white_objs[i];
        for (int32_t f = 0; f < obj->len; f++) {
            wysna_cycle_scrub_slot(&obj->vals[f], &st);
        }
        while (st.cstack_len > 0) {
            WVal c = st.cstack[--st.cstack_len];
            wysna_cycle_container_iter_slots(c, wysna_cycle_scrub_slot, &st);
        }
    }

    /* Pass 5c: Collect remaining external children, free WHITE objects & weak control blocks. */
    int32_t rel_cap = 32;
    int32_t rel_count = 0;
    WVal* to_release = (WVal*)malloc(sizeof(WVal) * (size_t)rel_cap);
    if (!to_release) exit(1);
    for (int32_t i = 0; i < white_count; i++) {
        WStructBuf* obj = white_objs[i];
        for (int32_t f = 0; f < obj->len; f++) {
            WVal fv = obj->vals[f];
            if (fv.tag != W_UNIT) {
                if (rel_count == rel_cap) {
                    rel_cap *= 2;
                    to_release = (WVal*)realloc(to_release, sizeof(WVal) * (size_t)rel_cap);
                    if (!to_release) exit(1);
                }
                to_release[rel_count++] = fv;
            }
        }
        free(obj);
        wysna_mem_on_free();
        WWeakControl* ctrl = whites[i];
        ctrl->color = W_COLOR_BLACK;
        ctrl->strong = 0;
        if (wysna_atomic_dec_i32(&ctrl->weak) == 0) {
            free(ctrl);
            wysna_mem_on_free();
        }
    }

    free(whites);
    free(white_objs);
    free(st.gray);
    free(st.containers);
    free(st.cstack);

    for (int32_t i = 0; i < rel_count; i++) {
        wysna_release(to_release[i]);
    }
    free(to_release);

    wysna_runtime_context.collected_cycles += (int64_t)white_count;
    wysna_runtime_context.collecting_cycles = false;
    return (int64_t)white_count;
}

#endif /* WYSNA_RUNTIME_MEMORY_H */
