"""End-to-end persistence, reload, artifact size, p95 latency, and RSS campaign for shared-browser-app."""

from __future__ import annotations

import asyncio
from contextlib import closing
import gzip
import json
import os
from pathlib import Path
import resource
import sqlite3
import sys
import tempfile
import threading
import time
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT / "src") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "src"))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
APP_DIR = REPO_ROOT / "examples" / "shared-browser-app"
if str(APP_DIR) not in sys.path:
    sys.path.insert(0, str(APP_DIR))

from common.Version import WYSNA_VERSION
from compiler.CompilerService import CompilerService
from compiler.NativeToolchain import NativeToolchain
from service.app_server import serve_app
from tooling.ServiceHost import ServiceConfig


def _percentile(sorted_values: list[float], pct: float) -> float:
    if not sorted_values:
        return 0.0
    index = min(len(sorted_values) - 1, max(0, int((len(sorted_values) - 1) * pct)))
    return round(sorted_values[index], 2)


def _direct_http(url: str, *, method: str = "GET", body: bytes | None = None, accept_gzip: bool = False) -> tuple[int, dict[str, str], bytes]:
    opener = build_opener(ProxyHandler({}))
    headers = {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if accept_gzip:
        headers["Accept-Encoding"] = "gzip"
    req = Request(url, data=body, method=method, headers=headers)
    try:
        with opener.open(req, timeout=15) as resp:
            return resp.status, dict(resp.headers.items()), resp.read()
    except HTTPError as err:
        return err.code, dict(err.headers.items()), err.read()


def _measure_asset_footprint(project_dir: Path) -> dict[str, dict[str, int]]:
    assets: dict[str, dict[str, int]] = {}
    for folder in ("web", "build"):
        target_dir = project_dir / folder
        if not target_dir.is_dir():
            continue
        for item in sorted(target_dir.iterdir()):
            if item.is_file():
                raw = item.read_bytes()
                compressed = gzip.compress(raw, compresslevel=6)
                assets[f"{folder}/{item.name}"] = {
                    "raw_bytes": len(raw),
                    "gzip_bytes": len(compressed),
                }
    return assets


def _start_threaded_app_server(worker: Path, database: Path, static_root: Path):
    ready = threading.Event()
    state: dict[str, object] = {}

    def _run():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        config = ServiceConfig(worker=worker, database=database, host="127.0.0.1", port=0, workers=2, queue_size=8)
        runner, endpoint, supervisor = loop.run_until_complete(serve_app(config, static_root))
        stop_event = asyncio.Event()
        state.update(loop=loop, runner=runner, endpoint=endpoint, supervisor=supervisor, stop_event=stop_event)
        ready.set()
        loop.run_until_complete(stop_event.wait())
        supervisor.close()
        loop.run_until_complete(runner.cleanup())
        loop.close()

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    ready.wait(timeout=15)
    return thread, state


def _stop_threaded_app_server(thread: threading.Thread, state: dict[str, object]) -> None:
    loop = state["loop"]
    stop_event = state["stop_event"]
    loop.call_soon_threadsafe(stop_event.set)
    thread.join(timeout=15)


def run_persistence_and_load_check(project_dir: Path = APP_DIR, *, iterations: int = 20) -> dict[str, object]:
    compiler_service = CompilerService(project_root=str(REPO_ROOT))
    checked = compiler_service.check_file(project_dir / "src" / "service_worker.wysna")
    c_source = compiler_service.emit_c(checked).source
    adapter = project_dir / "service" / "quotes_adapter.c"

    with tempfile.TemporaryDirectory(prefix="wysna-h3-e2e-") as tmpdir:
        work = Path(tmpdir)
        worker = work / ("quotes_worker.exe" if os.name == "nt" else "quotes_worker")
        database = work / "quotes.sqlite"
        NativeToolchain(REPO_ROOT).build(c_source, worker, link_libraries=(adapter, "sqlite3"))

        thread, state = _start_threaded_app_server(worker, database, project_dir)
        try:
            endpoint = str(state["endpoint"])
            # 1. Verify UI HTML/JS assets are served directly alongside API.
            st, _, html_bytes = _direct_http(endpoint + "/web/")
            assert st == 200 and b"save-quote" in html_bytes and b"Shared Wysna pricing" in html_bytes
            st_js, _, quote_js_bytes = _direct_http(endpoint + "/web/quote.js")
            assert st_js == 200 and b"saveQuoteToServer" in quote_js_bytes

            # 2. Verify invalid input is rejected with HTTP 400 and leaves SQLite untouched.
            for bad_body in (
                b'{"quantity":0,"unit_price_cents":1299}',
                b'{"quantity":1001,"unit_price_cents":1299}',
                b'{"quantity":3,"unit_price_cents":-1}',
                b'{"quantity":3,"unit_price_cents":100000001}',
            ):
                bad_st, _, _ = _direct_http(endpoint + "/quotes", method="POST", body=bad_body)
                assert bad_st == 400, (bad_body, bad_st)

            q0_st, _, q0_body = _direct_http(endpoint + "/quotes")
            assert q0_st == 200 and json.loads(q0_body.decode("utf-8")) == {"count": 0}
            with closing(sqlite3.connect(database)) as conn:
                count_before = conn.execute("SELECT count(*) FROM quotes").fetchone()[0]
            assert count_before == 0

            # 3. Simulate save (POST /quotes) + measured write & read latency benchmark.
            write_latencies_ms: list[float] = []
            read_latencies_ms: list[float] = []
            saved_ids: list[int] = []

            t0 = time.perf_counter()
            st_post, _, body_post = _direct_http(
                endpoint + "/quotes",
                method="POST",
                body=b'{"quantity":3,"unit_price_cents":1299}',
            )
            write_latencies_ms.append((time.perf_counter() - t0) * 1000.0)
            assert st_post == 201, (st_post, body_post)
            saved = json.loads(body_post.decode("utf-8"))
            assert saved == {"id": 1, "quantity": 3, "unit_price_cents": 1299, "total_cents": 3897}
            saved_ids.append(saved["id"])

            for i in range(1, iterations):
                w0 = time.perf_counter()
                w_st, _, w_body = _direct_http(
                    endpoint + "/quotes",
                    method="POST",
                    body=f'{{"quantity":{(i % 50) + 1},"unit_price_cents":1299}}'.encode("utf-8"),
                )
                write_latencies_ms.append((time.perf_counter() - w0) * 1000.0)
                assert w_st == 201
                saved_ids.append(json.loads(w_body.decode("utf-8"))["id"])

            for qid in saved_ids:
                r0 = time.perf_counter()
                r_st, _, r_body = _direct_http(endpoint + f"/quotes/{qid}")
                read_latencies_ms.append((time.perf_counter() - r0) * 1000.0)
                assert r_st == 200 and json.loads(r_body.decode("utf-8"))["id"] == qid
        finally:
            _stop_threaded_app_server(thread, state)

        # 4. Restart server against the same SQLite DB and verify reload `?quote=1` path retrieves 3897.
        thread2, state2 = _start_threaded_app_server(worker, database, project_dir)
        try:
            endpoint2 = str(state2["endpoint"])
            page_st, _, page_bytes = _direct_http(endpoint2 + "/web/?quote=1")
            assert page_st == 200 and b"app.js" in page_bytes
            get_st, _, get_body = _direct_http(endpoint2 + "/quotes/1")
            assert get_st == 200
            assert json.loads(get_body.decode("utf-8")) == {
                "id": 1,
                "quantity": 3,
                "unit_price_cents": 1299,
                "total_cents": 3897,
            }
        finally:
            _stop_threaded_app_server(thread2, state2)

    rss_raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    rss_kb = int(rss_raw / 1024) if sys.platform == "darwin" else int(rss_raw)
    write_sorted = sorted(write_latencies_ms)
    read_sorted = sorted(read_latencies_ms)
    return {
        "schema": "wysna.shared-browser-e2e/1",
        "wysna_version": WYSNA_VERSION,
        "iterations": iterations,
        "assets": _measure_asset_footprint(project_dir),
        "write_p50_ms": _percentile(write_sorted, 0.50),
        "write_p95_ms": _percentile(write_sorted, 0.95),
        "read_p50_ms": _percentile(read_sorted, 0.50),
        "read_p95_ms": _percentile(read_sorted, 0.95),
        "host_max_rss_kb": rss_kb,
        "passed": True,
    }


if __name__ == "__main__":
    print(json.dumps(run_persistence_and_load_check(), indent=2))
