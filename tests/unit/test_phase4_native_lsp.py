"""Phase 4 tests: Stage-3 Native LSP over stdio JSON-RPC 2.0 and driver.c integration."""
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


class LspStdioClient:
    """Thread-backed Content-Length JSON-RPC 2.0 client for testing native LSP subprocesses."""

    def __init__(self, proc: subprocess.Popen):
        self.proc = proc
        self._incoming: queue.Queue = queue.Queue()
        self._reader_error = None
        self._thread = threading.Thread(target=self._read_loop, daemon=True)
        self._thread.start()
        self._next_id = 1

    def _read_exact(self, n: int) -> bytes:
        chunks = []
        remaining = n
        while remaining > 0:
            chunk = self.proc.stdout.read(remaining)
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)

    def _read_loop(self):
        try:
            while True:
                headers = {}
                while True:
                    line = self.proc.stdout.readline()
                    if not line:
                        return
                    if line in (b"\r\n", b"\n"):
                        if headers:
                            break
                        continue
                    text = line.decode("ascii", errors="replace").strip()
                    if ":" in text:
                        k, v = text.split(":", 1)
                        headers[k.strip().lower()] = v.strip()
                length = int(headers.get("content-length", "0"))
                if length <= 0:
                    continue
                payload = self._read_exact(length)
                if len(payload) != length:
                    return
                self._incoming.put(json.loads(payload.decode("utf-8")))
        except BaseException as exc:
            self._reader_error = exc

    def send(self, message: dict):
        body = json.dumps(message, ensure_ascii=False).encode("utf-8")
        frame = f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body
        self.proc.stdin.write(frame)
        self.proc.stdin.flush()

    def request(self, method: str, params=None, timeout: float = 20.0):
        rid = self._next_id
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self.send(msg)
        buffered = []
        deadline = time.monotonic() + timeout
        while True:
            rem = deadline - time.monotonic()
            if rem <= 0:
                raise TimeoutError(f"Timed out waiting for LSP response id={rid} ({method})")
            item = self._incoming.get(timeout=rem)
            if item.get("id") == rid:
                for b in buffered:
                    self._incoming.put(b)
                return item
            buffered.append(item)

    def notify(self, method: str, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.send(msg)

    def wait_diagnostics(self, uri: str, timeout: float = 20.0) -> list:
        deadline = time.monotonic() + timeout
        buffered = []
        while True:
            rem = deadline - time.monotonic()
            if rem <= 0:
                for b in buffered:
                    self._incoming.put(b)
                raise TimeoutError(f"Timed out waiting for publishDiagnostics for {uri}")
            item = self._incoming.get(timeout=rem)
            if item.get("method") == "textDocument/publishDiagnostics" and item.get("params", {}).get("uri") == uri:
                for b in buffered:
                    self._incoming.put(b)
                return item["params"]["diagnostics"]
            buffered.append(item)


class Phase4NativeLspTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler_bin = ensure_selfhost_compiler_binary(str(ROOT))

    def _spawn_lsp(self, cmd, *, env=None):
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env or dict(os.environ, WYSNA_MEM_CHECK="1"),
        )
        return proc, LspStdioClient(proc)

    def test_native_lsp_navigation_graph_hover_incremental_edits_and_resilience(self):
        with tempfile.TemporaryDirectory(prefix="wysna_p4_lsp_") as tmp:
            work = Path(tmp).resolve()
            contracts = work / "contracts.wysna"
            main_file = work / "main.wysna"
            contracts_src = (
                'pub interface Greeter {\n'
                '    fn greet(who: str) -> str\n'
                '}\n'
                'pub class ConsoleGreeter implements Greeter {\n'
                '    tag: str\n'
                '    public fn init(tag: str) { this.tag = tag }\n'
                '    public fn greet(who: str) -> str { return this.tag + ":" + who }\n'
                '}\n'
            )
            main_src = (
                'from "contracts.wysna" use { Greeter, ConsoleGreeter }\n'
                'fn run_greet(msg: str) -> str {\n'
                '    let g = ConsoleGreeter("ok")\n'
                '    print(msg)\n'
                '    return g.greet(msg)\n'
                '}\n'
                'fn main() -> unit {\n'
                '    let emoji = "😀"\n'
                '    print(run_greet(emoji))\n'
                '}\n'
            )
            contracts.write_text(contracts_src, encoding="utf-8")
            main_file.write_text(main_src, encoding="utf-8")

            proc, client = self._spawn_lsp([self.compiler_bin, "--stdlib-root", str(ROOT / "stdlib"), "lsp"])
            try:
                init = client.request("initialize", {"rootUri": work.as_uri()})
                caps = init["result"]["capabilities"]
                self.assertEqual(caps["positionEncoding"], "utf-16")
                self.assertEqual(caps["textDocumentSync"], {"openClose": True, "change": 2})
                self.assertTrue(caps["definitionProvider"])
                self.assertTrue(caps["implementationProvider"])
                self.assertTrue(caps["hoverProvider"])
                self.assertTrue(caps["referencesProvider"])
                self.assertTrue(caps["completionProvider"])
                self.assertTrue(caps["documentFormattingProvider"])
                self.assertTrue(caps["semanticTokensProvider"]["full"])
                self.assertEqual(init["result"]["serverInfo"]["name"], "wysna-native")
                client.notify("initialized", {})

                c_uri = contracts.as_uri()
                m_uri = main_file.as_uri()

                client.notify("textDocument/didOpen", {"textDocument": {"uri": c_uri, "languageId": "wysna", "version": 1, "text": contracts_src}})
                self.assertEqual(client.wait_diagnostics(c_uri), [])

                client.notify("textDocument/didOpen", {"textDocument": {"uri": m_uri, "languageId": "wysna", "version": 1, "text": main_src}})
                self.assertEqual(client.wait_diagnostics(m_uri), [])

                # Cross-file go-to-definition from main.wysna -> contracts.wysna
                def_resp = client.request("textDocument/definition", {"textDocument": {"uri": m_uri}, "position": {"line": 2, "character": 17}})
                self.assertEqual(def_resp["result"]["uri"], c_uri)
                self.assertEqual(def_resp["result"]["range"]["start"]["line"], 3)

                # Interface go-to-implementation -> ConsoleGreeter
                impl_resp = client.request("textDocument/implementation", {"textDocument": {"uri": c_uri}, "position": {"line": 0, "character": 16}})
                self.assertTrue(any(loc["uri"] == c_uri and loc["range"]["start"]["line"] == 3 for loc in impl_resp["result"]), impl_resp)

                # Graph-enriched hover on Greeter & run_greet
                hover_iface = client.request("textDocument/hover", {"textDocument": {"uri": c_uri}, "position": {"line": 0, "character": 16}})
                self.assertIn("Implemented by", hover_iface["result"]["contents"]["value"])
                self.assertIn("ConsoleGreeter", hover_iface["result"]["contents"]["value"])

                hover_fn = client.request("textDocument/hover", {"textDocument": {"uri": m_uri}, "position": {"line": 1, "character": 5}})
                self.assertIn("Calls", hover_fn["result"]["contents"]["value"])
                self.assertIn("Capabilities", hover_fn["result"]["contents"]["value"])

                # Cross-file references for run_greet (declaration + call) and ConsoleGreeter
                refs_incl = client.request("textDocument/references", {"textDocument": {"uri": m_uri}, "position": {"line": 1, "character": 5}, "context": {"includeDeclaration": True}})
                self.assertGreaterEqual(len(refs_incl["result"]), 2)
                refs_excl = client.request("textDocument/references", {"textDocument": {"uri": m_uri}, "position": {"line": 1, "character": 5}, "context": {"includeDeclaration": False}})
                self.assertEqual(len(refs_excl["result"]), 1)

                # Completion & semantic tokens
                comp_resp = client.request("textDocument/completion", {"textDocument": {"uri": m_uri}, "position": {"line": 4, "character": 12}})
                labels = [item["label"] for item in comp_resp["result"]]
                self.assertIn("greet", labels)

                tok_resp = client.request("textDocument/semanticTokens/full", {"textDocument": {"uri": m_uri}})
                tok_data = tok_resp["result"]["data"]
                self.assertGreater(len(tok_data), 0)
                self.assertEqual(len(tok_data) % 5, 0)

                # Incremental didChange with UTF-16 surrogate emoji edit followed by syntax error
                client.notify("textDocument/didChange", {
                    "textDocument": {"uri": m_uri, "version": 2},
                    "contentChanges": [
                        {"range": {"start": {"line": 7, "character": 17}, "end": {"line": 7, "character": 19}}, "text": "ok"},
                    ],
                })
                self.assertEqual(client.wait_diagnostics(m_uri), [])

                # Stale version must be ignored cleanly
                client.notify("textDocument/didChange", {
                    "textDocument": {"uri": m_uri, "version": 1},
                    "contentChanges": [{"text": "broken syntax\n"}],
                })

                # Introduce type error on a mid-file line: server must stay alive, publish accurate multi-line diagnostic, and keep last-valid graph completion!
                bad_src = (
                    'from "contracts.wysna" use { Greeter, ConsoleGreeter }\n'
                    'fn ok() -> int { return 1 }\n'
                    'fn broken() -> int {\n'
                    '    let wrong: int = "str"\n'
                    '    return 1\n'
                    '}\n'
                )
                client.notify("textDocument/didChange", {
                    "textDocument": {"uri": m_uri, "version": 3},
                    "contentChanges": [{"text": bad_src}],
                })
                broken_diags = client.wait_diagnostics(m_uri)
                self.assertGreater(len(broken_diags), 0)
                self.assertEqual(broken_diags[0]["source"], "wysna")
                self.assertTrue(broken_diags[0]["code"].startswith("W"))
                self.assertGreaterEqual(broken_diags[0]["range"]["start"]["line"], 3)
                self.assertIsNone(proc.poll())

                # Fallback completion while currently broken
                resilient_comp = client.request("textDocument/completion", {"textDocument": {"uri": m_uri}, "position": {"line": 0, "character": 0}})
                self.assertGreater(len(resilient_comp["result"]), 0)

                # Restore main_src with clean syntax for formatting and cross-file dependency test
                client.notify("textDocument/didChange", {
                    "textDocument": {"uri": m_uri, "version": 4},
                    "contentChanges": [{"text": "from \"contracts.wysna\" use { Greeter, ConsoleGreeter }\nfn main() -> unit {\n  let g = ConsoleGreeter(\"x\")\n  print(g.greet(\"y\"))\n}\n"}],
                })
                self.assertEqual(client.wait_diagnostics(m_uri), [])

                fmt_resp = client.request("textDocument/formatting", {"textDocument": {"uri": m_uri}})
                self.assertEqual(len(fmt_resp["result"]), 1)
                self.assertIn("    let g = ConsoleGreeter(\"x\")", fmt_resp["result"][0]["newText"])

                # Unsaved dependency break in contracts.wysna propagates into main.wysna without writing to disk!
                client.notify("textDocument/didChange", {
                    "textDocument": {"uri": c_uri, "version": 2},
                    "contentChanges": [{"text": "pub interface Greeter {\n    fn greet(who: str) -> str\n}\n"}],
                })
                _c_diags = client.wait_diagnostics(c_uri)
                dep_diags = client.wait_diagnostics(m_uri)
                self.assertGreater(len(dep_diags), 0)

                # Close main_file clears diagnostics
                client.notify("textDocument/didClose", {"textDocument": {"uri": m_uri}})
                self.assertEqual(client.wait_diagnostics(m_uri), [])

                shut = client.request("shutdown")
                self.assertIsNone(shut["result"])
                post_shut = client.request("textDocument/hover", {"textDocument": {"uri": c_uri}, "position": {"line": 0, "character": 0}})
                self.assertEqual(post_shut["error"]["code"], -32600)
                client.notify("exit")

                rc = proc.wait(timeout=15)
                stderr_text = proc.stderr.read().decode("utf-8", errors="replace")
                self.assertEqual(rc, 0, stderr_text)
                self.assertNotIn("Memory leak detected", stderr_text)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
                proc.stdin.close()
                proc.stdout.close()
                proc.stderr.close()

    def test_native_workspace_rename_signature_help_and_quickfix(self):
        with tempfile.TemporaryDirectory(prefix="wysna-lsp-edits-") as temporary:
            root = Path(temporary).resolve()
            library = root / "scores.wysna"
            client_file = root / "main.wysna"
            unopened = root / "batch.wysna"
            homonym = root / "other.wysna"
            library.write_text('pub fn score(points: int, tag: str) -> int { points }\n', encoding="utf-8")
            source = (
                'import "scores.wysna" as scores\n'
                'let result = scores.score((2 + 3), "😀,ok")\n'
                'print("score", result)\n'
            )
            client_file.write_text(source, encoding="utf-8")
            unopened.write_text('from "scores.wysna" use score as keep\nprint(keep(1, "audit"))\n',
                                encoding="utf-8")
            homonym.write_text('fn score() -> int { 0 }\nprint(score())\n', encoding="utf-8")
            proc, client = self._spawn_lsp([self.compiler_bin, "--stdlib-root", str(ROOT / "stdlib"), "lsp"])
            try:
                capabilities = client.request("initialize", {"rootUri": root.as_uri()})["result"]["capabilities"]
                self.assertTrue(capabilities["renameProvider"])
                self.assertIn("quickfix", capabilities["codeActionProvider"]["codeActionKinds"])
                self.assertIn(",", capabilities["signatureHelpProvider"]["triggerCharacters"])
                uri = client_file.as_uri()
                client.notify("textDocument/didOpen", {"textDocument": {
                    "uri": uri, "version": 3, "languageId": "wysna", "text": source,
                }})
                self.assertEqual(client.wait_diagnostics(uri), [])
                params = {"textDocument": {"uri": uri}}
                signature = client.request("textDocument/signatureHelp", {
                    **params, "position": {"line": 1, "character": 36},
                })["result"]
                self.assertEqual(signature["activeParameter"], 1)
                self.assertEqual(signature["signatures"][0]["parameters"],
                                 [{"label": "points: int"}, {"label": "tag: str"}])

                rename = {**params, "position": {"line": 1, "character": 21}, "newName": "calculate_score"}
                result = client.request("textDocument/rename", rename)["result"]
                edits = {change["textDocument"]["uri"]: change for change in result["documentChanges"]}
                self.assertEqual(set(edits), {library.as_uri(), uri, unopened.as_uri()})
                self.assertEqual(edits[uri]["textDocument"]["version"], 3)
                for path in (library, client_file, unopened):
                    self.assertEqual(len(edits[path.as_uri()]["edits"]), 1)
                    edit = edits[path.as_uri()]["edits"][0]
                    line = edit["range"]["start"]["line"]
                    start = edit["range"]["start"]["character"]
                    end = edit["range"]["end"]["character"]
                    self.assertEqual(path.read_text().splitlines()[line][start:end], "score")
                    self.assertEqual(edit["newText"], "calculate_score")
                rejected = client.request("textDocument/rename", {**rename, "newName": "result"})
                self.assertIsNone(rejected["result"])

                alias_uri = unopened.as_uri()
                alias_source = unopened.read_text(encoding="utf-8")
                client.notify("textDocument/didOpen", {"textDocument": {
                    "uri": alias_uri, "version": 1, "languageId": "wysna", "text": alias_source,
                }})
                self.assertEqual(client.wait_diagnostics(alias_uri), [])
                self.assertEqual(client.wait_diagnostics(uri), [])
                client.notify("textDocument/didChange", {
                    "textDocument": {"uri": alias_uri, "version": 2},
                    "contentChanges": [{"text": alias_source[:alias_source.index('"audit"')]}],
                })
                self.assertTrue(client.wait_diagnostics(alias_uri))
                self.assertEqual(client.wait_diagnostics(uri), [])
                alias_help = client.request("textDocument/signatureHelp", {
                    "textDocument": {"uri": alias_uri}, "position": {"line": 1, "character": 14},
                })["result"]
                self.assertEqual(alias_help["activeParameter"], 1)
                self.assertEqual(alias_help["signatures"][0]["parameters"], signature["signatures"][0]["parameters"])

                typo = 'let priority = 1\nprint("😀", priorit)\n'
                client.notify("textDocument/didChange", {
                    "textDocument": {"uri": uri, "version": 4},
                    "contentChanges": [{"text": typo}],
                })
                diagnostics = client.wait_diagnostics(uri)
                self.assertEqual(diagnostics[0]["code"], "W1001")
                action_params = {**params, "range": diagnostics[0]["range"],
                                 "context": {"diagnostics": diagnostics, "only": ["quickfix"]}}
                edit_action, = client.request("textDocument/codeAction", action_params)["result"]
                self.assertTrue(edit_action["isPreferred"])
                doc_change, = edit_action["edit"]["documentChanges"]
                self.assertEqual(doc_change["textDocument"], {"uri": uri, "version": 4})
                fix, = doc_change["edits"]
                self.assertEqual(fix["newText"], "priority")
                self.assertEqual(fix["range"]["start"], {"line": 1, "character": 12})
                self.assertEqual(fix["range"]["end"], {"line": 1, "character": 19})
                client.request("shutdown")
                client.notify("exit")
                self.assertEqual(proc.wait(timeout=15), 0, proc.stderr.read())
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
                proc.stdin.close()
                proc.stdout.close()
                proc.stderr.close()

    def test_native_driver_c_lsp_passthrough_without_python(self):
        with tempfile.TemporaryDirectory(prefix="wysna_p4_sdk_lsp_") as tmp:
            tmp_path = Path(tmp).resolve()
            sdk_root = tmp_path / "sdk"
            bin_dir = sdk_root / "bin"
            libexec_dir = sdk_root / "libexec"
            bin_dir.mkdir(parents=True)
            libexec_dir.mkdir(parents=True)
            shutil.copy2(self.compiler_bin, libexec_dir / executable_name("wysna-compiler"))
            shutil.copytree(ROOT / "stdlib", sdk_root / "stdlib")

            driver_bin = bin_dir / executable_name("wysna")
            cc = subprocess.run(
                [
                    "cc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
                    f'-DWYSNA_NATIVE_VERSION="{WYSNA_VERSION}"',
                    '-DWYSNA_NATIVE_TARGET="native-host"',
                    f"-I{ROOT}",
                    str(ROOT / "packaging/native/driver.c"),
                    "-o", str(driver_bin),
                ],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(cc.returncode, 0, cc.stderr)

            sample = tmp_path / "hello.wysna"
            sample_src = 'fn answer() -> int { return 42 }\nfn main() -> unit { print(answer()) }\n'
            sample.write_text(sample_src, encoding="utf-8")

            env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
            env["PATH"] = "/usr/bin:/bin"
            env["WYSNA_MEM_CHECK"] = "1"
            proc, client = self._spawn_lsp([str(driver_bin), "lsp"], env=env)
            try:
                init = client.request("initialize", {})
                self.assertEqual(init["result"]["serverInfo"]["name"], "wysna-native")
                client.notify("initialized", {})

                s_uri = sample.as_uri()
                client.notify("textDocument/didOpen", {"textDocument": {"uri": s_uri, "languageId": "wysna", "version": 1, "text": sample_src}})
                self.assertEqual(client.wait_diagnostics(s_uri), [])

                hover = client.request("textDocument/hover", {"textDocument": {"uri": s_uri}, "position": {"line": 0, "character": 5}})
                self.assertIn("answer", hover["result"]["contents"]["value"])

                client.request("shutdown")
                client.notify("exit")
                rc = proc.wait(timeout=15)
                stderr_text = proc.stderr.read().decode("utf-8", errors="replace")
                self.assertEqual(rc, 0, stderr_text)
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=5)
                proc.stdin.close()
                proc.stdout.close()
                proc.stderr.close()


if __name__ == "__main__":
    unittest.main()
