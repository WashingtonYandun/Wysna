/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_ACCESS_H
#define WYSNA_RUNTIME_ACCESS_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
#endif

/* Indexing & Member Access */
static inline WVal wysna_index_get(WVal obj, WVal idx) {
    uint32_t hi = wysna_hi(obj);
    if (hi == WVAL_HI_LIST) {
        WListBuf* b = wysna_as_list(obj);
        int64_t i = wysna_as_int(idx);
        if (!b || i < 0 || i >= b->len) {
            wysna_runtime_fail("W4002", "List index out of bounds");
        }
        return b->data[i];
    }
    if (wysna_tag(obj) == W_STR) {
        size_t off = 0, blen = 0;
        if (!wysna_string_offset(obj, wysna_as_int(idx), &off, &blen) || blen == 0) {
            wysna_runtime_fail("W4002", "String index out of bounds");
        }
        char scratch[7];
        size_t total_len = 0;
        const char* sdata = wysna_str_bytes(obj, scratch, &total_len);
        (void)total_len;
        if (blen <= 6) {
            return wysna_sso_from_n(sdata + off, blen);
        }
        char* buf = (char*)wysna_alloc_safe(blen + 1);
        memcpy(buf, sdata + off, blen);
        buf[blen] = '\0';
        return wysna_owned_str_n(buf, blen);
    }
    if (hi == WVAL_HI_DICT) {
        WDictBuf* d = wysna_as_dict(obj);
        int32_t index = wysna_dict_find(d, idx);
        if (index < 0) {
            /* SPEC 10.2: a missing key is W4002. `d.get(k)` is the optional form. */
            wysna_runtime_fail("W4002", "Dictionary key not found");
        }
        return d->vals[index];
    }
    fprintf(stderr, "[W4003] RuntimeError: Cannot index object of tag %d\n", wysna_tag(obj));
    exit(1);
}

static inline WVal wysna_index_get_owned(WVal object, WVal index) {
    WVal value = wysna_index_get(object, index);
    return wysna_tag(object) == W_STR ? value : wysna_clone(value);
}

static inline WVal wysna_index_get_at(WVal object, WVal index, const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    return wysna_index_get(object, index);
}

static inline WVal wysna_index_get_owned_at(WVal object, WVal index, const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    return wysna_index_get_owned(object, index);
}

static inline WVal* wysna_index_ref(WVal* obj, WVal idx) {
    uint32_t hi = wysna_hi(*obj);
    if (hi == WVAL_HI_LIST) {
        wysna_list_detach(obj);
        WListBuf* b = wysna_as_list(*obj);
        int64_t i = wysna_as_int(idx);
        if (!b || i < 0 || i >= b->len) {
            wysna_runtime_fail("W4002", "List index out of bounds");
        }
        WTag existing_tag = wysna_tag(b->data[i]);
        if ((existing_tag != W_INT && existing_tag != W_FLOAT && existing_tag != W_BOOL) ||
            (existing_tag == W_INT && wysna_hi(b->data[i]) == WVAL_HI_EXT)) {
            b->scalar_only = false;
        }
        return &b->data[i];
    }
    if (hi == WVAL_HI_DICT) {
        wysna_dict_detach(obj);
        WDictBuf* d = wysna_as_dict(*obj);
        int32_t found = wysna_dict_find(d, idx);
        if (found >= 0) return &d->vals[found];
        if (d->cap > INT32_MAX / 2 || d->bucket_count > INT32_MAX / 2) {
            fprintf(stderr, "[W4003] RuntimeError: Dictionary too large\n"); exit(1);
        }
        if ((int64_t)(d->len + 1) * 2 >= d->bucket_count) wysna_dict_reindex(d, d->bucket_count * 2);
        if (d->len >= d->cap) {
            d->cap = d->cap > 0 ? d->cap * 2 : 4;
            d->keys = (WVal*)wysna_realloc_safe(d->keys, sizeof(WVal) * d->cap);
            d->vals = (WVal*)wysna_realloc_safe(d->vals, sizeof(WVal) * d->cap);
        }
        d->keys[d->len] = wysna_clone(idx);
        d->vals[d->len] = wysna_none();
        d->len++;
        wysna_dict_index_entry(d, d->len - 1);
        return &d->vals[d->len - 1];
    }
    fprintf(stderr, "[W4003] RuntimeError: Cannot index-assign non-collection\n");
    exit(1);
}

static inline WVal* wysna_index_ref_at(WVal* obj, WVal idx, const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    return wysna_index_ref(obj, idx);
}

static inline WVal wysna_member_get(WVal obj, const char* prop) {
    WStructBuf* st = wysna_as_struct(obj);
    if (st) {
        for (int32_t i = 0; i < st->len; i++) {
            if (st->keys[i] == prop || strcmp(st->keys[i], prop) == 0) {
                return st->vals[i];
            }
        }
    }
    return wysna_none();
}

static inline WVal* wysna_member_ref(WVal* obj, const char* prop) {
    uint32_t hi = wysna_hi(*obj);
    if (hi == WVAL_HI_STRUCT) {
        wysna_struct_detach(obj);
        WStructBuf* st = wysna_as_struct(*obj);
        for (int32_t i = 0; i < st->len; i++) {
            if (st->keys[i] == prop || strcmp(st->keys[i], prop) == 0) {
                return &st->vals[i];
            }
        }
    } else if (hi == WVAL_HI_OBJECT) {
        WStructBuf* st = wysna_as_struct(*obj);
        for (int32_t i = 0; i < st->len; i++) {
            if (st->keys[i] == prop || strcmp(st->keys[i], prop) == 0) {
                return &st->vals[i];
            }
        }
    }
    fprintf(stderr, "[W4003] RuntimeError: Invalid field '%s'\n", prop);
    exit(1);
}

/* Static types do not authorize dereferencing an uninitialized default value. */
static inline WVal wysna_tuple_get_at(WVal value, int32_t index,
                                     const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    WTupleBuf* t = wysna_as_tuple(value);
    if (!t) wysna_runtime_fail("W4003", "Cannot destructure tuple: length mismatch");
    if (index < 0 || index >= t->len) wysna_runtime_fail("W4002", "Tuple index out of bounds");
    return t->items[index];
}
static inline WVal wysna_tuple_get_owned_at(WVal value, int32_t index,
                                           const char* file, int32_t line, int32_t col) {
    return wysna_clone(wysna_tuple_get_at(value, index, file, line, col));
}
static inline int32_t wysna_list_iteration_length_at(WVal value,
                                                    const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    WListBuf* b = wysna_as_list(value);
    if (!b) wysna_runtime_fail("W4003", "For loop requires a list value");
    return b->len;
}
static inline WVal wysna_list_iteration_get_at(WVal value, int32_t index,
                                              const char* file, int32_t line, int32_t col) {
    int32_t length = wysna_list_iteration_length_at(value, file, line, col);
    if (index < 0 || index >= length)
        wysna_runtime_fail("W4002", "List index out of bounds");
    return wysna_as_list(value)->data[index];
}
static inline WVal wysna_list_iteration_get_owned_at(WVal value, int32_t index,
                                                    const char* file, int32_t line, int32_t col) {
    return wysna_clone(wysna_list_iteration_get_at(value, index, file, line, col));
}

/* W03: Unboxed scalar list accessors (zero retain/release, zero allocation) */
static inline int64_t wysna_list_get_int_at(WVal obj, int64_t idx,
                                            const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    WListBuf* b = wysna_as_list(obj);
    if (!b || idx < 0 || idx >= b->len) {
        wysna_runtime_fail("W4002", "List index out of bounds");
    }
    return wysna_as_int(b->data[idx]);
}

static inline double wysna_list_get_float_at(WVal obj, int64_t idx,
                                             const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    WListBuf* b = wysna_as_list(obj);
    if (!b || idx < 0 || idx >= b->len) {
        wysna_runtime_fail("W4002", "List index out of bounds");
    }
    return wysna_as_float(b->data[idx]);
}

static inline bool wysna_list_get_bool_at(WVal obj, int64_t idx,
                                          const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    WListBuf* b = wysna_as_list(obj);
    if (!b || idx < 0 || idx >= b->len) {
        wysna_runtime_fail("W4002", "List index out of bounds");
    }
    return wysna_as_bool(b->data[idx]);
}

static inline int64_t wysna_list_iteration_get_int_at(WVal value, int32_t index,
                                                      const char* file, int32_t line, int32_t col) {
    return wysna_as_int(wysna_list_iteration_get_at(value, index, file, line, col));
}

static inline double wysna_list_iteration_get_float_at(WVal value, int32_t index,
                                                       const char* file, int32_t line, int32_t col) {
    return wysna_as_float(wysna_list_iteration_get_at(value, index, file, line, col));
}

static inline bool wysna_list_iteration_get_bool_at(WVal value, int32_t index,
                                                    const char* file, int32_t line, int32_t col) {
    return wysna_as_bool(wysna_list_iteration_get_at(value, index, file, line, col));
}

/* Physical field offsets resolved and checked before MIR reaches the backend. */
static inline WVal wysna_field_get_at(WVal object, int32_t index) {
    WStructBuf* st = wysna_as_struct(object);
    if (!st || index < 0 || index >= st->len)
        wysna_runtime_fail("W4003", "Invalid physical field read");
    return st->vals[index];
}

static inline WVal* wysna_field_ref_at(WVal* object, int32_t index) {
    WStructBuf* st = wysna_as_struct(*object);
    if (!st || index < 0 || index >= st->len)
        wysna_runtime_fail("W4003", "Invalid physical field destination");
    if (wysna_hi(*object) == WVAL_HI_STRUCT) {
        wysna_struct_detach(object);
        st = wysna_as_struct(*object);
    }
    return &st->vals[index];
}

static inline WVal wysna_not(WVal value) { return wysna_bool(!wysna_as_bool(value)); }
static inline WVal wysna_equal(WVal left, WVal right) { return wysna_bool(wysna_eq(left, right)); }
static inline WVal wysna_not_equal(WVal left, WVal right) { return wysna_bool(!wysna_eq(left, right)); }
static inline WVal wysna_enum_payload_owned(WVal value) { return wysna_clone(wysna_peek_enum_payload(value)); }

/* Methods on List / Dict */
static inline WVal wysna_list_append(WVal* list_val, WVal item) {
    if (wysna_hi(*list_val) != WVAL_HI_LIST) wysna_runtime_fail("W4003", "List intrinsic requires list receiver");
    wysna_list_detach(list_val);
    WListBuf* b = wysna_as_list(*list_val);
    if (b->len >= b->cap) {
        b->cap = b->cap > 0 ? b->cap * 2 : 4;
        b->data = (WVal*)wysna_realloc_safe(b->data, sizeof(WVal) * b->cap);
    }
    WTag it = wysna_tag(item);
    if ((it != W_INT && it != W_FLOAT && it != W_BOOL) ||
        (it == W_INT && wysna_hi(item) == WVAL_HI_EXT)) {
        b->scalar_only = false;
    }
    b->data[b->len++] = wysna_is_immediate(item) ? item : wysna_clone(item);
    return wysna_unit();
}

static inline WVal wysna_list_pop(WVal* list_val) {
    if (wysna_hi(*list_val) != WVAL_HI_LIST) wysna_runtime_fail("W4003", "List intrinsic requires list receiver");
    wysna_list_detach(list_val);
    WListBuf* b = wysna_as_list(*list_val);
    if (b->len == 0) {
        wysna_runtime_fail("W4002", "pop from empty list");
    }
    return b->data[--b->len];
}

static inline WVal wysna_list_pop_at(WVal* list_val, const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    return wysna_list_pop(list_val);
}

static inline WVal wysna_list_clear(WVal* list_val) {
    if (wysna_hi(*list_val) != WVAL_HI_LIST) wysna_runtime_fail("W4003", "List intrinsic requires list receiver");
    wysna_list_detach(list_val);
    WListBuf* b = wysna_as_list(*list_val);
    if (!b->scalar_only) {
        for (int32_t i = 0; i < b->len; i++) {
            wysna_release(b->data[i]);
        }
    }
    b->len = 0;
    b->scalar_only = true;
    return wysna_unit();
}

static inline WVal wysna_dict_get(WVal dict_val, WVal key) {
    /* The optional lookup: unlike `d[k]`, an absent key is `none`, not W4002. */
    WDictBuf* d = wysna_as_dict(dict_val);
    if (!d || d->len == 0) return wysna_none();
    int32_t found = wysna_dict_find(d, key);
    if (found < 0) return wysna_none();
    return wysna_enum_payload("Option", "Some", wysna_clone(d->vals[found]));
}

static inline WVal wysna_dict_keys(WVal dict_val) {
    WDictBuf* d = wysna_as_dict(dict_val);
    if (!d) wysna_runtime_fail("W4003", "Dictionary intrinsic requires dict receiver");
    return wysna_make_list(d->len, d->keys);
}

static inline WVal wysna_dict_values(WVal dict_val) {
    WDictBuf* d = wysna_as_dict(dict_val);
    if (!d) wysna_runtime_fail("W4003", "Dictionary intrinsic requires dict receiver");
    return wysna_make_list(d->len, d->vals);
}

#endif /* WYSNA_RUNTIME_ACCESS_H */
