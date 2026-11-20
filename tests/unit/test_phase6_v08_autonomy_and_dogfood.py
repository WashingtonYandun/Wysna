"""Phase 6 tests: v0.8.0 Python-free native `api`, 11/11 MCP, `project` workflows, MIR temp-scope CoW speed, and `examples/wysofia-ops` dogfooding."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.Platform import compiler_command, executable_name, native_stack_link_args
from common.Version import WYSNA_VERSION
from compiler.Bootstrap import ensure_selfhost_compiler_binary
from tests.unit.test_phase5_native_agent_autonomy import McpLineClient


_ZIG_SHIM_C = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char** argv) {
    char** forwarded = (char**)calloc((size_t)argc + 2, sizeof(char*));
    if (!forwarded) return 1;
    int out = 0;
    forwarded[out++] = (char*)HOST_CC_PATH;
    for (int i = 1; i < argc; i++) {
        if (i == 1 && strcmp(argv[i], "cc") == 0) continue;
        if (strcmp(argv[i], "-target") == 0 && i + 1 < argc) { i++; continue; }
        if (strcmp(argv[i], "-mcpu=baseline") == 0) continue;
        forwarded[out++] = argv[i];
    }
    forwarded[out] = NULL;
    execv(HOST_CC_PATH, forwarded);
    perror("execv host cc");
    return 127;
}
'''


def _resolve_host_cc() -> str:
    cmd = compiler_command()
    if not cmd:
        return ""
    resolved = shutil.which(cmd[0]) or cmd[0]
    return resolved if Path(resolved).is_file() else ""


def _probe_sqlite3(cc_path: str) -> bool:
    if not cc_path or os.name == "nt":
        return False
    with tempfile.TemporaryDirectory(prefix="wysna_sqlite_probe_") as tmp:
        c_file = Path(tmp) / "probe.c"
        out_bin = Path(tmp) / "probe"
        c_file.write_text("#include <sqlite3.h>\nint main(void) { return sqlite3_libversion_number() > 0 ? 0 : 1; }\n", encoding="utf-8")
        res = subprocess.run([cc_path, str(c_file), "-lsqlite3", "-o", str(out_bin)], capture_output=True, text=True)
        return res.returncode == 0


class Phase6V08AutonomyAndDogfoodTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler_bin = ensure_selfhost_compiler_binary(str(ROOT))
        cls.host_cc = _resolve_host_cc()
        cls.has_sqlite3 = _probe_sqlite3(cls.host_cc)
        cls._sdk_tmpdir = tempfile.TemporaryDirectory(prefix="wysna_p6_sdk_")
        tmp_path = Path(cls._sdk_tmpdir.name).resolve()
        cls.sdk_root = tmp_path / "sdk"
        bin_dir = cls.sdk_root / "bin"
        libexec_dir = cls.sdk_root / "libexec"
        toolchain_dir = cls.sdk_root / "toolchain/zig"
        bin_dir.mkdir(parents=True)
        libexec_dir.mkdir(parents=True)
        toolchain_dir.mkdir(parents=True)

        shutil.copy2(cls.compiler_bin, libexec_dir / executable_name("wysna-compiler"))
        shutil.copytree(ROOT / "stdlib", cls.sdk_root / "stdlib")
        shutil.copytree(ROOT / "runtime", cls.sdk_root / "runtime")

        cls.driver_bin = bin_dir / executable_name("wysna")
        cc_cmd = compiler_command()
        driver_cmd = cc_cmd + [
            "-std=c99", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
            f'-DWYSNA_NATIVE_VERSION="{WYSNA_VERSION}"',
            '-DWYSNA_NATIVE_TARGET="native-host"',
            f"-I{ROOT}",
            f"-I{ROOT / 'packaging/native'}",
            str(ROOT / "packaging/native/driver.c"),
        ]
        if os.name == "nt":
            driver_cmd += native_stack_link_args(cc_cmd)
        else:
            driver_cmd += ["-lm"]
        driver_cmd += ["-o", str(cls.driver_bin)]
        built = subprocess.run(driver_cmd, capture_output=True, text=True, timeout=60)
        if built.returncode != 0:
            raise RuntimeError(f"Failed building test native driver: {built.stderr}")

        cls.has_posix_cc_shim = False
        if os.name != "nt" and cls.host_cc:
            shim_src = tmp_path / "zig_shim.c"
            shim_bin = toolchain_dir / "zig"
            shim_src.write_text(_ZIG_SHIM_C, encoding="utf-8")
            shim_built = subprocess.run(
                [cls.host_cc, "-std=c99", f'-DHOST_CC_PATH="{cls.host_cc}"', str(shim_src), "-o", str(shim_bin)],
                capture_output=True,
                text=True,
                timeout=30,
            )
            if shim_built.returncode == 0:
                cls.has_posix_cc_shim = True

    @classmethod
    def tearDownClass(cls):
        cls._sdk_tmpdir.cleanup()

    def _clean_env(self, scratch_tmp: Path) -> dict:
        env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME")}
        if os.name != "nt":
            env["PATH"] = ""
        env["WYSNA_MEM_CHECK"] = "1"
        env["TMPDIR"] = str(scratch_tmp)
        env["TEMP"] = str(scratch_tmp)
        env["TMP"] = str(scratch_tmp)
        return env

    def _run_sdk(self, args: list, cwd: Path, scratch_tmp: Path, timeout: float = 45.0):
        return subprocess.run(
            [str(self.driver_bin), *args],
            cwd=cwd,
            env=self._clean_env(scratch_tmp),
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def test_native_project_new_creates_a_project_once(self):
        with tempfile.TemporaryDirectory(prefix="wysna_p6_scaffold_") as tmp:
            work = Path(tmp).resolve()
            scratch = work / "_scratch"
            scratch.mkdir()
            args = ["project", "new", "from-native-sdk", "--output-format=json-v1"]

            result = self._run_sdk(args, work, scratch)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            envelope = json.loads(result.stdout)
            self.assertEqual(envelope["status"], "ok")
            self.assertEqual(len(envelope["artifacts"]), 3)
            project = work / "from-native-sdk"
            manifest = project / "wysna.toml"
            original = manifest.read_bytes()
            check = self._run_sdk(["project", "check", "--manifest", str(manifest)], work, scratch)
            self.assertEqual(check.returncode, 0, check.stderr + check.stdout)

            repeat = self._run_sdk(args, work, scratch)
            self.assertEqual(repeat.returncode, 2, repeat.stderr + repeat.stdout)
            self.assertIn("already exists", json.loads(repeat.stdout)["diagnostics"][0]["message"])
            self.assertEqual(manifest.read_bytes(), original)

    def test_native_project_verifies_frozen_lock_contents(self):
        with tempfile.TemporaryDirectory(prefix="wysna_p6_frozen_") as tmp:
            work = Path(tmp).resolve()
            scratch = work / "_scratch"
            scratch.mkdir()
            project = work / "project"
            shutil.copytree(ROOT / "examples/wysofia-ops", project)
            manifest = project / "wysna.toml"
            (project / "wysna.lock").write_text("{}\n", encoding="utf-8")

            result = self._run_sdk(
                ["project", "check", "--manifest", str(manifest), "--frozen", "--output-format=json-v1"],
                work,
                scratch,
            )
            self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
            diagnostics = json.loads(result.stdout)["diagnostics"]
            self.assertEqual(diagnostics[0]["code"], "W5002")
            self.assertIn("stale or invalid", diagnostics[0]["message"])

            lock = self._run_sdk(
                ["project", "lock", "--manifest", str(manifest), "--output-format=json-v1"], work, scratch,
            )
            self.assertEqual(lock.returncode, 0, lock.stderr + lock.stdout)
            lock_path = project / "wysna.lock"
            native_lock = lock_path.read_bytes()
            checked = self._run_sdk(["project", "check", "--manifest", str(manifest), "--frozen"], work, scratch)
            self.assertEqual(checked.returncode, 0, checked.stderr + checked.stdout)
            seed = subprocess.run(
                [sys.executable, str(ROOT / "wysna"), "project", "lock", "--manifest", str(manifest)],
                cwd=work, capture_output=True, text=True, timeout=30,
            )
            self.assertEqual(seed.returncode, 0, seed.stderr + seed.stdout)
            self.assertEqual(json.loads(lock_path.read_bytes()), json.loads(native_lock))
            checked = self._run_sdk(["project", "check", "--manifest", str(manifest), "--frozen"], work, scratch)
            self.assertEqual(checked.returncode, 0, checked.stderr + checked.stdout)
            with manifest.open("a", encoding="utf-8") as stream:
                stream.write("\n# manifest changed\n")
            stale = self._run_sdk(["project", "check", "--manifest", str(manifest), "--frozen"], work, scratch)
            self.assertEqual(stale.returncode, 2, stale.stderr + stale.stdout)

    def test_native_project_impact_uses_manifest_targets_and_mcp(self):
        with tempfile.TemporaryDirectory(prefix="wysna_p6_impact_") as tmp:
            scratch = Path(tmp).resolve()
            project = ROOT / "examples/shared-browser-app"
            args = ["graph", "--manifest", "wysna.toml", "--symbol", "quote_validation_code",
                    "--rel", "impact", "--output-format=json-v1"]
            result = self._run_sdk(args, project, scratch)
            self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
            payload = json.loads(result.stdout)["result"]
            entries = {entry["name"]: entry for entry in payload["entries"]}
            self.assertEqual(set(entries), {"app", "service", "web", "shared-pricing-contract"})
            self.assertTrue(all(entry["affected"] for entry in entries.values()), entries)
            self.assertIn("javascript", entries["web"]["unanalyzed_boundaries"])
            self.assertIn("c-ffi", entries["service"]["unanalyzed_boundaries"])
            self.assertTrue(entries["app"]["paths"][0]["chain"])

            requests = [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                 "params": {"protocolVersion": "2025-06-18"}},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
                    "name": "wysna_graph", "arguments": {
                        "manifest": "wysna.toml", "symbol": "quote_validation_code", "relation": "impact",
                    },
                }},
            ]
            completed = subprocess.run(
                [str(self.driver_bin), "mcp", "--root", str(project)], cwd=project,
                env=self._clean_env(scratch), input="".join(json.dumps(row) + "\n" for row in requests),
                capture_output=True, text=True, timeout=45,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            responses = {row["id"]: row for row in map(json.loads, completed.stdout.splitlines())}
            reply = responses[2]["result"]
            self.assertFalse(reply["isError"], reply)
            self.assertEqual(json.loads(reply["content"][0]["text"])["result"], payload)

    def test_native_api_cli_stdlib_and_manifest(self):
        with tempfile.TemporaryDirectory(prefix="wysna_p6_api_") as tmp:
            scratch = Path(tmp).resolve() / "_scratch"
            scratch.mkdir()

            all_res = self._run_sdk(["api", "--output-format=json-v1"], ROOT, scratch)
            self.assertEqual(all_res.returncode, 0, all_res.stderr)
            all_env = json.loads(all_res.stdout)
            self.assertEqual(all_env["status"], "ok")
            self.assertEqual(all_env["result"]["schema_version"], "wysna.api-index/1")
            identities = {m["identity"] for m in all_env["result"]["modules"]}
            self.assertGreaterEqual(len(identities), 18)
            self.assertIn("std/json", identities)

            json_res = self._run_sdk(["api", "std/json", "--output-format=json-v1"], ROOT, scratch)
            self.assertEqual(json_res.returncode, 0, json_res.stderr)
            json_env = json.loads(json_res.stdout)
            json_mods = [m["identity"] for m in json_env["result"]["modules"]]
            self.assertIn("std/json", json_mods)

            bad_res = self._run_sdk(["api", "std/does_not_exist", "--output-format=json-v1"], ROOT, scratch)
            self.assertEqual(bad_res.returncode, 2)
            bad_env = json.loads(bad_res.stdout)
            self.assertEqual(bad_env["status"], "usage_error")
            self.assertEqual(bad_env["diagnostics"][0]["code"], "W5002")

            manifest = ROOT / "examples/wysofia-ops/wysna.toml"
            man_res = self._run_sdk(["api", "--manifest", str(manifest), "--output-format=json-v1"], ROOT, scratch)
            self.assertEqual(man_res.returncode, 0, man_res.stderr)
            man_env = json.loads(man_res.stdout)
            man_mods = {m["identity"] for m in man_env["result"]["modules"]}
            self.assertIn("wysofia-ops@0.1.0/audit", man_mods)
            self.assertIn("wysofia-ops@0.1.0/main", man_mods)

    def test_native_mcp_11_tools_including_api_and_confinement(self):
        with tempfile.TemporaryDirectory(prefix="wysna_p6_mcp_") as tmp:
            scratch = Path(tmp).resolve() / "_scratch"
            scratch.mkdir()

            proc = subprocess.Popen(
                [str(self.driver_bin), "mcp", "--root", str(ROOT)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._clean_env(scratch),
            )
            client = McpLineClient(proc)
            try:
                init = client.request("initialize", {"protocolVersion": "2025-06-18"})
                self.assertEqual(init["result"]["serverInfo"]["version"], WYSNA_VERSION)
                client.notify("notifications/initialized", {})

                listed = client.request("tools/list", {})
                names = {t["name"] for t in listed["result"]["tools"]}
                self.assertEqual(len(names), 11)
                self.assertIn("wysna_api", names)

                failed_api, ok_payload = client.call_tool("wysna_api", {"module": "std/json"})
                self.assertFalse(failed_api, ok_payload)
                self.assertEqual(ok_payload["result"]["schema_version"], "wysna.api-index/1")
                self.assertTrue(any(m["identity"] == "std/json" for m in ok_payload["result"]["modules"]))

                failed_man, man_payload = client.call_tool("wysna_api", {"manifest": "examples/wysofia-ops/wysna.toml"})
                self.assertFalse(failed_man, man_payload)
                man_ids = {m["identity"] for m in man_payload["result"]["modules"]}
                self.assertIn("wysofia-ops@0.1.0/audit", man_ids)

                failed_esc, esc_payload = client.call_tool("wysna_api", {"manifest": "../outside/wysna.toml"})
                self.assertTrue(failed_esc, esc_payload)
                self.assertIn("outside the server root", esc_payload["error"])

                proc.stdin.close()
                rc = proc.wait(timeout=15)
                stderr_text = proc.stderr.read().decode("utf-8", errors="replace")
                self.assertEqual(rc, 0, stderr_text)
                self.assertNotIn("Memory leak detected", stderr_text)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
                proc.stdout.close()
                proc.stderr.close()

    @unittest.skipIf(os.name == "nt", "POSIX execv cc shim used for compiled project build/run/test without Python")
    def test_native_project_dogfood_wysofia_ops_without_python(self):
        self.assertTrue(self.has_posix_cc_shim, "Host C compiler required for native project tests")
        with tempfile.TemporaryDirectory(prefix="wysna_p6_ops_") as tmp:
            work = Path(tmp).resolve()
            scratch = work / "_scratch"
            scratch.mkdir()

            manifest = ROOT / "examples/wysofia-ops/wysna.toml"
            out_bin = work / executable_name("wysofia-ops")

            chk = self._run_sdk(["project", "check", "--manifest", str(manifest), "--output-format=json-v1"], work, scratch)
            self.assertEqual(chk.returncode, 0, chk.stderr + chk.stdout)
            chk_env = json.loads(chk.stdout)
            self.assertEqual(chk_env["status"], "ok")

            bld = self._run_sdk(
                ["project", "build", "--manifest", str(manifest), "-o", str(out_bin), "--profile", "release", "--output-format=json-v1"],
                work,
                scratch,
            )
            self.assertEqual(bld.returncode, 0, bld.stderr + bld.stdout)
            self.assertTrue(out_bin.is_file())
            bld_env = json.loads(bld.stdout)
            self.assertEqual(bld_env["status"], "ok")

            run_res = self._run_sdk(["project", "run", "--manifest", str(manifest), "--", "--demo"], work, scratch)
            self.assertEqual(run_res.returncode, 0, run_res.stderr)
            self.assertEqual(
                run_res.stdout,
                '{"critical_events":1,"health_score":35,"total_events":4,"total_tokens":1650,"warning_events":1}\n',
            )

            tst_res = self._run_sdk(["project", "test", "--manifest", str(manifest)], work, scratch)
            self.assertEqual(tst_res.returncode, 0, tst_res.stderr + tst_res.stdout)
            self.assertIn("[PASS] audit-domain-contracts (native)", tst_res.stdout)

            if self.has_sqlite3:
                sql_res = self._run_sdk(
                    [
                        "project", "run", "--manifest", str(manifest), "--target", "sqlite-audit",
                        "--include-dir", str(ROOT / "examples/wysofia-ops/native"),
                        "--link", str(ROOT / "examples/wysofia-ops/native/ops_sqlite.c"),
                        "--library", "sqlite3",
                    ],
                    work,
                    scratch,
                )
                self.assertEqual(sql_res.returncode, 0, sql_res.stderr)
                self.assertEqual(sql_res.stdout, "sqlite_total=3 critical=1\n")

            # Apple toolchain discovery can create its own cache in TMPDIR.
            leftovers = [path for path in scratch.iterdir() if not (sys.platform == "darwin" and path.name == "xcrun_db")]
            self.assertEqual(leftovers, [], f"Temporary files leaked in scratch dir: {leftovers}")

    @unittest.skipIf(os.name == "nt", "POSIX execv cc shim used for compiled run without Python")
    def test_mir_temp_scope_cow_no_quadratic_detach(self):
        self.assertTrue(self.has_posix_cc_shim, "Host C compiler required for native CoW regression test")
        with tempfile.TemporaryDirectory(prefix="wysna_p6_cow_") as tmp:
            work = Path(tmp).resolve()
            scratch = work / "_scratch"
            scratch.mkdir()
            src = work / "cow_15k.wysna"
            src.write_text(
                "fn run_loop() -> int {\n"
                "    let mut items: list[int] = []\n"
                "    let mut i = 0\n"
                "    while i < 15000 {\n"
                "        if len(items) >= 0 {\n"
                "            items.append(i)\n"
                "        }\n"
                "        i = i + 1\n"
                "    }\n"
                "    return len(items)\n"
                "}\n"
                "print(run_loop())\n",
                encoding="utf-8",
            )
            t0 = time.monotonic()
            res = self._run_sdk(["run", str(src), "--profile", "release"], work, scratch, timeout=20.0)
            elapsed = time.monotonic() - t0
            self.assertEqual(res.returncode, 0, res.stderr)
            self.assertEqual(res.stdout, "15000\n")
            self.assertLess(elapsed, 15.0, f"Unexpectedly slow 15k append loop: {elapsed:.2f}s")


if __name__ == "__main__":
    unittest.main()
