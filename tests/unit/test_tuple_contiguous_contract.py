"""Verify contiguous memory layout and leak-free destruction for Wysna tuple buffers."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from common.Platform import compiler_command, executable_name
from common.Paths import get_repo_root

ROOT = Path(get_repo_root())


class TupleContiguousContractTests(unittest.TestCase):
    def test_tuple_buffer_contiguous_layout_and_cleanup(self):
        compiler = compiler_command()
        with tempfile.TemporaryDirectory(prefix="wysna-tuple-layout-") as tmpdir:
            temp = Path(tmpdir)
            main_c = temp / "test_tuple.c"
            binary = temp / executable_name("test_tuple")

            main_c.write_text(
                '#include "runtime/wysna_runtime.h"\n'
                '#include <assert.h>\n'
                '#include <stdint.h>\n'
                'int main(int argc, char** argv) {\n'
                '    wysna_init_args(argc, argv);\n'
                '    int64_t initial_allocs = wysna_runtime_context.alloc_count;\n'
                '    int64_t initial_frees = wysna_runtime_context.free_count;\n'
                '    \n'
                '    // Test 3-element tuple\n'
                '    WVal elems[3] = { wysna_int(10), wysna_str("wysna"), wysna_bool(true) };\n'
                '    WVal tup = wysna_make_tuple(3, elems);\n'
                '    WTupleBuf* tb = wysna_as_tuple(tup);\n'
                '    assert(wysna_tag(tup) == W_TUPLE);\n'
                '    assert(tb != NULL);\n'
                '    assert(tb->len == 3);\n'
                '    // Assert contiguous layout: items pointer immediately follows WTupleBuf struct\n'
                '    assert((char*)tb->items == (char*)tb + sizeof(WTupleBuf));\n'
                '    assert(wysna_as_int(tb->items[0]) == 10);\n'
                '    assert(wysna_as_bool(tb->items[2]) == true);\n'
                '    \n'
                '    // Clean up\n'
                '    wysna_release(elems[1]); // release local string copy\n'
                '    wysna_release(tup);\n'
                '    \n'
                '    // Check telemetry balance\n'
                '    assert(wysna_runtime_context.alloc_count > initial_allocs);\n'
                '    assert(wysna_runtime_context.alloc_count == wysna_runtime_context.free_count);\n'
                '    \n'
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
            run = subprocess.run([str(binary)], cwd=ROOT, capture_output=True, text=True, timeout=10, env=env)
            self.assertEqual(run.returncode, 0, f"Execution failed: code={run.returncode}, err={run.stderr}\nout={run.stdout}")


if __name__ == "__main__":
    unittest.main()
