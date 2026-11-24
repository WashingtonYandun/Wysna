/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_ACCESS_H
#define WYSNA_RUNTIME_ACCESS_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
#endif

/* Indexing & Member Access */
WYSNA_RT_FN WVal wysna_index_get(WVal obj, WVal idx) {
    uint32_t hi = wysna_hi(obj);
    if (hi == WVAL_HI_LIST) {
        WListBuf* b = wysna_as_list(obj);
        int64_t i = wysna_as_int(idx);
        if (!b || i < 0 || i >= b->len) {
            wysna_runtime_fail("W4002", "List index out of bounds");
        }
        return wysna_list_load(b, (int32_t)i);
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
    wysna_runtime_fail_unlocated("W4003", "Cannot index object of tag %d", wysna_tag(obj));
}

WYSNA_RT_FN WVal wysna_index_get_owned(WVal object, WVal index) {
    WVal value = wysna_index_get(object, index);
    return wysna_tag(object) == W_STR ? value : wysna_clone(value);
}

WYSNA_RT_FN WVal wysna_index_get_at(WVal object, WVal index, const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    return wysna_index_get(object, index);
}

WYSNA_RT_FN WVal wysna_index_get_owned_at(WVal object, WVal index, const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    return wysna_index_get_owned(object, index);
}

WYSNA_RT_FN WVal* wysna_index_ref(WVal* obj, WVal idx) {
    uint32_t hi = wysna_hi(*obj);
    if (hi == WVAL_HI_LIST) {
        wysna_list_detach(obj);
        WListBuf* b = wysna_as_list(*obj);
        int64_t i = wysna_as_int(idx);
        if (!b || i < 0 || i >= b->len) {
            wysna_runtime_fail("W4002", "List index out of bounds");
        }
        if (wysna_list_is_u8(b)) wysna_runtime_fail("W4003", "Packed list elements require a value store");
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
        uint64_t full_hash = 0;
        uint32_t slot = 0;
        int32_t found = wysna_dict_probe(d, idx, &full_hash, &slot);
        if (found >= 0) return &d->vals[found];
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
        int32_t new_index = d->len++;
        d->keys[new_index] = wysna_clone(idx);
        d->vals[new_index] = wysna_none();
        if (reindexed) {
            wysna_dict_index_entry_hashed(d, new_index, full_hash);
        } else {
            d->buckets[slot] = new_index;
        }
        return &d->vals[new_index];
    }
    wysna_runtime_fail_unlocated("W4003", "Cannot index-assign non-collection");
}

WYSNA_RT_FN WVal* wysna_index_ref_at(WVal* obj, WVal idx, const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    return wysna_index_ref(obj, idx);
}

WYSNA_RT_FN WVal wysna_index_store_at(WVal* object, WVal index, WVal value,
                                        const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    WListBuf* list = wysna_as_list(*object);
    if (list && wysna_list_is_u8(list)) {
        int64_t offset = wysna_as_int(index);
        if (offset < 0 || offset >= list->len) wysna_runtime_fail("W4002", "List index out of bounds");
        uint8_t byte = wysna_as_u8(value);
        wysna_release(value);
        wysna_list_detach(object);
        list = wysna_as_list(*object);
        ((uint8_t*)list->data)[offset] = byte;
        return wysna_int_inline(byte);
    }
    return wysna_assign(wysna_index_ref(object, index), value);
}

WYSNA_RT_FN WVal wysna_member_get(WVal obj, const char* prop) {
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

WYSNA_RT_FN WVal* wysna_member_ref(WVal* obj, const char* prop) {
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
    wysna_runtime_fail_unlocated("W4003", "Invalid field '%s'", prop);
}

/* Static types do not authorize dereferencing an uninitialized default value. */
WYSNA_RT_FN WVal wysna_tuple_get_at(WVal value, int32_t index,
                                     const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    WTupleBuf* t = wysna_as_tuple(value);
    if (!t) wysna_runtime_fail("W4003", "Cannot destructure tuple: length mismatch");
    if (index < 0 || index >= t->len) wysna_runtime_fail("W4002", "Tuple index out of bounds");
    return t->items[index];
}
WYSNA_RT_FN WVal wysna_tuple_get_owned_at(WVal value, int32_t index,
                                           const char* file, int32_t line, int32_t col) {
    return wysna_clone(wysna_tuple_get_at(value, index, file, line, col));
}
WYSNA_RT_FN int32_t wysna_list_iteration_length_at(WVal value,
                                                    const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    WListBuf* b = wysna_as_list(value);
    if (!b) wysna_runtime_fail("W4003", "For loop requires a list value");
    return b->len;
}
WYSNA_RT_FN WVal wysna_list_iteration_get_at(WVal value, int32_t index,
                                              const char* file, int32_t line, int32_t col) {
    int32_t length = wysna_list_iteration_length_at(value, file, line, col);
    if (index < 0 || index >= length)
        wysna_runtime_fail("W4002", "List index out of bounds");
    return wysna_list_load(wysna_as_list(value), index);
}
WYSNA_RT_FN WVal wysna_list_iteration_get_owned_at(WVal value, int32_t index,
                                                    const char* file, int32_t line, int32_t col) {
    return wysna_clone(wysna_list_iteration_get_at(value, index, file, line, col));
}

/* W03: Unboxed scalar list accessors (zero retain/release, zero allocation) */
WYSNA_RT_FN int64_t wysna_list_get_int_at(WVal obj, int64_t idx,
                                            const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    WListBuf* b = wysna_as_list(obj);
    if (!b || idx < 0 || idx >= b->len) {
        wysna_runtime_fail("W4002", "List index out of bounds");
    }
    return wysna_as_int(wysna_list_load(b, (int32_t)idx));
}

WYSNA_RT_FN double wysna_list_get_float_at(WVal obj, int64_t idx,
                                             const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    WListBuf* b = wysna_as_list(obj);
    if (!b || idx < 0 || idx >= b->len) {
        wysna_runtime_fail("W4002", "List index out of bounds");
    }
    return wysna_as_float(wysna_list_load(b, (int32_t)idx));
}

WYSNA_RT_FN bool wysna_list_get_bool_at(WVal obj, int64_t idx,
                                          const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    WListBuf* b = wysna_as_list(obj);
    if (!b || idx < 0 || idx >= b->len) {
        wysna_runtime_fail("W4002", "List index out of bounds");
    }
    return wysna_as_bool(wysna_list_load(b, (int32_t)idx));
}

WYSNA_RT_FN int64_t wysna_list_iteration_get_int_at(WVal value, int32_t index,
                                                      const char* file, int32_t line, int32_t col) {
    return wysna_as_int(wysna_list_iteration_get_at(value, index, file, line, col));
}

WYSNA_RT_FN double wysna_list_iteration_get_float_at(WVal value, int32_t index,
                                                       const char* file, int32_t line, int32_t col) {
    return wysna_as_float(wysna_list_iteration_get_at(value, index, file, line, col));
}

WYSNA_RT_FN bool wysna_list_iteration_get_bool_at(WVal value, int32_t index,
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

WYSNA_RT_FN WVal* wysna_field_ref_at(WVal* object, int32_t index) {
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
WYSNA_RT_FN WVal wysna_enum_payload_owned(WVal value) { return wysna_clone(wysna_peek_enum_payload(value)); }

/* Methods on List / Dict */
WYSNA_RT_FN WVal wysna_list_append(WVal* list_val, WVal item) {
    if (wysna_hi(*list_val) != WVAL_HI_LIST) wysna_runtime_fail("W4003", "List intrinsic requires list receiver");
    wysna_list_detach(list_val);
    WListBuf* b = wysna_as_list(*list_val);
    if (b->len >= b->cap) {
        if (b->cap > INT32_MAX / 2) wysna_runtime_fail("W4003", "List too large");
        b->cap = b->cap > 0 ? b->cap * 2 : 4;
        b->data = (WVal*)wysna_realloc_safe(b->data, wysna_list_element_size(b) * b->cap);
    }
    if (wysna_list_is_u8(b)) {
        ((uint8_t*)b->data)[b->len++] = wysna_as_u8(item);
        return wysna_unit();
    }
    WTag it = wysna_tag(item);
    if ((it != W_INT && it != W_FLOAT && it != W_BOOL) ||
        (it == W_INT && wysna_hi(item) == WVAL_HI_EXT)) {
        b->scalar_only = false;
    }
    b->data[b->len++] = wysna_is_immediate(item) ? item : wysna_clone(item);
    return wysna_unit();
}

WYSNA_RT_FN WVal wysna_list_pop(WVal* list_val) {
    if (wysna_hi(*list_val) != WVAL_HI_LIST) wysna_runtime_fail("W4003", "List intrinsic requires list receiver");
    wysna_list_detach(list_val);
    WListBuf* b = wysna_as_list(*list_val);
    if (b->len == 0) {
        wysna_runtime_fail("W4002", "pop from empty list");
    }
    b->len--;
    return wysna_list_load(b, b->len);
}

WYSNA_RT_FN WVal wysna_list_pop_at(WVal* list_val, const char* file, int32_t line, int32_t col) {
    wysna_runtime_set_position(file, line, col);
    return wysna_list_pop(list_val);
}

WYSNA_RT_FN WVal wysna_list_clear(WVal* list_val) {
    if (wysna_hi(*list_val) != WVAL_HI_LIST) wysna_runtime_fail("W4003", "List intrinsic requires list receiver");
    wysna_list_detach(list_val);
    WListBuf* b = wysna_as_list(*list_val);
    if (!b->scalar_only) {
        for (int32_t i = 0; i < b->len; i++) {
            wysna_release(b->data[i]);
        }
    }
    b->len = 0;
    if (!wysna_list_is_u8(b)) b->scalar_only = true;
    return wysna_unit();
}

WYSNA_RT_FN WVal wysna_dict_get(WVal dict_val, WVal key) {
    /* The optional lookup: unlike `d[k]`, an absent key is `none`, not W4002. */
    WDictBuf* d = wysna_as_dict(dict_val);
    if (!d || d->len == 0) return wysna_none();
    int32_t found = wysna_dict_find(d, key);
    if (found < 0) return wysna_none();
    return wysna_enum_payload("Option", "Some", wysna_clone(d->vals[found]));
}

WYSNA_RT_FN WVal wysna_dict_keys(WVal dict_val) {
    WDictBuf* d = wysna_as_dict(dict_val);
    if (!d) wysna_runtime_fail("W4003", "Dictionary intrinsic requires dict receiver");
    return wysna_make_list(d->len, d->keys);
}

WYSNA_RT_FN WVal wysna_dict_values(WVal dict_val) {
    WDictBuf* d = wysna_as_dict(dict_val);
    if (!d) wysna_runtime_fail("W4003", "Dictionary intrinsic requires dict receiver");
    return wysna_make_list(d->len, d->vals);
}

WYSNA_RT_FN WVal wysna_dict_clear(WVal* dict_val) {
    if (wysna_hi(*dict_val) != WVAL_HI_DICT) wysna_runtime_fail("W4003", "Dictionary intrinsic requires dict receiver");
    wysna_dict_detach(dict_val);
    WDictBuf* d = wysna_as_dict(*dict_val);
    for (int32_t i = 0; i < d->len; i++) {
        wysna_release(d->keys[i]);
        wysna_release(d->vals[i]);
    }
    d->len = 0;
    for (int32_t i = 0; i < d->bucket_count; i++) d->buckets[i] = -1;
    return wysna_unit();
}

WYSNA_RT_FN WVal wysna_dict_remove(WVal* dict_val, WVal key) {
    if (wysna_hi(*dict_val) != WVAL_HI_DICT) wysna_runtime_fail("W4003", "Dictionary intrinsic requires dict receiver");
    WDictBuf* probe = wysna_as_dict(*dict_val);
    if (!probe || probe->len == 0) return wysna_none();
    int32_t found = wysna_dict_find(probe, key);
    if (found < 0) return wysna_none();
    wysna_dict_detach(dict_val);
    WDictBuf* d = wysna_as_dict(*dict_val);
    found = wysna_dict_find(d, key);
    if (found < 0) return wysna_none();
    WVal removed_key = d->keys[found];
    WVal removed_val = d->vals[found];
    int32_t last = d->len - 1;
    if (found != last) {
        memmove(&d->keys[found], &d->keys[found + 1], sizeof(WVal) * (size_t)(last - found));
        memmove(&d->vals[found], &d->vals[found + 1], sizeof(WVal) * (size_t)(last - found));
    }
    d->len = last;
    for (int32_t i = 0; i < d->bucket_count; i++) d->buckets[i] = -1;
    for (int32_t i = 0; i < d->len; i++) wysna_dict_index_entry(d, i);
    wysna_release(removed_key);
    return wysna_enum_payload("Option", "Some", removed_val);
}

WYSNA_RT_FN WVal wysna_copy_str_n(const char* data, size_t length) {
    if (!data || length == 0) return wysna_sso_from_n("", 0);
    if (length <= 6) return wysna_sso_from_n(data, length);
    char* copy = (char*)wysna_alloc_safe(length + 1);
    memcpy(copy, data, length);
    copy[length] = '\0';
    return wysna_owned_str_n(copy, length);
}

static inline bool wysna_is_ascii_ws(unsigned char c) {
    return c == ' ' || (c >= 9 && c <= 13);
}

WYSNA_RT_FN WVal wysna_str_trim(WVal s) {
    char sb[7];
    size_t len = 0;
    const char* data = wysna_str_bytes(s, sb, &len);
    size_t start = 0;
    while (start < len && wysna_is_ascii_ws((unsigned char)data[start])) start++;
    size_t end = len;
    while (end > start && wysna_is_ascii_ws((unsigned char)data[end - 1])) end--;
    return wysna_copy_str_n(data + start, end - start);
}

WYSNA_RT_FN WVal wysna_str_to_lower(WVal s) {
    char sb[7];
    size_t len = 0;
    const char* data = wysna_str_bytes(s, sb, &len);
    if (len == 0) return wysna_sso_from_n("", 0);
    char* out = (char*)wysna_alloc_safe(len + 1);
    for (size_t i = 0; i < len; i++) {
        unsigned char c = (unsigned char)data[i];
        out[i] = (c >= 'A' && c <= 'Z') ? (char)(c + 32) : (char)c;
    }
    out[len] = '\0';
    return wysna_owned_str_n(out, len);
}

WYSNA_RT_FN WVal wysna_str_to_upper(WVal s) {
    char sb[7];
    size_t len = 0;
    const char* data = wysna_str_bytes(s, sb, &len);
    if (len == 0) return wysna_sso_from_n("", 0);
    char* out = (char*)wysna_alloc_safe(len + 1);
    for (size_t i = 0; i < len; i++) {
        unsigned char c = (unsigned char)data[i];
        out[i] = (c >= 'a' && c <= 'z') ? (char)(c - 32) : (char)c;
    }
    out[len] = '\0';
    return wysna_owned_str_n(out, len);
}

WYSNA_RT_FN WVal wysna_str_starts_with(WVal s, WVal prefix) {
    char sbs[7], sbp[7];
    size_t slen = 0, plen = 0;
    const char* sd = wysna_str_bytes(s, sbs, &slen);
    const char* pd = wysna_str_bytes(prefix, sbp, &plen);
    return wysna_bool(plen <= slen && (plen == 0 || memcmp(sd, pd, plen) == 0));
}

WYSNA_RT_FN WVal wysna_str_ends_with(WVal s, WVal suffix) {
    char sbs[7], sbp[7];
    size_t slen = 0, plen = 0;
    const char* sd = wysna_str_bytes(s, sbs, &slen);
    const char* pd = wysna_str_bytes(suffix, sbp, &plen);
    return wysna_bool(plen <= slen && (plen == 0 || memcmp(sd + slen - plen, pd, plen) == 0));
}

WYSNA_RT_FN WVal wysna_str_split(WVal s, WVal sep) {
    char sbs[7], sbsep[7];
    size_t slen = 0, seplen = 0;
    const char* sd = wysna_str_bytes(s, sbs, &slen);
    const char* sepd = wysna_str_bytes(sep, sbsep, &seplen);
    WListBuf* b = (WListBuf*)wysna_alloc_safe(sizeof(WListBuf));
    b->refcount = 1;
    b->len = 0;
    b->cap = 8;
    b->scalar_only = false;
    b->data = (WVal*)wysna_alloc_safe(sizeof(WVal) * (size_t)b->cap);
    wysna_mem_on_alloc();
    #define W_SPLIT_PUSH(elem_val) do { \
        if (b->len >= b->cap) { \
            b->cap *= 2; \
            b->data = (WVal*)wysna_realloc_safe(b->data, sizeof(WVal) * (size_t)b->cap); \
        } \
        b->data[b->len++] = (elem_val); \
    } while (0)
    if (seplen == 0) {
        size_t pos = 0;
        while (pos < slen) {
            size_t step = 1;
            wysna_utf8_valid_codepoint_at_n(sd, slen, pos, &step, NULL);
            if (step == 0) step = 1;
            W_SPLIT_PUSH(wysna_copy_str_n(sd + pos, step));
            pos += step;
        }
    } else {
        size_t start = 0;
        size_t i = 0;
        while (i + seplen <= slen) {
            if (memcmp(sd + i, sepd, seplen) == 0) {
                W_SPLIT_PUSH(wysna_copy_str_n(sd + start, i - start));
                i += seplen;
                start = i;
            } else {
                i++;
            }
        }
        W_SPLIT_PUSH(wysna_copy_str_n(sd + start, slen - start));
    }
    #undef W_SPLIT_PUSH
    return wysna_make_hi_ptr(WVAL_HI_LIST, b);
}

WYSNA_RT_FN WVal wysna_str_replace(WVal s, WVal old_sub, WVal new_sub) {
    char sbs[7], sbo[7], sbn[7];
    size_t slen = 0, olen = 0, nlen = 0;
    const char* sd = wysna_str_bytes(s, sbs, &slen);
    const char* od = wysna_str_bytes(old_sub, sbo, &olen);
    const char* nd = wysna_str_bytes(new_sub, sbn, &nlen);
    if (olen == 0) return wysna_copy_str_n(sd, slen);
    size_t cap = slen + 16;
    char* out = (char*)wysna_alloc_safe(cap);
    size_t out_len = 0;
    size_t i = 0;
    while (i < slen) {
        if (i + olen <= slen && memcmp(sd + i, od, olen) == 0) {
            if (out_len + nlen + 1 > cap) {
                while (out_len + nlen + 1 > cap) cap *= 2;
                out = (char*)wysna_realloc_safe(out, cap);
            }
            if (nlen > 0) memcpy(out + out_len, nd, nlen);
            out_len += nlen;
            i += olen;
        } else {
            if (out_len + 2 > cap) {
                cap *= 2;
                out = (char*)wysna_realloc_safe(out, cap);
            }
            out[out_len++] = sd[i++];
        }
    }
    out[out_len] = '\0';
    return wysna_owned_str_n(out, out_len);
}

WYSNA_RT_FN WVal wysna_contains(WVal container, WVal item) {
    uint32_t hi = wysna_hi(container);
    if (hi == WVAL_HI_LIST) {
        WListBuf* b = wysna_as_list(container);
        if (!b || b->len == 0) return wysna_bool(false);
        for (int32_t i = 0; i < b->len; i++) {
            if (wysna_eq(wysna_list_load(b, i), item)) return wysna_bool(true);
        }
        return wysna_bool(false);
    }
    if (hi == WVAL_HI_DICT) {
        WDictBuf* d = wysna_as_dict(container);
        if (!d || d->len == 0) return wysna_bool(false);
        return wysna_bool(wysna_dict_find(d, item) >= 0);
    }
    if (wysna_tag(container) == W_STR && wysna_tag(item) == W_STR) {
        char sbh[7], sbn[7];
        size_t hlen = 0, nlen = 0;
        const char* hay = wysna_str_bytes(container, sbh, &hlen);
        const char* needle = wysna_str_bytes(item, sbn, &nlen);
        if (nlen == 0) return wysna_bool(true);
        if (nlen > hlen) return wysna_bool(false);
        for (size_t i = 0; i <= hlen - nlen; i++) {
            if (memcmp(hay + i, needle, nlen) == 0) return wysna_bool(true);
        }
        return wysna_bool(false);
    }
    return wysna_bool(false);
}

#endif /* WYSNA_RUNTIME_ACCESS_H */
