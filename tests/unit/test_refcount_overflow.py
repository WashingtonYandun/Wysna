"""Reject reference-count retains before signed overflow can occur."""
import os
from pathlib import Path
import subprocess
import tempfile


CONTRACT = r'''
#include "runtime/wysna_runtime.h"
#include <stdint.h>
#include <string.h>

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    if (strcmp(argv[1], "list") == 0) {
        WVal value = wysna_make_list(0, NULL);
        value.as.list->refcount = INT32_MAX;
        (void)wysna_clone(value);
        return 3;
    }
    if (strcmp(argv[1], "struct") == 0) {
        WVal value = wysna_make_struct("Record", 0, NULL, NULL);
        value.as.st->refcount = INT32_MAX;
        (void)wysna_clone(value);
        return 3;
    }
    if (strcmp(argv[1], "object") == 0) {
        WVal value = wysna_make_object("Node", 0, NULL, NULL);
        value.as.st->control->strong = INT32_MAX;
        (void)wysna_clone(value);
        return 3;
    }
    if (strcmp(argv[1], "weak-clone") == 0) {
        WVal object = wysna_make_object("Node", 0, NULL, NULL);
        WVal value = wysna_builtin_weak(object);
        value.as.weak->weak = INT32_MAX;
        (void)wysna_clone(value);
        return 3;
    }
    if (strcmp(argv[1], "weak-create") == 0) {
        WVal object = wysna_make_object("Node", 0, NULL, NULL);
        object.as.st->control->weak = INT32_MAX;
        (void)wysna_builtin_weak(object);
        return 3;
    }
    return 2;
}
'''


def test_refcount_overflow_is_rejected_before_increment():
    root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix="wysna-refcount-overflow-") as directory:
        source = Path(directory) / "test.c"
        binary = Path(directory) / ("test.exe" if os.name == "nt" else "test")
        source.write_text(CONTRACT, encoding="utf-8")
        compiler = os.environ.get("CC", "cc")
        flags = [] if os.name == "nt" else ["-fsanitize=undefined", "-fno-sanitize-recover=all"]
        built = subprocess.run([compiler, "-std=c99", *flags, "-I" + str(root), str(source), "-o", str(binary)],
                               capture_output=True, text=True, timeout=60)
        assert built.returncode == 0, built.stderr

        for value_kind in ("list", "struct", "object", "weak-clone", "weak-create"):
            environment = dict(os.environ, UBSAN_OPTIONS="halt_on_error=1")
            result = subprocess.run([str(binary), value_kind], capture_output=True, text=True, env=environment,
                                    timeout=10)
            assert result.returncode == 1, (value_kind, result.returncode, result.stderr)
            assert "[W4003]" in result.stderr, (value_kind, result.stderr)
            assert "reference count overflow" in result.stderr, (value_kind, result.stderr)
