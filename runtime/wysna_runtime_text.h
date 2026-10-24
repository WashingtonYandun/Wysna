/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_TEXT_H
#define WYSNA_RUNTIME_TEXT_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
#endif

/* UTF-8 Code-Point Helpers (SPEC Section 3.3) */
static inline size_t wysna_utf8_seq_len(unsigned char c) {
    if (c < 0x80) return 1;
    if ((c & 0xE0) == 0xC0) return 2;
    if ((c & 0xF0) == 0xE0) return 3;
    if ((c & 0xF8) == 0xF0) return 4;
    return 1;
}

static inline bool wysna_utf8_valid_codepoint_at_n(const char* s, size_t length, size_t pos, size_t* out_len, int64_t* out_cp) {
    W_STRING_SCAN_STEP();
    const unsigned char* u = (const unsigned char*)s + pos;
    if (pos >= length) {
        if (out_len) *out_len = 0;
        return false;
    }
    if (u[0] < 0x80) {
        if (out_len) *out_len = 1;
        if (out_cp) *out_cp = u[0];
        return true;
    }
    if ((u[0] & 0xE0) == 0xC0) {
        if (length - pos < 2) return false;
        if ((u[1] & 0xC0) != 0x80) return false;
        int64_t cp = ((u[0] & 0x1F) << 6) | (u[1] & 0x3F);
        if (cp < 0x80) return false;
        if (out_len) *out_len = 2;
        if (out_cp) *out_cp = cp;
        return true;
    }
    if ((u[0] & 0xF0) == 0xE0) {
        if (length - pos < 3) return false;
        if ((u[1] & 0xC0) != 0x80 || (u[2] & 0xC0) != 0x80) return false;
        int64_t cp = ((u[0] & 0x0F) << 12) | ((u[1] & 0x3F) << 6) | (u[2] & 0x3F);
        if (cp < 0x800 || (cp >= 0xD800 && cp <= 0xDFFF)) return false;
        if (out_len) *out_len = 3;
        if (out_cp) *out_cp = cp;
        return true;
    }
    if ((u[0] & 0xF8) == 0xF0) {
        if (length - pos < 4) return false;
        if ((u[1] & 0xC0) != 0x80 || (u[2] & 0xC0) != 0x80 || (u[3] & 0xC0) != 0x80) return false;
        int64_t cp = ((u[0] & 0x07) << 18) | ((u[1] & 0x3F) << 12) | ((u[2] & 0x3F) << 6) | (u[3] & 0x3F);
        if (cp < 0x10000 || cp > 0x10FFFF) return false;
        if (out_len) *out_len = 4;
        if (out_cp) *out_cp = cp;
        return true;
    }
    return false;
}

static inline int64_t wysna_utf8_cp_len_n(const char* s, size_t length) {
    int64_t count = 0;
    size_t pos = 0;
    while (pos < length) {
        size_t step = 0;
        if (wysna_utf8_valid_codepoint_at_n(s, length, pos, &step, NULL)) {
            pos += step;
        } else {
            pos++;
        }
        count++;
    }
    return count;
}

static inline bool wysna_utf8_cp_offset_n(const char* s, size_t length, int64_t target_cp, size_t* out_off, size_t* out_len) {
    if (target_cp < 0) return false;
    int64_t idx = 0;
    size_t pos = 0;
    while (pos < length) {
        size_t step = 0;
        size_t start = pos;
        if (wysna_utf8_valid_codepoint_at_n(s, length, pos, &step, NULL)) {
            pos += step;
        } else {
            step = 1;
            pos++;
        }
        if (idx == target_cp) {
            *out_off = start;
            *out_len = step;
            return true;
        }
        idx++;
    }
    if (idx == target_cp) {
        *out_off = pos;
        *out_len = 0;
        return false;
    }
    return false;
}

/* Keep UTF-8 decoding and index construction out of cached index callers. */
static W_RUNTIME_NOINLINE bool wysna_string_offset_slow(WVal value, int64_t index, size_t* offset, size_t* length) {
    WStringBuf* owner = value.as.string.owner;
    if (!owner) return wysna_utf8_cp_offset_n(value.as.string.data, value.as.string.length, index, offset, length);
    if (!owner->codepoint_offsets) {
        size_t ascii_pos = 0;
        const unsigned char* bytes = (const unsigned char*)value.as.string.data;
        while (ascii_pos < value.as.string.length && bytes[ascii_pos] < 0x80) {
            W_STRING_SCAN_STEP();
            ascii_pos++;
        }
        if (ascii_pos == value.as.string.length) {
            owner->codepoint_count = value.as.string.length;
            if ((uint64_t)index > value.as.string.length) return false;
            *offset = (size_t)index;
            *length = (uint64_t)index < value.as.string.length ? 1 : 0;
            return *length != 0;
        }
        if (value.as.string.length > SIZE_MAX / sizeof(size_t) - 1) {
            fprintf(stderr, "[W4003] RuntimeError: String index too large\n"); exit(1);
        }
        size_t count_hint = owner->codepoint_count;
        if (count_hint == SIZE_MAX) count_hint = (size_t)wysna_utf8_cp_len_n(value.as.string.data, value.as.string.length);
        owner->codepoint_offsets = (size_t*)wysna_alloc_safe((count_hint + 1) * sizeof(size_t));
        size_t position = 0, count = 0;
        while (position < value.as.string.length) {
            size_t step = 1;
            owner->codepoint_offsets[count++] = position;
            wysna_utf8_valid_codepoint_at_n(value.as.string.data, value.as.string.length, position, &step, NULL);
            position += step;
        }
        owner->codepoint_offsets[count] = value.as.string.length;
        owner->codepoint_count = count;
    }
    if ((uint64_t)index > owner->codepoint_count) return false;
    *offset = owner->codepoint_offsets[index];
    *length = (uint64_t)index < owner->codepoint_count ? owner->codepoint_offsets[index + 1] - *offset : 0;
    return *length != 0;
}

/* Lazily index shared owned strings once; repeated lexer indexing is O(1). */
static inline bool wysna_string_offset(WVal value, int64_t index, size_t* offset, size_t* length) {
    if (index < 0) return false;
    WStringBuf* owner = value.as.string.owner;
    if (owner && owner->codepoint_count == value.as.string.length) {
        if ((uint64_t)index > value.as.string.length) return false;
        *offset = (size_t)index;
        *length = (uint64_t)index < value.as.string.length ? 1 : 0;
        return *length != 0;
    }
    if (owner && owner->codepoint_offsets) {
        if ((uint64_t)index > owner->codepoint_count) return false;
        *offset = owner->codepoint_offsets[index];
        *length = (uint64_t)index < owner->codepoint_count ? owner->codepoint_offsets[index + 1] - *offset : 0;
        return *length != 0;
    }
    return wysna_string_offset_slow(value, index, offset, length);
}

/* Owned strings cache counts independently of the lazy random-access index. */
static inline int64_t wysna_string_length(WVal value) {
    if (value.as.string.owner && value.as.string.owner->codepoint_count != SIZE_MAX)
        return (int64_t)value.as.string.owner->codepoint_count;
    int64_t count = wysna_utf8_cp_len_n(value.as.string.data, value.as.string.length);
    if (value.as.string.owner) value.as.string.owner->codepoint_count = (size_t)count;
    return count;
}

#endif /* WYSNA_RUNTIME_TEXT_H */
