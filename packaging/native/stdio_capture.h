/* Scoped compiler output capture; application streams use bounded host pipes. */
#ifndef WYSNA_SDK_STDIO_CAPTURE_H
#define WYSNA_SDK_STDIO_CAPTURE_H
typedef struct {
    int out, err;
#ifdef _WIN32
    HANDLE handle_out, handle_err;
#endif
} SdkStdioCapture;

static void sdk_stdio_restore(SdkStdioCapture* capture) {
    fflush(stdout); fflush(stderr);
#ifdef _WIN32
    if (capture->out >= 0) { _dup2(capture->out, 1); _close(capture->out); }
    if (capture->err >= 0) { _dup2(capture->err, 2); _close(capture->err); }
    SetStdHandle(STD_OUTPUT_HANDLE, capture->handle_out);
    SetStdHandle(STD_ERROR_HANDLE, capture->handle_err);
#else
    if (capture->out >= 0) { dup2(capture->out, 1); close(capture->out); }
    if (capture->err >= 0) { dup2(capture->err, 2); close(capture->err); }
#endif
    capture->out = capture->err = -1;
}

static int sdk_stdio_capture(SdkStdioCapture* capture, const char* output, const char* error) {
    fflush(stdout); fflush(stderr);
#ifdef _WIN32
    capture->handle_out = GetStdHandle(STD_OUTPUT_HANDLE);
    capture->handle_err = GetStdHandle(STD_ERROR_HANDLE);
    capture->out = _dup(1); capture->err = _dup(2);
    wchar_t* wide_out = wysna_host_process_wide(output);
    wchar_t* wide_err = wysna_host_process_wide(error);
    int first = wide_out ? _wopen(wide_out, _O_WRONLY | _O_CREAT | _O_TRUNC | _O_BINARY, 0600) : -1;
    int second = wide_err ? _wopen(wide_err, _O_WRONLY | _O_CREAT | _O_TRUNC | _O_BINARY, 0600) : -1;
    free(wide_out); free(wide_err);
    int ok = capture->out >= 0 && capture->err >= 0 && first >= 0 && second >= 0;
    if (ok) ok = _dup2(first, 1) == 0 && _dup2(second, 2) == 0;
    if (first >= 0) _close(first);
    if (second >= 0) _close(second);
    if (ok) {
        SetStdHandle(STD_OUTPUT_HANDLE, (HANDLE)_get_osfhandle(1));
        SetStdHandle(STD_ERROR_HANDLE, (HANDLE)_get_osfhandle(2));
    }
#else
    capture->out = dup(1); capture->err = dup(2);
    int first = open(output, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    int second = open(error, O_WRONLY | O_CREAT | O_TRUNC, 0600);
    int ok = capture->out >= 0 && capture->err >= 0 && first >= 0 && second >= 0;
    if (ok) ok = dup2(first, 1) >= 0 && dup2(second, 2) >= 0;
    if (first >= 0) close(first);
    if (second >= 0) close(second);
#endif
    if (!ok) sdk_stdio_restore(capture);
    return ok;
}
#endif
