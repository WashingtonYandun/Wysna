/* Scoped cancellation includes descendants that created their own process groups. */
#ifndef WYSNA_PROCESS_TREE_H
#define WYSNA_PROCESS_TREE_H
#if !defined(_WIN32) && !defined(__wasi__)
#include <dirent.h>
#include <signal.h>
#include <unistd.h>
#ifdef __APPLE__
#include <dlfcn.h>
#endif

static int wysna_process_grow(pid_t** values, int* capacity, int required) {
    if (required <= *capacity) return 1;
    if (required < 0 || required > INT32_MAX / (4 * (int)sizeof(pid_t))) return 0;
    int size = required < 128 ? 128 : required * 2;
    pid_t* grown = realloc(*values, sizeof(pid_t) * (size_t)size);
    if (!grown) return 0;
    *values = grown; *capacity = size; return 1;
}
static int wysna_process_children(pid_t parent, pid_t** output, int* capacity) {
#ifdef __APPLE__
    void* library = dlopen("/usr/lib/libproc.dylib", RTLD_LAZY);
    if (!library) return -1;
    int (*children)(int, void*, int) = (int (*)(int, void*, int))dlsym(library, "proc_listchildpids");
    if (!children) { dlclose(library); return -1; }
    int required = children(parent, NULL, 0);
    if (required < 0 || !wysna_process_grow(output, capacity, required + 32)) { dlclose(library); return -1; }
    int count = children(parent, *output, *capacity * (int)sizeof(pid_t));
    dlclose(library);
    return count;
#else
    char directory[128];
    snprintf(directory, sizeof(directory), "/proc/%ld/task", (long)parent);
    DIR* tasks = opendir(directory);
    if (!tasks) return errno == ENOENT ? 0 : -1;
    int count = 0;
    struct dirent* task;
    while ((task = readdir(tasks)) != NULL) {
        if (task->d_name[0] < '0' || task->d_name[0] > '9') continue;
        char path[512];
        int length = snprintf(path, sizeof(path), "%s/%s/children", directory, task->d_name);
        if (length < 0 || (size_t)length >= sizeof(path)) { count = -1; break; }
        FILE* children = fopen(path, "r");
        if (!children) continue;
        long child;
        while (fscanf(children, "%ld", &child) == 1) {
            if (!wysna_process_grow(output, capacity, count + 1)) { count = -1; break; }
            (*output)[count++] = (pid_t)child;
        }
        fclose(children);
        if (count < 0) break;
    }
    closedir(tasks);
    return count;
#endif
}

static void wysna_process_terminate_tree(pid_t root) {
    pid_t* processes = NULL; pid_t* children = NULL;
    int capacity = 0, child_capacity = 0, count = 1;
    if (!wysna_process_grow(&processes, &capacity, 1)) { (void)kill(-root, SIGKILL); (void)kill(root, SIGKILL); return; }
    processes[0] = root;
    (void)kill(root, SIGSTOP);
    for (int index = 0; index < count; index++) {
        int found = wysna_process_children(processes[index], &children, &child_capacity);
        if (found < 0) continue;
        for (int child = 0; child < found; child++) {
            if (children[child] <= 0) continue;
            /* A stopped parent cannot reap/reuse this child PID while we collect it. */
            if (kill(children[child], SIGSTOP) != 0) continue;
            if (wysna_process_grow(&processes, &capacity, count + 1)) processes[count++] = children[child];
            else { (void)kill(-children[child], SIGKILL); (void)kill(children[child], SIGKILL); }
        }
    }
    for (int index = count; index > 0; index--) (void)kill(processes[index - 1], SIGKILL);
    (void)kill(-root, SIGKILL);
    free(children); free(processes);
}
#endif
#endif
