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
            wysna_runtime_fail_unlocated("W4003", "Equality work list is too large");
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
    if (a.bits == b.bits && !wysna_is_double(a)) return true;
    WTag ta = wysna_tag(a);
    if (ta == wysna_tag(b)) {
        switch (ta) {
            case W_UNIT:
            case W_NONE: return true;
            case W_INT: return wysna_as_int(a) == wysna_as_int(b);
            case W_FLOAT: return wysna_as_float(a) == wysna_as_float(b);
            case W_BOOL: return a.bits == b.bits;
            case W_STR: {
                WStringBuf* oa = wysna_str_owner(a);
                WStringBuf* ob = wysna_str_owner(b);
                if (oa && ob) {
                    if (oa == ob) return true;
                    uint32_t ha = wysna_rc_load_u32(&oa->hash_cache);
                    uint32_t hb = wysna_rc_load_u32(&ob->hash_cache);
                    if (ha != 0 && hb != 0 && ha != hb) return false;
                }
                char sba[7], sbb[7];
                size_t la = 0, lb = 0;
                const char* da = wysna_str_bytes(a, sba, &la);
                const char* db = wysna_str_bytes(b, sbb, &lb);
                return la == lb && (da == db || memcmp(da, db, la) == 0);
            }
            case W_POINTER: return wysna_as_pointer(a) == wysna_as_pointer(b);
            case W_OBJECT: return wysna_as_struct(a) == wysna_as_struct(b);
            case W_WEAK: return wysna_as_weak(a) == wysna_as_weak(b);
            case W_LIST: if (wysna_as_list(a) == wysna_as_list(b)) return true; break;
            case W_TUPLE: if (wysna_as_tuple(a) == wysna_as_tuple(b)) return true; break;
            case W_STRUCT: if (wysna_as_struct(a) == wysna_as_struct(b)) return true; break;
            case W_DICT: if (wysna_as_dict(a) == wysna_as_dict(b)) return true; break;
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
        if (left.bits == right.bits && !wysna_is_double(left)) continue;
        WTag ltag = wysna_tag(left);
        WTag rtag = wysna_tag(right);
        WEnumBuf* lenum = ltag == W_ENUM ? wysna_as_enum(left) : NULL;
        WEnumBuf* renum = rtag == W_ENUM ? wysna_as_enum(right) : NULL;
        bool left_some = lenum && strcmp(lenum->enum_name, "Option") == 0 && strcmp(lenum->variant, "Some") == 0;
        bool right_some = renum && strcmp(renum->enum_name, "Option") == 0 && strcmp(renum->variant, "Some") == 0;

        if (left_some && !right_some && rtag != W_NONE) {
            if (!lenum->has_payload) { equal = false; continue; }
            wysna_eq_push(&items, &count, &capacity, &heap_items, lenum->payload, right);
            continue;
        }
        if (right_some && !left_some && ltag != W_NONE) {
            if (!renum->has_payload) { equal = false; continue; }
            wysna_eq_push(&items, &count, &capacity, &heap_items, left, renum->payload);
            continue;
        }

        if (ltag != rtag) {
            if ((ltag == W_NONE && renum && strcmp(renum->enum_name, "Option") == 0 && strcmp(renum->variant, "None") == 0) ||
                (rtag == W_NONE && lenum && strcmp(lenum->enum_name, "Option") == 0 && strcmp(lenum->variant, "None") == 0)) {
                continue;
            }
            bool left_result = ltag == W_OK || ltag == W_ERR;
            bool right_result = rtag == W_OK || rtag == W_ERR;
            const char* variant = (ltag == W_OK || rtag == W_OK) ? "Ok" : "Err";
            if (left_result && renum && strcmp(renum->enum_name, "Result") == 0 &&
                (renum->variant == variant || strcmp(renum->variant, variant) == 0) &&
                wysna_as_box(left) && renum->has_payload) {
                wysna_eq_push(&items, &count, &capacity, &heap_items, wysna_as_box(left)->val, renum->payload);
                continue;
            }
            if (right_result && lenum && strcmp(lenum->enum_name, "Result") == 0 &&
                (lenum->variant == variant || strcmp(lenum->variant, variant) == 0) &&
                lenum->has_payload && wysna_as_box(right)) {
                wysna_eq_push(&items, &count, &capacity, &heap_items, lenum->payload, wysna_as_box(right)->val);
                continue;
            }
            equal = false;
            continue;
        }

        switch (ltag) {
            case W_POINTER:
                equal = wysna_as_pointer(left) == wysna_as_pointer(right);
                break;
            case W_FUNCTION:
                equal = wysna_as_function(left) == wysna_as_function(right);
                break;
            case W_UNIT:
            case W_NONE:
                break;
            case W_INT:
                equal = wysna_as_int(left) == wysna_as_int(right);
                break;
            case W_FLOAT:
                equal = wysna_as_float(left) == wysna_as_float(right);
                break;
            case W_BOOL:
                equal = left.bits == right.bits;
                break;
            case W_STR: {
                char sba[7], sbb[7];
                size_t la = 0, lb = 0;
                const char* da = wysna_str_bytes(left, sba, &la);
                const char* db = wysna_str_bytes(right, sbb, &lb);
                equal = la == lb && (da == db || memcmp(da, db, la) == 0);
                break;
            }
            case W_ENUM:
                if (!lenum || !renum ||
                    (lenum->enum_name != renum->enum_name && strcmp(lenum->enum_name, renum->enum_name) != 0) ||
                    (lenum->variant != renum->variant && strcmp(lenum->variant, renum->variant) != 0) ||
                    (lenum->has_payload != renum->has_payload)) {
                    equal = false;
                } else if (lenum->has_payload) {
                    wysna_eq_push(&items, &count, &capacity, &heap_items, lenum->payload, renum->payload);
                }
                break;
            case W_LIST: {
                WListBuf* la = wysna_as_list(left);
                WListBuf* lb = wysna_as_list(right);
                if (la == lb) break;
                if (!la || !lb || la->len != lb->len) { equal = false; break; }
                for (int32_t i = 0; i < la->len; i++) {
                    wysna_eq_push(&items, &count, &capacity, &heap_items,
                                  wysna_list_load(la, i), wysna_list_load(lb, i));
                }
                break;
            }
            case W_TUPLE: {
                WTupleBuf* ta = wysna_as_tuple(left);
                WTupleBuf* tb = wysna_as_tuple(right);
                if (ta == tb) break;
                if (!ta || !tb || ta->len != tb->len) { equal = false; break; }
                for (int32_t i = 0; i < ta->len; i++) {
                    wysna_eq_push(&items, &count, &capacity, &heap_items, ta->items[i], tb->items[i]);
                }
                break;
            }
            case W_DICT: {
                WDictBuf* da = wysna_as_dict(left);
                WDictBuf* db = wysna_as_dict(right);
                if (da == db) break;
                if (!da || !db || da->len != db->len) { equal = false; break; }
                for (int32_t i = 0; i < da->len && equal; i++) {
                    int32_t found = wysna_dict_find(db, da->keys[i]);
                    if (found < 0) equal = false;
                    else wysna_eq_push(&items, &count, &capacity, &heap_items, da->vals[i], db->vals[found]);
                }
                break;
            }
            case W_STRUCT: {
                WStructBuf* sa = wysna_as_struct(left);
                WStructBuf* sb = wysna_as_struct(right);
                if (sa == sb) break;
                if (!sa || !sb || (sa->name != sb->name && strcmp(sa->name, sb->name) != 0) || sa->len != sb->len) {
                    equal = false;
                    break;
                }
                for (int32_t i = 0; i < sa->len && equal; i++) {
                    int32_t found = i;
                    if (sa->keys[i] != sb->keys[i] && strcmp(sa->keys[i], sb->keys[i]) != 0) {
                        found = -1;
                        for (int32_t j = 0; j < sb->len; j++) {
                            if (sa->keys[i] == sb->keys[j] || strcmp(sa->keys[i], sb->keys[j]) == 0) { found = j; break; }
                        }
                    }
                    if (found < 0) equal = false;
                    else wysna_eq_push(&items, &count, &capacity, &heap_items, sa->vals[i], sb->vals[found]);
                }
                break;
            }
            case W_WEAK:
                equal = wysna_as_weak(left) == wysna_as_weak(right);
                break;
            case W_OBJECT:
                equal = wysna_as_struct(left) == wysna_as_struct(right);
                break;
            case W_OK:
            case W_ERR: {
                WBoxBuf* ba = wysna_as_box(left);
                WBoxBuf* bb = wysna_as_box(right);
                if (!ba || !bb) equal = ba == bb;
                else wysna_eq_push(&items, &count, &capacity, &heap_items, ba->val, bb->val);
                break;
            }
        }
    }

    if (heap_items) free(items);
    return equal;
}

static inline bool wysna_val64_eq(WVal64 a, WVal64 b) {
    return wysna_eq(a, b);
}
#endif /* WYSNA_RUNTIME_EQUALITY_H */
