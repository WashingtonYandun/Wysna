/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_COLLECTIONS_H
#define WYSNA_RUNTIME_COLLECTIONS_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
#endif

/* Containers: List, Dict, Tuple, Struct */
WYSNA_RT_FN WVal wysna_make_list(int32_t count, const WVal* elems) {
    WListBuf* b = (WListBuf*)wysna_alloc_safe(sizeof(WListBuf));
    b->refcount = 1;
    b->len = count;
    b->cap = count > 4 ? count : 4;
    b->scalar_only = true;
    b->data = (WVal*)wysna_alloc_safe(sizeof(WVal) * b->cap);
    wysna_mem_on_alloc();
    for (int32_t i = 0; i < count; i++) {
        WTag t = wysna_tag(elems[i]);
        if (t != W_INT && t != W_FLOAT && t != W_BOOL) {
            b->scalar_only = false;
        } else if (t == W_INT && wysna_hi(elems[i]) == WVAL_HI_EXT) {
            b->scalar_only = false;
        }
        b->data[i] = wysna_clone(elems[i]);
    }
    return wysna_make_hi_ptr(WVAL_HI_LIST, b);
}

static inline WListBuf* wysna_new_byte_buffer(int32_t count) {
    WListBuf* buffer = (WListBuf*)wysna_alloc_safe(sizeof(WListBuf));
    buffer->refcount = 1;
    buffer->len = count;
    buffer->cap = count > 4 ? count : 4;
    buffer->scalar_only = WYSNA_LIST_PACKED_U8;
    buffer->data = (WVal*)wysna_alloc_safe((size_t)buffer->cap);
    wysna_mem_on_alloc();
    return buffer;
}

WYSNA_RT_FN WVal wysna_make_u8_list(int32_t count, const WVal* elements) {
    WListBuf* buffer = wysna_new_byte_buffer(count);
    for (int32_t index = 0; index < count; index++) {
        ((uint8_t*)buffer->data)[index] = wysna_as_u8(elements[index]);
    }
    return wysna_make_hi_ptr(WVAL_HI_LIST, buffer);
}

WYSNA_RT_FN WVal wysna_pack_u8_owned(WVal value) {
    WListBuf* list = wysna_as_list(value);
    if (!list) wysna_runtime_fail("W4003", "Expected list[u8]");
    if (wysna_list_is_u8(list)) return value;
    WVal packed = wysna_make_u8_list(list->len, list->data);
    wysna_release(value);
    return packed;
}

WYSNA_RT_FN WVal wysna_list_slice(const WListBuf* input, int32_t start, int32_t count) {
    if (!wysna_list_is_u8(input)) return wysna_make_list(count, input->data + start);
    WListBuf* output = wysna_new_byte_buffer(count);
    if (count > 0) memcpy(output->data, (const uint8_t*)input->data + start, (size_t)count);
    return wysna_make_hi_ptr(WVAL_HI_LIST, output);
}

WYSNA_RT_FN int32_t wysna_range_count_i64(int64_t s, int64_t e, bool inclusive) {
    if (inclusive) {
        if (e == INT64_MAX) {
            wysna_runtime_fail_unlocated("W4003", "Range bound overflow at INT64_MAX");
        }
        e++;
    }
    uint64_t diff = e > s ? (uint64_t)e - (uint64_t)s : 0;
    if (diff > 100000000ULL) {
        wysna_runtime_fail_unlocated("W4003", "Range length %" PRIu64 " exceeds maximum list capacity", diff);
    }
    return (int32_t)diff;
}

WYSNA_RT_FN int32_t wysna_range_count(WVal start, WVal end, bool inclusive) {
    return wysna_range_count_i64(wysna_as_int(start), wysna_as_int(end), inclusive);
}

WYSNA_RT_FN WVal wysna_make_range(WVal start, WVal end, bool inclusive) {
    int64_t s = wysna_as_int(start);
    int32_t count = wysna_range_count(start, end, inclusive);
    WListBuf* b = (WListBuf*)wysna_alloc_safe(sizeof(WListBuf));
    b->refcount = 1;
    b->len = count;
    b->cap = count > 4 ? count : 4;
    b->scalar_only = true;
    b->data = (WVal*)wysna_alloc_safe(sizeof(WVal) * b->cap);
    wysna_mem_on_alloc();
    for (int32_t i = 0; i < count; i++) {
        int64_t elem = s + (int64_t)i;
        if (!wysna_int_fits_48(elem)) b->scalar_only = false;
        b->data[i] = wysna_int(elem);
    }
    return wysna_make_hi_ptr(WVAL_HI_LIST, b);
}

WYSNA_RT_FN WVal wysna_make_tuple(int32_t count, const WVal* elems) {
    size_t items_size = sizeof(WVal) * (size_t)(count > 0 ? count : 1);
    WTupleBuf* t = (WTupleBuf*)wysna_alloc_safe(sizeof(WTupleBuf) + items_size);
    t->refcount = 1;
    t->len = count;
    t->items = (WVal*)((char*)t + sizeof(WTupleBuf));
    wysna_mem_on_alloc();
    for (int32_t i = 0; i < count; i++) {
        t->items[i] = wysna_clone(elems[i]);
    }
    return wysna_make_hi_ptr(WVAL_HI_TUPLE, t);
}

WYSNA_RT_FN WVal wysna_make_dict(int32_t count, const WVal* keys, const WVal* vals) {
    if (count < 0 || count > INT32_MAX / 4) { wysna_runtime_fail_unlocated("W4003", "Dictionary too large"); }
    WDictBuf* d = (WDictBuf*)wysna_alloc_safe(sizeof(WDictBuf));
    d->refcount = 1;
    d->len = 0;
    d->cap = count > 4 ? count * 2 : 4;
    d->keys = (WVal*)wysna_alloc_safe(sizeof(WVal) * d->cap);
    d->vals = (WVal*)wysna_alloc_safe(sizeof(WVal) * d->cap);
    d->buckets = NULL;
    int32_t buckets = 8;
    while (buckets < d->cap) {
        if (buckets > INT32_MAX / 2) { wysna_runtime_fail_unlocated("W4003", "Dictionary too large"); }
        buckets *= 2;
    }
    wysna_dict_reindex(d, buckets);
    wysna_mem_on_alloc();
    WVal v = wysna_make_hi_ptr(WVAL_HI_DICT, d);
    for (int32_t i = 0; i < count; i++) {
        uint64_t full_hash = 0;
        uint32_t slot = 0;
        int32_t index = wysna_dict_probe(d, keys[i], &full_hash, &slot);
        if (index >= 0) { wysna_release(d->vals[index]); d->vals[index] = wysna_clone(vals[i]); }
        else {
            bool reindexed = false;
            if ((int64_t)(d->len + 1) * 2 >= d->bucket_count) {
                if (d->bucket_count > INT32_MAX / 2) { wysna_runtime_fail_unlocated("W4003", "Dictionary too large"); }
                wysna_dict_reindex(d, d->bucket_count * 2);
                reindexed = true;
            }
            index = d->len++;
            d->keys[index] = wysna_clone(keys[i]);
            d->vals[index] = wysna_clone(vals[i]);
            if (reindexed) {
                wysna_dict_index_entry_hashed(d, index, full_hash);
            } else {
                d->buckets[slot] = index;
            }
        }
    }
    return v;
}

WYSNA_RT_FN WVal wysna_make_struct(const char* name, int32_t count, const char** keys, const WVal* vals) {
    int32_t cap = count > 0 ? count : 1;
    size_t keys_size = sizeof(const char*) * (size_t)cap;
    size_t vals_size = sizeof(WVal) * (size_t)cap;
    size_t total = sizeof(WStructBuf) + keys_size + vals_size;
    WStructBuf* st = (WStructBuf*)wysna_alloc_safe(total);
    st->refcount = 1;
    st->control = NULL;
    st->vtable = NULL;
    st->name = name;
    st->len = count;
    st->keys = (const char**)((char*)st + sizeof(WStructBuf));
    st->vals = (WVal*)((char*)st + sizeof(WStructBuf) + keys_size);
    wysna_mem_on_alloc();
    for (int32_t i = 0; i < count; i++) {
        st->keys[i] = keys[i];
        st->vals[i] = wysna_clone(vals[i]);
    }
    return wysna_make_hi_ptr(WVAL_HI_STRUCT, st);
}

WYSNA_RT_FN WVal wysna_make_object(const char* name, int32_t count, const char** keys, const WVal* vals) {
    int32_t cap = count > 0 ? count : 1;
    size_t keys_size = sizeof(const char*) * (size_t)cap;
    size_t vals_size = sizeof(WVal) * (size_t)cap;
    size_t total = sizeof(WStructBuf) + keys_size + vals_size;
    WStructBuf* st = (WStructBuf*)wysna_alloc_safe(total);
    st->refcount = 1;
    st->control = (WWeakControl*)wysna_alloc_safe(sizeof(WWeakControl));
    memset(st->control, 0, sizeof(WWeakControl));
    st->control->kind = W_WEAK;
    st->control->strong = 1;
    st->control->weak = 1;
    st->control->color = W_COLOR_BLACK;
    st->control->object = st;
    wysna_mem_on_alloc();
    st->vtable = NULL;
    st->name = name;
    st->len = count;
    st->keys = (const char**)((char*)st + sizeof(WStructBuf));
    st->vals = (WVal*)((char*)st + sizeof(WStructBuf) + keys_size);
    wysna_mem_on_alloc();
    for (int32_t i = 0; i < count; i++) {
        st->keys[i] = keys[i];
        st->vals[i] = wysna_clone(vals[i]);
    }
    return wysna_make_hi_ptr(WVAL_HI_OBJECT, st);
}

static inline size_t wysna_struct_vals_offset(void) {
    return ((sizeof(WStructBuf) + sizeof(WVal) - 1) / sizeof(WVal)) * sizeof(WVal);
}

WYSNA_RT_FN WVal wysna_make_struct_static(const char* name, int32_t count, const char* const* keys, const WVal* vals) {
    int32_t cap = count > 0 ? count : 1;
    size_t vals_offset = wysna_struct_vals_offset();
    size_t vals_size = sizeof(WVal) * (size_t)cap;
    size_t total = vals_offset + vals_size;
    WStructBuf* st = (WStructBuf*)wysna_alloc_safe(total);
    st->refcount = 1;
    st->control = NULL;
    st->vtable = NULL;
    st->name = name;
    st->len = count;
    st->keys = (const char**)keys;
    st->vals = (WVal*)((char*)st + vals_offset);
    wysna_mem_on_alloc();
    for (int32_t i = 0; i < count; i++) {
        st->vals[i] = wysna_clone(vals[i]);
    }
    return wysna_make_hi_ptr(WVAL_HI_STRUCT, st);
}

WYSNA_RT_FN WVal wysna_make_object_static(const char* name, int32_t count, const char* const* keys, const WVal* vals) {
    int32_t cap = count > 0 ? count : 1;
    size_t vals_offset = wysna_struct_vals_offset();
    size_t vals_size = sizeof(WVal) * (size_t)cap;
    size_t total = vals_offset + vals_size;
    WStructBuf* st = (WStructBuf*)wysna_alloc_safe(total);
    st->refcount = 1;
    st->control = (WWeakControl*)wysna_alloc_safe(sizeof(WWeakControl));
    memset(st->control, 0, sizeof(WWeakControl));
    st->control->kind = W_WEAK;
    st->control->strong = 1;
    st->control->weak = 1;
    st->control->color = W_COLOR_BLACK;
    st->control->object = st;
    wysna_mem_on_alloc();
    st->vtable = NULL;
    st->name = name;
    st->len = count;
    st->keys = (const char**)keys;
    st->vals = (WVal*)((char*)st + vals_offset);
    wysna_mem_on_alloc();
    for (int32_t i = 0; i < count; i++) {
        st->vals[i] = wysna_clone(vals[i]);
    }
    return wysna_make_hi_ptr(WVAL_HI_OBJECT, st);
}

/* Default non-optional weak fields are independent expired handles. */
WYSNA_RT_FN WVal wysna_empty_weak(void) {
    WWeakControl* control = (WWeakControl*)wysna_alloc_safe(sizeof(WWeakControl));
    memset(control, 0, sizeof(WWeakControl));
    control->kind = W_WEAK;
    control->strong = 0;
    control->weak = 1;
    control->color = W_COLOR_BLACK;
    control->object = NULL;
    wysna_mem_on_alloc();
    return wysna_make_hi_ptr(WVAL_HI_EXT, control);
}

/* Both helpers borrow their input and return one owned reference. */
WYSNA_RT_FN WVal wysna_builtin_weak(WVal object) {
    WStructBuf* st = wysna_hi(object) == WVAL_HI_OBJECT ? wysna_as_struct(object) : NULL;
    if (!st || !st->control) {
        wysna_runtime_fail_unlocated("W4003", "weak expects a class object");
    }
    WWeakControl* control = st->control;
    wysna_check_retain_count(wysna_rc_load(&control->weak), "retain/weak");
    wysna_rc_inc(&control->weak);
    wysna_rc_note_retain();
    return wysna_make_hi_ptr(WVAL_HI_EXT, control);
}

/* Language weak calls consume their evaluated argument snapshot immediately. */
WYSNA_RT_FN WVal wysna_builtin_weak_owned(WVal object) {
    WVal handle = wysna_builtin_weak(object);
    wysna_release(object);
    return handle;
}

WYSNA_RT_FN WVal wysna_builtin_upgrade(WVal weak) {
    WWeakControl* control = wysna_as_weak(weak);
    if (!control) {
        wysna_runtime_fail_unlocated("W4003", "upgrade expects a weak reference");
    }
    if (control->strong == 0 || control->object == NULL) return wysna_none();
    return wysna_clone(wysna_make_hi_ptr(WVAL_HI_OBJECT, control->object));
}

#endif /* WYSNA_RUNTIME_COLLECTIONS_H */
