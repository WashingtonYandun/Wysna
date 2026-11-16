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
#include <pthread.h>
#include <signal.h>
#include <sys/select.h>
#include <sys/time.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>
#if (defined(__GNUC__) || defined(__clang__)) && !defined(__APPLE__)
#pragma weak pthread_create
#pragma weak pthread_join
#endif
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
#ifdef WYSNA_SHARED_RUNTIME
extern WysnaHostContext wysna_host_context;
#else
static WysnaHostContext wysna_host_context = {NULL, 1, false, 0, 0};
#endif
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

#include "wysna_host_process.h"
#include "wysna_host_paths.h"
#include "wysna_host_crypto.h"

#define WYSNA_TIMER_HANDLE_BASE 1000000
#define WYSNA_SPAWN_HANDLE_BASE 2000000LL

typedef struct WysnaAsyncTimer {
    int64_t id;
    int64_t deadline_ms;
    bool done;
    struct WysnaAsyncTimer* next;
} WysnaAsyncTimer;

typedef struct WysnaSpawnTask {
    int64_t id;
    bool done;
    bool joined;
    bool has_thread;
    WVal fn;
    WVal result;
#if defined(_WIN32)
    HANDLE thread_handle;
#elif !defined(__wasi__)
    pthread_t thread_id;
#endif
    struct WysnaSpawnTask* next;
} WysnaSpawnTask;

static WysnaAsyncTimer* wysna_async_timers = NULL;
static int64_t wysna_async_timer_next_id = WYSNA_TIMER_HANDLE_BASE;
static volatile int wysna_async_timer_lock = 0;
static WysnaSpawnTask* wysna_spawn_tasks = NULL;
static int64_t wysna_spawn_next_id = WYSNA_SPAWN_HANDLE_BASE;
static volatile int wysna_spawn_lock = 0;

static inline int64_t wysna_host_monotonic_ms(void) {
#ifdef _WIN32
    LARGE_INTEGER freq, counter;
    if (!QueryPerformanceFrequency(&freq) || freq.QuadPart == 0) return 0;
    QueryPerformanceCounter(&counter);
    return (int64_t)((counter.QuadPart / freq.QuadPart) * 1000LL +
                     ((counter.QuadPart % freq.QuadPart) * 1000LL) / freq.QuadPart);
#elif defined(CLOCK_MONOTONIC)
    struct timespec ts;
    if (clock_gettime(CLOCK_MONOTONIC, &ts) == 0) {
        return (int64_t)ts.tv_sec * 1000LL + (int64_t)(ts.tv_nsec / 1000000LL);
    }
    return 0;
#else
    struct timeval tv;
    if (gettimeofday(&tv, NULL) == 0) {
        return (int64_t)tv.tv_sec * 1000LL + (int64_t)(tv.tv_usec / 1000LL);
    }
    return 0;
#endif
}

static inline void wysna_host_sleep_ms(int64_t ms) {
    if (ms <= 0) return;
#ifdef _WIN32
    Sleep((DWORD)ms);
#elif defined(__wasi__)
    (void)ms;
#else
    struct timeval tv;
    tv.tv_sec = (long)(ms / 1000LL);
    tv.tv_usec = (int)((ms % 1000LL) * 1000LL);
    select(0, NULL, NULL, NULL, &tv);
#endif
}

#include "wysna_host_system.h"
#include "wysna_host_unicode.h"

static void wysna_async_shutdown(void) {
    for (;;) {
        wysna_spinlock_acquire(&wysna_spawn_lock);
        WysnaSpawnTask* head = wysna_spawn_tasks;
        wysna_spawn_tasks = NULL;
        wysna_spinlock_release(&wysna_spawn_lock);
        if (!head) break;
        while (head) {
            WysnaSpawnTask* cur = head;
            head = cur->next;
            if (cur->has_thread && !cur->joined) {
#if defined(_WIN32)
                WaitForSingleObject(cur->thread_handle, INFINITE);
                CloseHandle(cur->thread_handle);
#elif !defined(__wasi__)
                pthread_join(cur->thread_id, NULL);
#endif
                cur->joined = true;
            }
            wysna_release(cur->fn);
            wysna_release(cur->result);
            free(cur);
        }
    }
    wysna_spinlock_acquire(&wysna_async_timer_lock);
    WysnaAsyncTimer* t = wysna_async_timers;
    wysna_async_timers = NULL;
    wysna_spinlock_release(&wysna_async_timer_lock);
    while (t) {
        WysnaAsyncTimer* nxt = t->next;
        free(t);
        t = nxt;
    }
}

static inline void wysna_spawn_worker_run(WysnaSpawnTask* task) {
    WVal fn_local = task->fn;
    task->fn = wysna_unit();
    WVal out = wysna_call(fn_local, 0, NULL);
    wysna_release(fn_local);
    wysna_spinlock_acquire(&wysna_spawn_lock);
    task->result = out;
    task->done = true;
    wysna_spinlock_release(&wysna_spawn_lock);
}

#if !defined(_WIN32) && !defined(__wasi__)
static void* wysna_spawn_posix_entry(void* arg) {
    wysna_spawn_worker_run((WysnaSpawnTask*)arg);
    return NULL;
}
#elif defined(_WIN32)
static DWORD WINAPI wysna_spawn_win32_entry(LPVOID arg) {
    wysna_spawn_worker_run((WysnaSpawnTask*)arg);
    return 0;
}
#endif

static inline bool wysna_spawn_threads_available(void) {
#if defined(__wasi__)
    return false;
#elif !defined(_WIN32) && (defined(__GNUC__) || defined(__clang__)) && !defined(__APPLE__)
    typedef int (*WysnaPthreadCreateFn)(pthread_t*, const pthread_attr_t*, void*(*)(void*), void*);
    typedef int (*WysnaPthreadJoinFn)(pthread_t, void**);
    volatile WysnaPthreadCreateFn p_create = pthread_create;
    volatile WysnaPthreadJoinFn p_join = pthread_join;
    return p_create && p_join;
#else
    return true;
#endif
}

static inline int64_t wysna_async_spawn_handle(WVal fn) {
    wysna_step_tick();
    wysna_runtime_context.host_async_cleanup = wysna_async_shutdown;
    WysnaSpawnTask* task = (WysnaSpawnTask*)calloc(1, sizeof(WysnaSpawnTask));
    if (!task) return -1;
    task->fn = wysna_clone(fn);
    task->result = wysna_unit();
    task->done = false;
    task->joined = false;
    task->has_thread = false;
    wysna_spinlock_acquire(&wysna_spawn_lock);
    task->id = wysna_spawn_next_id++;
    task->next = wysna_spawn_tasks;
    wysna_spawn_tasks = task;
    int64_t task_id = task->id;
    wysna_spinlock_release(&wysna_spawn_lock);

    if (wysna_spawn_threads_available()) {
#if !defined(_WIN32) && !defined(__wasi__)
        if (pthread_create(&task->thread_id, NULL, wysna_spawn_posix_entry, task) == 0) {
            task->has_thread = true;
            return task_id;
        }
#elif defined(_WIN32)
        HANDLE h = CreateThread(NULL, 0, wysna_spawn_win32_entry, task, 0, NULL);
        if (h != NULL) {
            task->thread_handle = h;
            task->has_thread = true;
            return task_id;
        }
#endif
    }
    wysna_spawn_worker_run(task);
    return task_id;
}

static inline int64_t wysna_spawn_poll(int64_t handle_id) {
    bool is_done = false;
    bool need_join = false;
#if defined(_WIN32)
    HANDLE win_h = NULL;
#elif !defined(__wasi__)
    pthread_t pt = {0};
#endif
    wysna_spinlock_acquire(&wysna_spawn_lock);
    WysnaSpawnTask* cur = wysna_spawn_tasks;
    while (cur && cur->id != handle_id) cur = cur->next;
    if (!cur) {
        wysna_spinlock_release(&wysna_spawn_lock);
        return -1;
    }
    is_done = cur->done;
    if (is_done && cur->has_thread && !cur->joined) {
        cur->joined = true;
        need_join = true;
#if defined(_WIN32)
        win_h = cur->thread_handle;
#elif !defined(__wasi__)
        pt = cur->thread_id;
#endif
    }
    wysna_spinlock_release(&wysna_spawn_lock);
    if (need_join) {
#if defined(_WIN32)
        WaitForSingleObject(win_h, INFINITE);
        CloseHandle(win_h);
#elif !defined(__wasi__)
        pthread_join(pt, NULL);
#endif
    }
    return is_done ? 1 : 0;
}

static inline int64_t wysna_spawn_pending_count(void) {
    int64_t pending = 0;
    wysna_spinlock_acquire(&wysna_spawn_lock);
    WysnaSpawnTask* cur = wysna_spawn_tasks;
    while (cur) {
        if (!cur->done) pending++;
        cur = cur->next;
    }
    wysna_spinlock_release(&wysna_spawn_lock);
    return pending;
}

static inline WVal wysna_spawn_clone_result(int64_t handle_id) {
    for (;;) {
        int64_t st = wysna_spawn_poll(handle_id);
        if (st < 0) {
            wysna_runtime_fail("W4003", "%s", "Invalid spawned task handle");
            return wysna_unit();
        }
        if (st == 1) break;
        wysna_host_sleep_ms(1);
    }
    WVal out = wysna_unit();
    wysna_spinlock_acquire(&wysna_spawn_lock);
    WysnaSpawnTask* cur = wysna_spawn_tasks;
    while (cur && cur->id != handle_id) cur = cur->next;
    if (cur) out = wysna_clone(cur->result);
    wysna_spinlock_release(&wysna_spawn_lock);
    return out;
}

static inline WVal wysna_spawn_take_option(WVal handle_val, WVal fallback) {
    int64_t handle_id = (handle_val.tag == W_INT) ? handle_val.as.i : 0;
    if (handle_id >= WYSNA_SPAWN_HANDLE_BASE) {
        return wysna_enum_payload("Option", "Some", wysna_spawn_clone_result(handle_id));
    }
    return wysna_clone(fallback);
}

static inline int64_t wysna_timer_poll_or_create(int64_t transfer_id) {
    if (transfer_id <= -1000) {
        int64_t duration_ms = -(transfer_id + 1000);
        if (duration_ms < 0) duration_ms = 0;
        WysnaAsyncTimer* t = (WysnaAsyncTimer*)calloc(1, sizeof(WysnaAsyncTimer));
        if (!t) return -1;
        wysna_runtime_context.host_async_cleanup = wysna_async_shutdown;
        int64_t now = wysna_host_monotonic_ms();
        wysna_spinlock_acquire(&wysna_async_timer_lock);
        t->id = wysna_async_timer_next_id++;
        t->deadline_ms = now + duration_ms;
        t->done = (duration_ms == 0);
        t->next = wysna_async_timers;
        wysna_async_timers = t;
        int64_t id = t->id;
        wysna_spinlock_release(&wysna_async_timer_lock);
        return id;
    }
    int64_t now = wysna_host_monotonic_ms();
    int64_t result = -1;
    wysna_spinlock_acquire(&wysna_async_timer_lock);
    WysnaAsyncTimer* cur = wysna_async_timers;
    while (cur) {
        if (cur->id == transfer_id) {
            if (!cur->done && now >= cur->deadline_ms) {
                cur->done = true;
            }
            result = cur->done ? 1 : 0;
            break;
        }
        cur = cur->next;
    }
    wysna_spinlock_release(&wysna_async_timer_lock);
    return result;
}

static inline int64_t wysna_timer_next_wait_ms(int64_t* out_pending) {
    int64_t now = wysna_host_monotonic_ms();
    int64_t pending = 0;
    int64_t min_rem = INT64_MAX;
    wysna_spinlock_acquire(&wysna_async_timer_lock);
    WysnaAsyncTimer* cur = wysna_async_timers;
    while (cur) {
        if (!cur->done) {
            int64_t rem = cur->deadline_ms - now;
            if (rem <= 0) {
                cur->done = true;
            } else {
                pending++;
                if (rem < min_rem) min_rem = rem;
            }
        }
        cur = cur->next;
    }
    wysna_spinlock_release(&wysna_async_timer_lock);
    if (out_pending) *out_pending = pending;
    return pending > 0 ? min_rem : 0;
}

static inline int64_t wysna_timer_multi_poll(int64_t timeout_ms, bool allow_sleep) {
    int64_t timer_pending = 0;
    int64_t min_rem = wysna_timer_next_wait_ms(&timer_pending);
    int64_t spawn_pending = wysna_spawn_pending_count();
    if (allow_sleep && (timer_pending > 0 || spawn_pending > 0) && timeout_ms > 0) {
        int64_t deadline = wysna_host_monotonic_ms() + timeout_ms;
        if (timer_pending > 0 && min_rem < timeout_ms) {
            deadline = wysna_host_monotonic_ms() + (min_rem > 0 ? min_rem : 1);
        }
        int64_t initial_spawns = spawn_pending;
        for (;;) {
            int64_t now = wysna_host_monotonic_ms();
            spawn_pending = wysna_spawn_pending_count();
            wysna_timer_next_wait_ms(&timer_pending);
            if (spawn_pending < initial_spawns || (timer_pending == 0 && spawn_pending == 0) || now >= deadline) {
                break;
            }
            wysna_host_sleep_ms(1);
        }
    }
    return timer_pending + spawn_pending;
}

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

#ifdef WYSNA_SHARED_RUNTIME
extern bool wysna_http_curl_initialized;
#else
static bool wysna_http_curl_initialized = false;
#endif

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

typedef struct WysnaAsyncHttpTransfer {
    int64_t id;
    CURL* curl;
    struct curl_slist* headers;
    char* url_copy;
    char* method_copy;
    unsigned char* header_buf;
    size_t header_len;
    size_t header_cap;
    unsigned char* body_buf;
    size_t body_len;
    size_t body_cap;
    size_t header_fields;
    size_t max_response_bytes;
    long status;
    int error_kind;
    int error_code;
    int done; /* 0: pending, 1: completed, -1: error */
    struct WysnaAsyncHttpTransfer* next;
} WysnaAsyncHttpTransfer;

static CURLM* wysna_http_multi = NULL;
static WysnaAsyncHttpTransfer* wysna_async_http_transfers = NULL;
static int64_t wysna_async_http_next_id = 1;

static size_t wysna_http_async_write_headers(char* buffer, size_t size, size_t nitems, void* userdata) {
    WysnaAsyncHttpTransfer* t = (WysnaAsyncHttpTransfer*)userdata;
    size_t length = size * nitems;
    if (t->error_kind != 0) return 0;
    if (t->header_len + length > WYSNA_HTTP_MAX_HEADERS) {
        t->error_kind = WYSNA_HTTP_LIMIT;
        return 0;
    }
    if (length >= 5 && memcmp(buffer, "HTTP/", 5) == 0) {
        t->header_len = 0;
        t->header_fields = 0;
        return length;
    }
    if (length == 0 || (length == 2 && buffer[0] == '\r' && buffer[1] == '\n') || (length == 1 && buffer[0] == '\n')) {
        return length;
    }
    if (t->header_fields >= WYSNA_HTTP_MAX_HEADER_FIELDS) {
        t->error_kind = WYSNA_HTTP_LIMIT;
        return 0;
    }
    size_t content_length = length;
    if (content_length && buffer[content_length - 1] == '\n') content_length--;
    if (content_length && buffer[content_length - 1] == '\r') content_length--;
    char* colon = (char*)memchr(buffer, ':', content_length);
    if (!colon || colon == buffer || !wysna_http_ascii_header((const unsigned char*)buffer, content_length)) {
        t->error_kind = WYSNA_HTTP_INVALID_RESPONSE;
        return 0;
    }
    for (char* cursor = buffer; cursor < colon; cursor++) {
        if (!wysna_http_token_byte((unsigned char)*cursor)) {
            t->error_kind = WYSNA_HTTP_INVALID_RESPONSE;
            return 0;
        }
    }
    if (t->header_len + content_length + 2 > t->header_cap) {
        size_t next_cap = t->header_cap ? t->header_cap * 2 : 1024;
        while (next_cap < t->header_len + content_length + 2) next_cap *= 2;
        unsigned char* grown = (unsigned char*)realloc(t->header_buf, next_cap);
        if (!grown) { t->error_kind = WYSNA_HTTP_LIMIT; return 0; }
        t->header_buf = grown;
        t->header_cap = next_cap;
    }
    memcpy(t->header_buf + t->header_len, buffer, content_length);
    t->header_len += content_length;
    t->header_buf[t->header_len++] = '\n';
    t->header_fields++;
    return length;
}

static size_t wysna_http_async_write_body(char* ptr, size_t size, size_t nmemb, void* userdata) {
    WysnaAsyncHttpTransfer* t = (WysnaAsyncHttpTransfer*)userdata;
    size_t length = size * nmemb;
    if (t->error_kind != 0) return 0;
    if (t->body_len + length > t->max_response_bytes) {
        t->error_kind = WYSNA_HTTP_LIMIT;
        return 0;
    }
    if (t->body_len + length > t->body_cap) {
        size_t next_cap = t->body_cap ? t->body_cap * 2 : 4096;
        while (next_cap < t->body_len + length) next_cap *= 2;
        unsigned char* grown = (unsigned char*)realloc(t->body_buf, next_cap);
        if (!grown) { t->error_kind = WYSNA_HTTP_LIMIT; return 0; }
        t->body_buf = grown;
        t->body_cap = next_cap;
    }
    memcpy(t->body_buf + t->body_len, ptr, length);
    t->body_len += length;
    return length;
}

static WysnaAsyncHttpTransfer* wysna_async_http_find(int64_t id) {
    WysnaAsyncHttpTransfer* curr = wysna_async_http_transfers;
    while (curr) {
        if (curr->id == id) return curr;
        curr = curr->next;
    }
    return NULL;
}

static WysnaAsyncHttpTransfer* wysna_async_http_find_by_curl(CURL* curl) {
    WysnaAsyncHttpTransfer* curr = wysna_async_http_transfers;
    while (curr) {
        if (curr->curl == curl) return curr;
        curr = curr->next;
    }
    return NULL;
}

static void wysna_async_http_process_messages(void) {
    if (!wysna_http_multi) return;
    CURLMsg* msg = NULL;
    int msgs_left = 0;
    while ((msg = curl_multi_info_read(wysna_http_multi, &msgs_left))) {
        if (msg->msg == CURLMSG_DONE) {
            CURL* easy = msg->easy_handle;
            WysnaAsyncHttpTransfer* t = wysna_async_http_find_by_curl(easy);
            if (t && t->done == 0) {
                CURLcode res = msg->data.result;
                if (res == CURLE_OK && t->error_kind == 0) {
                    curl_easy_getinfo(easy, CURLINFO_RESPONSE_CODE, &t->status);
                    if (t->status < 100 || t->status > 599) {
                        t->error_kind = WYSNA_HTTP_INVALID_RESPONSE;
                        t->error_code = EIO;
                        t->done = -1;
                    } else {
                        t->done = 1;
                    }
                } else {
                    t->done = -1;
                    if (t->error_kind == 0) {
                        if (res == CURLE_OPERATION_TIMEDOUT) t->error_kind = WYSNA_HTTP_TIMEOUT;
                        else if (res == CURLE_PEER_FAILED_VERIFICATION || res == CURLE_SSL_CONNECT_ERROR || res == CURLE_SSL_CERTPROBLEM) t->error_kind = WYSNA_HTTP_TLS;
                        else if (res == CURLE_URL_MALFORMAT || res == CURLE_UNSUPPORTED_PROTOCOL || res == CURLE_BAD_FUNCTION_ARGUMENT) t->error_kind = WYSNA_HTTP_INVALID_REQUEST;
                        else t->error_kind = WYSNA_HTTP_TRANSPORT;
                    }
                    t->error_code = res;
                }
            }
        }
    }
}

static void wysna_async_http_free(WysnaAsyncHttpTransfer* t) {
    if (!t) return;
    if (wysna_http_multi && t->curl) {
        curl_multi_remove_handle(wysna_http_multi, t->curl);
    }
    if (t->curl) curl_easy_cleanup(t->curl);
    if (t->headers) curl_slist_free_all(t->headers);
    if (t->url_copy) free(t->url_copy);
    if (t->method_copy) free(t->method_copy);
    if (t->header_buf) free(t->header_buf);
    if (t->body_buf) free(t->body_buf);
    free(t);
}

static int64_t wysna_host_http_async_start(void* url_handle, void* method_handle,
        void* request_headers_handle, void* request_body_handle,
        int64_t timeout_ms, int64_t max_response_bytes) {
    wysna_host_error_code = 0;
    wysna_host_error_domain = 0;
    WysnaHostEntry* url = wysna_host_find(url_handle, 1);
    WysnaHostEntry* method = wysna_host_find(method_handle, 1);
    WysnaHostEntry* request_headers = wysna_host_find(request_headers_handle, 1);
    WysnaHostEntry* request_body = wysna_host_find(request_body_handle, 1);
    WysnaHostEntry* all[] = {url, method, request_headers, request_body};
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
    if (!wysna_http_multi) {
        wysna_http_multi = curl_multi_init();
        if (!wysna_http_multi) {
            curl_slist_free_all(headers); free(url_copy); free(method_copy);
            return wysna_host_http_fail(WYSNA_HTTP_UNAVAILABLE, CURLE_OUT_OF_MEMORY);
        }
    }
    CURL* curl = curl_easy_init();
    if (!curl) {
        curl_slist_free_all(headers); free(url_copy); free(method_copy);
        return wysna_host_http_fail(WYSNA_HTTP_UNAVAILABLE, CURLE_OUT_OF_MEMORY);
    }

    WysnaAsyncHttpTransfer* transfer = (WysnaAsyncHttpTransfer*)wysna_alloc_safe(sizeof(WysnaAsyncHttpTransfer));
    memset(transfer, 0, sizeof(*transfer));
    transfer->id = wysna_async_http_next_id++;
    transfer->curl = curl;
    transfer->headers = headers;
    transfer->url_copy = url_copy;
    transfer->method_copy = method_copy;
    transfer->max_response_bytes = (size_t)max_response_bytes;
    transfer->done = 0;

    curl_easy_setopt(curl, CURLOPT_URL, url_copy);
    curl_easy_setopt(curl, CURLOPT_CUSTOMREQUEST, method_copy);
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, wysna_http_async_write_body);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, transfer);
    curl_easy_setopt(curl, CURLOPT_HEADERFUNCTION, wysna_http_async_write_headers);
    curl_easy_setopt(curl, CURLOPT_HEADERDATA, transfer);
    curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 0L);
    curl_easy_setopt(curl, CURLOPT_MAXREDIRS, 0L);
    curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 1L);
    curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 2L);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT_MS, (long)timeout_ms);
    curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT_MS, (long)(timeout_ms < 5000 ? timeout_ms : 5000));
    curl_easy_setopt(curl, CURLOPT_NOSIGNAL, 1L);
    curl_easy_setopt(curl, CURLOPT_NETRC, (long)CURL_NETRC_IGNORED);
    curl_easy_setopt(curl, CURLOPT_HTTP_VERSION, (long)CURL_HTTP_VERSION_1_1);
#if LIBCURL_VERSION_NUM >= 0x075500
    curl_easy_setopt(curl, CURLOPT_PROTOCOLS_STR, "http,https");
#else
    curl_easy_setopt(curl, CURLOPT_PROTOCOLS, (long)(CURLPROTO_HTTP | CURLPROTO_HTTPS));
#endif
    if (strcmp(method_copy, "HEAD") == 0) curl_easy_setopt(curl, CURLOPT_NOBODY, 1L);
    if (request_body->length || strcmp(method_copy, "POST") == 0
            || strcmp(method_copy, "PUT") == 0 || strcmp(method_copy, "PATCH") == 0) {
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS, request_body->length ? request_body->data : "");
        curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE_LARGE, (curl_off_t)request_body->length);
    }

    curl_multi_add_handle(wysna_http_multi, curl);

    transfer->next = wysna_async_http_transfers;
    wysna_async_http_transfers = transfer;

    int still_running = 0;
    curl_multi_perform(wysna_http_multi, &still_running);
    wysna_async_http_process_messages();

    return transfer->id;
}

static int64_t wysna_host_http_async_poll(int64_t transfer_id) {
    if (transfer_id >= WYSNA_SPAWN_HANDLE_BASE) {
        return wysna_spawn_poll(transfer_id);
    }
    if (transfer_id <= -1000 || transfer_id >= WYSNA_TIMER_HANDLE_BASE) {
        return wysna_timer_poll_or_create(transfer_id);
    }
    WysnaAsyncHttpTransfer* t = wysna_async_http_find(transfer_id);
    if (!t) return -1;
    if (t->done == 0 && wysna_http_multi) {
        int still_running = 0;
        curl_multi_perform(wysna_http_multi, &still_running);
        wysna_async_http_process_messages();
    }
    return (int64_t)t->done;
}

static int64_t wysna_host_http_multi_poll(int64_t timeout_ms) {
    int64_t timer_pending = 0;
    int64_t min_timer_ms = wysna_timer_next_wait_ms(&timer_pending);
    int64_t spawn_pending = wysna_spawn_pending_count();
    if (!wysna_http_multi) {
        return wysna_timer_multi_poll(timeout_ms, true);
    }
    int still_running = 0;
    curl_multi_perform(wysna_http_multi, &still_running);
    wysna_async_http_process_messages();
    if (still_running > 0) {
        int poll_ms = (int)timeout_ms;
        if (timer_pending > 0 && min_timer_ms < poll_ms) {
            poll_ms = min_timer_ms > 0 ? (int)min_timer_ms : 1;
        }
        if (spawn_pending > 0 && poll_ms > 1) {
            poll_ms = 1;
        }
        int numfds = 0;
        curl_multi_poll(wysna_http_multi, NULL, 0, poll_ms, &numfds);
        curl_multi_perform(wysna_http_multi, &still_running);
        wysna_async_http_process_messages();
        wysna_timer_next_wait_ms(&timer_pending);
    } else if (timer_pending > 0 || spawn_pending > 0) {
        return (int64_t)still_running + wysna_timer_multi_poll(timeout_ms, true);
    }
    return (int64_t)still_running + timer_pending + wysna_spawn_pending_count();
}

static int64_t wysna_host_http_async_finish(int64_t transfer_id, void* response_headers_handle, void* response_body_handle) {
    WysnaAsyncHttpTransfer** prev = &wysna_async_http_transfers;
    WysnaAsyncHttpTransfer* t = *prev;
    while (t && t->id != transfer_id) {
        prev = &t->next;
        t = t->next;
    }
    if (!t) return wysna_host_http_fail(WYSNA_HTTP_INVALID_REQUEST, EINVAL);
    *prev = t->next;

    if (t->done == 0 && wysna_http_multi) {
        int still_running = 0;
        curl_multi_perform(wysna_http_multi, &still_running);
        wysna_async_http_process_messages();
    }

    WysnaHostEntry* resp_headers = wysna_host_find(response_headers_handle, 1);
    WysnaHostEntry* resp_body = wysna_host_find(response_body_handle, 1);
    if (!resp_headers || !resp_body) {
        wysna_async_http_free(t);
        return wysna_host_http_fail(WYSNA_HTTP_INVALID_REQUEST, EBADF);
    }

    resp_headers->length = 0;
    resp_body->length = 0;

    if (t->done != 1 || t->error_kind != 0) {
        int kind = t->error_kind ? t->error_kind : WYSNA_HTTP_TRANSPORT;
        int code = t->error_code;
        wysna_async_http_free(t);
        return wysna_host_http_fail(kind, code);
    }

    if (t->header_len > 0) {
        wysna_host_buffer_append_bytes(resp_headers, t->header_buf, t->header_len);
    }
    if (t->body_len > 0) {
        wysna_host_buffer_append_bytes(resp_body, t->body_buf, t->body_len);
    }
    long status = t->status;
    wysna_async_http_free(t);
    return (int64_t)status;
}

static int64_t wysna_host_http_async_cancel(int64_t transfer_id) {
    WysnaAsyncHttpTransfer** prev = &wysna_async_http_transfers;
    WysnaAsyncHttpTransfer* t = *prev;
    while (t && t->id != transfer_id) {
        prev = &t->next;
        t = t->next;
    }
    if (!t) return 0;
    *prev = t->next;
    wysna_async_http_free(t);
    return 1;
}
#else
static int64_t wysna_host_http_async_start(void* u, void* m, void* h, void* b, int64_t t, int64_t max) {
    (void)u; (void)m; (void)h; (void)b; (void)t; (void)max;
    wysna_host_error_code = ENOSYS; wysna_host_error_domain = 1;
    return -2;
}
static int64_t wysna_host_http_async_poll(int64_t id) {
    if (id >= WYSNA_SPAWN_HANDLE_BASE) {
        return wysna_spawn_poll(id);
    }
    if (id <= -1000 || id >= WYSNA_TIMER_HANDLE_BASE) {
        return wysna_timer_poll_or_create(id);
    }
    return -1;
}
static int64_t wysna_host_http_multi_poll(int64_t t) {
    return wysna_timer_multi_poll(t, true);
}
static int64_t wysna_host_http_async_finish(int64_t id, void* h, void* b) {
    (void)id; (void)h; (void)b;
    wysna_host_error_code = ENOSYS; wysna_host_error_domain = 1;
    return -2;
}
static int64_t wysna_host_http_async_cancel(int64_t id) { (void)id; return 0; }
#endif /* WYSNA_HAS_HTTP && !__wasi__ */

static inline WVal wysna_await(WVal val) {
    if (val.tag == W_STRUCT || val.tag == W_OBJECT) {
        bool is_task = false;
        for (int32_t i = 0; i < val.as.st->len; i++) {
            if (val.as.st->keys[i] && strcmp(val.as.st->keys[i], "is_ready") == 0) {
                is_task = true;
                break;
            }
        }
        if (is_task) {
            int64_t spawn_handle = 0;
            {
                WVal h_init = wysna_member_get(val, "handle");
                if (h_init.tag == W_INT) spawn_handle = h_init.as.i;
            }
            while (true) {
                WVal r = wysna_member_get(val, "is_ready");
                WVal f = wysna_member_get(val, "is_failed");
                bool ready = (r.tag == W_BOOL && r.as.b);
                bool failed = (f.tag == W_BOOL && f.as.b);
                wysna_release(r);
                wysna_release(f);
                if (ready) break;
                if (failed) {
                    WVal err = wysna_member_get(val, "error");
                    char* msg_copy = NULL;
                    if (err.tag == W_ENUM && strcmp(err.as.en.variant, "Some") == 0 && err.as.en.box) {
                        WVal inner_err = err.as.en.box->val;
                        if (inner_err.tag == W_STR && inner_err.as.string.data) {
                            size_t len = inner_err.as.string.length;
                            msg_copy = (char*)malloc(len + 1);
                            if (msg_copy) {
                                if (len) memcpy(msg_copy, inner_err.as.string.data, len);
                                msg_copy[len] = '\0';
                            }
                        }
                    }
                    wysna_release(val);
                    if (msg_copy) {
                        wysna_runtime_fail("W4003", "%s", msg_copy);
                    } else {
                        wysna_runtime_fail("W4003", "%s", "Async task failed");
                    }
                    return wysna_unit();
                }
                WVal h = wysna_member_get(val, "handle");
                int64_t handle_val = (h.tag == W_INT) ? h.as.i : 0;
                wysna_release(h);
                if (handle_val > 0) {
                    spawn_handle = handle_val;
                    wysna_host_http_multi_poll(10);
                    int64_t poll_st = wysna_host_http_async_poll(handle_val);
                    if (poll_st == 1) {
                        break;
                    }
                    if (poll_st == 2) {
                        wysna_release(val);
                        wysna_runtime_fail("W4003", "%s", "Async HTTP transfer failed");
                        return wysna_unit();
                    }
                    if (poll_st < 0) {
                        wysna_release(val);
                        wysna_runtime_fail("W4003", "%s", "Invalid async transfer handle");
                        return wysna_unit();
                    }
                } else {
                    wysna_release(val);
                    wysna_runtime_fail("W4003", "%s", "Deadlock: awaiting pending Task with no active handle");
                    return wysna_unit();
                }
            }
            if (spawn_handle >= WYSNA_SPAWN_HANDLE_BASE) {
                WVal res = wysna_spawn_clone_result(spawn_handle);
                wysna_release(val);
                return res;
            }
            WVal v = wysna_member_get(val, "value");
            WVal res;
            if (v.tag == W_ENUM && strcmp(v.as.en.variant, "Some") == 0 && v.as.en.box) {
                res = wysna_clone(v.as.en.box->val);
            } else {
                res = wysna_clone(v);
            }
            wysna_release(val);
            return res;
        }
    }
    return val;
}

typedef struct WysnaParallelWork {
    WVal* inputs;
    WVal* outputs;
    WVal fn;
    int32_t start;
    int32_t end;
} WysnaParallelWork;

static int32_t wysna_parallel_active_depth = 0;

static inline void wysna_parallel_run_slice(WysnaParallelWork* work) {
    for (int32_t i = work->start; i < work->end; i++) {
        work->outputs[i] = wysna_call(work->fn, 1, &work->inputs[i]);
        wysna_release(work->inputs[i]);
        work->inputs[i] = wysna_unit();
    }
    wysna_release(work->fn);
    work->fn = wysna_unit();
}

#if !defined(_WIN32) && !defined(__wasi__)
static void* wysna_parallel_posix_entry(void* raw) {
    wysna_parallel_run_slice((WysnaParallelWork*)raw);
    return NULL;
}
#elif defined(_WIN32)
static DWORD WINAPI wysna_parallel_win32_entry(LPVOID raw) {
    wysna_parallel_run_slice((WysnaParallelWork*)raw);
    return 0;
}
#endif

static inline WVal wysna_parallel_map(WVal values, WVal transform) {
    wysna_step_tick();
    if (values.tag != W_LIST || !values.as.list) {
        wysna_runtime_fail("W4003", "parallel_map() expects list");
    }
    if (transform.tag != W_FUNCTION || !transform.as.function) {
        wysna_runtime_fail("W4003", "parallel_map() expects function");
    }
    int32_t len = values.as.list->len;
    if (len <= 0) {
        return wysna_make_list(0, NULL);
    }

    int32_t max_workers = 4;
    const char* threads_env = getenv("WYSNA_PARALLEL_THREADS");
    if (threads_env && threads_env[0] != '\0') {
        long parsed = strtol(threads_env, NULL, 10);
        if (parsed <= 1) max_workers = 1;
        else if (parsed < (long)max_workers) max_workers = (int32_t)parsed;
    }
#if defined(__wasi__)
    max_workers = 1;
#elif !defined(_WIN32)
#if (defined(__GNUC__) || defined(__clang__)) && !defined(__APPLE__)
    typedef int (*WysnaPthreadCreateFn)(pthread_t*, const pthread_attr_t*, void*(*)(void*), void*);
    typedef int (*WysnaPthreadJoinFn)(pthread_t, void**);
    volatile WysnaPthreadCreateFn p_create = pthread_create;
    volatile WysnaPthreadJoinFn p_join = pthread_join;
    if (!p_create || !p_join) max_workers = 1;
#endif
#endif
    if (wysna_atomic_load_i32(&wysna_parallel_active_depth) > 0) {
        max_workers = 1;
    }

    int32_t workers = len < max_workers ? len : max_workers;
    if (workers <= 1 || len <= 1) {
        WVal* outputs = (WVal*)wysna_alloc_safe(sizeof(WVal) * (size_t)len);
        for (int32_t i = 0; i < len; i++) {
            WVal item = values.as.list->data[i];
            outputs[i] = wysna_call(transform, 1, &item);
        }
        WVal result = wysna_make_list(len, outputs);
        for (int32_t i = 0; i < len; i++) wysna_release(outputs[i]);
        free(outputs);
        return result;
    }

    wysna_atomic_inc_i32(&wysna_parallel_active_depth);
    WVal* inputs = (WVal*)wysna_alloc_safe(sizeof(WVal) * (size_t)len);
    WVal* outputs = (WVal*)wysna_alloc_safe(sizeof(WVal) * (size_t)len);
    for (int32_t i = 0; i < len; i++) {
        inputs[i] = wysna_clone(values.as.list->data[i]);
        outputs[i] = wysna_unit();
    }

    WysnaParallelWork tasks[4];
    int32_t base = len / workers;
    int32_t rem = len % workers;
    int32_t cursor = 0;
    for (int32_t w = 0; w < workers; w++) {
        int32_t span = base + (w < rem ? 1 : 0);
        tasks[w].inputs = inputs;
        tasks[w].outputs = outputs;
        tasks[w].fn = wysna_clone(transform);
        tasks[w].start = cursor;
        tasks[w].end = cursor + span;
        cursor += span;
    }

#if !defined(_WIN32) && !defined(__wasi__)
    pthread_t tids[4];
    bool launched[4] = {false, false, false, false};
    for (int32_t w = 0; w < workers - 1; w++) {
        if (pthread_create(&tids[w], NULL, wysna_parallel_posix_entry, &tasks[w]) == 0) {
            launched[w] = true;
        } else {
            wysna_parallel_run_slice(&tasks[w]);
        }
    }
    wysna_parallel_run_slice(&tasks[workers - 1]);
    for (int32_t w = 0; w < workers - 1; w++) {
        if (launched[w]) pthread_join(tids[w], NULL);
    }
#elif defined(_WIN32)
    HANDLE handles[4];
    bool launched[4] = {false, false, false, false};
    for (int32_t w = 0; w < workers - 1; w++) {
        handles[w] = CreateThread(NULL, 0, wysna_parallel_win32_entry, &tasks[w], 0, NULL);
        if (handles[w] != NULL) {
            launched[w] = true;
        } else {
            wysna_parallel_run_slice(&tasks[w]);
        }
    }
    wysna_parallel_run_slice(&tasks[workers - 1]);
    for (int32_t w = 0; w < workers - 1; w++) {
        if (launched[w]) {
            WaitForSingleObject(handles[w], INFINITE);
            CloseHandle(handles[w]);
        }
    }
#endif

    wysna_atomic_dec_i32(&wysna_parallel_active_depth);
    WVal result = wysna_make_list(len, outputs);
    for (int32_t i = 0; i < len; i++) wysna_release(outputs[i]);
    free(inputs);
    free(outputs);
    return result;
}

static inline WVal wysna_parallel_batch(WVal values, int64_t batch_size, WVal transform) {
    wysna_step_tick();
    if (values.tag != W_LIST || !values.as.list) {
        wysna_runtime_fail("W4003", "parallel_batch() expects list");
    }
    int32_t len = values.as.list->len;
    if (len <= 0) return wysna_make_list(0, NULL);
    int64_t step = batch_size > 0 ? batch_size : 1;
    if (step >= (int64_t)len) {
        return wysna_parallel_map(values, transform);
    }
    WVal* outputs = (WVal*)wysna_alloc_safe(sizeof(WVal) * (size_t)len);
    int32_t offset = 0;
    while (offset < len) {
        int32_t chunk = (step < (int64_t)(len - offset)) ? (int32_t)step : (len - offset);
        WVal slice_list = wysna_make_list(chunk, values.as.list->data + offset);
        WVal mapped = wysna_parallel_map(slice_list, transform);
        for (int32_t i = 0; i < chunk; i++) {
            outputs[offset + i] = wysna_clone(mapped.as.list->data[i]);
        }
        wysna_release(mapped);
        wysna_release(slice_list);
        offset += chunk;
    }
    WVal result = wysna_make_list(len, outputs);
    for (int32_t i = 0; i < len; i++) wysna_release(outputs[i]);
    free(outputs);
    return result;
}

static inline WVal wysna_parallel_filter(WVal values, WVal predicate) {
    wysna_step_tick();
    if (values.tag != W_LIST || !values.as.list) {
        wysna_runtime_fail("W4003", "parallel_filter() expects list");
    }
    int32_t len = values.as.list->len;
    if (len <= 0) return wysna_make_list(0, NULL);
    WVal flags = wysna_parallel_map(values, predicate);
    WVal* kept = (WVal*)wysna_alloc_safe(sizeof(WVal) * (size_t)len);
    int32_t kept_count = 0;
    for (int32_t i = 0; i < len; i++) {
        WVal flag = flags.as.list->data[i];
        if (flag.tag == W_BOOL && flag.as.b) {
            kept[kept_count++] = wysna_clone(values.as.list->data[i]);
        }
    }
    WVal result = wysna_make_list(kept_count, kept);
    for (int32_t i = 0; i < kept_count; i++) wysna_release(kept[i]);
    free(kept);
    wysna_release(flags);
    return result;
}

#endif
