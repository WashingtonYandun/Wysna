"""Check opt-in telemetry and native byte accounting without assuming a C ABI."""
import contextlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from runtime.Environment import create_global_environment
from runtime.Interpreter import Interpreter
from runtime.Values import WysnaStr, WysnaInt, WysnaList, WysnaDict, WysnaStructInstance, cow_telemetry


NATIVE_CONTRACT = r'''
#include "runtime/wysna_runtime.h"
#include <assert.h>
int main(void) {
    WVal args[2] = {wysna_str("__COW_CHECKPOINT__:ordinary"), wysna_str("second")};
    wysna_builtin_eprint(2, args);
    wysna_builtin_eprint(1, args);
    WVal items[5] = {wysna_int(1), wysna_int(2), wysna_int(3), wysna_int(4), wysna_int(5)};
    WVal list = wysna_make_list(5, items);
    for (int i = 0; i < 4; i++) wysna_release(wysna_list_pop(&list));
    WVal alias = wysna_clone(list);
    wysna_cow_reset_stats();
    wysna_list_detach(&list);
    assert(wysna_g_cow_detach_list == 1 && wysna_g_cow_copied_slots == 1);
    assert(wysna_g_cow_copied_bytes == sizeof(WVal));
    assert(wysna_g_cow_allocated_bytes == sizeof(WListBuf) + 5 * sizeof(WVal));
    assert(wysna_g_cow_alloc_count == 2);
    wysna_list_detach(&list);
    assert(wysna_g_cow_detach_list == 1);
    wysna_release(alias); wysna_release(list);
    WVal dict = wysna_make_dict(0, NULL, NULL);
    for (int i = 0; i < 5; i++) wysna_assign(wysna_index_ref(&dict, wysna_int(i)), wysna_int(i));
    alias = wysna_clone(dict);
    wysna_cow_reset_stats();
    wysna_dict_detach(&dict);
    assert(wysna_g_cow_copied_slots == 10 && wysna_g_cow_detach_dict == 1);
    assert(wysna_g_cow_copied_bytes == 10 * sizeof(WVal) + dict.as.dict->bucket_count * sizeof(int32_t));
    assert(wysna_g_cow_allocated_bytes == sizeof(WDictBuf) + 16 * sizeof(WVal) + dict.as.dict->bucket_count * sizeof(int32_t));
    assert(wysna_g_cow_alloc_count == 4);
    wysna_release(alias); wysna_release(dict);
    WVal record = wysna_make_struct("Empty", 0, NULL, NULL);
    alias = wysna_clone(record);
    wysna_cow_reset_stats();
    wysna_struct_detach(&record);
    assert(wysna_g_cow_copied_slots == 0 && wysna_g_cow_copied_bytes == 0);
    assert(wysna_g_cow_allocated_bytes == sizeof(WStructBuf) + sizeof(char*) + sizeof(WVal));
    assert(wysna_g_cow_alloc_count == 1);
    wysna_release(alias); wysna_release(record);
    WVal rows[3] = {wysna_make_list(2, items), wysna_make_list(2, items), wysna_make_list(2, items)};
    WVal matrix = wysna_make_list(3, rows);
    for (int i = 0; i < 3; i++) wysna_release(rows[i]);
    alias = wysna_clone(matrix);
    wysna_cow_reset_stats();
    wysna_assign(wysna_index_ref(wysna_index_ref(&matrix, wysna_int(0)), wysna_int(0)), wysna_int(99));
    assert(wysna_g_cow_detach_list == 2 && wysna_g_cow_copied_slots == 5);
    assert(matrix.as.list->data[0].as.list != alias.as.list->data[0].as.list);
    assert(matrix.as.list->data[1].as.list == alias.as.list->data[1].as.list);
    assert(matrix.as.list->data[2].as.list == alias.as.list->data[2].as.list);
    wysna_assign(wysna_index_ref(wysna_index_ref(&matrix, wysna_int(0)), wysna_int(1)), wysna_int(88));
    assert(wysna_g_cow_detach_list == 2 && wysna_g_cow_copied_slots == 5);
    wysna_release(alias); wysna_release(matrix);
    wysna_cow_reset_stats();
    assert(wysna_g_cow_allocated_bytes == 0 && wysna_g_cow_copied_bytes == 0);
    wysna_runtime_cleanup();
    return 0;
}
'''


def run_metrics_contract():
    previous = os.environ.get('WYSNA_COW_METRICS')
    marker = '__COW_CHECKPOINT__:ordinary'
    try:
        for enabled in [False, True]:
            os.environ['WYSNA_COW_METRICS'] = '1' if enabled else '0'
            output = io.StringIO()
            cow_telemetry.reset()
            with contextlib.redirect_stderr(output):
                eprint = create_global_environment().lookup('eprint').call_fn
                eprint([WysnaStr(marker), WysnaStr('second')])
                eprint([WysnaStr(marker)])
            lines = output.getvalue().splitlines()
            assert lines[0] == marker + ' second'
            assert len(lines) == 2
            assert lines[1].startswith('[WYSNA_COW_CHECKPOINT:ordinary]') if enabled else lines[1] == marker
        cow_telemetry.detach_list = 7
        Interpreter()
        assert cow_telemetry.detach_list == 7, 'Creating an import interpreter must not reset process counters'
    finally:
        cow_telemetry.reset()
        if previous is None:
            os.environ.pop('WYSNA_COW_METRICS', None)
        else:
            os.environ['WYSNA_COW_METRICS'] = previous
    for value in [WysnaList(elements=[WysnaInt(1)]), WysnaDict(pairs={"key": WysnaInt(1)}),
                  WysnaStructInstance("Record", fields={"value": WysnaInt(1)})]:
        alias = value.clone_value()
        assert value.storage.refcount == 2
        del alias
        assert value.storage.refcount == 1, "Dead aliases must release their buffer reference"
        cow_telemetry.reset()
        value.ensure_unique()
        assert cow_telemetry.copied_slots == 0
    root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='wysna-cow-contract-') as directory:
        source, binary = Path(directory) / 'contract.c', Path(directory) / 'contract'
        source.write_text(NATIVE_CONTRACT)
        subprocess.run([os.environ.get('CC', 'cc'), '-std=c99', '-fsanitize=address,undefined',
                        '-fno-omit-frame-pointer', '-I' + str(root), str(source), '-o', str(binary)],
                       check=True, capture_output=True, text=True, timeout=60)
        for enabled in [False, True]:
            env = dict(os.environ, WYSNA_COW_METRICS='1' if enabled else '0', WYSNA_MEM_CHECK='1')
            env['ASAN_OPTIONS'] = 'detect_leaks=1' if sys.platform.startswith('linux') else 'detect_leaks=0'
            result = subprocess.run([str(binary)], env=env, capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr
            lines = result.stderr.splitlines()
            assert lines[0] == marker + ' second'
            assert lines[1].startswith('[WYSNA_COW_CHECKPOINT:ordinary]') if enabled else lines[1] == marker
            assert len(lines) == (3 if enabled else 2), result.stderr
    print('  [PASS] CoW opt-in output, import lifetime and native ABI/capacity accounting')


if __name__ == '__main__':
    run_metrics_contract()
