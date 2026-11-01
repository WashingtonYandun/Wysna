"""Verify string conversion and printing fast paths under ASan/UBSan."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from common.Platform import compiler_command, executable_name
from common.Paths import get_repo_root

ROOT = Path(get_repo_root())


class StrOptimizationTests(unittest.TestCase):
    def test_str_and_print_fast_paths(self):
        compiler = compiler_command()
        with tempfile.TemporaryDirectory(prefix="wysna-str-opt-") as tmpdir:
            temp = Path(tmpdir)
            main_c = temp / "test_str.c"
            binary = temp / executable_name("test_str")

            main_c.write_text(
                '#include <assert.h>\n'
                '#include <stdio.h>\n'
                '#include <string.h>\n'
                '#include "runtime/wysna_runtime.h"\n\n'
                'int main(int argc, char** argv) {\n'
                '    wysna_init_args(argc, argv);\n\n'
                '    /* 1. Boolean fast path (unowned literal, owner == NULL) */\n'
                '    WVal s_true = wysna_builtin_str(wysna_bool(true));\n'
                '    assert(s_true.tag == W_STR);\n'
                '    assert(s_true.as.string.owner == NULL);\n'
                '    assert(s_true.as.string.length == 4);\n'
                '    assert(strcmp(s_true.as.string.data, "true") == 0);\n'
                '    wysna_release(s_true);\n\n'
                '    WVal s_false = wysna_builtin_str(wysna_bool(false));\n'
                '    assert(s_false.tag == W_STR);\n'
                '    assert(s_false.as.string.owner == NULL);\n'
                '    assert(s_false.as.string.length == 5);\n'
                '    assert(strcmp(s_false.as.string.data, "false") == 0);\n'
                '    wysna_release(s_false);\n\n'
                '    /* 2. None and Unit fast path (unowned literal) */\n'
                '    WVal s_none = wysna_builtin_str(wysna_none());\n'
                '    assert(s_none.tag == W_STR);\n'
                '    assert(s_none.as.string.owner == NULL);\n'
                '    assert(strcmp(s_none.as.string.data, "none") == 0);\n'
                '    wysna_release(s_none);\n\n'
                '    WVal s_unit = wysna_builtin_str(wysna_unit());\n'
                '    assert(s_unit.tag == W_STR);\n'
                '    assert(s_unit.as.string.owner == NULL);\n'
                '    assert(strcmp(s_unit.as.string.data, "unit") == 0);\n'
                '    wysna_release(s_unit);\n\n'
                '    /* 3. Small integer fast path (0..9, static ASCII table) */\n'
                '    for (int64_t d = 0; d <= 9; d++) {\n'
                '        WVal s_digit = wysna_builtin_str(wysna_int(d));\n'
                '        assert(s_digit.tag == W_STR);\n'
                '        assert(s_digit.as.string.owner == NULL);\n'
                '        assert(s_digit.as.string.length == 1);\n'
                '        assert(s_digit.as.string.data[0] == (char)(\'0\' + d));\n'
                '        wysna_release(s_digit);\n'
                '    }\n\n'
                '    /* 4. Multi-digit integer fast path (direct buffer copy) */\n'
                '    WVal s_int = wysna_builtin_str(wysna_int(123456789));\n'
                '    assert(s_int.tag == W_STR);\n'
                '    assert(s_int.as.string.owner != NULL);\n'
                '    assert(strcmp(s_int.as.string.data, "123456789") == 0);\n'
                '    wysna_release(s_int);\n\n'
                '    WVal s_neg = wysna_builtin_str(wysna_int(-987654321));\n'
                '    assert(s_neg.tag == W_STR);\n'
                '    assert(s_neg.as.string.owner != NULL);\n'
                '    assert(strcmp(s_neg.as.string.data, "-987654321") == 0);\n'
                '    wysna_release(s_neg);\n\n'
                '    /* 5. String clone fast path */\n'
                '    WVal orig = wysna_str("already a string");\n'
                '    WVal s_str = wysna_builtin_str(orig);\n'
                '    assert(s_str.tag == W_STR);\n'
                '    assert(s_str.as.string.data == orig.as.string.data);\n'
                '    wysna_release(orig);\n'
                '    wysna_release(s_str);\n\n'
                '    /* 5b. Copy str single ASCII vs multi-byte */\n'
                '    WVal c_ascii = wysna_copy_str("z");\n'
                '    assert(c_ascii.tag == W_STR);\n'
                '    assert(c_ascii.as.string.owner == NULL);\n'
                '    assert(c_ascii.as.string.length == 1);\n'
                '    assert(c_ascii.as.string.data[0] == \'z\');\n'
                '    wysna_release(c_ascii);\n\n'
                '    WVal c_multi = wysna_copy_str("xyz");\n'
                '    assert(c_multi.tag == W_STR);\n'
                '    assert(c_multi.as.string.owner != NULL);\n'
                '    assert(c_multi.as.string.length == 3);\n'
                '    assert(strcmp(c_multi.as.string.data, "xyz") == 0);\n'
                '    wysna_release(c_multi);\n\n'
                '    /* 6. Builtin print and eprint scalar fast paths */\n'
                '    WVal print_args[5] = {\n'
                '        wysna_int(42),\n'
                '        wysna_bool(true),\n'
                '        wysna_none(),\n'
                '        wysna_unit(),\n'
                '        wysna_str("test_msg")\n'
                '    };\n'
                '    wysna_builtin_print(5, print_args);\n'
                '    wysna_builtin_eprint(5, print_args);\n'
                '    for (int i = 0; i < 5; i++) wysna_release(print_args[i]);\n\n'
                '    wysna_runtime_cleanup();\n'
                '    return 0;\n'
                '}\n'
            )

            cmd = (
                compiler
                + [
                    "-I" + str(ROOT),
                    "-fsanitize=address,undefined",
                    "-fno-sanitize-recover=all",
                    str(main_c),
                    "-o",
                    str(binary),
                ]
            )
            compile_proc = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(compile_proc.returncode, 0, compile_proc.stderr)

            env = dict(os.environ)
            env["WYSNA_MEM_CHECK"] = "1"
            run_proc = subprocess.run(
                [str(binary)],
                capture_output=True,
                text=True,
                env=env,
                timeout=15,
            )
            self.assertEqual(run_proc.returncode, 0, run_proc.stderr)
            self.assertIn("42 true none unit test_msg", run_proc.stdout)
            self.assertIn("42 true none unit test_msg", run_proc.stderr)
            self.assertNotIn("Sanitizer", run_proc.stderr)
            self.assertNotIn("Leak", run_proc.stderr)

    def test_slice_and_read_file_fast_paths(self):
        compiler = compiler_command()
        with tempfile.TemporaryDirectory(prefix="wysna-slice-opt-") as tmpdir:
            temp = Path(tmpdir)
            main_c = temp / "test_slice.c"
            binary = temp / executable_name("test_slice")
            empty_txt = temp / "empty.txt"
            empty_txt.write_text("", encoding="utf-8")
            small_txt = temp / "small.txt"
            small_txt.write_text("1234567890", encoding="utf-8")

            main_c.write_text(
                '#include <assert.h>\n'
                '#include <stdio.h>\n'
                '#include <string.h>\n'
                '#include "runtime/wysna_runtime.h"\n\n'
                'int main(int argc, char** argv) {\n'
                '    wysna_init_args(argc, argv);\n\n'
                '    /* 1. Slicing single ASCII char returns unowned literal */\n'
                '    WVal s = wysna_str("abcdef");\n'
                '    WVal sub1 = wysna_builtin_slice(s, wysna_int(2), wysna_int(3));\n'
                '    assert(sub1.tag == W_STR);\n'
                '    assert(sub1.as.string.owner == NULL);\n'
                '    assert(sub1.as.string.length == 1);\n'
                '    assert(sub1.as.string.data[0] == \'c\');\n'
                '    wysna_release(sub1);\n\n'
                '    /* 2. Slicing 0 chars returns unowned empty string */\n'
                '    WVal sub0 = wysna_builtin_slice(s, wysna_int(2), wysna_int(2));\n'
                '    assert(sub0.tag == W_STR);\n'
                '    assert(sub0.as.string.owner == NULL);\n'
                '    assert(sub0.as.string.length == 0);\n'
                '    wysna_release(sub0);\n\n'
                '    /* 3. Slicing multiple chars returns owned string */\n'
                '    WVal sub_multi = wysna_builtin_slice(s, wysna_int(1), wysna_int(4));\n'
                '    assert(sub_multi.tag == W_STR);\n'
                '    assert(sub_multi.as.string.owner != NULL);\n'
                '    assert(strcmp(sub_multi.as.string.data, "bcd") == 0);\n'
                '    wysna_release(sub_multi);\n'
                '    wysna_release(s);\n\n'
                '    /* 4. Reading empty file returns unowned string with 0 allocations */\n'
                '    WVal p_empty = wysna_str("' + str(empty_txt).replace('\\', '/') + '");\n'
                '    WVal res_empty = wysna_builtin_read_file(p_empty);\n'
                '    assert(res_empty.tag == W_OK);\n'
                '    WVal str_empty = res_empty.as.box->val;\n'
                '    assert(str_empty.tag == W_STR);\n'
                '    assert(str_empty.as.string.owner == NULL);\n'
                '    assert(str_empty.as.string.length == 0);\n'
                '    wysna_release(p_empty);\n'
                '    wysna_release(res_empty);\n\n'
                '    /* 5. Reading small file shrinks buffer and succeeds cleanly */\n'
                '    WVal p_small = wysna_str("' + str(small_txt).replace('\\', '/') + '");\n'
                '    WVal res_small = wysna_builtin_read_file(p_small);\n'
                '    assert(res_small.tag == W_OK);\n'
                '    WVal str_small = res_small.as.box->val;\n'
                '    assert(str_small.tag == W_STR);\n'
                '    assert(str_small.as.string.owner != NULL);\n'
                '    assert(strcmp(str_small.as.string.data, "1234567890") == 0);\n'
                '    wysna_release(p_small);\n'
                '    wysna_release(res_small);\n\n'
                '    printf("SLICE_AND_FILE_OPT_OK\\n");\n'
                '    wysna_runtime_cleanup();\n'
                '    return 0;\n'
                '}\n'
            )

            cmd = (
                compiler
                + [
                    "-I" + str(ROOT),
                    "-fsanitize=address,undefined",
                    "-fno-sanitize-recover=all",
                    str(main_c),
                    "-o",
                    str(binary),
                ]
            )
            compile_proc = subprocess.run(cmd, capture_output=True, text=True)
            self.assertEqual(compile_proc.returncode, 0, compile_proc.stderr)

            env = dict(os.environ)
            env["WYSNA_MEM_CHECK"] = "1"
            run_proc = subprocess.run(
                [str(binary)],
                capture_output=True,
                text=True,
                env=env,
                timeout=15,
            )
            self.assertEqual(run_proc.returncode, 0, run_proc.stderr)
            self.assertIn("SLICE_AND_FILE_OPT_OK", run_proc.stdout)
            self.assertNotIn("Sanitizer", run_proc.stderr)
            self.assertNotIn("Leak", run_proc.stderr)


if __name__ == "__main__":
    unittest.main()
