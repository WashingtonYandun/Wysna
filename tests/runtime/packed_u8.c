#include "runtime/wysna_runtime.h"
#include <assert.h>

int main(void) {
    const int32_t count = 1000000;
    WVal* source = malloc((size_t)count * sizeof(*source));
    assert(source);
    for (int32_t index = 0; index < count; index++) source[index] = wysna_int_inline(index % 256);
    WVal bytes = wysna_make_u8_list(count, source);
    WVal integers = wysna_make_list(count, source);
    free(source);
    WListBuf* byte_list = wysna_as_list(bytes);
    WListBuf* int_list = wysna_as_list(integers);
    size_t byte_payload = wysna_list_element_size(byte_list) * (size_t)byte_list->cap;
    size_t int_payload = wysna_list_element_size(int_list) * (size_t)int_list->cap;
    assert(byte_payload == 1000000 && int_payload == 8000000);
    assert(wysna_eq(bytes, integers));
    WVal alias = wysna_clone(bytes);
    wysna_index_store_at(&bytes, wysna_int_inline(0), wysna_int_inline(255), __FILE__, __LINE__, 1);
    assert(wysna_as_u8(wysna_list_load(wysna_as_list(alias), 0)) == 0);
    assert(wysna_as_u8(wysna_list_load(wysna_as_list(bytes), 0)) == 255);
    WVal slice = wysna_list_slice(wysna_as_list(bytes), 250, 10);
    assert(wysna_list_is_u8(wysna_as_list(slice)) && wysna_as_u8(wysna_list_load(wysna_as_list(slice), 6)) == 0);
    WVal snapshot = wysna_worker_snapshot(bytes);
    assert(wysna_list_is_u8(wysna_as_list(snapshot)) && wysna_eq(snapshot, bytes));
    printf("%zu %zu\n", byte_payload, int_payload);
    wysna_release(snapshot);
    wysna_release(slice);
    wysna_release(alias);
    wysna_release(bytes);
    wysna_release(integers);
    wysna_runtime_cleanup();
}
