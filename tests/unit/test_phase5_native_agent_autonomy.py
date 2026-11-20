"""Phase 5 tests: Stage-3 native schema, fix, skills, capabilities, and stdio MCP server without Python."""
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.Platform import executable_name
from common.Version import WYSNA_VERSION
from main import ensure_selfhost_compiler_binary


class McpLineClient:
    """Newline-delimited JSON-RPC 2.0 client for testing native `wysna mcp` subprocesses."""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self._incoming: queue.Queue = queue.Queue()
        self._reader_error = None
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        self._next_id = 1

    def _read_loop(self):
        try:
            while True:
                line = self.proc.stdout.readline()
                if not line:
                    return
                text = line.decode("utf-8").strip()
                if text:
                    self._incoming.put(json.loads(text))
        except BaseException as exc:
            self._reader_error = exc

    def send_raw(self, raw_line: str):
        self.proc.stdin.write((raw_line + "\n").encode("utf-8"))
        self.proc.stdin.flush()

    def request(self, method: str, params=None, timeout: float = 20.0):
        rid = self._next_id
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self.send_raw(json.dumps(msg, ensure_ascii=False))
        return self._read_for_id(rid, timeout=timeout)

    def _read_for_id(self, expected_id, timeout: float = 20.0):
        deadline = time.monotonic() + timeout
        buffered = []
        while True:
            rem = deadline - time.monotonic()
            if rem <= 0:
                raise TimeoutError(f"Timed out waiting for MCP response id={expected_id}")
            item = self._incoming.get(timeout=rem)
            if item.get("id") == expected_id:
                for b in buffered:
                    self._incoming.put(b)
                return item
            buffered.append(item)

    def notify(self, method: str, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.send_raw(json.dumps(msg, ensure_ascii=False))

    def call_tool(self, name: str, arguments: dict, timeout: float = 20.0):
        resp = self.request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        res = resp["result"]
        payload = json.loads(res["content"][0]["text"])
        return bool(res["isError"]), payload


class Phase5NativeAgentAutonomyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler_bin = ensure_selfhost_compiler_binary(str(ROOT))
        cls._sdk_tmpdir = tempfile.TemporaryDirectory(prefix="wysna_p5_sdk_")
        tmp_path = Path(cls._sdk_tmpdir.name).resolve()
        cls.sdk_root = tmp_path / "sdk"
        bin_dir = cls.sdk_root / "bin"
        libexec_dir = cls.sdk_root / "libexec"
        bin_dir.mkdir(parents=True)
        libexec_dir.mkdir(parents=True)
        shutil.copy2(cls.compiler_bin, libexec_dir / executable_name("wysna-compiler"))
        shutil.copytree(ROOT / "stdlib", cls.sdk_root / "stdlib")

        cls.driver_bin = bin_dir / executable_name("wysna")
        cc = subprocess.run(
            [
                "cc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                f'-DWYSNA_NATIVE_VERSION="{WYSNA_VERSION}"',
                '-DWYSNA_NATIVE_TARGET="native-host"',
                f"-I{ROOT}",
                str(ROOT / "packaging/native/driver.c"),
                "-o", str(cls.driver_bin),
            ],
            capture_output=True, text=True, timeout=60,
        )
        if cc.returncode != 0:
            raise RuntimeError(f"Failed building test native driver: {cc.stderr}")

    @classmethod
    def tearDownClass(cls):
        cls._sdk_tmpdir.cleanup()

    def _clean_env(self, scratch_tmp: Path) -> dict:
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        if os.name != "nt":
            env["PATH"] = "/usr/bin:/bin"
        env["WYSNA_MEM_CHECK"] = "1"
        env["TMPDIR"] = str(scratch_tmp)
        env["TEMP"] = str(scratch_tmp)
        env["TMP"] = str(scratch_tmp)
        return env

    def _run_sdk(self, args: list, cwd: Path, scratch_tmp: Path):
        return subprocess.run(
            [str(self.driver_bin), *args],
            cwd=cwd,
            env=self._clean_env(scratch_tmp),
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_native_skills_and_capabilities_without_python(self):
        with tempfile.TemporaryDirectory(prefix="wysna_p5_caps_") as tmp:
            work = Path(tmp).resolve()
            scratch = work / "_scratch"
            scratch.mkdir()

            res_sk = self._run_sdk(["skills", "--output-format=json-v1"], work, scratch)
            self.assertEqual(res_sk.returncode, 0, res_sk.stderr)
            sk_env = json.loads(res_sk.stdout)
            self.assertEqual(sk_env["status"], "ok")
            self.assertEqual(sk_env["result"]["schema_version"], "wysna.skills/1")
            self.assertEqual(sk_env["result"]["topic"], "overview")
            self.assertEqual(
                sorted(sk_env["result"]["available_topics"]),
                ["agent-loop", "contracts", "graph", "overview", "patch", "stdlib", "syntax"],
            )

            res_graph_sk = self._run_sdk(["skills", "graph", "--output-format=json-v1"], work, scratch)
            self.assertEqual(res_graph_sk.returncode, 0, res_graph_sk.stderr)
            self.assertIn("blast radius", json.loads(res_graph_sk.stdout)["result"]["content"])

            bad_sk = self._run_sdk(["skills", "does-not-exist", "--output-format=json-v1"], work, scratch)
            self.assertEqual(bad_sk.returncode, 2)
            bad_env = json.loads(bad_sk.stdout)
            self.assertEqual(bad_env["status"], "usage_error")
            self.assertEqual(bad_env["diagnostics"][0]["code"], "W5002")

            res_cap = self._run_sdk(["capabilities", "--output-format=json-v1"], work, scratch)
            self.assertEqual(res_cap.returncode, 0, res_cap.stderr)
            cap_env = json.loads(res_cap.stdout)
            self.assertEqual(cap_env["status"], "ok")
            self.assertEqual(cap_env["result"]["schema_version"], "wysna.capabilities/1")
            for expected_cmd in ("schema", "fix", "skills", "mcp", "lsp", "patch"):
                self.assertIn(expected_cmd, cap_env["result"]["commands"])
            self.assertEqual(list(scratch.iterdir()), [])

    def test_native_schema_contracts_and_codecs_without_python(self):
        with tempfile.TemporaryDirectory(prefix="wysna_p5_schema_") as tmp:
            work = Path(tmp).resolve()
            scratch = work / "_scratch"
            scratch.mkdir()

            src = work / "billing.wysna"
            src.write_text(
                "## Status of an invoice.\n"
                "pub enum Status { Pending, Paid(int) }\n"
                "## Invoice record.\n"
                "pub struct Invoice {\n"
                "    id: int requires id >= 1,\n"
                "    amount: float,\n"
                "    note: str?,\n"
                "    status: Status,\n"
                "    tag: str requires len(tag) >= 2 and len(tag) <= 16,\n"
                "}\n"
                "pub struct BillingError { reason: str }\n"
                "## Compute invoice total cleanly.\n"
                "pub fn compute_total(inv: Invoice, tax_rate: float) -> float ! BillingError\n"
                "    requires tax_rate >= 0.0\n"
                "{\n"
                "    return Ok(inv.amount * (1.0 + tax_rate))\n"
                "}\n"
                "## Log invoice to stdout.\n"
                "fn helper_log(inv: Invoice) {\n"
                "    print(inv.id)\n"
                "}\n",
                encoding="utf-8",
            )

            res = self._run_sdk(["schema", "--output-format=json-v1", "--emit-codecs", str(src)], work, scratch)
            self.assertEqual(res.returncode, 0, res.stderr)
            env = json.loads(res.stdout)
            self.assertEqual(env["status"], "ok")
            schema = env["result"]
            self.assertEqual(schema["schema_version"], "wysna.schema/1")
            self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")

            inv_def = schema["$defs"]["Invoice"]
            self.assertEqual(inv_def["description"], "Invoice record.")
            self.assertEqual(set(inv_def["required"]), {"id", "amount", "status", "tag"})
            self.assertEqual(inv_def["properties"]["id"]["minimum"], 1)
            self.assertEqual(inv_def["properties"]["tag"]["minLength"], 2)
            self.assertEqual(inv_def["properties"]["tag"]["maxLength"], 16)
            self.assertEqual(inv_def["properties"]["status"], {"$ref": "#/$defs/Status"})
            self.assertIn("Status", schema["$defs"])

            # Default selects exported pub fn only
            self.assertEqual([t["name"] for t in schema["tools"]], ["compute_total"])
            tool = schema["tools"][0]
            self.assertEqual(tool["description"], "Compute invoice total cleanly.")
            self.assertTrue(tool["fallible"])
            self.assertEqual(tool["output_schema"], {"type": "number"})
            self.assertEqual(tool["error_schema"], {"$ref": "#/$defs/BillingError"})
            self.assertEqual(tool["input_schema"]["properties"]["tax_rate"]["minimum"], 0.0)
            self.assertTrue(tool["pure"])
            self.assertTrue(tool["idempotent"])

            # --all includes private helper_log and models stdio capability
            res_all = self._run_sdk(["schema", "--all", "--output-format=json-v1", str(src)], work, scratch)
            self.assertEqual(res_all.returncode, 0, res_all.stderr)
            tools_all = {t["name"]: t for t in json.loads(res_all.stdout)["result"]["tools"]}
            self.assertEqual(set(tools_all), {"compute_total", "helper_log"})
            self.assertFalse(tools_all["helper_log"]["pure"])
            self.assertIn("stdio", tools_all["helper_log"]["capabilities"])

            # -o writes generated codecs that type-check with native check
            codecs_path = work / "codecs.wysna"
            res_out = self._run_sdk(["schema", "--emit-codecs", "-o", str(codecs_path), str(src)], work, scratch)
            self.assertEqual(res_out.returncode, 0, res_out.stderr)
            codecs_text = codecs_path.read_text(encoding="utf-8")
            self.assertIn('import "std/json"', codecs_text)
            self.assertIn("pub fn encode_Invoice(value: Invoice) -> json.Value", codecs_text)
            self.assertIn("pub fn decode_Status(raw_val: json.Value) -> Status ! json.JsonError", codecs_text)

            # Missing function returns usage_error exit 2
            res_miss = self._run_sdk(["schema", "--function", "nope", "--output-format=json-v1", str(src)], work, scratch)
            self.assertEqual(res_miss.returncode, 2)
            self.assertEqual(json.loads(res_miss.stdout)["status"], "usage_error")
            self.assertEqual(list(scratch.iterdir()), [])

    def test_native_fix_machine_maybe_and_atomic_apply_without_python(self):
        with tempfile.TemporaryDirectory(prefix="wysna_p5_fix_") as tmp:
            work = Path(tmp).resolve()
            scratch = work / "_scratch"
            scratch.mkdir()

            target = work / "repair.wysna"
            original = (
                "fn demo() -> int {\n"
                "    let counter = 1\n"
                "    counter = counter + 1\n"
                "    let value_alpha = 10\n"
                "    return counte + value_alpa\n"
                "}\n"
            )
            # First test W3001 + high-similarity W1001 ("counter" vs "countr" -> sim = 6/7 = 85% machine)
            original_machine = (
                "fn demo() -> int {\n"
                "    let counter = 1\n"
                "    counter = counter + 1\n"
                "    return countr\n"
                "}\n"
            )
            target.write_text(original_machine, encoding="utf-8")

            dry = self._run_sdk(["fix", "--output-format=json-v1", str(target)], work, scratch)
            self.assertEqual(dry.returncode, 0, dry.stderr)
            dry_env = json.loads(dry.stdout)
            self.assertEqual(dry_env["status"], "ok")
            dry_res = dry_env["result"]
            self.assertFalse(dry_res["written"])
            self.assertTrue(dry_res["changed"])
            self.assertIn("let mut counter = 1", dry_res["fixed_source"])
            self.assertIn("return counter", dry_res["fixed_source"])
            self.assertEqual(target.read_text(encoding="utf-8"), original_machine)
            self.assertEqual(list(work.glob("*.wysna_tmp_*")), [])
            self.assertEqual(list(scratch.iterdir()), [])

            # Heuristic ("maybe") case: "total" vs "tot" -> dist 2, len 5 => 60% ("maybe")
            maybe_file = work / "maybe.wysna"
            maybe_src = "fn calc() -> int {\n    let total = 9\n    return tot\n}\n"
            maybe_file.write_text(maybe_src, encoding="utf-8")

            no_maybe = self._run_sdk(["fix", "--apply", "--output-format=json-v1", str(maybe_file)], work, scratch)
            self.assertEqual(no_maybe.returncode, 1)
            no_maybe_env = json.loads(no_maybe.stdout)
            self.assertFalse(no_maybe_env["result"]["written"])
            self.assertEqual(len(no_maybe_env["result"]["remaining_diagnostics"]), 1)
            self.assertEqual(no_maybe_env["result"]["remaining_diagnostics"][0]["code"], "W1001")
            self.assertEqual(maybe_file.read_text(encoding="utf-8"), maybe_src)

            with_maybe = self._run_sdk(
                ["fix", "--apply", "--include-maybe", "--output-format=json-v1", str(maybe_file)],
                work, scratch,
            )
            self.assertEqual(with_maybe.returncode, 0, with_maybe.stderr)
            wm_res = json.loads(with_maybe.stdout)["result"]
            self.assertTrue(wm_res["written"])
            self.assertIn("return total", maybe_file.read_text(encoding="utf-8"))
            self.assertEqual(list(work.glob("*.wysna_tmp_*")), [])
            self.assertEqual(list(scratch.iterdir()), [])

    def test_native_mcp_stdio_all_tools_confinement_and_errors_without_python(self):
        with tempfile.TemporaryDirectory(prefix="wysna_p5_mcp_parent_") as tmp:
            parent = Path(tmp).resolve()
            root = parent / "root"
            sibling_evil = parent / "root_evil"
            root.mkdir()
            sibling_evil.mkdir()
            scratch = parent / "_scratch"
            scratch.mkdir()

            outside = parent / "outside.wysna"
            outside.write_text("fn ext() -> int { return 1 }\n", encoding="utf-8")
            evil_file = sibling_evil / "secret.wysna"
            evil_file.write_text("fn evil() -> int { return 2 }\n", encoding="utf-8")

            app = root / "app.wysna"
            app.write_text(
                "pub struct Item { id: int requires id >= 1 }\n"
                "## Double positive id.\n"
                "pub fn double_id(x: Item) -> int {\n"
                "    return x.id * 2\n"
                "}\n",
                encoding="utf-8",
            )
            fixme = root / "fixme.wysna"
            fixme.write_text(
                "fn bump() -> int {\n    let num = 1\n    num = num + 1\n    return num\n}\n",
                encoding="utf-8",
            )

            proc = subprocess.Popen(
                [str(self.driver_bin), "mcp", "--root", str(root)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=self._clean_env(scratch),
            )
            client = McpLineClient(proc)
            try:
                init = client.request("initialize", {"protocolVersion": "2025-06-18"})
                self.assertEqual(init["result"]["serverInfo"]["name"], "wysna")
                self.assertEqual(init["result"]["serverInfo"]["version"], WYSNA_VERSION)
                client.notify("notifications/initialized", {})

                ping = client.request("ping", {})
                self.assertEqual(ping["result"], {})

                # Parse error, unknown method, unknown tool, bad args
                client.send_raw("{bad json")
                parse_err = client._read_for_id(None)
                self.assertEqual(parse_err["error"]["code"], -32700)

                meth_err = client.request("no/such/method", {})
                self.assertEqual(meth_err["error"]["code"], -32601)

                tool_err = client.request("tools/call", {"name": "wysna_unknown", "arguments": {}})
                self.assertEqual(tool_err["error"]["code"], -32602)

                arg_err = client.request("tools/call", {"name": "wysna_check", "arguments": [1, 2]})
                self.assertEqual(arg_err["error"]["code"], -32602)

                listed = client.request("tools/list", {})
                names = {t["name"] for t in listed["result"]["tools"]}
                self.assertEqual(
                    names,
                    {
                        "wysna_check", "wysna_fix", "wysna_fmt", "wysna_explain",
                        "wysna_schema", "wysna_api", "wysna_capabilities", "wysna_graph",
                        "wysna_inspect", "wysna_patch", "wysna_skills",
                    },
                )
                self.assertFalse(any("run" in n for n in names))

                # Path confinement: parent traversal & prefix-attack sibling dir rejected
                for bad_rel in ("../outside.wysna", str(evil_file)):
                    failed, bad_payload = client.call_tool("wysna_check", {"path": bad_rel})
                    self.assertTrue(failed, bad_payload)
                    self.assertIn("outside the server root", bad_payload["error"])

                # Exercises all 10 static tools
                f_chk, p_chk = client.call_tool("wysna_check", {"path": "app.wysna"})
                self.assertFalse(f_chk)
                self.assertEqual(p_chk["status"], "ok")

                f_fmt, p_fmt = client.call_tool("wysna_fmt", {"source": "fn x() -> int {\n  return 1\n}\n"})
                self.assertFalse(f_fmt)
                self.assertTrue(p_fmt["result"]["changed"])
                self.assertIn("    return 1", p_fmt["result"]["formatted_source"])

                f_exp, p_exp = client.call_tool("wysna_explain", {"source": "pub fn p() { print(1) }\n"})
                self.assertFalse(f_exp)
                self.assertIn("stdio", p_exp["result"]["program"]["capabilities"])

                f_sch, p_sch = client.call_tool("wysna_schema", {"path": "app.wysna"})
                self.assertFalse(f_sch)
                self.assertIn("Item", p_sch["result"]["$defs"])
                self.assertEqual(p_sch["result"]["tools"][0]["name"], "double_id")

                f_cap, p_cap = client.call_tool("wysna_capabilities", {})
                self.assertFalse(f_cap)
                self.assertEqual(p_cap["result"]["schema_version"], "wysna.capabilities/1")

                f_gr, p_gr = client.call_tool("wysna_graph", {"path": "app.wysna"})
                self.assertFalse(f_gr)
                ghash = p_gr["result"]["graph_hash"]
                self.assertTrue(ghash.startswith("graph:"))

                f_ins, p_ins = client.call_tool("wysna_inspect", {"path": "app.wysna", "symbol": "double_id", "with_deps": True})
                self.assertFalse(f_ins)
                self.assertIn("Item", p_ins["result"]["source"] + json.dumps(p_ins["result"]["dependencies"]))

                f_pat_bad, p_pat_bad = client.call_tool("wysna_patch", {
                    "path": "app.wysna", "symbol": "double_id",
                    "expect_graph_hash": "graph:stale",
                    "replacement": "pub fn double_id(x: Item) -> int { return x.id * 3 }",
                    "apply": True,
                })
                self.assertFalse(f_pat_bad)
                self.assertEqual(p_pat_bad["diagnostics"][0]["code"], "W5005")

                f_pat_ok, p_pat_ok = client.call_tool("wysna_patch", {
                    "path": "app.wysna", "symbol": "double_id",
                    "expect_graph_hash": ghash,
                    "replacement": "pub fn double_id(x: Item) -> int { return x.id * 3 }",
                    "apply": True,
                })
                self.assertFalse(f_pat_ok)
                self.assertTrue(p_pat_ok["result"]["written"])
                self.assertIn("x.id * 3", app.read_text(encoding="utf-8"))

                # fix dry-run then apply via MCP
                f_fix_no_path, p_fix_np = client.call_tool("wysna_fix", {"source": "fn a() -> int { return 1 }", "apply": True})
                self.assertTrue(f_fix_no_path)
                self.assertIn("'apply' requires 'path'", p_fix_np["error"])

                f_fix, p_fix = client.call_tool("wysna_fix", {"path": "fixme.wysna", "apply": True})
                self.assertFalse(f_fix)
                self.assertTrue(p_fix["written"])
                self.assertIn("let mut num = 1", fixme.read_text(encoding="utf-8"))

                f_sk, p_sk = client.call_tool("wysna_skills", {"topic": "graph"})
                self.assertFalse(f_sk)
                self.assertIn("blast radius", p_sk["result"]["content"])

                # Close stdin for clean EOF shutdown
                proc.stdin.close()
                rc = proc.wait(timeout=15)
                stderr_text = proc.stderr.read().decode("utf-8", errors="replace")
                self.assertEqual(rc, 0, stderr_text)
                self.assertNotIn("Memory leak detected", stderr_text)
                self.assertEqual(list(scratch.iterdir()), [])
                self.assertEqual(list(root.glob("*.wysna_tmp_*")), [])
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
                if proc.stdin and not proc.stdin.closed:
                    proc.stdin.close()
                proc.stdout.close()
                proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
