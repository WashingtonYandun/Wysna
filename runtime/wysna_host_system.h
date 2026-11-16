/* Host system services for std/time, std/io and std/environment.
 * Included by wysna_host.h after the buffer table (wysna_host_find, wysna_host_buffer_append_bytes). */
#ifndef WYSNA_HOST_SYSTEM_H
#define WYSNA_HOST_SYSTEM_H

static inline int64_t wysna_host_wall_ms(void) {
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
    if (ms < 0) return -1;
    wysna_host_sleep_ms(ms);
    return 0;
}

static inline int64_t wysna_host_stdout_flush(void) {
    return fflush(stdout) == 0 ? 0 : -1;
}

/* Reads one line from stdin into the buffer (without the line terminator).
 * Returns 1 when a line was produced, 0 on end of input with no data, -1 on failure. */
static inline int64_t wysna_host_stdin_read_line(void* output_handle) {
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
    WysnaHostEntry* output = wysna_host_find(output_handle, 1);
    if (!output) return -1;
    char path[4096];
#ifdef _WIN32
    if (!_getcwd(path, (int)sizeof(path))) return -1;
#else
    if (!getcwd(path, sizeof(path))) return -1;
#endif
    size_t length = strlen(path);
    output->length = 0;
    if (wysna_host_buffer_append_bytes(output, path, length) != length) return -1;
    return (int64_t)length;
}

#endif
