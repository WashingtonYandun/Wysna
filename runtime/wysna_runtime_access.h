/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_ACCESS_H
#define WYSNA_RUNTIME_ACCESS_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
#endif

/* Indexing & Member Access */
static inline WVal wysna_index_get(WVal obj, WVal idx) {
    if (obj.tag == W_LIST) {
        int64_t i = idx.as.i;
        if (i < 0 || i >= obj.as.list->len) {
            wysna_runtime_fail("W4002", "List index out of bounds");
        }
        return obj.as.list->data[i];
    }
    if (obj.tag == W_STR) {
        size_t off = 0, blen = 0;
        if (!wysna_string_offset(obj, idx.as.i, &off, &blen) || blen == 0) {
            wysna_runtime_fail("W4002", "String index out of bounds");
        }
        if (blen == 1 && (unsigned char)obj.as.string.data[off] < 128)
            return wysna_ascii_string((uint8_t)obj.as.string.data[off]);
        char* buf = (char*)wysna_alloc_safe(blen + 1);
        memcpy(buf, obj.as.string.data + off, blen);
        buf[blen] = '\0';
        return wysna_owned_str_n(buf, blen);
    }
    if (obj.tag == W_DICT) {
        int32_t index = wysna_dict_find(obj.as.dict, idx);
        if (index < 0) {
            /* SPEC 10.2: a missing key is W4002. `d.get(k)` is the optional form. */
            wysna_runtime_fail("W4002", "Dictionary key not found");
        }
        return obj.as.dict->vals[index];
    }
    fprintf(stderr, "[W4003] RuntimeError: Cannot index object of tag %d\n", obj.tag);
    exit(1);
}

static inline WVal wysna_index_get_owned(WVal object, WVal index) {
    WVal value = wysna_index_get(object, index);
    return object.tag == W_STR ? value : wysna_clone(value);
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
    if (obj->tag == W_LIST) {
        wysna_list_detach(obj);
        int64_t i = idx.as.i;
        if (i < 0 || i >= obj->as.list->len) {
            wysna_runtime_fail("W4002", "List index out of bounds");
        }
        return &obj->as.list->data[i];
    }
    if (obj->tag == W_DICT) {
        wysna_dict_detach(obj);
        WDictBuf* d = obj->as.dict;
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
    if (obj.tag == W_STRUCT || obj.tag == W_OBJECT) {
        for (int32_t i = 0; i < obj.as.st->len; i++) {
            if (obj.as.st->keys[i] == prop || strcmp(obj.as.st->keys[i], prop) == 0) {
                return obj.as.st->vals[i];
            }
        }
    }
    return wysna_none();
}

static inline WVal* wysna_member_ref(WVal* obj, const char* prop) {
    if (obj->tag == W_STRUCT) {
        wysna_struct_detach(obj);
        for (int32_t i = 0; i < obj->as.st->len; i++) {
            if (obj->as.st->keys[i] == prop || strcmp(obj->as.st->keys[i], prop) == 0) {
                return &obj->as.st->vals[i];
            }
        }
    } else if (obj->tag == W_OBJECT) {
        for (int32_t i = 0; i < obj->as.st->len; i++) {
            if (obj->as.st->keys[i] == prop || strcmp(obj->as.st->keys[i], prop) == 0) {
                return &obj->as.st->vals[i];
            }
        }
    }
    fprintf(stderr, "[W4003] RuntimeError: Invalid field '%s'\n", prop);
    exit(1);
}

/* Physical field offsets resolved and checked before MIR reaches the backend. */
static inline WVal wysna_field_get_at(WVal object, int32_t index) {
    if ((object.tag != W_STRUCT && object.tag != W_OBJECT) || index < 0 || index >= object.as.st->len)
        wysna_runtime_fail("W4003", "Invalid physical field read");
    return object.as.st->vals[index];
}

static inline WVal* wysna_field_ref_at(WVal* object, int32_t index) {
    if ((object->tag != W_STRUCT && object->tag != W_OBJECT) || index < 0 || index >= object->as.st->len)
        wysna_runtime_fail("W4003", "Invalid physical field destination");
    if (object->tag == W_STRUCT) wysna_struct_detach(object);
    return &object->as.st->vals[index];
}

static inline WVal wysna_not(WVal value) { return wysna_bool(!value.as.b); }
static inline WVal wysna_equal(WVal left, WVal right) { return wysna_bool(wysna_eq(left, right)); }
static inline WVal wysna_not_equal(WVal left, WVal right) { return wysna_bool(!wysna_eq(left, right)); }
static inline WVal wysna_enum_payload_owned(WVal value) { return wysna_clone(wysna_peek_enum_payload(value)); }

/* Methods on List / Dict */
static inline WVal wysna_list_append(WVal* list_val, WVal item) {
    if (list_val->tag != W_LIST) wysna_runtime_fail("W4003", "List intrinsic requires list receiver");
    wysna_list_detach(list_val);
    WListBuf* b = list_val->as.list;
    if (b->len >= b->cap) {
        b->cap = b->cap > 0 ? b->cap * 2 : 4;
        b->data = (WVal*)wysna_realloc_safe(b->data, sizeof(WVal) * b->cap);
    }
    b->data[b->len++] = wysna_clone(item);
    return wysna_unit();
}

static inline WVal wysna_list_pop(WVal* list_val) {
    if (list_val->tag != W_LIST) wysna_runtime_fail("W4003", "List intrinsic requires list receiver");
    wysna_list_detach(list_val);
    WListBuf* b = list_val->as.list;
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
    if (list_val->tag != W_LIST) wysna_runtime_fail("W4003", "List intrinsic requires list receiver");
    wysna_list_detach(list_val);
    WListBuf* b = list_val->as.list;
    for (int32_t i = 0; i < b->len; i++) {
        wysna_release(b->data[i]);
    }
    b->len = 0;
    return wysna_unit();
}

static inline WVal wysna_dict_get(WVal dict_val, WVal key) {
    /* The optional lookup: unlike `d[k]`, an absent key is `none`, not W4002. */
    if (dict_val.tag != W_DICT) return wysna_none();
    int32_t found = wysna_dict_find(dict_val.as.dict, key);
    if (found < 0) return wysna_none();
    return wysna_enum_payload("Option", "Some", wysna_clone(dict_val.as.dict->vals[found]));
}

static inline WVal wysna_dict_keys(WVal dict_val) {
    if (dict_val.tag != W_DICT) wysna_runtime_fail("W4003", "Dictionary intrinsic requires dict receiver");
    WDictBuf* d = dict_val.as.dict;
    return wysna_make_list(d->len, d->keys);
}

static inline WVal wysna_dict_values(WVal dict_val) {
    if (dict_val.tag != W_DICT) wysna_runtime_fail("W4003", "Dictionary intrinsic requires dict receiver");
    WDictBuf* d = dict_val.as.dict;
    return wysna_make_list(d->len, d->vals);
}

#endif /* WYSNA_RUNTIME_ACCESS_H */
