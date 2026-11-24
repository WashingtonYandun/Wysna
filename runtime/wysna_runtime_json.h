/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_JSON_H
#define WYSNA_RUNTIME_JSON_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
#endif

typedef struct {
    const unsigned char* base;
    const unsigned char* cursor;
    const unsigned char* end;
    int32_t depth;
    int32_t max_depth;
    int32_t node_count;
    int32_t max_nodes;
    const char* enum_name;
    WVal proto_null;
    WVal val_true;
    WVal val_false;
    WVal proto_err;
    bool failed;
    size_t err_byte_pos;
    char err_msg[128];
} WysnaJsonCtx;

static inline int64_t wysna_json_cp_offset(const unsigned char* base, size_t byte_pos) {
    int64_t cp = 0;
    size_t pos = 0;
    const char* s = (const char*)base;
    while (pos < byte_pos) {
        size_t step = 0;
        if (wysna_utf8_valid_codepoint_at_n(s, byte_pos, pos, &step, NULL)) {
            pos += step;
        } else {
            pos++;
        }
        cp++;
    }
    return cp;
}

static inline const unsigned char* wysna_json_step_next(const unsigned char* p, const unsigned char* end) {
    if (p >= end) return end;
    if (*p < 0x80u) return p + 1;
    size_t step = 0;
    if (!wysna_utf8_valid_codepoint_at_n((const char*)p, (size_t)(end - p), 0, &step, NULL)) {
        return p + 1;
    }
    return p + step;
}

static inline void wysna_json_fail(WysnaJsonCtx* ctx, const unsigned char* pos, const char* msg) {
    if (ctx->failed) return;
    ctx->failed = true;
    ctx->err_byte_pos = (pos >= ctx->base) ? (size_t)(pos - ctx->base) : 0;
    if (ctx->end && ctx->err_byte_pos > (size_t)(ctx->end - ctx->base)) {
        ctx->err_byte_pos = (size_t)(ctx->end - ctx->base);
    }
    size_t mlen = strlen(msg);
    if (mlen >= sizeof(ctx->err_msg)) mlen = sizeof(ctx->err_msg) - 1;
    memcpy(ctx->err_msg, msg, mlen);
    ctx->err_msg[mlen] = '\0';
}

static inline WVal wysna_json_make_error(WVal proto_err, int64_t offset, const char* msg) {
    WStructBuf* err_st = wysna_as_struct(proto_err);
    const char* err_name = err_st && err_st->name ? err_st->name : "JsonError";
    const char* keys[2] = {"offset", "message"};
    WVal msg_val = wysna_copy_str(msg);
    WVal off_val = wysna_int(offset);
    WVal vals[2] = {off_val, msg_val};
    if (err_st && err_st->len == 2 && err_st->keys && err_st->keys[0] && err_st->keys[1]) {
        keys[0] = err_st->keys[0];
        keys[1] = err_st->keys[1];
        if (strcmp(keys[0], "message") == 0 && strcmp(keys[1], "offset") == 0) {
            vals[0] = msg_val;
            vals[1] = off_val;
        }
    }
    WVal err_val = wysna_make_struct(err_name, 2, keys, vals);
    wysna_release(msg_val);
    return err_val;
}

static inline void wysna_json_skip_space(WysnaJsonCtx* ctx) {
    const unsigned char* p = ctx->cursor;
    const unsigned char* end = ctx->end;
    while (p < end) {
        unsigned char c = *p;
        if (c == ' ' || c == '\t' || c == '\r' || c == '\n') {
            p++;
        } else {
            break;
        }
    }
    ctx->cursor = p;
}

static inline bool wysna_json_reserve_node(WysnaJsonCtx* ctx) {
    if (ctx->node_count >= ctx->max_nodes) {
        wysna_json_fail(ctx, ctx->cursor, "maximum JSON value node limit exceeded");
        return false;
    }
    ctx->node_count++;
    return true;
}

static inline int32_t wysna_json_hex_digit(unsigned char c) {
    if (c >= '0' && c <= '9') return (int32_t)(c - '0');
    if (c >= 'A' && c <= 'F') return (int32_t)(c - 'A' + 10);
    if (c >= 'a' && c <= 'f') return (int32_t)(c - 'a' + 10);
    return -1;
}

static inline int32_t wysna_json_read_hex4(WysnaJsonCtx* ctx, const unsigned char** pp) {
    const unsigned char* p = *pp;
    const unsigned char* end = ctx->end;
    int32_t value = 0;
    for (int i = 0; i < 4; i++) {
        if (p >= end) {
            *pp = end;
            wysna_json_fail(ctx, end, "expected four hexadecimal digits");
            return -1;
        }
        unsigned char ch = *p++;
        int32_t d = wysna_json_hex_digit(ch);
        if (d < 0) {
            *pp = p;
            wysna_json_fail(ctx, p, "expected four hexadecimal digits");
            return -1;
        }
        value = (value << 4) | d;
    }
    *pp = p;
    return value;
}

static inline void wysna_json_buf_append_bytes(char** buf, size_t* len, size_t* cap, const char* src, size_t count) {
    if (*len + count + 1 > *cap) {
        size_t new_cap = (*cap == 0) ? 64 : *cap * 2;
        while (new_cap < *len + count + 1) new_cap *= 2;
        *buf = (char*)wysna_realloc_safe(*buf, new_cap);
        *cap = new_cap;
    }
    if (count > 0) memcpy(*buf + *len, src, count);
    *len += count;
}

static inline void wysna_json_buf_append_utf8(char** buf, size_t* len, size_t* cap, uint32_t cp) {
    char tmp[4];
    size_t n = 0;
    if (cp <= 0x7Fu) {
        tmp[0] = (char)cp;
        n = 1;
    } else if (cp <= 0x7FFu) {
        tmp[0] = (char)(0xC0u | (cp >> 6));
        tmp[1] = (char)(0x80u | (cp & 0x3Fu));
        n = 2;
    } else if (cp <= 0xFFFFu) {
        tmp[0] = (char)(0xE0u | (cp >> 12));
        tmp[1] = (char)(0x80u | ((cp >> 6) & 0x3Fu));
        tmp[2] = (char)(0x80u | (cp & 0x3Fu));
        n = 3;
    } else {
        tmp[0] = (char)(0xF0u | (cp >> 18));
        tmp[1] = (char)(0x80u | ((cp >> 12) & 0x3Fu));
        tmp[2] = (char)(0x80u | ((cp >> 6) & 0x3Fu));
        tmp[3] = (char)(0x80u | (cp & 0x3Fu));
        n = 4;
    }
    wysna_json_buf_append_bytes(buf, len, cap, tmp, n);
}

static inline WVal wysna_json_read_string(WysnaJsonCtx* ctx) {
    const unsigned char* p = ctx->cursor;
    const unsigned char* end = ctx->end;
    if (p >= end || *p != '"') {
        if (p < end) p++;
        wysna_json_fail(ctx, p, "expected string");
        return wysna_unit();
    }
    p++;
    const unsigned char* span_start = p;
    while (p < end) {
        unsigned char c = *p;
        if (c == '"') {
            size_t slen = (size_t)(p - span_start);
            ctx->cursor = p + 1;
            return wysna_copy_str_n((const char*)span_start, slen);
        }
        if (c == '\\') break;
        if (c < 0x20u) {
            wysna_json_fail(ctx, p + 1, "unescaped control character in string");
            return wysna_unit();
        }
        p++;
    }
    if (p >= end) {
        wysna_json_fail(ctx, end, "unterminated string");
        return wysna_unit();
    }

    size_t prefix_len = (size_t)(p - span_start);
    size_t cap = prefix_len + 32;
    if (cap < 64) cap = 64;
    char* buf = (char*)wysna_alloc_safe(cap);
    size_t blen = 0;
    if (prefix_len > 0) {
        memcpy(buf, span_start, prefix_len);
        blen = prefix_len;
    }

    while (p < end && *p != '"') {
        const unsigned char* chunk_start = p;
        while (p < end && *p != '"' && *p != '\\' && *p >= 0x20u) {
            p++;
        }
        if (p > chunk_start) {
            wysna_json_buf_append_bytes(&buf, &blen, &cap, (const char*)chunk_start, (size_t)(p - chunk_start));
        }
        if (p >= end || *p == '"') break;
        unsigned char ch = *p++;
        if (ch < 0x20u) {
            free(buf);
            wysna_json_fail(ctx, p, "unescaped control character in string");
            return wysna_unit();
        }
        /* ch == '\\' */
        if (p >= end) {
            free(buf);
            wysna_json_fail(ctx, end, "invalid string escape");
            return wysna_unit();
        }
        unsigned char esc = *p++;
        switch (esc) {
            case '"':  wysna_json_buf_append_bytes(&buf, &blen, &cap, "\"", 1); break;
            case '\\': wysna_json_buf_append_bytes(&buf, &blen, &cap, "\\", 1); break;
            case '/':  wysna_json_buf_append_bytes(&buf, &blen, &cap, "/", 1); break;
            case 'b':  wysna_json_buf_append_bytes(&buf, &blen, &cap, "\b", 1); break;
            case 'f':  wysna_json_buf_append_bytes(&buf, &blen, &cap, "\f", 1); break;
            case 'n':  wysna_json_buf_append_bytes(&buf, &blen, &cap, "\n", 1); break;
            case 'r':  wysna_json_buf_append_bytes(&buf, &blen, &cap, "\r", 1); break;
            case 't':  wysna_json_buf_append_bytes(&buf, &blen, &cap, "\t", 1); break;
            case 'u': {
                int32_t first = wysna_json_read_hex4(ctx, &p);
                if (first < 0) {
                    free(buf);
                    return wysna_unit();
                }
                uint32_t cp = (uint32_t)first;
                if (first >= 0xD800 && first <= 0xDBFF) {
                    if (p >= end) {
                        free(buf);
                        wysna_json_fail(ctx, end, "high surrogate must be followed by a low surrogate");
                        return wysna_unit();
                    }
                    unsigned char s1 = *p++;
                    if (s1 != '\\') {
                        free(buf);
                        wysna_json_fail(ctx, p, "high surrogate must be followed by a low surrogate");
                        return wysna_unit();
                    }
                    if (p >= end) {
                        free(buf);
                        wysna_json_fail(ctx, end, "high surrogate must be followed by a low surrogate");
                        return wysna_unit();
                    }
                    unsigned char s2 = *p++;
                    if (s2 != 'u') {
                        free(buf);
                        wysna_json_fail(ctx, p, "high surrogate must be followed by a low surrogate");
                        return wysna_unit();
                    }
                    int32_t second = wysna_json_read_hex4(ctx, &p);
                    if (second < 0) {
                        free(buf);
                        return wysna_unit();
                    }
                    if (second < 0xDC00 || second > 0xDFFF) {
                        free(buf);
                        wysna_json_fail(ctx, p, "high surrogate must be followed by a low surrogate");
                        return wysna_unit();
                    }
                    cp = 0x10000u + (((uint32_t)(first - 0xD800)) << 10) + (uint32_t)(second - 0xDC00);
                } else if (first >= 0xDC00 && first <= 0xDFFF) {
                    free(buf);
                    wysna_json_fail(ctx, p, "unexpected low surrogate");
                    return wysna_unit();
                }
                wysna_json_buf_append_utf8(&buf, &blen, &cap, cp);
                break;
            }
            default:
                free(buf);
                wysna_json_fail(ctx, p, "invalid string escape");
                return wysna_unit();
        }
    }
    if (p >= end) {
        free(buf);
        wysna_json_fail(ctx, end, "unterminated string");
        return wysna_unit();
    }
    p++; /* consume closing '"' */
    ctx->cursor = p;
    buf[blen] = '\0';
    return wysna_owned_str_n(buf, blen);
}

static inline WVal wysna_json_read_number(WysnaJsonCtx* ctx) {
    const unsigned char* start = ctx->cursor;
    const unsigned char* p = start;
    const unsigned char* end = ctx->end;
    if (p < end && *p == '-') p++;
    if (p < end && *p == '0') {
        p++;
        if (p < end && *p >= '0' && *p <= '9') {
            wysna_json_fail(ctx, p, "leading zero in number");
            return wysna_unit();
        }
    } else if (p < end && *p >= '1' && *p <= '9') {
        p++;
        while (p < end && *p >= '0' && *p <= '9') p++;
    } else {
        wysna_json_fail(ctx, p, "expected integer part");
        return wysna_unit();
    }
    if (p < end && *p == '.') {
        p++;
        if (p >= end || *p < '0' || *p > '9') {
            wysna_json_fail(ctx, p, "expected digit after decimal point");
            return wysna_unit();
        }
        while (p < end && *p >= '0' && *p <= '9') p++;
    }
    if (p < end && (*p == 'e' || *p == 'E')) {
        p++;
        if (p < end && (*p == '+' || *p == '-')) p++;
        if (p >= end || *p < '0' || *p > '9') {
            wysna_json_fail(ctx, p, "expected exponent digits");
            return wysna_unit();
        }
        while (p < end && *p >= '0' && *p <= '9') p++;
    }
    ctx->cursor = p;
    WVal num_str = wysna_copy_str_n((const char*)start, (size_t)(p - start));
    return wysna_enum_payload(ctx->enum_name, "Number", num_str);
}

static inline WVal wysna_json_read_value(WysnaJsonCtx* ctx);

static inline WVal wysna_json_read_array(WysnaJsonCtx* ctx) {
    ctx->cursor++; /* skip '[' */
    ctx->depth++;
    if (ctx->depth > ctx->max_depth) {
        const char* msg = (ctx->max_depth == 32)
            ? "maximum JSON nesting depth is 32"
            : "maximum JSON nesting depth limit exceeded";
        wysna_json_fail(ctx, ctx->cursor, msg);
        return wysna_unit();
    }
    wysna_json_skip_space(ctx);

    WListBuf* list = (WListBuf*)wysna_alloc_safe(sizeof(WListBuf));
    list->refcount = 1;
    list->len = 0;
    list->cap = 8;
    list->scalar_only = 0;
    list->data = (WVal*)wysna_alloc_safe(sizeof(WVal) * (size_t)list->cap);
    wysna_mem_on_alloc();
    WVal list_val = wysna_make_hi_ptr(WVAL_HI_LIST, list);

    if (ctx->cursor < ctx->end && *ctx->cursor == ']') {
        ctx->cursor++;
        ctx->depth--;
        return wysna_enum_payload(ctx->enum_name, "Array", list_val);
    }

    while (true) {
        WVal item = wysna_json_read_value(ctx);
        if (ctx->failed) {
            wysna_release(list_val);
            return wysna_unit();
        }
        if (list->len >= list->cap) {
            list->cap = list->cap > 0 ? list->cap * 2 : 8;
            list->data = (WVal*)wysna_realloc_safe(list->data, sizeof(WVal) * (size_t)list->cap);
        }
        list->data[list->len++] = item;

        wysna_json_skip_space(ctx);
        if (ctx->cursor < ctx->end && *ctx->cursor == ']') {
            ctx->cursor++;
            ctx->depth--;
            return wysna_enum_payload(ctx->enum_name, "Array", list_val);
        }
        if (ctx->cursor >= ctx->end || *ctx->cursor != ',') {
            wysna_release(list_val);
            wysna_json_fail(ctx, ctx->cursor, "expected ',' or ']'");
            return wysna_unit();
        }
        ctx->cursor++;
        wysna_json_skip_space(ctx);
    }
}

static inline WVal wysna_json_read_object(WysnaJsonCtx* ctx) {
    ctx->cursor++; /* skip '{' */
    ctx->depth++;
    if (ctx->depth > ctx->max_depth) {
        const char* msg = (ctx->max_depth == 32)
            ? "maximum JSON nesting depth is 32"
            : "maximum JSON nesting depth limit exceeded";
        wysna_json_fail(ctx, ctx->cursor, msg);
        return wysna_unit();
    }
    wysna_json_skip_space(ctx);

    WDictBuf* d = (WDictBuf*)wysna_alloc_safe(sizeof(WDictBuf));
    d->refcount = 1;
    d->len = 0;
    d->cap = 8;
    d->keys = (WVal*)wysna_alloc_safe(sizeof(WVal) * (size_t)d->cap);
    d->vals = (WVal*)wysna_alloc_safe(sizeof(WVal) * (size_t)d->cap);
    d->bucket_count = 16;
    d->buckets = (int32_t*)wysna_alloc_safe(sizeof(int32_t) * (size_t)d->bucket_count);
    for (int32_t i = 0; i < d->bucket_count; i++) d->buckets[i] = -1;
    wysna_mem_on_alloc();
    WVal dict_val = wysna_make_hi_ptr(WVAL_HI_DICT, d);

    if (ctx->cursor < ctx->end && *ctx->cursor == '}') {
        ctx->cursor++;
        ctx->depth--;
        return wysna_enum_payload(ctx->enum_name, "Object", dict_val);
    }

    while (true) {
        if (ctx->cursor >= ctx->end || *ctx->cursor != '"') {
            wysna_release(dict_val);
            wysna_json_fail(ctx, ctx->cursor, "object key must be a string");
            return wysna_unit();
        }
        WVal key = wysna_json_read_string(ctx);
        if (ctx->failed) {
            wysna_release(dict_val);
            return wysna_unit();
        }
        wysna_json_skip_space(ctx);
        if (ctx->cursor >= ctx->end || *ctx->cursor != ':') {
            wysna_release(key);
            wysna_release(dict_val);
            const unsigned char* fail_pos = wysna_json_step_next(ctx->cursor, ctx->end);
            wysna_json_fail(ctx, fail_pos, "expected ':' after object key");
            return wysna_unit();
        }
        ctx->cursor++;
        wysna_json_skip_space(ctx);
        WVal val = wysna_json_read_value(ctx);
        if (ctx->failed) {
            wysna_release(key);
            wysna_release(dict_val);
            return wysna_unit();
        }
        wysna_dict_insert_owned(d, key, val);

        wysna_json_skip_space(ctx);
        if (ctx->cursor < ctx->end && *ctx->cursor == '}') {
            ctx->cursor++;
            ctx->depth--;
            return wysna_enum_payload(ctx->enum_name, "Object", dict_val);
        }
        if (ctx->cursor >= ctx->end || *ctx->cursor != ',') {
            wysna_release(dict_val);
            wysna_json_fail(ctx, ctx->cursor, "expected ',' or '}'");
            return wysna_unit();
        }
        ctx->cursor++;
        wysna_json_skip_space(ctx);
    }
}

static inline WVal wysna_json_read_value(WysnaJsonCtx* ctx) {
    wysna_json_skip_space(ctx);
    if (ctx->cursor >= ctx->end) {
        wysna_json_fail(ctx, ctx->cursor, "expected JSON value");
        return wysna_unit();
    }
    unsigned char ch = *ctx->cursor;
    if (ch == '"') {
        if (!wysna_json_reserve_node(ctx)) return wysna_unit();
        WVal str_val = wysna_json_read_string(ctx);
        if (ctx->failed) return wysna_unit();
        return wysna_enum_payload(ctx->enum_name, "String", str_val);
    }
    if (ch == '{') {
        if (!wysna_json_reserve_node(ctx)) return wysna_unit();
        return wysna_json_read_object(ctx);
    }
    if (ch == '[') {
        if (!wysna_json_reserve_node(ctx)) return wysna_unit();
        return wysna_json_read_array(ctx);
    }
    if (ch == 't') {
        if (!wysna_json_reserve_node(ctx)) return wysna_unit();
        if ((size_t)(ctx->end - ctx->cursor) < 4 || memcmp(ctx->cursor, "true", 4) != 0) {
            wysna_json_fail(ctx, ctx->cursor, "invalid literal");
            return wysna_unit();
        }
        ctx->cursor += 4;
        if (ctx->val_true.bits == WVAL_UNIT_BITS) {
            ctx->val_true = wysna_enum_payload(ctx->enum_name, "Bool", wysna_bool(true));
        }
        return wysna_clone(ctx->val_true);
    }
    if (ch == 'f') {
        if (!wysna_json_reserve_node(ctx)) return wysna_unit();
        if ((size_t)(ctx->end - ctx->cursor) < 5 || memcmp(ctx->cursor, "false", 5) != 0) {
            wysna_json_fail(ctx, ctx->cursor, "invalid literal");
            return wysna_unit();
        }
        ctx->cursor += 5;
        if (ctx->val_false.bits == WVAL_UNIT_BITS) {
            ctx->val_false = wysna_enum_payload(ctx->enum_name, "Bool", wysna_bool(false));
        }
        return wysna_clone(ctx->val_false);
    }
    if (ch == 'n') {
        if (!wysna_json_reserve_node(ctx)) return wysna_unit();
        if ((size_t)(ctx->end - ctx->cursor) < 4 || memcmp(ctx->cursor, "null", 4) != 0) {
            wysna_json_fail(ctx, ctx->cursor, "invalid literal");
            return wysna_unit();
        }
        ctx->cursor += 4;
        return wysna_clone(ctx->proto_null);
    }
    if (ch == '-' || (ch >= '0' && ch <= '9')) {
        if (!wysna_json_reserve_node(ctx)) return wysna_unit();
        return wysna_json_read_number(ctx);
    }
    wysna_json_fail(ctx, ctx->cursor, "expected JSON value");
    return wysna_unit();
}

static inline WVal wysna_builtin_json_parse(
    WVal source,
    WVal max_bytes_v,
    WVal max_depth_v,
    WVal max_nodes_v,
    WVal proto_null,
    WVal proto_err
) {
    int64_t max_bytes = wysna_as_int(max_bytes_v);
    int64_t max_depth = wysna_as_int(max_depth_v);
    int64_t max_nodes = wysna_as_int(max_nodes_v);

    if (max_bytes < 1 || max_bytes > 16777216) {
        return wysna_err(wysna_json_make_error(proto_err, 0, "max_input_bytes must be between 1 and 16777216"));
    }
    if (max_depth < 1 || max_depth > 32) {
        return wysna_err(wysna_json_make_error(proto_err, 0, "max_depth must be between 1 and 32"));
    }
    if (max_nodes < 1 || max_nodes > 100000) {
        return wysna_err(wysna_json_make_error(proto_err, 0, "max_nodes must be between 1 and 100000"));
    }

    char scratch[7];
    size_t byte_len = 0;
    const char* bytes = wysna_str_bytes(source, scratch, &byte_len);
    if (byte_len > (size_t)max_bytes) {
        size_t total_bytes = 0;
        size_t pos = 0;
        int64_t cp_index = 0;
        while (pos < byte_len) {
            size_t step = 0;
            if (!wysna_utf8_valid_codepoint_at_n(bytes, byte_len, pos, &step, NULL)) {
                step = 1;
            }
            if (total_bytes + step > (size_t)max_bytes) {
                return wysna_err(wysna_json_make_error(proto_err, cp_index, "maximum JSON input byte limit exceeded"));
            }
            total_bytes += step;
            pos += step;
            cp_index++;
        }
        return wysna_err(wysna_json_make_error(proto_err, cp_index, "maximum JSON input byte limit exceeded"));
    }

    WEnumBuf* null_en = wysna_as_enum(proto_null);
    const char* enum_name = (null_en && null_en->enum_name) ? null_en->enum_name : "Value";

    WysnaJsonCtx ctx;
    ctx.base = (const unsigned char*)bytes;
    ctx.cursor = ctx.base;
    ctx.end = ctx.base + byte_len;
    ctx.depth = 0;
    ctx.max_depth = (int32_t)max_depth;
    ctx.node_count = 0;
    ctx.max_nodes = (int32_t)max_nodes;
    ctx.enum_name = enum_name;
    ctx.proto_null = proto_null;
    ctx.val_true = wysna_unit();
    ctx.val_false = wysna_unit();
    ctx.proto_err = proto_err;
    ctx.failed = false;
    ctx.err_byte_pos = 0;
    ctx.err_msg[0] = '\0';

    wysna_json_skip_space(&ctx);
    WVal root = wysna_json_read_value(&ctx);
    if (!ctx.failed) {
        wysna_json_skip_space(&ctx);
        if (ctx.cursor != ctx.end) {
            wysna_release(root);
            root = wysna_unit();
            wysna_json_fail(&ctx, ctx.cursor, "trailing characters after JSON value");
        }
    }

    wysna_release(ctx.val_true);
    wysna_release(ctx.val_false);

    if (ctx.failed) {
        int64_t err_off = wysna_json_cp_offset(ctx.base, ctx.err_byte_pos);
        return wysna_err(wysna_json_make_error(proto_err, err_off, ctx.err_msg));
    }
    return wysna_ok(root);
}

static inline WVal wysna_builtin_json_parse_int(WVal raw_val) {
    char scratch[7];
    size_t slen = 0;
    const unsigned char* s = (const unsigned char*)wysna_str_bytes(raw_val, scratch, &slen);
    const unsigned char* end = s + slen;
    while (s < end && (*s == ' ' || *s == '\t' || *s == '\r' || *s == '\n')) s++;
    while (end > s && (end[-1] == ' ' || end[-1] == '\t' || end[-1] == '\r' || end[-1] == '\n')) end--;
    if (s >= end) {
        return wysna_err(wysna_copy_str("number value must contain one JSON number"));
    }

    const unsigned char* p = s;
    bool negative = false;
    if (*p == '-') {
        negative = true;
        p++;
        if (p >= end) {
            return wysna_err(wysna_copy_str("number value must contain one JSON number"));
        }
    }

    int64_t limit = negative ? INT64_MIN : -INT64_MAX;
    int64_t acc = 0;
    if (*p == '0') {
        p++;
        if (p < end && *p >= '0' && *p <= '9') {
            return wysna_err(wysna_copy_str("number value must contain one JSON number"));
        }
    } else if (*p >= '1' && *p <= '9') {
        while (p < end && *p >= '0' && *p <= '9') {
            int64_t d = (int64_t)(*p - '0');
            if (acc < limit / 10) {
                /* Still validate rest is integer syntax before reporting overflow vs fraction/exponent. */
                while (p < end && *p >= '0' && *p <= '9') p++;
                if (p < end && (*p == '.' || *p == 'e' || *p == 'E')) {
                    goto check_frac_or_exp;
                }
                if (p == end) {
                    return wysna_err(wysna_copy_str("integer overflow"));
                }
                return wysna_err(wysna_copy_str("number value must contain one JSON number"));
            }
            acc *= 10;
            if (acc < limit + d) {
                while (p < end && *p >= '0' && *p <= '9') p++;
                if (p < end && (*p == '.' || *p == 'e' || *p == 'E')) {
                    goto check_frac_or_exp;
                }
                if (p == end) {
                    return wysna_err(wysna_copy_str("integer overflow"));
                }
                return wysna_err(wysna_copy_str("number value must contain one JSON number"));
            }
            acc -= d;
            p++;
        }
    } else {
        return wysna_err(wysna_copy_str("number value must contain one JSON number"));
    }

    if (p == end) {
        return wysna_ok(wysna_int(negative ? acc : -acc));
    }

check_frac_or_exp:
    if (*p == '.' || *p == 'e' || *p == 'E') {
        if (*p == '.') {
            p++;
            if (p >= end || *p < '0' || *p > '9') {
                return wysna_err(wysna_copy_str("number value must contain one JSON number"));
            }
            while (p < end && *p >= '0' && *p <= '9') p++;
        }
        if (p < end && (*p == 'e' || *p == 'E')) {
            p++;
            if (p < end && (*p == '+' || *p == '-')) p++;
            if (p >= end || *p < '0' || *p > '9') {
                return wysna_err(wysna_copy_str("number value must contain one JSON number"));
            }
            while (p < end && *p >= '0' && *p <= '9') p++;
        }
        if (p == end) {
            return wysna_err(wysna_copy_str("expected JSON integer, found JSON number with a fractional or exponent part"));
        }
    }
    return wysna_err(wysna_copy_str("number value must contain one JSON number"));
}

#endif /* WYSNA_RUNTIME_JSON_H */
