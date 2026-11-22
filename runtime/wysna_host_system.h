/* Host system services for std/time, std/io and std/environment.
 * Included by wysna_host.h after the buffer table (wysna_host_find, wysna_host_buffer_append_bytes). */
#ifndef WYSNA_HOST_SYSTEM_H
#define WYSNA_HOST_SYSTEM_H

static inline int64_t wysna_host_wall_ms(void) {
#ifdef WYSNA_DENY_CAP_CLOCK
    wysna_sandbox_deny("clock");
    return -1;
#endif
#ifdef _WIN32
    FILETIME ft;
    ULARGE_INTEGER value;
    GetSystemTimeAsFileTime(&ft);
    value.LowPart = ft.dwLowDateTime;
    value.HighPart = ft.dwHighDateTime;
    /* FILETIME counts 100ns ticks since 1601-01-01; shift to the Unix epoch. */
    return (int64_t)((value.QuadPart - 116444736000000000ULL) / 10000ULL);
#elif defined(CLOCK_REALTIME)
    struct timespec ts;
    if (clock_gettime(CLOCK_REALTIME, &ts) == 0) {
        return (int64_t)ts.tv_sec * 1000LL + (int64_t)(ts.tv_nsec / 1000000LL);
    }
    return -1;
#else
    struct timeval tv;
    if (gettimeofday(&tv, NULL) == 0) {
        return (int64_t)tv.tv_sec * 1000LL + (int64_t)(tv.tv_usec / 1000LL);
    }
    return -1;
#endif
}

/* Blocking sleep. Returns 0 on success, -1 for a negative duration. */
static inline int64_t wysna_host_sleep(int64_t ms) {
#ifdef WYSNA_DENY_CAP_CLOCK
    (void)ms;
    wysna_sandbox_deny("clock");
    return -1;
#endif
    if (ms < 0) return -1;
    wysna_host_sleep_ms(ms);
    return 0;
}

static inline int64_t wysna_host_stdout_flush(void) {
#ifdef WYSNA_DENY_CAP_STDIO
    wysna_sandbox_deny("stdio");
    return -1;
#endif
    return fflush(stdout) == 0 ? 0 : -1;
}

/* Reads one line from stdin into the buffer (without the line terminator).
 * Returns 1 when a line was produced, 0 on end of input with no data, -1 on failure. */
static inline int64_t wysna_host_stdin_read_line(void* output_handle) {
#ifdef WYSNA_DENY_CAP_STDIO
    (void)output_handle;
    wysna_sandbox_deny("stdio");
    return -1;
#endif
    WysnaHostEntry* output = wysna_host_find(output_handle, 1);
    if (!output) return -1;
    output->length = 0;
    int c;
    bool any = false;
    while ((c = fgetc(stdin)) != EOF) {
        any = true;
        if (c == '\n') break;
        char byte = (char)c;
        if (wysna_host_buffer_append_bytes(output, &byte, 1) != 1) return -1;
    }
    if (!any) return ferror(stdin) ? -1 : 0;
    if (output->length > 0 && ((char*)output->data)[output->length - 1] == '\r') {
        output->length -= 1;
    }
    return 1;
}

/* Reads the remainder of stdin into the buffer. Returns the byte count or -1. */
static inline int64_t wysna_host_stdin_read_all(void* output_handle) {
#ifdef WYSNA_DENY_CAP_STDIO
    (void)output_handle;
    wysna_sandbox_deny("stdio");
    return -1;
#endif
    WysnaHostEntry* output = wysna_host_find(output_handle, 1);
    if (!output) return -1;
    output->length = 0;
    char chunk[4096];
    size_t got;
    while ((got = fread(chunk, 1, sizeof(chunk), stdin)) > 0) {
        if (wysna_host_buffer_append_bytes(output, chunk, got) != got) return -1;
    }
    if (ferror(stdin)) return -1;
    return (int64_t)output->length;
}

/* Writes the working directory into the buffer. Returns the byte count or -1. */
static inline int64_t wysna_host_getcwd(void* output_handle) {
#ifdef WYSNA_DENY_CAP_ENVIRONMENT
    (void)output_handle;
    wysna_sandbox_deny("environment");
    return -1;
#endif
    WysnaHostEntry* output = wysna_host_find(output_handle, 1);
    if (!output) return -1;
    output->length = 0;
#ifdef _WIN32
    wchar_t wide[32768];
    DWORD n = GetCurrentDirectoryW((DWORD)(sizeof(wide) / sizeof(wide[0])), wide);
    if (n == 0 || n >= (DWORD)(sizeof(wide) / sizeof(wide[0]))) return -1;
    int needed = WideCharToMultiByte(CP_UTF8, 0, wide, (int)n, NULL, 0, NULL, NULL);
    if (needed <= 0) return -1;
    char* utf8 = (char*)malloc((size_t)needed);
    if (!utf8) return -1;
    int converted = WideCharToMultiByte(CP_UTF8, 0, wide, (int)n, utf8, needed, NULL, NULL);
    size_t written = (converted == needed) ? wysna_host_buffer_append_bytes(output, utf8, (size_t)needed) : 0;
    free(utf8);
    if (converted != needed || written != (size_t)needed) return -1;
    return (int64_t)needed;
#else
    char path[4096];
    if (!getcwd(path, sizeof(path))) return -1;
    size_t length = strlen(path);
    if (wysna_host_buffer_append_bytes(output, path, length) != length) return -1;
    return (int64_t)length;
#endif
}

/* Reads exact `count` bytes from stdin into `output_handle`.
 * Returns `count` on full read, 0 on clean EOF at byte 0, -1 on short read/error. */
static inline int64_t wysna_host_stdin_read_exact(void* output_handle, int64_t count) {
#ifdef WYSNA_DENY_CAP_STDIO
    (void)output_handle; (void)count;
    wysna_sandbox_deny("stdio");
    return -1;
#endif
    if (count < 0 || count > 16777216LL) return -1;
    WysnaHostEntry* output = wysna_host_find(output_handle, 1);
    if (!output) return -1;
    output->length = 0;
    if (count == 0) return 0;
    size_t target = (size_t)count;
    size_t total = 0;
    char chunk[4096];
    while (total < target) {
        size_t want = target - total;
        if (want > sizeof(chunk)) want = sizeof(chunk);
        size_t got = fread(chunk, 1, want, stdin);
        if (got == 0) {
            if (total == 0 && feof(stdin) && !ferror(stdin)) return 0;
            return -1;
        }
        if (wysna_host_buffer_append_bytes(output, chunk, got) != got) return -1;
        total += got;
    }
    return (int64_t)total;
}

/* Writes raw bytes from `input_handle` to stdout without appending a newline. */
static inline int64_t wysna_host_stdout_write_raw(void* input_handle) {
#ifdef WYSNA_DENY_CAP_STDIO
    (void)input_handle;
    wysna_sandbox_deny("stdio");
    return -1;
#endif
    WysnaHostEntry* input = wysna_host_find(input_handle, 1);
    if (!input) return -1;
    if (input->length > 0) {
        if (fwrite(input->data, 1, input->length, stdout) != input->length) return -1;
    }
    return fflush(stdout) == 0 ? 0 : -1;
}

/* Writes the current executable's path into `output_handle`. Returns byte length or -1. */
static inline int64_t wysna_host_self_executable(void* output_handle) {
#ifdef WYSNA_DENY_CAP_ENVIRONMENT
    (void)output_handle;
    wysna_sandbox_deny("environment");
    return -1;
#endif
    WysnaHostEntry* output = wysna_host_find(output_handle, 1);
    if (!output) return -1;
    output->length = 0;
#ifdef _WIN32
    wchar_t wide[32768];
    DWORD n = GetModuleFileNameW(NULL, wide, (DWORD)(sizeof(wide) / sizeof(wide[0])));
    if (n == 0 || n >= (DWORD)(sizeof(wide) / sizeof(wide[0]))) return -1;
    int needed = WideCharToMultiByte(CP_UTF8, 0, wide, (int)n, NULL, 0, NULL, NULL);
    if (needed <= 0 || needed > 32768) return -1;
    char utf8[32768];
    if (WideCharToMultiByte(CP_UTF8, 0, wide, (int)n, utf8, needed, NULL, NULL) != needed) return -1;
    if (wysna_host_buffer_append_bytes(output, utf8, (size_t)needed) != (size_t)needed) return -1;
    return (int64_t)needed;
#elif defined(__APPLE__)
    uint32_t cap = 4096;
    char stack_buf[4096];
    char* path_buf = stack_buf;
    char* heap_buf = NULL;
    if (_NSGetExecutablePath(path_buf, &cap) != 0) {
        heap_buf = (char*)malloc((size_t)cap + 1);
        if (!heap_buf) return -1;
        path_buf = heap_buf;
        if (_NSGetExecutablePath(path_buf, &cap) != 0) { free(heap_buf); return -1; }
    }
    size_t len = strlen(path_buf);
    size_t written = wysna_host_buffer_append_bytes(output, path_buf, len);
    free(heap_buf);
    return written == len ? (int64_t)len : -1;
#elif defined(__wasi__)
    return -1;
#else
    char path[4096];
    ssize_t n = readlink("/proc/self/exe", path, sizeof(path) - 1);
    if (n <= 0) return -1;
    if (wysna_host_buffer_append_bytes(output, path, (size_t)n) != (size_t)n) return -1;
    return (int64_t)n;
#endif
}

#endif
