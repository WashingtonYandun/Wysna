"""Verify shared collection and leaf fast paths in wysna_release under ARC."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from common.Platform import compiler_command, executable_name
from common.Paths import get_repo_root

ROOT = Path(get_repo_root())


class ReleaseFastPathTests(unittest.TestCase):
    def test_release_shared_containers_and_leaf_fast_paths(self):
        compiler = compiler_command()
        with tempfile.TemporaryDirectory(prefix="wysna-release-opt-") as tmpdir:
            temp = Path(tmpdir)
            main_c = temp / "test_release.c"
            binary = temp / executable_name("test_release")

            main_c.write_text(
                '#include <assert.h>\n'
                '#include <stdio.h>\n'
                '#include <string.h>\n'
                '#include "runtime/wysna_runtime.h"\n\n'
                'int main(int argc, char** argv) {\n'
                '    wysna_init_args(argc, argv);\n\n'
                '    /* 1. Shared List fast path */\n'
                '    WVal elems[2] = { wysna_int(10), wysna_int(20) };\n'
                '    WVal lst = wysna_make_list(2, elems);\n'
                '    assert(lst.as.list->refcount == 1);\n'
                '    WVal lst2 = wysna_clone(lst);\n'
                '    assert(lst.as.list->refcount == 2);\n'
                '    wysna_release(lst2);\n'
                '    assert(lst.as.list->refcount == 1);\n'
                '    wysna_release(lst);\n\n'
                '    /* 2. Shared Tuple fast path */\n'
                '    WVal tup_elems[2] = { wysna_float(1.5), wysna_bool(true) };\n'
                '    WVal tup = wysna_make_tuple(2, tup_elems);\n'
                '    assert(tup.as.tuple->refcount == 1);\n'
                '    WVal tup2 = wysna_clone(tup);\n'
                '    assert(tup.as.tuple->refcount == 2);\n'
                '    wysna_release(tup2);\n'
                '    assert(tup.as.tuple->refcount == 1);\n'
                '    wysna_release(tup);\n\n'
                '    /* 3. Shared Struct fast path */\n'
                '    const char* keys[1] = { "field" };\n'
                '    WVal vals[1] = { wysna_int(99) };\n'
                '    WVal st = wysna_make_struct("MyStruct", 1, keys, vals);\n'
                '    assert(st.as.st->refcount == 1);\n'
                '    WVal st2 = wysna_clone(st);\n'
                '    assert(st.as.st->refcount == 2);\n'
                '    wysna_release(st2);\n'
                '    assert(st.as.st->refcount == 1);\n'
                '    wysna_release(st);\n\n'
                '    /* 4. Shared Dict fast path */\n'
                '    WVal dkeys[1] = { wysna_copy_str("key") };\n'
                '    WVal dvals[1] = { wysna_int(100) };\n'
                '    WVal dict = wysna_make_dict(1, dkeys, dvals);\n'
                '    wysna_release(dkeys[0]);\n'
                '    assert(dict.as.dict->refcount == 1);\n'
                '    WVal dict2 = wysna_clone(dict);\n'
                '    assert(dict.as.dict->refcount == 2);\n'
                '    wysna_release(dict2);\n'
                '    assert(dict.as.dict->refcount == 1);\n'
                '    wysna_release(dict);\n\n'
                '    /* 5. Shared Object fast path */\n'
                '    WVal obj = wysna_make_object("MyClass", 1, keys, vals);\n'
                '    assert(obj.as.st->control->strong == 1);\n'
                '    WVal obj2 = wysna_clone(obj);\n'
                '    assert(obj.as.st->control->strong == 2);\n'
                '    wysna_release(obj2);\n'
                '    assert(obj.as.st->control->strong == 1);\n'
                '    wysna_release(obj);\n\n'
                '    /* 6. Function without environment fast path */\n'
                '    WVal fn_val = wysna_function_new(NULL, wysna_unit());\n'
                '    assert(fn_val.as.function->refcount == 1);\n'
                '    WVal fn_val2 = wysna_clone(fn_val);\n'
                '    assert(fn_val.as.function->refcount == 2);\n'
                '    wysna_release(fn_val2);\n'
                '    assert(fn_val.as.function->refcount == 1);\n'
                '    wysna_release(fn_val);\n\n'
                '    /* Verify zero memory leak and retain/release balance */\n'
                '    wysna_runtime_cleanup();\n'
                '    return 0;\n'
                '}\n',
                encoding="utf-8"
            )

            math_flags = [] if os.name == "nt" else ["-lm"]
            cmd = compiler + [
                "-std=c99",
                "-fsanitize=address,undefined",
                f"-I{ROOT}",
                str(main_c),
            ] + math_flags + ["-o", str(binary)]

            build = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(build.returncode, 0, f"Compilation failed: {build.stderr}")

            env = dict(os.environ, WYSNA_MEM_CHECK="1")
            run = subprocess.run([str(binary)], cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, f"Execution failed: code={run.returncode}, err={run.stderr}")


if __name__ == "__main__":
    unittest.main()
