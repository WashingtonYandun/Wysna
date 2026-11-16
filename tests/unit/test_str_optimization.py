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
                '    /* 1. Boolean fast path (inline 0..6B SSO, owner == NULL) */\n'
                '    WVal s_true = wysna_builtin_str(wysna_bool(true));\n'
                '    assert(wysna_tag(s_true) == W_STR);\n'
                '    assert(wysna_str_owner(s_true) == NULL);\n'
                '    assert(wysna_str_len(s_true) == 4);\n'
                '    assert(strcmp(wysna_str_bytes(s_true, (char[7]){0}, NULL), "true") == 0);\n'
                '    wysna_release(s_true);\n\n'
                '    WVal s_false = wysna_builtin_str(wysna_bool(false));\n'
                '    assert(wysna_tag(s_false) == W_STR);\n'
                '    assert(wysna_str_owner(s_false) == NULL);\n'
                '    assert(wysna_str_len(s_false) == 5);\n'
                '    assert(strcmp(wysna_str_bytes(s_false, (char[7]){0}, NULL), "false") == 0);\n'
                '    wysna_release(s_false);\n\n'
                '    /* 2. None and Unit fast path (inline SSO) */\n'
                '    WVal s_none = wysna_builtin_str(wysna_none());\n'
                '    assert(wysna_tag(s_none) == W_STR);\n'
                '    assert(wysna_str_owner(s_none) == NULL);\n'
                '    assert(strcmp(wysna_str_bytes(s_none, (char[7]){0}, NULL), "none") == 0);\n'
                '    wysna_release(s_none);\n\n'
                '    WVal s_unit = wysna_builtin_str(wysna_unit());\n'
                '    assert(wysna_tag(s_unit) == W_STR);\n'
                '    assert(wysna_str_owner(s_unit) == NULL);\n'
                '    assert(strcmp(wysna_str_bytes(s_unit, (char[7]){0}, NULL), "unit") == 0);\n'
                '    wysna_release(s_unit);\n\n'
                '    /* 3. Small integer fast path (0..9, inline SSO) */\n'
                '    for (int64_t d = 0; d <= 9; d++) {\n'
                '        WVal s_digit = wysna_builtin_str(wysna_int(d));\n'
                '        assert(wysna_tag(s_digit) == W_STR);\n'
                '        assert(wysna_str_owner(s_digit) == NULL);\n'
                '        assert(wysna_str_len(s_digit) == 1);\n'
                '        assert(wysna_str_bytes(s_digit, (char[7]){0}, NULL)[0] == (char)(\'0\' + d));\n'
                '        wysna_release(s_digit);\n'
                '    }\n\n'
                '    /* 4. Multi-digit integer (>6B) owned string fast path */\n'
                '    WVal s_int = wysna_builtin_str(wysna_int(123456789));\n'
                '    assert(wysna_tag(s_int) == W_STR);\n'
                '    assert(wysna_str_owner(s_int) != NULL);\n'
                '    assert(strcmp(wysna_str_bytes(s_int, (char[7]){0}, NULL), "123456789") == 0);\n'
                '    wysna_release(s_int);\n\n'
                '    WVal s_neg = wysna_builtin_str(wysna_int(-987654321));\n'
                '    assert(wysna_tag(s_neg) == W_STR);\n'
                '    assert(wysna_str_owner(s_neg) != NULL);\n'
                '    assert(strcmp(wysna_str_bytes(s_neg, (char[7]){0}, NULL), "-987654321") == 0);\n'
                '    wysna_release(s_neg);\n\n'
                '    /* 5. String clone fast path */\n'
                '    WVal orig = wysna_str("already a string");\n'
                '    WVal s_str = wysna_builtin_str(orig);\n'
                '    assert(wysna_tag(s_str) == W_STR);\n'
                '    assert(s_str.bits == orig.bits);\n'
                '    wysna_release(orig);\n'
                '    wysna_release(s_str);\n\n'
                '    /* 5b. Copy str single ASCII/SSO (<=6B) vs heap (>6B) */\n'
                '    WVal c_ascii = wysna_copy_str("z");\n'
                '    assert(wysna_tag(c_ascii) == W_STR);\n'
                '    assert(wysna_str_owner(c_ascii) == NULL);\n'
                '    assert(wysna_str_len(c_ascii) == 1);\n'
                '    assert(wysna_str_bytes(c_ascii, (char[7]){0}, NULL)[0] == \'z\');\n'
                '    wysna_release(c_ascii);\n\n'
                '    WVal c_sso = wysna_copy_str("xyz");\n'
                '    assert(wysna_tag(c_sso) == W_STR && wysna_str_owner(c_sso) == NULL && wysna_str_len(c_sso) == 3);\n'
                '    assert(strcmp(wysna_str_bytes(c_sso, (char[7]){0}, NULL), "xyz") == 0);\n'
                '    wysna_release(c_sso);\n'
                '    WVal c_heap = wysna_copy_str("1234567");\n'
                '    assert(wysna_tag(c_heap) == W_STR && wysna_str_owner(c_heap) != NULL && wysna_str_len(c_heap) == 7);\n'
                '    assert(strcmp(wysna_str_bytes(c_heap, (char[7]){0}, NULL), "1234567") == 0);\n'
                '    wysna_release(c_heap);\n\n'
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
                '    /* 1. Slicing single ASCII char returns inline SSO (zero alloc) */\n'
                '    WVal s = wysna_str("abcdefghij");\n'
                '    WVal sub1 = wysna_builtin_slice(s, wysna_int(2), wysna_int(3));\n'
                '    assert(wysna_tag(sub1) == W_STR);\n'
                '    assert(wysna_str_owner(sub1) == NULL);\n'
                '    assert(wysna_str_len(sub1) == 1);\n'
                '    assert(wysna_str_bytes(sub1, (char[7]){0}, NULL)[0] == \'c\');\n'
                '    wysna_release(sub1);\n\n'
                '    /* 2. Slicing 0 chars returns empty SSO */\n'
                '    WVal sub0 = wysna_builtin_slice(s, wysna_int(2), wysna_int(2));\n'
                '    assert(wysna_tag(sub0) == W_STR);\n'
                '    assert(wysna_str_owner(sub0) == NULL);\n'
                '    assert(wysna_str_len(sub0) == 0);\n'
                '    wysna_release(sub0);\n\n'
                '    /* 3. Slicing <=6 chars returns inline SSO; >6 chars returns owned string */\n'
                '    WVal sub_sso = wysna_builtin_slice(s, wysna_int(1), wysna_int(4));\n'
                '    assert(wysna_tag(sub_sso) == W_STR && wysna_str_owner(sub_sso) == NULL);\n'
                '    assert(strcmp(wysna_str_bytes(sub_sso, (char[7]){0}, NULL), "bcd") == 0);\n'
                '    wysna_release(sub_sso);\n'
                '    WVal sub_heap = wysna_builtin_slice(s, wysna_int(1), wysna_int(9));\n'
                '    assert(wysna_tag(sub_heap) == W_STR && wysna_str_owner(sub_heap) != NULL);\n'
                '    assert(strcmp(wysna_str_bytes(sub_heap, (char[7]){0}, NULL), "bcdefghi") == 0);\n'
                '    wysna_release(sub_heap);\n'
                '    wysna_release(s);\n\n'
                '    /* 4. Reading empty file returns unowned string with 0 allocations */\n'
                '    WVal p_empty = wysna_str("' + str(empty_txt).replace('\\', '/') + '");\n'
                '    WVal res_empty = wysna_builtin_read_file(p_empty);\n'
                '    assert(wysna_tag(res_empty) == W_OK);\n'
                '    WVal str_empty = wysna_as_box(res_empty)->val;\n'
                '    assert(wysna_tag(str_empty) == W_STR);\n'
                '    assert(wysna_str_owner(str_empty) == NULL);\n'
                '    assert(wysna_str_len(str_empty) == 0);\n'
                '    wysna_release(p_empty);\n'
                '    wysna_release(res_empty);\n\n'
                '    /* 5. Reading small (>6B) file shrinks buffer and succeeds cleanly */\n'
                '    WVal p_small = wysna_str("' + str(small_txt).replace('\\', '/') + '");\n'
                '    WVal res_small = wysna_builtin_read_file(p_small);\n'
                '    assert(wysna_tag(res_small) == W_OK);\n'
                '    WVal str_small = wysna_as_box(res_small)->val;\n'
                '    assert(wysna_tag(str_small) == W_STR);\n'
                '    assert(wysna_str_owner(str_small) != NULL);\n'
                '    assert(strcmp(wysna_str_bytes(str_small, (char[7]){0}, NULL), "1234567890") == 0);\n'
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
