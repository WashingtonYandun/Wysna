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
                '    assert(wysna_as_list(lst)->refcount == 1);\n'
                '    WVal lst2 = wysna_clone(lst);\n'
                '    assert(wysna_as_list(lst)->refcount == 2);\n'
                '    wysna_release(lst2);\n'
                '    assert(wysna_as_list(lst)->refcount == 1);\n'
                '    wysna_release(lst);\n\n'
                '    /* 2. Shared Tuple fast path */\n'
                '    WVal tup_elems[2] = { wysna_float(1.5), wysna_bool(true) };\n'
                '    WVal tup = wysna_make_tuple(2, tup_elems);\n'
                '    assert(wysna_as_tuple(tup)->refcount == 1);\n'
                '    WVal tup2 = wysna_clone(tup);\n'
                '    assert(wysna_as_tuple(tup)->refcount == 2);\n'
                '    wysna_release(tup2);\n'
                '    assert(wysna_as_tuple(tup)->refcount == 1);\n'
                '    wysna_release(tup);\n\n'
                '    /* 3. Shared Struct fast path */\n'
                '    const char* keys[1] = { "field" };\n'
                '    WVal vals[1] = { wysna_int(99) };\n'
                '    WVal st = wysna_make_struct("MyStruct", 1, keys, vals);\n'
                '    assert(wysna_as_struct(st)->refcount == 1);\n'
                '    WVal st2 = wysna_clone(st);\n'
                '    assert(wysna_as_struct(st)->refcount == 2);\n'
                '    wysna_release(st2);\n'
                '    assert(wysna_as_struct(st)->refcount == 1);\n'
                '    wysna_release(st);\n\n'
                '    /* 4. Shared Dict fast path */\n'
                '    WVal dkeys[1] = { wysna_copy_str("key") };\n'
                '    WVal dvals[1] = { wysna_int(100) };\n'
                '    WVal dict = wysna_make_dict(1, dkeys, dvals);\n'
                '    wysna_release(dkeys[0]);\n'
                '    assert(wysna_as_dict(dict)->refcount == 1);\n'
                '    WVal dict2 = wysna_clone(dict);\n'
                '    assert(wysna_as_dict(dict)->refcount == 2);\n'
                '    wysna_release(dict2);\n'
                '    assert(wysna_as_dict(dict)->refcount == 1);\n'
                '    wysna_release(dict);\n\n'
                '    /* 5. Shared Object fast path */\n'
                '    WVal obj = wysna_make_object("MyClass", 1, keys, vals);\n'
                '    assert(wysna_as_struct(obj)->control->strong == 1);\n'
                '    WVal obj2 = wysna_clone(obj);\n'
                '    assert(wysna_as_struct(obj)->control->strong == 2);\n'
                '    wysna_release(obj2);\n'
                '    assert(wysna_as_struct(obj)->control->strong == 1);\n'
                '    wysna_release(obj);\n\n'
                '    /* 6. Function without environment fast path */\n'
                '    WVal fn_val = wysna_function_new(NULL, wysna_unit());\n'
                '    assert(wysna_as_function(fn_val)->refcount == 1);\n'
                '    WVal fn_val2 = wysna_clone(fn_val);\n'
                '    assert(wysna_as_function(fn_val)->refcount == 2);\n'
                '    wysna_release(fn_val2);\n'
                '    assert(wysna_as_function(fn_val)->refcount == 1);\n'
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

    def test_release_graph_uses_zero_temporary_mallocs_under_depth_64(self):
        compiler = compiler_command()
        with tempfile.TemporaryDirectory(prefix="wysna-release-zero-malloc-") as tmpdir:
            temp = Path(tmpdir)
            main_c = temp / "test_zero_malloc.c"
            binary = temp / executable_name("test_zero_malloc")

            main_c.write_text(
                '#include <assert.h>\n'
                '#include <stdlib.h>\n'
                'static size_t g_malloc_calls = 0;\n'
                'static void* counted_malloc(size_t sz) { g_malloc_calls++; return malloc(sz); }\n'
                '#define malloc counted_malloc\n'
                '#include "runtime/wysna_runtime.h"\n'
                '#undef malloc\n\n'
                'int main(int argc, char** argv) {\n'
                '    wysna_init_args(argc, argv);\n'
                '    const char* s_keys[2] = {"a", "b"};\n'
                '    WVal s_vals[2] = {wysna_int(1), wysna_copy_str("long_string_value")};\n'
                '    WVal st = wysna_make_struct("LeafStruct", 2, s_keys, s_vals);\n'
                '    wysna_release(s_vals[1]);\n'
                '    WVal inner_tup_elems[2] = {wysna_int(7), st};\n'
                '    WVal tup = wysna_make_tuple(2, inner_tup_elems);\n'
                '    wysna_release(st);\n'
                '    WVal dk = wysna_copy_str("nested_dict_key");\n'
                '    WVal dv = wysna_enum_payload("BoxEnum", "Wrap", tup);\n'
                '    WVal dict = wysna_make_dict(1, &dk, &dv);\n'
                '    wysna_release(dk);\n'
                '    wysna_release(dv);\n'
                '    WVal root = dict;\n'
                '    for (int i = 0; i < 40; i++) {\n'
                '        WVal next = wysna_make_list(1, &root);\n'
                '        wysna_release(root);\n'
                '        root = next;\n'
                '    }\n'
                '    size_t before_release = g_malloc_calls;\n'
                '    wysna_release(root);\n'
                '    assert(g_malloc_calls == before_release);\n'
                '    assert(wysna_g_alloc_count == wysna_g_free_count && wysna_g_live_objects == 0);\n'
                '    wysna_runtime_cleanup();\n'
                '    return 0;\n'
                '}\n',
                encoding="utf-8"
            )

            math_flags = [] if os.name == "nt" else ["-lm"]
            cmd = compiler + [
                "-std=c99",
                "-O2",
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
