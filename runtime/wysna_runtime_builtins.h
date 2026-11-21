/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_BUILTINS_H
#define WYSNA_RUNTIME_BUILTINS_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
#endif

/* Standard Built-ins */
static inline WVal wysna_builtin_print(int32_t argc, const WVal* argv) {
#ifdef WYSNA_DENY_CAP_STDIO
    (void)argc; (void)argv;
    wysna_sandbox_deny("stdio");
    return wysna_unit();
#endif
    for (int32_t i = 0; i < argc; i++) {
        if (i > 0) fputc(' ', stdout);
        WVal value = argv[i];
        for (WEnumBuf* en = wysna_as_enum(value);
             en && strcmp(en->enum_name, "Option") == 0 && strcmp(en->variant, "Some") == 0 && en->has_payload;
             en = wysna_as_enum(value)) {
            value = en->payload;
        }
        WTag vtag = wysna_tag(value);
        if (vtag == W_STR) {
            char sb[7];
            size_t slen = 0;
            const char* sdata = wysna_str_bytes(value, sb, &slen);
            fwrite(sdata, 1, slen, stdout);
        }
        else if (vtag == W_INT) fprintf(stdout, "%" PRId64, wysna_as_int(value));
        else if (vtag == W_BOOL) fputs(wysna_as_bool(value) ? "true" : "false", stdout);
        else if (vtag == W_NONE) fputs("none", stdout);
        else if (vtag == W_UNIT) fputs("unit", stdout);
        else { char* s = wysna_to_cstr(value, false); fputs(s, stdout); free(s); }
    }
    fputc('\n', stdout);
    fflush(stdout);
    return wysna_unit();
}

static inline WVal wysna_builtin_eprint(int32_t argc, const WVal* argv) {
#ifdef WYSNA_DENY_CAP_STDIO
    (void)argc; (void)argv;
    wysna_sandbox_deny("stdio");
    return wysna_unit();
#endif
    for (int32_t i = 0; i < argc; i++) {
        if (i > 0) fputc(' ', stderr);
        WVal value = argv[i];
        for (WEnumBuf* en = wysna_as_enum(value);
             en && strcmp(en->enum_name, "Option") == 0 && strcmp(en->variant, "Some") == 0 && en->has_payload;
             en = wysna_as_enum(value)) {
            value = en->payload;
        }
        WTag vtag = wysna_tag(value);
        if (vtag == W_STR) {
            char sb[7];
            size_t slen = 0;
            const char* sdata = wysna_str_bytes(value, sb, &slen);
            const char* metrics = getenv("WYSNA_COW_METRICS");
            if (metrics && strcmp(metrics, "1") == 0 && argc == 1 &&
                slen >= 19 &&
                strncmp(sdata, "__COW_CHECKPOINT__:", 19) == 0) {
                const char* label = sdata + 19;
                fprintf(stderr, "[WYSNA_COW_CHECKPOINT:%s] detach_list=%" PRId64 " detach_dict=%" PRId64 " detach_struct=%" PRId64 " copied_slots=%" PRId64 " copied_bytes=%" PRId64 " allocated_bytes=%" PRId64 " allocs=%" PRId64,
                        label, wysna_g_cow_detach_list, wysna_g_cow_detach_dict, wysna_g_cow_detach_struct, wysna_g_cow_copied_slots, wysna_g_cow_copied_bytes, wysna_g_cow_allocated_bytes, wysna_g_cow_alloc_count);
            } else {
                fwrite(sdata, 1, slen, stderr);
            }
        }
        else if (vtag == W_INT) fprintf(stderr, "%" PRId64, wysna_as_int(value));
        else if (vtag == W_BOOL) fputs(wysna_as_bool(value) ? "true" : "false", stderr);
        else if (vtag == W_NONE) fputs("none", stderr);
        else if (vtag == W_UNIT) fputs("unit", stderr);
        else {
            char* s = wysna_to_cstr(value, false);
            fputs(s, stderr);
            free(s);
        }
    }
    fputc('\n', stderr);
    fflush(stderr);
    return wysna_unit();
}

static inline WVal wysna_builtin_exit(WVal code) {
#ifdef WYSNA_DENY_CAP_PROCESS_CONTROL
    (void)code;
    wysna_sandbox_deny("process-control");
    return wysna_unit();
#endif
    int c = (wysna_tag(code) == W_INT) ? (int)wysna_as_int(code) : 0;
    wysna_runtime_cleanup_for_exit();
    wysna_panic_exit(c);
    return wysna_unit();
}

static inline WVal wysna_builtin_len(WVal v) {
    switch (wysna_tag(v)) {
        case W_LIST:  return wysna_int(wysna_as_list(v)->len);
        case W_DICT:  return wysna_int(wysna_as_dict(v)->len);
        case W_TUPLE: return wysna_int(wysna_as_tuple(v)->len);
        case W_STR:   return wysna_int(wysna_string_length(v));
        default:      return wysna_int(0);
    }
}

static inline WVal wysna_builtin_str(WVal v) {
    WTag tag = wysna_tag(v);
    if (tag == W_STR) return wysna_clone(v);
    if (tag == W_BOOL) return wysna_bool(false).bits == v.bits ? wysna_sso_from_n("false", 5) : wysna_sso_from_n("true", 4);
    if (tag == W_NONE) return wysna_sso_from_n("none", 4);
    if (tag == W_UNIT) return wysna_sso_from_n("unit", 4);
    if (tag == W_INT) {
        int64_t val = wysna_as_int(v);
        if (val >= 0 && val <= 9) return wysna_ascii_string((uint8_t)('0' + val));
        char buf[32];
        int len = snprintf(buf, sizeof(buf), "%" PRId64, val);
        if (len > 0) return wysna_copy_str(buf);
    }
    return wysna_owned_str(wysna_to_cstr(v, false));
}

static inline void wysna_conversion_error(const char* kind) {
    fprintf(stderr, "[W4003] RuntimeError: Invalid %s conversion\n", kind);
    wysna_panic_exit(1);
}

static inline const char* wysna_conversion_end(const char* end) {
    while (*end && isspace((unsigned char)*end)) end++;
    return end;
}

static inline WVal wysna_builtin_int(WVal v) {
    WTag tag = wysna_tag(v);
    if (tag == W_BOOL) return wysna_int(wysna_as_bool(v) ? 1 : 0);
    if (tag == W_INT) return wysna_clone(v);
    if (tag == W_FLOAT) {
        double fv = wysna_as_float(v);
        if (!isfinite(fv) || fv < -9223372036854775808.0 || fv >= 9223372036854775808.0)
            wysna_conversion_error("int");
        return wysna_int((int64_t)fv);
    }
    if (tag == W_STR) {
        char sb[7];
        size_t slen = 0;
        const char* sdata = wysna_str_bytes(v, sb, &slen);
        if (!memchr(sdata, 0, slen)) {
            char* end; errno = 0;
            int64_t value = strtoll(sdata, &end, 10);
            if (errno != ERANGE && end != sdata && *wysna_conversion_end(end) == 0) return wysna_int(value);
        }
    }
    wysna_conversion_error("int");
    return wysna_unit();
}

static inline bool wysna_ascii_space(unsigned char value) {
    return value == ' ' || (value >= 9 && value <= 13);
}

/* Match Python's explicit ASCII grammar, including inf/infinity/nan, before strtod. */
static inline bool wysna_float_text_valid(const char* text, size_t length) {
    size_t begin = 0, end = length;
    while (begin < end && wysna_ascii_space((unsigned char)text[begin])) begin++;
    while (end > begin && wysna_ascii_space((unsigned char)text[end - 1])) end--;
    if (begin < end && (text[begin] == '+' || text[begin] == '-')) begin++;
    const char* words[] = { "inf", "infinity", "nan" };
    for (size_t word = 0; word < 3; word++) {
        if (end - begin != strlen(words[word])) continue;
        bool same = true;
        for (size_t i = begin; i < end; i++) {
            unsigned char ch = (unsigned char)text[i];
            if (ch >= 'A' && ch <= 'Z') ch += 'a' - 'A';
            if (ch != (unsigned char)words[word][i - begin]) same = false;
        }
        if (same) return true;
    }
    size_t position = begin, digits = 0;
    while (position < end && text[position] >= '0' && text[position] <= '9') { position++; digits++; }
    if (position < end && text[position] == '.') {
        position++;
        while (position < end && text[position] >= '0' && text[position] <= '9') { position++; digits++; }
    }
    if (!digits) return false;
    if (position < end && (text[position] == 'e' || text[position] == 'E')) {
        position++;
        if (position < end && (text[position] == '+' || text[position] == '-')) position++;
        size_t exponent = position;
        while (position < end && text[position] >= '0' && text[position] <= '9') position++;
        if (position == exponent) return false;
    }
    return position == end;
}

static inline WVal wysna_builtin_float(WVal v) {
    WTag t = wysna_tag(v);
    if (t == W_BOOL) return wysna_float(wysna_as_bool(v) ? 1.0 : 0.0);
    if (t == W_FLOAT) return v;
    if (t == W_INT) return wysna_float((double)wysna_as_int(v));
    if (t == W_STR) {
        char scratch[7];
        size_t slen = 0;
        const char* sdata = wysna_str_bytes(v, scratch, &slen);
        if (wysna_float_text_valid(sdata, slen)) {
            char* end;
            double value = strtod(sdata, &end);
            if (end != sdata && *wysna_conversion_end(end) == 0 && !strstr(sdata, "0x") && !strstr(sdata, "0X"))
                return wysna_float(value);
        }
    }
    wysna_conversion_error("float");
    return wysna_unit();
}

static inline WVal wysna_builtin_ord(WVal v) {
    if (wysna_tag(v) != W_STR) {
        fprintf(stderr, "[W4003] RuntimeError: ord() expects non-empty str\n");
        wysna_panic_exit(1);
    }
    char scratch[7];
    size_t slen = 0;
    const char* sdata = wysna_str_bytes(v, scratch, &slen);
    if (slen == 0) {
        fprintf(stderr, "[W4003] RuntimeError: ord() expects non-empty str\n");
        wysna_panic_exit(1);
    }
    size_t cplen = 0;
    int64_t cp = 0;
    if (!wysna_utf8_valid_codepoint_at_n(sdata, slen, 0, &cplen, &cp)) {
        return wysna_int((unsigned char)sdata[0]);
    }
    return wysna_int(cp);
}

static inline WVal wysna_builtin_chr(WVal v) {
    if (wysna_tag(v) != W_INT) {
        fprintf(stderr, "[W4003] RuntimeError: chr() expects int\n");
        wysna_panic_exit(1);
    }
    int64_t cp = wysna_as_int(v);
    if (cp < 0 || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) {
        fprintf(stderr, "[W4003] RuntimeError: chr() arg not in valid Unicode scalar range: %" PRId64 "\n", cp);
        wysna_panic_exit(1);
    }
    if (cp < 0x80) return wysna_ascii_string((uint8_t)cp);
    char small[4];
    size_t len;
    if (cp < 0x800) {
        small[0] = (char)(0xC0 | (cp >> 6));
        small[1] = (char)(0x80 | (cp & 0x3F));
        len = 2;
    } else if (cp < 0x10000) {
        small[0] = (char)(0xE0 | (cp >> 12));
        small[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
        small[2] = (char)(0x80 | (cp & 0x3F));
        len = 3;
    } else {
        small[0] = (char)(0xF0 | (cp >> 18));
        small[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
        small[2] = (char)(0x80 | ((cp >> 6) & 0x3F));
        small[3] = (char)(0x80 | (cp & 0x3F));
        len = 4;
    }
    return wysna_sso_from_n(small, len);
}

static inline WVal wysna_builtin_slice(WVal s, WVal start, WVal end) {
    int64_t start_i = wysna_as_int(start);
    int64_t end_i = wysna_as_int(end);
    WTag st_tag = wysna_tag(s);
    if (st_tag == W_LIST) {
        /* SPEC 11.1: slice() is polymorphic over str and list[T]. */
        WListBuf* b = wysna_as_list(s);
        int64_t st = start_i < 0 ? 0 : start_i;
        int64_t en = end_i > b->len ? b->len : end_i;
        if (en <= st) return wysna_make_list(0, NULL);
        if (st == 0 && en == b->len) return wysna_clone(s);
        return wysna_make_list((int32_t)(en - st), b->data + st);
    }
    if (st_tag != W_STR) {
        fprintf(stderr, "[W4003] RuntimeError: slice() expects str or list\n");
        wysna_panic_exit(1);
    }
    char scratch[7];
    size_t slen = 0;
    const char* sdata = wysna_str_bytes(s, scratch, &slen);
    int64_t total_cp = wysna_string_length(s);
    int64_t st = start_i < 0 ? 0 : start_i;
    int64_t en = end_i > total_cp ? total_cp : end_i;
    if (en <= st || st >= total_cp) return wysna_sso_from_n("", 0);
    if (st == 0 && en == total_cp) return wysna_clone(s);
    size_t st_off = 0, dummy_len = 0, en_off = 0;
    wysna_string_offset(s, st, &st_off, &dummy_len);
    if (en == total_cp) {
        en_off = slen;
    } else {
        wysna_string_offset(s, en, &en_off, &dummy_len);
    }
    size_t blen = en_off - st_off;
    if (blen == 0) return wysna_sso_from_n("", 0);
    if (blen <= 6) return wysna_sso_from_n(sdata + st_off, blen);
    char* buf = (char*)wysna_alloc_safe(blen + 1);
    memcpy(buf, sdata + st_off, blen);
    buf[blen] = '\0';
    return wysna_owned_str_n(buf, blen);
}

/* A two-pass string builder: one allocation and one copy per input byte. */
static inline WVal wysna_builtin_join_strings(WVal parts, WVal separator) {
    if (wysna_tag(parts) != W_LIST || wysna_tag(separator) != W_STR)
        wysna_runtime_fail("W4003", "join_strings requires list[str] and str");
    WListBuf* p_list = wysna_as_list(parts);
    int32_t count = p_list->len;
    if (count == 0) return wysna_sso_from_n("", 0);
    if (count == 1) {
        WVal first = p_list->data[0];
        if (wysna_tag(first) != W_STR) wysna_runtime_fail("W4003", "join_strings requires string elements");
        return wysna_clone(first);
    }
    char sep_buf[7];
    size_t sep_len = 0;
    const char* sep_data = wysna_str_bytes(separator, sep_buf, &sep_len);
    size_t length = 0;
    for (int32_t i = 0; i < count; i++) {
        WVal part = p_list->data[i];
        if (wysna_tag(part) != W_STR) wysna_runtime_fail("W4003", "join_strings requires string elements");
        size_t part_len = wysna_str_len(part);
        size_t extra = i ? sep_len : 0;
        if (part_len > SIZE_MAX - length - 1 || extra > SIZE_MAX - length - part_len - 1)
            wysna_runtime_fail("W4003", "Joined string is too large");
        length += extra + part_len;
    }
    if (length == 0) return wysna_sso_from_n("", 0);
    if (length <= 6) {
        char small[7];
        size_t offset = 0;
        for (int32_t i = 0; i < count; i++) {
            if (i && sep_len) { memcpy(small + offset, sep_data, sep_len); offset += sep_len; }
            char pbuf[7];
            size_t plen = 0;
            const char* pdata = wysna_str_bytes(p_list->data[i], pbuf, &plen);
            if (plen) { memcpy(small + offset, pdata, plen); offset += plen; }
        }
        return wysna_sso_from_n(small, length);
    }
    char* data = (char*)wysna_alloc_safe(length + 1);
    size_t offset = 0;
    for (int32_t i = 0; i < count; i++) {
        if (i && sep_len) { memcpy(data + offset, sep_data, sep_len); offset += sep_len; }
        char pbuf[7];
        size_t plen = 0;
        const char* pdata = wysna_str_bytes(p_list->data[i], pbuf, &plen);
        if (plen) { memcpy(data + offset, pdata, plen); offset += plen; }
    }
    data[length] = 0;
    return wysna_owned_str_n(data, length);
}

static inline WVal wysna_builtin_bytes(WVal s) {
    if (wysna_tag(s) != W_STR) {
        fprintf(stderr, "[W4003] RuntimeError: bytes() expects str\n");
        wysna_panic_exit(1);
    }
    char scratch[7];
    size_t slen = 0;
    const char* sdata = wysna_str_bytes(s, scratch, &slen);
    WListBuf* b = (WListBuf*)wysna_alloc_safe(sizeof(WListBuf));
    b->refcount = 1;
    b->len = (int32_t)slen;
    b->cap = slen > 4 ? (int32_t)slen : 4;
    b->scalar_only = true;
    b->data = (WVal*)wysna_alloc_safe(sizeof(WVal) * b->cap);
    wysna_mem_on_alloc();
    for (size_t i = 0; i < slen; i++) {
        b->data[i] = wysna_int((int64_t)(unsigned char)sdata[i]);
    }
    return wysna_make_hi_ptr(WVAL_HI_LIST, b);
}

static inline WVal wysna_builtin_enumerate(WVal list_val) {
    if (wysna_tag(list_val) != W_LIST) {
        fprintf(stderr, "[W4003] RuntimeError: enumerate() expects list\n");
        wysna_panic_exit(1);
    }
    WListBuf* src = wysna_as_list(list_val);
    WListBuf* b = (WListBuf*)wysna_alloc_safe(sizeof(WListBuf));
    b->refcount = 1;
    b->len = src->len;
    b->cap = src->len > 4 ? src->len : 4;
    b->scalar_only = false;
    b->data = (WVal*)wysna_alloc_safe(sizeof(WVal) * b->cap);
    wysna_mem_on_alloc();
    for (int32_t i = 0; i < src->len; i++) {
        WVal pair[2] = { wysna_int(i), src->data[i] };
        b->data[i] = wysna_make_tuple(2, pair);
    }
    return wysna_make_hi_ptr(WVAL_HI_LIST, b);
}

static inline WVal wysna_builtin_dict_items(WVal dict_val) {
    if (wysna_tag(dict_val) != W_DICT) {
        fprintf(stderr, "[W4003] RuntimeError: dict_items() expects dict\n");
        wysna_panic_exit(1);
    }
    WDictBuf* d = wysna_as_dict(dict_val);
    WListBuf* b = (WListBuf*)wysna_alloc_safe(sizeof(WListBuf));
    b->refcount = 1;
    b->len = d->len;
    b->cap = d->len > 4 ? d->len : 4;
    b->scalar_only = false;
    b->data = (WVal*)wysna_alloc_safe(sizeof(WVal) * b->cap);
    wysna_mem_on_alloc();
    for (int32_t i = 0; i < d->len; i++) {
        WVal pair[2] = { d->keys[i], d->vals[i] };
        b->data[i] = wysna_make_tuple(2, pair);
    }
    return wysna_make_hi_ptr(WVAL_HI_LIST, b);
}

static inline WVal wysna_io_error(const char* message) {
    const char* keys[1] = { "message" };
    WVal values[1] = { wysna_str(message) };
    return wysna_err(wysna_make_struct("IoError", 1, keys, values));
}

static inline FILE* wysna_builtin_fopen(const char* path, const char* mode) {
#ifdef _WIN32
    int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, NULL, 0);
    if (!count) count = MultiByteToWideChar(CP_ACP, 0, path, -1, NULL, 0);
    if (!count) return NULL;
    wchar_t* wide = (wchar_t*)wysna_alloc_safe((size_t)count * sizeof(wchar_t));
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, path, -1, wide, count) &&
        !MultiByteToWideChar(CP_ACP, 0, path, -1, wide, count)) {
        free(wide);
        return NULL;
    }
    wchar_t wide_mode[8];
    MultiByteToWideChar(CP_UTF8, 0, mode, -1, wide_mode, 8);
    FILE* file = _wfopen(wide, wide_mode);
    free(wide);
    return file;
#else
    return fopen(path, mode);
#endif
}

static inline WVal wysna_builtin_read_file(WVal path) {
#ifdef WYSNA_DENY_CAP_FILESYSTEM_READ
    (void)path;
    wysna_sandbox_deny("filesystem.read");
    return wysna_io_error("Permission denied");
#endif
    if (wysna_tag(path) != W_STR) return wysna_io_error("Invalid path");
    char path_buf[7];
    size_t path_len = 0;
    const char* path_data = wysna_str_bytes(path, path_buf, &path_len);
    if (memchr(path_data, 0, path_len)) return wysna_io_error("Invalid path");
    FILE* file = wysna_builtin_fopen(path_data, "rb");
    if (!file) return wysna_io_error("Failed to open file");
    size_t length = 0, capacity = 4096;
    char* data = (char*)wysna_alloc_safe(capacity + 1);
    while (1) {
        size_t count = fread(data + length, 1, capacity - length, file);
        length += count;
        if (ferror(file)) { fclose(file); free(data); return wysna_io_error("Failed to read file"); }
        if (feof(file)) break;
        if (length == capacity) {
            if (capacity > SIZE_MAX / 2 - 1) { fclose(file); free(data); return wysna_io_error("File too large"); }
            capacity *= 2;
            data = (char*)wysna_realloc_safe(data, capacity + 1);
        }
    }
    if (fclose(file) != 0) { free(data); return wysna_io_error("Failed to close file"); }
    data[length] = 0;
    size_t offset = 0;
    while (offset < length) {
        size_t step;
        if (!wysna_utf8_valid_codepoint_at_n(data, length, offset, &step, NULL)) {
            free(data); return wysna_io_error("Invalid UTF-8 file");
        }
        offset += step;
    }
    if (length == 0) {
        free(data);
        return wysna_ok(wysna_sso_from_n("", 0));
    }
    if (capacity > length + 256) {
        data = (char*)wysna_realloc_safe(data, length + 1);
    }
    return wysna_ok(wysna_owned_str_n(data, length));
}

static inline WVal wysna_builtin_write_file(WVal path, WVal content) {
#ifdef WYSNA_DENY_CAP_FILESYSTEM_WRITE
    (void)path; (void)content;
    wysna_sandbox_deny("filesystem.write");
    return wysna_io_error("Permission denied");
#endif
    if (wysna_tag(path) != W_STR || wysna_tag(content) != W_STR)
        return wysna_io_error("Invalid arguments");
    char path_buf[7], content_buf[7];
    size_t path_len = 0, content_len = 0;
    const char* path_data = wysna_str_bytes(path, path_buf, &path_len);
    const char* content_data = wysna_str_bytes(content, content_buf, &content_len);
    if (memchr(path_data, 0, path_len))
        return wysna_io_error("Invalid arguments");
    FILE* file = wysna_builtin_fopen(path_data, "wb");
    if (!file) return wysna_io_error("Failed to write file");
    size_t written = fwrite(content_data, 1, content_len, file);
    bool failed = written != content_len || ferror(file);
    if (fclose(file) != 0) failed = true;
    return failed ? wysna_io_error("Failed to write file") : wysna_ok(wysna_unit());
}

/*
 * Propagates an unhandled `Err` out of the program entrypoint.
 *
 * Wysna has no stack unwinding, so a fallible `main` returns its error as a value.
 * Discarding that value would make a failed program indistinguishable from a
 * successful one (exit code 0, no output) to make/CI/shell. The entrypoint
 * therefore reports the error on stderr and signals failure to the caller,
 * which terminates with a non-zero status.
 *
 * Error structs that expose a `message: str` field print that field; anything
 * else prints through the ordinary value formatter.
 */
static inline int wysna_integer_entry_status(int64_t value) {
    if (value >= 0 && value <= 255) return (int)value;
    fprintf(stderr, "[W4003] RuntimeError: main returned an exit status outside 0..255\n");
    return 1;
}

static inline void wysna_report_entrypoint_error(WVal error) {
    if (wysna_hi(error) == WVAL_HI_STRUCT) {
        WStructBuf* st = wysna_as_struct(error);
        if (st && st->name && strcmp(st->name, "IoError") == 0) {
            for (int32_t i = 0; i < st->len; i++) {
                if (st->keys[i] && strcmp(st->keys[i], "message") == 0 && wysna_tag(st->vals[i]) == W_STR) {
                    char msg_buf[7];
                    size_t msg_len = 0;
                    const char* message = wysna_str_bytes(st->vals[i], msg_buf, &msg_len);
                    fprintf(stderr, "[W4003] RuntimeError: %.*s\n", (int)msg_len, message);
                    return;
                }
            }
        }
    }
    char* rendered = wysna_to_cstr(error, false);
    fprintf(stderr, "[W4003] RuntimeError: unhandled error returned by main: %s\n", rendered);
    free(rendered);
}

static inline int wysna_entry_status(WVal value) {
    if (wysna_is_err(value)) {
        wysna_report_entrypoint_error(wysna_as_box(value)->val);
        return 1;
    }
    if (wysna_is_ok(value)) value = wysna_as_box(value)->val;
    return wysna_tag(value) == W_INT ? wysna_integer_entry_status(wysna_as_int(value)) : 0;
}


#ifdef _WIN32
static inline void wysna_init_args(int argc, char** argv) {
    (void)argc; (void)argv;
    typedef LPWSTR* (WINAPI *pfnCommandLineToArgvW)(LPCWSTR, int*);
    HMODULE hShell = LoadLibraryA("shell32.dll");
    pfnCommandLineToArgvW pCmdLineToArgvW = hShell ? (pfnCommandLineToArgvW)GetProcAddress(hShell, "CommandLineToArgvW") : NULL;
    int wysna_argc = 0;
    LPWSTR* wysna_argv = pCmdLineToArgvW ? pCmdLineToArgvW(GetCommandLineW(), &wysna_argc) : NULL;
    if (wysna_argv && wysna_argc > 0) {
        wysna_g_argc = wysna_argc;
        wysna_g_argv = (char**)wysna_alloc_safe(sizeof(char*) * (size_t)(wysna_argc + 1));
        wysna_g_argv_allocated = true;
        for (int i = 0; i < wysna_argc; i++) {
            int len = WideCharToMultiByte(CP_UTF8, 0, wysna_argv[i], -1, NULL, 0, NULL, NULL);
            char* utf8 = (char*)wysna_alloc_safe(len > 0 ? (size_t)len : 1);
            if (len > 0) {
                WideCharToMultiByte(CP_UTF8, 0, wysna_argv[i], -1, utf8, len, NULL, NULL);
            } else {
                utf8[0] = '\0';
            }
            wysna_g_argv[i] = utf8;
        }
        wysna_g_argv[wysna_argc] = NULL;
        LocalFree(wysna_argv);
    } else {
        wysna_g_argc = argc;
        wysna_g_argv = (char**)wysna_alloc_safe(sizeof(char*) * (size_t)(argc + 1));
        wysna_g_argv_allocated = true;
        for (int i = 0; i < argc; i++) {
            int wlen = MultiByteToWideChar(CP_ACP, 0, argv[i], -1, NULL, 0);
            if (wlen > 0) {
                wchar_t* wide = (wchar_t*)wysna_alloc_safe((size_t)wlen * sizeof(wchar_t));
                MultiByteToWideChar(CP_ACP, 0, argv[i], -1, wide, wlen);
                int u8len = WideCharToMultiByte(CP_UTF8, 0, wide, -1, NULL, 0, NULL, NULL);
                char* utf8 = (char*)wysna_alloc_safe(u8len > 0 ? (size_t)u8len : 1);
                if (u8len > 0) {
                    WideCharToMultiByte(CP_UTF8, 0, wide, -1, utf8, u8len, NULL, NULL);
                } else {
                    utf8[0] = '\0';
                }
                wysna_g_argv[i] = utf8;
                free(wide);
            } else {
                size_t blen = strlen(argv[i]);
                char* copy = (char*)wysna_alloc_safe(blen + 1);
                memcpy(copy, argv[i], blen + 1);
                wysna_g_argv[i] = copy;
            }
        }
        wysna_g_argv[argc] = NULL;
    }
}
#else
static inline void wysna_init_args(int argc, char** argv) {
    wysna_g_argc = argc;
    wysna_g_argv = argv;
}
#endif

static inline WVal wysna_builtin_args(void) {
#ifdef WYSNA_DENY_CAP_ARGUMENTS
    wysna_sandbox_deny("arguments");
    return wysna_unit();
#endif
    WListBuf* b = (WListBuf*)wysna_alloc_safe(sizeof(WListBuf));
    b->refcount = 1;
    b->len = (int32_t)wysna_g_argc;
    b->cap = wysna_g_argc > 4 ? (int32_t)wysna_g_argc : 4;
    b->scalar_only = false;
    b->data = (WVal*)wysna_alloc_safe(sizeof(WVal) * b->cap);
    wysna_mem_on_alloc();
    for (int i = 0; i < wysna_g_argc; i++) {
        b->data[i] = wysna_copy_str(wysna_g_argv[i]);
    }
    return wysna_make_hi_ptr(WVAL_HI_LIST, b);
}

static inline WVal wysna_builtin_env(WVal name) {
#ifdef WYSNA_DENY_CAP_ENVIRONMENT
    (void)name;
    wysna_sandbox_deny("environment");
    return wysna_none();
#endif
    if (wysna_tag(name) != W_STR)
        wysna_runtime_fail("W4003", "env() expects a valid environment variable name");
    char name_buf[7];
    size_t name_len = 0;
    const char* name_data = wysna_str_bytes(name, name_buf, &name_len);
    if (memchr(name_data, 0, name_len))
        wysna_runtime_fail("W4003", "env() expects a valid environment variable name");
    char* key = (char*)wysna_alloc_safe(name_len + 1);
    if (name_len) memcpy(key, name_data, name_len);
    key[name_len] = 0;
#ifdef _WIN32
    int key_length = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, key, -1, NULL, 0);
    if (!key_length) { free(key); wysna_runtime_fail("W4003", "environment variable name is not valid UTF-8"); }
    wchar_t* wide_key = (wchar_t*)wysna_alloc_safe((size_t)key_length * sizeof(wchar_t));
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, key, -1, wide_key, key_length)) {
        free(key); free(wide_key); wysna_runtime_fail("W4003", "environment variable name conversion failed");
    }
    free(key);
    SetLastError(ERROR_SUCCESS);
    DWORD required = GetEnvironmentVariableW(wide_key, NULL, 0);
    DWORD error = GetLastError();
    if (required == 0 && error == ERROR_ENVVAR_NOT_FOUND) { free(wide_key); return wysna_none(); }
    if (required == 0 && error == ERROR_SUCCESS) {
        free(wide_key);
        return wysna_enum_payload("Option", "Some", wysna_sso_from_n("", 0));
    }
    if (required == 0 && error != ERROR_SUCCESS) {
        free(wide_key); wysna_runtime_fail("W4003", "unable to read environment variable");
    }
    wchar_t* wide_value = (wchar_t*)wysna_alloc_safe((size_t)(required ? required : 1) * sizeof(wchar_t));
    DWORD copied = required ? GetEnvironmentVariableW(wide_key, wide_value, required) : 0;
    free(wide_key);
    if (required && copied >= required) {
        free(wide_value); wysna_runtime_fail("W4003", "environment variable changed while reading");
    }
    int value_length = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide_value, -1, NULL, 0, NULL, NULL);
    if (!value_length) { free(wide_value); wysna_runtime_fail("W4003", "environment variable is not valid Unicode"); }
    char* copy = (char*)wysna_alloc_safe((size_t)value_length);
    if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide_value, -1, copy, value_length, NULL, NULL)) {
        free(wide_value); free(copy); wysna_runtime_fail("W4003", "environment variable conversion failed");
    }
    free(wide_value);
    return wysna_enum_payload("Option", "Some", wysna_owned_str_n(copy, (size_t)value_length - 1));
#else
    const char* value = getenv(key);
    free(key);
    if (!value) return wysna_none();
    size_t length = strlen(value);
    size_t offset = 0;
    while (offset < length) {
        size_t width;
        if (!wysna_utf8_valid_codepoint_at_n(value, length, offset, &width, NULL))
            wysna_runtime_fail("W4003", "environment variable is not valid UTF-8");
        offset += width;
    }
    if (length <= 6) return wysna_enum_payload("Option", "Some", wysna_sso_from_n(value, length));
    char* copy = (char*)wysna_alloc_safe(length + 1);
    memcpy(copy, value, length + 1);
    return wysna_enum_payload("Option", "Some", wysna_owned_str_n(copy, length));
#endif
}

static inline WVal wysna_builtin_unix_time(void) {
#ifdef WYSNA_DENY_CAP_CLOCK
    wysna_sandbox_deny("clock");
    return wysna_int(0);
#endif
    time_t current = time(NULL);
    if (current == (time_t)-1) wysna_runtime_fail("W4003", "unable to read the system clock");
    return wysna_int((int64_t)current);
}

static inline WVal wysna_builtin_contract_assert(WVal cond, WVal msg) {
    if (wysna_tag(cond) != W_BOOL || !wysna_as_bool(cond)) {
        char msg_buf[7];
        const char* text = (wysna_tag(msg) == W_STR) ? wysna_str_bytes(msg, msg_buf, NULL) : "Contract violation";
        wysna_runtime_fail("W4005", "%s", text);
    }
    return wysna_unit();
}

static inline WVal wysna_builtin_contract_assert_at(WVal cond, WVal msg, const char* file, int32_t line, int32_t col) {
    if (wysna_tag(cond) != W_BOOL || !wysna_as_bool(cond)) {
        wysna_runtime_set_position(file, line, col);
        char msg_buf[7];
        const char* text = (wysna_tag(msg) == W_STR) ? wysna_str_bytes(msg, msg_buf, NULL) : "Contract violation";
        wysna_runtime_fail("W4005", "%s", text);
    }
    return wysna_unit();
}

#endif /* WYSNA_RUNTIME_BUILTINS_H */
