"""Loopback host serving shared-browser-app static UI/Wasm assets alongside SQLite worker API."""

from __future__ import annotations

import asyncio
import json
import mimetypes
from pathlib import Path
import time
from typing import Any

from tooling.ServiceHost import (
    MAX_REQUEST_HEADERS_BYTES,
    MAX_WORKER_REQUEST_BYTES,
    ServiceConfig,
    ServiceSaturated,
    ServiceStopping,
    WorkerFailed,
    WorkerOutputLimit,
    WorkerSupervisor,
    WorkerTimedOut,
    _SAFE_REQUEST_HEADERS,
    _json_error,
    _json_response,
    _parse_worker_response,
)

_STATIC_MIME_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".mjs": "application/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".wasm": "application/wasm",
    ".json": "application/json; charset=utf-8",
}


def _resolve_static_asset(static_root: Path, url_path: str) -> Path | None:
    """Confine static file serving strictly to `/web/...` and `/build/...` under `static_root`."""
    clean = url_path.split("?", 1)[0]
    if clean in ("/", "/web", "/web/"):
        clean = "/web/index.html"
    if not (clean.startswith("/web/") or clean.startswith("/build/")):
        return None
    rel = clean.lstrip("/")
    if not rel or "\x00" in rel:
        return None
    root_resolved = static_root.resolve()
    candidate = (root_resolved / rel).resolve()
    if not candidate.is_relative_to(root_resolved):
        return None
    if not (candidate.is_relative_to((root_resolved / "web").resolve()) or candidate.is_relative_to((root_resolved / "build").resolve())):
        return None
    if not candidate.is_file():
        return None
    return candidate


async def serve_app(config: ServiceConfig, static_root: Path) -> tuple[Any, str, WorkerSupervisor]:
    """Start loopback aiohttp app server and return `(runner, base_url, supervisor)`."""
    from aiohttp import web

    config = config.validate()
    supervisor = WorkerSupervisor(config)

    async def handle(request: web.Request) -> web.StreamResponse:
        if request.method in ("GET", "HEAD") and not (
            request.path == "/health" or request.path == "/quotes" or request.path.startswith("/quotes/")
        ):
            asset = _resolve_static_asset(static_root, request.path)
            if asset is not None:
                ctype = _STATIC_MIME_TYPES.get(asset.suffix.lower()) or mimetypes.guess_type(asset.name)[0] or "application/octet-stream"
                payload = b"" if request.method == "HEAD" else asset.read_bytes()
                return web.Response(status=200, body=payload, headers={"Content-Type": ctype, "Cache-Control": "no-store"})
            if request.path.startswith("/web/") or request.path.startswith("/build/"):
                return web.Response(status=404, text="not found")

        header_bytes = sum(len(name) + len(value) + 4 for name, value in request.raw_headers)
        if header_bytes > MAX_REQUEST_HEADERS_BYTES:
            return _json_response(431, _json_error(431, "request_headers_too_large"))

        loop = asyncio.get_running_loop()
        deadline = loop.time() + config.request_timeout_ms / 1000
        try:
            body = await asyncio.wait_for(request.read(), timeout=max(0.001, deadline - loop.time()))
            try:
                body_text = body.decode("utf-8")
            except UnicodeDecodeError:
                return _json_response(400, _json_error(400, "request_body_must_be_utf8"))
            headers = {
                name.lower(): value
                for name, value in request.headers.items()
                if name.lower() in _SAFE_REQUEST_HEADERS
            }
            worker_request = json.dumps(
                {
                    "method": request.method,
                    "path": request.raw_path.split("?", 1)[0],
                    "headers": headers,
                    "body": body_text,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(worker_request) > MAX_WORKER_REQUEST_BYTES:
                return _json_response(413, _json_error(413, "worker_request_limit"))
            job = supervisor.submit(worker_request, time.monotonic() + max(0.0, deadline - loop.time()))
            raw_response = await asyncio.wait_for(asyncio.wrap_future(job.future), timeout=max(0.001, deadline - loop.time()))
        except ServiceSaturated:
            return _json_response(503, _json_error(503, "service_busy"))
        except ServiceStopping:
            return _json_response(503, _json_error(503, "service_stopping"))
        except WorkerTimedOut:
            return _json_response(504, _json_error(504, "worker_timeout"))
        except (WorkerFailed, WorkerOutputLimit, OSError):
            return _json_response(502, _json_error(502, "worker_failed"))

        try:
            status, payload = _parse_worker_response(raw_response)
        except WorkerFailed:
            return _json_response(502, _json_error(502, "worker_invalid_response"))
        return _json_response(status, payload)

    app = web.Application(client_max_size=MAX_WORKER_REQUEST_BYTES + 4096)
    app.router.add_route("*", "/{tail:.*}", handle)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, config.host, config.port, backlog=config.backlog)
    await site.start()
    bound_port = site._server.sockets[0].getsockname()[1]
    return runner, f"http://{config.host}:{bound_port}", supervisor
