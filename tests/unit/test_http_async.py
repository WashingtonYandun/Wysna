"""Unit tests for non-blocking HTTP multi-dispatch (Phase 1.1)."""
import os
import shutil
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import pytest

from compiler.CompilerService import CompilerService
from compiler.NativeToolchain import NativeToolchain
from tests.engine_harness import run_engine
from main import ensure_selfhost_compiler_binary


ROOT = Path(__file__).resolve().parents[2]


class _AsyncServerHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, _format, *_args):
        return

    def do_GET(self):
        if self.path == "/slow1":
            time.sleep(0.25)
            self._respond(200, b'{"id":1}')
        elif self.path == "/slow2":
            time.sleep(0.25)
            self._respond(200, b'{"id":2}')
        elif self.path == "/slow3":
            time.sleep(0.25)
            self._respond(200, b'{"id":4}')
        elif self.path == "/fast":
            self._respond(200, b'{"id":3}')
        elif self.path == "/fail":
            self.send_response(500)
            self.end_headers()
        else:
            self._respond(200, b'ok')

    def _respond(self, status, body):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


@pytest.fixture(scope="module")
def async_http_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _AsyncServerHandler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    prev_no_proxy = os.environ.get("NO_PROXY")
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    try:
        yield base_url
    finally:
        server.shutdown()
        server.server_close()
        if prev_no_proxy is None:
            os.environ.pop("NO_PROXY", None)
        else:
            os.environ["NO_PROXY"] = prev_no_proxy


def test_batch_get_concurrency_and_ordering(async_http_server):
    """Verifies that batch_get executes concurrently and preserves original order."""
    source = f'''import "std/http"

fn main() -> unit {{
    let urls = [
        "{async_http_server}/slow1",
        "{async_http_server}/fast",
        "{async_http_server}/slow2",
        "{async_http_server}/slow3"
    ]
    let results = http.batch_get(urls)
    print("COUNT", len(results))
    for i in 0..len(results) {{
        let r = results[i]
        match r.response {{
            Some(resp) => {{
                let body = http.response_text(resp) else err {{ "ERR" }}
                print(r.url, r.ok, resp.status, body)
                let _ = resp.body.close()
            }},
            None => {{ print(r.url, r.ok, "NONE") }},
        }}
    }}
}}

main()
'''
    CompilerService(project_root=ROOT).check(source, file="batch_test.wysna")

    start_time = time.time()
    result = run_engine("interpreter", source, "batch_test", "", Path(tempfile.gettempdir()))
    elapsed = time.time() - start_time

    assert result.exit_code == 0, f"Interpreter error: {result.stderr}"
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "COUNT 4"
    assert lines[1] == f"{async_http_server}/slow1 true 200 {{\"id\":1}}"
    assert lines[2] == f"{async_http_server}/fast true 200 {{\"id\":3}}"
    assert lines[3] == f"{async_http_server}/slow2 true 200 {{\"id\":2}}"
    assert lines[4] == f"{async_http_server}/slow3 true 200 {{\"id\":4}}"
    # 3 slow requests at 0.25s each would take >= 0.75s sequentially.
    # Concurrent execution completes in ~0.35s total elapsed time.
    assert elapsed < 0.65, f"Batch requests did not run concurrently; took {elapsed:.2f}s"


def test_batch_get_across_native_engines(async_http_server):
    """Verifies batch_get across interpreter, ast, and hir C99 native engines."""
    if not shutil.which("curl-config"):
        pytest.skip("curl-config required for native test")

    source = f'''import "std/http"

fn main() -> unit {{
    let urls = [
        "{async_http_server}/fast",
        "{async_http_server}/slow1"
    ]
    let results = http.batch_get(urls)
    for i in 0..len(results) {{
        let r = results[i]
        match r.response {{
            Some(resp) => {{
                print(r.ok, resp.status)
                let _ = resp.body.close()
            }},
            None => {{ print("NONE") }},
        }}
    }}
}}

main()
'''
    compile_flags, link_flags = NativeToolchain._curl_flags()
    selfhost = ensure_selfhost_compiler_binary(str(ROOT))

    with tempfile.TemporaryDirectory(prefix="wysna-async-engines-") as tmpdir:
        for engine in ("interpreter", "ast", "hir"):
            extra_cflags = compile_flags if engine != "interpreter" else ()
            extra_lflags = link_flags if engine != "interpreter" else ()
            res = run_engine(
                engine,
                source,
                "batch_native",
                selfhost,
                Path(tmpdir) / engine,
                extra_compile_flags=extra_cflags,
                extra_link_flags=extra_lflags,
            )
            assert res.exit_code == 0, f"{engine} failed: {res.stderr}"
            assert res.stdout == "true 200\ntrue 200\n", f"{engine} mismatch: {res.stdout}"


def test_get_async_poll_finish_and_cancel(async_http_server):
    """Verifies low-level get_async, poll, finish, and cancel lifecycle."""
    source = f'''import "std/http"

fn main() -> unit {{
    let req1 = http.get_async("{async_http_server}/fast") else err {{
        return
    }}
    let mut done = false
    while not done {{
        let st = http.poll(req1)
        if st != 0 {{
            done = true
        }}
    }}
    let resp = http.finish(req1) else err {{
        return
    }}
    print("ASYNC_GET", resp.status)
    let _ = resp.body.close()

    let req2 = http.get_async("{async_http_server}/slow1") else err {{
        return
    }}
    let cancelled = http.cancel(req2)
    print("CANCELLED", cancelled)
}}

main()
'''
    CompilerService(project_root=ROOT).check(source, file="async_lifecycle.wysna")
    result = run_engine("interpreter", source, "async_lifecycle", "", Path(tempfile.gettempdir()))
    assert result.exit_code == 0, f"Error: {result.stderr}"
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "ASYNC_GET 200"
    assert lines[1] == "CANCELLED 1"

