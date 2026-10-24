/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_EQUALITY_H
#define WYSNA_RUNTIME_EQUALITY_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
#endif

/* Structural Equality */
typedef struct {
    WVal left;
    WVal right;
} WEqualityPair;

static inline void wysna_eq_push(WEqualityPair** items, int32_t* count, int32_t* capacity,
                             bool* heap_items, WVal left, WVal right) {
    if (*count == *capacity) {
        if (*capacity > INT32_MAX / 2) {
            fprintf(stderr, "[W4003] RuntimeError: Equality work list is too large\n");
            exit(1);
        }
        int32_t new_capacity = *capacity * 2;
        WEqualityPair* grown = (WEqualityPair*)wysna_alloc_safe(
            sizeof(WEqualityPair) * (size_t)new_capacity);
        memcpy(grown, *items, sizeof(WEqualityPair) * (size_t)*count);
        if (*heap_items) free(*items);
        *items = grown;
        *capacity = new_capacity;
        *heap_items = true;
    }
    (*items)[(*count)++] = (WEqualityPair){left, right};
}

static W_RUNTIME_NOINLINE bool wysna_eq_graph(WVal a, WVal b);

/* Scalar equality must not create the structural comparison work list. */
static inline bool wysna_eq(WVal a, WVal b) {
    if (a.tag == b.tag) {
        switch (a.tag) {
            case W_UNIT:
            case W_NONE: return true;
            case W_INT: return a.as.i == b.as.i;
            case W_FLOAT: return a.as.f == b.as.f;
            case W_BOOL: return a.as.b == b.as.b;
            case W_STR: return a.as.string.length == b.as.string.length &&
                (a.as.string.data == b.as.string.data || memcmp(a.as.string.data, b.as.string.data, a.as.string.length) == 0);
            case W_POINTER: return a.as.pointer == b.as.pointer;
            case W_OBJECT: return a.as.st == b.as.st;
            case W_WEAK: return a.as.weak == b.as.weak;
            default: break;
        }
    }
    return wysna_eq_graph(a, b);
}

static bool wysna_eq_graph(WVal a, WVal b) {
    WEqualityPair local_items[64];
    WEqualityPair* items = local_items;
    int32_t count = 0;
    int32_t capacity = 64;
    bool heap_items = false;
    bool equal = true;
    wysna_eq_push(&items, &count, &capacity, &heap_items, a, b);

    while (count > 0 && equal) {
        WEqualityPair pair = items[--count];
        WVal left = pair.left;
        WVal right = pair.right;
        bool left_some = left.tag == W_ENUM &&
            strcmp(left.as.en.enum_name, "Option") == 0 &&
            strcmp(left.as.en.variant, "Some") == 0;
        bool right_some = right.tag == W_ENUM &&
            strcmp(right.as.en.enum_name, "Option") == 0 &&
            strcmp(right.as.en.variant, "Some") == 0;

        if (left_some && !right_some && right.tag != W_NONE) {
            if (!left.as.en.box) { equal = false; continue; }
            wysna_eq_push(&items, &count, &capacity, &heap_items,
                      left.as.en.box->val, right);
            continue;
        }
        if (right_some && !left_some && left.tag != W_NONE) {
            if (!right.as.en.box) { equal = false; continue; }
            wysna_eq_push(&items, &count, &capacity, &heap_items,
                      left, right.as.en.box->val);
            continue;
        }

        if (left.tag != right.tag) {
            if ((left.tag == W_NONE && right.tag == W_ENUM &&
                 strcmp(right.as.en.enum_name, "Option") == 0 &&
                 strcmp(right.as.en.variant, "None") == 0) ||
                (right.tag == W_NONE && left.tag == W_ENUM &&
                 strcmp(left.as.en.enum_name, "Option") == 0 &&
                 strcmp(left.as.en.variant, "None") == 0)) {
                continue;
            }
            bool left_result = left.tag == W_OK || left.tag == W_ERR;
            bool right_result = right.tag == W_OK || right.tag == W_ERR;
            const char* variant = (left.tag == W_OK || right.tag == W_OK) ? "Ok" : "Err";
            if (left_result && right.tag == W_ENUM &&
                strcmp(right.as.en.enum_name, "Result") == 0 &&
                (right.as.en.variant == variant || strcmp(right.as.en.variant, variant) == 0) &&
                left.as.box && right.as.en.box) {
                wysna_eq_push(&items, &count, &capacity, &heap_items,
                          left.as.box->val, right.as.en.box->val);
                continue;
            }
            if (right_result && left.tag == W_ENUM &&
                strcmp(left.as.en.enum_name, "Result") == 0 &&
                (left.as.en.variant == variant || strcmp(left.as.en.variant, variant) == 0) &&
                left.as.en.box && right.as.box) {
                wysna_eq_push(&items, &count, &capacity, &heap_items,
                          left.as.en.box->val, right.as.box->val);
                continue;
            }
            equal = false;
            continue;
        }

        switch (left.tag) {
            case W_POINTER:
                equal = left.as.pointer == right.as.pointer;
                break;
            case W_FUNCTION:
                equal = left.as.function == right.as.function;
                break;
            case W_UNIT:
            case W_NONE:
                break;
            case W_INT:
                equal = left.as.i == right.as.i;
                break;
            case W_FLOAT:
                equal = left.as.f == right.as.f;
                break;
            case W_BOOL:
                equal = left.as.b == right.as.b;
                break;
            case W_STR:
                equal = left.as.string.length == right.as.string.length &&
                    (left.as.string.data == right.as.string.data || memcmp(left.as.string.data, right.as.string.data, left.as.string.length) == 0);
                break;
            case W_ENUM:
                if ((left.as.en.enum_name != right.as.en.enum_name && strcmp(left.as.en.enum_name, right.as.en.enum_name) != 0) ||
                    (left.as.en.variant != right.as.en.variant && strcmp(left.as.en.variant, right.as.en.variant) != 0) ||
                    (!!left.as.en.box != !!right.as.en.box)) {
                    equal = false;
                } else if (left.as.en.box) {
                    wysna_eq_push(&items, &count, &capacity, &heap_items,
                              left.as.en.box->val, right.as.en.box->val);
                }
                break;
            case W_LIST:
                if (left.as.list->len != right.as.list->len) {
                    equal = false;
                    break;
                }
                for (int32_t i = 0; i < left.as.list->len; i++) {
                    wysna_eq_push(&items, &count, &capacity, &heap_items,
                              left.as.list->data[i], right.as.list->data[i]);
                }
                break;
            case W_TUPLE:
                if (left.as.tuple->len != right.as.tuple->len) {
                    equal = false;
                    break;
                }
                for (int32_t i = 0; i < left.as.tuple->len; i++) {
                    wysna_eq_push(&items, &count, &capacity, &heap_items,
                              left.as.tuple->items[i], right.as.tuple->items[i]);
                }
                break;
            case W_DICT:
                if (left.as.dict->len != right.as.dict->len) {
                    equal = false;
                    break;
                }
                for (int32_t i = 0; i < left.as.dict->len && equal; i++) {
                    int32_t found = wysna_dict_find(right.as.dict, left.as.dict->keys[i]);
                    if (found < 0) {
                        equal = false;
                    } else {
                        wysna_eq_push(&items, &count, &capacity, &heap_items,
                                  left.as.dict->vals[i], right.as.dict->vals[found]);
                    }
                }
                break;
            case W_STRUCT:
                if ((left.as.st->name != right.as.st->name && strcmp(left.as.st->name, right.as.st->name) != 0) ||
                    left.as.st->len != right.as.st->len) {
                    equal = false;
                    break;
                }
                for (int32_t i = 0; i < left.as.st->len && equal; i++) {
                    int32_t found = i;
                    if (left.as.st->keys[i] != right.as.st->keys[i] && strcmp(left.as.st->keys[i], right.as.st->keys[i]) != 0) {
                        found = -1;
                        for (int32_t j = 0; j < right.as.st->len; j++) {
                            if (left.as.st->keys[i] == right.as.st->keys[j] || strcmp(left.as.st->keys[i], right.as.st->keys[j]) == 0) { found = j; break; }
                        }
                    }
                    if (found < 0) equal = false;
                    else wysna_eq_push(&items, &count, &capacity, &heap_items,
                                   left.as.st->vals[i], right.as.st->vals[found]);
                }
                break;
            case W_WEAK:
                equal = left.as.weak == right.as.weak;
                break;
            case W_OBJECT:
                equal = left.as.st == right.as.st;
                break;
            case W_OK:
            case W_ERR:
                if (!left.as.box || !right.as.box) {
                    equal = left.as.box == right.as.box;
                } else {
                    wysna_eq_push(&items, &count, &capacity, &heap_items,
                              left.as.box->val, right.as.box->val);
                }
                break;
        }
    }

    if (heap_items) free(items);
    return equal;
}
#endif /* WYSNA_RUNTIME_EQUALITY_H */
