/* Canonical physical paths shared by module loading and the native SDK. */
#ifndef WYSNA_HOST_PATHS_H
#define WYSNA_HOST_PATHS_H
#ifndef W_HOST_H
#error "Include wysna_runtime.h before internal path helpers"
#endif

static char* wysna_host_canonical_path_utf8(const char* path) {
#if defined(__wasi__)
    (void)path; errno = ENOSYS; return NULL;
#elif defined(_WIN32)
    wchar_t* input = wysna_host_process_wide(path);
    if (!input) return NULL;
    HANDLE file = CreateFileW(input, 0, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                              NULL, OPEN_EXISTING, FILE_FLAG_BACKUP_SEMANTICS, NULL);
    free(input);
    if (file == INVALID_HANDLE_VALUE) return NULL;
    DWORD capacity = 32768;
    wchar_t* full = (wchar_t*)wysna_alloc_safe((size_t)capacity * sizeof(wchar_t));
    DWORD count = GetFinalPathNameByHandleW(file, full, capacity, FILE_NAME_NORMALIZED);
    DWORD error = GetLastError(); CloseHandle(file);
    if (!count || count >= capacity) { free(full); SetLastError(error); return NULL; }
    const wchar_t* normalized = full;
    if (wcsncmp(full, L"\\\\?\\UNC\\", 8) == 0) { full[6] = L'\\'; normalized = full + 6; }
    else if (wcsncmp(full, L"\\\\?\\", 4) == 0) normalized = full + 4;
    int size = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, normalized, -1, NULL, 0, NULL, NULL);
    if (!size) { free(full); return NULL; }
    char* result = (char*)wysna_alloc_safe((size_t)size);
    if (!WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, normalized, -1, result, size, NULL, NULL)) {
        free(result); free(full); return NULL;
    }
    free(full); return result;
#else
    return realpath(path, NULL);
#endif
}
static int64_t wysna_host_fs_canonical(void* path_handle, void* output_handle) {
    wysna_host_error_code = 0; wysna_host_error_domain = 0;
#if defined(__wasi__)
    (void)path_handle; (void)output_handle; return wysna_host_errno(ENOSYS);
#else
    char* path = wysna_host_path(path_handle);
    WysnaHostEntry* output = wysna_host_find(output_handle, 1);
    if (!path || !output || output->length) { free(path); return wysna_host_errno(EINVAL); }
    char* canonical = wysna_host_canonical_path_utf8(path);
#ifdef _WIN32
    DWORD error = GetLastError();
#else
    int error = errno;
#endif
    free(path);
    if (!canonical) {
#ifdef _WIN32
        wysna_host_error_code = error; wysna_host_error_domain = 2; return -1;
#else
        return wysna_host_errno(error);
#endif
    }
    size_t length = strlen(canonical);
    if (!wysna_host_process_utf8_valid((const unsigned char*)canonical, length)) { free(canonical); return wysna_host_errno(EILSEQ); }
    size_t written = wysna_host_buffer_append_bytes(output, canonical, length);
    free(canonical);
    return written == length ? (int64_t)written : wysna_host_errno(ENOMEM);
#endif
}
#endif /* WYSNA_HOST_PATHS_H */
