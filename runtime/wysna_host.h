#ifndef W_HOST_H
#define W_HOST_H
/* Internal scalar/opaque ABI for bounded binary I/O. Handles are checked IDs,
 * never exposed allocation addresses; stale handles cannot access freed storage. */
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <stdbool.h>
#include <string.h>
#include <errno.h>
#include <ctype.h>
#include <sys/stat.h>

#ifdef _WIN32
#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>
#include <io.h>
#include <direct.h>
#include <wchar.h>
#elif defined(__wasi__)
#include <unistd.h>
#else
#include <fcntl.h>
#include <signal.h>
#include <sys/select.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#endif

/* Guard against POSIX unistd.h access() macros colliding with WTag W_OK */
#ifdef W_OK
#undef W_OK
#endif

typedef struct WysnaHostEntry {
    uintptr_t id;
    int kind;
    void* data;
    size_t length;
    size_t capacity;
    struct WysnaHostEntry* next;
} WysnaHostEntry;
/* Host resources and diagnostics share the lifetime of the owning runtime instance. */
typedef struct WysnaHostContext {
    WysnaHostEntry* entries;
    uintptr_t next_id;
    bool initialized;
    int64_t error_code;
    int64_t error_domain;
} WysnaHostContext;
static WysnaHostContext wysna_host_context = {NULL, 1, false, 0, 0};
#define wysna_host_entries (wysna_host_context.entries)
#define wysna_host_next_id (wysna_host_context.next_id)
#define wysna_host_initialized (wysna_host_context.initialized)
#define wysna_host_error_code (wysna_host_context.error_code)
#define wysna_host_error_domain (wysna_host_context.error_domain)
/* Error codes are captured immediately, before cleanup can overwrite errno. */
static int64_t wysna_host_last_error(void) { return wysna_host_error_code; }
static int64_t wysna_host_last_error_domain(void) { return wysna_host_error_domain; }
static int64_t wysna_host_errno(int code) {
    wysna_host_error_code = code ? code : EIO; wysna_host_error_domain = 1; return -1;
}

static WysnaHostEntry* wysna_host_find(void* handle, int kind) {
    for (WysnaHostEntry* entry = wysna_host_entries; entry; entry = entry->next)
        if (entry->id == (uintptr_t)handle && entry->kind == kind) return entry;
    return NULL;
}
static int64_t wysna_host_close(void* handle) {
    WysnaHostEntry** slot = &wysna_host_entries;
    while (*slot && (*slot)->id != (uintptr_t)handle) slot = &(*slot)->next;
    if (!*slot) return 0;
    WysnaHostEntry* entry = *slot;
    /* fclose consumes the stream even when flushing fails. */
    errno = 0;
    int status = entry->kind == 2 ? fclose((FILE*)entry->data) : 0;
    int close_error = errno;
    if (entry->kind == 1) free(entry->data);
    *slot = entry->next;
    free(entry);
    return status == 0 ? 0 : wysna_host_errno(close_error);
}
static WVal wysna_host_owned_pointer(void* address) {
    return wysna_managed_pointer(address, wysna_host_close);
}
static void* wysna_host_borrow(void* address) { return address; }
static int64_t wysna_host_active_handles(void) {
    int64_t count = 0;
    for (WysnaHostEntry* entry = wysna_host_entries; entry; entry = entry->next) count++;
    return count;
}
static int64_t wysna_host_active_bytes(void) {
    int64_t count = 0;
    for (WysnaHostEntry* entry = wysna_host_entries; entry; entry = entry->next)
        if (entry->kind == 1) count += (int64_t)entry->capacity;
    return count;
}
static void wysna_host_cleanup(void) {
    while (wysna_host_entries) wysna_host_close((void*)wysna_host_entries->id);
}
static void* wysna_host_new(int kind, void* data) {
    if (!wysna_host_initialized) {
        if (atexit(wysna_host_cleanup) != 0) wysna_runtime_fail("W4003", "Cannot register resource cleanup");
        wysna_host_initialized = true;
    }
    if (wysna_host_next_id == UINTPTR_MAX) wysna_runtime_fail("W4003", "Host handle identity exhausted");
    WysnaHostEntry* entry = (WysnaHostEntry*)wysna_alloc_safe(sizeof(WysnaHostEntry));
    entry->id = wysna_host_next_id++;
    entry->kind = kind; entry->data = data; entry->length = 0; entry->capacity = 0;
    entry->next = wysna_host_entries; wysna_host_entries = entry;
    return (void*)entry->id;
}
static void* wysna_host_claim(void* raw) {
    for (WysnaHostEntry* entry = wysna_host_entries; entry; entry = entry->next)
        if (entry->kind == 3 && entry->data == raw) return NULL;
    return wysna_host_new(3, raw);
}
static int64_t wysna_host_claim_valid(void* handle) { return wysna_host_find(handle, 3) != NULL; }
static void* wysna_host_buffer_new(void) { return wysna_host_new(1, NULL); }
static int64_t wysna_host_buffer_size(void* handle) {
    WysnaHostEntry* entry = wysna_host_find(handle, 1);
    return entry ? (int64_t)entry->length : -1;
}
static int64_t wysna_host_buffer_get(void* handle, int64_t index) {
    WysnaHostEntry* entry = wysna_host_find(handle, 1);
    return entry && index >= 0 && (uint64_t)index < entry->length ? ((unsigned char*)entry->data)[index] : -1;
}
static int64_t wysna_host_buffer_append(void* handle, int64_t value) {
    WysnaHostEntry* entry = wysna_host_find(handle, 1);
    if (!entry || value < 0 || value > 255) return -1;
    if (entry->length == entry->capacity) {
        if (entry->capacity > SIZE_MAX / 2 - 1) return -1;
        entry->capacity = entry->capacity ? entry->capacity * 2 : 64;
        entry->data = wysna_realloc_safe(entry->data, entry->capacity);
    }
    ((unsigned char*)entry->data)[entry->length++] = (unsigned char)value;
    return 0;
}
static size_t wysna_host_buffer_append_bytes(WysnaHostEntry* entry, const void* data, size_t length) {
    if (!entry || entry->kind != 1 || length > SIZE_MAX - entry->length) return 0;
    size_t required = entry->length + length;
    if (required > entry->capacity) {
        size_t capacity = entry->capacity ? entry->capacity : 64;
        while (capacity < required) {
            if (capacity > SIZE_MAX / 2) { capacity = required; break; }
            capacity *= 2;
        }
        entry->data = wysna_realloc_safe(entry->data, capacity);
        entry->capacity = capacity;
    }
    if (length) memcpy((unsigned char*)entry->data + entry->length, data, length);
    entry->length = required;
    return length;
}
static char* wysna_host_path(void* handle) {
    WysnaHostEntry* entry = wysna_host_find(handle, 1);
    if (!entry || !entry->length || memchr(entry->data, 0, entry->length)) return NULL;
    char* path = (char*)wysna_alloc_safe(entry->length + 1);
    memcpy(path, entry->data, entry->length); path[entry->length] = 0;
    return path;
}
#ifdef _WIN32
static wchar_t* wysna_host_wide(const char* text) {
    if (!text) return NULL;
    int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text, -1, NULL, 0);
    if (!count) {
        count = MultiByteToWideChar(CP_ACP, 0, text, -1, NULL, 0);
        if (!count) return NULL;
        wchar_t* wide = (wchar_t*)wysna_alloc_safe((size_t)count * sizeof(wchar_t));
        if (!MultiByteToWideChar(CP_ACP, 0, text, -1, wide, count)) { free(wide); return NULL; }
        return wide;
    }
    wchar_t* wide = (wchar_t*)wysna_alloc_safe((size_t)count * sizeof(wchar_t));
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, text, -1, wide, count)) { free(wide); return NULL; }
    return wide;
}
#endif
static void* wysna_host_file_open(void* path_handle, int64_t mode) {
    char* path = wysna_host_path(path_handle);
    if (!path || mode < 0 || mode > 2) { free(path); wysna_host_errno(EINVAL); return NULL; }
    const char* modes[] = { "rb", "wb", "ab" };
#ifdef _WIN32
    wchar_t* wide = wysna_host_wide(path);
    const wchar_t* wide_modes[] = { L"rb", L"wb", L"ab" };
    FILE* file = wide ? _wfopen(wide, wide_modes[mode]) : NULL;
    free(wide);
#else
    FILE* file = fopen(path, modes[mode]);
#endif
    if (!file) wysna_host_errno(errno);
    free(path);
    return file ? wysna_host_new(2, file) : NULL;
}
static int64_t wysna_host_file_read(void* file_handle, void* buffer_handle, int64_t count) {
    WysnaHostEntry* file = wysna_host_find(file_handle, 2);
    WysnaHostEntry* buffer = wysna_host_find(buffer_handle, 1);
    /* An explicit bound prevents an untrusted request from allocating a whole file. */
    if (!file || !buffer) return wysna_host_errno(EBADF);
    if (count <= 0 || count > 16777216) return wysna_host_errno(EINVAL);
    if ((uint64_t)count > buffer->capacity) {
        buffer->capacity = (size_t)count;
        buffer->data = wysna_realloc_safe(buffer->data, buffer->capacity);
    }
    errno = 0;
    buffer->length = fread(buffer->data, 1, (size_t)count, (FILE*)file->data);
    return ferror((FILE*)file->data) ? wysna_host_errno(errno) : (int64_t)buffer->length;
}
static int64_t wysna_host_file_write(void* file_handle, void* buffer_handle) {
    WysnaHostEntry* file = wysna_host_find(file_handle, 2);
    WysnaHostEntry* buffer = wysna_host_find(buffer_handle, 1);
    if (!file || !buffer) return wysna_host_errno(EBADF);
    if (!buffer->length) return 0;
    errno = 0;
    if (fwrite(buffer->data, 1, buffer->length, (FILE*)file->data) != buffer->length) return wysna_host_errno(errno);
    if (fflush((FILE*)file->data) != 0) return wysna_host_errno(errno);
    return 0;
}
static int64_t wysna_host_is_open(void* handle) { return wysna_host_find(handle, 2) != NULL; }
/* Operations: exists, size, mkdir, remove, rename. Failures return -1. */
static int64_t wysna_host_fs(void* first, void* second, int64_t operation) {
    char* path = wysna_host_path(first);
    char* other = operation == 4 ? wysna_host_path(second) : NULL;
    if (!path || (operation == 4 && !other)) { free(path); free(other); return wysna_host_errno(EINVAL); }
    int64_t result = -1;
#ifdef _WIN32
    wchar_t* wide = wysna_host_wide(path);
    wchar_t* wide_other = other ? wysna_host_wide(other) : NULL;
    if (wide) {
        if (operation == 0) {
            DWORD attr = GetFileAttributesW(wide);
            if (attr != INVALID_FILE_ATTRIBUTES) result = 1;
            else {
                DWORD code = GetLastError();
                result = code == ERROR_FILE_NOT_FOUND || code == ERROR_PATH_NOT_FOUND ? 0 : -1;
                if (result < 0) { wysna_host_error_code = code; wysna_host_error_domain = 2; }
            }
        } else if (operation == 1) {
            WIN32_FILE_ATTRIBUTE_DATA data;
            if (GetFileAttributesExW(wide, GetFileExInfoStandard, &data)) {
                result = (int64_t)(((uint64_t)data.nFileSizeHigh << 32) | data.nFileSizeLow);
            } else {
                result = -1;
            }
        } else if (operation == 2) {
            result = CreateDirectoryW(wide, NULL) ? 0 : -1;
        } else if (operation == 3) {
            result = (DeleteFileW(wide) || RemoveDirectoryW(wide)) ? 0 : -1;
        } else if (operation == 4 && wide_other) {
            result = MoveFileExW(wide, wide_other, MOVEFILE_REPLACE_EXISTING) ? 0 : -1;
        }
    }
    if (result < 0 && operation != 0) { wysna_host_error_code = GetLastError(); wysna_host_error_domain = 2; }
    free(wide); free(wide_other);
#else
    struct stat status;
    if (operation <= 1) result = stat(path, &status) == 0 ? (operation == 0 ? 1 : status.st_size) : (operation == 0 && errno == ENOENT ? 0 : -1);
    else if (operation == 2) result = mkdir(path, 0777) == 0 ? 0 : -1;
    else if (operation == 3) result = remove(path) == 0 ? 0 : -1;
    else if (operation == 4) result = rename(path, other) == 0 ? 0 : -1;
#endif
#ifndef _WIN32
    if (result < 0) wysna_host_errno(errno);
#endif
    free(path); free(other); return result;
}

#if !defined(__wasi__)
static int wysna_host_process_utf8_valid(const unsigned char* data, size_t length) {
    size_t offset = 0;
    while (offset < length) {
        size_t width;
        if (!wysna_utf8_valid_codepoint_at_n((const char*)data, length, offset, &width, NULL)) return 0;
        offset += width;
    }
    return 1;
}

#ifdef _WIN32
static int64_t wysna_host_process_error(int code) {
    wysna_host_error_code = code == E2BIG ? ERROR_BUFFER_OVERFLOW : ERROR_INVALID_PARAMETER;
    wysna_host_error_domain = 2;
    return -1;
}
#else
static int64_t wysna_host_process_error(int code) {
    return wysna_host_errno(code ? code : EIO);
}
#endif

static int wysna_host_process_decode(void* executable_handle, void* arguments_handle,
                                     char** executable_out, char*** argv_out, int* argc_out) {
    WysnaHostEntry* executable_entry = wysna_host_find(executable_handle, 1);
    WysnaHostEntry* arguments_entry = wysna_host_find(arguments_handle, 1);
    if (!executable_entry || !arguments_entry || !executable_entry->length
            || memchr(executable_entry->data, 0, executable_entry->length)
            || !wysna_host_process_utf8_valid((const unsigned char*)executable_entry->data,
                                               executable_entry->length)) {
        wysna_host_process_error(EINVAL); return 0;
    }
    char* executable = (char*)wysna_alloc_safe(executable_entry->length + 1);
    memcpy(executable, executable_entry->data, executable_entry->length);
    executable[executable_entry->length] = 0;

    char** arguments = (char**)wysna_alloc_safe(sizeof(char*) * 258);
    size_t offset = 0;
    int count = 0;
    while (offset < arguments_entry->length) {
        if (count >= 256 || offset + 4 > arguments_entry->length) {
            for (int index = 0; index < count; index++) free(arguments[index]);
            free(executable); free(arguments); wysna_host_process_error(E2BIG); return 0;
        }
        const unsigned char* data = (const unsigned char*)arguments_entry->data;
        uint32_t length = ((uint32_t)data[offset] << 24) | ((uint32_t)data[offset + 1] << 16)
                        | ((uint32_t)data[offset + 2] << 8) | (uint32_t)data[offset + 3];
        offset += 4;
        if (length > 1048576 || offset + length > arguments_entry->length
                || memchr(data + offset, 0, length)
                || !wysna_host_process_utf8_valid(data + offset, length)) {
            for (int index = 0; index < count; index++) free(arguments[index]);
            free(executable); free(arguments); wysna_host_process_error(EINVAL); return 0;
        }
        arguments[count] = (char*)wysna_alloc_safe((size_t)length + 1);
        memcpy(arguments[count], data + offset, length);
        arguments[count][length] = 0;
        count++;
        offset += length;
        if (offset > 16777216) {
            for (int index = 0; index < count; index++) free(arguments[index]);
            free(executable); free(arguments); wysna_host_process_error(E2BIG); return 0;
        }
    }
    arguments[count] = NULL;
    char** argv = (char**)wysna_alloc_safe(sizeof(char*) * (size_t)(count + 2));
    argv[0] = executable;
    for (int index = 0; index < count; index++) argv[index + 1] = arguments[index];
    argv[count + 1] = NULL;
    free(arguments);
    *executable_out = executable;
    *argv_out = argv;
    *argc_out = count + 1;
    return 1;
}

#ifdef _WIN32
static wchar_t* wysna_host_process_wide(const char* value) {
    int count = MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value, -1, NULL, 0);
    if (!count) return NULL;
    wchar_t* wide = (wchar_t*)wysna_alloc_safe((size_t)count * sizeof(wchar_t));
    if (!MultiByteToWideChar(CP_UTF8, MB_ERR_INVALID_CHARS, value, -1, wide, count)) {
        free(wide); return NULL;
    }
    return wide;
}

static wchar_t* wysna_host_process_quote(const wchar_t* value) {
    size_t length = wcslen(value);
    wchar_t* quoted = (wchar_t*)wysna_alloc_safe((length * 2 + 3) * sizeof(wchar_t));
    size_t output = 0;
    quoted[output++] = L'"';
    size_t slashes = 0;
    for (size_t index = 0; index < length; index++) {
        if (value[index] == L'\\') { slashes++; continue; }
        if (value[index] == L'"') {
            for (size_t count = 0; count < slashes * 2 + 1; count++) quoted[output++] = L'\\';
            quoted[output++] = L'"';
        } else {
            for (size_t count = 0; count < slashes; count++) quoted[output++] = L'\\';
            quoted[output++] = value[index];
        }
        slashes = 0;
    }
    for (size_t count = 0; count < slashes * 2; count++) quoted[output++] = L'\\';
    quoted[output++] = L'"';
    quoted[output] = 0;
    return quoted;
}

static int64_t wysna_host_process_run(void* executable_handle, void* arguments_handle, int64_t timeout_ms) {
    wysna_host_error_code = 0; wysna_host_error_domain = 0;
    if (timeout_ms < 1 || timeout_ms > 600000) return wysna_host_process_error(EINVAL);
    char* executable = NULL; char** argv = NULL; int argc = 0;
    if (!wysna_host_process_decode(executable_handle, arguments_handle, &executable, &argv, &argc)) {
        wysna_host_error_domain = 2; return -1;
    }
    wchar_t** wide_argv = (wchar_t**)wysna_alloc_safe(sizeof(wchar_t*) * (size_t)(argc + 1));
    size_t command_length = 1;
    for (int index = 0; index < argc; index++) {
        wchar_t* wide = wysna_host_process_wide(argv[index]);
        if (!wide) {
            for (int previous = 0; previous < index; previous++) {
                free(wide_argv[previous]);
            }
            for (int argument = 1; argument < argc; argument++) free(argv[argument]);
            free(wide_argv); free(argv); free(executable);
            wysna_host_error_code = GetLastError(); wysna_host_error_domain = 2; return -1;
        }
        wide_argv[index] = wysna_host_process_quote(wide);
        free(wide);
        command_length += wcslen(wide_argv[index]) + 1;
    }
    wide_argv[argc] = NULL;
    wchar_t* application = wysna_host_process_wide(executable);
    wchar_t* command_line = (wchar_t*)wysna_alloc_safe(command_length * sizeof(wchar_t));
    size_t position = 0;
    for (int index = 0; index < argc; index++) {
        if (index) command_line[position++] = L' ';
        size_t length = wcslen(wide_argv[index]);
        memcpy(command_line + position, wide_argv[index], length * sizeof(wchar_t));
        position += length;
        free(wide_argv[index]);
        if (index > 0) free(argv[index]);
    }
    command_line[position] = 0;
    free(wide_argv); free(argv); free(executable);
    HANDLE job = CreateJobObjectW(NULL, NULL);
    if (job) {
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits;
        memset(&limits, 0, sizeof(limits));
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation,
                                     &limits, sizeof(limits))) {
            CloseHandle(job); job = NULL;
        }
    }
    if (!application) {
        DWORD error = GetLastError();
        if (job) CloseHandle(job);
        free(command_line); wysna_host_error_code = error; wysna_host_error_domain = 2; return -1;
    }
    STARTUPINFOW startup; PROCESS_INFORMATION process;
    memset(&startup, 0, sizeof(startup)); memset(&process, 0, sizeof(process));
    startup.cb = sizeof(startup); startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
    startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    DWORD creation_flags = CREATE_NEW_PROCESS_GROUP | (job ? CREATE_SUSPENDED : 0);
    BOOL started = CreateProcessW(application, command_line, NULL, NULL, TRUE, creation_flags,
                                  NULL, NULL, &startup, &process);
    DWORD error = started ? 0 : GetLastError();
    free(application); free(command_line);
    if (!started) {
        if (job) CloseHandle(job);
        wysna_host_error_code = error; wysna_host_error_domain = 2; return -1;
    }
    int suspended = job != NULL;
    if (job && !AssignProcessToJobObject(job, process.hProcess)) {
        CloseHandle(job); job = NULL;
    }
    if (suspended && ResumeThread(process.hThread) == (DWORD)-1) {
        error = GetLastError();
        if (job) TerminateJobObject(job, error);
        else TerminateProcess(process.hProcess, error);
        WaitForSingleObject(process.hProcess, INFINITE);
        CloseHandle(process.hThread); CloseHandle(process.hProcess);
        if (job) CloseHandle(job);
        wysna_host_error_code = error; wysna_host_error_domain = 2; return -1;
    }
    DWORD waited = WaitForSingleObject(process.hProcess, (DWORD)timeout_ms);
    if (waited == WAIT_TIMEOUT) {
        if (job) TerminateJobObject(job, ERROR_TIMEOUT);
        else TerminateProcess(process.hProcess, ERROR_TIMEOUT);
        WaitForSingleObject(process.hProcess, INFINITE);
        CloseHandle(process.hThread); CloseHandle(process.hProcess);
        if (job) CloseHandle(job);
        wysna_host_error_code = ERROR_TIMEOUT; wysna_host_error_domain = 2; return -1;
    }
    if (waited != WAIT_OBJECT_0) {
        error = GetLastError();
        if (job) TerminateJobObject(job, error);
        else TerminateProcess(process.hProcess, error);
        WaitForSingleObject(process.hProcess, INFINITE);
        CloseHandle(process.hThread); CloseHandle(process.hProcess);
        if (job) CloseHandle(job);
        wysna_host_error_code = error; wysna_host_error_domain = 2; return -1;
    }
    DWORD exit_code = 0;
    BOOL got_code = GetExitCodeProcess(process.hProcess, &exit_code);
    error = got_code ? 0 : GetLastError();
    CloseHandle(process.hThread); CloseHandle(process.hProcess);
    if (job) CloseHandle(job);
    if (!got_code) { wysna_host_error_code = error; wysna_host_error_domain = 2; return -1; }
    return (int64_t)exit_code;
}
#else
static int64_t wysna_host_process_run(void* executable_handle, void* arguments_handle, int64_t timeout_ms) {
    wysna_host_error_code = 0; wysna_host_error_domain = 0;
    if (timeout_ms < 1 || timeout_ms > 600000) return wysna_host_process_error(EINVAL);
    char* executable = NULL; char** argv = NULL; int argc = 0;
    if (!wysna_host_process_decode(executable_handle, arguments_handle, &executable, &argv, &argc)) return -1;
    argv[0] = executable;
    int error_pipe[2];
    if (pipe(error_pipe) != 0) {
        int error = errno;
        for (int index = 1; index < argc; index++) free(argv[index]);
        free(argv); free(executable); return wysna_host_process_error(error);
    }
    if (fcntl(error_pipe[1], F_SETFD, FD_CLOEXEC) == -1) {
        int error = errno; close(error_pipe[0]); close(error_pipe[1]);
        for (int index = 1; index < argc; index++) free(argv[index]);
        free(argv); free(executable); return wysna_host_process_error(error);
    }
    struct timespec started;
    if (clock_gettime(CLOCK_MONOTONIC, &started) != 0) {
        int error = errno; close(error_pipe[0]); close(error_pipe[1]);
        for (int index = 1; index < argc; index++) free(argv[index]);
        free(argv); free(executable); return wysna_host_process_error(error);
    }
    pid_t child = fork();
    if (child < 0) {
        int error = errno; close(error_pipe[0]); close(error_pipe[1]);
        for (int index = 1; index < argc; index++) free(argv[index]);
        free(argv); free(executable); return wysna_host_process_error(error);
    }
    if (child == 0) {
        close(error_pipe[0]); setpgid(0, 0);
        execvp(executable, argv);
        int error = errno;
        (void)write(error_pipe[1], &error, sizeof(error));
        _exit(127);
    }
    close(error_pipe[1]); (void)setpgid(child, child);
    int exec_error = 0;
    ssize_t received;
    do { received = read(error_pipe[0], &exec_error, sizeof(exec_error)); } while (received < 0 && errno == EINTR);
    close(error_pipe[0]);
    if (received < 0) {
        int error = errno; (void)kill(-child, SIGKILL);
        int status; while (waitpid(child, &status, 0) < 0 && errno == EINTR) { }
        for (int index = 1; index < argc; index++) free(argv[index]);
        free(argv); free(executable); return wysna_host_process_error(error);
    }
    if (received > 0) {
        int status; while (waitpid(child, &status, 0) < 0 && errno == EINTR) { }
        for (int index = 1; index < argc; index++) free(argv[index]);
        free(argv); free(executable); return wysna_host_process_error(exec_error);
    }

    struct timespec current;
    int status = 0;
    for (;;) {
        pid_t waited = waitpid(child, &status, WNOHANG);
        if (waited == child) break;
        if (waited < 0 && errno != EINTR) {
            int error = errno;
            for (int index = 1; index < argc; index++) free(argv[index]);
            free(argv); free(executable); return wysna_host_process_error(error);
        }
        if (clock_gettime(CLOCK_MONOTONIC, &current) != 0) {
            int error = errno; (void)kill(-child, SIGKILL);
            while (waitpid(child, &status, 0) < 0 && errno == EINTR) { }
            for (int index = 1; index < argc; index++) free(argv[index]);
            free(argv); free(executable); return wysna_host_process_error(error);
        }
        int64_t elapsed_ns = ((int64_t)current.tv_sec - started.tv_sec) * 1000000000LL
                           + ((int64_t)current.tv_nsec - started.tv_nsec);
        int64_t elapsed_ms = elapsed_ns / 1000000LL;
        if (elapsed_ms >= timeout_ms) {
            (void)kill(-child, SIGTERM);
            struct timespec grace_start, grace_now;
            if (clock_gettime(CLOCK_MONOTONIC, &grace_start) != 0) {
                int error = errno;
                (void)kill(-child, SIGKILL);
                while (waitpid(child, &status, 0) < 0 && errno == EINTR) { }
                for (int index = 1; index < argc; index++) free(argv[index]);
                free(argv); free(executable); return wysna_host_process_error(error);
            }
            int leader_stopped = 0;
            for (;;) {
                if (!leader_stopped) {
                    pid_t stopped = waitpid(child, &status, WNOHANG);
                    if (stopped == child) leader_stopped = 1;
                    else if (stopped < 0 && errno != EINTR) {
                        int error = errno; (void)kill(-child, SIGKILL);
                        for (int index = 1; index < argc; index++) free(argv[index]);
                        free(argv); free(executable); return wysna_host_process_error(error);
                    }
                }
                if (clock_gettime(CLOCK_MONOTONIC, &grace_now) != 0) {
                    int error = errno;
                    (void)kill(-child, SIGKILL);
                    if (!leader_stopped) while (waitpid(child, &status, 0) < 0 && errno == EINTR) { }
                    for (int index = 1; index < argc; index++) free(argv[index]);
                    free(argv); free(executable); return wysna_host_process_error(error);
                }
                int64_t grace_ns = ((int64_t)grace_now.tv_sec - grace_start.tv_sec) * 1000000000LL
                                 + ((int64_t)grace_now.tv_nsec - grace_start.tv_nsec);
                int64_t grace_ms = grace_ns / 1000000LL;
                if (grace_ms >= 100) {
                    (void)kill(-child, SIGKILL);
                    if (!leader_stopped) while (waitpid(child, &status, 0) < 0 && errno == EINTR) { }
                    break;
                }
                struct timeval delay = {0, 10000}; (void)select(0, NULL, NULL, NULL, &delay);
            }
            for (int index = 1; index < argc; index++) free(argv[index]);
            free(argv); free(executable); return wysna_host_process_error(ETIMEDOUT);
        }
        struct timeval delay = {0, 10000}; (void)select(0, NULL, NULL, NULL, &delay);
    }
    for (int index = 1; index < argc; index++) free(argv[index]);
    free(argv); free(executable);
    if (WIFEXITED(status)) return (int64_t)WEXITSTATUS(status);
    if (WIFSIGNALED(status)) return 128 + WTERMSIG(status);
    return wysna_host_process_error(EIO);
}
#endif
#endif /* !__wasi__ */

#if defined(WYSNA_HAS_HTTP) && !defined(__wasi__)
#include <curl/curl.h>
#if LIBCURL_VERSION_NUM < 0x080300
#error "std/http requires libcurl 8.3.0 or newer for bounded response headers"
#endif

#define WYSNA_HTTP_INVALID_REQUEST 1
#define WYSNA_HTTP_UNAVAILABLE 2
#define WYSNA_HTTP_TRANSPORT 3
#define WYSNA_HTTP_TLS 4
#define WYSNA_HTTP_TIMEOUT 5
#define WYSNA_HTTP_LIMIT 6
#define WYSNA_HTTP_INVALID_RESPONSE 7
#define WYSNA_HTTP_MAX_BODY (1024u * 1024u)
#define WYSNA_HTTP_MAX_HEADERS (32u * 1024u)
#define WYSNA_HTTP_MAX_HEADER_FIELDS 100u
#define WYSNA_HTTP_MAX_URL 8192u

typedef struct WysnaHttpTransfer {
    WysnaHostEntry* headers;
    WysnaHostEntry* body;
    size_t max_body;
    size_t header_bytes;
    size_t header_fields;
    long status;
    int error_kind;
} WysnaHttpTransfer;

static bool wysna_http_curl_initialized = false;

static int64_t wysna_host_http_fail(int kind, int64_t code) {
    wysna_host_error_code = code ? code : EIO;
    wysna_host_error_domain = 3;
    return -kind;
}

static int wysna_http_token_byte(unsigned char value) {
    return (value >= '0' && value <= '9') || (value >= 'A' && value <= 'Z')
        || (value >= 'a' && value <= 'z') || strchr("!#$%&'*+-.^_`|~", value) != NULL;
}

static int wysna_http_ascii_header(const unsigned char* data, size_t length) {
    for (size_t index = 0; index < length; index++)
        if ((data[index] < 32 && data[index] != '\t') || data[index] > 126) return 0;
    return 1;
}

static int wysna_http_prefix_case(const char* value, const char* prefix) {
    size_t index = 0;
    while (prefix[index]) {
        if (!value[index] || tolower((unsigned char)value[index]) != tolower((unsigned char)prefix[index]))
            return 0;
        index++;
    }
    return 1;
}

static size_t wysna_http_write_body(char* data, size_t size, size_t count, void* opaque) {
    WysnaHttpTransfer* transfer = (WysnaHttpTransfer*)opaque;
    if (size && count > SIZE_MAX / size) {
        transfer->error_kind = WYSNA_HTTP_LIMIT;
        return 0;
    }
    size_t length = size * count;
    if (length > transfer->max_body - transfer->body->length) {
        transfer->error_kind = WYSNA_HTTP_LIMIT;
        return 0;
    }
    if (length > 0) {
        if (transfer->body->length + length > transfer->body->capacity) {
            size_t capacity = transfer->body->capacity ? transfer->body->capacity : 256;
            while (capacity < transfer->body->length + length) {
                if (capacity > SIZE_MAX / 2) { capacity = transfer->body->length + length; break; }
                capacity *= 2;
            }
            transfer->body->data = wysna_realloc_safe(transfer->body->data, capacity);
            transfer->body->capacity = capacity;
        }
        memcpy((unsigned char*)transfer->body->data + transfer->body->length, data, length);
        transfer->body->length += length;
    }
    return length;
}

static size_t wysna_http_write_headers(char* data, size_t size, size_t count, void* opaque) {
    WysnaHttpTransfer* transfer = (WysnaHttpTransfer*)opaque;
    if (size && count > SIZE_MAX / size) {
        transfer->error_kind = WYSNA_HTTP_LIMIT;
        return 0;
    }
    size_t length = size * count;
    if (length >= 5 && memcmp(data, "HTTP/", 5) == 0) {
        if (length > WYSNA_HTTP_MAX_HEADERS) {
            transfer->error_kind = WYSNA_HTTP_LIMIT;
            return 0;
        }
        const char* status = memchr(data, ' ', length);
        if (!status || status + 4 > data + length || status[1] < '1' || status[1] > '5'
                || status[2] < '0' || status[2] > '9' || status[3] < '0' || status[3] > '9') {
            transfer->error_kind = WYSNA_HTTP_INVALID_RESPONSE;
            return 0;
        }
        transfer->status = (long)(status[1] - '0') * 100L
                         + (long)(status[2] - '0') * 10L + (long)(status[3] - '0');
        transfer->headers->length = 0;
        transfer->header_bytes = length;
        transfer->header_fields = 0;
        return length;
    }
    if ((length == 2 && data[0] == '\r' && data[1] == '\n')
            || (length == 1 && data[0] == '\n')) {
        if (length > WYSNA_HTTP_MAX_HEADERS - transfer->header_bytes) {
            transfer->error_kind = WYSNA_HTTP_LIMIT;
            return 0;
        }
        transfer->header_bytes += length;
        return length;
    }
    if (transfer->status < 100 || transfer->status > 599
            || transfer->header_fields >= WYSNA_HTTP_MAX_HEADER_FIELDS
            || length > WYSNA_HTTP_MAX_HEADERS - transfer->header_bytes) {
        transfer->error_kind = WYSNA_HTTP_LIMIT;
        return 0;
    }
    size_t content_length = length;
    while (content_length > 0 && (data[content_length - 1] == '\r' || data[content_length - 1] == '\n'))
        content_length--;
    unsigned char* colon = (unsigned char*)memchr(data, ':', content_length);
    if (!colon || colon == (unsigned char*)data
            || !wysna_http_ascii_header((unsigned char*)data, content_length)) {
        transfer->error_kind = WYSNA_HTTP_INVALID_RESPONSE;
        return 0;
    }
    for (unsigned char* cursor = (unsigned char*)data; cursor < colon; cursor++) {
        if (!wysna_http_token_byte(*cursor)) {
            transfer->error_kind = WYSNA_HTTP_INVALID_RESPONSE;
            return 0;
        }
    }
    if (content_length + 1 > WYSNA_HTTP_MAX_HEADERS - transfer->headers->length) {
        transfer->error_kind = WYSNA_HTTP_LIMIT;
        return 0;
    }
    size_t stored = wysna_host_buffer_append_bytes(transfer->headers, data, content_length);
    if (stored != content_length || wysna_host_buffer_append_bytes(transfer->headers, "\n", 1) != 1) {
        transfer->error_kind = WYSNA_HTTP_LIMIT;
        return 0;
    }
    transfer->header_bytes += length;
    transfer->header_fields++;
    return length;
}

static int wysna_http_forbidden_header(const unsigned char* name, size_t length) {
    static const char* names[] = {
        "host", "content-length", "transfer-encoding", "connection", "proxy-authorization"
    };
    for (size_t candidate = 0; candidate < sizeof(names) / sizeof(names[0]); candidate++) {
        size_t expected = strlen(names[candidate]);
        if (length != expected) continue;
        size_t index = 0;
        while (index < length && (unsigned char)tolower(name[index]) == (unsigned char)names[candidate][index]) index++;
        if (index == length) return 1;
    }
    return 0;
}

static int64_t wysna_host_http_request(void* url_handle, void* method_handle,
        void* request_headers_handle, void* request_body_handle,
        void* response_headers_handle, void* response_body_handle,
        int64_t timeout_ms, int64_t max_response_bytes) {
    wysna_host_error_code = 0;
    wysna_host_error_domain = 0;
    WysnaHostEntry* url = wysna_host_find(url_handle, 1);
    WysnaHostEntry* method = wysna_host_find(method_handle, 1);
    WysnaHostEntry* request_headers = wysna_host_find(request_headers_handle, 1);
    WysnaHostEntry* request_body = wysna_host_find(request_body_handle, 1);
    WysnaHostEntry* response_headers = wysna_host_find(response_headers_handle, 1);
    WysnaHostEntry* response_body = wysna_host_find(response_body_handle, 1);
    WysnaHostEntry* all[] = {url, method, request_headers, request_body, response_headers, response_body};
    for (size_t left = 0; left < sizeof(all) / sizeof(all[0]); left++) {
        if (!all[left]) return wysna_host_http_fail(WYSNA_HTTP_INVALID_REQUEST, EBADF);
        for (size_t right = left + 1; right < sizeof(all) / sizeof(all[0]); right++)
            if (all[left] == all[right]) return wysna_host_http_fail(WYSNA_HTTP_INVALID_REQUEST, EINVAL);
    }
    if (timeout_ms < 1 || timeout_ms > 30000 || max_response_bytes < 1
            || max_response_bytes > WYSNA_HTTP_MAX_BODY || url->length > WYSNA_HTTP_MAX_URL
            || request_body->length > WYSNA_HTTP_MAX_BODY
            || request_headers->length > WYSNA_HTTP_MAX_HEADERS || method->length == 0
            || method->length > 16)
        return wysna_host_http_fail(WYSNA_HTTP_INVALID_REQUEST, EINVAL);
    response_headers->length = 0;
    response_body->length = 0;
    if (!url->length || !method->length || memchr(url->data, 0, url->length)
            || memchr(method->data, 0, method->length)
            || memchr(request_headers->data, 0, request_headers->length))
        return wysna_host_http_fail(WYSNA_HTTP_INVALID_REQUEST, EINVAL);
    const unsigned char* url_text = (const unsigned char*)url->data;
    for (size_t index = 0; index < url->length; index++)
        if (url_text[index] <= 32 || url_text[index] > 126)
            return wysna_host_http_fail(WYSNA_HTTP_INVALID_REQUEST, EINVAL);
    char* url_copy = (char*)wysna_alloc_safe(url->length + 1);
    char* method_copy = (char*)wysna_alloc_safe(method->length + 1);
    memcpy(url_copy, url->data, url->length); url_copy[url->length] = 0;
    memcpy(method_copy, method->data, method->length); method_copy[method->length] = 0;
    if ((!wysna_http_prefix_case(url_copy, "http://") && !wysna_http_prefix_case(url_copy, "https://"))
            || strchr(url_copy, '#') != NULL) {
        free(url_copy); free(method_copy);
        return wysna_host_http_fail(WYSNA_HTTP_INVALID_REQUEST, EINVAL);
    }
    size_t authority_start = wysna_http_prefix_case(url_copy, "http://") ? 7 : 8;
    char* authority_end = strpbrk(url_copy + authority_start, "/?");
    if (!authority_end) authority_end = url_copy + strlen(url_copy);
    if (memchr(url_copy + authority_start, '@', (size_t)(authority_end - (url_copy + authority_start)))) {
        free(url_copy); free(method_copy);
        return wysna_host_http_fail(WYSNA_HTTP_INVALID_REQUEST, EINVAL);
    }
    if (strcmp(method_copy, "GET") && strcmp(method_copy, "POST") && strcmp(method_copy, "PUT")
            && strcmp(method_copy, "PATCH") && strcmp(method_copy, "DELETE") && strcmp(method_copy, "HEAD")) {
        free(url_copy); free(method_copy);
        return wysna_host_http_fail(WYSNA_HTTP_INVALID_REQUEST, EINVAL);
    }
    if ((strcmp(method_copy, "GET") == 0 || strcmp(method_copy, "HEAD") == 0) && request_body->length) {
        free(url_copy); free(method_copy);
        return wysna_host_http_fail(WYSNA_HTTP_INVALID_REQUEST, EINVAL);
    }

    struct curl_slist* headers = NULL;
    size_t offset = 0;
    while (offset < request_headers->length) {
        size_t end = offset;
        while (end < request_headers->length && ((char*)request_headers->data)[end] != '\n') end++;
        if (end == request_headers->length || end == offset
                || memchr((char*)request_headers->data + offset, '\r', end - offset)) {
            curl_slist_free_all(headers); free(url_copy); free(method_copy);
            return wysna_host_http_fail(WYSNA_HTTP_INVALID_REQUEST, EINVAL);
        }
        unsigned char* line = (unsigned char*)request_headers->data + offset;
        size_t length = end - offset;
        unsigned char* colon = (unsigned char*)memchr(line, ':', length);
        if (!colon || colon == line || wysna_http_forbidden_header(line, (size_t)(colon - line))
                || !wysna_http_ascii_header(line, length)) {
            curl_slist_free_all(headers); free(url_copy); free(method_copy);
            return wysna_host_http_fail(WYSNA_HTTP_INVALID_REQUEST, EINVAL);
        }
        for (unsigned char* cursor = line; cursor < colon; cursor++) {
            if (!wysna_http_token_byte(*cursor)) {
                curl_slist_free_all(headers); free(url_copy); free(method_copy);
                return wysna_host_http_fail(WYSNA_HTTP_INVALID_REQUEST, EINVAL);
            }
        }
        char* header = (char*)wysna_alloc_safe(length + 1);
        memcpy(header, line, length); header[length] = 0;
        struct curl_slist* appended = curl_slist_append(headers, header);
        free(header);
        if (!appended) {
            curl_slist_free_all(headers); free(url_copy); free(method_copy);
            return wysna_host_http_fail(WYSNA_HTTP_TRANSPORT, CURLE_OUT_OF_MEMORY);
        }
        headers = appended;
        offset = end + 1;
    }

    if (!wysna_http_curl_initialized) {
        CURLcode initialized = curl_global_init(CURL_GLOBAL_DEFAULT);
        if (initialized != CURLE_OK) {
            curl_slist_free_all(headers); free(url_copy); free(method_copy);
            return wysna_host_http_fail(WYSNA_HTTP_UNAVAILABLE, initialized);
        }
        wysna_http_curl_initialized = true;
    }
    const curl_version_info_data* curl_version = curl_version_info(CURLVERSION_NOW);
    if (!curl_version || curl_version->version_num < 0x080300) {
        curl_slist_free_all(headers); free(url_copy); free(method_copy);
        return wysna_host_http_fail(WYSNA_HTTP_UNAVAILABLE, CURLE_NOT_BUILT_IN);
    }
    CURL* curl = curl_easy_init();
    if (!curl) {
        curl_slist_free_all(headers); free(url_copy); free(method_copy);
        return wysna_host_http_fail(WYSNA_HTTP_UNAVAILABLE, CURLE_OUT_OF_MEMORY);
    }
    WysnaHttpTransfer transfer = {response_headers, response_body,
        (size_t)max_response_bytes, 0, 0, 0, 0};
    CURLcode result = CURLE_OK;
#define WYSNA_CURL_SET(option, value) do { result = curl_easy_setopt(curl, option, value); if (result != CURLE_OK) goto wysna_http_done; } while (0)
    WYSNA_CURL_SET(CURLOPT_URL, url_copy);
    WYSNA_CURL_SET(CURLOPT_CUSTOMREQUEST, method_copy);
    WYSNA_CURL_SET(CURLOPT_HTTPHEADER, headers);
    WYSNA_CURL_SET(CURLOPT_WRITEFUNCTION, wysna_http_write_body);
    WYSNA_CURL_SET(CURLOPT_WRITEDATA, &transfer);
    WYSNA_CURL_SET(CURLOPT_HEADERFUNCTION, wysna_http_write_headers);
    WYSNA_CURL_SET(CURLOPT_HEADERDATA, &transfer);
    WYSNA_CURL_SET(CURLOPT_FOLLOWLOCATION, 0L);
    WYSNA_CURL_SET(CURLOPT_MAXREDIRS, 0L);
    WYSNA_CURL_SET(CURLOPT_SSL_VERIFYPEER, 1L);
    WYSNA_CURL_SET(CURLOPT_SSL_VERIFYHOST, 2L);
    WYSNA_CURL_SET(CURLOPT_TIMEOUT_MS, (long)timeout_ms);
    WYSNA_CURL_SET(CURLOPT_CONNECTTIMEOUT_MS, (long)(timeout_ms < 5000 ? timeout_ms : 5000));
    WYSNA_CURL_SET(CURLOPT_NOSIGNAL, 1L);
    WYSNA_CURL_SET(CURLOPT_NETRC, (long)CURL_NETRC_IGNORED);
    WYSNA_CURL_SET(CURLOPT_HTTP_VERSION, (long)CURL_HTTP_VERSION_1_1);
#if LIBCURL_VERSION_NUM >= 0x075500
    WYSNA_CURL_SET(CURLOPT_PROTOCOLS_STR, "http,https");
#else
    WYSNA_CURL_SET(CURLOPT_PROTOCOLS, (long)(CURLPROTO_HTTP | CURLPROTO_HTTPS));
#endif
    if (strcmp(method_copy, "HEAD") == 0) WYSNA_CURL_SET(CURLOPT_NOBODY, 1L);
    if (request_body->length || strcmp(method_copy, "POST") == 0
            || strcmp(method_copy, "PUT") == 0 || strcmp(method_copy, "PATCH") == 0) {
        WYSNA_CURL_SET(CURLOPT_POSTFIELDS, request_body->length ? request_body->data : "");
        WYSNA_CURL_SET(CURLOPT_POSTFIELDSIZE_LARGE, (curl_off_t)request_body->length);
    }
    result = curl_easy_perform(curl);
    if (result == CURLE_OK && transfer.error_kind == 0) {
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &transfer.status);
    }
wysna_http_done:
#undef WYSNA_CURL_SET
    if (result != CURLE_OK || transfer.error_kind != 0) {
        int kind = transfer.error_kind;
        if (!kind) {
            if (result == CURLE_OPERATION_TIMEDOUT) kind = WYSNA_HTTP_TIMEOUT;
            else if (result == CURLE_PEER_FAILED_VERIFICATION || result == CURLE_SSL_CONNECT_ERROR
                     || result == CURLE_SSL_CERTPROBLEM) kind = WYSNA_HTTP_TLS;
            else if (result == CURLE_URL_MALFORMAT || result == CURLE_UNSUPPORTED_PROTOCOL
                     || result == CURLE_BAD_FUNCTION_ARGUMENT) kind = WYSNA_HTTP_INVALID_REQUEST;
            else kind = WYSNA_HTTP_TRANSPORT;
        }
        curl_easy_cleanup(curl); curl_slist_free_all(headers); free(url_copy); free(method_copy);
        return wysna_host_http_fail(kind, result);
    }
    long status = transfer.status;
    curl_easy_cleanup(curl); curl_slist_free_all(headers); free(url_copy); free(method_copy);
    if (status < 100 || status > 599)
        return wysna_host_http_fail(WYSNA_HTTP_INVALID_RESPONSE, EIO);
    return (int64_t)status;
}
#endif /* WYSNA_HAS_HTTP && !__wasi__ */

#endif
