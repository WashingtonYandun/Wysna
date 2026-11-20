#ifndef WYSNA_HOST_PROJECT_H
#define WYSNA_HOST_PROJECT_H

#if !defined(_WIN32) && !defined(__wasi__)
#include <dirent.h>
/* Some embedders include libc before the runtime's feature macros. */
extern int lstat(const char* restrict path, struct stat* restrict status);
#endif

#ifndef __wasi__
/* Directory records are a kind byte followed by a NUL-terminated UTF-8 name. */
static bool wysna_project_append_entry(WysnaHostEntry* output, char kind, const char* name) {
    size_t size = strlen(name);
    if (!wysna_host_process_utf8_valid((const unsigned char*)name, size)) return false;
    return wysna_host_buffer_append_bytes(output, &kind, 1) == 1 &&
           wysna_host_buffer_append_bytes(output, name, size + 1) == size + 1;
}
#endif

static int64_t wysna_host_project_entries(void* path_handle, void* output_handle) {
#ifdef WYSNA_DENY_CAP_FILESYSTEM_READ
    wysna_sandbox_deny("filesystem.read");
    return -1;
#elif defined(__wasi__)
    (void)path_handle; (void)output_handle;
    return wysna_host_errno(ENOSYS);
#else
    char* path = wysna_host_path(path_handle);
    WysnaHostEntry* output = wysna_host_find(output_handle, 1);
    if (!path || !output || output->length) {
        free(path);
        return wysna_host_errno(EINVAL);
    }
    int error = 0;
#ifdef _WIN32
    size_t length = strlen(path);
    char* pattern = (char*)wysna_alloc_safe(length + 3);
    memcpy(pattern, path, length);
    memcpy(pattern + length, "/*", 3);
    wchar_t* wide = wysna_host_process_wide(pattern);
    free(pattern);
    WIN32_FIND_DATAW entry;
    HANDLE iterator = wide ? FindFirstFileW(wide, &entry) : INVALID_HANDLE_VALUE;
    free(wide);
    if (iterator == INVALID_HANDLE_VALUE) {
        if (GetLastError() != ERROR_FILE_NOT_FOUND) error = EIO;
    } else {
        do {
            if (wcscmp(entry.cFileName, L".") == 0 || wcscmp(entry.cFileName, L"..") == 0) continue;
            char name[4 * MAX_PATH];
            if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, entry.cFileName, -1,
                                     name, sizeof(name), NULL, NULL)) {
                error = EILSEQ; break;
            }
            DWORD attributes = entry.dwFileAttributes;
            char kind = attributes & FILE_ATTRIBUTE_REPARSE_POINT ? 'l' :
                        attributes & FILE_ATTRIBUTE_DIRECTORY ? 'd' : 'f';
            if (!wysna_project_append_entry(output, kind, name)) {
                error = EILSEQ; break;
            }
        } while (FindNextFileW(iterator, &entry));
        if (!error && GetLastError() != ERROR_NO_MORE_FILES) error = EIO;
        FindClose(iterator);
    }
#else
    DIR* directory = opendir(path);
    if (!directory) {
        error = errno;
    } else {
        size_t length = strlen(path);
        while (!error) {
            errno = 0;
            struct dirent* entry = readdir(directory);
            if (!entry) { error = errno; break; }
            const char* name = entry->d_name;
            if (strcmp(name, ".") == 0 || strcmp(name, "..") == 0) continue;
            size_t size = strlen(name);
            char* full = (char*)wysna_alloc_safe(length + size + 2);
            memcpy(full, path, length);
            full[length] = '/';
            memcpy(full + length + 1, name, size + 1);
            struct stat info;
            int observed = lstat(full, &info);
            if (observed) error = errno;
            free(full);
            if (observed) break;
            char kind = S_ISLNK(info.st_mode) ? 'l' : S_ISDIR(info.st_mode) ? 'd' :
                        S_ISREG(info.st_mode) ? 'f' : 'o';
            if (!wysna_project_append_entry(output, kind, name)) error = EILSEQ;
        }
        closedir(directory);
    }
#endif
    free(path);
    if (error) return wysna_host_errno(error);
    return (int64_t)output->length;
#endif
}

static int64_t wysna_host_project_path_key(void* input_handle, void* output_handle) {
    WysnaHostEntry* output = wysna_host_find(output_handle, 1);
    if (!output || output->length) return wysna_host_errno(EINVAL);
    void* normalized_handle = wysna_host_buffer_new();
    int64_t status = wysna_host_unicode_normalize(input_handle, 0, normalized_handle);
    WysnaHostEntry* normalized = wysna_host_find(normalized_handle, 1);
    WysnaCodepoints codepoints = {NULL, 0, 0};
    if (!status && wysna_utf8_decode_all((const uint8_t*)normalized->data, normalized->length, &codepoints) != 0)
        status = -1;
    wysna_host_close(normalized_handle);
    size_t table_size = sizeof(wysna_unicode_casefold) / sizeof(wysna_unicode_casefold[0]);
    for (size_t i = 0; !status && i < codepoints.length; ++i) {
        uint32_t cp = codepoints.data[i];
        size_t first = 0;
        size_t last = table_size;
        while (first < last) {
            size_t middle = first + (last - first) / 2;
            if (wysna_unicode_casefold[middle].code < cp) first = middle + 1;
            else last = middle;
        }
        uint32_t unchanged[] = {cp, 0, 0};
        const uint32_t* values = first < table_size && wysna_unicode_casefold[first].code == cp
            ? wysna_unicode_casefold[first].output : unchanged;
        for (size_t n = 0; n < 3 && values[n]; ++n) {
            uint8_t encoded[4];
            size_t size = wysna_utf8_encode(values[n], encoded);
            if (wysna_host_buffer_append_bytes(output, encoded, size) != size) { status = -1; break; }
        }
    }
    free(codepoints.data);
    return status ? -1 : (int64_t)output->length;
}

static int64_t wysna_host_project_file_sha256(void* path_handle, void* output_handle) {
    WysnaHostEntry* output = wysna_host_find(output_handle, 1);
    if (!output || output->length) return wysna_host_errno(EINVAL);
    void* handle = wysna_host_file_open(path_handle, 0);
    WysnaHostEntry* file = wysna_host_find(handle, 2);
    if (!file) return -1;

    WysnaSha256Context context;
    wysna_sha256_init(&context);
    uint8_t input[64 * 1024];
    FILE* stream = (FILE*)file->data;
    for (;;) {
        size_t count = fread(input, 1, sizeof(input), stream);
        wysna_sha256_update(&context, input, count);
        if (count < sizeof(input)) break;
    }
    bool failed = ferror(stream) != 0;
    wysna_host_close(handle);
    if (failed) return wysna_host_errno(EIO);
    uint8_t digest[32];
    wysna_sha256_final(&context, digest);
    return wysna_host_buffer_append_bytes(output, digest, sizeof(digest)) == sizeof(digest) ? 0 : -1;
}

#endif
