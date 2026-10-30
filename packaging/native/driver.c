/* Command orchestration for an installed SDK; source compilation stays in Wysna. */
#ifndef _WIN32
#define _XOPEN_SOURCE 700
#ifdef __APPLE__
#define _DARWIN_C_SOURCE 1
#endif
#endif
#include "runtime/wysna_runtime.h"
#include "platform.h"
#include <signal.h>

#ifndef WYSNA_NATIVE_VERSION
#error "The native package builder must declare the Wysna version"
#endif
#ifndef WYSNA_NATIVE_TARGET
#error "The native package builder must declare its pinned target"
#endif
#define SDK_ARGUMENT_LIMIT 240
#define SDK_LINK_LIMIT 16
/* Selfbuild and large programs can exceed the old two-minute engine cap on slow runners. */
#define SDK_COMPILE_TIMEOUT_MS 600000

#ifdef _WIN32
#define SDK_ENGINE "libexec/wysna-compiler.exe"
#define SDK_ZIG "toolchain/zig/zig.exe"
#define SDK_PROGRAM "program.exe"
#else
#define SDK_ENGINE "libexec/wysna-compiler"
#define SDK_ZIG "toolchain/zig/zig"
#define SDK_PROGRAM "program"
#endif

typedef struct {
    const char *command, *source, *output, *profile;
    int arguments, include_count, link_count, library_count, library_dir_count, profile_set, timeout_set;
    const char *includes[SDK_LINK_LIMIT], *links[SDK_LINK_LIMIT], *libraries[SDK_LINK_LIMIT], *library_dirs[SDK_LINK_LIMIT];
    int64_t timeout;
} SdkOptions;
typedef struct {
    char root[SDK_PATH_CAP], engine[SDK_PATH_CAP], zig[SDK_PATH_CAP], stdlib[SDK_PATH_CAP];
    char source[SDK_PATH_CAP], output[SDK_PATH_CAP], parent[SDK_PATH_CAP];
    char workspace[SDK_PATH_CAP], generated[SDK_PATH_CAP], product[SDK_PATH_CAP];
    char scratch[SDK_PATH_CAP], global_cache[SDK_PATH_CAP];
    char* owned[SDK_ARGUMENT_LIMIT]; int owned_count;
    const char *previous_local_cache, *previous_global_cache;
} SdkInvocation;

/* Signal handlers need process-lifetime storage, independent of command cleanup. */
static WysnaProcessCancellation sdk_cancellation;
#ifdef _WIN32
static BOOL WINAPI sdk_console(DWORD event) {
    if (event != CTRL_C_EVENT && event != CTRL_BREAK_EVENT) return FALSE;
    wysna_host_process_request_cancel(&sdk_cancellation, SIGINT); return TRUE;
}
#else
static void sdk_signal(int signal) { wysna_host_process_request_cancel(&sdk_cancellation, signal); }
#endif
static int sdk_error(const char* code, const char* message, const char* detail) {
    fprintf(stderr, "[%s] %s%s%s\n", code, message, detail ? ": " : "", detail ? detail : "");
    return strcmp(code, "W5002") == 0 ? 2 : 1;
}
static void sdk_help(void) {
    puts("Wysna " WYSNA_NATIVE_VERSION " native SDK\n"
         "Usage: wysna check SOURCE\n"
         "       wysna emit-c|emit-mir SOURCE [-o OUTPUT]\n"
         "       wysna build SOURCE [-o OUTPUT] [--profile dev|release|size]\n"
         "       wysna run SOURCE [--profile dev|release|size] [--timeout-ms N] [-- ARGUMENTS]\n"
         "Build and run accept --include-dir DIR, --link FILE, --library-dir DIR and --library NAME.\n"
         "This SDK compiles through verified MIR without Python. See native-sdk.json for its profile.");
}
static int sdk_parse(SdkOptions* options, int argc, char** argv) {
    if (argc < 3 || argc > SDK_ARGUMENT_LIMIT) return 0;
    options->command = argv[1]; options->profile = "dev"; options->timeout = 600000;
    if (strcmp(options->command, "check") && strcmp(options->command, "emit-c") &&
        strcmp(options->command, "emit-mir") && strcmp(options->command, "build") && strcmp(options->command, "run")) return 0;
    for (int index = 2; index < argc; index++) {
        const char* value = argv[index];
        if (strcmp(value, "--") == 0 && strcmp(options->command, "run") == 0) { options->arguments = index + 1; break; }
        if (value[0] != '-') { if (options->source) return 0; options->source = value; continue; }
        if (++index == argc) return 0;
        const char* argument = argv[index];
        if (strcmp(value, "-o") == 0) { if (options->output) return 0; options->output = argument; }
        else if (strcmp(value, "--profile") == 0) { if (options->profile_set++) return 0; options->profile = argument; }
        else if (strcmp(value, "--timeout-ms") == 0) {
            if (options->timeout_set++) return 0;
            char* end = NULL; errno = 0; long parsed = strtol(argument, &end, 10);
            if (errno || !end || *end || end == argument || parsed < 1 || parsed > 600000) return 0;
            options->timeout = parsed;
        } else if (strcmp(value, "--include-dir") == 0 && options->include_count < SDK_LINK_LIMIT) options->includes[options->include_count++] = argument;
        else if (strcmp(value, "--link") == 0 && options->link_count < SDK_LINK_LIMIT) options->links[options->link_count++] = argument;
        else if (strcmp(value, "--library-dir") == 0 && options->library_dir_count < SDK_LINK_LIMIT) options->library_dirs[options->library_dir_count++] = argument;
        else if (strcmp(value, "--library") == 0 && options->library_count < SDK_LINK_LIMIT) {
            if (!argument[0]) return 0;
            for (const char* current = argument; *current; current++)
                if (!isalnum((unsigned char)*current) && *current != '_' && *current != '-' && *current != '.') return 0;
            options->libraries[options->library_count++] = argument;
        } else return 0;
    }
    if (!options->source) return 0;
    if (strcmp(options->profile, "dev") && strcmp(options->profile, "release") && strcmp(options->profile, "size")) return 0;
    int compiles = strcmp(options->command, "build") == 0 || strcmp(options->command, "run") == 0;
    if (!compiles && (options->include_count || options->link_count || options->library_count || options->library_dir_count || options->profile_set)) return 0;
    if (options->timeout_set && strcmp(options->command, "run")) return 0;
    if (options->output && (strcmp(options->command, "check") == 0 || strcmp(options->command, "run") == 0)) return 0;
    return 1;
}
static int sdk_component_paths(SdkInvocation* invocation) {
    if (!sdk_executable(invocation->scratch) || !sdk_absolute(invocation->root, invocation->scratch, 1) ||
        !sdk_parent(invocation->scratch, invocation->root) || !sdk_parent(invocation->root, invocation->scratch) ||
        !sdk_join(invocation->stdlib, invocation->root, "stdlib") ||
        !sdk_join(invocation->engine, invocation->root, SDK_ENGINE) || !sdk_join(invocation->zig, invocation->root, SDK_ZIG)) return 0;
    return sdk_is_file(invocation->engine);
}
static void* sdk_buffer(const char* data, size_t size) {
    void* buffer = wysna_host_buffer_new();
    for (size_t index = 0; index < size; index++) {
        if (wysna_host_buffer_append(buffer, (unsigned char)data[index]) != 0) { wysna_host_close(buffer); return NULL; }
    }
    return buffer;
}
static int sdk_child(const char* executable, int argc, const char** argv, int64_t timeout) {
    void* command = sdk_buffer(executable, strlen(executable));
    void* arguments = wysna_host_buffer_new();
    int valid = command != NULL;
    for (int index = 0; valid && index < argc; index++) {
        size_t length = strlen(argv[index]);
        if (length > 1048576) { valid = 0; break; }
        for (int shift = 24; shift >= 0; shift -= 8)
            if (wysna_host_buffer_append(arguments, (length >> shift) & 255) != 0) valid = 0;
        for (size_t offset = 0; valid && offset < length; offset++)
            if (wysna_host_buffer_append(arguments, (unsigned char)argv[index][offset]) != 0) valid = 0;
    }
    int64_t result = valid ? wysna_host_process_run_cancellable(command, arguments, timeout, &sdk_cancellation) : -1;
    int64_t error = wysna_host_last_error();
    wysna_host_close(command); wysna_host_close(arguments);
    int interrupted = wysna_host_process_cancelled(&sdk_cancellation);
    if (interrupted) return 128 + interrupted;
    if (result < 0) {
        fprintf(stderr, "[W5001] Process failed to start or exceeded its deadline: %s (host error %" PRId64 ")\n", executable, error);
        return 1;
    }
    return (int)(uint32_t)result;
}
static const char* sdk_owned(SdkInvocation* invocation, const char* prefix, const char* value) {
    if (invocation->owned_count == SDK_ARGUMENT_LIMIT) return NULL;
    size_t size = strlen(prefix) + strlen(value) + 1;
    char* copy = wysna_alloc_safe(size);
    snprintf(copy, size, "%s%s", prefix, value);
    invocation->owned[invocation->owned_count++] = copy;
    return copy;
}
static const char* sdk_env(SdkInvocation* invocation, const char* name) {
    char* value = sdk_environment_value(name);
    if (!value) return NULL;
    if (invocation->owned_count == SDK_ARGUMENT_LIMIT) { free(value); return NULL; }
    invocation->owned[invocation->owned_count++] = value;
    return value;
}
static int sdk_prepare_cache(SdkInvocation* invocation) {
    const char* local = sdk_env(invocation, "ZIG_LOCAL_CACHE_DIR");
    const char* global = sdk_env(invocation, "ZIG_GLOBAL_CACHE_DIR");
    invocation->previous_local_cache = local;
    invocation->previous_global_cache = global;
    const char* cache = sdk_env(invocation, "WYSNA_CACHE_HOME");
    if (cache && cache[0]) {
        if (!sdk_absolute(invocation->global_cache, cache, 0)) return 0;
    } else {
#ifdef _WIN32
        const char* home = sdk_env(invocation, "LOCALAPPDATA");
        if (!home || !home[0]) return 0;
        if (!sdk_join(invocation->global_cache, home, "Wysna/zig-0.15.2")) return 0;
#else
        const char* home = sdk_env(invocation, "XDG_CACHE_HOME");
        if (home && home[0]) { if (!sdk_join(invocation->global_cache, home, "wysna/zig-0.15.2")) return 0; }
        else {
            home = sdk_env(invocation, "HOME"); if (!home || !home[0]) return 0;
            if (!sdk_join(invocation->global_cache, home, ".cache/wysna/zig-0.15.2")) return 0;
        }
#endif
    }
    return sdk_join(invocation->scratch, invocation->workspace, "zig-cache") &&
        sdk_environment("ZIG_LOCAL_CACHE_DIR", invocation->scratch) &&
        sdk_environment("ZIG_GLOBAL_CACHE_DIR", invocation->global_cache);
}
static int sdk_build(SdkInvocation* invocation, const SdkOptions* options) {
    if (!sdk_is_file(invocation->zig)) return sdk_error("W5001", "Missing bundled C toolchain; reinstall the SDK", invocation->zig);
    if (!sdk_prepare_cache(invocation)) return sdk_error("W5001", "Cannot configure a writable compiler cache", NULL);
    const char* arguments[SDK_ARGUMENT_LIMIT]; int count = 0;
    arguments[count++] = "cc"; arguments[count++] = "-target"; arguments[count++] = WYSNA_NATIVE_TARGET; arguments[count++] = "-std=c99"; arguments[count++] = "-mcpu=baseline";
    if (strcmp(options->profile, "dev") == 0) { arguments[count++] = "-O0"; arguments[count++] = "-g"; }
    else arguments[count++] = strcmp(options->profile, "size") == 0 ? "-Os" : "-O2";
    arguments[count++] = sdk_owned(invocation, "-I", invocation->root);
    for (int index = 0; index < options->include_count; index++) {
        if (!sdk_absolute(invocation->scratch, options->includes[index], 1) || !sdk_is_directory(invocation->scratch)) return sdk_error("W5002", "Invalid include directory", options->includes[index]);
        arguments[count++] = sdk_owned(invocation, "-I", invocation->scratch);
    }
    arguments[count++] = invocation->generated;
    for (int index = 0; index < options->link_count; index++) {
        if (!sdk_absolute(invocation->scratch, options->links[index], 1) || !sdk_is_file(invocation->scratch)) return sdk_error("W5002", "Invalid link input", options->links[index]);
        arguments[count++] = sdk_owned(invocation, "", invocation->scratch);
    }
    for (int index = 0; index < options->library_dir_count; index++) {
        if (!sdk_absolute(invocation->scratch, options->library_dirs[index], 1) || !sdk_is_directory(invocation->scratch)) return sdk_error("W5002", "Invalid library directory", options->library_dirs[index]);
        arguments[count++] = sdk_owned(invocation, "-L", invocation->scratch);
    }
    for (int index = 0; index < options->library_count; index++) arguments[count++] = sdk_owned(invocation, "-l", options->libraries[index]);
#ifdef _WIN32
    arguments[count++] = WYSNA_NATIVE_STACK_LINK_ARGUMENT;
#endif
    arguments[count++] = "-lc"; arguments[count++] = "-lm"; arguments[count++] = "-o"; arguments[count++] = invocation->product;
    int status = sdk_child(invocation->zig, count, arguments, SDK_COMPILE_TIMEOUT_MS);
    int restored_local = sdk_environment("ZIG_LOCAL_CACHE_DIR", invocation->previous_local_cache);
    int restored_global = sdk_environment("ZIG_GLOBAL_CACHE_DIR", invocation->previous_global_cache);
    if (!restored_local || !restored_global) return sdk_error("W5001", "Cannot restore the application environment", NULL);
    return status;
}
static int sdk_execute(SdkInvocation* invocation, const SdkOptions* options, int argc, char** argv) {
    if (!sdk_component_paths(invocation)) return sdk_error("W5001", "Cannot locate the native compiler beside this SDK", NULL);
    if (!sdk_absolute(invocation->source, options->source, 1) || !sdk_is_file(invocation->source)) return sdk_error("W5001", "Source is not an accessible regular file", options->source);
    int build = strcmp(options->command, "build") == 0;
    int run = strcmp(options->command, "run") == 0;
    if (build || options->output) {
        const char* target = options->output;
        if (!target) {
            target = strrchr(invocation->source, '/');
#ifdef _WIN32
            const char* backslash = strrchr(invocation->source, '\\');
            if (backslash && (!target || backslash > target)) target = backslash;
#endif
            target = target ? target + 1 : invocation->source;
            if (!sdk_copy(invocation->scratch, target)) return sdk_error("W5002", "Output path is too long", NULL);
            char* dot = strrchr(invocation->scratch, '.'); if (dot && dot != invocation->scratch) *dot = 0;
            target = invocation->scratch;
        }
        if (!sdk_absolute(invocation->output, target, 0)) return sdk_error("W5002", "Invalid output path", target);
#ifdef _WIN32
        if (build) {
            size_t size = strlen(invocation->output);
            if (size < 4 || _stricmp(invocation->output + size - 4, ".exe") != 0) {
                if (size + 4 >= SDK_PATH_CAP) return sdk_error("W5002", "Output path is too long", NULL);
                memcpy(invocation->output + size, ".exe", 5);
            }
        }
#endif
        if (sdk_same_file(invocation->source, invocation->output)) return sdk_error("W5002", "Output must not replace the source or an alias", options->output);
        size_t size = strlen(invocation->output);
        int source_suffix = size >= 6;
        const char* extension = ".wysna";
        for (int index = 0; source_suffix && index < 6; index++)
            if (tolower((unsigned char)invocation->output[size - 6 + index]) != extension[index]) source_suffix = 0;
        if (source_suffix) return sdk_error("W5002", "Output must not replace a Wysna source file", invocation->output);
        if (!sdk_parent(invocation->parent, invocation->output) || !sdk_temp(invocation->workspace, invocation->parent)) return sdk_error("W5001", "Cannot create a staging directory beside the output", invocation->output);
    } else if (!sdk_temp(invocation->workspace, NULL)) return sdk_error("W5001", "Cannot create a private staging directory", NULL);
    if (!sdk_join(invocation->generated, invocation->workspace, "program.c") ||
        !sdk_join(invocation->product, invocation->workspace, SDK_PROGRAM)) return sdk_error("W5002", "Staging path is too long", NULL);
    const char* engine_arguments[6]; int engine_count = 0;
    engine_arguments[engine_count++] = "--stdlib-root";
    engine_arguments[engine_count++] = invocation->stdlib;
    engine_arguments[engine_count++] = strcmp(options->command, "check") == 0 ? "--check-mir" : strcmp(options->command, "emit-mir") == 0 ? "--emit-mir" : "--emit-c-mir";
    engine_arguments[engine_count++] = invocation->source;
    if (build || run || options->output) engine_arguments[engine_count++] = invocation->generated;
    int status = sdk_child(invocation->engine, engine_count, engine_arguments, SDK_COMPILE_TIMEOUT_MS);
    if (status) return status;
    if (build || run) {
        status = sdk_build(invocation, options); if (status) return status;
    }
    if (invocation->output[0]) {
        if (sdk_same_file(invocation->source, invocation->output)) return sdk_error("W5002", "Output became a source alias during compilation", NULL);
        if (!sdk_publish(build ? invocation->product : invocation->generated, invocation->output)) return sdk_error("W5001", "Cannot publish the completed output", invocation->output);
    }
    if (run) {
        int count = options->arguments ? argc - options->arguments : 0;
        return sdk_child(invocation->product, count,
                         options->arguments ? (const char**)(argv + options->arguments) : NULL, options->timeout);
    }
    return 0;
}
int main(int argc, char** argv) {
    wysna_init_args(argc, argv); argc = wysna_g_argc; argv = wysna_g_argv;
    if (argc == 2 && strcmp(argv[1], "--version") == 0) { puts("Wysna " WYSNA_NATIVE_VERSION " native SDK"); wysna_runtime_cleanup(); return 0; }
    if (argc == 2 && (strcmp(argv[1], "--help") == 0 || strcmp(argv[1], "-h") == 0)) { sdk_help(); wysna_runtime_cleanup(); return 0; }
    SdkOptions options = {0};
    if (!sdk_parse(&options, argc, argv)) { int status = sdk_error("W5002", "Invalid command; run wysna --help", NULL); wysna_runtime_cleanup(); return status; }
    SdkInvocation* invocation = calloc(1, sizeof(*invocation));
    if (!invocation) { wysna_runtime_cleanup(); return sdk_error("W5001", "Cannot allocate command context", NULL); }
#ifdef _WIN32
    SetConsoleCtrlHandler(sdk_console, TRUE);
#else
    struct sigaction action; memset(&action, 0, sizeof(action)); action.sa_handler = sdk_signal;
    sigemptyset(&action.sa_mask); sigaction(SIGINT, &action, NULL); sigaction(SIGTERM, &action, NULL);
#endif
    int status = sdk_execute(invocation, &options, argc, argv);
    int cleaned = !invocation->workspace[0] || sdk_remove_tree(invocation->workspace);
#ifdef _WIN32
    for (int attempt = 0; !cleaned && attempt < 10; attempt++) { Sleep(50); cleaned = sdk_remove_tree(invocation->workspace); }
    SetConsoleCtrlHandler(sdk_console, FALSE);
#else
    signal(SIGINT, SIG_DFL); signal(SIGTERM, SIG_DFL);
#endif
    if (!cleaned) {
        sdk_error("W5001", "Cannot remove the command's staging directory", invocation->workspace);
        if (!status) status = 1;
    }
    for (int index = 0; index < invocation->owned_count; index++) free(invocation->owned[index]);
    free(invocation); wysna_runtime_cleanup(); return status;
}
