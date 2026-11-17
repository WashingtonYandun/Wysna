/* Native filesystem boundaries. All public paths are UTF-8. */
#ifndef WYSNA_SDK_PLATFORM_H
#define WYSNA_SDK_PLATFORM_H
#include <sys/stat.h>
#ifdef __APPLE__
#include <mach-o/dyld.h>
#endif
#ifndef _WIN32
#include <dirent.h>
#endif

#define SDK_PATH_CAP 32768

static int sdk_copy(char* output, const char* input) {
    size_t size = strlen(input);
    if (size >= SDK_PATH_CAP) return 0;
    memmove(output, input, size + 1);
    return 1;
}
static int sdk_join(char* output, const char* directory, const char* name) {
    int count = snprintf(output, SDK_PATH_CAP, "%s/%s", directory, name);
    return count >= 0 && count < SDK_PATH_CAP;
}
static int sdk_has_wysna_suffix(const char* name) {
    size_t len = strlen(name);
    return len > 6 && strcmp(name + len - 6, ".wysna") == 0;
}
static int sdk_string_cmp(const void* a, const void* b) {
    const char* sa = *(const char* const*)a;
    const char* sb = *(const char* const*)b;
    return strcmp(sa, sb);
}
static int sdk_read_file(const char* path, char** out_buf, size_t* out_len) {
    *out_buf = NULL;
    if (out_len) *out_len = 0;
    FILE* file = NULL;
#ifdef _WIN32
    wchar_t* wide = wysna_host_process_wide(path);
    if (!wide) return 0;
    file = _wfopen(wide, L"rb");
    free(wide);
#else
    file = fopen(path, "rb");
#endif
    if (!file) return 0;
    if (fseek(file, 0, SEEK_END) != 0) { fclose(file); return 0; }
    long size = ftell(file);
    if (size < 0) { fclose(file); return 0; }
    rewind(file);
    char* buffer = (char*)malloc((size_t)size + 1);
    if (!buffer) { fclose(file); return 0; }
    size_t got = size > 0 ? fread(buffer, 1, (size_t)size, file) : 0;
    fclose(file);
    if (got != (size_t)size) { free(buffer); return 0; }
    buffer[got] = 0;
    *out_buf = buffer;
    if (out_len) *out_len = got;
    return 1;
}
static int sdk_parent(char* output, const char* path) {
    if (!sdk_copy(output, path)) return 0;
    char* boundary = NULL;
    for (char* current = output; *current; current++) {
        if (*current == '/') boundary = current;
#ifdef _WIN32
        if (*current == '\\') boundary = current;
#endif
    }
    if (!boundary) return sdk_copy(output, ".");
    if (boundary == output) boundary[1] = 0;
#ifdef _WIN32
    else if (boundary == output + 2 && output[1] == ':') boundary[1] = 0;
#endif
    else *boundary = 0;
    return 1;
}
#ifdef _WIN32
static int sdk_utf8(char* output, const wchar_t* input) {
    return WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, input, -1,
                              output, SDK_PATH_CAP, NULL, NULL) > 0;
}
static int sdk_absolute(char* output, const char* input, int existing) {
    wchar_t* wide = wysna_host_process_wide(input);
    wchar_t* full = calloc(SDK_PATH_CAP, sizeof(wchar_t));
    if (!wide || !full) { free(wide); free(full); return 0; }
    DWORD count = GetFullPathNameW(wide, SDK_PATH_CAP, full, NULL);
    free(wide);
    int valid = count > 0 && count < SDK_PATH_CAP;
    int result = valid && sdk_utf8(output, full);
    free(full);
    if (result && existing) {
        char* canonical = wysna_host_canonical_path_utf8(output);
        result = canonical && sdk_copy(output, canonical);
        free(canonical);
    }
    return result;
}
static int sdk_executable(char* output) {
    wchar_t* path = calloc(SDK_PATH_CAP, sizeof(wchar_t));
    if (!path) return 0;
    DWORD count = GetModuleFileNameW(NULL, path, SDK_PATH_CAP);
    int result = count > 0 && count < SDK_PATH_CAP && sdk_utf8(output, path);
    free(path);
    return result;
}
static int sdk_is_file(const char* path) {
    wchar_t* wide = wysna_host_process_wide(path);
    if (!wide) return 0;
    DWORD attributes = GetFileAttributesW(wide); free(wide);
    return attributes != INVALID_FILE_ATTRIBUTES && !(attributes & FILE_ATTRIBUTE_DIRECTORY);
}
static int sdk_same_file(const char* first, const char* second) {
    wchar_t* paths[2] = {wysna_host_process_wide(first), wysna_host_process_wide(second)};
    BY_HANDLE_FILE_INFORMATION info[2]; int present = 1;
    for (int index = 0; index < 2; index++) {
        HANDLE file = paths[index] ? CreateFileW(paths[index], 0,
            FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL, OPEN_EXISTING, 0, NULL) : INVALID_HANDLE_VALUE;
        free(paths[index]);
        if (file == INVALID_HANDLE_VALUE) present = 0;
        else { if (!GetFileInformationByHandle(file, &info[index])) present = 0; CloseHandle(file); }
    }
    return present && info[0].dwVolumeSerialNumber == info[1].dwVolumeSerialNumber &&
           info[0].nFileIndexHigh == info[1].nFileIndexHigh && info[0].nFileIndexLow == info[1].nFileIndexLow;
}
static int sdk_temp(char* output, const char* parent) {
    wchar_t* directory = parent ? wysna_host_process_wide(parent) : calloc(SDK_PATH_CAP, sizeof(wchar_t));
    wchar_t* path = calloc(SDK_PATH_CAP, sizeof(wchar_t));
    if (!directory || !path) { free(directory); free(path); return 0; }
    int result = 0;
    if (parent || GetTempPathW(SDK_PATH_CAP, directory)) {
        for (int attempt = 0; attempt < 16; attempt++) {
            if (!GetTempFileNameW(directory, L"wys", 0, path)) break;
            if (!DeleteFileW(path)) break;
            if (CreateDirectoryW(path, NULL)) { result = sdk_utf8(output, path); break; }
        }
    }
    free(path); free(directory);
    return result;
}
static int sdk_publish(const char* source, const char* target) {
    wchar_t* from = wysna_host_process_wide(source);
    wchar_t* to = wysna_host_process_wide(target);
    int result = from && to && MoveFileExW(from, to, MOVEFILE_REPLACE_EXISTING | MOVEFILE_WRITE_THROUGH);
    free(from); free(to); return result;
}
static int sdk_environment(const char* name, const char* value) {
    wchar_t* wide_name = wysna_host_process_wide(name);
    wchar_t* wide_value = value ? wysna_host_process_wide(value) : NULL;
    int result = wide_name && (!value || wide_value) && SetEnvironmentVariableW(wide_name, wide_value);
    free(wide_name); free(wide_value); return result;
}
static char* sdk_environment_value(const char* name) {
    wchar_t* wide_name = wysna_host_process_wide(name);
    if (!wide_name) return NULL;
    SetLastError(ERROR_SUCCESS);
    DWORD count = GetEnvironmentVariableW(wide_name, NULL, 0);
    if (!count) {
        DWORD error = GetLastError(); free(wide_name);
        return error == ERROR_SUCCESS ? calloc(1, 1) : NULL;
    }
    wchar_t* wide = calloc(count, sizeof(wchar_t));
    DWORD copied = wide ? GetEnvironmentVariableW(wide_name, wide, count) : 0;
    free(wide_name);
    char* value = NULL;
    if (copied && copied < count) {
        int bytes = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide, -1, NULL, 0, NULL, NULL);
        value = bytes > 0 ? malloc((size_t)bytes) : NULL;
        if (value && !WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, wide, -1, value, bytes, NULL, NULL)) { free(value); value = NULL; }
    }
    free(wide); return value;
}
static int sdk_is_directory(const char* path) {
    wchar_t* wide = wysna_host_process_wide(path);
    if (!wide) return 0;
    DWORD attributes = GetFileAttributesW(wide); free(wide);
    return attributes != INVALID_FILE_ATTRIBUTES && (attributes & FILE_ATTRIBUTE_DIRECTORY);
}
static int sdk_remove_tree(const char* path) {
    wchar_t* wide = wysna_host_process_wide(path);
    if (!wide) return 0;
    DWORD attributes = GetFileAttributesW(wide);
    if (attributes == INVALID_FILE_ATTRIBUTES) { free(wide); return 1; }
    if (!(attributes & FILE_ATTRIBUTE_DIRECTORY)) { int result = DeleteFileW(wide); free(wide); return result; }
    if (!(attributes & FILE_ATTRIBUTE_REPARSE_POINT)) {
        char* pattern = malloc(SDK_PATH_CAP); char* child = malloc(SDK_PATH_CAP);
        if (!pattern || !child || !sdk_join(pattern, path, "*")) { free(pattern); free(child); free(wide); return 0; }
        wchar_t* search = wysna_host_process_wide(pattern); WIN32_FIND_DATAW entry;
        HANDLE found = search ? FindFirstFileW(search, &entry) : INVALID_HANDLE_VALUE;
        free(search); free(pattern);
        int result = 1;
        if (found != INVALID_HANDLE_VALUE) {
            do {
                if (wcscmp(entry.cFileName, L".") == 0 || wcscmp(entry.cFileName, L"..") == 0) continue;
                char* name = malloc(SDK_PATH_CAP);
                int removed = name && sdk_utf8(name, entry.cFileName) && sdk_join(child, path, name) && sdk_remove_tree(child);
                free(name); if (!removed) { result = 0; break; }
            } while (FindNextFileW(found, &entry));
            FindClose(found);
        }
        free(child);
        if (!result) { free(wide); return 0; }
    }
    int result = RemoveDirectoryW(wide); free(wide); return result;
}
static int sdk_collect_wysna_tests_win32(const char* path, char*** list_ptr, size_t* count_ptr, size_t* cap_ptr) {
    char* pattern = (char*)malloc(SDK_PATH_CAP);
    char* child = (char*)malloc(SDK_PATH_CAP);
    if (!pattern || !child || !sdk_join(pattern, path, "*")) { free(pattern); free(child); return 0; }
    wchar_t* search = wysna_host_process_wide(pattern);
    WIN32_FIND_DATAW entry;
    HANDLE found = search ? FindFirstFileW(search, &entry) : INVALID_HANDLE_VALUE;
    free(search); free(pattern);
    int ok = 1;
    if (found != INVALID_HANDLE_VALUE) {
        do {
            if (wcscmp(entry.cFileName, L".") == 0 || wcscmp(entry.cFileName, L"..") == 0) continue;
            char* name = (char*)malloc(SDK_PATH_CAP);
            if (!name || !sdk_utf8(name, entry.cFileName) || !sdk_join(child, path, name)) {
                free(name); ok = 0; break;
            }
            free(name);
            if (entry.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
                if (!(entry.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT)) {
                    if (!sdk_collect_wysna_tests_win32(child, list_ptr, count_ptr, cap_ptr)) { ok = 0; break; }
                }
            } else if (sdk_has_wysna_suffix(child)) {
                if (*count_ptr == *cap_ptr) {
                    size_t next_cap = *cap_ptr ? (*cap_ptr * 2) : 16;
                    char** grown = (char**)realloc(*list_ptr, next_cap * sizeof(char*));
                    if (!grown) { ok = 0; break; }
                    *list_ptr = grown; *cap_ptr = next_cap;
                }
                size_t len = strlen(child) + 1;
                char* copy = (char*)malloc(len);
                if (!copy) { ok = 0; break; }
                memcpy(copy, child, len);
                (*list_ptr)[(*count_ptr)++] = copy;
            }
        } while (FindNextFileW(found, &entry));
        FindClose(found);
    }
    free(child);
    return ok;
}
static int sdk_list_wysna_tests(const char* root_dir, char*** files_out, size_t* count_out) {
    char** list = NULL; size_t count = 0, cap = 0;
    int ok = sdk_collect_wysna_tests_win32(root_dir, &list, &count, &cap);
    if (!ok) { for (size_t i = 0; i < count; i++) free(list[i]); free(list); return 0; }
    if (count > 1) qsort(list, count, sizeof(char*), sdk_string_cmp);
    *files_out = list; *count_out = count;
    return 1;
}
#else
static int sdk_absolute(char* output, const char* input, int existing) {
    if (existing) {
        char* canonical = wysna_host_canonical_path_utf8(input);
        int result = canonical && sdk_copy(output, canonical);
        free(canonical); return result;
    }
    if (input[0] == '/') return sdk_copy(output, input);
    char* directory = malloc(SDK_PATH_CAP);
    int result = directory && getcwd(directory, SDK_PATH_CAP) && sdk_join(output, directory, input);
    free(directory); return result;
}
static int sdk_executable(char* output) {
#ifdef __APPLE__
    uint32_t size = SDK_PATH_CAP;
    return _NSGetExecutablePath(output, &size) == 0;
#else
    ssize_t size = readlink("/proc/self/exe", output, SDK_PATH_CAP - 1);
    if (size < 1 || size >= SDK_PATH_CAP - 1) return 0;
    output[size] = 0; return 1;
#endif
}
static int sdk_is_file(const char* path) { struct stat info; return stat(path, &info) == 0 && S_ISREG(info.st_mode); }
static int sdk_same_file(const char* first, const char* second) {
    struct stat left, right;
    return stat(first, &left) == 0 && stat(second, &right) == 0 && left.st_dev == right.st_dev && left.st_ino == right.st_ino;
}
static int sdk_temp(char* output, const char* parent) {
    const char* temporary = parent ? parent : getenv("TMPDIR");
    if (!temporary || !temporary[0]) temporary = "/tmp";
    return sdk_join(output, temporary, ".wysna-XXXXXX") && mkdtemp(output) != NULL;
}
static char* sdk_environment_value(const char* name) {
    const char* value = getenv(name);
    if (!value) return NULL;
    size_t size = strlen(value) + 1;
    char* copy = malloc(size);
    if (copy) memcpy(copy, value, size);
    return copy;
}
static int sdk_is_directory(const char* path) { struct stat info; return stat(path, &info) == 0 && S_ISDIR(info.st_mode); }
static int sdk_publish(const char* source, const char* target) { return rename(source, target) == 0; }
static int sdk_environment(const char* name, const char* value) { return (value ? setenv(name, value, 1) : unsetenv(name)) == 0; }
static int sdk_remove_tree(const char* path) {
    struct stat info;
    if (lstat(path, &info) != 0) return errno == ENOENT;
    if (!S_ISDIR(info.st_mode)) return unlink(path) == 0;
    DIR* directory = opendir(path);
    if (!directory) return 0;
    int result = 1;
    char* child = malloc(SDK_PATH_CAP); struct dirent* entry;
    if (!child) result = 0;
    while (result && (entry = readdir(directory)) != NULL) {
        if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0) continue;
        result = sdk_join(child, path, entry->d_name) && sdk_remove_tree(child);
    }
    free(child); closedir(directory);
    return result && rmdir(path) == 0;
}
static int sdk_collect_wysna_tests_posix(const char* path, char*** list_ptr, size_t* count_ptr, size_t* cap_ptr) {
    DIR* directory = opendir(path);
    if (!directory) return 0;
    int ok = 1;
    char* child = (char*)malloc(SDK_PATH_CAP);
    if (!child) { closedir(directory); return 0; }
    struct dirent* entry;
    while (ok && (entry = readdir(directory)) != NULL) {
        if (strcmp(entry->d_name, ".") == 0 || strcmp(entry->d_name, "..") == 0) continue;
        if (!sdk_join(child, path, entry->d_name)) { ok = 0; break; }
        struct stat info;
        if (stat(child, &info) != 0) continue;
        if (S_ISDIR(info.st_mode)) {
            if (!sdk_collect_wysna_tests_posix(child, list_ptr, count_ptr, cap_ptr)) { ok = 0; break; }
        } else if (S_ISREG(info.st_mode) && sdk_has_wysna_suffix(child)) {
            if (*count_ptr == *cap_ptr) {
                size_t next_cap = *cap_ptr ? (*cap_ptr * 2) : 16;
                char** grown = (char**)realloc(*list_ptr, next_cap * sizeof(char*));
                if (!grown) { ok = 0; break; }
                *list_ptr = grown; *cap_ptr = next_cap;
            }
            size_t len = strlen(child) + 1;
            char* copy = (char*)malloc(len);
            if (!copy) { ok = 0; break; }
            memcpy(copy, child, len);
            (*list_ptr)[(*count_ptr)++] = copy;
        }
    }
    free(child); closedir(directory);
    return ok;
}
static int sdk_list_wysna_tests(const char* root_dir, char*** files_out, size_t* count_out) {
    char** list = NULL; size_t count = 0, cap = 0;
    int ok = sdk_collect_wysna_tests_posix(root_dir, &list, &count, &cap);
    if (!ok) { for (size_t i = 0; i < count; i++) free(list[i]); free(list); return 0; }
    if (count > 1) qsort(list, count, sizeof(char*), sdk_string_cmp);
    *files_out = list; *count_out = count;
    return 1;
}
#endif
#endif /* WYSNA_SDK_PLATFORM_H */
