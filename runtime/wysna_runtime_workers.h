/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_WORKERS_H
#define WYSNA_RUNTIME_WORKERS_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
#endif

/* Each boundary owns one memo: aliases remain local, with no shared ARC buffers. */
typedef struct {
    uint64_t key;
    WVal value;
} WysnaWorkerCopyEntry;

typedef struct {
    WysnaWorkerCopyEntry* entries;
    size_t capacity;
    size_t count;
} WysnaWorkerCopy;

static inline WysnaWorkerCopyEntry* wysna_worker_copy_slot(WysnaWorkerCopy* memo, uint64_t key) {
    size_t slot = (size_t)wysna_mix64(key) & (memo->capacity - 1);
    while (memo->entries[slot].key && memo->entries[slot].key != key) {
        slot = (slot + 1) & (memo->capacity - 1);
    }
    return &memo->entries[slot];
}

static inline void wysna_worker_copy_grow(WysnaWorkerCopy* memo) {
    size_t capacity = memo->capacity ? memo->capacity * 2 : 32;
    if (capacity < memo->capacity || capacity > SIZE_MAX / sizeof(WysnaWorkerCopyEntry)) {
        wysna_runtime_fail("W4003", "Worker snapshot is too large");
    }
    WysnaWorkerCopyEntry* old = memo->entries;
    size_t old_capacity = memo->capacity;
    memo->entries = (WysnaWorkerCopyEntry*)wysna_alloc_safe(capacity * sizeof(WysnaWorkerCopyEntry));
    memset(memo->entries, 0, capacity * sizeof(WysnaWorkerCopyEntry));
    memo->capacity = capacity;
    for (size_t i = 0; i < old_capacity; i++) {
        if (old[i].key) *wysna_worker_copy_slot(memo, old[i].key) = old[i];
    }
    free(old);
}

static inline WVal wysna_worker_copy_value(WysnaWorkerCopy* memo, WVal value, int32_t depth) {
    wysna_step_tick();
    if (depth > 256) wysna_runtime_fail("W4003", "Worker snapshot nesting exceeds 256");
    WTag tag = wysna_tag(value);
    if (tag == W_UNIT || tag == W_NONE || tag == W_BOOL || tag == W_FLOAT) return value;
    if (tag == W_INT) return wysna_int(wysna_as_int(value));
    if (tag == W_OBJECT || tag == W_WEAK || tag == W_POINTER) {
        wysna_runtime_fail("W4003", "Worker transfer rejects classes, Weak and opaque handles");
    }
    if (tag == W_STR && (wysna_hi(value) == WVAL_HI_SSO_SHORT || wysna_hi(value) == WVAL_HI_SSO_6)) return value;
    if (!memo->capacity || memo->count >= memo->capacity / 2) wysna_worker_copy_grow(memo);
    WysnaWorkerCopyEntry* entry = wysna_worker_copy_slot(memo, value.bits);
    if (entry->key) {
        if (wysna_tag(entry->value) == W_UNIT) wysna_runtime_fail("W4003", "Worker transfer rejects cyclic value graphs");
        return wysna_clone(entry->value);
    }
    entry->key = value.bits;
    entry->value = wysna_unit();
    memo->count++;
    WVal copied = wysna_unit();
    if (tag == W_STR) {
        char scratch[7];
        size_t length = 0;
        const char* data = wysna_str_bytes(value, scratch, &length);
        copied = wysna_copy_str_n(data, length);
    } else if (tag == W_FUNCTION) {
        WFunctionBuf* function = wysna_as_function(value);
        WVal environment = wysna_worker_copy_value(memo, function->environment, depth + 1);
        copied = wysna_function_new(function->invoke, environment);
    } else if (tag == W_OK || tag == W_ERR) {
        WVal inner = wysna_worker_copy_value(memo, wysna_as_box(value)->val, depth + 1);
        copied = tag == W_OK ? wysna_ok(inner) : wysna_err(inner);
    } else if (tag == W_ENUM) {
        WEnumBuf* enumeration = wysna_as_enum(value);
        if (enumeration->has_payload) {
            WVal payload = wysna_worker_copy_value(memo, enumeration->payload, depth + 1);
            copied = wysna_enum_payload(enumeration->enum_name, enumeration->variant, payload);
        } else copied = wysna_enum(enumeration->enum_name, enumeration->variant);
    } else if (tag == W_LIST && wysna_list_is_u8(wysna_as_list(value))) {
        WListBuf* list = wysna_as_list(value);
        copied = wysna_list_slice(list, 0, list->len);
    } else if (tag == W_LIST || tag == W_TUPLE || tag == W_STRUCT || tag == W_DICT) {
        int32_t count = tag == W_LIST ? wysna_as_list(value)->len :
            tag == W_TUPLE ? wysna_as_tuple(value)->len :
            tag == W_STRUCT ? wysna_as_struct(value)->len : wysna_as_dict(value)->len;
        if (count < 0) wysna_runtime_fail("W4003", "Worker snapshot has invalid container length");
        size_t slots = (size_t)count * (tag == W_DICT ? 2 : 1);
        if (slots > SIZE_MAX / sizeof(WVal)) wysna_runtime_fail("W4003", "Worker snapshot is too large");
        WVal* children = (WVal*)wysna_alloc_safe(slots * sizeof(WVal));
        const WVal* first = tag == W_LIST ? wysna_as_list(value)->data :
            tag == W_TUPLE ? wysna_as_tuple(value)->items :
            tag == W_STRUCT ? wysna_as_struct(value)->vals : wysna_as_dict(value)->keys;
        for (int32_t i = 0; i < count; i++) {
            children[i] = wysna_worker_copy_value(memo, first[i], depth + 1);
            if (tag == W_DICT) children[count + i] = wysna_worker_copy_value(memo, wysna_as_dict(value)->vals[i], depth + 1);
        }
        if (tag == W_LIST) copied = wysna_make_list(count, children);
        else if (tag == W_TUPLE) copied = wysna_make_tuple(count, children);
        else if (tag == W_DICT) copied = wysna_make_dict(count, children, children ? children + count : NULL);
        else {
            WStructBuf* record = wysna_as_struct(value);
            copied = wysna_make_struct(record->name, count, record->keys, children);
        }
        for (size_t i = 0; i < slots; i++) wysna_release(children[i]);
        free(children);
    } else wysna_runtime_fail("W4003", "Worker transfer encountered an unsupported value");
    /* Recursion may grow the memo, invalidating the original slot pointer. */
    wysna_worker_copy_slot(memo, value.bits)->value = wysna_clone(copied);
    return copied;
}

static inline void wysna_worker_copy_clear(WysnaWorkerCopy* memo) {
    for (size_t i = 0; i < memo->capacity; i++) {
        if (memo->entries[i].key) wysna_release(memo->entries[i].value);
    }
    free(memo->entries);
    memo->entries = NULL;
    memo->capacity = memo->count = 0;
}

static inline WVal wysna_worker_snapshot(WVal value) {
    WysnaWorkerCopy memo = {0};
    WVal copied = wysna_worker_copy_value(&memo, value, 0);
    wysna_worker_copy_clear(&memo);
    return copied;
}

#endif
