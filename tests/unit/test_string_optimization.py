"""Verify empty string concatenation and copy optimization in Wysna runtime."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from common.Platform import compiler_command, executable_name
from common.Paths import get_repo_root

ROOT = Path(get_repo_root())


class StringOptimizationTests(unittest.TestCase):
    def test_empty_string_concatenation_and_copy_no_alloc(self):
        compiler = compiler_command()
        with tempfile.TemporaryDirectory(prefix="wysna-str-opt-") as tmpdir:
            temp = Path(tmpdir)
            main_c = temp / "test_str.c"
            binary = temp / executable_name("test_str")

            main_c.write_text(
                '#include "runtime/wysna_runtime.h"\n'
                '#include <assert.h>\n'
                '#include <stdint.h>\n'
                'int main(int argc, char** argv) {\n'
                '    wysna_init_args(argc, argv);\n'
                '    int64_t initial_allocs = wysna_runtime_context.alloc_count;\n'
                '    \n'
                '    // 1. wysna_copy_str with empty or NULL data must not allocate on heap\n'
                '    WVal empty1 = wysna_copy_str("");\n'
                '    assert(empty1.tag == W_STR);\n'
                '    assert(empty1.as.string.length == 0);\n'
                '    assert(empty1.as.string.owner == NULL);\n'
                '    assert(wysna_runtime_context.alloc_count == initial_allocs);\n'
                '    \n'
                '    // 2. wysna_add(empty, s) must return retained s without allocating new buffer\n'
                '    WVal hello = wysna_copy_str("hello");\n'
                '    int64_t allocs_after_hello = wysna_runtime_context.alloc_count;\n'
                '    assert(allocs_after_hello > initial_allocs);\n'
                '    \n'
                '    WVal res1 = wysna_add(empty1, hello);\n'
                '    assert(res1.tag == W_STR);\n'
                '    assert(res1.as.string.length == 5);\n'
                '    assert(strcmp(res1.as.string.data, "hello") == 0);\n'
                '    assert(wysna_runtime_context.alloc_count == allocs_after_hello);\n'
                '    \n'
                '    // 3. wysna_add(s, empty) must return retained s without allocating new buffer\n'
                '    WVal res2 = wysna_add(hello, empty1);\n'
                '    assert(res2.tag == W_STR);\n'
                '    assert(res2.as.string.length == 5);\n'
                '    assert(strcmp(res2.as.string.data, "hello") == 0);\n'
                '    assert(wysna_runtime_context.alloc_count == allocs_after_hello);\n'
                '    \n'
                '    // 4. wysna_add(empty, empty) must return empty string without allocating new buffer\n'
                '    WVal res3 = wysna_add(empty1, empty1);\n'
                '    assert(res3.tag == W_STR);\n'
                '    assert(res3.as.string.length == 0);\n'
                '    assert(wysna_runtime_context.alloc_count == allocs_after_hello);\n'
                '    \n'
                '    // 5. wysna_builtin_slice with full range must clone without new allocation\n'
                '    WVal sliced = wysna_builtin_slice(hello, wysna_int(0), wysna_int(5));\n'
                '    assert(sliced.tag == W_STR);\n'
                '    assert(sliced.as.string.length == 5);\n'
                '    assert(sliced.as.string.data == hello.as.string.data);\n'
                '    assert(wysna_runtime_context.alloc_count == allocs_after_hello);\n'
                '    \n'
                '    // 6. wysna_builtin_join_strings with single element must clone without new allocation\n'
                '    WVal parts_arr[1] = { hello };\n'
                '    WVal list1 = wysna_make_list(1, parts_arr);\n'
                '    int64_t allocs_with_list = wysna_runtime_context.alloc_count;\n'
                '    WVal joined1 = wysna_builtin_join_strings(list1, empty1);\n'
                '    assert(joined1.tag == W_STR);\n'
                '    assert(joined1.as.string.length == 5);\n'
                '    assert(joined1.as.string.data == hello.as.string.data);\n'
                '    assert(wysna_runtime_context.alloc_count == allocs_with_list);\n'
                '    \n'
                '    // Clean up\n'
                '    wysna_release(sliced);\n'
                '    wysna_release(joined1);\n'
                '    wysna_release(list1);\n'
                '    wysna_release(empty1);\n'
                '    wysna_release(res1);\n'
                '    wysna_release(res2);\n'
                '    wysna_release(res3);\n'
                '    wysna_release(hello);\n'
                '    \n'
                '    // Assert perfect zero-leak memory balance\n'
                '    assert(wysna_runtime_context.alloc_count == wysna_runtime_context.free_count);\n'
                '    wysna_runtime_cleanup();\n'
                '    return 0;\n'
                '}\n',
                encoding='utf-8'
            )

            import os
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
            run = subprocess.run([str(binary)], cwd=ROOT, capture_output=True, text=True, timeout=10, env=env)
            self.assertEqual(run.returncode, 0, f"Execution failed: code={run.returncode}, err={run.stderr}\nout={run.stdout}")
