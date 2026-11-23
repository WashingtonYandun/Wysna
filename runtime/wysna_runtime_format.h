/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_FORMAT_H
#define WYSNA_RUNTIME_FORMAT_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
#endif

/* Formatting & String Conversion (matching Wysna Seed Values.__str__ exactly) */
/* Internal module identity is retained for equality/dispatch, not display. */
typedef struct { const char* data; size_t length; } WNominalName;
static inline WNominalName wysna_nominal_display_name(const char* name) {
    const char* start = name;
    size_t length = strlen(name);
    static const char generic[] = "_WYSNA_G_";
    size_t prefix = sizeof(generic) - 1;
    if (length > prefix && memcmp(name, generic, prefix) == 0) {
        size_t cursor = prefix, characters = 0;
        bool digits = false, valid = true;
        while (cursor < length && name[cursor] >= '0' && name[cursor] <= '9') {
            size_t digit = (size_t)(name[cursor++] - '0');
            digits = true;
            if (characters > (SIZE_MAX - digit) / 10) { valid = false; break; }
            characters = characters * 10 + digit;
        }
        if (valid && digits && cursor < length && name[cursor] == '_') {
            size_t begin = ++cursor, count = 0;
            while (cursor < length && count < characters) {
                size_t width;
                if (!wysna_utf8_valid_codepoint_at_n(name, length, cursor, &width, NULL)) break;
                cursor += width; count++;
            }
            if (count == characters && cursor + 2 <= length && name[cursor] == '_' && name[cursor + 1] == '_') {
                start = name + begin; length = cursor - begin;
            }
        }
    }
    static const char module[] = "__wysna_module_";
    prefix = sizeof(module) - 1;
    if (length >= prefix && memcmp(start, module, prefix) == 0) {
        for (size_t index = prefix; index + 1 < length; index++) {
            if (start[index] == '_' && start[index + 1] == '_') {
                length -= index + 2; start += index + 2; break;
            }
        }
    }
    WNominalName result = {start, length}; return result;
}

typedef struct {
    WVal value;
    const char* text;
    bool is_text;
    bool quote_str;
    bool leave_object;
    size_t text_length;
} WFormatTask;

static inline void wysna_format_push(WFormatTask** tasks, int32_t* count, int32_t* capacity,
                                 bool* heap_tasks, WFormatTask task) {
    if (*count == INT32_MAX) {
        wysna_runtime_fail("W4003", "Formatting work list is too large");
    }
    if (*count == *capacity) {
        if (*capacity > INT32_MAX / 2) {
            wysna_runtime_fail("W4003", "Formatting work list is too large");
        }
        int32_t new_capacity = *capacity * 2;
        WFormatTask* grown = (WFormatTask*)wysna_alloc_safe(
            sizeof(WFormatTask) * (size_t)new_capacity);
        memcpy(grown, *tasks, sizeof(WFormatTask) * (size_t)*count);
        if (*heap_tasks) free(*tasks);
        *tasks = grown;
        *capacity = new_capacity;
        *heap_tasks = true;
    }
    (*tasks)[(*count)++] = task;
}

static inline void wysna_format_push_text(WFormatTask** tasks, int32_t* count, int32_t* capacity,
                                      bool* heap_tasks, const char* text) {
    WVal empty = wysna_unit();
    WFormatTask task = {empty, text, true, false, false, strlen(text)};
    wysna_format_push(tasks, count, capacity, heap_tasks, task);
}

static inline void wysna_format_push_name(WFormatTask** tasks, int32_t* count, int32_t* capacity,
                                          bool* heap_tasks, const char* name) {
    WNominalName display = wysna_nominal_display_name(name);
    WVal empty = wysna_unit();
    WFormatTask task = {empty, display.data, true, false, false, display.length};
    wysna_format_push(tasks, count, capacity, heap_tasks, task);
}

static inline void wysna_format_push_value(WFormatTask** tasks, int32_t* count, int32_t* capacity,
                                       bool* heap_tasks, WVal value, bool quote_str) {
    WFormatTask task = {value, NULL, false, quote_str, false, 0};
    wysna_format_push(tasks, count, capacity, heap_tasks, task);
}

static inline void wysna_format_append(char** buffer, size_t* length, size_t* capacity,
                                   const char* data, size_t data_length) {
    if (data_length > SIZE_MAX - *length - 1) {
        wysna_runtime_fail("W4003", "Formatted value is too large");
    }
    size_t needed = *length + data_length + 1;
    if (needed > *capacity) {
        size_t new_capacity = *capacity;
        while (new_capacity < needed) {
            if (new_capacity > SIZE_MAX / 2) {
                new_capacity = needed;
                break;
            }
            new_capacity *= 2;
        }
        *buffer = (char*)wysna_realloc_safe(*buffer, new_capacity);
        *capacity = new_capacity;
    }
    if (data_length > 0) memcpy(*buffer + *length, data, data_length);
    *length += data_length;
    (*buffer)[*length] = '\0';
}

static inline void wysna_format_append_text(char** buffer, size_t* length, size_t* capacity,
                                        const char* text) {
    wysna_format_append(buffer, length, capacity, text, strlen(text));
}

/* Active ancestors only: repeated aliases outside the current path render fully. */
typedef struct {
    WStructBuf** entries;
    size_t capacity;
    size_t count;
    size_t used;
} WFormatObjects;
#define W_FORMAT_DELETED ((WStructBuf*)(uintptr_t)1)

static inline size_t wysna_format_object_hash(WStructBuf* object) {
    uintptr_t value = (uintptr_t)object >> 3;
    value ^= value >> 17;
    return (size_t)value;
}

static inline void wysna_format_objects_resize(WFormatObjects* set, size_t capacity) {
    if (capacity > SIZE_MAX / sizeof(WStructBuf*))
        wysna_runtime_fail("W4003", "Formatting ancestor set is too large");
    WStructBuf** entries = (WStructBuf**)wysna_alloc_safe(capacity * sizeof(WStructBuf*));
    memset(entries, 0, capacity * sizeof(WStructBuf*));
    for (size_t index = 0; index < set->capacity; index++) {
        WStructBuf* object = set->entries[index];
        if (!object || object == W_FORMAT_DELETED) continue;
        size_t position = wysna_format_object_hash(object) & (capacity - 1);
        while (entries[position]) position = (position + 1) & (capacity - 1);
        entries[position] = object;
    }
    free(set->entries);
    set->entries = entries;
    set->capacity = capacity;
    set->used = set->count;
}

static inline bool wysna_format_object_enter(WFormatObjects* set, WStructBuf* object) {
    if (!set->capacity) wysna_format_objects_resize(set, 64);
    if (set->used >= set->capacity - set->capacity / 4) {
        size_t capacity = set->capacity;
        if (set->count >= capacity / 4) {
            if (capacity > SIZE_MAX / 2) wysna_runtime_fail("W4003", "Formatting ancestor set is too large");
            capacity *= 2;
        }
        wysna_format_objects_resize(set, capacity);
    }
    size_t position = wysna_format_object_hash(object) & (set->capacity - 1);
    size_t deleted = SIZE_MAX;
    while (set->entries[position]) {
        if (set->entries[position] == object) return false;
        if (set->entries[position] == W_FORMAT_DELETED && deleted == SIZE_MAX) deleted = position;
        position = (position + 1) & (set->capacity - 1);
    }
    if (deleted != SIZE_MAX) position = deleted;
    else set->used++;
    set->entries[position] = object;
    set->count++;
    return true;
}

static inline void wysna_format_object_leave(WFormatObjects* set, WStructBuf* object) {
    size_t position = wysna_format_object_hash(object) & (set->capacity - 1);
    while (set->entries[position] != object) position = (position + 1) & (set->capacity - 1);
    set->entries[position] = W_FORMAT_DELETED;
    set->count--;
}

static inline char* wysna_to_cstr(WVal v, bool quote_str) {
    WFormatObjects active_objects = {0};
    WFormatTask local_tasks[64];
    WFormatTask* tasks = local_tasks;
    int32_t task_count = 0;
    int32_t task_capacity = 64;
    bool heap_tasks = false;
    size_t buffer_capacity = 64;
    size_t buffer_length = 0;
    char* buffer = (char*)wysna_alloc_safe(buffer_capacity);
    buffer[0] = '\0';
    wysna_format_push_value(&tasks, &task_count, &task_capacity, &heap_tasks, v, quote_str);

    while (task_count > 0) {
        WFormatTask task = tasks[--task_count];
        if (task.leave_object) {
            wysna_format_object_leave(&active_objects, wysna_as_struct(task.value));
            continue;
        }
        if (task.is_text) {
            wysna_format_append(&buffer, &buffer_length, &buffer_capacity, task.text, task.text_length);
            continue;
        }

        WVal value = task.value;
        WTag vtag = wysna_tag(value);
        switch (vtag) {
            case W_POINTER:
                wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, "<opaque>");
                break;
            case W_FUNCTION:
                wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, "<function>");
                break;
            case W_WEAK: {
                WWeakControl* wk = wysna_as_weak(value);
                wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity,
                    (wk && wk->object) ? "weak(alive)" : "weak(expired)");
                break;
            }
            case W_UNIT:
                wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, "unit");
                break;
            case W_NONE:
                wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, "none");
                break;
            case W_INT: {
                char number[32];
                snprintf(number, sizeof(number), "%" PRId64, wysna_as_int(value));
                wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, number);
                break;
            }
            case W_FLOAT: {
                char* number = wysna_format_float(wysna_as_float(value));
                wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, number);
                free(number);
                break;
            }
            case W_BOOL:
                wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity,
                                     wysna_as_bool(value) ? "true" : "false");
                break;
            case W_STR: {
                char sb[7];
                size_t slen = 0;
                const char* sdata = wysna_str_bytes(value, sb, &slen);
                if (!task.quote_str) {
                    wysna_format_append(&buffer, &buffer_length, &buffer_capacity, sdata, slen);
                } else {
                    wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, "\"");
                    for (size_t i = 0; i < slen; i++) {
                        if (sdata[i] == 0) {
                            wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, "\\0");
                        } else {
                            wysna_format_append(&buffer, &buffer_length, &buffer_capacity, sdata + i, 1);
                        }
                    }
                    wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, "\"");
                }
                break;
            }
            case W_ENUM: {
                WEnumBuf* en = wysna_as_enum(value);
                if (!en) break;
                if (strcmp(en->enum_name, "Option") == 0 &&
                    strcmp(en->variant, "Some") == 0 && en->has_payload) {
                    wysna_format_push_value(&tasks, &task_count, &task_capacity, &heap_tasks,
                                        en->payload, task.quote_str);
                } else if (en->has_payload && wysna_tag(en->payload) == W_TUPLE) {
                    WTupleBuf* payload = wysna_as_tuple(en->payload);
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, ")");
                    for (int32_t i = payload->len - 1; i >= 0; i--) {
                        wysna_format_push_value(&tasks, &task_count, &task_capacity, &heap_tasks,
                                            payload->items[i], true);
                        if (i > 0) wysna_format_push_text(&tasks, &task_count, &task_capacity,
                                                      &heap_tasks, ", ");
                    }
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, "(");
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, en->variant);
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, ".");
                    wysna_format_push_name(&tasks, &task_count, &task_capacity, &heap_tasks, en->enum_name);
                } else if (en->has_payload) {
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, ")");
                    wysna_format_push_value(&tasks, &task_count, &task_capacity, &heap_tasks,
                                        en->payload, true);
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, "(");
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, en->variant);
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, ".");
                    wysna_format_push_name(&tasks, &task_count, &task_capacity, &heap_tasks, en->enum_name);
                } else {
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, en->variant);
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, ".");
                    wysna_format_push_name(&tasks, &task_count, &task_capacity, &heap_tasks, en->enum_name);
                }
                break;
            }
            case W_LIST: {
                WListBuf* list = wysna_as_list(value);
                wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, "]");
                for (int32_t i = list->len - 1; i >= 0; i--) {
                    wysna_format_push_value(&tasks, &task_count, &task_capacity, &heap_tasks,
                                        list->data[i], true);
                    if (i > 0) wysna_format_push_text(&tasks, &task_count, &task_capacity,
                                                  &heap_tasks, ", ");
                }
                wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, "[");
                break;
            }
            case W_TUPLE: {
                WTupleBuf* tuple = wysna_as_tuple(value);
                wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, ")");
                for (int32_t i = tuple->len - 1; i >= 0; i--) {
                    wysna_format_push_value(&tasks, &task_count, &task_capacity, &heap_tasks,
                                        tuple->items[i], true);
                    if (i > 0) wysna_format_push_text(&tasks, &task_count, &task_capacity,
                                                  &heap_tasks, ", ");
                }
                wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, "(");
                break;
            }
            case W_DICT: {
                WDictBuf* dict = wysna_as_dict(value);
                wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, "}");
                for (int32_t i = dict->len - 1; i >= 0; i--) {
                    wysna_format_push_value(&tasks, &task_count, &task_capacity, &heap_tasks,
                                        dict->vals[i], true);
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, ": ");
                    wysna_format_push_value(&tasks, &task_count, &task_capacity, &heap_tasks,
                                        dict->keys[i], true);
                    if (i > 0) wysna_format_push_text(&tasks, &task_count, &task_capacity,
                                                  &heap_tasks, ", ");
                }
                wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, "{");
                break;
            }
            case W_STRUCT:
            case W_OBJECT: {
                WStructBuf* structure = wysna_as_struct(value);
                if (vtag == W_OBJECT) {
                    if (!wysna_format_object_enter(&active_objects, structure)) {
                        wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, "<cycle>");
                        break;
                    }
                    WFormatTask leave = {value, NULL, false, false, true, 0};
                    wysna_format_push(&tasks, &task_count, &task_capacity, &heap_tasks, leave);
                }
                wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, " }");
                for (int32_t i = structure->len - 1; i >= 0; i--) {
                    wysna_format_push_value(&tasks, &task_count, &task_capacity, &heap_tasks,
                                        structure->vals[i], true);
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, ": ");
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks,
                                       structure->keys[i]);
                    if (i > 0) wysna_format_push_text(&tasks, &task_count, &task_capacity,
                                                  &heap_tasks, ", ");
                }
                wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, " { ");
                wysna_format_push_name(&tasks, &task_count, &task_capacity, &heap_tasks, structure->name);
                break;
            }
            case W_OK:
            case W_ERR: {
                WBoxBuf* bx = wysna_as_box(value);
                wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, ")");
                if (bx) {
                    wysna_format_push_value(&tasks, &task_count, &task_capacity, &heap_tasks,
                                        bx->val, false);
                }
                wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks,
                                   vtag == W_OK ? "Ok(" : "Err(");
                break;
            }
        }
    }

    if (heap_tasks) free(tasks);
    free(active_objects.entries);
    return buffer;
}
#endif /* WYSNA_RUNTIME_FORMAT_H */
