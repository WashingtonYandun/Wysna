/* Internal runtime domain. Include wysna_runtime.h rather than this file. */
#ifndef WYSNA_RUNTIME_BUILTINS_H
#define WYSNA_RUNTIME_BUILTINS_H
#ifndef WYSNA_RUNTIME_H
#error "Include wysna_runtime.h before internal runtime headers"
#endif

/* Standard Built-ins */
static inline WVal wysna_builtin_print(int32_t argc, const WVal* argv) {
    for (int32_t i = 0; i < argc; i++) {
        if (i > 0) fputc(' ', stdout);
        WVal value = argv[i];
        while (value.tag == W_ENUM && strcmp(value.as.en.enum_name, "Option") == 0 &&
               strcmp(value.as.en.variant, "Some") == 0 && value.as.en.box) value = value.as.en.box->val;
        if (value.tag == W_STR) fwrite(value.as.string.data, 1, value.as.string.length, stdout);
        else { char* s = wysna_to_cstr(value, false); fputs(s, stdout); free(s); }
    }
    fputc('\n', stdout);
    fflush(stdout);
    return wysna_unit();
}

static inline WVal wysna_builtin_eprint(int32_t argc, const WVal* argv) {
    for (int32_t i = 0; i < argc; i++) {
        if (i > 0) fputc(' ', stderr);
        char* s = wysna_to_cstr(argv[i], false);
        const char* metrics = getenv("WYSNA_COW_METRICS");
        if (metrics && strcmp(metrics, "1") == 0 && argc == 1 && argv[i].tag == W_STR &&
            strncmp(s, "__COW_CHECKPOINT__:", 19) == 0) {
            const char* label = s + 19;
            fprintf(stderr, "[WYSNA_COW_CHECKPOINT:%s] detach_list=%" PRId64 " detach_dict=%" PRId64 " detach_struct=%" PRId64 " copied_slots=%" PRId64 " copied_bytes=%" PRId64 " allocated_bytes=%" PRId64 " allocs=%" PRId64,
                    label, wysna_g_cow_detach_list, wysna_g_cow_detach_dict, wysna_g_cow_detach_struct, wysna_g_cow_copied_slots, wysna_g_cow_copied_bytes, wysna_g_cow_allocated_bytes, wysna_g_cow_alloc_count);
        } else {
            if (argv[i].tag == W_STR) fwrite(argv[i].as.string.data, 1, argv[i].as.string.length, stderr);
            else fputs(s, stderr);
        }
        free(s);
    }
    fputc('\n', stderr);
    fflush(stderr);
    return wysna_unit();
}

static inline WVal wysna_builtin_exit(WVal code) {
    int c = (code.tag == W_INT) ? (int)code.as.i : 0;
    wysna_runtime_cleanup();
    exit(c);
    return wysna_unit();
}

static inline WVal wysna_builtin_len(WVal v) {
    if (v.tag == W_LIST) return wysna_int(v.as.list->len);
    if (v.tag == W_DICT) return wysna_int(v.as.dict->len);
    if (v.tag == W_TUPLE) return wysna_int(v.as.tuple->len);
    if (v.tag == W_STR) return wysna_int(wysna_string_length(v));
    return wysna_int(0);
}

static inline WVal wysna_builtin_str(WVal v) {
    if (v.tag == W_STR) return wysna_clone(v);
    return wysna_owned_str(wysna_to_cstr(v, false));
}

static inline void wysna_conversion_error(const char* kind) {
    fprintf(stderr, "[W4003] RuntimeError: Invalid %s conversion\n", kind);
    exit(1);
}

static inline const char* wysna_conversion_end(const char* end) {
    while (*end && isspace((unsigned char)*end)) end++;
    return end;
}

static inline WVal wysna_builtin_int(WVal v) {
    if (v.tag == W_BOOL) return wysna_int(v.as.b ? 1 : 0);
    if (v.tag == W_INT) return v;
    if (v.tag == W_FLOAT) {
        if (!isfinite(v.as.f) || v.as.f < -9223372036854775808.0 || v.as.f >= 9223372036854775808.0)
            wysna_conversion_error("int");
        return wysna_int((int64_t)v.as.f);
    }
    if (v.tag == W_STR && !memchr(v.as.string.data, 0, v.as.string.length)) {
        char* end; errno = 0;
        int64_t value = strtoll(v.as.string.data, &end, 10);
        if (errno != ERANGE && end != v.as.string.data && *wysna_conversion_end(end) == 0) return wysna_int(value);
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
    if (v.tag == W_BOOL) return wysna_float(v.as.b ? 1.0 : 0.0);
    if (v.tag == W_FLOAT) return v;
    if (v.tag == W_INT) return wysna_float((double)v.as.i);
    if (v.tag == W_STR && wysna_float_text_valid(v.as.string.data, v.as.string.length)) {
        char* end;
        double value = strtod(v.as.string.data, &end);
        if (end != v.as.string.data && *wysna_conversion_end(end) == 0 && !strstr(v.as.string.data, "0x") && !strstr(v.as.string.data, "0X"))
            return wysna_float(value);
    }
    wysna_conversion_error("float");
    return wysna_unit();
}

static inline WVal wysna_builtin_ord(WVal v) {
    if (v.tag != W_STR || v.as.string.length == 0) {
        fprintf(stderr, "[W4003] RuntimeError: ord() expects non-empty str\n");
        exit(1);
    }
    size_t slen = 0;
    int64_t cp = 0;
    if (!wysna_utf8_valid_codepoint_at_n(v.as.string.data, v.as.string.length, 0, &slen, &cp)) {
        return wysna_int((unsigned char)v.as.string.data[0]);
    }
    return wysna_int(cp);
}

static inline WVal wysna_builtin_chr(WVal v) {
    if (v.tag != W_INT) {
        fprintf(stderr, "[W4003] RuntimeError: chr() expects int\n");
        exit(1);
    }
    int64_t cp = v.as.i;
    if (cp < 0 || cp > 0x10FFFF || (cp >= 0xD800 && cp <= 0xDFFF)) {
        fprintf(stderr, "[W4003] RuntimeError: chr() arg not in valid Unicode scalar range: %" PRId64 "\n", cp);
        exit(1);
    }
    if (cp < 0x80) return wysna_ascii_string((uint8_t)cp);
    char* buf = (char*)wysna_alloc_safe(5);
    if (cp < 0x800) {
        buf[0] = (char)(0xC0 | (cp >> 6));
        buf[1] = (char)(0x80 | (cp & 0x3F));
        buf[2] = '\0';
    } else if (cp < 0x10000) {
        buf[0] = (char)(0xE0 | (cp >> 12));
        buf[1] = (char)(0x80 | ((cp >> 6) & 0x3F));
        buf[2] = (char)(0x80 | (cp & 0x3F));
        buf[3] = '\0';
    } else {
        buf[0] = (char)(0xF0 | (cp >> 18));
        buf[1] = (char)(0x80 | ((cp >> 12) & 0x3F));
        buf[2] = (char)(0x80 | ((cp >> 6) & 0x3F));
        buf[3] = (char)(0x80 | (cp & 0x3F));
        buf[4] = '\0';
    }
    return wysna_owned_str_n(buf, cp < 0x800 ? 2 : cp < 0x10000 ? 3 : 4);
}

static inline WVal wysna_builtin_slice(WVal s, WVal start, WVal end) {
    if (s.tag == W_LIST) {
        /* SPEC 11.1: slice() is polymorphic over str and list[T]. */
        WListBuf* b = s.as.list;
        int64_t st = start.as.i < 0 ? 0 : start.as.i;
        int64_t en = end.as.i > b->len ? b->len : end.as.i;
        if (en <= st) return wysna_make_list(0, NULL);
        return wysna_make_list((int32_t)(en - st), b->data + st);
    }
    if (s.tag != W_STR) {
        fprintf(stderr, "[W4003] RuntimeError: slice() expects str or list\n");
        exit(1);
    }
    int64_t total_cp = wysna_string_length(s);
    int64_t st = start.as.i < 0 ? 0 : start.as.i;
    int64_t en = end.as.i > total_cp ? total_cp : end.as.i;
    if (en <= st || st >= total_cp) return wysna_str("");
    size_t st_off = 0, dummy_len = 0, en_off = 0;
    wysna_string_offset(s, st, &st_off, &dummy_len);
    if (en == total_cp) {
        en_off = s.as.string.length;
    } else {
        wysna_string_offset(s, en, &en_off, &dummy_len);
    }
    size_t blen = en_off - st_off;
    char* buf = (char*)wysna_alloc_safe(blen + 1);
    memcpy(buf, s.as.string.data + st_off, blen);
    buf[blen] = '\0';
    return wysna_owned_str_n(buf, blen);
}

/* A two-pass string builder: one allocation and one copy per input byte. */
static inline WVal wysna_builtin_join_strings(WVal parts, WVal separator) {
    if (parts.tag != W_LIST || separator.tag != W_STR)
        wysna_runtime_fail("W4003", "join_strings requires list[str] and str");
    size_t length = 0;
    for (int32_t i = 0; i < parts.as.list->len; i++) {
        WVal part = parts.as.list->data[i];
        if (part.tag != W_STR) wysna_runtime_fail("W4003", "join_strings requires string elements");
        size_t extra = i ? separator.as.string.length : 0;
        if (part.as.string.length > SIZE_MAX - length - 1 || extra > SIZE_MAX - length - part.as.string.length - 1)
            wysna_runtime_fail("W4003", "Joined string is too large");
        length += extra + part.as.string.length;
    }
    char* data = (char*)wysna_alloc_safe(length + 1);
    size_t offset = 0;
    for (int32_t i = 0; i < parts.as.list->len; i++) {
        if (i) { memcpy(data + offset, separator.as.string.data, separator.as.string.length); offset += separator.as.string.length; }
        WVal part = parts.as.list->data[i];
        memcpy(data + offset, part.as.string.data, part.as.string.length); offset += part.as.string.length;
    }
    data[length] = 0;
    return wysna_owned_str_n(data, length);
}

static inline WVal wysna_builtin_bytes(WVal s) {
    if (s.tag != W_STR) {
        fprintf(stderr, "[W4003] RuntimeError: bytes() expects str\n");
        exit(1);
    }
    size_t slen = s.as.string.length;
    WListBuf* b = (WListBuf*)wysna_alloc_safe(sizeof(WListBuf));
    b->refcount = 1;
    b->len = (int32_t)slen;
    b->cap = slen > 4 ? (int32_t)slen : 4;
    b->data = (WVal*)wysna_alloc_safe(sizeof(WVal) * b->cap);
    wysna_mem_on_alloc();
    for (size_t i = 0; i < slen; i++) {
        b->data[i] = wysna_int((int64_t)(unsigned char)s.as.string.data[i]);
    }
    WVal v; v.tag = W_LIST; v.as.list = b; return v;
}

static inline WVal wysna_builtin_enumerate(WVal list_val) {
    if (list_val.tag != W_LIST) {
        fprintf(stderr, "[W4003] RuntimeError: enumerate() expects list\n");
        exit(1);
    }
    WListBuf* src = list_val.as.list;
    WListBuf* b = (WListBuf*)wysna_alloc_safe(sizeof(WListBuf));
    b->refcount = 1;
    b->len = src->len;
    b->cap = src->len > 4 ? src->len : 4;
    b->data = (WVal*)wysna_alloc_safe(sizeof(WVal) * b->cap);
    wysna_mem_on_alloc();
    for (int32_t i = 0; i < src->len; i++) {
        WVal pair[2] = { wysna_int(i), src->data[i] };
        b->data[i] = wysna_make_tuple(2, pair);
    }
    WVal v; v.tag = W_LIST; v.as.list = b; return v;
}

static inline WVal wysna_builtin_dict_items(WVal dict_val) {
    if (dict_val.tag != W_DICT) {
        fprintf(stderr, "[W4003] RuntimeError: dict_items() expects dict\n");
        exit(1);
    }
    WDictBuf* d = dict_val.as.dict;
    WListBuf* b = (WListBuf*)wysna_alloc_safe(sizeof(WListBuf));
    b->refcount = 1;
    b->len = d->len;
    b->cap = d->len > 4 ? d->len : 4;
    b->data = (WVal*)wysna_alloc_safe(sizeof(WVal) * b->cap);
    wysna_mem_on_alloc();
    for (int32_t i = 0; i < d->len; i++) {
        WVal pair[2] = { d->keys[i], d->vals[i] };
        b->data[i] = wysna_make_tuple(2, pair);
    }
    WVal v; v.tag = W_LIST; v.as.list = b; return v;
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
    if (path.tag != W_STR || memchr(path.as.string.data, 0, path.as.string.length)) return wysna_io_error("Invalid path");
    FILE* file = wysna_builtin_fopen(path.as.string.data, "rb");
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
    return wysna_ok(wysna_owned_str_n(data, length));
}

static inline WVal wysna_builtin_write_file(WVal path, WVal content) {
    if (path.tag != W_STR || content.tag != W_STR || memchr(path.as.string.data, 0, path.as.string.length))
        return wysna_io_error("Invalid arguments");
    FILE* file = wysna_builtin_fopen(path.as.string.data, "wb");
    if (!file) return wysna_io_error("Failed to write file");
    size_t written = fwrite(content.as.string.data, 1, content.as.string.length, file);
    bool failed = written != content.as.string.length || ferror(file);
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
static inline void wysna_report_entrypoint_error(WVal error) {
    const char* message = NULL;
    if (error.tag == W_STRUCT && strcmp(error.as.st->name, "IoError") == 0) {
        for (int32_t i = 0; i < error.as.st->len; i++) {
            if (strcmp(error.as.st->keys[i], "message") == 0 && error.as.st->vals[i].tag == W_STR) {
                message = error.as.st->vals[i].as.string.data;
                break;
            }
        }
    }
    if (message) {
        fprintf(stderr, "[W4003] RuntimeError: %s\n", message);
        return;
    }
    char* rendered = wysna_to_cstr(error, false);
    fprintf(stderr, "[W4003] RuntimeError: unhandled error returned by main: %s\n", rendered);
    free(rendered);
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
    WListBuf* b = (WListBuf*)wysna_alloc_safe(sizeof(WListBuf));
    b->refcount = 1;
    b->len = (int32_t)wysna_g_argc;
    b->cap = wysna_g_argc > 4 ? (int32_t)wysna_g_argc : 4;
    b->data = (WVal*)wysna_alloc_safe(sizeof(WVal) * b->cap);
    wysna_mem_on_alloc();
    for (int i = 0; i < wysna_g_argc; i++) {
        b->data[i] = wysna_copy_str(wysna_g_argv[i]);
    }
    WVal v; v.tag = W_LIST; v.as.list = b; return v;
}

static inline WVal wysna_builtin_env(WVal name) {
    if (name.tag != W_STR || memchr(name.as.string.data, 0, name.as.string.length))
        wysna_runtime_fail("W4003", "env() expects a valid environment variable name");
    char* key = (char*)wysna_alloc_safe(name.as.string.length + 1);
    memcpy(key, name.as.string.data, name.as.string.length);
    key[name.as.string.length] = 0;
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
        return wysna_enum_payload("Option", "Some", wysna_str(""));
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
    char* copy = (char*)wysna_alloc_safe(length + 1);
    memcpy(copy, value, length + 1);
    return wysna_enum_payload("Option", "Some", wysna_owned_str_n(copy, length));
#endif
}

static inline WVal wysna_builtin_unix_time(void) {
    time_t current = time(NULL);
    if (current == (time_t)-1) wysna_runtime_fail("W4003", "unable to read the system clock");
    return wysna_int((int64_t)current);
}

#endif /* WYSNA_RUNTIME_BUILTINS_H */
