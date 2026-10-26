/* Launch the private Python runtime without a shell or changes to the user's Python. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>
#ifndef WYSNA_ENTRYPOINT
#define WYSNA_ENTRYPOINT L"main.py"
#endif

static int report_error(const wchar_t *operation) {
    DWORD error = GetLastError();
    fwprintf(stderr, L"Wysna: %ls failed (Windows error %lu). Reinstall Wysna if files are missing.\n",
             operation, (unsigned long)error);
    return 1;
}

static BOOL WINAPI console_handler(DWORD event) {
    /* Python receives console signals directly; keep the parent waiting for cleanup. */
    return event == CTRL_C_EVENT || event == CTRL_BREAK_EVENT;
}

int wmain(void) {
    wchar_t root[32768];
    DWORD length = GetModuleFileNameW(NULL, root, 32768);
    if (!length || length >= 32768) return report_error(L"Locating the installation");
    wchar_t *separator = wcsrchr(root, L'\\');
    if (!separator) return 1;
    *separator = L'\0';

    wchar_t *tail = GetCommandLineW();
    int quoted = 0;
    while (*tail) {
        if (*tail == L'"') quoted = !quoted;
        else if (!quoted && (*tail == L' ' || *tail == L'\t')) break;
        ++tail;
    }
    while (*tail == L' ' || *tail == L'\t') ++tail;
    size_t capacity = 2 * wcslen(root) + wcslen(tail) + 128;
    if (capacity >= 32767) {
        fwprintf(stderr, L"Wysna: command line exceeds the Windows length limit.\n");
        return 1;
    }
    wchar_t *python = calloc(wcslen(root) + 32, sizeof(wchar_t));
    wchar_t *command = calloc(capacity, sizeof(wchar_t));
    if (!python || !command) {
        free(python);
        free(command);
        return 1;
    }
    swprintf(python, wcslen(root) + 32, L"%ls\\python\\python.exe", root);
    swprintf(command, capacity, L"\"%ls\" -I -B -X utf8 \"%ls\\%ls\" %ls",
             python, root, WYSNA_ENTRYPOINT, tail);
    STARTUPINFOW startup = {0};
    PROCESS_INFORMATION process = {0};
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
    startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    BOOL launched = CreateProcessW(python, command, NULL, NULL, TRUE, 0,
                                   NULL, NULL, &startup, &process);
    free(python);
    free(command);
    if (!launched) return report_error(L"Starting the private runtime");
    SetConsoleCtrlHandler(console_handler, TRUE);
    DWORD wait_status = WaitForSingleObject(process.hProcess, INFINITE);
    DWORD exit_code = 1;
    BOOL completed = wait_status == WAIT_OBJECT_0 && GetExitCodeProcess(process.hProcess, &exit_code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return completed ? (int)exit_code : report_error(L"Waiting for the compiler");
}
