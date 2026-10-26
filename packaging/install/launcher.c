/* Execute the private runtime in place; preserve signals, argv and working directory. */
#define _XOPEN_SOURCE 700
#ifdef __APPLE__
#include <mach-o/dyld.h>
#endif
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char **argv) {
    char executable[PATH_MAX], actual[PATH_MAX], python[PATH_MAX], entry[PATH_MAX];
#ifdef __APPLE__
    uint32_t capacity = sizeof(executable);
    if (_NSGetExecutablePath(executable, &capacity) != 0) return 1;
#else
    ssize_t length = readlink("/proc/self/exe", executable, sizeof(executable) - 1);
    if (length < 0 || (size_t)length >= sizeof(executable) - 1) return 1;
    executable[length] = 0;
#endif
    if (!realpath(executable, actual)) return 1;
    char *separator = strrchr(actual, '/');
    if (!separator) return 1;
    *separator = 0;
    separator = strrchr(actual, '/');
    if (!separator) return 1;
    *separator = 0;
    if (snprintf(python, sizeof(python), "%s/python/bin/python3", actual) >= (int)sizeof(python) ||
        snprintf(entry, sizeof(entry), "%s/wysna_entry.py", actual) >= (int)sizeof(entry)) return 1;
    char **arguments = calloc((size_t)argc + 7, sizeof(*arguments));
    if (!arguments) return 1;
    arguments[0] = python;
    arguments[1] = "-I";
    arguments[2] = "-B";
    arguments[3] = "-X";
    arguments[4] = "utf8";
    arguments[5] = entry;
    for (int index = 1; index < argc; index++) arguments[index + 5] = argv[index];
    execv(python, arguments);
    fprintf(stderr, "[W5001] Cannot start the private Wysna runtime: %s. Repair the installation.\n", strerror(errno));
    free(arguments);
    return 1;
}
