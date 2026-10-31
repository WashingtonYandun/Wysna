"""Verify dictionary key lookup fast paths and zero-leak execution under ASan/UBSan."""
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

from common.Platform import compiler_command, executable_name
from common.Paths import get_repo_root

ROOT = Path(get_repo_root())


class DictOptimizationTests(unittest.TestCase):
    def test_dict_lookup_fast_paths(self):
        compiler = compiler_command()
        with tempfile.TemporaryDirectory(prefix="wysna-dict-opt-") as tmpdir:
            temp = Path(tmpdir)
            main_c = temp / "test_dict.c"
            binary = temp / executable_name("test_dict")

            main_c.write_text(
                '#include <assert.h>\n'
                '#include <stdio.h>\n'
                '#include <string.h>\n'
                '#include "runtime/wysna_runtime.h"\n\n'
                'int main(int argc, char** argv) {\n'
                '    wysna_init_args(argc, argv);\n\n'
                '    /* 1. Empty dictionary fast path */\n'
                '    WVal empty_dict = wysna_make_dict(0, NULL, NULL);\n'
                '    assert(wysna_dict_find(empty_dict.as.dict, wysna_int(42)) == -1);\n'
                '    assert(wysna_dict_find(empty_dict.as.dict, wysna_str("key")) == -1);\n'
                '    WVal opt = wysna_dict_get(empty_dict, wysna_int(42));\n'
                '    assert(opt.tag == W_NONE);\n'
                '    wysna_release(empty_dict);\n\n'
                '    /* 2. Integer key fast path */\n'
                '    WVal ikeys[3] = { wysna_int(10), wysna_int(20), wysna_int(30) };\n'
                '    WVal ivals[3] = { wysna_int(100), wysna_int(200), wysna_int(300) };\n'
                '    WVal idict = wysna_make_dict(3, ikeys, ivals);\n'
                '    assert(wysna_dict_find(idict.as.dict, wysna_int(10)) == 0);\n'
                '    assert(wysna_dict_find(idict.as.dict, wysna_int(20)) == 1);\n'
                '    assert(wysna_dict_find(idict.as.dict, wysna_int(30)) == 2);\n'
                '    assert(wysna_dict_find(idict.as.dict, wysna_int(999)) == -1);\n'
                '    wysna_release(idict);\n\n'
                '    /* 3. String key fast path (pointer equality and memcmp length fast path) */\n'
                '    WVal skeys[3] = {\n'
                '        wysna_str("short"),\n'
                '        wysna_str("longer_string_key"),\n'
                '        wysna_str("short_variant")\n'
                '    };\n'
                '    WVal svals[3] = { wysna_int(1), wysna_int(2), wysna_int(3) };\n'
                '    WVal sdict = wysna_make_dict(3, skeys, svals);\n'
                '    /* Pointer identity hit */\n'
                '    assert(wysna_dict_find(sdict.as.dict, skeys[0]) == 0);\n'
                '    assert(wysna_dict_find(sdict.as.dict, skeys[1]) == 1);\n'
                '    assert(wysna_dict_find(sdict.as.dict, skeys[2]) == 2);\n'
                '    /* Dynamic copy string with matching bytes */\n'
                '    WVal dyn_key = wysna_copy_str("longer_string_key");\n'
                '    assert(dyn_key.as.string.data != skeys[1].as.string.data);\n'
                '    assert(wysna_dict_find(sdict.as.dict, dyn_key) == 1);\n'
                '    wysna_release(dyn_key);\n'
                '    /* String length mismatch fast path */\n'
                '    WVal miss_len = wysna_str("shor");\n'
                '    assert(wysna_dict_find(sdict.as.dict, miss_len) == -1);\n'
                '    wysna_release(sdict);\n\n'
                '    /* 4. Boolean key fast path */\n'
                '    WVal bkeys[2] = { wysna_bool(true), wysna_bool(false) };\n'
                '    WVal bvals[2] = { wysna_int(1), wysna_int(0) };\n'
                '    WVal bdict = wysna_make_dict(2, bkeys, bvals);\n'
                '    assert(wysna_dict_find(bdict.as.dict, wysna_bool(true)) == 0);\n'
                '    assert(wysna_dict_find(bdict.as.dict, wysna_bool(false)) == 1);\n'
                '    wysna_release(bdict);\n\n'
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
