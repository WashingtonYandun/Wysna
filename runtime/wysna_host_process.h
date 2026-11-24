/* Internal process domain shared by the runtime and native SDK driver. */
#ifndef WYSNA_HOST_PROCESS_H
#define WYSNA_HOST_PROCESS_H
#ifndef W_HOST_H
#error "Include wysna_runtime.h before internal process helpers"
#endif
#if !defined(__wasi__)
#include <signal.h>
#include "wysna_process_tree.h"
typedef struct {
#ifdef _WIN32
    volatile LONG requested;
#else
    volatile sig_atomic_t requested;
#endif
} WysnaProcessCancellation;

WYSNA_RT_FN void wysna_host_process_request_cancel(WysnaProcessCancellation* cancellation, int signal) {
#ifdef _WIN32
    InterlockedExchange(&cancellation->requested, signal);
#else
    cancellation->requested = signal;
#endif
}
WYSNA_RT_FN int wysna_host_process_cancelled(WysnaProcessCancellation* cancellation) {
    if (!cancellation) return 0;
#ifdef _WIN32
    return (int)InterlockedCompareExchange(&cancellation->requested, 0, 0);
#else
    return cancellation->requested;
#endif
}
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

WYSNA_RT_FN int64_t wysna_host_process_run_cancellable(void* executable_handle, void* arguments_handle, int64_t timeout_ms, WysnaProcessCancellation* cancellation) {
#ifdef WYSNA_DENY_CAP_PROCESSES
    (void)executable_handle; (void)arguments_handle; (void)timeout_ms; (void)cancellation;
    wysna_sandbox_deny("processes");
    return -1;
#endif
    wysna_host_error_code = 0; wysna_host_error_domain = 0;
    if (wysna_host_process_cancelled(cancellation)) return wysna_host_process_error(EINTR);
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
    if (cancellation && !job) {
        free(application); free(command_line);
        wysna_host_error_code = ERROR_NOT_SUPPORTED; wysna_host_error_domain = 2; return -1;
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
        if (cancellation) {
            TerminateProcess(process.hProcess, ERROR_NOT_SUPPORTED);
            WaitForSingleObject(process.hProcess, INFINITE);
            CloseHandle(process.hThread); CloseHandle(process.hProcess);
            wysna_host_error_code = ERROR_NOT_SUPPORTED; wysna_host_error_domain = 2; return -1;
        }
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
    ULONGLONG wait_begin = GetTickCount64();
    DWORD waited = WAIT_TIMEOUT;
    DWORD termination_error = ERROR_TIMEOUT;
    for (;;) {
        if (wysna_host_process_cancelled(cancellation)) { termination_error = ERROR_OPERATION_ABORTED; break; }
        ULONGLONG elapsed = GetTickCount64() - wait_begin;
        if (elapsed >= (ULONGLONG)timeout_ms) break;
        DWORD remaining = (DWORD)((ULONGLONG)timeout_ms - elapsed);
        waited = WaitForSingleObject(process.hProcess, remaining < 50 ? remaining : 50);
        if (waited != WAIT_TIMEOUT) break;
    }
    if (waited == WAIT_TIMEOUT) {
        if (job) TerminateJobObject(job, termination_error);
        else TerminateProcess(process.hProcess, termination_error);
        WaitForSingleObject(process.hProcess, INFINITE);
        CloseHandle(process.hThread); CloseHandle(process.hProcess);
        if (job) CloseHandle(job);
        wysna_host_error_code = termination_error; wysna_host_error_domain = 2; return -1;
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

WYSNA_RT_FN int64_t wysna_host_process_capture_cancellable(void* executable_handle, void* arguments_handle,
                                          int64_t timeout_ms, int64_t max_output_bytes,
                                          void* stdout_handle, void* stderr_handle, WysnaProcessCancellation* cancellation) {
#ifdef WYSNA_DENY_CAP_PROCESSES
    (void)executable_handle; (void)arguments_handle; (void)timeout_ms;
    (void)max_output_bytes; (void)stdout_handle; (void)stderr_handle; (void)cancellation;
    wysna_sandbox_deny("processes");
    return -1;
#endif
    wysna_host_error_code = 0; wysna_host_error_domain = 0;
    if (timeout_ms < 1 || timeout_ms > 600000 || max_output_bytes < 1 || max_output_bytes > 16777216) {
        wysna_host_error_code = ERROR_INVALID_PARAMETER; wysna_host_error_domain = 2; return -1;
    }
    WysnaHostEntry* stdout_entry = wysna_host_find(stdout_handle, 1);
    WysnaHostEntry* stderr_entry = wysna_host_find(stderr_handle, 1);
    if (!stdout_entry || !stderr_entry || stdout_entry->length || stderr_entry->length) {
        wysna_host_error_code = ERROR_INVALID_PARAMETER; wysna_host_error_domain = 2; return -1;
    }
    char* executable = NULL; char** argv = NULL; int argc = 0;
    if (!wysna_host_process_decode(executable_handle, arguments_handle, &executable, &argv, &argc)) {
        wysna_host_error_domain = 2; return -1;
    }
    wchar_t** wide_argv = (wchar_t**)wysna_alloc_safe(sizeof(wchar_t*) * (size_t)(argc + 1));
    size_t command_length = 1;
    for (int index = 0; index < argc; index++) {
        wchar_t* wide = wysna_host_process_wide(argv[index]);
        if (!wide) {
            for (int previous = 0; previous < index; previous++) free(wide_argv[previous]);
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
    if (!application) {
        DWORD error = GetLastError();
        free(command_line); wysna_host_error_code = error; wysna_host_error_domain = 2; return -1;
    }
    SECURITY_ATTRIBUTES sa;
    memset(&sa, 0, sizeof(sa));
    sa.nLength = sizeof(sa);
    sa.bInheritHandle = TRUE;
    HANDLE out_read = NULL, out_write = NULL, err_read = NULL, err_write = NULL;
    if (!CreatePipe(&out_read, &out_write, &sa, 0) || !CreatePipe(&err_read, &err_write, &sa, 0)) {
        DWORD error = GetLastError();
        if (out_read) CloseHandle(out_read);
        if (out_write) CloseHandle(out_write);
        if (err_read) CloseHandle(err_read);
        if (err_write) CloseHandle(err_write);
        free(application); free(command_line);
        wysna_host_error_code = error; wysna_host_error_domain = 2; return -1;
    }
    SetHandleInformation(out_read, HANDLE_FLAG_INHERIT, 0);
    SetHandleInformation(err_read, HANDLE_FLAG_INHERIT, 0);
    HANDLE job = CreateJobObjectW(NULL, NULL);
    if (job) {
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits;
        memset(&limits, 0, sizeof(limits));
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if (!SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, sizeof(limits))) {
            CloseHandle(job); job = NULL;
        }
    }
    STARTUPINFOW startup; PROCESS_INFORMATION process;
    memset(&startup, 0, sizeof(startup)); memset(&process, 0, sizeof(process));
    startup.cb = sizeof(startup); startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = out_write;
    startup.hStdError = err_write;
    DWORD creation_flags = CREATE_NEW_PROCESS_GROUP | (job ? CREATE_SUSPENDED : 0);
    BOOL started = CreateProcessW(application, command_line, NULL, NULL, TRUE, creation_flags,
                                  NULL, NULL, &startup, &process);
    DWORD error = started ? 0 : GetLastError();
    CloseHandle(out_write); CloseHandle(err_write);
    free(application); free(command_line);
    if (!started) {
        CloseHandle(out_read); CloseHandle(err_read);
        if (job) CloseHandle(job);
        wysna_host_error_code = error; wysna_host_error_domain = 2; return -1;
    }
    if (job) {
        if (!AssignProcessToJobObject(job, process.hProcess)) { CloseHandle(job); job = NULL; }
        if (ResumeThread(process.hThread) == (DWORD)-1) {
            error = GetLastError();
            if (job) TerminateJobObject(job, error); else TerminateProcess(process.hProcess, error);
            WaitForSingleObject(process.hProcess, INFINITE);
            CloseHandle(out_read); CloseHandle(err_read);
            CloseHandle(process.hThread); CloseHandle(process.hProcess);
            if (job) CloseHandle(job);
            wysna_host_error_code = error; wysna_host_error_domain = 2; return -1;
        }
    }
    ULONGLONG wait_begin = GetTickCount64();
    int overflow = 0, timed_out = 0, cancelled = 0;
    for (;;) {
        if (wysna_host_process_cancelled(cancellation)) { cancelled = 1; break; }
        char buf[4096];
        int progressed = 0;
        for (int s = 0; s < 2; s++) {
            HANDLE h = s == 0 ? out_read : err_read;
            WysnaHostEntry* dst = s == 0 ? stdout_entry : stderr_entry;
            DWORD avail = 0;
            while (PeekNamedPipe(h, NULL, 0, NULL, &avail, NULL) && avail > 0) {
                DWORD want = avail > sizeof(buf) ? (DWORD)sizeof(buf) : avail;
                DWORD got = 0;
                if (!ReadFile(h, buf, want, &got, NULL) || got == 0) break;
                progressed = 1;
                if ((int64_t)dst->length + (int64_t)got > max_output_bytes) {
                    size_t remaining = (size_t)max_output_bytes - dst->length;
                    if (remaining) (void)wysna_host_buffer_append_bytes(dst, buf, remaining);
                    overflow = 1; break;
                }
                if (wysna_host_buffer_append_bytes(dst, buf, (size_t)got) != (size_t)got) {
                    overflow = 1; break;
                }
            }
            if (overflow) break;
        }
        if (overflow) break;
        DWORD waited = WaitForSingleObject(process.hProcess, 0);
        if (waited == WAIT_OBJECT_0) {
            DWORD out_avail = 0, err_avail = 0;
            PeekNamedPipe(out_read, NULL, 0, NULL, &out_avail, NULL);
            PeekNamedPipe(err_read, NULL, 0, NULL, &err_avail, NULL);
            if (out_avail == 0 && err_avail == 0) break;
        }
        if (GetTickCount64() - wait_begin >= (ULONGLONG)timeout_ms) { timed_out = 1; break; }
        if (!progressed) Sleep(10);
    }
    if (overflow || timed_out || cancelled) {
        DWORD err_code = cancelled ? ERROR_OPERATION_ABORTED : overflow ? ERROR_BUFFER_OVERFLOW : ERROR_TIMEOUT;
        if (job) TerminateJobObject(job, err_code); else TerminateProcess(process.hProcess, err_code);
        WaitForSingleObject(process.hProcess, INFINITE);
        CloseHandle(out_read); CloseHandle(err_read);
        CloseHandle(process.hThread); CloseHandle(process.hProcess);
        if (job) CloseHandle(job);
        wysna_host_error_code = err_code; wysna_host_error_domain = 2; return -1;
    }
    DWORD exit_code = 0;
    BOOL got_code = GetExitCodeProcess(process.hProcess, &exit_code);
    error = got_code ? 0 : GetLastError();
    CloseHandle(out_read); CloseHandle(err_read);
    CloseHandle(process.hThread); CloseHandle(process.hProcess);
    if (job) CloseHandle(job);
    if (!got_code) { wysna_host_error_code = error; wysna_host_error_domain = 2; return -1; }
    return (int64_t)exit_code;
}
#else
WYSNA_RT_FN int64_t wysna_host_process_run_cancellable(void* executable_handle, void* arguments_handle, int64_t timeout_ms, WysnaProcessCancellation* cancellation) {
#ifdef WYSNA_DENY_CAP_PROCESSES
    (void)executable_handle; (void)arguments_handle; (void)timeout_ms; (void)cancellation;
    wysna_sandbox_deny("processes");
    return -1;
#endif
    wysna_host_error_code = 0; wysna_host_error_domain = 0;
    if (wysna_host_process_cancelled(cancellation)) return wysna_host_process_error(EINTR);
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
        int error = errno; wysna_process_terminate_tree(child);
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
            int error = errno; wysna_process_terminate_tree(child);
            while (waitpid(child, &status, 0) < 0 && errno == EINTR) { }
            for (int index = 1; index < argc; index++) free(argv[index]);
            free(argv); free(executable); return wysna_host_process_error(error);
        }
        int64_t elapsed_ns = ((int64_t)current.tv_sec - started.tv_sec) * 1000000000LL
                           + ((int64_t)current.tv_nsec - started.tv_nsec);
        int64_t elapsed_ms = elapsed_ns / 1000000LL;
        int cancelled = wysna_host_process_cancelled(cancellation);
        if (cancelled || elapsed_ms >= timeout_ms) {
            wysna_process_terminate_tree(child);
            while (waitpid(child, &status, 0) < 0 && errno == EINTR) { }
            for (int index = 1; index < argc; index++) free(argv[index]);
            free(argv); free(executable); return wysna_host_process_error(cancelled ? EINTR : ETIMEDOUT);
        }
        struct timeval delay = {0, 10000}; (void)select(0, NULL, NULL, NULL, &delay);
    }
    for (int index = 1; index < argc; index++) free(argv[index]);
    free(argv); free(executable);
    if (WIFEXITED(status)) return (int64_t)WEXITSTATUS(status);
    if (WIFSIGNALED(status)) return 128 + WTERMSIG(status);
    return wysna_host_process_error(EIO);
}

WYSNA_RT_FN int64_t wysna_host_process_capture_cancellable(void* executable_handle, void* arguments_handle,
                                          int64_t timeout_ms, int64_t max_output_bytes,
                                          void* stdout_handle, void* stderr_handle, WysnaProcessCancellation* cancellation) {
#ifdef WYSNA_DENY_CAP_PROCESSES
    (void)executable_handle; (void)arguments_handle; (void)timeout_ms;
    (void)max_output_bytes; (void)stdout_handle; (void)stderr_handle; (void)cancellation;
    wysna_sandbox_deny("processes");
    return -1;
#endif
    wysna_host_error_code = 0; wysna_host_error_domain = 0;
    if (timeout_ms < 1 || timeout_ms > 600000 || max_output_bytes < 1 || max_output_bytes > 16777216) {
        return wysna_host_process_error(EINVAL);
    }
    WysnaHostEntry* stdout_entry = wysna_host_find(stdout_handle, 1);
    WysnaHostEntry* stderr_entry = wysna_host_find(stderr_handle, 1);
    if (!stdout_entry || !stderr_entry || stdout_entry->length || stderr_entry->length) {
        return wysna_host_process_error(EINVAL);
    }
    char* executable = NULL; char** argv = NULL; int argc = 0;
    if (!wysna_host_process_decode(executable_handle, arguments_handle, &executable, &argv, &argc)) return -1;
    argv[0] = executable;
    int error_pipe[2] = {-1, -1}, stdout_pipe[2] = {-1, -1}, stderr_pipe[2] = {-1, -1};
    if (pipe(error_pipe) != 0 || pipe(stdout_pipe) != 0 || pipe(stderr_pipe) != 0) {
        int err = errno;
        for (int i = 0; i < 2; i++) {
            if (error_pipe[i] >= 0) close(error_pipe[i]);
            if (stdout_pipe[i] >= 0) close(stdout_pipe[i]);
            if (stderr_pipe[i] >= 0) close(stderr_pipe[i]);
        }
        for (int index = 1; index < argc; index++) free(argv[index]);
        free(argv); free(executable); return wysna_host_process_error(err);
    }
    if (fcntl(error_pipe[1], F_SETFD, FD_CLOEXEC) == -1
            || fcntl(stdout_pipe[0], F_SETFD, FD_CLOEXEC) == -1
            || fcntl(stderr_pipe[0], F_SETFD, FD_CLOEXEC) == -1) {
        int err = errno;
        close(error_pipe[0]); close(error_pipe[1]);
        close(stdout_pipe[0]); close(stdout_pipe[1]);
        close(stderr_pipe[0]); close(stderr_pipe[1]);
        for (int index = 1; index < argc; index++) free(argv[index]);
        free(argv); free(executable); return wysna_host_process_error(err);
    }
    struct timespec started;
    if (clock_gettime(CLOCK_MONOTONIC, &started) != 0) {
        int err = errno;
        close(error_pipe[0]); close(error_pipe[1]);
        close(stdout_pipe[0]); close(stdout_pipe[1]);
        close(stderr_pipe[0]); close(stderr_pipe[1]);
        for (int index = 1; index < argc; index++) free(argv[index]);
        free(argv); free(executable); return wysna_host_process_error(err);
    }
    pid_t child = fork();
    if (child < 0) {
        int err = errno;
        close(error_pipe[0]); close(error_pipe[1]);
        close(stdout_pipe[0]); close(stdout_pipe[1]);
        close(stderr_pipe[0]); close(stderr_pipe[1]);
        for (int index = 1; index < argc; index++) free(argv[index]);
        free(argv); free(executable); return wysna_host_process_error(err);
    }
    if (child == 0) {
        close(error_pipe[0]); close(stdout_pipe[0]); close(stderr_pipe[0]);
        setpgid(0, 0);
        if (dup2(stdout_pipe[1], STDOUT_FILENO) < 0 || dup2(stderr_pipe[1], STDERR_FILENO) < 0) {
            int err = errno;
            (void)write(error_pipe[1], &err, sizeof(err));
            _exit(127);
        }
        close(stdout_pipe[1]); close(stderr_pipe[1]);
        execvp(executable, argv);
        int err = errno;
        (void)write(error_pipe[1], &err, sizeof(err));
        _exit(127);
    }
    close(error_pipe[1]); close(stdout_pipe[1]); close(stderr_pipe[1]);
    (void)setpgid(child, child);
    int exec_error = 0;
    ssize_t received;
    do { received = read(error_pipe[0], &exec_error, sizeof(exec_error)); } while (received < 0 && errno == EINTR);
    close(error_pipe[0]);
    if (received != 0) {
        int err = received < 0 ? errno : exec_error;
        close(stdout_pipe[0]); close(stderr_pipe[0]);
        if (received < 0) wysna_process_terminate_tree(child);
        int status; while (waitpid(child, &status, 0) < 0 && errno == EINTR) { }
        for (int index = 1; index < argc; index++) free(argv[index]);
        free(argv); free(executable); return wysna_host_process_error(err);
    }
    int out_flags = fcntl(stdout_pipe[0], F_GETFL, 0);
    int err_flags = fcntl(stderr_pipe[0], F_GETFL, 0);
    if (out_flags >= 0) (void)fcntl(stdout_pipe[0], F_SETFL, out_flags | O_NONBLOCK);
    if (err_flags >= 0) (void)fcntl(stderr_pipe[0], F_SETFL, err_flags | O_NONBLOCK);
    int out_open = 1, err_open = 1, reaped = 0, status = 0, fail_err = 0;
    for (;;) {
        if (wysna_host_process_cancelled(cancellation)) { fail_err = EINTR; break; }
        int fds[2] = { stdout_pipe[0], stderr_pipe[0] };
        int* opens[2] = { &out_open, &err_open };
        WysnaHostEntry* entries[2] = { stdout_entry, stderr_entry };
        for (int s = 0; s < 2 && !fail_err; s++) {
            if (!*opens[s]) continue;
            char buf[4096];
            for (;;) {
                ssize_t n = read(fds[s], buf, sizeof(buf));
                if (n > 0) {
                    if ((int64_t)entries[s]->length + (int64_t)n > max_output_bytes) {
                        size_t remaining = (size_t)max_output_bytes - entries[s]->length;
                        if (remaining) (void)wysna_host_buffer_append_bytes(entries[s], buf, remaining);
                        fail_err = E2BIG; break;
                    }
                    if (wysna_host_buffer_append_bytes(entries[s], buf, (size_t)n) != (size_t)n) {
                        fail_err = ENOMEM; break;
                    }
                } else if (n == 0) {
                    close(fds[s]); *opens[s] = 0; break;
                } else if (errno == EINTR) {
                    continue;
                } else if (errno == EAGAIN || errno == EWOULDBLOCK) {
                    break;
                } else {
                    fail_err = errno; break;
                }
            }
        }
        if (fail_err) break;
        if (!reaped) {
            pid_t waited = waitpid(child, &status, WNOHANG);
            if (waited == child) reaped = 1;
            else if (waited < 0 && errno != EINTR) { fail_err = errno; break; }
        }
        if (!out_open && !err_open && reaped) break;
        struct timespec current;
        if (clock_gettime(CLOCK_MONOTONIC, &current) != 0) { fail_err = errno; break; }
        int64_t elapsed_ms = ((int64_t)current.tv_sec - started.tv_sec) * 1000LL
                           + ((int64_t)current.tv_nsec - started.tv_nsec) / 1000000LL;
        if (elapsed_ms >= timeout_ms) { fail_err = ETIMEDOUT; break; }
        fd_set readfds;
        FD_ZERO(&readfds);
        int maxfd = -1;
        if (out_open) { FD_SET(stdout_pipe[0], &readfds); if (stdout_pipe[0] > maxfd) maxfd = stdout_pipe[0]; }
        if (err_open) { FD_SET(stderr_pipe[0], &readfds); if (stderr_pipe[0] > maxfd) maxfd = stderr_pipe[0]; }
        struct timeval delay = {0, 10000};
        (void)select(maxfd >= 0 ? maxfd + 1 : 0, maxfd >= 0 ? &readfds : NULL, NULL, NULL, &delay);
    }
    if (out_open) close(stdout_pipe[0]);
    if (err_open) close(stderr_pipe[0]);
    if (fail_err) {
        wysna_process_terminate_tree(child);
        if (!reaped) {
            while (waitpid(child, &status, 0) < 0 && errno == EINTR) { }
        }
        for (int index = 1; index < argc; index++) free(argv[index]);
        free(argv); free(executable);
        return wysna_host_process_error(fail_err);
    }
    for (int index = 1; index < argc; index++) free(argv[index]);
    free(argv); free(executable);
    if (WIFEXITED(status)) return (int64_t)WEXITSTATUS(status);
    if (WIFSIGNALED(status)) return 128 + WTERMSIG(status);
    return wysna_host_process_error(EIO);
}
#endif
WYSNA_RT_FN int64_t wysna_host_process_capture(void* executable, void* arguments,
                                          int64_t timeout_ms, int64_t max_output_bytes,
                                          void* stdout_handle, void* stderr_handle) {
    return wysna_host_process_capture_cancellable(executable, arguments, timeout_ms, max_output_bytes,
                                                 stdout_handle, stderr_handle, NULL);
}
WYSNA_RT_FN int64_t wysna_host_process_run(void* executable, void* arguments, int64_t timeout_ms) {
    return wysna_host_process_run_cancellable(executable, arguments, timeout_ms, NULL);
}
#endif /* !__wasi__ */


#endif /* WYSNA_HOST_PROCESS_H */
