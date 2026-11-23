/* Command orchestration for an installed SDK; source compilation stays in Wysna. */
#ifndef _WIN32
#define _XOPEN_SOURCE 700
#ifdef __APPLE__
#define _DARWIN_C_SOURCE 1
#endif
#endif
#include "runtime/wysna_runtime.h"
#include "platform.h"
#include <fcntl.h>
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
#include <process.h>
#define SDK_ENGINE "libexec/wysna-compiler.exe"
#define SDK_PROJECT_ENGINE "libexec/wysna-project.exe"
#define SDK_MCP_ENGINE "libexec/wysna-mcp.exe"
#define SDK_ZIG "toolchain/zig/zig.exe"
#define SDK_PROGRAM "program.exe"
#else
#define SDK_ENGINE "libexec/wysna-compiler"
#define SDK_PROJECT_ENGINE "libexec/wysna-project"
#define SDK_MCP_ENGINE "libexec/wysna-mcp"
#define SDK_ZIG "toolchain/zig/zig"
#define SDK_PROGRAM "program"
#endif

typedef struct {
    const char *command, *source, *output, *profile;
    const char *project_subcmd, *project_src_root, *unsupported_target;
    const char *allow_caps, *deny_caps;
    int arguments, include_count, link_count, library_count, library_dir_count, dep_count;
    int profile_set, timeout_set, project_json_v1, test_diagnostics, sandbox, json_v1, target_set, wasi;
    const char *includes[SDK_LINK_LIMIT], *links[SDK_LINK_LIMIT], *libraries[SDK_LINK_LIMIT], *library_dirs[SDK_LINK_LIMIT];
    const char *dep_aliases[SDK_LINK_LIMIT], *dep_roots[SDK_LINK_LIMIT];
    int64_t timeout, test_deadline;
} SdkOptions;
typedef struct {
    char root[SDK_PATH_CAP], engine[SDK_PATH_CAP], project_engine[SDK_PATH_CAP], mcp_engine[SDK_PATH_CAP];
    char zig[SDK_PATH_CAP], stdlib[SDK_PATH_CAP];
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
         "Usage: wysna check SOURCE [--output-format=json-v1]\n"
         "       wysna emit-c|emit-mir SOURCE [-o OUTPUT]\n"
         "       wysna build SOURCE [-o OUTPUT] [--profile dev|release|size]\n"
         "       wysna run SOURCE [--profile dev|release|size] [--timeout-ms N] [-- ARGUMENTS]\n"
         "       wysna test TARGET [--timeout-ms N] [-- ARGUMENTS]\n"
         "       wysna fmt SOURCE [--write] [--output-format=json-v1]\n"
         "       wysna graph SOURCE [--symbol NAME] [--rel KIND] [--output-format=json-v1]\n"
         "       wysna inspect SOURCE [--symbol NAME] [--with-deps] [--output-format=json-v1]\n"
         "       wysna patch SOURCE --symbol NAME (--with CODE | --with-file PATH) [--expect-graph-hash HASH] [--apply] [--output-format=json-v1]\n"
         "       wysna explain (SOURCE | --code Wxxxx) [--function NAME] [--output-format=json-v1]\n"
         "       wysna schema SOURCE [--all] [--function NAME] [--emit-codecs] [-o OUTPUT] [--output-format=json-v1]\n"
         "       wysna fix SOURCE [--apply] [--include-maybe] [--output-format=json-v1]\n"
         "       wysna skills [TOPIC] [--output-format=json-v1]\n"
         "       wysna capabilities [--output-format=json-v1]\n"
         "       wysna api [MODULE] [--manifest PATH] [--output-format=json-v1]\n"
         "       wysna project <new|lock|check|build|run|test> [--manifest PATH] [--target NAME] [--profile dev|release|size] [-o OUTPUT] [--filter NAME] [-- ARGUMENTS]\n"
         "       wysna packages <subcommand> [options]\n"
         "       wysna mcp [--root DIR]\n"
         "       wysna lsp\n"
         "Build and run accept --include-dir DIR, --link FILE, --library-dir DIR and --library NAME.\n"
         "Build accepts --target=wasi for a standalone WASI preview 1 command.\n"
         "This SDK compiles through verified MIR without Python. See native-sdk.json for its profile.");
}
static int sdk_has_json_v1(int argc, char** argv) {
    for (int i = 2; i < argc; i++) {
        if (strcmp(argv[i], "--output-format=json-v1") == 0) return 1;
        if (strcmp(argv[i], "--output-format") == 0 && i + 1 < argc && strcmp(argv[i + 1], "json-v1") == 0) return 1;
    }
    return 0;
}
static int sdk_is_wasm_target(const char* target) {
    return strcmp(target, "wasi") == 0 || strcmp(target, "wasi-command") == 0 ||
           strcmp(target, "wasm32-wasi") == 0 || strcmp(target, "wasm32-wasip1") == 0 ||
           strcmp(target, "web") == 0 || strcmp(target, "browser-library") == 0 ||
           strcmp(target, "wasm32-unknown-emscripten") == 0;
}
static int sdk_parse_target(SdkOptions* options, const char* target) {
    if (options->target_set++) return 0;
    if (strcmp(target, "native") == 0 || strcmp(target, "native-cli") == 0) return 1;
    if (!sdk_is_wasm_target(target)) return 0;
    if (strcmp(options->command, "build") == 0 &&
        (strcmp(target, "wasi") == 0 || strcmp(target, "wasi-command") == 0 ||
         strcmp(target, "wasm32-wasi") == 0 || strcmp(target, "wasm32-wasip1") == 0)) {
        options->wasi = 1;
    } else {
        options->unsupported_target = target;
    }
    return 1;
}
static int sdk_is_agent_passthrough(const char* cmd) {
    return strcmp(cmd, "graph") == 0 || strcmp(cmd, "inspect") == 0 ||
           strcmp(cmd, "patch") == 0 || strcmp(cmd, "explain") == 0 ||
           strcmp(cmd, "schema") == 0 || strcmp(cmd, "fix") == 0 ||
           strcmp(cmd, "skills") == 0 || strcmp(cmd, "capabilities") == 0 ||
           strcmp(cmd, "api") == 0 || strcmp(cmd, "packages") == 0 ||
           strcmp(cmd, "mcp") == 0 || strcmp(cmd, "lsp") == 0;
}
static int sdk_parse_timeout_ms(const char* argument, int64_t* out) {
    char* end = NULL; errno = 0; long parsed = strtol(argument, &end, 10);
    if (errno || !end || *end || end == argument || parsed < 1 || parsed > 600000) return 0;
    *out = (int64_t)parsed;
    return 1;
}
static int sdk_valid_library_name(const char* argument) {
    if (!argument[0]) return 0;
    for (const char* current = argument; *current; current++)
        if (!isalnum((unsigned char)*current) && *current != '_' && *current != '-' && *current != '.') return 0;
    return 1;
}
static int sdk_parse(SdkOptions* options, int argc, char** argv) {
    if (argc >= 2 && argc <= SDK_ARGUMENT_LIMIT &&
        (strcmp(argv[1], "lsp") == 0 || strcmp(argv[1], "mcp") == 0 ||
         strcmp(argv[1], "skills") == 0 || strcmp(argv[1], "capabilities") == 0 ||
         strcmp(argv[1], "api") == 0 || strcmp(argv[1], "packages") == 0)) {
        options->command = argv[1];
        options->profile = "dev";
        options->timeout = (strcmp(argv[1], "lsp") == 0 || strcmp(argv[1], "mcp") == 0) ? 0 : 600000;
        options->source = "";
        return 1;
    }
    if (argc >= 2 && argc <= SDK_ARGUMENT_LIMIT && strcmp(argv[1], "project") == 0) {
        options->command = "project";
        options->profile = "dev";
        options->timeout = 600000;
        options->source = "";
        options->project_subcmd = argc >= 3 ? argv[2] : "";
        if (strcmp(options->project_subcmd, "build") != 0 &&
            strcmp(options->project_subcmd, "run") != 0 &&
            strcmp(options->project_subcmd, "test") != 0) {
            return 1;
        }
        for (int index = 3; index < argc; index++) {
            const char* value = argv[index];
            if (strcmp(value, "--") == 0 && strcmp(options->project_subcmd, "run") == 0) {
                options->arguments = index + 1;
                break;
            }
            if (strcmp(value, "--output-format=json-v1") == 0) { options->project_json_v1 = 1; continue; }
            if (strcmp(value, "--output-format") == 0 && index + 1 < argc) {
                if (strcmp(argv[index + 1], "json-v1") == 0) options->project_json_v1 = 1;
                index++;
                continue;
            }
            if (strcmp(value, "--sandbox") == 0) { options->sandbox = 1; continue; }
            if (strncmp(value, "--allow=", 8) == 0) {
                if (options->allow_caps) return 0;
                options->allow_caps = value + 8;
                continue;
            }
            if (strncmp(value, "--deny=", 7) == 0) {
                if (options->deny_caps) return 0;
                options->deny_caps = value + 7;
                continue;
            }
            if (strcmp(value, "--allow") == 0) {
                if (++index >= argc || options->allow_caps) return 0;
                options->allow_caps = argv[index];
            } else if (strcmp(value, "--deny") == 0) {
                if (++index >= argc || options->deny_caps) return 0;
                options->deny_caps = argv[index];
            } else if (strcmp(value, "--profile") == 0) {
                if (++index >= argc || options->profile_set++) return 0;
                options->profile = argv[index];
            } else if (strcmp(value, "--timeout-ms") == 0) {
                if (++index >= argc || options->timeout_set++) return 0;
                if (!sdk_parse_timeout_ms(argv[index], &options->timeout)) return 0;
            } else if (strcmp(value, "--include-dir") == 0) {
                if (++index >= argc || options->include_count >= SDK_LINK_LIMIT) return 0;
                options->includes[options->include_count++] = argv[index];
            } else if (strcmp(value, "--link") == 0) {
                if (++index >= argc || options->link_count >= SDK_LINK_LIMIT) return 0;
                options->links[options->link_count++] = argv[index];
            } else if (strcmp(value, "--library-dir") == 0) {
                if (++index >= argc || options->library_dir_count >= SDK_LINK_LIMIT) return 0;
                options->library_dirs[options->library_dir_count++] = argv[index];
            } else if (strcmp(value, "--library") == 0) {
                if (++index >= argc || options->library_count >= SDK_LINK_LIMIT) return 0;
                if (!sdk_valid_library_name(argv[index])) return 0;
                options->libraries[options->library_count++] = argv[index];
            }
        }
        if (strcmp(options->profile, "dev") && strcmp(options->profile, "release") && strcmp(options->profile, "size")) return 0;
        if (options->timeout_set && strcmp(options->project_subcmd, "build") == 0) return 0;
        if (options->allow_caps && options->deny_caps) return 0;
        if ((options->sandbox || options->allow_caps || options->deny_caps) &&
            (strcmp(options->project_subcmd, "test") == 0 ||
             options->include_count || options->link_count || options->library_count || options->library_dir_count)) return 0;
        return 1;
    }
    if (argc < 3 || argc > SDK_ARGUMENT_LIMIT) return 0;
    options->command = argv[1]; options->profile = "dev"; options->timeout = 600000;
    if (sdk_is_agent_passthrough(options->command)) {
        options->source = argv[2];
        return 1;
    }
    if (strcmp(options->command, "check") && strcmp(options->command, "emit-c") &&
        strcmp(options->command, "emit-mir") && strcmp(options->command, "build") &&
        strcmp(options->command, "run") && strcmp(options->command, "test") &&
        strcmp(options->command, "fmt")) return 0;
    for (int index = 2; index < argc; index++) {
        const char* value = argv[index];
        if (strcmp(value, "--") == 0 && (strcmp(options->command, "run") == 0 || strcmp(options->command, "test") == 0)) { options->arguments = index + 1; break; }
        if (value[0] != '-') { if (options->source) return 0; options->source = value; continue; }
        if (strcmp(value, "--output-format=json-v1") == 0 &&
            (strcmp(options->command, "check") == 0 || strcmp(options->command, "fmt") == 0 ||
             strcmp(options->command, "build") == 0 || strcmp(options->command, "emit-c") == 0)) {
            options->json_v1 = 1;
            continue;
        }
        if (strcmp(value, "--sandbox") == 0) { options->sandbox = 1; continue; }
        if (strncmp(value, "--allow=", 8) == 0) {
            if (options->allow_caps) return 0;
            options->allow_caps = value + 8;
            continue;
        }
        if (strncmp(value, "--deny=", 7) == 0) {
            if (options->deny_caps) return 0;
            options->deny_caps = value + 7;
            continue;
        }
        if (strncmp(value, "--target=", 9) == 0) {
            if (!sdk_parse_target(options, value + 9)) return 0;
            if (options->unsupported_target) return 1;
            continue;
        }
        if ((strcmp(value, "--write") == 0 || strcmp(value, "-w") == 0) && strcmp(options->command, "fmt") == 0) continue;
        if (++index == argc) return 0;
        const char* argument = argv[index];
        if (strcmp(value, "-o") == 0) { if (options->output) return 0; options->output = argument; }
        else if (strcmp(value, "--allow") == 0) { if (options->allow_caps) return 0; options->allow_caps = argument; }
        else if (strcmp(value, "--deny") == 0) { if (options->deny_caps) return 0; options->deny_caps = argument; }
        else if (strcmp(value, "--target") == 0) {
            if (!sdk_parse_target(options, argument)) return 0;
            if (options->unsupported_target) return 1;
        }
        else if (strcmp(value, "--output-format") == 0 &&
                 (strcmp(options->command, "check") == 0 || strcmp(options->command, "fmt") == 0 ||
                  strcmp(options->command, "build") == 0 || strcmp(options->command, "emit-c") == 0)) {
            if (strcmp(argument, "json-v1") != 0) return 0;
            options->json_v1 = 1;
        }
        else if (strcmp(value, "--profile") == 0) { if (options->profile_set++) return 0; options->profile = argument; }
        else if (strcmp(value, "--timeout-ms") == 0) {
            if (options->timeout_set++ || !sdk_parse_timeout_ms(argument, &options->timeout)) return 0;
        } else if (strcmp(value, "--include-dir") == 0 && options->include_count < SDK_LINK_LIMIT) options->includes[options->include_count++] = argument;
        else if (strcmp(value, "--link") == 0 && options->link_count < SDK_LINK_LIMIT) options->links[options->link_count++] = argument;
        else if (strcmp(value, "--library-dir") == 0 && options->library_dir_count < SDK_LINK_LIMIT) options->library_dirs[options->library_dir_count++] = argument;
        else if (strcmp(value, "--library") == 0 && options->library_count < SDK_LINK_LIMIT) {
            if (!sdk_valid_library_name(argument)) return 0;
            options->libraries[options->library_count++] = argument;
        } else return 0;
    }
    if (!options->source) return 0;
    if (strcmp(options->profile, "dev") && strcmp(options->profile, "release") && strcmp(options->profile, "size")) return 0;
    int compiles = strcmp(options->command, "build") == 0 || strcmp(options->command, "run") == 0 || strcmp(options->command, "test") == 0;
    int allows_sandbox = strcmp(options->command, "build") == 0 || strcmp(options->command, "run") == 0 || strcmp(options->command, "emit-c") == 0;
    if (!compiles && (options->include_count || options->link_count || options->library_count || options->library_dir_count || options->profile_set)) return 0;
    if (options->allow_caps && options->deny_caps) return 0;
    if (options->wasi) {
        if (options->allow_caps || options->deny_caps || options->include_count || options->link_count ||
            options->library_count || options->library_dir_count) return 0;
        options->sandbox = 1;
        options->deny_caps = "network,processes,process-control,native-ffi";
    }
    if (options->sandbox || options->allow_caps || options->deny_caps) {
        if (!allows_sandbox) return 0;
        if (options->include_count || options->link_count || options->library_count || options->library_dir_count) return 0;
    }
    if (options->timeout_set && strcmp(options->command, "run") != 0 && strcmp(options->command, "test") != 0) return 0;
    if (options->output && (strcmp(options->command, "check") == 0 || strcmp(options->command, "run") == 0 || strcmp(options->command, "test") == 0 || strcmp(options->command, "fmt") == 0)) return 0;
    return 1;
}
static int sdk_component_paths(SdkInvocation* invocation) {
    if (!sdk_executable(invocation->scratch) || !sdk_absolute(invocation->root, invocation->scratch, 1) ||
        !sdk_parent(invocation->scratch, invocation->root) || !sdk_parent(invocation->root, invocation->scratch) ||
        !sdk_join(invocation->stdlib, invocation->root, "stdlib") ||
        !sdk_join(invocation->engine, invocation->root, SDK_ENGINE) || !sdk_join(invocation->zig, invocation->root, SDK_ZIG)) return 0;
    if (!sdk_is_file(invocation->engine)) return 0;
    if (!sdk_join(invocation->project_engine, invocation->root, SDK_PROJECT_ENGINE) || !sdk_is_file(invocation->project_engine)) {
        (void)sdk_copy(invocation->project_engine, invocation->engine);
    }
    if (!sdk_join(invocation->mcp_engine, invocation->root, SDK_MCP_ENGINE) || !sdk_is_file(invocation->mcp_engine)) {
        (void)sdk_copy(invocation->mcp_engine, invocation->engine);
    }
    return 1;
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
static int64_t sdk_compile_timeout(const SdkOptions* options) {
    if (!options->test_deadline) return SDK_COMPILE_TIMEOUT_MS;
    int64_t remaining = options->test_deadline - wysna_host_monotonic_ms_internal();
    return remaining > 0 ? remaining : 1;
}

static int sdk_build(SdkInvocation* invocation, const SdkOptions* options) {
    if (!sdk_is_file(invocation->zig)) return sdk_error("W5001", "Missing bundled C toolchain; reinstall the SDK", invocation->zig);
    if (!sdk_prepare_cache(invocation)) return sdk_error("W5001", "Cannot configure a writable compiler cache", NULL);
    const char* arguments[SDK_ARGUMENT_LIMIT]; int count = 0;
    arguments[count++] = "cc";
    arguments[count++] = "-target";
    arguments[count++] = options->wasi ? "wasm32-wasi" : WYSNA_NATIVE_TARGET;
    arguments[count++] = "-std=c99";
    if (options->wasi) arguments[count++] = "-Wl,-z,stack-size=1048576";
    else arguments[count++] = "-mcpu=baseline";
    if (strcmp(options->profile, "dev") == 0) { arguments[count++] = "-O0"; arguments[count++] = "-g"; }
    else arguments[count++] = strcmp(options->profile, "size") == 0 ? "-Os" : "-O2";
#ifdef __linux__
    /* Weak references alone cannot pull pthread into the SDK's static musl programs. */
    if (!options->wasi) {
        arguments[count++] = "-pthread";
        arguments[count++] = "-DWYSNA_LINK_PTHREAD";
    }
#endif
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
    if (!options->wasi) arguments[count++] = WYSNA_NATIVE_STACK_LINK_ARGUMENT;
#endif
    arguments[count++] = "-lc"; arguments[count++] = "-lm"; arguments[count++] = "-o"; arguments[count++] = invocation->product;
    int status = sdk_child(invocation->zig, count, arguments, sdk_compile_timeout(options));
    int restored_local = sdk_environment("ZIG_LOCAL_CACHE_DIR", invocation->previous_local_cache);
    int restored_global = sdk_environment("ZIG_GLOBAL_CACHE_DIR", invocation->previous_global_cache);
    if (!restored_local || !restored_global) return sdk_error("W5001", "Cannot restore the application environment", NULL);
    return status;
}
#include "stdio_capture.h"

static int sdk_child_captured(const char* executable, int argc, const char** argv, int64_t timeout,
                              const char* stdout_path, const char* stderr_path) {
    SdkStdioCapture capture;
    if (!sdk_stdio_capture(&capture, stdout_path, stderr_path)) return -1;
    int result = sdk_child(executable, argc, argv, timeout);
    sdk_stdio_restore(&capture);
    return result;
}

static size_t sdk_normalize_newlines(char* text, size_t len) {
    size_t write = 0;
    for (size_t i = 0; i < len; i++) {
        if (text[i] == '\r' && i + 1 < len && text[i + 1] == '\n') continue;
        text[write++] = text[i];
    }
    text[write] = 0;
    return write;
}

static int sdk_parse_panic_expectation(const char* expected, char* panic_buf, size_t cap) {
    const char* p = expected;
    while (*p == ' ' || *p == '\t' || *p == '\r' || *p == '\n') p++;
    const char* prefix = "# RUNTIME_PANIC:";
    size_t plen = strlen(prefix);
    if (strncmp(p, prefix, plen) != 0) return 0;
    p += plen;
    while (*p == ' ' || *p == '\t') p++;
    size_t n = 0;
    while (p[n] && p[n] != '\n' && p[n] != '\r') n++;
    while (n > 0 && (p[n - 1] == ' ' || p[n - 1] == '\t')) n--;
    if (n == 0 || n >= cap) return 0;
    memcpy(panic_buf, p, n);
    panic_buf[n] = 0;
    return 1;
}

static int sdk_compile_source(SdkInvocation* invocation, const SdkOptions* options, const char* command_mode, const char* source_path, int need_product_dir) {
    if (!sdk_join(invocation->generated, invocation->workspace, "program.c") ||
        !sdk_join(invocation->product, invocation->workspace, SDK_PROGRAM)) return sdk_error("W5002", "Staging path is too long", NULL);
    const char* engine_arguments[SDK_ARGUMENT_LIMIT]; int engine_count = 0;
    engine_arguments[engine_count++] = "--stdlib-root";
    engine_arguments[engine_count++] = invocation->stdlib;
    if (options->project_src_root && options->project_src_root[0]) {
        engine_arguments[engine_count++] = "--project-sources-root";
        engine_arguments[engine_count++] = options->project_src_root;
    }
    for (int d = 0; d < options->dep_count && engine_count + 3 < SDK_ARGUMENT_LIMIT; d++) {
        engine_arguments[engine_count++] = "--project-dep";
        engine_arguments[engine_count++] = options->dep_aliases[d];
        engine_arguments[engine_count++] = options->dep_roots[d];
    }
    if (options->test_diagnostics) {
        if (!sdk_join(invocation->scratch, invocation->workspace, "diagnostics.json")) return 1;
        engine_arguments[engine_count++] = "--diag-array";
        engine_arguments[engine_count++] = "--diag-out";
        engine_arguments[engine_count++] = invocation->scratch;
    }
    engine_arguments[engine_count++] = command_mode;
    if (strcmp(command_mode, "--emit-c-mir") == 0) {
        if (options->sandbox) engine_arguments[engine_count++] = "--sandbox";
        if (options->allow_caps) {
            engine_arguments[engine_count++] = "--allow";
            engine_arguments[engine_count++] = options->allow_caps;
        }
        if (options->deny_caps) {
            engine_arguments[engine_count++] = "--deny";
            engine_arguments[engine_count++] = options->deny_caps;
        }
        if (options->json_v1 || options->project_json_v1) {
            engine_arguments[engine_count++] = "--output-format=json-v1";
        }
    }
    engine_arguments[engine_count++] = source_path;
    if (need_product_dir || options->output) engine_arguments[engine_count++] = invocation->generated;
    return sdk_child(invocation->engine, engine_count, engine_arguments, sdk_compile_timeout(options));
}

static int sdk_run_test_case(SdkInvocation* invocation, const SdkOptions* options, const char* source_path, const char* display_name, int argc, char** argv) {
    if (!sdk_temp(invocation->workspace, NULL)) return sdk_error("W5001", "Cannot create a private staging directory", NULL);
    int status = sdk_compile_source(invocation, options, "--emit-c-mir", source_path, 1);
    if (!status) status = sdk_build(invocation, options);
    if (status) {
        fprintf(stderr, "[FAIL] %s (native compilation failed)\n", display_name);
        goto cleanup;
    }

    char expected_path[SDK_PATH_CAP];
    size_t slen = strlen(source_path);
    int has_expected = 0;
    if (slen > 6 && strcmp(source_path + slen - 6, ".wysna") == 0 && slen + 4 < SDK_PATH_CAP) {
        memcpy(expected_path, source_path, slen - 6);
        memcpy(expected_path + slen - 6, ".expected", 10);
        has_expected = sdk_is_file(expected_path);
    }

    int arg_count = options->arguments ? argc - options->arguments : 0;
    const char** child_args = options->arguments ? (const char**)(argv + options->arguments) : NULL;
    if (!has_expected) {
        int result = sdk_child(invocation->product, arg_count, child_args, options->timeout);
        if (result == 0) {
            printf("[PASS] %s (native)\n", display_name);
            status = 0;
        } else {
            fprintf(stderr, "[FAIL] %s (native, exit code %d)\n", display_name, result);
            status = result;
        }
        goto cleanup;
    }

    char out_file[SDK_PATH_CAP], err_file[SDK_PATH_CAP];
    if (!sdk_join(out_file, invocation->workspace, "test_stdout.txt") ||
        !sdk_join(err_file, invocation->workspace, "test_stderr.txt")) {
        status = sdk_error("W5002", "Test staging path too long", NULL);
        goto cleanup;
    }
    int result = sdk_child_captured(invocation->product, arg_count, child_args, options->timeout, out_file, err_file);
    char *exp_buf = NULL, *got_out = NULL, *got_err = NULL;
    size_t exp_len = 0, out_len = 0, err_len = 0;
    if (!sdk_read_file(expected_path, &exp_buf, &exp_len) ||
        !sdk_read_file(out_file, &got_out, &out_len) ||
        !sdk_read_file(err_file, &got_err, &err_len)) {
        free(exp_buf); free(got_out); free(got_err);
        fprintf(stderr, "[FAIL] %s (native output capture failed)\n", display_name);
        status = 1;
        goto cleanup;
    }
    exp_len = sdk_normalize_newlines(exp_buf, exp_len);
    out_len = sdk_normalize_newlines(got_out, out_len);
    char panic_code[64];
    if (sdk_parse_panic_expectation(exp_buf, panic_code, sizeof(panic_code))) {
        if (result != 0 && strstr(got_err, panic_code) != NULL) {
            printf("[PASS] %s (native panic %s)\n", display_name, panic_code);
            status = 0;
        } else {
            fprintf(stderr, "[FAIL] %s (expected panic %s, exit code %d)\n", display_name, panic_code, result);
            status = 1;
        }
    } else if (result != 0) {
        fprintf(stderr, "[FAIL] %s (native exit code %d)\n", display_name, result);
        status = result;
    } else if (out_len != exp_len || (out_len > 0 && memcmp(got_out, exp_buf, exp_len) != 0)) {
        fprintf(stderr, "[FAIL] %s (native output mismatch: expected %zu bytes, got %zu bytes)\n",
                display_name, exp_len, out_len);
        status = 1;
    } else {
        printf("[PASS] %s (native)\n", display_name);
        status = 0;
    }
    free(exp_buf); free(got_out); free(got_err);

cleanup:
    for (int i = 0; i < invocation->owned_count; i++) free(invocation->owned[i]);
    invocation->owned_count = 0;
    if (invocation->workspace[0]) {
        sdk_remove_tree(invocation->workspace);
        invocation->workspace[0] = 0;
    }
    return status;
}

#include "project_tests.h"

static void sdk_print_json_quoted(const char* text) {
    sdk_test_string(stdout, text);
}

static int sdk_execute_project(SdkInvocation* invocation, const SdkOptions* options, int argc, char** argv) {
    if (strcmp(options->project_subcmd, "build") != 0 &&
        strcmp(options->project_subcmd, "run") != 0 &&
        strcmp(options->project_subcmd, "test") != 0) {
        const char* target_exe = strcmp(options->project_subcmd, "check") == 0 ? invocation->engine : invocation->project_engine;
        const char* engine_arguments[SDK_ARGUMENT_LIMIT + 3];
        engine_arguments[0] = "--stdlib-root";
        engine_arguments[1] = invocation->stdlib;
        int out_count = 2;
        for (int i = 1; i < argc && out_count < SDK_ARGUMENT_LIMIT + 2; i++) {
            engine_arguments[out_count++] = argv[i];
        }
        if (strcmp(target_exe, invocation->engine) != 0) {
            (void)sdk_environment("WYSNA_SELFHOST_COMPILER", invocation->engine);
        }
        return sdk_child(target_exe, out_count, engine_arguments, options->timeout);
    }

    char plan_dir[SDK_PATH_CAP], plan_path[SDK_PATH_CAP];
    if (!sdk_temp(plan_dir, NULL) || !sdk_join(plan_path, plan_dir, "plan.txt")) {
        return sdk_error("W5001", "Cannot create temporary project plan staging directory", NULL);
    }
    const char* plan_argv[SDK_ARGUMENT_LIMIT];
    int pcount = 0;
    plan_argv[pcount++] = "--stdlib-root";
    plan_argv[pcount++] = invocation->stdlib;
    plan_argv[pcount++] = "project";
    plan_argv[pcount++] = "--resolve-plan";
    plan_argv[pcount++] = plan_path;
    plan_argv[pcount++] = options->project_subcmd;
    for (int i = 3; i < argc && pcount < SDK_ARGUMENT_LIMIT - 1; i++) {
        const char* v = argv[i];
        if (strcmp(v, "--") == 0) break;
        if (strcmp(v, "--sandbox") == 0 || strncmp(v, "--allow=", 8) == 0 || strncmp(v, "--deny=", 7) == 0) {
            continue;
        }
        if (strcmp(v, "--allow") == 0 || strcmp(v, "--deny") == 0 ||
            strcmp(v, "--profile") == 0 || strcmp(v, "--timeout-ms") == 0 ||
            strcmp(v, "--include-dir") == 0 || strcmp(v, "--link") == 0 ||
            strcmp(v, "--library-dir") == 0 || strcmp(v, "--library") == 0) {
            i++;
            continue;
        }
        plan_argv[pcount++] = v;
    }
    if (strcmp(invocation->project_engine, invocation->engine) != 0) {
        (void)sdk_environment("WYSNA_SELFHOST_COMPILER", invocation->engine);
    }
    int plan_rc = sdk_child(invocation->project_engine, pcount, plan_argv, SDK_COMPILE_TIMEOUT_MS);
    char* plan_buf = NULL;
    size_t plan_len = 0;
    int read_ok = (plan_rc == 0) && sdk_read_file(plan_path, &plan_buf, &plan_len);
    sdk_remove_tree(plan_dir);
    if (plan_rc != 0) { free(plan_buf); return plan_rc; }
    if (!read_ok) { free(plan_buf); return sdk_error("W5001", "Failed reading resolved project plan", NULL); }

    const char *pkg_name = "", *target_entry = NULL, *target_output = NULL;
    SdkProjectTestPlan test_plan = {0};
    test_plan.root = test_plan.manifest = test_plan.manifest_sha = test_plan.lock_sha = test_plan.hash = "";
    test_plan.report_json = test_plan.report_junit = test_plan.json = "";
    test_plan.cases = calloc(SDK_TEST_CASE_LIMIT, sizeof(SdkProjectTest));
    if (!test_plan.cases) { free(plan_buf); return sdk_error("W5001", "Cannot allocate project test plan", NULL); }
    SdkOptions run_opts = *options;
    run_opts.output = NULL;

    char* cursor = plan_buf;
    while (*cursor) {
        char* line = cursor;
        char* nl = strchr(cursor, '\n');
        if (nl) { *nl = 0; cursor = nl + 1; }
        else { cursor += strlen(cursor); }
        size_t llen = strlen(line);
        if (llen > 0 && line[llen - 1] == '\r') line[--llen] = 0;
        if (llen == 0) continue;
        char* value = strchr(line, '=');
        if (value && strncmp(line, "DEP=", 4) != 0 && strncmp(line, "TEST=", 5) != 0 && !sdk_plan_decode(value + 1)) {
            free(test_plan.cases); free(plan_buf); return sdk_error("W5002", "Invalid project plan encoding", NULL);
        }
        if (strncmp(line, "NAME=", 5) == 0) pkg_name = line + 5;
        else if (strncmp(line, "ROOT=", 5) == 0) test_plan.root = line + 5;
        else if (strncmp(line, "MANIFEST=", 9) == 0) test_plan.manifest = line + 9;
        else if (strncmp(line, "MANIFEST_SHA=", 13) == 0) test_plan.manifest_sha = line + 13;
        else if (strncmp(line, "LOCK_SHA=", 9) == 0) test_plan.lock_sha = line + 9;
        else if (strncmp(line, "PLAN_HASH=", 10) == 0) test_plan.hash = line + 10;
        else if (strncmp(line, "PLAN_JSON=", 10) == 0) test_plan.json = line + 10;
        else if (strncmp(line, "LIST_ONLY=", 10) == 0) test_plan.list_only = strcmp(line + 10, "1") == 0;
        else if (strncmp(line, "REPORT_JSON=", 12) == 0) test_plan.report_json = line + 12;
        else if (strncmp(line, "REPORT_JUNIT=", 13) == 0) test_plan.report_junit = line + 13;
        else if (strncmp(line, "SRC_ROOT=", 9) == 0) run_opts.project_src_root = line + 9;
        else if (strncmp(line, "ENTRY=", 6) == 0) target_entry = line + 6;
        else if (strncmp(line, "OUTPUT=", 7) == 0) target_output = line + 7;
        else if (strncmp(line, "DEP=", 4) == 0 && run_opts.dep_count < SDK_LINK_LIMIT) {
            char* tab = strchr(line + 4, '\t');
            if (tab) {
                *tab = 0;
                if (!sdk_plan_decode(line + 4) || !sdk_plan_decode(tab + 1)) { free(test_plan.cases); free(plan_buf); return 1; }
                run_opts.dep_aliases[run_opts.dep_count] = line + 4;
                run_opts.dep_roots[run_opts.dep_count] = tab + 1;
                run_opts.dep_count++;
            }
        } else if (strncmp(line, "TEST=", 5) == 0) {
            char* first = strchr(line + 5, '\t');
            char* second = first ? strchr(first + 1, '\t') : NULL;
            char* third = second ? strchr(second + 1, '\t') : NULL;
            if (!first || !second || !third || test_plan.count == SDK_TEST_CASE_LIMIT) {
                free(test_plan.cases); free(plan_buf); return sdk_error("W5002", "Invalid or excessive test selection", NULL);
            }
            *first = *second = *third = 0;
            if (!sdk_plan_decode(line + 5) || !sdk_plan_decode(second + 1)) { free(test_plan.cases); free(plan_buf); return 1; }
            SdkProjectTest* record = &test_plan.cases[test_plan.count++];
            record->name = line + 5; record->entry = second + 1; record->sha = third + 1;
            if (!sdk_parse_timeout_ms(first + 1, &record->timeout)) { free(test_plan.cases); free(plan_buf); return 1; }
        }
    }

    int status = 0;
    if (strcmp(options->project_subcmd, "test") == 0) {
        test_plan.name = pkg_name;
        status = sdk_run_project_tests(invocation, &run_opts, &test_plan);
        free(test_plan.cases); free(plan_buf); return status;
    }
    free(test_plan.cases);

    if (!target_entry || !sdk_absolute(invocation->source, target_entry, 1) || !sdk_is_file(invocation->source)) {
        free(plan_buf);
        return sdk_error("W5001", "Project target entry is not an accessible regular file", target_entry ? target_entry : "");
    }
    if (strcmp(options->project_subcmd, "build") == 0) {
        if (!target_output || !sdk_absolute(invocation->output, target_output, 0)) {
            free(plan_buf);
            return sdk_error("W5002", "Invalid project build output path", target_output ? target_output : "");
        }
#ifdef _WIN32
        size_t osize = strlen(invocation->output);
        if (osize < 4 || _stricmp(invocation->output + osize - 4, ".exe") != 0) {
            if (osize + 4 >= SDK_PATH_CAP) { free(plan_buf); return sdk_error("W5002", "Output path is too long", NULL); }
            memcpy(invocation->output + osize, ".exe", 5);
        }
#endif
        if (!sdk_parent(invocation->parent, invocation->output) || !sdk_mkdir_p(invocation->parent) ||
            !sdk_temp(invocation->workspace, invocation->parent)) {
            free(plan_buf);
            return sdk_error("W5001", "Cannot create build staging directory beside output", invocation->output);
        }
        run_opts.output = invocation->output;
        status = sdk_compile_source(invocation, &run_opts, "--emit-c-mir", invocation->source, 1);
        if (!status) status = sdk_build(invocation, &run_opts);
        if (!status && !sdk_publish(invocation->product, invocation->output)) {
            status = sdk_error("W5001", "Cannot publish completed project binary", invocation->output);
        }
        if (!status) {
            if (options->project_json_v1) {
                printf("{\"schema_version\":\"wysna.tool-result/1\",\"command\":\"project.build\",\"status\":\"ok\",\"invocation_id\":\"native\",\"tool_exit_code\":0,\"process_exit_code\":0,\"execution\":null,\"toolchain\":{\"compiler\":\"" WYSNA_NATIVE_VERSION "\"},\"target\":{\"profile\":\"native-cli\",\"triple\":null},\"diagnostics\":[],\"artifacts\":[{\"kind\":\"native_executable\",\"media_type\":\"application/octet-stream\",\"path\":");
                sdk_print_json_quoted(invocation->output);
                puts("}],\"telemetry\":null}");
            } else {
                printf("[OK] Built %s -> %s\n", pkg_name, invocation->output);
            }
        }
        free(plan_buf);
        return status;
    }

    if (!sdk_temp(invocation->workspace, NULL)) {
        free(plan_buf);
        return sdk_error("W5001", "Cannot create a private staging directory", NULL);
    }
    status = sdk_compile_source(invocation, &run_opts, "--emit-c-mir", invocation->source, 1);
    if (!status) status = sdk_build(invocation, &run_opts);
    if (!status) {
        int arg_count = options->arguments ? argc - options->arguments : 0;
        status = sdk_child(invocation->product, arg_count,
                           options->arguments ? (const char**)(argv + options->arguments) : NULL, options->timeout);
    }
    free(plan_buf);
    return status;
}

#ifdef _WIN32
static int sdk_spawn_stdio_win32(const char* executable, int argc, const char* const* argv) {
    wchar_t* application = wysna_host_process_wide(executable);
    if (!application) return sdk_error("W5001", "Cannot start the native stdio server", executable);
    wchar_t* wide_argv[SDK_ARGUMENT_LIMIT + 5];
    size_t command_length = 1;
    for (int i = 0; i < argc; i++) {
        wchar_t* wide = wysna_host_process_wide(argv[i]);
        if (!wide) {
            for (int j = 0; j < i; j++) free(wide_argv[j]);
            free(application);
            return sdk_error("W5001", "Cannot encode stdio server arguments", executable);
        }
        wide_argv[i] = wysna_host_process_quote(wide);
        free(wide);
        command_length += wcslen(wide_argv[i]) + 1;
    }
    wchar_t* command_line = (wchar_t*)wysna_alloc_safe(command_length * sizeof(wchar_t));
    size_t pos = 0;
    for (int i = 0; i < argc; i++) {
        if (i) command_line[pos++] = L' ';
        size_t len = wcslen(wide_argv[i]);
        memcpy(command_line + pos, wide_argv[i], len * sizeof(wchar_t));
        pos += len;
        free(wide_argv[i]);
    }
    command_line[pos] = 0;
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    memset(&startup, 0, sizeof(startup));
    memset(&process, 0, sizeof(process));
    startup.cb = sizeof(startup);
    startup.dwFlags = STARTF_USESTDHANDLES;
    startup.hStdInput = GetStdHandle(STD_INPUT_HANDLE);
    startup.hStdOutput = GetStdHandle(STD_OUTPUT_HANDLE);
    startup.hStdError = GetStdHandle(STD_ERROR_HANDLE);
    BOOL started = CreateProcessW(application, command_line, NULL, NULL, TRUE, 0, NULL, NULL, &startup, &process);
    free(application);
    free(command_line);
    if (!started) return sdk_error("W5001", "Cannot start the native stdio server", executable);
    WaitForSingleObject(process.hProcess, INFINITE);
    DWORD exit_code = 1;
    GetExitCodeProcess(process.hProcess, &exit_code);
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return (int)exit_code;
}
#endif

static int sdk_execute(SdkInvocation* invocation, const SdkOptions* options, int argc, char** argv) {
    if (!sdk_component_paths(invocation)) return sdk_error("W5001", "Cannot locate the native compiler beside this SDK", NULL);
    if (strcmp(options->command, "project") == 0) {
        return sdk_execute_project(invocation, options, argc, argv);
    }
    if (strcmp(options->command, "lsp") == 0 || strcmp(options->command, "mcp") == 0) {
        const char* target_exe = strcmp(options->command, "mcp") == 0 ? invocation->mcp_engine : invocation->engine;
        if (strcmp(target_exe, invocation->engine) != 0) {
            (void)sdk_environment("WYSNA_SELFHOST_COMPILER", invocation->engine);
        }
        const char* engine_arguments[SDK_ARGUMENT_LIMIT + 4];
        engine_arguments[0] = target_exe;
        engine_arguments[1] = "--stdlib-root";
        engine_arguments[2] = invocation->stdlib;
        int out_count = 3;
        for (int i = 1; i < argc && out_count < SDK_ARGUMENT_LIMIT + 3; i++) {
            engine_arguments[out_count++] = argv[i];
        }
        engine_arguments[out_count] = NULL;
#ifdef _WIN32
        return sdk_spawn_stdio_win32(target_exe, out_count, engine_arguments);
#else
        execv(target_exe, (char* const*)engine_arguments);
        return sdk_error("W5001", "Cannot start the native stdio server", target_exe);
#endif
    }
    if (sdk_is_agent_passthrough(options->command) ||
        (strcmp(options->command, "check") == 0 && sdk_has_json_v1(argc, argv)) ||
        strcmp(options->command, "fmt") == 0) {
        const char* target_exe = strcmp(options->command, "packages") == 0 ? invocation->project_engine : invocation->engine;
        if (strcmp(target_exe, invocation->engine) != 0) {
            (void)sdk_environment("WYSNA_SELFHOST_COMPILER", invocation->engine);
        }
        const char* engine_arguments[SDK_ARGUMENT_LIMIT + 3];
        engine_arguments[0] = "--stdlib-root";
        engine_arguments[1] = invocation->stdlib;
        int out_count = 2;
        for (int i = 1; i < argc && out_count < SDK_ARGUMENT_LIMIT + 2; i++) {
            engine_arguments[out_count++] = argv[i];
        }
        return sdk_child(target_exe, out_count, engine_arguments, options->timeout);
    }
    int build = strcmp(options->command, "build") == 0;
    int run = strcmp(options->command, "run") == 0;
    int test = strcmp(options->command, "test") == 0;
    if (!sdk_absolute(invocation->source, options->source, 1)) return sdk_error("W5001", "Source is not an accessible regular file", options->source);
    if (test && sdk_is_directory(invocation->source)) {
        char** test_files = NULL;
        size_t test_count = 0;
        if (!sdk_list_wysna_tests(invocation->source, &test_files, &test_count)) {
            return sdk_error("W5001", "Cannot enumerate tests in directory", options->source);
        }
        if (test_count == 0) {
            free(test_files);
            return sdk_error("W5002", "No .wysna test files found in directory", options->source);
        }
        size_t passed = 0, failed = 0;
        for (size_t idx = 0; idx < test_count; idx++) {
            int rc = sdk_run_test_case(invocation, options, test_files[idx], test_files[idx], argc, argv);
            if (rc == 0) passed++;
            else failed++;
            free(test_files[idx]);
        }
        free(test_files);
        printf("Ran %zu test(s): %zu passed, %zu failed.\n", test_count, passed, failed);
        return failed == 0 ? 0 : 1;
    }
    if (!sdk_is_file(invocation->source)) return sdk_error("W5001", "Source is not an accessible regular file", options->source);
    if (test) {
        return sdk_run_test_case(invocation, options, invocation->source, options->source, argc, argv);
    }
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
        if (options->wasi) {
            size_t length = strlen(invocation->output);
            if (length < 5 || strcmp(invocation->output + length - 5, ".wasm") != 0) {
                if (length + 5 >= SDK_PATH_CAP) return sdk_error("W5002", "Output path is too long", NULL);
                memcpy(invocation->output + length, ".wasm", 6);
            }
        }
#ifdef _WIN32
        if (build && !options->wasi) {
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
    const char* mode = strcmp(options->command, "check") == 0 ? "--check-mir" : strcmp(options->command, "emit-mir") == 0 ? "--emit-mir" : "--emit-c-mir";
    int status = sdk_compile_source(invocation, options, mode, invocation->source, build || run);
    if (status) return status;
    if (build || run) {
        status = sdk_build(invocation, options); if (status) return status;
    }
    if (invocation->output[0]) {
        if (sdk_same_file(invocation->source, invocation->output)) return sdk_error("W5002", "Output became a source alias during compilation", NULL);
        if (!sdk_publish(build ? invocation->product : invocation->generated, invocation->output)) return sdk_error("W5001", "Cannot publish the completed output", invocation->output);
    }
    if (options->json_v1 && (build || strcmp(options->command, "emit-c") == 0)) {
        const char* kind = options->wasi ? "wasm_module" : build ? "native_executable" : "c99_source";
        const char* media = options->wasi ? "application/wasm" : build ? "application/octet-stream" : "text/x-c";
        printf("{\"schema_version\":\"wysna.tool-result/1\",\"command\":");
        sdk_print_json_quoted(options->command);
        printf(",\"status\":\"ok\",\"invocation_id\":\"native\",\"tool_exit_code\":0,\"process_exit_code\":0,\"execution\":null,\"toolchain\":{\"compiler\":\"" WYSNA_NATIVE_VERSION "\"},\"target\":{\"profile\":\"%s\",\"triple\":%s},\"diagnostics\":[],\"artifacts\":",
               options->wasi ? "wasi-command" : "native-cli", options->wasi ? "\"wasm32-wasip1\"" : "null");
        if (invocation->output[0]) {
            printf("[{\"kind\":");
            sdk_print_json_quoted(kind);
            printf(",\"media_type\":");
            sdk_print_json_quoted(media);
            printf(",\"path\":");
            sdk_print_json_quoted(invocation->output);
            printf("}]");
        } else {
            printf("[]");
        }
        puts(",\"telemetry\":null}");
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
    if (options.unsupported_target) {
        char message[512];
        snprintf(message, sizeof(message),
                 "Target '%s' is unavailable for this command in the Native SDK. Use wysna build --target=wasi for a WASI artifact, or the Seed distribution for browser builds and WASI project execution.",
                 options.unsupported_target);
        if (sdk_has_json_v1(argc, argv)) {
            printf("{\"schema_version\":\"wysna.tool-result/1\",\"command\":");
            sdk_print_json_quoted(options.command ? options.command : "build");
            printf(",\"status\":\"usage_error\",\"invocation_id\":\"native\",\"tool_exit_code\":2,\"process_exit_code\":2,\"execution\":null,\"toolchain\":{\"compiler\":\"" WYSNA_NATIVE_VERSION "\"},\"target\":{\"profile\":\"native-cli\",\"triple\":null},\"diagnostics\":[{\"code\":\"W2013\",\"stage\":\"target\",\"severity\":\"error\",\"message\":");
            sdk_print_json_quoted(message);
            puts(",\"primary_span\":null,\"related\":[],\"data\":{},\"fixes\":[]}],\"artifacts\":[],\"telemetry\":null}");
        } else {
            fprintf(stderr, "[W2013] %s\n", message);
        }
        wysna_runtime_cleanup();
        return 2;
    }
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
