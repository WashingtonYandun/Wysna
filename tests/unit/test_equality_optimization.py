"""Verify pointer fast path and structural equality performance optimization."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from common.Platform import compiler_command, executable_name
from common.Paths import get_repo_root

ROOT = Path(get_repo_root())


class EqualityOptimizationTests(unittest.TestCase):
    def test_structural_equality_pointer_fast_paths(self):
        compiler = compiler_command()
        with tempfile.TemporaryDirectory(prefix="wysna-eq-opt-") as tmpdir:
            temp = Path(tmpdir)
            main_c = temp / "test_eq.c"
            binary = temp / executable_name("test_eq")

            main_c.write_text(
                '#include <assert.h>\n'
                '#include <stdio.h>\n'
                '#include <string.h>\n'
                '#include "runtime/wysna_runtime.h"\n\n'
                'int main(int argc, char** argv) {\n'
                '    wysna_init_args(argc, argv);\n'
                '    /* 1. List pointer identity fast path */\n'
                '    WVal elems[3] = { wysna_int(10), wysna_int(20), wysna_int(30) };\n'
                '    WVal list1 = wysna_make_list(3, elems);\n'
                '    WVal list2 = wysna_clone(list1);\n'
                '    assert(wysna_as_list(list1) == wysna_as_list(list2));\n'
                '    assert(wysna_eq(list1, list2) == true);\n'
                '    wysna_release(list2);\n'
                '    wysna_release(list1);\n\n'
                '    /* 2. Tuple pointer identity fast path */\n'
                '    WVal t_elems[2] = { wysna_str("first"), wysna_str("second") };\n'
                '    WVal tup1 = wysna_make_tuple(2, t_elems);\n'
                '    WVal tup2 = wysna_clone(tup1);\n'
                '    assert(wysna_as_tuple(tup1) == wysna_as_tuple(tup2));\n'
                '    assert(wysna_eq(tup1, tup2) == true);\n'
                '    wysna_release(tup2);\n'
                '    wysna_release(tup1);\n\n'
                '    /* 3. Struct pointer identity fast path */\n'
                '    const char* keys[2] = { "x", "y" };\n'
                '    WVal vals[2] = { wysna_int(100), wysna_int(200) };\n'
                '    WVal st1 = wysna_make_struct("Point", 2, keys, vals);\n'
                '    WVal st2 = wysna_clone(st1);\n'
                '    assert(wysna_as_struct(st1) == wysna_as_struct(st2));\n'
                '    assert(wysna_eq(st1, st2) == true);\n'
                '    wysna_release(st2);\n'
                '    wysna_release(st1);\n\n'
                '    /* 4. Dict pointer identity fast path */\n'
                '    WVal dkeys[2] = { wysna_str("k1"), wysna_str("k2") };\n'
                '    WVal dvals[2] = { wysna_int(1), wysna_int(2) };\n'
                '    WVal d1 = wysna_make_dict(2, dkeys, dvals);\n'
                '    WVal d2 = wysna_clone(d1);\n'
                '    assert(wysna_as_dict(d1) == wysna_as_dict(d2));\n'
                '    assert(wysna_eq(d1, d2) == true);\n'
                '    wysna_release(d2);\n'
                '    wysna_release(d1);\n\n'
                '    /* 5. Nested collection with shared sub-buffers */\n'
                '    WVal sub1 = wysna_make_list(3, elems);\n'
                '    WVal nest1_elems[2] = { sub1, wysna_int(99) };\n'
                '    WVal nest2_elems[2] = { wysna_clone(sub1), wysna_int(99) };\n'
                '    WVal nest1 = wysna_make_list(2, nest1_elems);\n'
                '    WVal nest2 = wysna_make_list(2, nest2_elems);\n'
                '    assert(wysna_as_list(nest1) != wysna_as_list(nest2));\n'
                '    assert(wysna_as_list(wysna_as_list(nest1)->data[0]) == wysna_as_list(wysna_as_list(nest2)->data[0]));\n'
                '    assert(wysna_eq(nest1, nest2) == true);\n'
                '    wysna_release(nest1);\n'
                '    wysna_release(nest2);\n'
                '    wysna_release(sub1);\n'
                '    wysna_release(nest2_elems[0]);\n\n'
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
