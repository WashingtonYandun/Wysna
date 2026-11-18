"""Verify retries and diagnostics for the shared browser application smoke."""

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = (REPO_ROOT / "examples" / "shared-browser-app"
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

    def test_shared_pricing_cli_and_js_host_agree_across_validation_boundaries(self):
        app_root = REPO_ROOT / "examples" / "shared-browser-app"
        for host_path in (app_root / "src" / "cli.wysna", app_root / "web" / "app.js", app_root / "web" / "quote.js"):
            text = host_path.read_text(encoding="utf-8")
            self.assertNotIn("quantity > 1000", text)
            self.assertNotIn("quantity > 1000n", text)
            self.assertNotIn("unitPriceCents > 100000000n", text)
            self.assertNotIn("unit_price_cents > 100000000", text)

        env = dict(os.environ, PYTHONPATH=f"{REPO_ROOT / 'src'}{os.pathsep}{REPO_ROOT}")
        manifest = app_root / "wysna.toml"
        with tempfile.TemporaryDirectory(prefix="wysna-shared-quote-") as tmpdir:
            binary = Path(tmpdir) / ("quote.exe" if os.name == "nt" else "quote")
            build_cli = subprocess.run(
                [sys.executable, str(REPO_ROOT / "main.py"), "project", "build",
                 "--manifest", str(manifest), "--target", "app", "--output", str(binary)],
                cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(build_cli.returncode, 0, build_cli.stderr)

            nominal = subprocess.run([str(binary), "3", "1299"], capture_output=True, text=True, timeout=30)
            self.assertEqual((nominal.returncode, nominal.stdout.strip(), nominal.stderr), (0, "3897", ""))

            boundaries = [
                (q, p)
                for q in (0, 1, 1000, 1001)
                for p in (-1, 0, 100000000, 100000001)
            ]
            boundaries.append((3, 1299))

            cli_outcomes = []
            for qty, price in boundaries:
                res = subprocess.run([str(binary), str(qty), str(price)], capture_output=True, text=True, timeout=30)
                if 1 <= qty <= 1000 and 0 <= price <= 100000000:
                    self.assertEqual(res.returncode, 0, (qty, price, res.stderr))
                    self.assertEqual(res.stdout.strip(), str(qty * price))
                    cli_outcomes.append(("ok", qty * price))
                elif qty < 1 or qty > 1000:
                    self.assertEqual(res.returncode, 2, (qty, price, res.stdout, res.stderr))
                    self.assertIn("quantity must be between 1 and 1000", res.stderr)
                    cli_outcomes.append(("bad_qty", None))
                else:
                    self.assertEqual(res.returncode, 2, (qty, price, res.stdout, res.stderr))
                    self.assertIn("unit price must be between 0 and 100000000 cents", res.stderr)
                    cli_outcomes.append(("bad_price", None))

            overflow_cli = subprocess.run([str(binary), str(1 << 63), "100"], capture_output=True, text=True, timeout=30)
            self.assertEqual(overflow_cli.returncode, 2)

            project_test = subprocess.run(
                [sys.executable, str(REPO_ROOT / "main.py"), "project", "test",
                 "--manifest", str(manifest)],
                cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(project_test.returncode, 0, project_test.stdout + project_test.stderr)

            sys.path[:0] = [str(REPO_ROOT / "src"), str(REPO_ROOT)]
            from compiler.CompilerService import CompilerService
            service = CompilerService(project_root=str(app_root))
            checked_web = service.check_file(app_root / "src" / "pricing.wysna")
            emission = service.emit_c(checked_web, target_profile="browser-library")
            export_names = {item["name"] for item in emission.abi_exports}
            self.assertTrue(
                {"quote_validation_code", "calculate_validated_total_cents", "calculate_total_cents"}
                <= export_names,
                export_names,
            )
            self.assertIn("calculate_validated_total_cents", emission.source)

            node = shutil.which("node")
            if node:
                web_quote_url = (app_root / "web" / "quote.js").resolve().as_uri()

                node_script = (
                    f"import {{ evaluateQuote }} from {json.dumps(web_quote_url)};\n"
                    "import { execFileSync } from 'node:child_process';\n"
                    f"const bin = {json.dumps(str(binary))};\n"
                    "const exportsBridge = {\n"
                    "  calculate_validated_total_cents(q, p) {\n"
                    "    try {\n"
                    "      const out = execFileSync(bin, [q.toString(), p.toString()], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] });\n"
                    "      return BigInt(out.trim());\n"
                    "    } catch (err) {\n"
                    "      const msg = String(err.stderr || '');\n"
                    "      if (msg.includes('quantity must be between')) return -1n;\n"
                    "      if (msg.includes('unit price must be between')) return -2n;\n"
                    "      return -99n;\n"
                    "    }\n"
                    "  },\n"
                    "};\n"
                    "const results = [];\n"
                    f"for (const [q, p] of {json.dumps(boundaries)}) {{\n"
                    "  try {\n"
                    "    results.push(['ok', Number(evaluateQuote(exportsBridge, BigInt(q), BigInt(p)))]);\n"
                    "  } catch (err) {\n"
                    "    if (err.message.includes('Quantity')) results.push(['bad_qty', null]);\n"
                    "    else if (err.message.includes('Unit price')) results.push(['bad_price', null]);\n"
                    "    else throw err;\n"
                    "  }\n"
                    "}\n"
                    "for (const bad of [-(1n << 63n) - 1n, 1n << 63n]) {\n"
                    "  let rejected = false;\n"
                    "  try { evaluateQuote(exportsBridge, bad, 100n); } catch (err) { rejected = /signed 64-bit/.test(err.message); }\n"
                    "  if (!rejected) throw new Error('BigInt ABI overflow was not rejected');\n"
                    "}\n"
                    "console.log(JSON.stringify(results));\n"
                )
                node_res = subprocess.run([node, "--input-type=module", "-e", node_script],
                                          capture_output=True, text=True, timeout=60)
                self.assertEqual(node_res.returncode, 0, node_res.stderr)
                js_outcomes = [tuple(item) for item in json.loads(node_res.stdout)]
                self.assertEqual(js_outcomes, cli_outcomes)


if __name__ == "__main__":
    unittest.main()
