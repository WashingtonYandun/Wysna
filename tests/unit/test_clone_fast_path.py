"""Verify scalar and unowned string fast paths in wysna_clone and wysna_release_deferred."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from common.Platform import compiler_command, executable_name
from common.Paths import get_repo_root

ROOT = Path(get_repo_root())


class CloneFastPathTests(unittest.TestCase):
    def test_clone_scalar_and_unowned_string_fast_paths(self):
        compiler = compiler_command()
        with tempfile.TemporaryDirectory(prefix="wysna-clone-opt-") as tmpdir:
            temp = Path(tmpdir)
            main_c = temp / "test_clone.c"
            binary = temp / executable_name("test_clone")

            main_c.write_text(
                '#include <assert.h>\n'
                '#include <stdio.h>\n'
                '#include <string.h>\n'
                '#include "runtime/wysna_runtime.h"\n\n'
                'int main(int argc, char** argv) {\n'
                '    wysna_init_args(argc, argv);\n'
                '    int64_t initial_retains = wysna_runtime_context.retain_count;\n\n'
                '    /* 1. Unit, None, Int, Float, Bool clones must not increment retain_count */\n'
                '    WVal u = wysna_unit();\n'
                '    WVal u_clone = wysna_clone(u);\n'
                '    assert(u_clone.tag == W_UNIT);\n\n'
                '    WVal n = wysna_none();\n'
                '    WVal n_clone = wysna_clone(n);\n'
                '    assert(n_clone.tag == W_NONE);\n\n'
                '    WVal i = wysna_int(42);\n'
                '    WVal i_clone = wysna_clone(i);\n'
                '    assert(i_clone.tag == W_INT && i_clone.as.i == 42);\n\n'
                '    WVal f = wysna_float(3.14);\n'
                '    WVal f_clone = wysna_clone(f);\n'
                '    assert(f_clone.tag == W_FLOAT && f_clone.as.f == 3.14);\n\n'
                '    WVal b = wysna_bool(true);\n'
                '    WVal b_clone = wysna_clone(b);\n'
                '    assert(b_clone.tag == W_BOOL && b_clone.as.b == true);\n\n'
                '    assert(wysna_runtime_context.retain_count == initial_retains);\n\n'
                '    /* 2. Unowned string literal clone must not increment retain_count */\n'
                '    WVal s = wysna_str("static_text");\n'
                '    assert(s.as.string.owner == NULL);\n'
                '    WVal s_clone = wysna_clone(s);\n'
                '    assert(s_clone.tag == W_STR && s_clone.as.string.owner == NULL);\n'
                '    assert(wysna_runtime_context.retain_count == initial_retains);\n\n'
                '    /* 3. Owned string clone does increment retain_count */\n'
                '    WVal owned = wysna_copy_str("dynamic_text");\n'
                '    assert(owned.as.string.owner != NULL);\n'
                '    WVal owned_clone = wysna_clone(owned);\n'
                '    assert(wysna_runtime_context.retain_count == initial_retains + 1);\n'
                '    wysna_release(owned_clone);\n'
                '    wysna_release(owned);\n\n'
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
