/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_MEMORY_H
#define WYSNA_RUNTIME_MEMORY_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
#endif

/* Deterministic RC + Copy-on-Write */
static inline void wysna_cycle_unlink(WWeakControl* ctrl) {
    if (!ctrl) return;
    if (wysna_runtime_context.multi_threaded) wysna_spinlock_acquire(&wysna_runtime_context.runtime_lock);
    if (ctrl->buffered) {
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
    if (wysna_runtime_context.multi_threaded) wysna_spinlock_release(&wysna_runtime_context.runtime_lock);
}

static inline void wysna_cycle_buffer_purple(WWeakControl* ctrl) {
    if (!ctrl || !ctrl->object || wysna_rc_load(&ctrl->strong) <= 0) return;
    if (wysna_runtime_context.multi_threaded) wysna_spinlock_acquire(&wysna_runtime_context.runtime_lock);
    if (ctrl->object && wysna_rc_load(&ctrl->strong) > 0) {
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
    if (wysna_runtime_context.multi_threaded) wysna_spinlock_release(&wysna_runtime_context.runtime_lock);
}

WYSNA_RT_FN WVal wysna_clone_heap(WVal v) {
    if (wysna_is_immediate(v)) return v;
    uint32_t hi = wysna_hi(v);
    if (hi == WVAL_HI_STR_PTR) {
        WStringBuf* s = wysna_str_owner(v);
        if (!s) return v;
        int32_t rc = wysna_rc_load(&s->refcount);
        if (rc == -1) return v;
        wysna_check_retain_count(rc, "retain/string");
        wysna_rc_inc(&s->refcount);
        wysna_rc_note_retain();
        return v;
    }
    if (hi == WVAL_HI_LIST) {
        WListBuf* b = wysna_as_list(v);
        if (b) {
            wysna_check_retain_count(wysna_rc_load(&b->refcount), "retain/list");
            wysna_rc_inc(&b->refcount);
            wysna_rc_note_retain();
        }
    } else if (hi == WVAL_HI_DICT) {
        WDictBuf* d = wysna_as_dict(v);
        if (d) {
            wysna_check_retain_count(wysna_rc_load(&d->refcount), "retain/dict");
            wysna_rc_inc(&d->refcount);
            wysna_rc_note_retain();
        }
    } else if (hi == WVAL_HI_OBJECT) {
        WStructBuf* st = wysna_as_struct(v);
        if (st && st->control) {
            wysna_check_retain_count(wysna_rc_load(&st->control->strong), "retain/object");
            wysna_rc_inc(&st->control->strong);
            st->control->color = W_COLOR_BLACK;
            wysna_rc_note_retain();
        }
    } else if (hi == WVAL_HI_STRUCT) {
        WStructBuf* st = wysna_as_struct(v);
        if (st) {
            wysna_check_retain_count(wysna_rc_load(&st->refcount), "retain/struct_or_object");
            wysna_rc_inc(&st->refcount);
            wysna_rc_note_retain();
        }
    } else if (hi == WVAL_HI_TUPLE) {
        WTupleBuf* t = wysna_as_tuple(v);
        if (t) {
            wysna_check_retain_count(wysna_rc_load(&t->refcount), "retain/tuple");
            wysna_rc_inc(&t->refcount);
            wysna_rc_note_retain();
        }
    } else if (hi == WVAL_HI_OK || hi == WVAL_HI_ERR) {
        WBoxBuf* bx = wysna_as_box(v);
        if (bx) {
            wysna_check_retain_count(wysna_rc_load(&bx->refcount), "retain/box");
            wysna_rc_inc(&bx->refcount);
            wysna_rc_note_retain();
        }
    } else if (hi == WVAL_HI_ENUM) {
        WEnumBuf* en = wysna_as_enum(v);
        if (en) {
            int32_t rc = wysna_rc_load(&en->refcount);
            if (rc == -1) return v;
            wysna_check_retain_count(rc, "retain/enum");
            wysna_rc_inc(&en->refcount);
            wysna_rc_note_retain();
        }
    } else if (hi == WVAL_HI_EXT) {
        uint32_t kind = *(const uint32_t*)(uintptr_t)wysna_payload(v);
        if (kind == W_INT) {
            WIntBox* ib = (WIntBox*)(uintptr_t)wysna_payload(v);
            int32_t rc = wysna_rc_load(&ib->refcount);
            if (rc == -1) return v;
            if (rc == -2) {
                /* Promote stack-scoped borrow to heap ARC when retained past expression scope. */
                return wysna_int(ib->value);
            }
            wysna_check_retain_count(rc, "retain/int_box");
            wysna_rc_inc(&ib->refcount);
            wysna_rc_note_retain();
        } else if (kind == W_WEAK) {
            WWeakControl* wk = (WWeakControl*)(uintptr_t)wysna_payload(v);
            wysna_check_retain_count(wysna_rc_load(&wk->weak), "retain/weak");
            wysna_rc_inc(&wk->weak);
            wysna_rc_note_retain();
        } else if (kind == W_FUNCTION) {
            WFunctionBuf* fn = (WFunctionBuf*)(uintptr_t)wysna_payload(v);
            wysna_check_retain_count(wysna_rc_load(&fn->refcount), "retain/function");
            wysna_rc_inc(&fn->refcount);
            wysna_rc_note_retain();
        } else if (kind == W_POINTER) {
            WPointerOwner* owner = (WPointerOwner*)(uintptr_t)wysna_payload(v);
            wysna_check_retain_count(wysna_rc_load(&owner->refcount), "retain/resource");
            wysna_rc_inc(&owner->refcount);
            wysna_rc_note_retain();
        }
    }
    return v;
}

static inline WVal wysna_clone(WVal v) {
    if (W_LIKELY(wysna_is_immediate(v))) return v;
    return wysna_clone_heap(v);
}

/*
 * Releasing a deeply nested value must not recurse: a 100k-deep list, tuple, enum
 * payload chain or struct overflowed the C stack and died with SIGSEGV while
 * freeing the program's result. `wysna_release_graph` therefore drains children
 * through an explicit inline work list (spilling to heap only above stack depth 64)
 * without allocating a temporary per-container child buffer.
 */

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
    if (!buffer) return;
    int32_t rc = wysna_rc_load(&buffer->refcount);
    if (rc == -1) return;
    wysna_check_refcount(rc, "release/string");
    wysna_rc_note_release();
    if (wysna_rc_dec(&buffer->refcount) == 0) {
        free(buffer->data);
        free(buffer->codepoint_offsets);
        free(buffer);
        wysna_mem_on_free();
    }
}

static inline bool wysna_is_leaf(WVal value) {
    if (wysna_is_immediate(value)) return true;
    uint32_t hi = wysna_hi(value);
    if (hi == WVAL_HI_STR_PTR) return true;
    if (hi == WVAL_HI_EXT) {
        uint32_t kind = *(const uint32_t*)(uintptr_t)wysna_payload(value);
        return kind == W_INT || kind == W_WEAK || kind == W_POINTER;
    }
    return false;
}

static inline void wysna_dispose_box(WBoxBuf* bx) {
    bx->refcount = 0;
#if !defined(WYSNA_HAS_ASAN)
    if (W_LIKELY(wysna_runtime_context.quotas_initialized &&
                 !wysna_runtime_context.multi_threaded &&
                 wysna_runtime_context.max_heap_bytes == 0 &&
                 wysna_runtime_context.box_pool_len < 16)) {
        wysna_runtime_context.box_pool[wysna_runtime_context.box_pool_len++] = bx;
        wysna_mem_on_free();
        return;
    }
#endif
    free(bx);
    wysna_mem_on_free();
}

static inline void wysna_dispose_enum(WEnumBuf* en) {
    en->refcount = 0;
#if !defined(WYSNA_HAS_ASAN)
    if (W_LIKELY(wysna_runtime_context.quotas_initialized &&
                 !wysna_runtime_context.multi_threaded &&
                 wysna_runtime_context.max_heap_bytes == 0 &&
                 wysna_runtime_context.enum_pool_len < 16)) {
        wysna_runtime_context.enum_pool[wysna_runtime_context.enum_pool_len++] = en;
        wysna_mem_on_free();
        return;
    }
#endif
    free(en);
    wysna_mem_on_free();
}

static inline void wysna_dispose_struct(WStructBuf* st) {
    wysna_rc_store(&st->refcount, -1);
#if !defined(WYSNA_HAS_ASAN)
    if (W_LIKELY(st->vtable == (const WClassVTable*)(uintptr_t)1 &&
                 wysna_runtime_context.quotas_initialized &&
                 !wysna_runtime_context.multi_threaded &&
                 wysna_runtime_context.max_heap_bytes == 0 &&
                 wysna_runtime_context.struct_pool_len < 32)) {
        st->refcount = 0;
        wysna_runtime_context.struct_pool[wysna_runtime_context.struct_pool_len++] = st;
        wysna_mem_on_free();
        return;
    }
#endif
    free(st);
    wysna_mem_on_free();
}

WYSNA_RT_FN void wysna_release_heap(WVal v);

static inline void wysna_release(WVal v) {
    if (W_LIKELY(wysna_is_immediate(v))) return;
    wysna_release_heap(v);
}

WYSNA_RT_FN void wysna_release_heap(WVal v) {
    if (wysna_is_immediate(v)) return;
    switch (wysna_hi(v)) {
        case WVAL_HI_STR_PTR:
            wysna_release_string(wysna_str_owner(v));
            return;
        case WVAL_HI_LIST: {
            WListBuf* b = wysna_as_list(v);
            if (!b) return;
            wysna_check_refcount(wysna_rc_load(&b->refcount), "release/list");
            if (wysna_rc_dec_if_gt1(&b->refcount)) {
                wysna_rc_note_release();
                return;
            }
            if (b->scalar_only) {
                wysna_rc_note_release();
                if (wysna_rc_dec(&b->refcount) == 0) {
                    b->refcount = -1;
                    free(b->data);
                    free(b);
                    wysna_mem_on_free();
                }
                return;
            }
            break;
        }
        case WVAL_HI_TUPLE: {
            WTupleBuf* t = wysna_as_tuple(v);
            if (!t) return;
            wysna_check_refcount(wysna_rc_load(&t->refcount), "release/tuple");
            if (wysna_rc_dec_if_gt1(&t->refcount)) {
                wysna_rc_note_release();
                return;
            }
            if (t->len <= 8) {
                bool all_leaf = true;
                for (int32_t i = 0; i < t->len; i++) {
                    if (!wysna_is_leaf(t->items[i])) {
                        all_leaf = false;
                        break;
                    }
                }
                if (all_leaf) {
                    wysna_rc_note_release();
                    if (wysna_rc_dec(&t->refcount) == 0) {
                        int32_t len = t->len;
                        WVal leaf_items[8];
                        for (int32_t i = 0; i < len; i++) leaf_items[i] = t->items[i];
                        t->refcount = -1;
                        free(t);
                        wysna_mem_on_free();
                        for (int32_t i = 0; i < len; i++) wysna_release(leaf_items[i]);
                    }
                    return;
                }
            }
            break;
        }
        case WVAL_HI_STRUCT: {
            WStructBuf* st = wysna_as_struct(v);
            if (!st) return;
            wysna_check_refcount(wysna_rc_load(&st->refcount), "release/struct_or_object");
            if (wysna_rc_dec_if_gt1(&st->refcount)) {
                wysna_rc_note_release();
                return;
            }
            if (st->len <= 8) {
                bool all_leaf = true;
                for (int32_t i = 0; i < st->len; i++) {
                    if (!wysna_is_leaf(st->vals[i])) {
                        all_leaf = false;
                        break;
                    }
                }
                if (all_leaf) {
                    wysna_rc_note_release();
                    if (wysna_rc_dec(&st->refcount) == 0) {
                        int32_t len = st->len;
                        WVal leaf_vals[8];
                        for (int32_t i = 0; i < len; i++) leaf_vals[i] = st->vals[i];
                        wysna_dispose_struct(st);
                        for (int32_t i = 0; i < len; i++) wysna_release(leaf_vals[i]);
                    }
                    return;
                }
            }
            break;
        }
        case WVAL_HI_DICT: {
            WDictBuf* d = wysna_as_dict(v);
            if (!d) return;
            wysna_check_refcount(wysna_rc_load(&d->refcount), "release/dict");
            if (wysna_rc_dec_if_gt1(&d->refcount)) {
                wysna_rc_note_release();
                return;
            }
            break;
        }
        case WVAL_HI_OBJECT: {
            WStructBuf* obj = wysna_as_struct(v);
            if (!obj) return;
            if (obj->control) {
                wysna_check_refcount(wysna_rc_load(&obj->control->strong), "release/object");
                if (wysna_rc_dec_if_gt1(&obj->control->strong)) {
                    wysna_rc_note_release();
                    wysna_cycle_buffer_purple(obj->control);
                    return;
                }
            }
            break;
        }
        case WVAL_HI_OK:
        case WVAL_HI_ERR: {
            WBoxBuf* bx = wysna_as_box(v);
            if (!bx) return;
            wysna_check_refcount(wysna_rc_load(&bx->refcount), "release/box");
            if (wysna_rc_dec_if_gt1(&bx->refcount)) {
                wysna_rc_note_release();
                return;
            }
            if (wysna_is_leaf(bx->val)) {
                wysna_rc_note_release();
                if (wysna_rc_dec(&bx->refcount) == 0) {
                    WVal inner = bx->val;
                    wysna_dispose_box(bx);
                    wysna_release(inner);
                }
                return;
            }
            break;
        }
        case WVAL_HI_ENUM: {
            WEnumBuf* en = wysna_as_enum(v);
            if (!en) return;
            int32_t rc = wysna_rc_load(&en->refcount);
            if (rc == -1) return;
            wysna_check_refcount(rc, "release/enum");
            if (wysna_rc_dec_if_gt1(&en->refcount)) {
                wysna_rc_note_release();
                return;
            }
            if (!en->has_payload || wysna_is_leaf(en->payload)) {
                wysna_rc_note_release();
                if (wysna_rc_dec(&en->refcount) == 0) {
                    bool has_payload = en->has_payload;
                    WVal inner = en->payload;
                    wysna_dispose_enum(en);
                    if (has_payload) wysna_release(inner);
                }
                return;
            }
            break;
        }
        case WVAL_HI_EXT: {
            uint32_t kind = *(const uint32_t*)(uintptr_t)wysna_payload(v);
            if (kind == W_INT) {
                WIntBox* ib = (WIntBox*)(uintptr_t)wysna_payload(v);
                int32_t rc = wysna_rc_load(&ib->refcount);
                if (rc <= 0) return;
                wysna_check_refcount(rc, "release/int_box");
                wysna_rc_note_release();
                if (wysna_rc_dec(&ib->refcount) == 0) {
                    free(ib);
                    wysna_mem_on_free();
                }
                return;
            }
            if (kind == W_POINTER) {
                WPointerOwner* owner = (WPointerOwner*)(uintptr_t)wysna_payload(v);
                wysna_check_refcount(wysna_rc_load(&owner->refcount), "release/resource");
                wysna_rc_note_release();
                if (wysna_rc_dec(&owner->refcount) == 0) {
                    owner->close(owner->address);
                    free(owner);
                    wysna_mem_on_free();
                }
                return;
            }
            if (kind == W_WEAK) {
                WWeakControl* control = (WWeakControl*)(uintptr_t)wysna_payload(v);
                wysna_check_refcount(wysna_rc_load(&control->weak), "release/weak");
                wysna_rc_note_release();
                if (wysna_rc_dec(&control->weak) == 0) {
                    free(control);
                    wysna_mem_on_free();
                }
                return;
            }
            if (kind == W_FUNCTION) {
                WFunctionBuf* fn = (WFunctionBuf*)(uintptr_t)wysna_payload(v);
                wysna_check_refcount(wysna_rc_load(&fn->refcount), "release/function");
                if (wysna_rc_dec_if_gt1(&fn->refcount)) {
                    wysna_rc_note_release();
                    return;
                }
                if (fn->environment.bits == WVAL_UNIT_BITS) {
                    wysna_rc_note_release();
                    if (wysna_rc_dec(&fn->refcount) == 0) {
                        free(fn);
                        wysna_mem_on_free();
                    }
                    return;
                }
                break;
            }
            return;
        }
        default:
            return;
    }
    wysna_release_graph(v);
}

static inline int64_t wysna_consume_int(WVal v) {
    int64_t out = wysna_as_int(v);
    wysna_release(v);
    return out;
}

static inline void wysna_release_work_push(
    WVal** work, int32_t* count, int32_t* capacity, bool* heap_work, WVal child
) {
    if (W_LIKELY(wysna_is_immediate(child))) return;
    if (wysna_hi(child) == WVAL_HI_STR_PTR) {
        wysna_release_string(wysna_str_owner(child));
        return;
    }
    if (wysna_is_leaf(child)) {
        wysna_release_heap(child);
        return;
    }
    if (W_UNLIKELY(*count >= *capacity)) {
        if (*capacity > INT32_MAX / 2) wysna_runtime_fail_unlocated("W4003", "Release work list is too large");
        int32_t new_capacity = *capacity * 2;
        WVal* grown = (WVal*)malloc(sizeof(WVal) * (size_t)new_capacity);
        if (!grown) wysna_runtime_fail_unlocated("W4003", "out of memory");
        memcpy(grown, *work, sizeof(WVal) * (size_t)(*count));
        if (*heap_work) free(*work);
        *work = grown;
        *capacity = new_capacity;
        *heap_work = true;
    }
    (*work)[(*count)++] = child;
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
        if (wysna_is_immediate(current)) continue;
        uint32_t hi = wysna_hi(current);
        if (hi == WVAL_HI_STR_PTR) {
            wysna_release_string(wysna_str_owner(current));
        } else if (hi == WVAL_HI_LIST) {
            WListBuf* b = wysna_as_list(current);
            if (!b) continue;
            wysna_check_refcount(wysna_rc_load(&b->refcount), "release/list");
            wysna_rc_note_release();
            if (wysna_rc_dec(&b->refcount) == 0) {
                b->refcount = -1;
                if (!b->scalar_only) {
                    for (int32_t i = 0; i < b->len; i++) {
                        wysna_release_work_push(&work, &count, &capacity, &heap_work, b->data[i]);
                    }
                }
                free(b->data);
                free(b);
                wysna_mem_on_free();
            }
        } else if (hi == WVAL_HI_DICT) {
            WDictBuf* d = wysna_as_dict(current);
            if (!d) continue;
            wysna_check_refcount(wysna_rc_load(&d->refcount), "release/dict");
            wysna_rc_note_release();
            if (wysna_rc_dec(&d->refcount) == 0) {
                d->refcount = -1;
                for (int32_t i = 0; i < d->len; i++) {
                    wysna_release_work_push(&work, &count, &capacity, &heap_work, d->keys[i]);
                    wysna_release_work_push(&work, &count, &capacity, &heap_work, d->vals[i]);
                }
                free(d->keys);
                free(d->vals);
                free(d->buckets);
                free(d);
                wysna_mem_on_free();
            }
        } else if (hi == WVAL_HI_OBJECT) {
            WStructBuf* object = wysna_as_struct(current);
            if (!object) continue;
            WWeakControl* control = object->control;
            wysna_check_refcount(wysna_rc_load(&control->strong), "release/object");
            wysna_rc_note_release();
            if (wysna_rc_dec(&control->strong) == 0) {
                wysna_cycle_unlink(control);
                control->object = NULL;
                for (int32_t i = 0; i < object->len; i++) {
                    wysna_release_work_push(&work, &count, &capacity, &heap_work, object->vals[i]);
                }
                free(object);
                wysna_mem_on_free();
                if (wysna_rc_dec(&control->weak) == 0) {
                    free(control);
                    wysna_mem_on_free();
                }
            } else {
                wysna_cycle_buffer_purple(control);
            }
        } else if (hi == WVAL_HI_STRUCT) {
            WStructBuf* st = wysna_as_struct(current);
            if (!st) continue;
            wysna_check_refcount(wysna_rc_load(&st->refcount), "release/struct_or_object");
            wysna_rc_note_release();
            if (wysna_rc_dec(&st->refcount) == 0) {
                st->refcount = -1;
                for (int32_t i = 0; i < st->len; i++) {
                    wysna_release_work_push(&work, &count, &capacity, &heap_work, st->vals[i]);
                }
                wysna_dispose_struct(st);
            }
        } else if (hi == WVAL_HI_TUPLE) {
            WTupleBuf* t = wysna_as_tuple(current);
            if (!t) continue;
            wysna_check_refcount(wysna_rc_load(&t->refcount), "release/tuple");
            wysna_rc_note_release();
            if (wysna_rc_dec(&t->refcount) == 0) {
                t->refcount = -1;
                for (int32_t i = 0; i < t->len; i++) {
                    wysna_release_work_push(&work, &count, &capacity, &heap_work, t->items[i]);
                }
                free(t);
                wysna_mem_on_free();
            }
        } else if (hi == WVAL_HI_OK || hi == WVAL_HI_ERR) {
            WBoxBuf* bx = wysna_as_box(current);
            if (!bx) continue;
            wysna_check_refcount(wysna_rc_load(&bx->refcount), "release/box");
            wysna_rc_note_release();
            if (wysna_rc_dec(&bx->refcount) == 0) {
                WVal inner = bx->val;
                wysna_dispose_box(bx);
                wysna_release_work_push(&work, &count, &capacity, &heap_work, inner);
            }
        } else if (hi == WVAL_HI_ENUM) {
            WEnumBuf* en = wysna_as_enum(current);
            if (!en) continue;
            int32_t rc = wysna_rc_load(&en->refcount);
            if (rc == -1) continue;
            wysna_check_refcount(rc, "release/enum");
            wysna_rc_note_release();
            if (wysna_rc_dec(&en->refcount) == 0) {
                bool has_payload = en->has_payload;
                WVal inner = en->payload;
                wysna_dispose_enum(en);
                if (has_payload) {
                    wysna_release_work_push(&work, &count, &capacity, &heap_work, inner);
                }
            }
        } else if (hi == WVAL_HI_EXT) {
            uint32_t kind = *(const uint32_t*)(uintptr_t)wysna_payload(current);
            if (kind == W_FUNCTION) {
                WFunctionBuf* fn = (WFunctionBuf*)(uintptr_t)wysna_payload(current);
                wysna_check_refcount(wysna_rc_load(&fn->refcount), "release/function");
                wysna_rc_note_release();
                if (wysna_rc_dec(&fn->refcount) == 0) {
                    WVal environment = fn->environment;
                    free(fn);
                    wysna_mem_on_free();
                    if (environment.bits != WVAL_UNIT_BITS) {
                        wysna_release_work_push(&work, &count, &capacity, &heap_work, environment);
                    }
                }
            } else {
                wysna_release_heap(current);
            }
        }
    }
    if (heap_work) free(work);
}

/* Consumes the environment; invocation borrows function, environment and arguments. */
WYSNA_RT_FN WVal wysna_function_new(WInvoke invoke, WVal environment) {
    WFunctionBuf* function = (WFunctionBuf*)wysna_alloc_safe(sizeof(WFunctionBuf));
    function->kind = W_FUNCTION; function->refcount = 1; function->invoke = invoke; function->environment = environment;
    wysna_mem_on_alloc();
    return wysna_make_hi_ptr(WVAL_HI_EXT, function);
}

WYSNA_RT_FN WVal wysna_call(WVal function, int32_t argc, const WVal* argv) {
    WFunctionBuf* fn = wysna_as_function(function);
    if (!fn) {
        wysna_runtime_fail_unlocated("W4003", "Indirect call requires function");
    }
    return fn->invoke(fn->environment, argc, argv);
}

WYSNA_RT_FN WVal wysna_discard_result_value(WVal value) {
    if (wysna_is_err(value)) return value;
    wysna_release(value);
    return wysna_ok(wysna_unit());
}

WYSNA_RT_FN void wysna_release_array(int32_t count, WVal* arr) {
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

WYSNA_RT_FN WVal wysna_unwrap_success(WVal value) {
    WEnumBuf* en = wysna_as_enum(value);
    if (en && strcmp(en->enum_name, "Option") == 0)
        return wysna_unwrap_optional(value);
    return wysna_unwrap_ok(value);
}

static inline WVal wysna_unwrap_err(WVal v) {
    if (wysna_is_err(v)) {
        WBoxBuf* bx = wysna_as_box(v);
        if (bx) return wysna_clone(bx->val);
    }
    return wysna_none();
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

WYSNA_RT_FN bool wysna_is_enum_variant(WVal value, const char* enum_name, const char* variant) {
    if (enum_name && enum_name[0] == 'R' && wysna_builtin_name(enum_name, "Result")) {
        if (variant && variant[0] == 'O' && strcmp(variant, "Ok") == 0) return wysna_is_ok(value);
        if (variant && variant[0] == 'E' && strcmp(variant, "Err") == 0) return wysna_is_err(value);
    }
    if (enum_name && enum_name[0] == 'O' && wysna_builtin_name(enum_name, "Option")) {
        if (variant && (variant[0] == 'N' || variant[0] == 'n') &&
            (strcmp(variant, "None") == 0 || strcmp(variant, "none") == 0)) return wysna_is_none(value);
        if (variant && variant[0] == 'S' && strcmp(variant, "Some") == 0) return !wysna_is_none(value);
    }
    WEnumBuf* en = wysna_as_enum(value);
    return en && en->enum_name && en->variant &&
        (en->variant == variant || (en->variant[0] == variant[0] && strcmp(en->variant, variant) == 0)) &&
        (en->enum_name == enum_name || strcmp(en->enum_name, enum_name) == 0);
}

WYSNA_RT_FN WVal wysna_peek_enum_payload(WVal value) {
    WEnumBuf* en = wysna_as_enum(value);
    if (en && en->has_payload) return en->payload;
    wysna_runtime_fail_unlocated("W4003", "enum payload access on invalid value");
}

WYSNA_RT_FN void wysna_list_detach(WVal* v) {
    WListBuf* old_b = wysna_as_list(*v);
    if (!old_b) return;
    int32_t rc = wysna_rc_load(&old_b->refcount);
    wysna_check_refcount(rc, "detach/list");
    if (rc <= 1) return;
    WListBuf* new_b = (WListBuf*)wysna_alloc_safe(sizeof(WListBuf));
    new_b->refcount = 1;
    new_b->len = old_b->len;
    new_b->cap = old_b->cap > 0 ? old_b->cap : 4;
    new_b->scalar_only = old_b->scalar_only;
    size_t element_size = wysna_list_element_size(old_b);
    new_b->data = (WVal*)wysna_alloc_safe(element_size * new_b->cap);
    wysna_mem_on_alloc();

    /* B02: CoW detach telemetry */
    wysna_cow_add(&wysna_g_cow_detach_list, 1);
    wysna_cow_add(&wysna_g_cow_copied_slots, old_b->len);
    wysna_cow_add(&wysna_g_cow_copied_bytes, (int64_t)(element_size * old_b->len));
    wysna_cow_add(&wysna_g_cow_allocated_bytes, (int64_t)(sizeof(WListBuf) + element_size * new_b->cap));
    wysna_cow_add(&wysna_g_cow_alloc_count, 2);

    if (old_b->scalar_only) {
        if (old_b->len > 0) {
            memcpy(new_b->data, old_b->data, element_size * (size_t)old_b->len);
        }
    } else {
        for (int32_t i = 0; i < old_b->len; i++) {
            new_b->data[i] = wysna_clone(old_b->data[i]);
        }
    }
    *v = wysna_make_hi_ptr(WVAL_HI_LIST, new_b);
    wysna_release(wysna_make_hi_ptr(WVAL_HI_LIST, old_b));
}

/* Dense insertion-order entries with an open-addressed lookup index. */
WYSNA_RT_FN bool wysna_eq_slow(WVal a, WVal b);
static inline bool wysna_eq(WVal a, WVal b) {
    if (W_LIKELY(a.bits == b.bits && !wysna_is_double(a))) return true;
    if (wysna_hi(a) == WVAL_HI_INT && wysna_hi(b) == WVAL_HI_INT) return false;
    return wysna_eq_slow(a, b);
}
static inline uint64_t wysna_key_hash_bytes(const unsigned char* data, size_t length, uint64_t hash) {
    for (size_t i = 0; i < length; i++) { hash ^= data[i]; hash *= UINT64_C(1099511628211); }
    return hash;
}
static inline uint64_t wysna_mix64(uint64_t number) {
    number ^= number >> 30; number *= UINT64_C(0xbf58476d1ce4e5b9);
    number ^= number >> 27; number *= UINT64_C(0x94d049bb133111eb);
    return number ^ (number >> 31);
}
static inline uint32_t wysna_sso_hash32(WVal value) {
    uint64_t h = wysna_mix64(value.bits);
    uint32_t h32 = (uint32_t)(h ^ (h >> 32));
    return h32 == 0 ? 1U : h32;
}
WYSNA_RT_FN uint32_t wysna_str_hash32(WVal value) {
    if (wysna_tag(value) != W_STR) return 0;
    uint32_t hi = wysna_hi(value);
    if (hi == WVAL_HI_SSO_SHORT || hi == WVAL_HI_SSO_6) {
        return wysna_sso_hash32(value);
    }
    WStringBuf* owner = wysna_str_owner(value);
    if (owner) {
        uint32_t cached = wysna_rc_load_u32(&owner->hash_cache);
        if (cached != 0) return cached;
    }
    char scratch[7];
    size_t slen = 0;
    const char* sdata = wysna_str_bytes(value, scratch, &slen);
    uint64_t seed = UINT64_C(14695981039346656037) ^ (uint64_t)W_STR;
    uint64_t computed = wysna_key_hash_bytes((const unsigned char*)sdata, slen, seed);
    uint32_t h32 = (uint32_t)(computed ^ (computed >> 32));
    if (h32 == 0) h32 = 1;
    if (owner) {
        wysna_rc_store_u32(&owner->hash_cache, h32);
    }
    return h32;
}
WYSNA_RT_FN uint64_t wysna_key_hash(WVal value) {
    uint32_t hi = wysna_hi(value);
    if (hi == WVAL_HI_INT) {
        uint64_t raw = wysna_payload(value);
        int64_t signed_val = (int64_t)((raw ^ UINT64_C(0x0000800000000000)) - UINT64_C(0x0000800000000000));
        return (UINT64_C(14695981039346656037) ^ (uint64_t)W_INT) ^ wysna_mix64((uint64_t)signed_val);
    }
    if (hi == WVAL_HI_SSO_SHORT || hi == WVAL_HI_SSO_6) {
        return (uint64_t)wysna_sso_hash32(value);
    }
    WTag tag = wysna_tag(value);
    uint64_t hash = UINT64_C(14695981039346656037) ^ (uint64_t)tag;
    if (tag == W_STR) return (uint64_t)wysna_str_hash32(value);
    if (tag == W_INT || tag == W_BOOL) {
        uint64_t number = (tag == W_INT) ? (uint64_t)wysna_as_int(value) : (uint64_t)wysna_as_bool(value);
        return hash ^ wysna_mix64(number);
    }
    if (tag == W_TUPLE) {
        WTupleBuf* t = wysna_as_tuple(value);
        if (t) {
            for (int32_t i = 0; i < t->len; i++) {
                uint64_t elem_h = wysna_key_hash(t->items[i]);
                hash = (hash ^ elem_h) * UINT64_C(1099511628211);
            }
        }
        return hash;
    }
    if (tag == W_ENUM) {
        WEnumBuf* en = wysna_as_enum(value);
        if (en) {
            hash = wysna_key_hash_bytes((const unsigned char*)en->enum_name, strlen(en->enum_name), hash);
            hash = wysna_key_hash_bytes((const unsigned char*)en->variant, strlen(en->variant), hash);
            if (en->has_payload) {
                if (wysna_tag(en->payload) == W_TUPLE) {
                    WTupleBuf* tp = wysna_as_tuple(en->payload);
                    if (tp) {
                        for (int32_t i = 0; i < tp->len; i++) {
                            hash = (hash ^ wysna_key_hash(tp->items[i])) * UINT64_C(1099511628211);
                        }
                    }
                } else {
                    hash ^= wysna_key_hash(en->payload);
                }
            }
        }
    }
    return hash;
}
static inline uint64_t wysna_key_hash_inline(WVal value) {
    uint32_t hi = wysna_hi(value);
    if (W_LIKELY(hi == WVAL_HI_INT)) {
        uint64_t raw = wysna_payload(value);
        int64_t signed_val = (int64_t)((raw ^ UINT64_C(0x0000800000000000)) - UINT64_C(0x0000800000000000));
        return (UINT64_C(14695981039346656037) ^ (uint64_t)W_INT) ^ wysna_mix64((uint64_t)signed_val);
    }
    if (W_LIKELY(hi == WVAL_HI_SSO_SHORT || hi == WVAL_HI_SSO_6)) {
        return (uint64_t)wysna_sso_hash32(value);
    }
    return wysna_key_hash(value);
}
WYSNA_RT_FN int32_t wysna_dict_probe(WDictBuf* dict, WVal key, uint64_t* out_hash, uint32_t* out_slot) {
    if (!dict || dict->bucket_count == 0) return -1;
    uint32_t mask = (uint32_t)dict->bucket_count - 1;
    uint64_t full_hash = wysna_key_hash_inline(key);
    if (out_hash) *out_hash = full_hash;
    uint32_t key_hi = wysna_hi(key);
    bool inline_integer = key_hi == WVAL_HI_INT;
    bool sso_str = key_hi == WVAL_HI_SSO_SHORT || key_hi == WVAL_HI_SSO_6;
    WTag key_tag = inline_integer ? W_INT : (sso_str ? W_STR : wysna_tag(key));
    uint32_t kh = (key_tag == W_STR) ? (uint32_t)full_hash : 0;
    uint32_t slot = (uint32_t)full_hash & mask;
    while (dict->buckets[slot] >= 0) {
        int32_t index = dict->buckets[slot];
        WVal candidate = dict->keys[index];
        if (candidate.bits == key.bits && !wysna_is_double(key)) return index;
        if (!sso_str && !(inline_integer && wysna_hi(candidate) == WVAL_HI_INT) &&
            wysna_tag(candidate) == key_tag) {
            if (key_tag == W_INT) {
                if (wysna_as_int(candidate) == wysna_as_int(key)) return index;
            } else if (key_tag == W_STR) {
                WStringBuf* co = wysna_str_owner(candidate);
                uint32_t ch = co ? wysna_rc_load_u32(&co->hash_cache) : 0;
                if (ch == 0 || kh == 0 || ch == kh) {
                    char sb_c[7], sb_k[7];
                    size_t lc = 0, lk = 0;
                    const char* dc = wysna_str_bytes(candidate, sb_c, &lc);
                    const char* dk = wysna_str_bytes(key, sb_k, &lk);
                    if (lc == lk && (dc == dk || memcmp(dc, dk, lc) == 0)) return index;
                }
            } else if (key_tag == W_BOOL) {
                if (candidate.bits == key.bits) return index;
            } else if (wysna_eq(candidate, key)) {
                return index;
            }
        }
        slot = (slot + 1) & mask;
    }
    if (out_slot) *out_slot = slot;
    return -1;
}
WYSNA_RT_FN int32_t wysna_dict_find(WDictBuf* dict, WVal key) {
    if (!dict || dict->len == 0 || dict->bucket_count == 0) return -1;
    return wysna_dict_probe(dict, key, NULL, NULL);
}
WYSNA_RT_FN void wysna_dict_index_entry_hashed(WDictBuf* dict, int32_t index, uint64_t full_hash) {
    uint32_t mask = (uint32_t)dict->bucket_count - 1;
    uint32_t slot = (uint32_t)full_hash & mask;
    while (dict->buckets[slot] >= 0) slot = (slot + 1) & mask;
    dict->buckets[slot] = index;
}
WYSNA_RT_FN void wysna_dict_index_entry(WDictBuf* dict, int32_t index) {
    wysna_dict_index_entry_hashed(dict, index, wysna_key_hash_inline(dict->keys[index]));
}
WYSNA_RT_FN void wysna_dict_reindex(WDictBuf* dict, int32_t capacity) {
    free(dict->buckets);
    dict->bucket_count = capacity;
    dict->buckets = (int32_t*)wysna_alloc_safe(sizeof(int32_t) * (size_t)capacity);
    memset(dict->buckets, -1, sizeof(int32_t) * (size_t)capacity);
    for (int32_t i = 0; i < dict->len; i++) wysna_dict_index_entry(dict, i);
}
WYSNA_RT_FN void wysna_dict_insert_owned(WDictBuf* d, WVal owned_key, WVal owned_val) {
    uint64_t full_hash = 0;
    uint32_t slot = 0;
    int32_t found = wysna_dict_probe(d, owned_key, &full_hash, &slot);
    if (found >= 0) {
        wysna_release(owned_key);
        wysna_release(d->vals[found]);
        d->vals[found] = owned_val;
        return;
    }
    if (d->cap > INT32_MAX / 2 || d->bucket_count > INT32_MAX / 2) {
        wysna_runtime_fail_unlocated("W4003", "Dictionary too large");
    }
    bool reindexed = false;
    if ((int64_t)(d->len + 1) * 2 >= d->bucket_count) {
        wysna_dict_reindex(d, d->bucket_count * 2);
        reindexed = true;
    }
    if (d->len >= d->cap) {
        d->cap = d->cap > 0 ? d->cap * 2 : 4;
        d->keys = (WVal*)wysna_realloc_safe(d->keys, sizeof(WVal) * d->cap);
        d->vals = (WVal*)wysna_realloc_safe(d->vals, sizeof(WVal) * d->cap);
    }
    int32_t idx = d->len++;
    d->keys[idx] = owned_key;
    d->vals[idx] = owned_val;
    if (reindexed) {
        wysna_dict_index_entry_hashed(d, idx, full_hash);
    } else {
        d->buckets[slot] = idx;
    }
}

WYSNA_RT_FN void wysna_dict_detach(WVal* v) {
    WDictBuf* old_b = wysna_as_dict(*v);
    if (!old_b) return;
    int32_t rc = wysna_rc_load(&old_b->refcount);
    wysna_check_refcount(rc, "detach/dict");
    if (rc <= 1) return;
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
    wysna_cow_add(&wysna_g_cow_detach_dict, 1);
    wysna_cow_add(&wysna_g_cow_copied_slots, ((int64_t)old_b->len * 2));
    wysna_cow_add(&wysna_g_cow_copied_bytes, (int64_t)(sizeof(WVal) * old_b->len * 2 + sizeof(int32_t) * old_b->bucket_count));
    wysna_cow_add(&wysna_g_cow_allocated_bytes, (int64_t)(sizeof(WDictBuf) + (sizeof(WVal) * new_b->cap * 2) + sizeof(int32_t) * new_b->bucket_count));
    wysna_cow_add(&wysna_g_cow_alloc_count, 4);

    for (int32_t i = 0; i < old_b->len; i++) {
        new_b->keys[i] = wysna_clone(old_b->keys[i]);
        new_b->vals[i] = wysna_clone(old_b->vals[i]);
    }
    *v = wysna_make_hi_ptr(WVAL_HI_DICT, new_b);
    wysna_release(wysna_make_hi_ptr(WVAL_HI_DICT, old_b));
}

WYSNA_RT_FN void wysna_struct_detach(WVal* v) {
    if (wysna_hi(*v) != WVAL_HI_STRUCT) return;
    WStructBuf* old_b = wysna_as_struct(*v);
    if (!old_b) return;
    int32_t rc = wysna_rc_load(&old_b->refcount);
    wysna_check_refcount(rc, "detach/struct");
    if (rc <= 1) return;
    int32_t cap = old_b->len > 0 ? old_b->len : 1;
    bool inline_keys = old_b->keys == (const char**)((char*)old_b + sizeof(WStructBuf));
    size_t keys_size = inline_keys ? (sizeof(const char*) * (size_t)cap) : 0;
    size_t vals_offset = inline_keys
        ? (sizeof(WStructBuf) + keys_size)
        : (((sizeof(WStructBuf) + sizeof(WVal) - 1) / sizeof(WVal)) * sizeof(WVal));
    size_t vals_size = sizeof(WVal) * (size_t)cap;
    size_t total = vals_offset + vals_size;
    WStructBuf* new_b = (WStructBuf*)wysna_alloc_safe(total);
    new_b->refcount = 1;
    new_b->control = NULL;
    new_b->vtable = NULL;
    new_b->name = old_b->name;
    new_b->len = old_b->len;
    if (inline_keys) {
        new_b->keys = (const char**)((char*)new_b + sizeof(WStructBuf));
    } else {
        new_b->keys = old_b->keys;
    }
    new_b->vals = (WVal*)((char*)new_b + vals_offset);
    wysna_mem_on_alloc();

    /* B02: CoW detach telemetry */
    wysna_cow_add(&wysna_g_cow_detach_struct, 1);
    wysna_cow_add(&wysna_g_cow_copied_slots, old_b->len);
    size_t copied_key_bytes = inline_keys ? (sizeof(const char*) * (size_t)old_b->len) : 0;
    wysna_cow_add(&wysna_g_cow_copied_bytes, (int64_t)(copied_key_bytes + (sizeof(WVal) * (size_t)old_b->len)));
    wysna_cow_add(&wysna_g_cow_allocated_bytes, (int64_t)(vals_offset + (sizeof(WVal) * (size_t)cap)));
    wysna_cow_add(&wysna_g_cow_alloc_count, 1);

    for (int32_t i = 0; i < old_b->len; i++) {
        if (inline_keys) {
            new_b->keys[i] = old_b->keys[i];
        }
        new_b->vals[i] = wysna_clone(old_b->vals[i]);
    }
    *v = wysna_make_hi_ptr(WVAL_HI_STRUCT, new_b);
    wysna_release(wysna_make_hi_ptr(WVAL_HI_STRUCT, old_b));
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
    switch (wysna_hi(v)) {
        case WVAL_HI_LIST: {
            WListBuf* b = wysna_as_list(v);
            if (b && !b->scalar_only && b->len > 0) {
                *out_rc = b->refcount;
                return (void*)b;
            }
            return NULL;
        }
        case WVAL_HI_TUPLE: {
            WTupleBuf* t = wysna_as_tuple(v);
            if (t && t->len > 0) {
                *out_rc = t->refcount;
                return (void*)t;
            }
            return NULL;
        }
        case WVAL_HI_DICT: {
            WDictBuf* d = wysna_as_dict(v);
            if (d && d->len > 0) {
                *out_rc = d->refcount;
                return (void*)d;
            }
            return NULL;
        }
        case WVAL_HI_STRUCT: {
            WStructBuf* st = wysna_as_struct(v);
            if (st && st->len > 0) {
                *out_rc = st->refcount;
                return (void*)st;
            }
            return NULL;
        }
        case WVAL_HI_OK:
        case WVAL_HI_ERR: {
            WBoxBuf* bx = wysna_as_box(v);
            if (bx) {
                *out_rc = bx->refcount;
                return (void*)bx;
            }
            return NULL;
        }
        case WVAL_HI_ENUM: {
            WEnumBuf* en = wysna_as_enum(v);
            if (en && en->refcount > 0 && en->has_payload) {
                *out_rc = en->refcount;
                return (void*)en;
            }
            return NULL;
        }
        case WVAL_HI_EXT: {
            WFunctionBuf* fn = wysna_as_function(v);
            if (fn && fn->environment.bits != WVAL_UNIT_BITS) {
                *out_rc = fn->refcount;
                return (void*)fn;
            }
            return NULL;
        }
        default:
            return NULL;
    }
}

static inline int32_t wysna_cycle_container_slots(WVal v, WVal*** out_a, WVal*** out_b, int32_t* len_b) {
    *out_a = NULL;
    *out_b = NULL;
    *len_b = 0;
    switch (wysna_hi(v)) {
        case WVAL_HI_LIST: {
            WListBuf* b = wysna_as_list(v);
            if (b && !b->scalar_only) {
                *out_a = &b->data;
                return b->len;
            }
            return 0;
        }
        case WVAL_HI_TUPLE: {
            WTupleBuf* t = wysna_as_tuple(v);
            if (t) {
                *out_a = &t->items;
                return t->len;
            }
            return 0;
        }
        case WVAL_HI_DICT: {
            WDictBuf* d = wysna_as_dict(v);
            if (d) {
                *out_a = &d->keys;
                *out_b = &d->vals;
                *len_b = d->len;
                return d->len;
            }
            return 0;
        }
        case WVAL_HI_STRUCT: {
            WStructBuf* st = wysna_as_struct(v);
            if (st) {
                *out_a = &st->vals;
                return st->len;
            }
            return 0;
        }
        case WVAL_HI_OK:
        case WVAL_HI_ERR:
            return wysna_as_box(v) ? -1 : 0;
        case WVAL_HI_ENUM: {
            WEnumBuf* en = wysna_as_enum(v);
            return (en && en->has_payload) ? -2 : 0;
        }
        case WVAL_HI_EXT: {
            WFunctionBuf* fn = wysna_as_function(v);
            return (fn && fn->environment.bits != WVAL_UNIT_BITS) ? -3 : 0;
        }
        default:
            return 0;
    }
}

static inline void wysna_cycle_container_iter_slots(WVal v, void (*cb)(WVal*, void*), void* ctx) {
    WVal** arr_a = NULL;
    WVal** arr_b = NULL;
    int32_t len_b = 0;
    int32_t n = wysna_cycle_container_slots(v, &arr_a, &arr_b, &len_b);
    if (n == -1) { WBoxBuf* bx = wysna_as_box(v); if (bx) cb(&bx->val, ctx); return; }
    if (n == -2) { WEnumBuf* en = wysna_as_enum(v); if (en) cb(&en->payload, ctx); return; }
    if (n == -3) { WFunctionBuf* fn = wysna_as_function(v); if (fn) cb(&fn->environment, ctx); return; }
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
        if (!st->containers) wysna_panic_exit(1);
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
        if (!st->gray) wysna_panic_exit(1);
    }
    st->gray[st->gray_count++] = ctrl;
}

static inline void wysna_cycle_push_cstack(WCycleScanState* st, WVal v) {
    if (st->cstack_len == st->cstack_cap) {
        st->cstack_cap = st->cstack_cap ? st->cstack_cap * 2 : 64;
        st->cstack = (WVal*)realloc(st->cstack, sizeof(WVal) * (size_t)st->cstack_cap);
        if (!st->cstack) wysna_panic_exit(1);
    }
    st->cstack[st->cstack_len++] = v;
}

static void wysna_cycle_p1_slot(WVal* slot, void* ctx) {
    WCycleScanState* st = (WCycleScanState*)ctx;
    WVal v = *slot;
    if (wysna_hi(v) == WVAL_HI_OBJECT) {
        WStructBuf* obj = wysna_as_struct(v);
        if (obj && obj->control) {
            wysna_cycle_push_gray(st, obj->control);
            return;
        }
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
    if (wysna_hi(v) == WVAL_HI_OBJECT) {
        WStructBuf* obj = wysna_as_struct(v);
        if (obj && obj->control) {
            WWeakControl* ch = obj->control;
            if (ch->color == W_COLOR_GRAY) ch->trial_rc--;
            return;
        }
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
    if (wysna_hi(v) == WVAL_HI_OBJECT) {
        WStructBuf* obj = wysna_as_struct(v);
        if (obj && obj->control) {
            WWeakControl* ch = obj->control;
            if (ch->color == W_COLOR_GRAY) {
                ch->color = W_COLOR_BLACK;
                wysna_cycle_push_cstack(st, v);
            }
            return;
        }
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
    if (wysna_hi(v) == WVAL_HI_OBJECT) {
        WStructBuf* obj = wysna_as_struct(v);
        if (obj && obj->control) {
            if (obj->control->color == W_COLOR_WHITE || obj->control->object == NULL) {
                *slot = wysna_unit();
            }
            return;
        }
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

WYSNA_RT_FN int64_t wysna_collect_cycles(void) {
    /* Identity-bearing objects and their cycle graph are confined to the owner. */
    if (wysna_worker_depth > 0) return 0;
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
            WVal root_v = wysna_make_hi_ptr(WVAL_HI_OBJECT, ctrl->object);
            wysna_cycle_push_cstack(&st, root_v);
        }
    }
    while (st.cstack_len > 0) {
        WVal item = st.cstack[--st.cstack_len];
        if (wysna_hi(item) == WVAL_HI_OBJECT) {
            WStructBuf* obj = wysna_as_struct(item);
            if (obj) {
                for (int32_t f = 0; f < obj->len; f++) {
                    wysna_cycle_rescue_slot(&obj->vals[f], &st);
                }
            }
        } else {
            wysna_cycle_container_iter_slots(item, wysna_cycle_rescue_slot, &st);
        }
    }

    /* Pass 4: Gather confirmed unreachable WHITE cycle nodes. */
    WWeakControl** whites = (WWeakControl**)malloc(sizeof(WWeakControl*) * (size_t)st.gray_count);
    WStructBuf** white_objs = (WStructBuf**)malloc(sizeof(WStructBuf*) * (size_t)st.gray_count);
    if (!whites || !white_objs) wysna_panic_exit(1);
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
    if (!to_release) wysna_panic_exit(1);
    for (int32_t i = 0; i < white_count; i++) {
        WStructBuf* obj = white_objs[i];
        for (int32_t f = 0; f < obj->len; f++) {
            WVal fv = obj->vals[f];
            if (fv.bits != WVAL_UNIT_BITS) {
                if (rel_count == rel_cap) {
                    rel_cap *= 2;
                    to_release = (WVal*)realloc(to_release, sizeof(WVal) * (size_t)rel_cap);
                    if (!to_release) wysna_panic_exit(1);
                }
                to_release[rel_count++] = fv;
            }
        }
        free(obj);
        wysna_mem_on_free();
        WWeakControl* ctrl = whites[i];
        ctrl->color = W_COLOR_BLACK;
        ctrl->strong = 0;
        if (wysna_rc_dec(&ctrl->weak) == 0) {
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
