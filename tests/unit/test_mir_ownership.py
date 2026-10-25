"""Independent lifetime, rejection, and allocator oracles for ownership optimization."""
from copy import deepcopy
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

from error.Error import WysnaError
from mir.Nodes import Operation, dump
from mir.Ownership import optimize_parameter_reads
from tests.unit.test_mir import lower

ROOT = Path(__file__).resolve().parents[2]


def parameter_clones(function):
    return [operation for block in function.blocks for operation in block.operations
            if operation.opcode == 'clone' and operation.args[0] in function.params]


def test_parameter_reads_preserve_consuming_escapes_and_other_owners():
    source = '''let global = "global"
fn read(text: str) -> int { len(text) + ord(text[0]) + len(text) }
fn escape(text: str) -> str { text }
fn result(text: str) -> str ! str { Ok(text) }
fn container(text: str) -> list[str] { [text, text] }
fn local(text: str) -> int {
    let saved = text
    len(saved)
}
fn global_read() -> int { len(global) }
fn list_read(values: list[int]) -> int { len(values) }
print(read("hello"))'''
    module, interner = lower(source, optimize=False)
    original = deepcopy(module)
    optimize_parameter_reads(module, interner)
    functions = {function.name: function for function in module.functions}
    assert not parameter_clones(functions['wysna_fn_read'])
    assert not parameter_clones(functions['wysna_fn_container'])
    for name in ('escape', 'result', 'local', 'list_read'):
        assert parameter_clones(functions['wysna_fn_' + name]), name
    original_global = next(function for function in original.functions if function.name == 'wysna_fn_global_read')
    assert functions['wysna_fn_global_read'] == original_global
    # Applying the pass again must not change the verified representation.
    once = dump(module)
    optimize_parameter_reads(module, interner)
    assert dump(module) == once
    # A mutable temporary is not interchangeable with its original parameter.
    mutable, arena = lower('fn read(text: str) -> int { len(text) }\nprint(read("hello"))', optimize=False)
    function = mutable.functions[0]
    copied = parameter_clones(function)[0]
    block = next(block for block in function.blocks if copied in block.operations)
    block.operations.insert(block.operations.index(copied) + 1,
                            Operation('const', copied.result, data={'value': 'changed'}))
    optimize_parameter_reads(mutable, arena)
    assert parameter_clones(function)


def test_parameter_optimization_rejects_invalid_input_before_rewriting():
    original, interner = lower('fn read(text: str) -> int { len(text) }\nprint(read("hello"))', optimize=False)
    for mode in ('move', 'overwrite', 'return'):
        module = deepcopy(original)
        function = module.functions[0]
        parameter = function.params[0]
        operation = parameter_clones(function)[0]
        block = next(block for block in function.blocks if operation in block.operations)
        if mode == 'move':
            operation.moves = (0,)
        elif mode == 'overwrite':
            block.operations.append(Operation('store', parameter, (operation.result,), moves=(0,)))
        else:
            block.terminator.value = parameter
            block.terminator.kind = 'return'
            block.terminator.targets = ()
        try:
            optimize_parameter_reads(module, interner)
        except WysnaError as error:
            assert error.stage == 'mir-verification' and error.code == 'W9999', error
        else:
            raise AssertionError('Malformed ownership accepted: ' + mode)


def test_result_release_uses_no_temporary_heap_for_leaves_or_shared_boxes():
    source = r'''#ifndef _WIN32
#define _POSIX_C_SOURCE 200809L
#endif
#include <stdlib.h>
#include <assert.h>
static size_t allocation_calls;
static void* counted_malloc(size_t size) { allocation_calls++; return malloc(size); }
#define malloc counted_malloc
#include "runtime/wysna_runtime.h"
#undef malloc

static void release_without_allocation(WVal value) {
    size_t before = allocation_calls;
    wysna_release(value);
    assert(allocation_calls == before);
}

int main(void) {
    WVal leaves[] = {wysna_unit(), wysna_none(), wysna_int(7), wysna_float(NAN), wysna_bool(true),
                     wysna_pointer(NULL), wysna_str("static"), wysna_copy_str("owned")};
    for (size_t index = 0; index < sizeof(leaves) / sizeof(leaves[0]); index++) {
        WVal result = index % 2 ? wysna_err(leaves[index]) : wysna_ok(leaves[index]);
        WVal shared = wysna_clone(result);
        release_without_allocation(result);
        assert(shared.as.box->refcount == 1);
        release_without_allocation(shared);
    }
    WVal compound = wysna_ok(wysna_make_list(1, (WVal[]){wysna_int(9)}));
    WVal alias = wysna_clone(compound);
    release_without_allocation(compound);
    assert(alias.as.box->val.as.list->data[0].as.i == 9);
    wysna_release(alias);
    WVal boxed_string = wysna_ok(wysna_copy_str("child"));
    WVal container = wysna_make_list(1, &boxed_string);
    wysna_release(boxed_string);
    wysna_release(container);
    WVal deep = wysna_copy_str("bottom");
    for (int index = 0; index < 100000; index++) deep = index % 2 ? wysna_ok(deep) : wysna_err(deep);
    wysna_release(deep);
    assert(wysna_g_alloc_count == wysna_g_free_count && wysna_g_live_objects == 0);
    wysna_runtime_cleanup();
    return 0;
}
'''
    with tempfile.TemporaryDirectory(prefix='wysna-result-release-') as temporary:
        path = Path(temporary) / 'contract.c'
        binary = Path(temporary) / 'contract'
        path.write_text(source)
        command = shlex.split(os.environ.get('CC', 'cc')) + [
            '-std=c99', '-O1', '-g', '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
            '-I' + str(ROOT), str(path), '-o', str(binary)]
        try:
            subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
        except subprocess.CalledProcessError as error:
            raise AssertionError(
                'failed to compile result-release sanitizer contract:\n'
                + (error.stdout or '')
                + (error.stderr or '')
            ) from error
        environment = dict(os.environ, WYSNA_MEM_CHECK='1', UBSAN_OPTIONS='halt_on_error=1')
        environment['ASAN_OPTIONS'] = 'detect_leaks=' + ('1' if sys.platform.startswith('linux') else '0')
        result = subprocess.run([str(binary)], env=environment, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0 and not result.stdout and not result.stderr, result


def test_parameter_lifetimes_across_engines():
    from main import ensure_selfhost_compiler_binary
    from tests.engine_harness import verify
    path = ROOT / 'tests/runtime/mir_parameter_lifetime.wysna'
    with tempfile.TemporaryDirectory(prefix='wysna-parameter-lifetime-') as temporary:
        ok, detail = verify(path.read_text(), str(path.relative_to(ROOT)),
                            ensure_selfhost_compiler_binary(str(ROOT)), temporary,
                            expected=path.with_suffix('.expected').read_text(), strict=True,
                            sanitize=os.environ.get('WYSNA_SANITIZE') == '1')
        assert ok, detail


def run_ownership_optimization_tests():
    test_parameter_reads_preserve_consuming_escapes_and_other_owners()
    test_parameter_optimization_rejects_invalid_input_before_rewriting()
    test_result_release_uses_no_temporary_heap_for_leaves_or_shared_boxes()
    test_parameter_lifetimes_across_engines()
