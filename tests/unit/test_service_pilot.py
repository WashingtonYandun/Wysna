"""Contract tests for the loopback service host and SQLite worker example."""

import json
import os
from pathlib import Path
import queue
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from compiler.CompilerService import CompilerService
from compiler.NativeToolchain import NativeToolchain
from tooling.ServiceHost import (
    ServiceConfig,
    ServiceHostError,
    ServiceSaturated,
    WorkerFailed,
    WorkerOutputLimit,
    WorkerSupervisor,
    WorkerTimedOut,
    _parse_worker_response,
)


ROOT = Path(__file__).resolve().parents[2]


def _test_configuration(temp_dir, *, workers=1, queue_size=1):
    worker = Path(temp_dir) / "worker"
    worker.write_text("placeholder", encoding="utf-8")
    database = Path(temp_dir) / "measurements.sqlite"
    return ServiceConfig(
        worker=worker,
        database=database,
        workers=workers,
        queue_size=queue_size,
    ).validate()


def test_service_config_and_worker_response_contract():
    with tempfile.TemporaryDirectory(prefix="wysna-service-contract-") as directory:
        config = _test_configuration(directory)
        assert config.host == "127.0.0.1"
        try:
            ServiceConfig(
                worker=config.worker,
                database=config.database,
                host="0.0.0.0",
            ).validate()
        except ServiceHostError:
            pass
        else:
            raise AssertionError("service host must reject non-loopback addresses")

        valid = _parse_worker_response(b'{"status":201,"body":{"id":1}}')
        assert valid == (201, {"id": 1})
        invalid_responses = (
            b'{"status":200,"body":null,"extra":1}',
            b'{"status":200,"status":201,"body":null}',
            b'{"status":101,"body":null}',
            b'{"status":true,"body":null}',
            b'{"status":200,"body":NaN}',
        )
        for response in invalid_responses:
            try:
                _parse_worker_response(response)
            except WorkerFailed:
                continue
            raise AssertionError(f"invalid worker response was accepted: {response!r}")


def test_worker_supervisor_has_a_bounded_queue():
    with tempfile.TemporaryDirectory(prefix="wysna-service-queue-") as directory:
        supervisor = WorkerSupervisor(_test_configuration(directory, workers=1, queue_size=1))
        started = threading.Event()
        release = threading.Event()

        def delayed_worker(job):
            started.set()
            if not release.wait(timeout=5):
                raise AssertionError("test worker was not released")
            return job.request

        supervisor._invoke = delayed_worker
        try:
            deadline = time.monotonic() + 5
            active = supervisor.submit(b"active", deadline)
            assert started.wait(timeout=2)
            queued = supervisor.submit(b"queued", deadline)
            try:
                supervisor.submit(b"overflow", deadline)
            except ServiceSaturated:
                pass
            else:
                raise AssertionError("the service queue accepted a request past its configured limit")
            release.set()
            assert active.future.result(timeout=3) == b"active"
            assert queued.future.result(timeout=3) == b"queued"
        finally:
            release.set()
            supervisor.close(timeout_ms=1000)


def test_worker_supervisor_kills_failed_timed_out_and_oversized_workers():
    if os.name == "nt":
        return
    with tempfile.TemporaryDirectory(prefix="wysna-service-lifecycle-") as directory:
        worker = Path(directory) / "worker"
        worker.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys, time\n"
            "request = json.load(open(sys.argv[1], encoding='utf-8'))\n"
            "if request['path'] == '/crash': sys.exit(7)\n"
            "if request['path'] == '/slow': time.sleep(3)\n"
            "if request['path'] == '/large':\n"
            "    sys.stdout.write('{\\\"status\\\":200,\\\"body\\\":\\\"' + 'x' * 1048576 + '\\\"}')\n"
            "else: print('{\\\"status\\\":200,\\\"body\\\":null}')\n",
            encoding="utf-8",
        )
        worker.chmod(0o700)
        config = ServiceConfig(
            worker=worker,
            database=Path(directory) / "measurements.sqlite",
            workers=1,
            queue_size=1,
        ).validate()
        supervisor = WorkerSupervisor(config)
        base_deadline = time.monotonic() + 5

        def submit(path, deadline=base_deadline):
            return supervisor.submit(
                json.dumps({"method": "GET", "path": path, "headers": {}, "body": ""}).encode(),
                deadline,
            )

        def expect_failure(future, failure_type):
            try:
                future.result(timeout=5)
            except failure_type:
                return
            raise AssertionError(f"worker did not fail with {failure_type.__name__}")

        try:
            expect_failure(submit("/crash").future, WorkerFailed)
            timed_job = submit("/slow", time.monotonic() + 0.25)
            expect_failure(timed_job.future, WorkerTimedOut)
            expect_failure(submit("/large").future, WorkerOutputLimit)
            healthy = submit("/healthy")
            assert healthy.future.result(timeout=5) == b'{"status":200,"body":null}\n'
        finally:
            supervisor.close(timeout_ms=1000)


def _http_request(url, method="GET", body=None):
    headers = {"Content-Type": "application/json"} if body is not None else {}
    request = Request(url, data=body, headers=headers, method=method)
    opener = build_opener(ProxyHandler({}))
    try:
        with opener.open(request, timeout=10) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        return error.code, json.loads(error.read().decode("utf-8"))


def _start_service(worker, database, *, request_timeout_ms=5000, workers=1, queue_size=2):
    server = subprocess.Popen(
        [
            sys.executable,
            str(ROOT / "main.py"),
            "service",
            "serve",
            "--worker",
            str(worker),
            "--database",
            str(database),
            "--host",
            "127.0.0.1",
            "--port",
            "0",
            "--workers",
            str(workers),
            "--queue-size",
            str(queue_size),
            "--request-timeout-ms",
            str(request_timeout_ms),
        ],
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    output = queue.Queue(maxsize=1)
    reader = threading.Thread(target=lambda: output.put(server.stdout.readline()), daemon=True)
    reader.start()
    try:
        ready = output.get(timeout=10).strip()
    except queue.Empty as error:
        if server.poll() is None:
            server.send_signal(signal.SIGINT)
        server.wait(timeout=20)
        raise AssertionError("service host did not report readiness within 10 seconds") from error
    prefix = "Wysna service pilot listening on http://127.0.0.1:"
    if not ready.startswith(prefix):
        if server.poll() is None:
            server.send_signal(signal.SIGINT)
        server.wait(timeout=20)
        raise AssertionError(f"service host did not become ready: {ready!r}")
    endpoint = "http://127.0.0.1:" + ready.removeprefix(prefix).split(" ", 1)[0]
    return server, endpoint


def _stop_service(server):
    if server.poll() is None:
        server.send_signal(signal.SIGINT)
    server.wait(timeout=20)


def test_sqlite_worker_runs_through_the_bounded_http_host():
    if os.name == "nt":
        return
    try:
        import aiohttp  # noqa: F401
    except ImportError as error:
        raise AssertionError("service integration requires the wysna[service] extra") from error

    source_path = ROOT / "examples/service/sqlite_worker.wysna"
    adapter_path = ROOT / "examples/service/sqlite_adapter.c"
    compiler_service = CompilerService(project_root=ROOT)
    checked = compiler_service.check_file(source_path)
    c_source = compiler_service.emit_c(checked).source

    with tempfile.TemporaryDirectory(prefix="wysna-service-e2e-") as directory:
        worker = Path(directory) / "sqlite_worker"
        database = Path(directory) / "measurements.sqlite"
        NativeToolchain(ROOT).build(
            c_source,
            worker,
            link_libraries=(adapter_path, "sqlite3"),
        )
        server, endpoint = _start_service(worker, database)
        try:
            status, body = _http_request(endpoint + "/health")
            assert (status, body) == (200, {"ok": True, "runtime": "wysna"})

            status, body = _http_request(
                endpoint + "/measurements",
                method="POST",
                body=b'{"id":1,"value":42}',
            )
            assert (status, body) == (201, {"id": 1, "value": 42})

            status, body = _http_request(endpoint + "/measurements")
            assert (status, body) == (200, {"count": 1})

            status, body = _http_request(
                endpoint + "/measurements",
                method="POST",
                body=b'{"id":1,"value":99}',
            )
            assert (status, body) == (409, {"error": "measurement_id_exists"})

            status, body = _http_request(
                endpoint + "/measurements",
                method="POST",
                body=b'{"id":2,"value":-1}',
            )
            assert (status, body) == (400, {"error": "value_must_be_non_negative"})

            status, body = _http_request(endpoint + "/missing")
            assert (status, body) == (404, {"error": "route_not_found"})
            status, body = _http_request(
                endpoint + "/measurements", method="POST", body=b"\xff",
            )
            assert (status, body) == (
                400, {"error": "request_body_must_be_utf8", "status": 400},
            ), f"unexpected invalid UTF-8 response: {(status, body)!r}"
            status, body = _http_request(
                endpoint + "/measurements",
                method="POST",
                body=b"x" * (1_048_576 + 1),
            )
            assert (status, body) == (
                413, {"error": "request_body_too_large", "status": 413},
            ), f"unexpected oversized request response: {(status, body)!r}"
            status, body = _http_request(endpoint + "/health", method="TRACE")
            assert (status, body) == (
                405, {"error": "method_not_allowed", "status": 405},
            ), f"unexpected unsupported method response: {(status, body)!r}"
        finally:
            _stop_service(server)


def test_http_host_maps_invalid_worker_responses_to_json_errors():
    if os.name == "nt":
        return
    try:
        import aiohttp  # noqa: F401
    except ImportError as error:
        raise AssertionError("service integration requires the wysna[service] extra") from error

    with tempfile.TemporaryDirectory(prefix="wysna-service-invalid-worker-") as directory:
        worker = Path(directory) / "worker"
        worker.write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "request = json.load(open(sys.argv[1], encoding='utf-8'))\n"
            "if request['path'] == '/invalid': print('{\\\"status\\\":200}')\n"
            "else: print('{\\\"status\\\":200,\\\"body\\\":{\\\"ok\\\":true}}')\n",
            encoding="utf-8",
        )
        worker.chmod(0o700)
        server, endpoint = _start_service(
            worker,
            Path(directory) / "measurements.sqlite",
            request_timeout_ms=1000,
        )
        try:
            status, body = _http_request(endpoint + "/invalid")
            assert (status, body) == (
                502, {"error": "worker_failed", "status": 502},
            ), f"unexpected malformed worker response: {(status, body)!r}"
            status, body = _http_request(endpoint + "/healthy")
            assert (status, body) == (200, {"ok": True})

            parsed_endpoint = urlsplit(endpoint)
            with socket.create_connection(
                (parsed_endpoint.hostname, parsed_endpoint.port), timeout=10,
            ) as connection:
                started = time.monotonic()
                connection.sendall(
                    b"GET /healthy HTTP/1.1\r\nHost: localhost\r\nX-Incomplete:"
                )
                assert connection.recv(1) == b""
                elapsed = time.monotonic() - started
                assert 4.0 <= elapsed <= 8.0, (
                    f"initial request deadline was not enforced: {elapsed:.2f}s"
                )

            with socket.create_connection(
                (parsed_endpoint.hostname, parsed_endpoint.port), timeout=5,
            ) as connection:
                connection.sendall(
                    b"POST /healthy HTTP/1.1\r\n"
                    b"Host: localhost\r\n"
                    b"Content-Type: application/json\r\n"
                    b"Content-Length: 10\r\n"
                    b"Connection: close\r\n\r\n{"
                )
                response = bytearray()
                while True:
                    chunk = connection.recv(4096)
                    if not chunk:
                        break
                    response.extend(chunk)
                assert response.startswith(b"HTTP/1.1 504 "), bytes(response)
                assert b'{"error":"request_timeout","status":504}' in response
        finally:
            _stop_service(server)


def test_http_host_returns_service_busy_when_worker_queue_is_full():
    if os.name == "nt":
        return
    try:
        import aiohttp  # noqa: F401
    except ImportError as error:
        raise AssertionError("service integration requires the wysna[service] extra") from error

    with tempfile.TemporaryDirectory(prefix="wysna-service-saturation-") as directory:
        worker = Path(directory) / "worker"
        worker.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, sys, time\n"
            "request = json.load(open(sys.argv[1], encoding='utf-8'))\n"
            "if request['path'] == '/slow':\n"
            "    pathlib.Path('worker-started').write_text('started', encoding='utf-8')\n"
            "    time.sleep(0.5)\n"
            "print('{\\\"status\\\":200,\\\"body\\\":{\\\"ok\\\":true}}')\n",
            encoding="utf-8",
        )
        worker.chmod(0o700)
        server, endpoint = _start_service(
            worker,
            Path(directory) / "measurements.sqlite",
            request_timeout_ms=3000,
            workers=1,
            queue_size=1,
        )
        try:
            with ThreadPoolExecutor(max_workers=3) as executor:
                active = executor.submit(_http_request, endpoint + "/slow")
                marker = Path(directory) / "worker-started"
                deadline = time.monotonic() + 3
                while not marker.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert marker.exists(), "the slow worker did not start"

                request_gate = threading.Barrier(3)

                def request_after_gate():
                    request_gate.wait(timeout=3)
                    return _http_request(endpoint + "/slow")

                queued = executor.submit(request_after_gate)
                contender = executor.submit(request_after_gate)
                request_gate.wait(timeout=3)
                queue_results = [queued.result(timeout=5), contender.result(timeout=5)]

                assert active.result(timeout=5) == (200, {"ok": True})
                assert sorted(status for status, _ in queue_results) == [200, 503]
                assert (503, {"error": "service_busy", "status": 503}) in queue_results
                assert (200, {"ok": True}) in queue_results
        finally:
            _stop_service(server)


def test_service_pilot_contract():
    test_service_config_and_worker_response_contract()
    test_worker_supervisor_has_a_bounded_queue()
    test_worker_supervisor_kills_failed_timed_out_and_oversized_workers()
    test_sqlite_worker_runs_through_the_bounded_http_host()
    test_http_host_maps_invalid_worker_responses_to_json_errors()
    test_http_host_returns_service_busy_when_worker_queue_is_full()


if __name__ == "__main__":
    test_service_pilot_contract()
    print("[OK] bounded service pilot contract")
