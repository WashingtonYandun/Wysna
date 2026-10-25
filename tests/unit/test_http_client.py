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

    let redirect = http.get("{http_url}/redirect")?
    print("REDIRECT", redirect.status)
    redirect.body.close()
    let error_status = http.get("{http_url}/status")?
    print("STATUS", error_status.status)
    error_status.body.close()

    let empty = new buffer.Bytes()
    let large = http.request("{http_url}/large", "GET", [], empty, 30000, 16)
    empty.close()
    match large {{
        Ok(response) => {{ response.body.close() print("LIMIT-MISSING") }},
        Err(error) => match error.kind {{
            LimitExceeded => print("BODY-LIMIT-OK"),
            else => print("BODY-LIMIT-WRONG"),
        }},
    }}

    let header_limit = http.get("{http_url}/headers")
    match header_limit {{
        Ok(response) => {{ response.body.close() print("HEADER-LIMIT-MISSING") }},
        Err(error) => match error.kind {{
            LimitExceeded => print("HEADER-LIMIT-OK"),
            else => print("HEADER-LIMIT-WRONG"),
        }},
    }}

    let timeout_body = new buffer.Bytes()
    let slow = http.request("{http_url}/slow", "GET", [], timeout_body, 100, 1024)
    timeout_body.close()
    match slow {{
        Ok(response) => {{ response.body.close() print("TIMEOUT-MISSING") }},
        Err(error) => match error.kind {{
            Timeout => print("TIMEOUT-OK"),
            else => print("TIMEOUT-WRONG"),
        }},
    }}

    let tls = http.get("{tls_url}/")
    match tls {{
        Ok(response) => {{ response.body.close() print("TLS-MISSING") }},
        Err(error) => match error.kind {{
            Tls => print("TLS-VERIFY-OK"),
            else => print("TLS-VERIFY-WRONG"),
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


if __name__ == "__main__":
    test_http_client_contract_across_four_engines()
    print("HTTP client: loopback contract passed across four engines")
