"""Verify that the Wysna runtime supports multi-object modular C linking with shared context."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from common.Platform import compiler_command, executable_name
from common.Paths import get_repo_root

ROOT = Path(get_repo_root())


class RuntimeModularLinkingTests(unittest.TestCase):
    def test_multi_translation_unit_linking_shares_runtime_context(self):
        compiler = compiler_command()
        with tempfile.TemporaryDirectory(prefix="wysna-modular-link-") as tmpdir:
            temp = Path(tmpdir)
            unit1_c = temp / "unit1.c"
            main_c = temp / "main.c"
            binary = temp / executable_name("modular_test")

            unit1_c.write_text(
                '#define WYSNA_SHARED_RUNTIME\n'
                '#include "runtime/wysna_runtime.h"\n'
                'void helper_record_alloc(void) {\n'
                '    wysna_mem_on_alloc();\n'
                '}\n',
                encoding="utf-8"
            )

            main_c.write_text(
                '#define WYSNA_SHARED_RUNTIME\n'
                '#include "runtime/wysna_runtime.h"\n'
                'extern void helper_record_alloc(void);\n'
                'int main(int argc, char** argv) {\n'
                '    wysna_init_args(argc, argv);\n'
                '    if (wysna_runtime_context.alloc_count != 0) return 1;\n'
                '    helper_record_alloc();\n'
                '    if (wysna_runtime_context.alloc_count != 1) return 2;\n'
                '    wysna_runtime_cleanup();\n'
                '    return 0;\n'
                '}\n',
                encoding="utf-8"
            )

            runtime_c = ROOT / "runtime" / "wysna_runtime.c"
            math_flags = [] if os.name == "nt" else ["-lm"]

            # Compile both units and the shared runtime unit, then link
            cmd = compiler + [
                "-std=c99",
                f"-I{ROOT}",
                str(unit1_c),
                str(main_c),
                str(runtime_c),
            ] + math_flags + ["-o", str(binary)]

            build = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=60)
            self.assertEqual(build.returncode, 0, f"Compilation failed: {build.stderr}")

            run = subprocess.run([str(binary)], cwd=ROOT, capture_output=True, text=True, timeout=10)
            self.assertEqual(run.returncode, 0, f"Execution failed: code={run.returncode}, err={run.stderr}")


if __name__ == "__main__":
    unittest.main()
