"""Native B03 control-block lifetime assertions under sanitizers."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

CONTRACT = r'''
#include "runtime/wysna_runtime.h"
#include <assert.h>
int main(void) {
    WVal empty_weak = wysna_empty_weak();
    assert(wysna_is_none(wysna_builtin_upgrade(empty_weak)));
    WVal empty_copy = wysna_clone(empty_weak);
    wysna_release(empty_weak); wysna_release(empty_copy);
    assert(wysna_g_live_objects == 0);
    WVal object = wysna_make_object("Node", 0, NULL, NULL);
    WVal weak = wysna_builtin_weak(object);
    WVal copy = wysna_clone(weak);
    WWeakControl* control = wysna_as_weak(weak);
    assert(control->strong == 1 && control->weak == 3);
    WVal upgraded = wysna_builtin_upgrade(weak);
    assert(control->strong == 2 && wysna_eq(object, upgraded));
    wysna_release(object);
    assert(control->strong == 1);
    wysna_release(upgraded);
    assert(control->object == NULL && control->strong == 0 && control->weak == 2);
    assert(wysna_is_none(wysna_builtin_upgrade(weak)));
    assert(wysna_eq(weak, copy));
    wysna_release(copy);
    assert(control->weak == 1);
    WVal other = wysna_make_object("Node", 0, NULL, NULL);
    WVal other_weak = wysna_builtin_weak(other);
    wysna_release(other);
    assert(!wysna_eq(weak, other_weak));
    wysna_release(weak); wysna_release(other_weak);
    assert(wysna_g_live_objects == 0);
    const char* fields[1] = {"link"};
    WVal empty[1] = {wysna_none()};
    object = wysna_make_object("Self", 1, fields, empty);
    weak = wysna_builtin_weak(object);
    wysna_assign(wysna_member_ref(&object, "link"), wysna_clone(weak));
    wysna_release(weak);
    wysna_release(object);
    assert(wysna_g_live_objects == 0);
    WVal parent = wysna_make_object("Parent", 1, fields, empty);
    weak = wysna_builtin_weak(parent);
    WVal values[1] = {weak};
    WVal child = wysna_make_object("Child", 1, fields, values);
    wysna_assign(wysna_member_ref(&parent, "link"), wysna_clone(child));
    wysna_release(child);
    wysna_release(parent);
    assert(wysna_as_weak(weak)->object == NULL);
    wysna_release(weak);
    assert(wysna_g_live_objects == 0);
    wysna_runtime_cleanup();
    return 0;
}
'''


def run_control_block_tests():
    root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='wysna-weak-control-') as directory:
        source, binary = Path(directory) / 'test.c', Path(directory) / 'test'
        source.write_text(CONTRACT)
        built = subprocess.run([os.environ.get('CC', 'cc'), '-std=c99', '-g',
                                '-fsanitize=address,undefined', '-fno-omit-frame-pointer',
                                '-I' + str(root), str(source), '-o', str(binary)],
                               capture_output=True, text=True, timeout=60)
        assert built.returncode == 0, built.stderr
        env = dict(os.environ, WYSNA_MEM_CHECK='1')
        env['ASAN_OPTIONS'] = 'detect_leaks=1' if sys.platform.startswith('linux') else 'detect_leaks=0'
        result = subprocess.run([str(binary)], env=env, capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
    print('  [PASS] Weak strong/weak counts, upgrade, expiration, identity and destruction order')


if __name__ == '__main__':
    run_control_block_tests()
