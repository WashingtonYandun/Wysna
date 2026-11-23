"""Loopback-only contract tests for the synchronous std/http pilot."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import ssl
import subprocess
import tempfile
import threading
import time

from main import ensure_selfhost_compiler_binary
from common.Platform import compiler_command
from compiler.CompilerService import CompilerService
from compiler.NativeToolchain import NativeToolchain
from tests.engine_harness import run_engine


ROOT = Path(__file__).resolve().parents[2]


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    requests = []

    def log_message(self, _format, *_args):
        return

    def _send(self, status, body=b"", headers=()):
        self.send_response(status)
        for name, value in headers:
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if body:
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        self.requests.append((self.path, dict(self.headers.items()), body))
        self._send(200, b'{"reply":"ok"}', (("Content-Type", "application/json"),))

    def do_GET(self):
        if self.path == "/redirect":
            self._send(302, headers=(("Location", "/should-not-follow"),))
        elif self.path == "/should-not-follow":
            self._send(200, b"redirect followed")
        elif self.path == "/status":
            self._send(503, b'{"error":"busy"}', (("Content-Type", "application/json"),))
        elif self.path == "/large":
            self._send(200, b"x" * 64)
        elif self.path == "/headers":
            self._send(200, b"ok", (("X-Large", "x" * 32760),))
        elif self.path == "/slow":
            time.sleep(0.35)
            self._send(200, b"late")
        else:
            self._send(200, b"ok")


def _start_server(handler, *, tls=False, certificate=None, key=None):
    handler.requests = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    if tls:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(certificate, key)
        server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    scheme = "https" if tls else "http"
    address = f"{scheme}://127.0.0.1:{server.server_port}"
    return server, thread, address


def _run_source(source, selfhost, directory):
    compile_flags, link_flags = NativeToolchain._curl_flags()
    results = []
    for engine in ("interpreter", "ast", "hir", "selfhost"):
        engine_compile_flags = compile_flags if engine != "interpreter" else ()
        if engine == "selfhost":
            engine_compile_flags = (*engine_compile_flags, "-DWYSNA_HAS_HTTP=1")
        results.append(run_engine(engine, source, "http-contract", selfhost,
                                  Path(directory) / engine,
                                  extra_compile_flags=engine_compile_flags,
                                  extra_link_flags=link_flags if engine != "interpreter" else ()))
    return results


def test_http_task_groups_bound_and_close_loopback_work():
    class GroupHandler(_Handler):
        arrivals = threading.Barrier(2)
        lock = threading.Lock()
        requests = []
        active = 0
        peak = 0

        def do_GET(self):
            cls = type(self)
            counts = self.path != '/slow' and self.path != '/never'
            with cls.lock:
                cls.requests.append(self.path)
                cls.active += int(counts)
                cls.peak = max(cls.peak, cls.active)
            try:
                if self.path in ('/barrier-a', '/barrier-b'):
                    cls.arrivals.wait(timeout=10)
                if self.path == '/slow':
                    time.sleep(0.6)
                self._send(200, b'group')
            finally:
                with cls.lock:
                    cls.active -= int(counts)

    server, _, address = _start_server(GroupHandler)
    try:
        with tempfile.TemporaryDirectory(prefix='wysna-http-group-') as directory:
            source = """
import "std/http"
from "std/async" use { TaskOutcome }
fn show(outcomes: list[TaskOutcome[http.Response, http.HttpError]]) {
    for outcome in outcomes {
        match outcome {
            Completed(response) => {
                print(response.status)
                response.body.close()
            },
            Failed(_) => print("failed"),
            Cancelled => print("cancelled"),
            DeadlineExceeded => print("deadline"),
        }
    }
}
show(http.batch_get_group(["URL/barrier-a", "URL/barrier-b", "URL/queued"], 2, 10000, false) else error { [] })
show(http.batch_get_group(["URL/never"], 1, 0, false) else error { [] })
show(http.batch_get_group(["invalid", "URL/never"], 1, -1, true) else error { [] })
show(http.batch_get_group(["URL/slow", "URL/never"], 1, 80, false) else error { [] })
""".replace('URL', address)
            results = _run_source(source, ensure_selfhost_compiler_binary(str(ROOT)), directory)
            for result in results:
                assert result.runtime_ok, (result.engine, result.stderr)
                assert result.stdout == '200\n200\n200\ndeadline\nfailed\ncancelled\ndeadline\ndeadline\n', (
                    result.engine, result.stdout, result.stderr,
                )
            assert GroupHandler.peak == 2
            assert '/never' not in GroupHandler.requests
            assert GroupHandler.requests.count('/queued') == 4
    finally:
        server.shutdown()
        server.server_close()


def test_http_client_contract_across_four_engines():
    if not shutil.which("curl-config"):
        raise AssertionError("native HTTP conformance requires curl-config")
    try:
        import pycurl
    except ImportError as error:
        raise AssertionError("interpreter HTTP conformance requires the wysna[http] extra") from error
    if int(pycurl.version_info()[2]) < 0x080300:
        raise AssertionError("HTTP conformance requires libcurl 8.3.0 or newer")
    openssl = shutil.which("openssl")
    if not openssl:
        raise AssertionError("TLS conformance requires openssl to create a local test certificate")

    previous_no_proxy = os.environ.get("NO_PROXY")
    previous_no_proxy_lower = os.environ.get("no_proxy")
    bypass = "127.0.0.1,localhost"
    os.environ["NO_PROXY"] = ",".join(filter(None, (previous_no_proxy, bypass)))
    os.environ["no_proxy"] = ",".join(filter(None, (previous_no_proxy_lower, bypass)))
    http_server, _, http_url = _start_server(_Handler)
    try:
        with tempfile.TemporaryDirectory(prefix="wysna-http-tls-") as tls_directory:
            certificate = Path(tls_directory) / "certificate.pem"
            key = Path(tls_directory) / "key.pem"
            generated = subprocess.run(
                [openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes",
                 "-keyout", str(key), "-out", str(certificate), "-days", "1",
                 "-subj", "/CN=localhost"],
                capture_output=True, text=True, timeout=30,
            )
            if generated.returncode != 0:
                raise AssertionError("could not create local TLS fixture: " + generated.stderr)
            tls_server, _, tls_url = _start_server(
                _Handler, tls=True, certificate=str(certificate), key=str(key),
            )
            try:
                example = (ROOT / "examples/http_json_client.wysna").read_text(encoding="utf-8")
                CompilerService(project_root=ROOT).check(
                    example, file="examples/http_json_client.wysna",
                )
                source = f'''import "std/buffer"
import "std/http"
import "std/json"

fn main() -> unit ! http.HttpError | json.JsonError {{
    let headers: list[http.Header] = [http.make_header("X-Test", "fixture")]
    let payload = json.object({{"prompt": json.string("hello")}})
    let echoed = http.post_json("{http_url}/echo", headers, payload)?
    let content_type = http.header(echoed, "content-type") ?? "missing"
    let body = http.response_json(echoed)?
    let body_text = json.stringify(body)?
    print("ECHO", echoed.status, content_type, body_text)
    echoed.body.close()
    match http.response_text(echoed) {{
        Ok(text) => print("CLOSED-BODY-ACCEPTED"),
        Err(error) => match error.kind {{
            InvalidResponse => print("CLOSED-BODY", error.code),
            _ => print("CLOSED-BODY-WRONG"),
        }},
    }}

    let redirect = http.get("{http_url}/redirect")?
    print("REDIRECT", redirect.status)
    redirect.body.close()
    let error_status = http.get("{http_url}/status")?
    print("STATUS", error_status.status)
    error_status.body.close()

    let empty = buffer.Bytes()
    let large = http.request("{http_url}/large", "GET", [], empty, 30000, 16)
    empty.close()
    match large {{
        Ok(response) => {{ response.body.close() print("LIMIT-MISSING") }},
        Err(error) => match error.kind {{
            LimitExceeded => print("BODY-LIMIT-OK"),
            _ => print("BODY-LIMIT-WRONG"),
        }},
    }}

    let header_limit = http.get("{http_url}/headers")
    match header_limit {{
        Ok(response) => {{ response.body.close() print("HEADER-LIMIT-MISSING") }},
        Err(error) => match error.kind {{
            LimitExceeded => print("HEADER-LIMIT-OK"),
            _ => print("HEADER-LIMIT-WRONG"),
        }},
    }}

    let timeout_body = buffer.Bytes()
    let slow = http.request("{http_url}/slow", "GET", [], timeout_body, 100, 1024)
    timeout_body.close()
    match slow {{
        Ok(response) => {{ response.body.close() print("TIMEOUT-MISSING") }},
        Err(error) => match error.kind {{
            Timeout => print("TIMEOUT-OK"),
            _ => print("TIMEOUT-WRONG"),
        }},
    }}

    let tls = http.get("{tls_url}/")
    match tls {{
        Ok(response) => {{ response.body.close() print("TLS-MISSING") }},
        Err(error) => match error.kind {{
            Tls => print("TLS-VERIFY-OK"),
            _ => print("TLS-VERIFY-WRONG"),
        }},
    }}
}}

main()
'''
                CompilerService(project_root=ROOT).check(source, file="http-contract.wysna")
                selfhost = ensure_selfhost_compiler_binary(str(ROOT))
                with tempfile.TemporaryDirectory(prefix="wysna-http-engines-") as directory:
                    results = _run_source(source, selfhost, directory)
                expected_lines = [
                    'ECHO 200 application/json {"reply":"ok"}',
                    "CLOSED-BODY 0",
                    "REDIRECT 302",
                    "STATUS 503",
                    "BODY-LIMIT-OK",
                    "HEADER-LIMIT-OK",
                    "TIMEOUT-OK",
                    "TLS-VERIFY-OK",
                ]
                expected = "\n".join(expected_lines) + "\n"
                for engine, result in zip(("interpreter", "ast", "hir", "selfhost"), results):
                    assert (result.exit_code, result.stdout, result.stderr) == (0, expected, ""), (
                        f"{engine} HTTP contract mismatch: exit={result.exit_code}, "
                        f"stdout={result.stdout!r}, stderr={result.stderr!r}"
                    )
                assert len(_Handler.requests) == 4, "redirect response was followed or requests were repeated"
                for path, headers, body in _Handler.requests:
                    if path == "/echo":
                        assert headers.get("Content-Type") == "application/json"
                        assert headers.get("X-Test") == "fixture"
                        assert json.loads(body) == {"prompt": "hello"}
            finally:
                tls_server.shutdown()
                tls_server.server_close()
    finally:
        http_server.shutdown()
        http_server.server_close()
        if previous_no_proxy is None:
            os.environ.pop("NO_PROXY", None)
        else:
            os.environ["NO_PROXY"] = previous_no_proxy
        if previous_no_proxy_lower is None:
            os.environ.pop("no_proxy", None)
        else:
            os.environ["no_proxy"] = previous_no_proxy_lower


def test_automation_fetch_publishes_atomically_and_preserves_prior_batch():
    if not shutil.which("curl-config"):
        raise AssertionError("native HTTP conformance requires curl-config")
    import sys

    previous_no_proxy = os.environ.get("NO_PROXY")
    previous_no_proxy_lower = os.environ.get("no_proxy")
    bypass = "127.0.0.1,localhost"
    os.environ["NO_PROXY"] = ",".join(filter(None, (previous_no_proxy, bypass)))
    os.environ["no_proxy"] = ",".join(filter(None, (previous_no_proxy_lower, bypass)))

    overlap_barrier = threading.Barrier(2, timeout=10)

    class FetchHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, _format, *_args):
            return

        def _reply(self, body: bytes):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path == "/batch-a":
                self._reply(b'[{"id":1,"value":11}]')
            elif self.path == "/batch-b":
                self._reply(b'[{"id":2,"value":22}]')
            elif self.path == "/race-a":
                overlap_barrier.wait()
                self._reply(b'[{"id":1,"value":101}]')
            elif self.path == "/race-b":
                overlap_barrier.wait()
                self._reply(b'[{"id":2,"value":202}]')
            else:
                self._reply(b'[]')

    server, _, base_url = _start_server(FetchHandler)
    try:
        fetch_source = ROOT / "examples/automation-sync/fetch.wysna"
        CompilerService(project_root=ROOT).check_file(fetch_source)
        selfhost = ensure_selfhost_compiler_binary(str(ROOT))
        compile_flags, link_flags = NativeToolchain._curl_flags()
        with tempfile.TemporaryDirectory(prefix="wysna-atomic-fetch-") as temporary:
            work = Path(temporary)
            seed_bin = work / ("fetch-seed.exe" if os.name == "nt" else "fetch-seed")
            built_seed = subprocess.run(
                [sys.executable, str(ROOT / "main.py"), "build", str(fetch_source), "-o", str(seed_bin)],
                cwd=ROOT, capture_output=True, text=True, timeout=120,
            )
            assert built_seed.returncode == 0, built_seed.stderr

            selfhost_c = work / "fetch_selfhost.c"
            selfhost_bin = work / ("fetch-selfhost.exe" if os.name == "nt" else "fetch-selfhost")
            emit_selfhost = subprocess.run(
                [str(selfhost), "--stdlib-root", str(ROOT / "stdlib"), str(fetch_source), str(selfhost_c)],
                cwd=ROOT, capture_output=True, text=True, timeout=120,
            )
            assert emit_selfhost.returncode == 0, emit_selfhost.stderr
            compiled_selfhost = subprocess.run(
                compiler_command() + ["-std=c99", "-O2", "-I" + str(ROOT), *compile_flags,
                                      "-DWYSNA_HAS_HTTP=1", str(selfhost_c), "-o", str(selfhost_bin),
                                      *link_flags],
                cwd=ROOT, capture_output=True, text=True, timeout=120,
            )
            assert compiled_selfhost.returncode == 0, compiled_selfhost.stderr

            run_env = dict(os.environ, WYSNA_MEM_CHECK="1")
            for label, binary in (("seed", seed_bin), ("selfhost", selfhost_bin)):
                case_dir = work / label
                case_dir.mkdir()
                dest = case_dir / "measurements.json"
                prior_bytes = b'[{"id":1,"value":7}]\n'
                dest.write_bytes(prior_bytes)

                # 1. Publish failure (destination is a directory): cleans up own temp file and exits 5.
                blocked_dir = case_dir / "blocked-dir"
                blocked_dir.mkdir()
                blocked_res = subprocess.run(
                    [str(binary), base_url + "/batch-a", str(blocked_dir)],
                    cwd=case_dir, env=run_env, capture_output=True, text=True, timeout=30,
                )
                assert blocked_res.returncode == 5, (label, blocked_res)
                assert sorted(p.name for p in case_dir.iterdir()) == ["blocked-dir", "measurements.json"]
                assert dest.read_bytes() == prior_bytes

                # 2. Mid-write failure on POSIX via RLIMIT_FSIZE: temp created, write fails, temp cleaned, prior intact.
                if os.name == "posix":
                    import resource
                    import signal

                    def _limit_fsize():
                        signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
                        resource.setrlimit(resource.RLIMIT_FSIZE, (8, 8))

                    failed_write = subprocess.run(
                        [str(binary), base_url + "/batch-a", str(dest)],
                        cwd=case_dir, env=run_env, capture_output=True, text=True, timeout=30,
                        preexec_fn=_limit_fsize,
                    )
                    assert failed_write.returncode == 5, (label, failed_write)
                    assert sorted(p.name for p in case_dir.iterdir()) == ["blocked-dir", "measurements.json"]
                    assert dest.read_bytes() == prior_bytes

                # 3. Two simultaneous downloads targeting the same file: distinct exclusive temps, atomic valid result.
                proc_a = subprocess.Popen(
                    [str(binary), base_url + "/race-a", str(dest)],
                    cwd=case_dir, env=run_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                proc_b = subprocess.Popen(
                    [str(binary), base_url + "/race-b", str(dest)],
                    cwd=case_dir, env=run_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                )
                out_a, err_a = proc_a.communicate(timeout=30)
                out_b, err_b = proc_b.communicate(timeout=30)
                assert (proc_a.returncode, out_a, err_a) == (0, "", ""), (label, proc_a.returncode, out_a, err_a)
                assert (proc_b.returncode, out_b, err_b) == (0, "", ""), (label, proc_b.returncode, out_b, err_b)
                assert sorted(p.name for p in case_dir.iterdir()) == ["blocked-dir", "measurements.json"]
                assert dest.read_bytes() in (b'[{"id":1,"value":101}]', b'[{"id":2,"value":202}]')

                # 4. Sequential replacement updates destination cleanly with zero temp leftovers.
                update_res = subprocess.run(
                    [str(binary), base_url + "/batch-b", str(dest)],
                    cwd=case_dir, env=run_env, capture_output=True, text=True, timeout=30,
                )
                assert (update_res.returncode, update_res.stdout, update_res.stderr) == (0, "", "")
                assert dest.read_bytes() == b'[{"id":2,"value":22}]'
                assert sorted(p.name for p in case_dir.iterdir()) == ["blocked-dir", "measurements.json"]
    finally:
        server.shutdown()
        server.server_close()
        if previous_no_proxy is None:
            os.environ.pop("NO_PROXY", None)
        else:
            os.environ["NO_PROXY"] = previous_no_proxy
        if previous_no_proxy_lower is None:
            os.environ.pop("no_proxy", None)
        else:
            os.environ["no_proxy"] = previous_no_proxy_lower


if __name__ == "__main__":
    test_http_client_contract_across_four_engines()
    test_http_task_groups_bound_and_close_loopback_work()
    test_automation_fetch_publishes_atomically_and_preserves_prior_batch()
    print("HTTP client: loopback contract passed across four engines")
