/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_FORMAT_H
#define WYSNA_RUNTIME_FORMAT_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
#endif

/* Formatting & String Conversion (matching Wysna Seed Values.__str__ exactly) */
/* Internal module identity is retained for equality/dispatch, not display. */
static inline const char* wysna_nominal_display_name(const char* name) {
    static const char module_prefix[] = "__wysna_module_";
    const size_t module_prefix_len = sizeof(module_prefix) - 1;
    if (strncmp(name, module_prefix, module_prefix_len) == 0) {
        const char* boundary = strstr(name + module_prefix_len, "__");
        if (boundary) return boundary + 2;
    }
    return name;
}

typedef struct {
    WVal value;
    const char* text;
    bool is_text;
    bool quote_str;
    bool leave_object;
} WFormatTask;

static inline void wysna_format_push(WFormatTask** tasks, int32_t* count, int32_t* capacity,
                                 bool* heap_tasks, WFormatTask task) {
    if (*count == INT32_MAX) {
        fprintf(stderr, "[W4003] RuntimeError: Formatting work list is too large\n");
        exit(1);
    }
    if (*count == *capacity) {
        if (*capacity > INT32_MAX / 2) {
            fprintf(stderr, "[W4003] RuntimeError: Formatting work list is too large\n");
            exit(1);
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
    WVal empty = {0};
    empty.tag = W_UNIT;
    WFormatTask task = {empty, text, true, false, false};
    wysna_format_push(tasks, count, capacity, heap_tasks, task);
}

static inline void wysna_format_push_value(WFormatTask** tasks, int32_t* count, int32_t* capacity,
                                       bool* heap_tasks, WVal value, bool quote_str) {
    WFormatTask task = {value, NULL, false, quote_str, false};
    wysna_format_push(tasks, count, capacity, heap_tasks, task);
}

static inline void wysna_format_append(char** buffer, size_t* length, size_t* capacity,
                                   const char* data, size_t data_length) {
    if (data_length > SIZE_MAX - *length - 1) {
        fprintf(stderr, "[W4003] RuntimeError: Formatted value is too large\n");
        exit(1);
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
            wysna_format_object_leave(&active_objects, task.value.as.st);
            continue;
        }
        if (task.is_text) {
            wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, task.text);
            continue;
        }

        WVal value = task.value;
        switch (value.tag) {
            case W_POINTER:
                wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, "<opaque>");
                break;
            case W_FUNCTION:
                wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, "<function>");
                break;
            case W_WEAK:
                wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity,
                    value.as.weak->object ? "weak(alive)" : "weak(expired)");
                break;
            case W_UNIT:
                wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, "unit");
                break;
            case W_NONE:
                wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, "none");
                break;
            case W_INT: {
                char number[32];
                snprintf(number, sizeof(number), "%" PRId64, value.as.i);
                wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, number);
                break;
            }
            case W_FLOAT: {
                char* number = wysna_format_float(value.as.f);
                wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, number);
                free(number);
                break;
            }
            case W_BOOL:
                wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity,
                                     value.as.b ? "true" : "false");
                break;
            case W_STR:
                if (!task.quote_str) {
                    wysna_format_append(&buffer, &buffer_length, &buffer_capacity,
                                    value.as.string.data, value.as.string.length);
                } else {
                    wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, "\"");
                    for (size_t i = 0; i < value.as.string.length; i++) {
                        if (value.as.string.data[i] == 0) {
                            wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, "\\0");
                        } else {
                            wysna_format_append(&buffer, &buffer_length, &buffer_capacity,
                                            value.as.string.data + i, 1);
                        }
                    }
                    wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, "\"");
                }
                break;
            case W_ENUM:
                if (strcmp(value.as.en.enum_name, "Option") == 0 &&
                    strcmp(value.as.en.variant, "Some") == 0 && value.as.en.box) {
                    wysna_format_push_value(&tasks, &task_count, &task_capacity, &heap_tasks,
                                        value.as.en.box->val, task.quote_str);
                } else if (value.as.en.box && value.as.en.box->val.tag == W_TUPLE) {
                    WTupleBuf* payload = value.as.en.box->val.as.tuple;
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, ")");
                    for (int32_t i = payload->len - 1; i >= 0; i--) {
                        wysna_format_push_value(&tasks, &task_count, &task_capacity, &heap_tasks,
                                            payload->items[i], true);
                        if (i > 0) wysna_format_push_text(&tasks, &task_count, &task_capacity,
                                                      &heap_tasks, ", ");
                    }
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, "(");
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks,
                                       value.as.en.variant);
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, ".");
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks,
                                       wysna_nominal_display_name(value.as.en.enum_name));
                } else if (value.as.en.box) {
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, ")");
                    wysna_format_push_value(&tasks, &task_count, &task_capacity, &heap_tasks,
                                        value.as.en.box->val, true);
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, "(");
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks,
                                       value.as.en.variant);
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, ".");
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks,
                                       wysna_nominal_display_name(value.as.en.enum_name));
                } else {
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks,
                                       value.as.en.variant);
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, ".");
                    wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks,
                                       wysna_nominal_display_name(value.as.en.enum_name));
                }
                break;
            case W_LIST: {
                WListBuf* list = value.as.list;
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
                WTupleBuf* tuple = value.as.tuple;
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
                WDictBuf* dict = value.as.dict;
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
                WStructBuf* structure = value.as.st;
                if (value.tag == W_OBJECT) {
                    if (!wysna_format_object_enter(&active_objects, structure)) {
                        wysna_format_append_text(&buffer, &buffer_length, &buffer_capacity, "<cycle>");
                        break;
                    }
                    WFormatTask leave = {value, NULL, false, false, true};
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
                wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks,
                                   wysna_nominal_display_name(structure->name));
                break;
            }
            case W_OK:
            case W_ERR:
                wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks, ")");
                if (value.as.box) {
                    wysna_format_push_value(&tasks, &task_count, &task_capacity, &heap_tasks,
                                        value.as.box->val, false);
                }
                wysna_format_push_text(&tasks, &task_count, &task_capacity, &heap_tasks,
                                   value.tag == W_OK ? "Ok(" : "Err(");
                break;
        }
    }

    if (heap_tasks) free(tasks);
    free(active_objects.entries);
    return buffer;
}
#endif /* WYSNA_RUNTIME_FORMAT_H */
