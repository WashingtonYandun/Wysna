"""Unit and integration tests for native tooling commands (check, fmt, run, test) using the compiler engine."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from common.Paths import get_repo_root


class NativeToolingTests(unittest.TestCase):
    def setUp(self):
        self.repo_root = Path(get_repo_root())
        self.wysna_cli = self.repo_root / "wysna"
        self.sample_valid = self.repo_root / "examples/01_hello_world.wysna"
        self.crypto_smoke = self.repo_root / "tests/runtime/crypto_smoke.wysna"

    def test_check_with_compiler_engine_plain_and_json_v1(self):
        # Plain text
        cmd_plain = [sys.executable, str(self.wysna_cli), "check", "--engine=compiler", str(self.sample_valid)]
        res_plain = subprocess.run(cmd_plain, cwd=self.repo_root, capture_output=True, text=True, timeout=120)
        self.assertEqual(res_plain.returncode, 0, res_plain.stderr)
        self.assertIn("[Wysna Compiler] Checked", res_plain.stdout)

        # JSON-v1
        cmd_json = [sys.executable, str(self.wysna_cli), "check", "--engine=compiler", "--output-format=json-v1", str(self.sample_valid)]
        res_json = subprocess.run(cmd_json, cwd=self.repo_root, capture_output=True, text=True, timeout=120)
        self.assertEqual(res_json.returncode, 0, res_json.stderr)
        data = json.loads(res_json.stdout)
        self.assertEqual(data.get("status"), "ok")
        self.assertEqual(data.get("command"), "check")
        self.assertEqual(data.get("tool_exit_code"), 0)

    def test_fmt_with_compiler_engine(self):
        cmd = [sys.executable, str(self.wysna_cli), "fmt", "--engine=compiler", str(self.sample_valid)]
        res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("fn main()", res.stdout)

    def test_run_with_compiler_engine(self):
        cmd = [sys.executable, str(self.wysna_cli), "run", "--engine=compiler", str(self.sample_valid)]
        res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("Hello, Washo!", res.stdout)
        self.assertIn("Sum 1..=100: 5050", res.stdout)

    def test_test_runner_seed_single_target(self):
        cmd = [sys.executable, str(self.wysna_cli), "test", str(self.crypto_smoke)]
        res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("[PASS]", res.stdout)
        self.assertIn("(seed,", res.stdout)

    def test_test_runner_native_compiler_target(self):
        cmd = [sys.executable, str(self.wysna_cli), "test", "--engine=compiler", str(self.crypto_smoke)]
        res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("[PASS]", res.stdout)
        self.assertIn("(native,", res.stdout)

    def test_test_runner_native_alias(self):
        smoke_arith = self.repo_root / "tests/runtime/arithmetic.wysna"
        cmd = [sys.executable, str(self.wysna_cli), "test", "--native", str(smoke_arith)]
        res = subprocess.run(cmd, cwd=self.repo_root, capture_output=True, text=True, timeout=120)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("[PASS]", res.stdout)
        self.assertIn("(native,", res.stdout)

    def test_driver_c_compilation_and_command_contract(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            obj_path = Path(tmpdir) / "driver.o"
            driver_src = self.repo_root / "packaging/native/driver.c"
            cmd = ["cc", "-std=c99", '-DWYSNA_NATIVE_VERSION="0.6.0"', '-DWYSNA_NATIVE_TARGET="aarch64-macos"',
                   f"-I{self.repo_root}", "-c", str(driver_src), "-o", str(obj_path)]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertTrue(obj_path.is_file())


if __name__ == "__main__":
    unittest.main()
