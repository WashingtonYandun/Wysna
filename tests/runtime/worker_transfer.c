#define W_STRING_METRICS
#include "runtime/wysna_runtime.h"
#include <assert.h>

static WListBuf* original_leaf;
static WStructBuf* original_record;
static WStringLiteral literal = {0};
static const char label[] = "worker á界\0 bytes";
static int32_t arrived = 0;

static WVal inspect_copy(WVal environment, int32_t argc, const WVal* argv) {
    assert(argc == 1);
    assert(wysna_worker_depth > 0);
    WStructBuf* record = wysna_as_struct(argv[0]);
    assert(record != original_record);
    WListBuf* leaf = wysna_as_list(record->vals[0]);
    assert(leaf != original_leaf);
    assert(wysna_as_list(record->vals[1]) == leaf);
    assert(wysna_as_list(environment) == leaf);
    WVal text = wysna_literal_str_n(&literal, label, sizeof(label) - 1);
    assert(wysna_str_owner(text) != &literal.buffer);
    assert(wysna_string_length(text) == 16);
    WVal values[2] = {wysna_clone(environment), text};
    WVal result = wysna_make_tuple(2, values);
    wysna_release(values[0]);
    wysna_release(text);
    return result;
}

static WVal trapped(WVal environment, int32_t argc, const WVal* argv) {
    (void)environment; (void)argc; (void)argv;
    wysna_runtime_fail("W4003", "worker trap exits without self-join");
}

static WVal identity_result(WVal environment, int32_t argc, const WVal* argv) {
    (void)environment; (void)argc; (void)argv;
    return wysna_make_object("Identity", 0, NULL, NULL);
}

static WVal delayed(WVal environment, int32_t argc, const WVal* argv) {
    (void)environment; (void)argc; (void)argv;
    wysna_host_sleep_ms_internal(10000);
    return wysna_unit();
}

static WVal simultaneous_trap(WVal environment, int32_t argc, const WVal* argv) {
    (void)environment; (void)argc; (void)argv;
    wysna_atomic_inc_i32(&arrived);
    while (wysna_atomic_load_i32(&arrived) < 4) {}
    wysna_runtime_fail("W4003", "one complete worker diagnostic");
}

static WVal simultaneous_conversion(WVal environment, int32_t argc, const WVal* argv) {
    (void)environment; (void)argc; (void)argv;
    wysna_atomic_inc_i32(&arrived);
    while (wysna_atomic_load_i32(&arrived) < 4) {}
    return wysna_builtin_int(wysna_str("invalid"));
}

int main(int argc, char** argv) {
    wysna_init_args(argc, argv);
    if (argc > 1) {
        if (strcmp(argv[1], "simultaneous-traps") == 0 || strcmp(argv[1], "simultaneous-conversions") == 0) {
            WVal inputs[4] = {wysna_unit(), wysna_unit(), wysna_unit(), wysna_unit()};
            WVal values = wysna_make_list(4, inputs);
            WVal fn = wysna_function_new(strcmp(argv[1], "simultaneous-traps") == 0 ? simultaneous_trap : simultaneous_conversion, wysna_unit());
            (void)wysna_parallel_map(values, fn);
            assert(false);
        }
        if (strcmp(argv[1], "main-trap") == 0) {
            WVal fn = wysna_function_new(delayed, wysna_unit());
            (void)wysna_async_spawn_handle(fn);
            wysna_runtime_fail("W4003", "main trap does not wait for workers");
        }
        if (strcmp(argv[1], "trap") == 0) {
            WVal fn = wysna_function_new(trapped, wysna_unit());
            int64_t handle = wysna_async_spawn_handle(fn);
            wysna_release(fn);
            WVal out = wysna_spawn_clone_result(handle);
            wysna_release(out);
            assert(false);
        }
        WVal rejected;
        if (strcmp(argv[1], "result") == 0) {
            WVal fn = wysna_function_new(identity_result, wysna_unit());
            (void)wysna_spawn_clone_result(wysna_async_spawn_handle(fn));
            assert(false);
        }
        if (strcmp(argv[1], "depth") == 0) {
            rejected = wysna_unit();
            for (int i = 0; i < 260; i++) {
                WVal next = wysna_make_list(1, &rejected);
                wysna_release(rejected);
                rejected = next;
            }
        } else if (strcmp(argv[1], "weak") == 0) {
            WVal object = wysna_make_object("Identity", 0, NULL, NULL);
            rejected = wysna_builtin_weak(object);
        } else if (strcmp(argv[1], "nested") == 0) {
            WVal pointer = wysna_pointer(NULL);
            rejected = wysna_make_list(1, &pointer);
        } else if (strcmp(argv[1], "object") == 0) rejected = wysna_make_object("Identity", 0, NULL, NULL);
        else rejected = wysna_pointer(NULL);
        WVal out = wysna_worker_snapshot(rejected);
        wysna_release(out);
        assert(false);
    }
    WVal leaf = wysna_make_list(0, NULL);
    WVal dag_nodes[100];
    WVal repeated[2] = {leaf, leaf};
    for (int i = 0; i < 100; i++) dag_nodes[i] = wysna_make_tuple(2, repeated);
    WVal dag = wysna_make_list(100, dag_nodes);
    for (int i = 0; i < 100; i++) wysna_release(dag_nodes[i]);
    WVal copied_dag = wysna_worker_snapshot(dag);
    WVal copied_leaf = wysna_as_tuple(wysna_as_list(copied_dag)->data[0])->items[0];
    assert(wysna_as_list(copied_leaf) != wysna_as_list(leaf));
    for (int i = 0; i < 100; i++) {
        WTupleBuf* tuple = wysna_as_tuple(wysna_as_list(copied_dag)->data[i]);
        assert(tuple->items[0].bits == copied_leaf.bits && tuple->items[1].bits == copied_leaf.bits);
    }
    wysna_release(copied_dag);
    wysna_release(dag);
    original_leaf = wysna_as_list(leaf);
    const char* fields[2] = {"first", "alias"};
    WVal members[2] = {leaf, leaf};
    WVal record = wysna_make_struct("Record", 2, fields, members);
    original_record = wysna_as_struct(record);
    WVal inputs[32];
    for (int i = 0; i < 32; i++) inputs[i] = record;
    WVal values = wysna_make_list(32, inputs);
    WVal callback = wysna_function_new(inspect_copy, wysna_clone(leaf));
    for (int repeat = 0; repeat < 12; repeat++) {
        WVal mapped = wysna_parallel_map(values, callback);
        assert(wysna_as_list(mapped)->len == 32);
        WTupleBuf* first = wysna_as_tuple(wysna_as_list(mapped)->data[0]);
        assert(wysna_as_list(first->items[0]) != original_leaf);
        assert(wysna_string_length(first->items[1]) == 16);
        wysna_release(mapped);
    }
    assert(literal.buffer.refcount == 0);
    assert(wysna_worker_depth == 0);
    assert(wysna_atomic_add_i64(&wysna_runtime_context.active_workers, 0) == 0);
    wysna_release(callback);
    wysna_release(values);
    wysna_release(record);
    wysna_release(leaf);
    wysna_runtime_cleanup();
    puts("worker graphs isolated");
    return 0;
}
