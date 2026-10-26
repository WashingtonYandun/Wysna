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
#endif
#endif /* WYSNA_SDK_PLATFORM_H */
