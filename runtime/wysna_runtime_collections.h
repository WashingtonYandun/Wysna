/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_COLLECTIONS_H
#define WYSNA_RUNTIME_COLLECTIONS_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
#endif

/* Containers: List, Dict, Tuple, Struct */
static inline WVal wysna_make_list(int32_t count, const WVal* elems) {
    WListBuf* b = (WListBuf*)wysna_alloc_safe(sizeof(WListBuf));
    b->refcount = 1;
    b->len = count;
    b->cap = count > 4 ? count : 4;
    b->data = (WVal*)wysna_alloc_safe(sizeof(WVal) * b->cap);
    wysna_mem_on_alloc();
    for (int32_t i = 0; i < count; i++) {
        b->data[i] = wysna_clone(elems[i]);
    }
    WVal v; v.tag = W_LIST; v.as.list = b; return v;
}

static inline int32_t wysna_range_count(WVal start, WVal end, bool inclusive) {
    int64_t s = start.as.i;
    int64_t e = end.as.i;
    if (inclusive) {
        if (e == INT64_MAX) {
            fprintf(stderr, "[W4003] RuntimeError: Range bound overflow at INT64_MAX\n");
            exit(1);
        }
        e++;
    }
    uint64_t diff = e > s ? (uint64_t)e - (uint64_t)s : 0;
    if (diff > 100000000ULL) {
        fprintf(stderr, "[W4003] RuntimeError: Range length %" PRIu64 " exceeds maximum list capacity\n", diff);
        exit(1);
    }
    return (int32_t)diff;
}

static inline WVal wysna_make_range(WVal start, WVal end, bool inclusive) {
    int64_t s = start.as.i;
    int32_t count = wysna_range_count(start, end, inclusive);
    WListBuf* b = (WListBuf*)wysna_alloc_safe(sizeof(WListBuf));
    b->refcount = 1;
    b->len = count;
    b->cap = count > 4 ? count : 4;
    b->data = (WVal*)wysna_alloc_safe(sizeof(WVal) * b->cap);
    wysna_mem_on_alloc();
    for (int32_t i = 0; i < count; i++) {
        b->data[i] = wysna_int(s + (int64_t)i);
    }
    WVal v; v.tag = W_LIST; v.as.list = b; return v;
}

static inline WVal wysna_make_tuple(int32_t count, const WVal* elems) {
    WTupleBuf* t = (WTupleBuf*)wysna_alloc_safe(sizeof(WTupleBuf));
    t->refcount = 1;
    t->len = count;
    t->items = (WVal*)wysna_alloc_safe(sizeof(WVal) * (count > 0 ? count : 1));
    wysna_mem_on_alloc();
    for (int32_t i = 0; i < count; i++) {
        t->items[i] = wysna_clone(elems[i]);
    }
    WVal v; v.tag = W_TUPLE; v.as.tuple = t; return v;
}

static inline WVal wysna_make_dict(int32_t count, const WVal* keys, const WVal* vals) {
    if (count < 0 || count > INT32_MAX / 4) { fprintf(stderr, "[W4003] RuntimeError: Dictionary too large\n"); exit(1); }
    WDictBuf* d = (WDictBuf*)wysna_alloc_safe(sizeof(WDictBuf));
    d->refcount = 1;
    d->len = 0;
    d->cap = count > 4 ? count * 2 : 4;
    d->keys = (WVal*)wysna_alloc_safe(sizeof(WVal) * d->cap);
    d->vals = (WVal*)wysna_alloc_safe(sizeof(WVal) * d->cap);
    d->buckets = NULL;
    int32_t buckets = 8;
    while (buckets < d->cap) {
        if (buckets > INT32_MAX / 2) { fprintf(stderr, "[W4003] RuntimeError: Dictionary too large\n"); exit(1); }
        buckets *= 2;
    }
    wysna_dict_reindex(d, buckets);
    wysna_mem_on_alloc();
    WVal v; v.tag = W_DICT; v.as.dict = d;
    for (int32_t i = 0; i < count; i++) {
        int32_t index = wysna_dict_find(d, keys[i]);
        if (index >= 0) { wysna_release(d->vals[index]); d->vals[index] = wysna_clone(vals[i]); }
        else {
            if ((int64_t)(d->len + 1) * 2 >= d->bucket_count) {
                if (d->bucket_count > INT32_MAX / 2) { fprintf(stderr, "[W4003] RuntimeError: Dictionary too large\n"); exit(1); }
                wysna_dict_reindex(d, d->bucket_count * 2);
            }
            index = d->len++;
            d->keys[index] = wysna_clone(keys[i]);
            d->vals[index] = wysna_clone(vals[i]);
            wysna_dict_index_entry(d, index);
        }
    }
    return v;
}

static inline WVal wysna_make_struct(const char* name, int32_t count, const char** keys, const WVal* vals) {
    WStructBuf* st = (WStructBuf*)wysna_alloc_safe(sizeof(WStructBuf));
    st->refcount = 1;
    st->control = NULL;
    st->vtable = NULL;
    st->name = name;
    st->len = count;
    st->keys = (const char**)wysna_alloc_safe(sizeof(const char*) * (count > 0 ? count : 1));
    st->vals = (WVal*)wysna_alloc_safe(sizeof(WVal) * (count > 0 ? count : 1));
    wysna_mem_on_alloc();
    for (int32_t i = 0; i < count; i++) {
        st->keys[i] = keys[i];
        st->vals[i] = wysna_clone(vals[i]);
    }
    WVal v; v.tag = W_STRUCT; v.as.st = st; return v;
}

static inline WVal wysna_make_object(const char* name, int32_t count, const char** keys, const WVal* vals) {
    WStructBuf* st = (WStructBuf*)wysna_alloc_safe(sizeof(WStructBuf));
    st->refcount = 1;
    st->control = (WWeakControl*)wysna_alloc_safe(sizeof(WWeakControl));
    st->control->strong = 1;
    st->control->weak = 1;
    st->control->object = st;
    wysna_mem_on_alloc();
    st->vtable = NULL;
    st->name = name;
    st->len = count;
    st->keys = (const char**)wysna_alloc_safe(sizeof(const char*) * (count > 0 ? count : 1));
    st->vals = (WVal*)wysna_alloc_safe(sizeof(WVal) * (count > 0 ? count : 1));
    wysna_mem_on_alloc();
    for (int32_t i = 0; i < count; i++) {
        st->keys[i] = keys[i];
        st->vals[i] = wysna_clone(vals[i]);
    }
    WVal v; v.tag = W_OBJECT; v.as.st = st; return v;
}

/* Default non-optional weak fields are independent expired handles. */
static inline WVal wysna_empty_weak(void) {
    WWeakControl* control = (WWeakControl*)wysna_alloc_safe(sizeof(WWeakControl));
    control->strong = 0;
    control->weak = 1;
    control->object = NULL;
    wysna_mem_on_alloc();
    WVal value = wysna_unit();
    value.tag = W_WEAK;
    value.as.weak = control;
    return value;
}

/* Both helpers borrow their input and return one owned reference. */
static inline WVal wysna_builtin_weak(WVal object) {
    if (object.tag != W_OBJECT) {
        fprintf(stderr, "[W4003] RuntimeError: weak expects a class object\n");
        exit(1);
    }
    WVal value = wysna_unit();
    value.tag = W_WEAK;
    value.as.weak = object.as.st->control;
    value.as.weak->weak++;
    wysna_g_retain_count++;
    return value;
}

/* Language weak calls consume their evaluated argument snapshot immediately. */
static inline WVal wysna_builtin_weak_owned(WVal object) {
    WVal handle = wysna_builtin_weak(object);
    wysna_release(object);
    return handle;
}

static inline WVal wysna_builtin_upgrade(WVal weak) {
    if (weak.tag != W_WEAK) {
        fprintf(stderr, "[W4003] RuntimeError: upgrade expects a weak reference\n");
        exit(1);
    }
    WWeakControl* control = weak.as.weak;
    if (control->strong == 0 || control->object == NULL) return wysna_none();
    WVal object = wysna_unit();
    object.tag = W_OBJECT;
    object.as.st = control->object;
    return wysna_clone(object);
}

#endif /* WYSNA_RUNTIME_COLLECTIONS_H */
