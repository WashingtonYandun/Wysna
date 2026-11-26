/* Native project test execution and machine reports; selection stays in Wysna. */
#ifndef WYSNA_SDK_PROJECT_TESTS_H
#define WYSNA_SDK_PROJECT_TESTS_H
#ifdef _WIN32
#include <direct.h>
#endif
#define SDK_TEST_OUTPUT_LIMIT 1048576
#define SDK_TEST_CASE_LIMIT 4096

typedef struct {
    const char *name, *entry, *sha;
    int64_t timeout, duration, compile_duration, run_duration;
    int exit_code, has_exit, output_limited;
    const char *status, *failure_kind;
    char *out, *err, *diagnostics;
    size_t out_len, err_len;
} SdkProjectTest;

typedef struct {
    const char *name, *root, *manifest, *manifest_sha, *lock_sha, *revision, *hash;
    const char *report_json, *report_junit, *json;
    int list_only;
    SdkProjectTest* cases;
    int count;
} SdkProjectTestPlan;

static FILE* sdk_test_open(const char* path, const char* mode) {
#ifdef _WIN32
    wchar_t* wide = wysna_host_process_wide(path);
    FILE* file = wide ? _wfopen(wide, mode[0] == 'r' ? L"rb" : L"wb") : NULL;
    free(wide); return file;
#else
    return fopen(path, mode);
#endif
}

static void sdk_test_hash(const char* data, size_t length, char output[65]) {
    WysnaSha256Context context;
    uint8_t digest[32];
    const char* digits = "0123456789abcdef";
    wysna_sha256_init(&context);
    wysna_sha256_update(&context, (const uint8_t*)(length ? data : ""), length);
    wysna_sha256_final(&context, digest);
    for (int index = 0; index < 32; index++) {
        output[index * 2] = digits[digest[index] >> 4];
        output[index * 2 + 1] = digits[digest[index] & 15];
    }
    output[64] = 0;
}

static void sdk_test_text(FILE* file, const char* data, size_t length, int mode) {
    int xml = mode == 1;
    if (mode == 0) fputc('"', file);
    for (size_t index = 0; index < length;) {
        unsigned char ch = (unsigned char)data[index];
        size_t width = 1;
        int64_t cp = ch;
        if (!wysna_utf8_valid_codepoint_at_n(data, length, index, &width, &cp)) {
            size_t expected = ch >= 0xc2 && ch <= 0xdf ? 2 : ch >= 0xe0 && ch <= 0xef ? 3 : ch >= 0xf0 && ch <= 0xf4 ? 4 : 1;
            width = 1;
            while (width < expected && index + width < length) {
                unsigned char next = (unsigned char)data[index + width];
                if (next < 0x80 || next > 0xbf || (width == 1 &&
                    ((ch == 0xe0 && next < 0xa0) || (ch == 0xed && next > 0x9f) ||
                     (ch == 0xf0 && next < 0x90) || (ch == 0xf4 && next > 0x8f)))) break;
                width++;
            }
            fputs("\xef\xbf\xbd", file);
        } else if (xml && ((cp < 32 && cp != 9 && cp != 10 && cp != 13) || cp == 0xfffe || cp == 0xffff)) {
            fputs("\xef\xbf\xbd", file);
        } else if (xml && ch == '&') fputs("&amp;", file);
        else if (xml && ch == '<') fputs("&lt;", file);
        else if (xml && ch == '>') fputs("&gt;", file);
        else if (xml && ch == '"') fputs("&quot;", file);
        else if (xml && ch == '\'') fputs("&apos;", file);
        else if (!xml && (ch == '"' || ch == '\\')) fprintf(file, "\\%c", ch);
        else if (!xml && ch < 32) fprintf(file, "\\u%04x", ch);
        else fwrite(data + index, 1, width, file);
        index += width;
    }
    if (mode == 0) fputc('"', file);
}

static void sdk_test_string(FILE* file, const char* value) {
    sdk_test_text(file, value ? value : "", value ? strlen(value) : 0, 0);
}

static int sdk_test_read(const char* path, char** data, size_t* length) {
    FILE* file = sdk_test_open(path, "rb");
    if (!file) return 0;
    *data = malloc(SDK_TEST_OUTPUT_LIMIT + 1);
    if (!*data) { fclose(file); return 0; }
    *length = fread(*data, 1, SDK_TEST_OUTPUT_LIMIT, file);
    int result = ferror(file) ? 0 : fgetc(file) == EOF ? 1 : 2;
    (*data)[*length] = 0;
    fclose(file); return result;
}

static int sdk_test_chdir(const char* path) {
#ifdef _WIN32
    wchar_t* wide = wysna_host_process_wide(path);
    int result = wide && _wchdir(wide) == 0;
    free(wide); return result;
#else
    return chdir(path) == 0;
#endif
}

static int sdk_plan_decode(char* text) {
    size_t write = 0;
    for (size_t read = 0; text[read]; read++) {
        if (text[read] != '%') { text[write++] = text[read]; continue; }
        if (!text[read + 1] || !text[read + 2]) return 0;
        const char* key = text + read + 1;
        if (strncmp(key, "25", 2) == 0) text[write++] = '%';
        else if (strncmp(key, "09", 2) == 0) text[write++] = '\t';
        else if (strncmp(key, "0A", 2) == 0) text[write++] = '\n';
        else if (strncmp(key, "0D", 2) == 0) text[write++] = '\r';
        else return 0;
        read += 2;
    }
    text[write] = 0; return 1;
}

typedef struct {
    int enabled;
    int allow_tier1;
    char bin_dir[SDK_PATH_CAP];
    char rev_dir[SDK_PATH_CAP];
    char runtime_sha[65];
} SdkTestCache;

static void sdk_test_hash_str(WysnaSha256Context* context, const char* value) {
    const char* text = value ? value : "";
    wysna_sha256_update(context, (const uint8_t*)text, strlen(text) + 1);
}

static int sdk_test_hash_file(WysnaSha256Context* context, const char* path) {
    FILE* file = sdk_test_open(path, "rb");
    if (!file) return 0;
    uint8_t buffer[8192];
    size_t read_len = 0;
    while ((read_len = fread(buffer, 1, sizeof(buffer), file)) != 0) {
        wysna_sha256_update(context, buffer, read_len);
    }
    int ok = !ferror(file);
    fclose(file);
    return ok;
}

static void sdk_test_hash_final_hex(WysnaSha256Context* context, char output[65]) {
    uint8_t digest[32];
    const char* digits = "0123456789abcdef";
    wysna_sha256_final(context, digest);
    for (int index = 0; index < 32; index++) {
        output[index * 2] = digits[digest[index] >> 4];
        output[index * 2 + 1] = digits[digest[index] & 15];
    }
    output[64] = 0;
}

static int sdk_test_path_within_root(const char* path, const char* root) {
    if (!path || !root || !root[0]) return 0;
    size_t root_len = strlen(root);
    while (root_len > 1 && (root[root_len - 1] == '/' || root[root_len - 1] == '\\')) root_len--;
    if (strncmp(path, root, root_len) != 0) return 0;
    char boundary = path[root_len];
    return boundary == '\0' || boundary == '/' || boundary == '\\';
}

static const char* sdk_test_relative_to_root(const char* path, const char* root) {
    if (!path || !root || !root[0]) return path ? path : "";
    size_t root_len = strlen(root);
    while (root_len > 1 && (root[root_len - 1] == '/' || root[root_len - 1] == '\\')) root_len--;
    if (strncmp(path, root, root_len) != 0) return path;
    if (path[root_len] == '\0') return "";
    if (path[root_len] == '/' || path[root_len] == '\\') return path + root_len + 1;
    return path;
}

static int sdk_test_is_hex64(const char* value, size_t length) {
    if (!value || length < 64) return 0;
    for (size_t index = 0; index < 64; index++) {
        char ch = value[index];
        if (!((ch >= '0' && ch <= '9') || (ch >= 'a' && ch <= 'f'))) return 0;
    }
    for (size_t index = 64; index < length; index++) {
        if (value[index] != '\n' && value[index] != '\r') return 0;
    }
    return 1;
}

static void sdk_test_cache_init(SdkTestCache* cache, const SdkInvocation* invocation,
                                const SdkOptions* options, const SdkProjectTestPlan* plan) {
    memset(cache, 0, sizeof(*cache));
    char* disabled = sdk_environment_value("WYSNA_DISABLE_TEST_CACHE");
    int is_disabled = disabled && strcmp(disabled, "1") == 0;
    free(disabled);
    if (is_disabled) return;
    if (options->wasi || options->link_count > 0 || options->include_count > 0 ||
        options->library_dir_count > 0 || options->library_count > 0) return;
    char base[SDK_PATH_CAP], cache_root[SDK_PATH_CAP];
    if (!sdk_resolve_global_cache(base) || !sdk_join(cache_root, base, "project-test-cache/v1") ||
        !sdk_join(cache->bin_dir, cache_root, "bin") || !sdk_join(cache->rev_dir, cache_root, "rev") ||
        !sdk_mkdir_p(cache->bin_dir) || !sdk_mkdir_p(cache->rev_dir)) {
        memset(cache, 0, sizeof(*cache));
        return;
    }

    const char* profile = options->profile ? options->profile : "release";
    const char* archive_rel = "lib/libwysna_runtime_release.a";
    if (strcmp(profile, "dev") == 0) archive_rel = "lib/libwysna_runtime_dev.a";
    else if (strcmp(profile, "size") == 0) archive_rel = "lib/libwysna_runtime_size.a";

    WysnaSha256Context context;
    wysna_sha256_init(&context);
    sdk_test_hash_str(&context, WYSNA_NATIVE_VERSION);
    sdk_test_hash_str(&context, WYSNA_NATIVE_TARGET);
    sdk_test_hash_str(&context, profile);

    char asset_path[SDK_PATH_CAP];
    if (sdk_join(asset_path, invocation->root, archive_rel) && sdk_is_file(asset_path)) {
        sdk_test_hash_str(&context, "archive:1");
        if (!sdk_test_hash_file(&context, asset_path)) { memset(cache, 0, sizeof(*cache)); return; }
    } else {
        sdk_test_hash_str(&context, "archive:0");
    }
    if (sdk_join(asset_path, invocation->root, "runtime/wysna_runtime.h") && sdk_is_file(asset_path)) {
        sdk_test_hash_str(&context, "header:1");
        if (!sdk_test_hash_file(&context, asset_path)) { memset(cache, 0, sizeof(*cache)); return; }
    } else {
        sdk_test_hash_str(&context, "header:0");
    }
    if (sdk_join(asset_path, invocation->root, "native-sdk.json") && sdk_is_file(asset_path)) {
        sdk_test_hash_str(&context, "meta:1");
        if (!sdk_test_hash_file(&context, asset_path)) { memset(cache, 0, sizeof(*cache)); return; }
    } else {
        sdk_test_hash_str(&context, "meta:0");
    }
    sdk_test_hash_final_hex(&context, cache->runtime_sha);

    cache->enabled = 1;
    if (plan->revision && plan->revision[0] && plan->root && plan->root[0] &&
        (!options->project_src_root || !options->project_src_root[0] ||
         sdk_test_path_within_root(options->project_src_root, plan->root))) {
        int deps_confined = 1;
        for (int index = 0; index < options->dep_count; index++) {
            if (!sdk_test_path_within_root(options->dep_roots[index], plan->root)) {
                deps_confined = 0;
                break;
            }
        }
        cache->allow_tier1 = deps_confined;
    }
}

static void sdk_test_cache_rev_key(const SdkTestCache* cache, const SdkOptions* options,
                                   const SdkProjectTestPlan* plan, const SdkProjectTest* record,
                                   char output[65]) {
    WysnaSha256Context context;
    wysna_sha256_init(&context);
    sdk_test_hash_str(&context, "wysna-project-test-rev/1");
    sdk_test_hash_str(&context, plan->revision);
    sdk_test_hash_str(&context, sdk_test_relative_to_root(record->entry, plan->root));
    sdk_test_hash_str(&context, record->sha);
    sdk_test_hash_str(&context, options->profile ? options->profile : "release");
    sdk_test_hash_str(&context, options->sandbox ? "1" : "0");
    sdk_test_hash_str(&context, options->allow_caps);
    sdk_test_hash_str(&context, options->deny_caps);
    sdk_test_hash_str(&context, cache->runtime_sha);
    sdk_test_hash_str(&context, sdk_test_relative_to_root(options->project_src_root, plan->root));
    for (int index = 0; index < options->dep_count; index++) {
        sdk_test_hash_str(&context, options->dep_aliases[index]);
        sdk_test_hash_str(&context, sdk_test_relative_to_root(options->dep_roots[index], plan->root));
    }
    sdk_test_hash_final_hex(&context, output);
}

static int sdk_test_cache_c99_key(const SdkTestCache* cache, const SdkOptions* options,
                                  const char* generated_path, char output[65]) {
    WysnaSha256Context context;
    wysna_sha256_init(&context);
    sdk_test_hash_str(&context, "wysna-project-test-bin/1");
    sdk_test_hash_str(&context, options->profile ? options->profile : "release");
    sdk_test_hash_str(&context, options->sandbox ? "1" : "0");
    sdk_test_hash_str(&context, options->allow_caps);
    sdk_test_hash_str(&context, options->deny_caps);
    sdk_test_hash_str(&context, cache->runtime_sha);
    if (!sdk_test_hash_file(&context, generated_path)) return 0;
    sdk_test_hash_final_hex(&context, output);
    return 1;
}

static int sdk_test_cache_bin_path(const SdkTestCache* cache, const char* c99_key, char output[SDK_PATH_CAP]) {
    char filename[80];
#ifdef _WIN32
    snprintf(filename, sizeof(filename), "%s.exe", c99_key);
#else
    snprintf(filename, sizeof(filename), "%s", c99_key);
#endif
    return sdk_join(output, cache->bin_dir, filename);
}

static int sdk_test_cache_lookup_rev(const SdkTestCache* cache, const char* rev_key, char bin_path[SDK_PATH_CAP]) {
    char ref_name[72], ref_path[SDK_PATH_CAP];
    snprintf(ref_name, sizeof(ref_name), "%s.ref", rev_key);
    if (!sdk_join(ref_path, cache->rev_dir, ref_name)) return 0;
    FILE* file = sdk_test_open(ref_path, "rb");
    if (!file) return 0;
    char content[68];
    size_t read_len = fread(content, 1, sizeof(content) - 1, file);
    int eof_ok = !ferror(file) && fgetc(file) == EOF;
    fclose(file);
    content[read_len] = 0;
    if (!eof_ok || !sdk_test_is_hex64(content, read_len)) return 0;
    content[64] = 0;
    return sdk_test_cache_bin_path(cache, content, bin_path) && sdk_is_file(bin_path);
}

static void sdk_test_cache_store_rev(const SdkTestCache* cache, const char* rev_key, const char* c99_key) {
    char ref_name[72], ref_path[SDK_PATH_CAP], tmp_dir[SDK_PATH_CAP], staged[SDK_PATH_CAP];
    snprintf(ref_name, sizeof(ref_name), "%s.ref", rev_key);
    if (!sdk_join(ref_path, cache->rev_dir, ref_name)) return;
    if (!sdk_temp(tmp_dir, cache->rev_dir)) return;
    if (sdk_join(staged, tmp_dir, "ref")) {
        FILE* file = sdk_test_open(staged, "wb");
        if (file) {
            size_t wrote = fwrite(c99_key, 1, 64, file);
            int ok = wrote == 64 && !ferror(file);
            if (fclose(file)) ok = 0;
            if (ok) (void)sdk_publish(staged, ref_path);
        }
    }
    (void)sdk_remove_tree(tmp_dir);
}

static int sdk_test_cache_store_bin(const SdkTestCache* cache, const char* c99_key,
                                    const char* source_product, char bin_path[SDK_PATH_CAP]) {
    if (!sdk_test_cache_bin_path(cache, c99_key, bin_path)) return 0;
    if (sdk_is_file(bin_path)) return 1;
    char tmp_dir[SDK_PATH_CAP], staged[SDK_PATH_CAP];
    if (!sdk_temp(tmp_dir, cache->bin_dir)) return 0;
    int stored = 0;
    if (sdk_join(staged, tmp_dir, "bin")) {
        FILE* src = sdk_test_open(source_product, "rb");
        FILE* dst = src ? sdk_test_open(staged, "wb") : NULL;
        int ok = src && dst;
        uint8_t buffer[16384];
        size_t read_len = 0;
        while (ok && (read_len = fread(buffer, 1, sizeof(buffer), src)) != 0) {
            if (fwrite(buffer, 1, read_len, dst) != read_len) ok = 0;
        }
        if (src && ferror(src)) ok = 0;
        if (dst && ferror(dst)) ok = 0;
        if (src) fclose(src);
        if (dst && fclose(dst)) ok = 0;
#ifndef _WIN32
        if (ok && chmod(staged, 0755) != 0) ok = 0;
#endif
        if (ok) {
            if (sdk_is_file(bin_path) || sdk_publish(staged, bin_path) || sdk_is_file(bin_path)) {
                stored = sdk_is_file(bin_path);
            }
        }
    }
    (void)sdk_remove_tree(tmp_dir);
    return stored;
}

static void sdk_project_test_case(SdkInvocation* invocation, const SdkOptions* options,
                                  const SdkProjectTestPlan* plan, const SdkTestCache* cache,
                                  SdkProjectTest* result) {
    result->status = "failed"; result->failure_kind = "infrastructure_error";
    int64_t started = wysna_host_monotonic_ms_internal();
    char out_path[SDK_PATH_CAP], err_path[SDK_PATH_CAP], diag_path[SDK_PATH_CAP];
    char fixture[SDK_PATH_CAP], cwd[SDK_PATH_CAP], exec_path[SDK_PATH_CAP], cached_bin[SDK_PATH_CAP];
    char rev_key[65], c99_key[65];
    char* previous_fixture = NULL;
    int changed_directory = 0, fixture_set = 0, have_rev_key = 0;
    exec_path[0] = 0;
    if (!sdk_temp(invocation->workspace, NULL) || !sdk_join(out_path, invocation->workspace, "compiler.out") ||
        !sdk_join(err_path, invocation->workspace, "compiler.err") || !sdk_join(diag_path, invocation->workspace, "diagnostics.json") ||
        !sdk_join(fixture, invocation->workspace, "fixture") || !sdk_mkdir_p(fixture)) goto cleanup;
    SdkOptions compile = *options;
    compile.test_diagnostics = 1;
    compile.test_deadline = started + result->timeout;
    int64_t compile_started = wysna_host_monotonic_ms_internal();
    if (cache && cache->enabled && cache->allow_tier1) {
        sdk_test_cache_rev_key(cache, options, plan, result, rev_key);
        have_rev_key = 1;
        if (sdk_test_cache_lookup_rev(cache, rev_key, cached_bin)) {
            if (sdk_copy(exec_path, cached_bin)) {
                result->compile_duration = wysna_host_monotonic_ms_internal() - compile_started;
            }
        }
    }
    if (!exec_path[0]) {
        SdkStdioCapture capture;
        if (!sdk_stdio_capture(&capture, out_path, err_path)) goto cleanup;
        int rc = sdk_compile_source(invocation, &compile, "--emit-c-mir", result->entry, 1);
        if (!rc) {
            int have_c99_key = cache && cache->enabled && sdk_test_cache_c99_key(cache, options, invocation->generated, c99_key);
            if (have_c99_key && sdk_test_cache_bin_path(cache, c99_key, cached_bin) && sdk_is_file(cached_bin) &&
                sdk_copy(exec_path, cached_bin)) {
                if (have_rev_key) sdk_test_cache_store_rev(cache, rev_key, c99_key);
            } else {
                rc = sdk_build(invocation, &compile);
                if (!rc) {
                    if (have_c99_key && sdk_test_cache_store_bin(cache, c99_key, invocation->product, cached_bin) &&
                        sdk_copy(exec_path, cached_bin)) {
                        if (have_rev_key) sdk_test_cache_store_rev(cache, rev_key, c99_key);
                    } else if (!sdk_copy(exec_path, invocation->product)) {
                        rc = 1;
                    }
                }
            }
        }
        result->compile_duration = wysna_host_monotonic_ms_internal() - compile_started;
        sdk_stdio_restore(&capture);
        if (rc == 124 || (rc != 130 && wysna_host_monotonic_ms_internal() >= compile.test_deadline)) {
            result->status = "timed_out"; result->failure_kind = "timed_out";
            goto cleanup;
        }
        if (rc) {
            result->status = rc == 130 ? "cancelled" : "failed";
            result->failure_kind = rc == 130 ? "cancelled" : "compile_error";
            result->has_exit = 1; result->exit_code = rc;
            (void)sdk_test_read(out_path, &result->out, &result->out_len);
            (void)sdk_test_read(err_path, &result->err, &result->err_len);
            size_t diagnostic_size = 0;
            if (sdk_test_read(diag_path, &result->diagnostics, &diagnostic_size) != 1 || diagnostic_size < 2 ||
                result->diagnostics[0] != '[' || result->diagnostics[diagnostic_size - 1] != ']') {
                free(result->diagnostics); result->diagnostics = NULL;
            }
            goto cleanup;
        }
    }
    if (!exec_path[0] || !sdk_absolute(cwd, ".", 1) || !sdk_test_chdir(plan->root)) goto cleanup;
    changed_directory = 1;
    previous_fixture = sdk_environment_value("WYSNA_TEST_TMPDIR");
    if (!sdk_environment("WYSNA_TEST_TMPDIR", fixture)) goto cleanup;
    fixture_set = 1;
    void* command = sdk_buffer(exec_path, strlen(exec_path));
    void* arguments = wysna_host_buffer_new();
    void* out = wysna_host_buffer_new();
    void* err = wysna_host_buffer_new();
    int64_t remaining = compile.test_deadline - wysna_host_monotonic_ms_internal();
    int64_t run_started = wysna_host_monotonic_ms_internal();
    int64_t code = wysna_host_process_capture_cancellable(command, arguments, remaining > 0 ? remaining : 1,
                                                          SDK_TEST_OUTPUT_LIMIT, out, err, &sdk_cancellation);
    int64_t host_error = wysna_host_last_error();
    result->run_duration = wysna_host_monotonic_ms_internal() - run_started;
    WysnaHostEntry* streams[2] = {wysna_host_find(out, 1), wysna_host_find(err, 1)};
    char** buffers[2] = {&result->out, &result->err};
    size_t* lengths[2] = {&result->out_len, &result->err_len};
    int copied_streams = 1;
    for (int stream = 0; stream < 2; stream++) {
        if (!streams[stream]) { copied_streams = 0; continue; }
        *lengths[stream] = streams[stream]->length;
        *buffers[stream] = malloc(*lengths[stream] + 1);
        if (*buffers[stream]) { if (*lengths[stream]) memcpy(*buffers[stream], streams[stream]->data, *lengths[stream]); (*buffers[stream])[*lengths[stream]] = 0; }
        else { *lengths[stream] = 0; copied_streams = 0; }
    }
    wysna_host_close(command); wysna_host_close(arguments); wysna_host_close(out); wysna_host_close(err);
    result->has_exit = code >= 0;
    result->exit_code = code >= 0 ? (int)(uint32_t)code : 0;
    if (!copied_streams) { result->has_exit = 0; }
    else if (wysna_host_process_cancelled(&sdk_cancellation)) { result->status = "cancelled"; result->failure_kind = "cancelled"; }
    else if (code == 0) { result->status = "passed"; result->failure_kind = NULL; }
    else if (code >= 0) result->failure_kind = "test_failure";
    else {
#ifdef _WIN32
        int timed_out = host_error == ERROR_TIMEOUT;
        int limited = host_error == ERROR_BUFFER_OVERFLOW;
#else
        int timed_out = host_error == ETIMEDOUT;
        int limited = host_error == E2BIG;
#endif
        if (timed_out) { result->status = "timed_out"; result->failure_kind = "timed_out"; }
        if (limited) { result->status = "output_limited"; result->failure_kind = "output_limited"; result->output_limited = 1; }
    }
cleanup:
    if (fixture_set) (void)sdk_environment("WYSNA_TEST_TMPDIR", previous_fixture);
    free(previous_fixture);
    if (changed_directory) (void)sdk_test_chdir(cwd);
    for (int index = 0; index < invocation->owned_count; index++) free(invocation->owned[index]);
    invocation->owned_count = 0;
    if (invocation->workspace[0]) { sdk_remove_tree(invocation->workspace); invocation->workspace[0] = 0; }
    result->duration = wysna_host_monotonic_ms_internal() - started;
}

static void sdk_project_test_report(FILE* file, const SdkProjectTestPlan* plan, int count, int64_t duration, int xml) {
    int failed = 0, cancelled = 0;
    for (int index = 0; index < count; index++) {
        if (strcmp(plan->cases[index].status, "passed") != 0) failed++;
        if (strcmp(plan->cases[index].status, "cancelled") == 0) cancelled = 1;
    }
    if (xml) {
        fputs("<?xml version=\"1.0\" encoding=\"utf-8\"?>\n<testsuite name=\"", file);
        sdk_test_text(file, plan->name, strlen(plan->name), 1);
        fprintf(file, "\" tests=\"%d\" failures=\"%d\" time=\"%" PRId64 ".%03" PRId64 "\">", count, failed, duration / 1000, duration % 1000);
    } else {
        fputs("{\"schema_version\":\"wysna.project-test-report/1\",\"project\":", file); sdk_test_string(file, plan->name);
        fputs(",\"manifest\":", file); sdk_test_string(file, plan->manifest);
        fputs(",\"manifest_sha256\":", file); sdk_test_string(file, plan->manifest_sha);
        fputs(",\"toolchain\":\"" WYSNA_NATIVE_VERSION "\",\"lock_sha256\":", file);
        if (plan->lock_sha[0]) sdk_test_string(file, plan->lock_sha); else fputs("null", file);
        fputs(",\"workspace_revision\":", file); sdk_test_string(file, plan->revision);
        fputs(",\"plan_hash\":", file); sdk_test_string(file, plan->hash);
        fprintf(file, ",\"status\":\"%s\",\"duration_ms\":%" PRId64 ",\"selected\":%d,\"executed\":%d,\"passed\":%d,\"failed\":%d,\"cases\":[",
                cancelled ? "cancelled" : count == 0 ? "empty" : failed ? "failed" : "passed", duration, plan->count, count, count - failed, failed);
    }
    for (int index = 0; index < count; index++) {
        const SdkProjectTest* record = &plan->cases[index];
        const char* relative = record->entry + strlen(plan->root) + 1;
        if (xml) {
            fputs("<testcase name=\"", file); sdk_test_text(file, record->name, strlen(record->name), 1);
            fputs("\" classname=\"", file); sdk_test_text(file, plan->name, strlen(plan->name), 1);
            fputs("\" file=\"", file); sdk_test_text(file, relative, strlen(relative), 1);
            fprintf(file, "\" time=\"%" PRId64 ".%03" PRId64 "\">", record->duration / 1000, record->duration % 1000);
            if (record->failure_kind) {
                fprintf(file, "<failure type=\"%s\" message=\"%s\">", record->status, record->failure_kind);
                sdk_test_text(file, record->err, record->err_len, 1); fputs("</failure>", file);
            }
            fputs("<system-out>", file); sdk_test_text(file, record->out, record->out_len, 1); fputs("</system-out><system-err>", file);
            sdk_test_text(file, record->err, record->err_len, 1); fputs("</system-err></testcase>", file);
        } else {
            if (index) fputc(',', file);
            fputs("{\"id\":\"test:", file); sdk_test_text(file, record->name, strlen(record->name), 2); fputc('"', file);
            fputs(",\"name\":", file); sdk_test_string(file, record->name);
            fputs(",\"entry\":", file); sdk_test_string(file, relative);
            fputs(",\"entry_sha256\":", file); sdk_test_string(file, record->sha);
            fprintf(file, ",\"status\":\"%s\",\"exit_code\":", record->status);
            if (record->has_exit) fprintf(file, "%d", record->exit_code); else fputs("null", file);
            fprintf(file, ",\"duration_ms\":%" PRId64 ",\"compile_ms\":%" PRId64 ",\"run_ms\":%" PRId64
                    ",\"timeout_ms\":%" PRId64 ",\"output_limit_bytes_per_stream\":%d,\"output_limited\":%s,\"failure_kind\":",
                    record->duration, record->compile_duration, record->run_duration, record->timeout,
                    SDK_TEST_OUTPUT_LIMIT, record->output_limited ? "true" : "false");
            if (record->failure_kind) sdk_test_string(file, record->failure_kind); else fputs("null", file);
            char digest[65]; sdk_test_hash(record->out, record->out_len, digest);
            fputs(",\"stdout_sha256\":", file); sdk_test_string(file, digest);
            sdk_test_hash(record->err, record->err_len, digest);
            fputs(",\"stderr_sha256\":", file); sdk_test_string(file, digest);
            fputs(",\"stdout\":", file); sdk_test_text(file, record->out, record->out_len, 0);
            fputs(",\"stderr\":", file); sdk_test_text(file, record->err, record->err_len, 0);
            fputs(",\"diagnostics\":", file); fputs(record->diagnostics ? record->diagnostics : "[]", file); fputc('}', file);
        }
    }
    fputs(xml ? "</testsuite>\n" : "]}", file);
}

static int sdk_publish_test_report(const char* destination, const SdkProjectTestPlan* plan, int count, int64_t duration, int xml) {
    if (!destination[0]) return 1;
    char parent[SDK_PATH_CAP], temporary[SDK_PATH_CAP], product[SDK_PATH_CAP];
    if (!sdk_parent(parent, destination) || !sdk_mkdir_p(parent) || !sdk_temp(temporary, parent) || !sdk_join(product, temporary, "report")) return 0;
    FILE* file = sdk_test_open(product, "wb");
    int ok = file != NULL;
    if (file) { if (xml == 2) fputs(plan->json, file); else sdk_project_test_report(file, plan, count, duration, xml); if (xml != 1) fputc('\n', file); ok = !ferror(file); if (fclose(file)) ok = 0; }
    if (ok) ok = sdk_publish(product, destination);
    sdk_remove_tree(temporary); return ok;
}

static void sdk_test_artifact(FILE* output, const char* path, const char* media, int* emitted) {
    FILE* file = sdk_test_open(path, "rb");
    if (!file) return;
    WysnaSha256Context context; wysna_sha256_init(&context);
    uint8_t buffer[8192], digest[32]; size_t size, total = 0;
    while ((size = fread(buffer, 1, sizeof(buffer), file)) != 0) {
        wysna_sha256_update(&context, buffer, size); total += size;
    }
    int ok = !ferror(file); fclose(file);
    if (!ok) return;
    wysna_sha256_final(&context, digest);
    if ((*emitted)++) fputc(',', output);
    fputs("{\"kind\":\"test-report\",\"media_type\":", output); sdk_test_string(output, media);
    fputs(",\"path\":", output); sdk_test_string(output, path);
    fprintf(output, ",\"size_bytes\":%zu,\"sha256\":\"", total);
    for (int index = 0; index < 32; index++) fprintf(output, "%02x", digest[index]);
    fputs("\"}", output);
}

static void sdk_test_envelope(const SdkProjectTestPlan* plan, int count, int64_t duration, int code, int json_ok, int junit_ok, const char* diag_out) {
    FILE* out = (diag_out && diag_out[0]) ? sdk_test_open(diag_out, "wb") : NULL;
    FILE* dst = out ? out : stdout;
    const char* status = code == 0 ? "ok" : code == 2 ? "usage_error" : code == 3 ? "toolchain_error" : code == 130 ? "cancelled" : "runtime_error";
    fprintf(dst, "{\"schema_version\":\"wysna.tool-result/1\",\"command\":\"project.test\",\"status\":\"%s\",\"invocation_id\":\"native\",\"tool_exit_code\":%d,\"process_exit_code\":%d,\"execution\":null,\"toolchain\":{\"compiler\":\"" WYSNA_NATIVE_VERSION "\"},\"target\":{\"profile\":\"native-cli\",\"triple\":null},\"diagnostics\":[", status, code, code);
    if (code == 3) fputs("{\"code\":\"W5004\",\"stage\":\"filesystem\",\"severity\":\"error\",\"message\":\"Cannot publish test report\",\"primary_span\":null,\"related\":[],\"data\":{},\"fixes\":[]}", dst);
    fputs("],\"artifacts\":[", dst);
    int emitted = 0;
    if (json_ok && plan->report_json[0]) sdk_test_artifact(dst, plan->report_json, "application/json", &emitted);
    if (junit_ok && plan->report_junit[0]) sdk_test_artifact(dst, plan->report_junit, "application/xml", &emitted);
    fputs("],\"telemetry\":null,\"result\":", dst);
    if (plan->list_only) fputs(plan->json, dst);
    else sdk_project_test_report(dst, plan, count, duration, 0);
    fputs("}\n", dst);
    if (out) fclose(out);
}

static int sdk_run_project_tests(SdkInvocation* invocation, const SdkOptions* options, SdkProjectTestPlan* plan) {
    if (plan->list_only) {
        int ok = sdk_publish_test_report(plan->report_json, plan, 0, 0, 2);
        int code = !ok ? 3 : plan->count ? 0 : 2;
        if (options->project_json_v1) sdk_test_envelope(plan, 0, 0, code, ok, 0, options->diag_out);
        else for (int index = 0; index < plan->count; index++) printf("test:%s %s\n", plan->cases[index].name, plan->cases[index].entry + strlen(plan->root) + 1);
        return code;
    }
    int64_t started = wysna_host_monotonic_ms_internal();
    SdkTestCache cache;
    sdk_test_cache_init(&cache, invocation, options, plan);
    int failed = 0, completed = 0;
    for (int index = 0; index < plan->count; index++) {
        SdkProjectTest* record = &plan->cases[index];
        if (options->timeout_set && options->timeout > 0) record->timeout = options->timeout;
        sdk_project_test_case(invocation, options, plan, &cache, record);
        completed++;
        if (record->failure_kind) failed++;
        if (!options->project_json_v1) {
            if (record->out_len) fwrite(record->out, 1, record->out_len, stdout);
            if (record->err_len) fwrite(record->err, 1, record->err_len, stderr);
            fprintf(record->failure_kind ? stderr : stdout, "[%s] %s (native)\n", record->failure_kind ? "FAIL" : "PASS", record->name);
        }
        if (wysna_host_process_cancelled(&sdk_cancellation)) break;
    }
    int64_t duration = wysna_host_monotonic_ms_internal() - started;
    int code = wysna_host_process_cancelled(&sdk_cancellation) ? 130 : completed == 0 ? 2 : failed ? 1 : 0;
    int json_ok = sdk_publish_test_report(plan->report_json, plan, completed, duration, 0);
    int junit_ok = sdk_publish_test_report(plan->report_junit, plan, completed, duration, 1);
    if (!json_ok || !junit_ok) code = 3;
    if (options->project_json_v1) sdk_test_envelope(plan, completed, duration, code, json_ok, junit_ok, options->diag_out);
    else printf("Ran %d test(s): %d passed, %d failed.\n", completed, completed - failed, failed);
    for (int index = 0; index < completed; index++) {
        free(plan->cases[index].out); free(plan->cases[index].err); free(plan->cases[index].diagnostics);
    }
    return code;
}
#endif
