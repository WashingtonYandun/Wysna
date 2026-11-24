/* Unicode canonical normalization (NFC / NFD, UAX #15) for std/text.
 * Included by wysna_host.h after the buffer table. Tables come from wysna_unicode_tables.h. */
#ifndef WYSNA_HOST_UNICODE_H
#define WYSNA_HOST_UNICODE_H

#include "wysna_unicode_tables.h"

#define WYSNA_HANGUL_SBASE 0xAC00u
#define WYSNA_HANGUL_LBASE 0x1100u
#define WYSNA_HANGUL_VBASE 0x1161u
#define WYSNA_HANGUL_TBASE 0x11A7u
#define WYSNA_HANGUL_LCOUNT 19u
#define WYSNA_HANGUL_VCOUNT 21u
#define WYSNA_HANGUL_TCOUNT 28u
#define WYSNA_HANGUL_NCOUNT (WYSNA_HANGUL_VCOUNT * WYSNA_HANGUL_TCOUNT)
#define WYSNA_HANGUL_SCOUNT (WYSNA_HANGUL_LCOUNT * WYSNA_HANGUL_NCOUNT)

typedef struct {
    uint32_t* data;
    size_t length;
    size_t capacity;
} WysnaCodepoints;

static inline int wysna_cp_push(WysnaCodepoints* list, uint32_t value) {
    if (list->length == list->capacity) {
        size_t capacity = list->capacity ? list->capacity * 2 : 64;
        uint32_t* grown = (uint32_t*)realloc(list->data, capacity * sizeof(uint32_t));
        if (!grown) return -1;
        list->data = grown;
        list->capacity = capacity;
    }
    list->data[list->length++] = value;
    return 0;
}

/* Strict UTF-8 decoder: rejects overlong forms, surrogates and values above U+10FFFF. */
static inline int wysna_utf8_decode_all(const uint8_t* data, size_t length, WysnaCodepoints* out) {
    size_t index = 0;
    while (index < length) {
        uint32_t first = data[index];
        uint32_t cp;
        size_t count;
        uint32_t minimum;
        if (first < 0x80) { cp = first; count = 1; minimum = 0; }
        else if (first >= 0xC2 && first <= 0xDF) { cp = first & 0x1F; count = 2; minimum = 0x80; }
        else if (first >= 0xE0 && first <= 0xEF) { cp = first & 0x0F; count = 3; minimum = 0x800; }
        else if (first >= 0xF0 && first <= 0xF4) { cp = first & 0x07; count = 4; minimum = 0x10000; }
        else return -1;
        if (index + count > length) return -1;
        for (size_t offset = 1; offset < count; offset++) {
            uint8_t next = data[index + offset];
            if ((next & 0xC0) != 0x80) return -1;
            cp = (cp << 6) | (next & 0x3F);
        }
        if (cp < minimum || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) return -1;
        if (wysna_cp_push(out, cp) != 0) return -1;
        index += count;
    }
    return 0;
}

static inline size_t wysna_utf8_encode(uint32_t cp, uint8_t out[4]) {
    if (cp < 0x80) { out[0] = (uint8_t)cp; return 1; }
    if (cp < 0x800) { out[0] = (uint8_t)(0xC0 | (cp >> 6)); out[1] = (uint8_t)(0x80 | (cp & 0x3F)); return 2; }
    if (cp < 0x10000) {
        out[0] = (uint8_t)(0xE0 | (cp >> 12)); out[1] = (uint8_t)(0x80 | ((cp >> 6) & 0x3F));
        out[2] = (uint8_t)(0x80 | (cp & 0x3F));
        return 3;
    }
    out[0] = (uint8_t)(0xF0 | (cp >> 18)); out[1] = (uint8_t)(0x80 | ((cp >> 12) & 0x3F));
    out[2] = (uint8_t)(0x80 | ((cp >> 6) & 0x3F)); out[3] = (uint8_t)(0x80 | (cp & 0x3F));
    return 4;
}

static inline const WysnaUnicodeMapping* wysna_unicode_find_decomposition(uint32_t cp) {
    size_t low = 0, high = WYSNA_UNICODE_DECOMPOSITIONS;
    while (low < high) {
        size_t mid = low + (high - low) / 2;
        uint32_t code = wysna_unicode_decompositions[mid].code;
        if (code == cp) return &wysna_unicode_decompositions[mid];
        if (code < cp) low = mid + 1; else high = mid;
    }
    return NULL;
}

static inline uint32_t wysna_unicode_find_composition(uint32_t first, uint32_t second) {
    size_t low = 0, high = WYSNA_UNICODE_COMPOSITIONS;
    while (low < high) {
        size_t mid = low + (high - low) / 2;
        const WysnaUnicodeMapping* item = &wysna_unicode_compositions[mid];
        if (item->code == first && item->first == second) return item->second;
        if (item->code < first || (item->code == first && item->first < second)) low = mid + 1; else high = mid;
    }
    return 0;
}

static inline uint8_t wysna_unicode_ccc(uint32_t cp) {
    size_t low = 0, high = WYSNA_UNICODE_CLASS_RUNS;
    while (low < high) {
        size_t mid = low + (high - low) / 2;
        const WysnaUnicodeClassRun* run = &wysna_unicode_classes[mid];
        if (cp < run->first) high = mid;
        else if (cp > run->last) low = mid + 1;
        else return run->ccc;
    }
    return 0;
}

static inline int wysna_unicode_decompose_one(uint32_t cp, WysnaCodepoints* out, int depth) {
    if (cp >= WYSNA_HANGUL_SBASE && cp < WYSNA_HANGUL_SBASE + WYSNA_HANGUL_SCOUNT) {
        uint32_t s_index = cp - WYSNA_HANGUL_SBASE;
        if (wysna_cp_push(out, WYSNA_HANGUL_LBASE + s_index / WYSNA_HANGUL_NCOUNT) != 0) return -1;
        if (wysna_cp_push(out, WYSNA_HANGUL_VBASE + (s_index % WYSNA_HANGUL_NCOUNT) / WYSNA_HANGUL_TCOUNT) != 0) return -1;
        uint32_t t_index = s_index % WYSNA_HANGUL_TCOUNT;
        if (t_index && wysna_cp_push(out, WYSNA_HANGUL_TBASE + t_index) != 0) return -1;
        return 0;
    }
    const WysnaUnicodeMapping* mapping = wysna_unicode_find_decomposition(cp);
    if (!mapping || depth > 8) return wysna_cp_push(out, cp);
    if (wysna_unicode_decompose_one(mapping->first, out, depth + 1) != 0) return -1;
    if (mapping->second && wysna_unicode_decompose_one(mapping->second, out, depth + 1) != 0) return -1;
    return 0;
}

/* Canonical ordering: stable insertion sort of each run of non-starters by combining class. */
static inline void wysna_unicode_reorder(uint32_t* data, size_t length) {
    size_t index = 0;
    while (index < length) {
        if (wysna_unicode_ccc(data[index]) == 0) { index++; continue; }
        size_t end = index;
        while (end < length && wysna_unicode_ccc(data[end]) != 0) end++;
        for (size_t i = index + 1; i < end; i++) {
            uint32_t value = data[i];
            uint8_t value_class = wysna_unicode_ccc(value);
            size_t j = i;
            while (j > index && wysna_unicode_ccc(data[j - 1]) > value_class) {
                data[j] = data[j - 1];
                j--;
            }
            data[j] = value;
        }
        index = end;
    }
}

static inline uint32_t wysna_unicode_compose_pair(uint32_t first, uint32_t second) {
    if (first >= WYSNA_HANGUL_LBASE && first < WYSNA_HANGUL_LBASE + WYSNA_HANGUL_LCOUNT &&
        second >= WYSNA_HANGUL_VBASE && second < WYSNA_HANGUL_VBASE + WYSNA_HANGUL_VCOUNT) {
        return WYSNA_HANGUL_SBASE + ((first - WYSNA_HANGUL_LBASE) * WYSNA_HANGUL_VCOUNT +
                                     (second - WYSNA_HANGUL_VBASE)) * WYSNA_HANGUL_TCOUNT;
    }
    if (first >= WYSNA_HANGUL_SBASE && first < WYSNA_HANGUL_SBASE + WYSNA_HANGUL_SCOUNT &&
        (first - WYSNA_HANGUL_SBASE) % WYSNA_HANGUL_TCOUNT == 0 &&
        second > WYSNA_HANGUL_TBASE && second < WYSNA_HANGUL_TBASE + WYSNA_HANGUL_TCOUNT) {
        return first + (second - WYSNA_HANGUL_TBASE);
    }
    return wysna_unicode_find_composition(first, second);
}

/* Composes in place; returns the new length. Input must already be NFD-ordered. */
static inline size_t wysna_unicode_compose(uint32_t* data, size_t length) {
    if (length == 0) return 0;
    size_t out = 0;
    size_t starter = (size_t)-1;
    uint8_t last_class = 0;
    for (size_t index = 0; index < length; index++) {
        uint32_t cp = data[index];
        uint8_t cls = wysna_unicode_ccc(cp);
        if (starter != (size_t)-1 && (last_class < cls || last_class == 0)) {
            uint32_t composite = wysna_unicode_compose_pair(data[starter], cp);
            if (composite) {
                data[starter] = composite;
                continue;
            }
        }
        if (cls == 0) { starter = out; last_class = 0; }
        else last_class = cls;
        data[out++] = cp;
    }
    return out;
}

/* form: 0 = NFC, 1 = NFD. Returns 0 on success, -1 for invalid UTF-8 or handles. */
WYSNA_RT_FN int64_t wysna_host_unicode_normalize(void* input_handle, int64_t form, void* output_handle) {
    WysnaHostEntry* input = wysna_host_find(input_handle, 1);
    WysnaHostEntry* output = wysna_host_find(output_handle, 1);
    if (!input || !output || (form != 0 && form != 1)) return -1;
    WysnaCodepoints decoded = {NULL, 0, 0};
    WysnaCodepoints expanded = {NULL, 0, 0};
    int64_t status = -1;
    if (wysna_utf8_decode_all((const uint8_t*)input->data, input->length, &decoded) != 0) goto done;
    for (size_t index = 0; index < decoded.length; index++) {
        if (wysna_unicode_decompose_one(decoded.data[index], &expanded, 0) != 0) goto done;
    }
    wysna_unicode_reorder(expanded.data, expanded.length);
    size_t final_length = expanded.length;
    if (form == 0) final_length = wysna_unicode_compose(expanded.data, expanded.length);
    output->length = 0;
    for (size_t index = 0; index < final_length; index++) {
        uint8_t encoded[4];
        size_t size = wysna_utf8_encode(expanded.data[index], encoded);
        if (wysna_host_buffer_append_bytes(output, encoded, size) != size) goto done;
    }
    status = 0;
done:
    free(decoded.data);
    free(expanded.data);
    return status;
}

#endif
