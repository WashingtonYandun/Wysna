"""Bounded loopback HTTP host for isolated native Wysna service workers."""

from __future__ import annotations

import asyncio
from concurrent.futures import Future
from dataclasses import dataclass, field
import ipaddress
import json
import os
from pathlib import Path
import queue
import signal
import subprocess
import tempfile
import threading
import time
from typing import Any

from runtime.HostAbi import attach_process_tree, release_process_tree, terminate_process_tree


MAX_REQUEST_BODY_BYTES = 1_048_576
MAX_REQUEST_HEADERS_BYTES = 32_768
MAX_REQUEST_HEADERS = 100
MAX_REQUEST_LINE_BYTES = 8_192
MAX_RESPONSE_BYTES = 1_048_576
MAX_WORKER_REQUEST_BYTES = MAX_REQUEST_BODY_BYTES + MAX_REQUEST_HEADERS_BYTES + MAX_REQUEST_LINE_BYTES
MAX_WORKER_COUNT = 8
MAX_QUEUE_SIZE = 32
DEFAULT_WORKERS = 4
DEFAULT_QUEUE_SIZE = 32
DEFAULT_REQUEST_TIMEOUT_MS = 15_000
DEFAULT_SHUTDOWN_TIMEOUT_MS = 10_000
DEFAULT_CONNECTION_LIMIT = 64
DEFAULT_BACKLOG = 32
DEFAULT_KEEPALIVE_TIMEOUT_SECONDS = 5
MAX_REQUEST_HEADER_FIELD_BYTES = 320
_SAFE_REQUEST_HEADERS = frozenset({"accept", "content-type", "x-request-id"})
_ALLOWED_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


class ServiceHostError(Exception):
    """A configuration or lifecycle error in the local service host."""


class ServiceSaturated(Exception):
    """The bounded worker queue has no capacity for another request."""


class WorkerTimedOut(Exception):
    """A native worker exceeded its end-to-end request budget."""


class WorkerFailed(Exception):
    """A native worker exited or returned an invalid result."""


class WorkerOutputLimit(Exception):
    """A native worker attempted to exceed the response byte ceiling."""


class ServiceStopping(Exception):
    """The server is shutting down and did not accept the request."""


@dataclass(frozen=True)
class ServiceConfig:
    worker: Path
    database: Path
    host: str = "127.0.0.1"
    port: int = 8080
    workers: int = DEFAULT_WORKERS
    queue_size: int = DEFAULT_QUEUE_SIZE
    request_timeout_ms: int = DEFAULT_REQUEST_TIMEOUT_MS
    shutdown_timeout_ms: int = DEFAULT_SHUTDOWN_TIMEOUT_MS
    connection_limit: int = DEFAULT_CONNECTION_LIMIT
    backlog: int = DEFAULT_BACKLOG

    def validate(self) -> "ServiceConfig":
        try:
            address = ipaddress.ip_address(self.host)
        except ValueError as error:
            raise ServiceHostError("host must be the literal IPv4 or IPv6 loopback address") from error
        if not address.is_loopback:
            raise ServiceHostError("the experimental service host only listens on loopback")
        if not 0 <= self.port <= 65535:
            raise ServiceHostError("port must be between 0 and 65535")
        if not 1 <= self.workers <= MAX_WORKER_COUNT:
            raise ServiceHostError(f"workers must be between 1 and {MAX_WORKER_COUNT}")
        if not 1 <= self.queue_size <= MAX_QUEUE_SIZE:
            raise ServiceHostError(f"queue size must be between 1 and {MAX_QUEUE_SIZE}")
        if not 1 <= self.request_timeout_ms <= 60_000:
            raise ServiceHostError("request timeout must be between 1 and 60000 milliseconds")
        if not 1_000 <= self.shutdown_timeout_ms <= 60_000:
            raise ServiceHostError("shutdown timeout must be between 1000 and 60000 milliseconds")
        if not self.workers <= self.connection_limit <= DEFAULT_CONNECTION_LIMIT:
            raise ServiceHostError(
                f"connection limit must be at least the worker count and at most {DEFAULT_CONNECTION_LIMIT}"
            )
        if not 1 <= self.backlog <= 256:
            raise ServiceHostError("backlog must be between 1 and 256")

        worker = self.worker.expanduser().resolve()
        if not worker.is_file():
            raise ServiceHostError(f"worker executable not found: {worker}")
        database = self.database.expanduser().resolve()
        if database.exists() and not database.is_file():
            raise ServiceHostError("database path must refer to a regular file")
        if not database.parent.is_dir():
            raise ServiceHostError("database parent directory does not exist")
        return ServiceConfig(
            worker=worker,
            database=database,
            host=str(address),
            port=self.port,
            workers=self.workers,
            queue_size=self.queue_size,
            request_timeout_ms=self.request_timeout_ms,
            shutdown_timeout_ms=self.shutdown_timeout_ms,
            connection_limit=self.connection_limit,
            backlog=self.backlog,
        )


@dataclass(eq=False)
class _WorkerJob:
    request: bytes
    deadline: float
    future: Future[bytes] = field(default_factory=Future)
    cancelled: threading.Event = field(default_factory=threading.Event)
    process: subprocess.Popen | None = None
    process_lock: threading.Lock = field(default_factory=threading.Lock)

    def cancel(self) -> None:
        self.cancelled.set()
        with self.process_lock:
            process = self.process
        if process is not None and process.poll() is None:
            terminate_process_tree(process, force_group=True)


class WorkerSupervisor:
    """Run one fresh child process per request with bounded workers and queue."""

    def __init__(self, config: ServiceConfig):
        self.config = config
        self._queue: queue.Queue[_WorkerJob | None] = queue.Queue(maxsize=config.queue_size)
        self._closing = threading.Event()
        self._active_lock = threading.Lock()
        self._active: set[_WorkerJob] = set()
        self._threads = [
            threading.Thread(target=self._worker_loop, name=f"wysna-worker-{index + 1}", daemon=True)
            for index in range(config.workers)
        ]
        for thread in self._threads:
            thread.start()

    def submit(self, request: bytes, deadline: float) -> _WorkerJob:
        job = _WorkerJob(request=request, deadline=deadline)
        with self._active_lock:
            if self._closing.is_set():
                raise ServiceStopping
            try:
                self._queue.put_nowait(job)
            except queue.Full as error:
                raise ServiceSaturated from error
        return job

    def cancel(self, job: _WorkerJob) -> None:
        job.cancel()

    def _worker_loop(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                if job.future.cancelled() or job.cancelled.is_set():
                    continue
                with self._active_lock:
                    if self._closing.is_set():
                        if not job.future.done():
                            job.future.set_exception(ServiceStopping())
                        continue
                    self._active.add(job)
                try:
                    result = self._invoke(job)
                    if not job.future.done():
                        job.future.set_result(result)
                except Exception as error:  # Worker failures are mapped at the HTTP boundary.
                    if not job.future.done():
                        job.future.set_exception(error)
                finally:
                    with self._active_lock:
                        self._active.discard(job)
            finally:
                self._queue.task_done()

    def _worker_environment(self) -> dict[str, str]:
        allowed = (
            "PATH", "SYSTEMROOT", "WINDIR", "TMP", "TEMP", "TMPDIR", "LANG", "LC_ALL",
            "LD_LIBRARY_PATH", "DYLD_LIBRARY_PATH",
        )
        environment = {name: os.environ[name] for name in allowed if name in os.environ}
        environment["WYSNA_SERVICE_DATABASE"] = os.fspath(self.config.database)
        return environment

    def _invoke(self, job: _WorkerJob) -> bytes:
        remaining = job.deadline - time.monotonic()
        if remaining <= 0:
            raise WorkerTimedOut
        with tempfile.TemporaryDirectory(prefix="wysna-service-request-") as directory:
            request_path = Path(directory) / "request.json"
            request_path.write_bytes(job.request)
            process = subprocess.Popen(
                [os.fspath(self.config.worker), os.fspath(request_path)],
                cwd=os.fspath(self.config.worker.parent),
                env=self._worker_environment(),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                start_new_session=(os.name != "nt"),
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0),
            )
            attach_process_tree(process)
            with job.process_lock:
                job.process = process
                should_stop = job.cancelled.is_set()

            captured = bytearray()
            overflow = threading.Event()

            def drain_stdout() -> None:
                assert process.stdout is not None
                while True:
                    chunk = process.stdout.read(16_384)
                    if not chunk:
                        return
                    remaining_bytes = MAX_RESPONSE_BYTES - len(captured)
                    if remaining_bytes > 0:
                        captured.extend(chunk[:remaining_bytes])
                    if len(chunk) > remaining_bytes:
                        overflow.set()
                        if process.poll() is None:
                            terminate_process_tree(process, force_group=True)

            reader = threading.Thread(target=drain_stdout, name="wysna-worker-stdout", daemon=True)
            reader.start()
            try:
                if should_stop:
                    terminate_process_tree(process, force_group=True)
                while process.poll() is None:
                    if job.cancelled.is_set():
                        terminate_process_tree(process, force_group=True)
                        break
                    remaining = job.deadline - time.monotonic()
                    if remaining <= 0:
                        terminate_process_tree(process, force_group=True)
                        raise WorkerTimedOut
                    try:
                        process.wait(timeout=min(remaining, 0.05))
                    except subprocess.TimeoutExpired:
                        continue
                process.wait()
                terminate_process_tree(process, force_group=True)
                reader.join(timeout=1)
            finally:
                if process.poll() is None:
                    terminate_process_tree(process, force_group=True)
                    process.wait()
                if reader.is_alive():
                    reader.join(timeout=1)
                release_process_tree(process)
                with job.process_lock:
                    job.process = None

            if job.cancelled.is_set():
                raise ServiceStopping
            if overflow.is_set():
                raise WorkerOutputLimit
            if process.returncode != 0:
                raise WorkerFailed(f"worker exited with status {process.returncode}")
            return bytes(captured)

    def close(self, timeout_ms: int = DEFAULT_SHUTDOWN_TIMEOUT_MS) -> None:
        with self._active_lock:
            if self._closing.is_set():
                return
            self._closing.set()
        while True:
            try:
                job = self._queue.get_nowait()
            except queue.Empty:
                break
            try:
                if job is not None:
                    job.cancelled.set()
                    if not job.future.done():
                        job.future.set_exception(ServiceStopping())
            finally:
                self._queue.task_done()

        deadline = time.monotonic() + max(0, timeout_ms) / 1000
        while time.monotonic() < deadline:
            with self._active_lock:
                if not self._active:
                    break
            time.sleep(0.02)
        with self._active_lock:
            remaining_jobs = list(self._active)
        for job in remaining_jobs:
            job.cancel()
        for _ in self._threads:
            try:
                self._queue.put_nowait(None)
            except queue.Full:
                break
        for thread in self._threads:
            thread.join(timeout=max(0.0, deadline - time.monotonic()))


def _json_error(status: int, code: str) -> dict[str, Any]:
    return {"error": code, "status": status}


def _json_response(status: int, value: Any):
    from aiohttp import web

    if status in (204, 205, 304):
        return web.Response(status=status)
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    if len(payload) > MAX_RESPONSE_BYTES:
        payload = json.dumps(_json_error(502, "worker_response_limit"), separators=(",", ":")).encode("ascii")
        status = 502
    return web.Response(status=status, body=payload, content_type="application/json")


def _reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _reject_json_constant(value):
    raise ValueError(f"invalid JSON constant: {value}")


def _parse_worker_response(raw: bytes) -> tuple[int, Any]:
    try:
        decoded = raw.decode("utf-8")
        value = json.loads(
            decoded,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_json_constant,
        )
    except (RecursionError, UnicodeError, ValueError, json.JSONDecodeError) as error:
        raise WorkerFailed("worker returned invalid JSON") from error
    if not isinstance(value, dict) or set(value) != {"status", "body"}:
        raise WorkerFailed("worker response must contain status and body")
    status = value["status"]
    if isinstance(status, bool) or not isinstance(status, int) or not 200 <= status <= 599:
        raise WorkerFailed("worker returned an invalid HTTP status")
    if status in (204, 205, 304) and value["body"] is not None:
        raise WorkerFailed("bodyless HTTP status cannot include a response body")
    try:
        json.dumps(value["body"], ensure_ascii=False, allow_nan=False)
    except (RecursionError, TypeError, ValueError) as error:
        raise WorkerFailed("worker returned a non-JSON body") from error
    return status, value["body"]


class _RejectedConnection(asyncio.Protocol):
    """Close excess accepted sockets before aiohttp creates a request handler."""

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        transport.close()

async def serve(config: ServiceConfig) -> None:
    try:
        from aiohttp import web
    except ImportError as error:
        raise ServiceHostError("service hosting requires the optional 'wysna[service]' dependency") from error

    config = config.validate()
    supervisor = WorkerSupervisor(config)
    stopping = asyncio.Event()
    loop = asyncio.get_running_loop()
    previous_signal_handlers = {}
    registered_loop_signals = []
    for signal_name in ("SIGINT", "SIGTERM"):
        signum = getattr(signal, signal_name, None)
        if signum is None:
            continue
        try:
            loop.add_signal_handler(signum, stopping.set)
            registered_loop_signals.append(signum)
        except (NotImplementedError, RuntimeError):
            previous_signal_handlers[signum] = signal.getsignal(signum)

            def request_shutdown(_signum, _frame, *, event=stopping, running_loop=loop):
                running_loop.call_soon_threadsafe(event.set)

            signal.signal(signum, request_shutdown)
    async def handle(request):
        if request.method not in _ALLOWED_METHODS:
            return _json_response(405, _json_error(405, "method_not_allowed"))
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
                    "path": request.raw_path,
                    "headers": headers,
                    "body": body_text,
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if len(worker_request) > MAX_WORKER_REQUEST_BYTES:
                return _json_response(413, _json_error(413, "worker_request_limit"))
            try:
                job = supervisor.submit(worker_request, time.monotonic() + max(0.0, deadline - loop.time()))
            except ServiceSaturated:
                return _json_response(503, _json_error(503, "service_busy"))
            except ServiceStopping:
                return _json_response(503, _json_error(503, "service_stopping"))
            remaining = max(0.001, deadline - loop.time())
            try:
                raw_response = await asyncio.wait_for(asyncio.wrap_future(job.future), timeout=remaining)
            except WorkerTimedOut:
                return _json_response(504, _json_error(504, "worker_timeout"))
            except ServiceStopping:
                return _json_response(503, _json_error(503, "service_stopping"))
            except (WorkerFailed, WorkerOutputLimit, OSError):
                return _json_response(502, _json_error(502, "worker_failed"))
            try:
                status, response_body = _parse_worker_response(raw_response)
            except WorkerFailed:
                return _json_response(502, _json_error(502, "worker_failed"))
            return _json_response(status, response_body)
        except asyncio.TimeoutError:
            if "job" in locals():
                supervisor.cancel(job)
            response = _json_response(504, _json_error(504, "request_timeout"))
            response.force_close()
            return response
        except web.HTTPRequestEntityTooLarge:
            return _json_response(413, _json_error(413, "request_body_too_large"))

    handler = handle
    class BoundedServer(web.Server):
        def __call__(self):
            if len(self.connections) >= config.connection_limit:
                return _RejectedConnection()
            return super().__call__()

    server = BoundedServer(
        handler,
        max_line_size=MAX_REQUEST_LINE_BYTES,
        max_headers=MAX_REQUEST_HEADERS,
        max_field_size=MAX_REQUEST_HEADER_FIELD_BYTES,
        auto_decompress=False,
        # aiohttp 3.14.4+ applies this while waiting for the initial request too.
        keepalive_timeout=DEFAULT_KEEPALIVE_TIMEOUT_SECONDS,
        # Do not retain sockets to drain bodies after a rejected or timed-out request.
        lingering_time=0,
        access_log=None,
    )
    runner = web.ServerRunner(
        server,
        shutdown_timeout=config.shutdown_timeout_ms / 1000,
    )
    site = None
    try:
        await runner.setup()
        site = web.TCPSite(
            runner,
            host=config.host,
            port=config.port,
            backlog=config.backlog,
            shutdown_timeout=config.shutdown_timeout_ms / 1000,
        )
        await site.start()
        print(
            f"Wysna service pilot listening on {site.name} "
            f"({config.workers} workers, queue {config.queue_size})",
            flush=True,
        )
        await stopping.wait()
    finally:
        for signum in registered_loop_signals:
            loop.remove_signal_handler(signum)
        for signum, previous_handler in previous_signal_handlers.items():
            signal.signal(signum, previous_handler)
        close_supervisor = asyncio.create_task(
            asyncio.to_thread(supervisor.close, config.shutdown_timeout_ms)
        )
        try:
            if site is not None:
                await site.stop()
            await runner.cleanup()
        finally:
            await close_supervisor


def run_service(config: ServiceConfig) -> None:
    try:
        asyncio.run(serve(config))
    except KeyboardInterrupt:
        return
