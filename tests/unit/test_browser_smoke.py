"""Verify retries and diagnostics for the shared browser application smoke."""

import importlib.util
from pathlib import Path
import subprocess
import unittest
from unittest.mock import Mock, patch


SCRIPT = (Path(__file__).resolve().parents[2] / "examples" / "shared-browser-app"
          / "tests" / "browser_smoke.py")
SPEC = importlib.util.spec_from_file_location("wysna_browser_smoke", SCRIPT)
browser_smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(browser_smoke)


class FakeProcess:
    def __init__(self, *, output="", error="", timeout=False):
        self.output = output
        self.error = error
        self.timeout = timeout
        self.pid = id(self)
        self.returncode = 0
        self.kill = Mock()

    def communicate(self, timeout=None):
        if self.timeout and timeout is not None:
            raise subprocess.TimeoutExpired(
                "google-chrome", timeout,
                output="partial browser output", stderr="partial browser error",
            )
        if self.timeout:
            return "partial browser output", "partial browser error"
        return self.output, self.error


class BrowserSmokeRetryTests(unittest.TestCase):
    def test_timeout_retries_with_a_fresh_profile_and_returns_expected_dom(self):
        first = FakeProcess(timeout=True)
        second = FakeProcess(output=browser_smoke.EXPECTED_RESULT)
        with patch.object(browser_smoke.subprocess, "Popen", side_effect=[first, second]) as popen:
            with patch.object(browser_smoke.os, "killpg") as kill_group:
                result = browser_smoke.run_headless_browser("google-chrome", "http://localhost/")

        self.assertEqual(result.returncode, 0)
        self.assertIn(browser_smoke.EXPECTED_RESULT, result.stdout)
        self.assertEqual(popen.call_count, 2)
        first_profile = next(arg for arg in popen.call_args_list[0].args[0]
                             if arg.startswith("--user-data-dir="))
        second_profile = next(arg for arg in popen.call_args_list[1].args[0]
                              if arg.startswith("--user-data-dir="))
        self.assertNotEqual(first_profile, second_profile)
        if browser_smoke.os.name == "posix":
            kill_group.assert_called_once_with(first.pid, browser_smoke.signal.SIGKILL)
        else:
            first.kill.assert_called_once()

    def test_repeated_timeouts_fail_with_partial_output_from_both_attempts(self):
        processes = [FakeProcess(timeout=True), FakeProcess(timeout=True)]
        with patch.object(browser_smoke.subprocess, "Popen", side_effect=processes):
            with patch.object(browser_smoke.os, "killpg"):
                with self.assertRaises(SystemExit) as failure:
                    browser_smoke.run_headless_browser("google-chrome", "http://localhost/")

        message = str(failure.exception)
        self.assertIn("attempt 1: timed out after 30 seconds", message)
        self.assertIn("attempt 2: timed out after 30 seconds", message)
        self.assertEqual(message.count("partial browser error"), 2)


if __name__ == "__main__":
    unittest.main()
