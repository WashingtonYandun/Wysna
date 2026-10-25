#!/usr/bin/env python3
"""Run a repeatable loopback load and process-tree RSS campaign for the service pilot."""

from __future__ import annotations

import argparse
from array import array
from collections import Counter
from dataclasses import dataclass, field
import hashlib
import http.client
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import platform
import queue
import signal
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from compiler.CompilerService import CompilerService
from compiler.NativeToolchain import NativeToolchain
from common.Platform import compiler_command
from common.Version import WYSNA_VERSION
from tooling.ServiceHost import DEFAULT_SHUTDOWN_TIMEOUT_MS

MAX_LATENCY_SAMPLES = 100_000
FIXED_DATASET_ROWS = 1_000
FIXED_DATASET = [(row_id, row_id % 1000) for row_id in range(1, FIXED_DATASET_ROWS + 1)]
FIXED_DATASET_LINES = [f"{row_id},{value}" for row_id, value in FIXED_DATASET]
FIXED_DATASET_SHA256 = hashlib.sha256(
    "\n".join(FIXED_DATASET_LINES).encode("ascii")
).hexdigest()
HEALTH_BODY = b'{"ok":true,"runtime":"wysna"}'
MEASUREMENTS_BODY = f'{{"count":{FIXED_DATASET_ROWS}}}'.encode("ascii")


@dataclass
class ClientMetrics:
    requests: int = 0
    response_bytes: int = 0
    status_counts: Counter[int] = field(default_factory=Counter)
    endpoint_counts: Counter[str] = field(default_factory=Counter)
    latencies_seconds: array = field(default_factory=lambda: array("d"))
    latency_sample_limit: int = 100_000
    latency_sample_limit_reached: bool = False
    errors: int = 0
    examples: list[str] = field(default_factory=list)

    def record_latency(self, value: float) -> None:
        if len(self.latencies_seconds) < self.latency_sample_limit:
            self.latencies_seconds.append(value)
        else:
            self.latency_sample_limit_reached = True


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, int((percentile * len(ordered)) + 0.999999))
    return ordered[min(rank - 1, len(ordered) - 1)]


def _format_seconds(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.4f}s"


def _abort_client_connections(connections: list[http.client.HTTPConnection]) -> None:
    for connection in connections:
        active_socket = connection.sock
        if active_socket is not None:
            try:
                active_socket.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        connection.close()


class ProcessTreeSampler:
    """Sample the service process and its descendants without third-party packages."""

    def __init__(self, root_pid: int, interval_seconds: float):
        self.root_pid = root_pid
        self.interval_seconds = interval_seconds
        self.samples: list[tuple[float, int, int, float]] = []
        self.observed_pids: set[int] = {root_pid}
        self.failure: str | None = None
        self._stop = threading.Event()
        self._samples_lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="wysna-rss-sampler", daemon=True)
        self._method = "Linux /proc RSS summed for the service process tree"
        if sys.platform == "darwin":
            self._method = "macOS ps RSS summed for the service process tree"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join(timeout=5)

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                self.sample_now()
                delay = self.interval_seconds - (time.monotonic() - started)
                if delay > 0:
                    self._stop.wait(delay)
        except Exception as error:  # Preserve the measurement failure in the report.
            self.failure = f"{type(error).__name__}: {error}"

    def sample_now(self) -> float:
        started = time.monotonic()
        snapshot = self._snapshot()
        elapsed = time.monotonic() - started
        timestamp = time.monotonic()
        with self._samples_lock:
            self.samples.append((timestamp, sum(snapshot.values()), len(snapshot), elapsed))
            self.observed_pids.update(snapshot)
        return timestamp

    def _snapshot(self) -> dict[int, int]:
        if sys.platform.startswith("linux"):
            return self._linux_snapshot()
        if sys.platform == "darwin":
            return self._darwin_snapshot()
        raise RuntimeError("RSS process-tree sampling currently supports Linux and macOS")

    def _linux_snapshot(self) -> dict[int, int]:
        pending = [self.root_pid]
        found: dict[int, int] = {}
        while pending:
            pid = pending.pop()
            if pid in found:
                continue
            process_dir = Path("/proc") / str(pid)
            try:
                status = (process_dir / "status").read_text(encoding="ascii")
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
            rss_bytes = 0
            for line in status.splitlines():
                if line.startswith("VmRSS:"):
                    rss_bytes = int(line.split()[1]) * 1024
                    break
            found[pid] = rss_bytes
            tasks_dir = process_dir / "task"
            try:
                tasks = list(tasks_dir.iterdir())
            except (FileNotFoundError, ProcessLookupError, PermissionError):
                continue
            for task in tasks:
                try:
                    child_text = (task / "children").read_text(encoding="ascii")
                except (FileNotFoundError, ProcessLookupError, PermissionError):
                    continue
                pending.extend(int(child) for child in child_text.split())
        return found

    def _darwin_snapshot(self) -> dict[int, int]:
        result = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,rss="],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        )
        parents: dict[int, list[int]] = {}
        rss_kib: dict[int, int] = {}
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) != 3:
                continue
            pid, parent, rss = map(int, fields)
            parents.setdefault(parent, []).append(pid)
            rss_kib[pid] = rss
        pending = [self.root_pid]
        found: dict[int, int] = {}
        while pending:
            pid = pending.pop()
            if pid in found or pid not in rss_kib:
                continue
            found[pid] = rss_kib[pid] * 1024
            pending.extend(parents.get(pid, ()))
        return found

    def _is_running(self, pid: int) -> bool:
        if sys.platform.startswith("linux"):
            return (Path("/proc") / str(pid)).exists()
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True

    def report(self, start_time: float, end_time: float) -> dict[str, Any]:
        with self._samples_lock:
            recorded_samples = list(self.samples)
        samples = [sample for sample in recorded_samples if start_time <= sample[0] <= end_time]
        if not samples:
            raise RuntimeError("RSS sampler did not capture any steady-load samples")
        rss_values = [sample[1] for sample in samples]
        process_counts = [sample[2] for sample in samples]
        sample_costs = [sample[3] for sample in samples]
        window = max(1, len(rss_values) // 5)
        first_median = _percentile(rss_values[:window], 0.5)
        last_median = _percentile(rss_values[-window:], 0.5)
        delta = None if first_median is None or last_median is None else last_median - first_median
        delta_percent = None if not first_median else (delta / first_median) * 100
        return {
            "method": self._method,
            "sample_interval_seconds": self.interval_seconds,
            "samples": len(samples),
            "tree_rss_bytes": {
                "minimum": min(rss_values),
                "median": _percentile(rss_values, 0.5),
                "p95": _percentile(rss_values, 0.95),
                "maximum": max(rss_values),
                "first_window_median": first_median,
                "last_window_median": last_median,
                "last_minus_first_window_median": delta,
                "last_minus_first_window_percent": delta_percent,
            },
            "process_count_maximum": max(process_counts),
            "sampler_seconds": {
                "mean": sum(sample_costs) / len(sample_costs) if sample_costs else 0,
                "maximum": max(sample_costs, default=0),
            },
            "raw_samples": [
                {
                    "elapsed_seconds": timestamp - start_time,
                    "tree_rss_bytes": rss_bytes,
                    "process_count": process_count,
                    "sampling_seconds": sampling_seconds,
                }
                for timestamp, rss_bytes, process_count, sampling_seconds in samples
            ],
            "campaign_descendant_pid_count": max(0, len(self.observed_pids) - 1),
            "descendants_running_after_server_exit": [],
            "sampler_error": self.failure,
            "limitations": [
                "RSS is sampled and may miss peaks shorter than the sampling interval.",
                "Summing RSS can count shared pages more than once.",
                "The health endpoint is a fixed, read-only workload and does not model a production application.",
            ],
        }

    def living_descendants(self) -> list[int]:
        return sorted(
            pid for pid in self.observed_pids
            if pid != self.root_pid and self._is_running(pid)
        )


def _client_loop(host: str, port: int, start_gate: threading.Event,
                 deadline_holder: list[float], ready_barrier: threading.Barrier,
                 timeout_seconds: float, latency_sample_limit: int,
                 stop_event: threading.Event,
                 connection_registry: tuple[threading.Lock, list[http.client.HTTPConnection]]) -> ClientMetrics:
    metrics = ClientMetrics(latency_sample_limit=latency_sample_limit)
    connection = http.client.HTTPConnection(host, port, timeout=timeout_seconds)
    registry_lock, connections = connection_registry
    with registry_lock:
        connections.append(connection)
    try:
        ready_barrier.wait(timeout=10)
        start_gate.wait()
        deadline = deadline_holder[0]
        while not stop_event.is_set() and time.monotonic() < deadline:
            request_path = "/health" if metrics.requests % 2 == 0 else "/measurements"
            started = time.perf_counter()
            try:
                connection.request("GET", request_path, headers={"Accept": "application/json"})
                response = connection.getresponse()
                body = response.read()
                elapsed = time.perf_counter() - started
                metrics.requests += 1
                metrics.response_bytes += len(body)
                metrics.status_counts[response.status] += 1
                metrics.endpoint_counts[request_path] += 1
                metrics.record_latency(elapsed)
                if response.status == 200:
                    expected_body = HEALTH_BODY if request_path == "/health" else MEASUREMENTS_BODY
                    if body != expected_body:
                        metrics.errors += 1
                        if len(metrics.examples) < 10:
                            metrics.examples.append(
                                f"{request_path} returned an unexpected JSON body"
                            )
                elif response.status == 503:
                    if body != b'{"error":"service_busy","status":503}':
                        metrics.errors += 1
                        if len(metrics.examples) < 10:
                            metrics.examples.append("503 response did not match the bounded saturation contract")
                else:
                    metrics.errors += 1
                    if len(metrics.examples) < 10:
                        metrics.examples.append(f"unexpected HTTP status {response.status}")
                if response.will_close:
                    connection.close()
            except (OSError, http.client.HTTPException, TimeoutError) as error:
                if stop_event.is_set():
                    break
                metrics.errors += 1
                if len(metrics.examples) < 10:
                    metrics.examples.append(f"{type(error).__name__}: {error}")
                connection.close()
                time.sleep(0.01)
    finally:
        connection.close()
    return metrics


def _run_clients(host: str, port: int, clients: int, duration_seconds: float,
                 timeout_seconds: float) -> tuple[float, float, list[ClientMetrics]]:
    gate = threading.Event()
    stop_event = threading.Event()
    registry_lock = threading.Lock()
    connections: list[http.client.HTTPConnection] = []
    connection_registry = (registry_lock, connections)
    deadline_holder: list[float] = []
    barrier = threading.Barrier(clients + 1)
    results: list[ClientMetrics | None] = [None] * clients

    def run(index: int) -> None:
        results[index] = _client_loop(
            host, port, gate, deadline_holder, barrier, timeout_seconds,
            max(1, (MAX_LATENCY_SAMPLES + clients - 1) // clients),
            stop_event, connection_registry,
        )

    threads = [threading.Thread(target=run, args=(index,), name=f"wysna-load-client-{index + 1}")
               for index in range(clients)]
    for thread in threads:
        thread.daemon = True
        thread.start()
    barrier.wait(timeout=10)
    start_time = time.monotonic()
    deadline_holder.append(start_time + duration_seconds)
    gate.set()
    join_deadline = deadline_holder[0] + timeout_seconds + 10
    for thread in threads:
        thread.join(timeout=max(0, join_deadline - time.monotonic()))
    unfinished = [thread.name for thread in threads if thread.is_alive()]
    if unfinished:
        stop_event.set()
        with registry_lock:
            active_connections = list(connections)
        _abort_client_connections(active_connections)
        abort_deadline = time.monotonic() + 5
        for thread in threads:
            thread.join(timeout=max(0, abort_deadline - time.monotonic()))
        unfinished = [thread.name for thread in threads if thread.is_alive()]
    if unfinished:
        raise RuntimeError("load clients exceeded their request budget: " + ", ".join(unfinished))
    failed_clients = [index + 1 for index, result in enumerate(results) if result is None]
    if failed_clients:
        raise RuntimeError("load clients exited without metrics: " + ", ".join(map(str, failed_clients)))
    end_time = time.monotonic()
    return start_time, end_time, [result for result in results if result is not None]


def _merge_metrics(metrics: list[ClientMetrics], elapsed_seconds: float) -> dict[str, Any]:
    durations = array("d")
    statuses: Counter[int] = Counter()
    endpoints: Counter[str] = Counter()
    examples: list[str] = []
    requests = 0
    response_bytes = 0
    errors = 0
    sample_limit_reached = False
    for client in metrics:
        requests += client.requests
        response_bytes += client.response_bytes
        errors += client.errors
        statuses.update(client.status_counts)
        endpoints.update(client.endpoint_counts)
        durations.extend(client.latencies_seconds)
        sample_limit_reached = sample_limit_reached or client.latency_sample_limit_reached
        examples.extend(client.examples[:max(0, 10 - len(examples))])
    latency_values = list(durations)
    success_count = statuses[200]
    saturated_count = statuses[503]
    return {
        "elapsed_seconds": elapsed_seconds,
        "clients": len(metrics),
        "requests": requests,
        "successful_requests": success_count,
        "saturated_requests": saturated_count,
        "successful_requests_per_second": success_count / elapsed_seconds if elapsed_seconds else 0,
        "all_responses_per_second": requests / elapsed_seconds if elapsed_seconds else 0,
        "response_bytes": response_bytes,
        "status_counts": {str(status): count for status, count in sorted(statuses.items())},
        "endpoint_counts": dict(sorted(endpoints.items())),
        "request_errors": errors,
        "error_examples": examples,
        "latency_sample_count": len(latency_values),
        "latency_sample_limit_reached": sample_limit_reached,
        "latency_samples_seconds": latency_values,
        "latency_seconds": {
            "p50": _percentile(latency_values, 0.50),
            "p95": _percentile(latency_values, 0.95),
            "p99": _percentile(latency_values, 0.99),
            "maximum": max(latency_values, default=None),
        },
    }


def _build_worker(directory: Path) -> tuple[Path, float, str]:
    source = ROOT / "examples" / "service" / "sqlite_worker.wysna"
    adapter = ROOT / "examples" / "service" / "sqlite_adapter.c"
    worker = directory / ("sqlite_worker.exe" if os.name == "nt" else "sqlite_worker")
    started = time.perf_counter()
    compiler_service = CompilerService(project_root=ROOT)
    checked = compiler_service.check_file(source)
    c_source = compiler_service.emit_c(checked).source
    NativeToolchain(ROOT).build(c_source, worker, link_libraries=(adapter, "sqlite3"))
    c_source_sha256 = hashlib.sha256(c_source.encode("utf-8")).hexdigest()
    return worker, time.perf_counter() - started, c_source_sha256


def _prepare_fixed_database(database: Path) -> None:
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TABLE measurements("
            "id INTEGER PRIMARY KEY, value INTEGER NOT NULL CHECK(value >= 0))"
        )
        connection.executemany(
            "INSERT INTO measurements(id, value) VALUES(?, ?)",
            FIXED_DATASET,
        )


def _cpu_model() -> str:
    if sys.platform.startswith("linux"):
        try:
            for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
                if line.lower().startswith(("model name", "hardware")):
                    return line.split(":", 1)[1].strip()
        except OSError:
            pass
    elif sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["sysctl", "-n", "machdep.cpu.brand_string"],
                capture_output=True, text=True, timeout=5, check=True,
            )
            value = result.stdout.strip()
            if value:
                return value
        except (OSError, subprocess.SubprocessError):
            pass
    return platform.processor() or "unknown"


def _start_server(worker: Path, database: Path, *, workers: int, queue_size: int,
                  request_timeout_ms: int) -> tuple[subprocess.Popen, int, float]:
    command = [
        sys.executable, str(ROOT / "main.py"), "service", "serve",
        "--worker", str(worker), "--database", str(database),
        "--host", "127.0.0.1", "--port", "0",
        "--workers", str(workers), "--queue-size", str(queue_size),
        "--request-timeout-ms", str(request_timeout_ms),
    ]
    ready: queue.Queue[str] = queue.Queue(maxsize=1)
    process = subprocess.Popen(
        command,
        cwd=ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
    )

    def read_ready_line() -> None:
        assert process.stdout is not None
        ready.put(process.stdout.readline())

    threading.Thread(target=read_ready_line, name="wysna-service-readiness", daemon=True).start()
    started = time.perf_counter()
    try:
        line = ready.get(timeout=20).strip()
    except queue.Empty as error:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        raise RuntimeError("service host did not report readiness within 20 seconds") from error
    prefix = "Wysna service pilot listening on http://127.0.0.1:"
    if not line.startswith(prefix):
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        raise RuntimeError(f"service host failed before readiness: {line!r}; exit={process.poll()}")
    port_text = line.removeprefix(prefix).split(" ", 1)[0]
    return process, int(port_text), time.perf_counter() - started


def _stop_server(process: subprocess.Popen, timeout_seconds: float) -> tuple[int, float]:
    started = time.perf_counter()
    if process.poll() is None:
        process.send_signal(signal.SIGINT)
    try:
        exit_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired as error:
        process.kill()
        process.wait(timeout=5)
        raise RuntimeError("service did not shut down within its configured budget") from error
    return exit_code, time.perf_counter() - started


def _run_campaign_repetition(worker: Path, directory: Path, arguments: argparse.Namespace,
                             repetition: int) -> dict[str, Any]:
    database = directory / f"measurements-{repetition}.sqlite"
    _prepare_fixed_database(database)
    server, port, startup_seconds = _start_server(
        worker,
        database,
        workers=arguments.workers,
        queue_size=arguments.queue_size,
        request_timeout_ms=arguments.request_timeout_ms,
    )
    sampler = ProcessTreeSampler(server.pid, arguments.sample_interval_ms / 1000)
    sampler.start()
    try:
        if arguments.warmup_seconds:
            _run_clients(
                "127.0.0.1", port, arguments.clients,
                arguments.warmup_seconds,
                arguments.request_timeout_ms / 1000 + 5,
            )
        steady_rss_start = sampler.sample_now()
        steady_start, steady_end, clients = _run_clients(
            "127.0.0.1", port, arguments.clients,
            arguments.duration_seconds,
            arguments.request_timeout_ms / 1000 + 5,
        )
        steady_rss_end = sampler.sample_now()
        load = _merge_metrics(clients, steady_end - steady_start)
        saturation_rss_start = sampler.sample_now()
        saturation_start, saturation_end, saturation_clients = _run_clients(
            "127.0.0.1", port, arguments.saturation_clients,
            arguments.saturation_seconds,
            arguments.request_timeout_ms / 1000 + 5,
        )
        saturation_rss_end = sampler.sample_now()
        saturation = _merge_metrics(saturation_clients, saturation_end - saturation_start)
        sampler.stop()
        steady_rss = sampler.report(steady_rss_start, steady_rss_end)
        saturation_rss = sampler.report(saturation_rss_start, saturation_rss_end)
        shutdown_exit_code, shutdown_seconds = _stop_server(
            server, DEFAULT_SHUTDOWN_TIMEOUT_MS / 1000 + 10,
        )
        living_descendants = sampler.living_descendants()
        steady_rss["descendants_running_after_server_exit"] = living_descendants
        saturation_rss["descendants_running_after_server_exit"] = living_descendants
    finally:
        sampler.stop()
        if server.poll() is None:
            server.kill()
            server.wait(timeout=5)

    rss_first = steady_rss["tree_rss_bytes"]["first_window_median"]
    rss_delta = steady_rss["tree_rss_bytes"]["last_minus_first_window_median"]
    rss_budget = max(
        rss_first * arguments.rss_growth_budget_percent / 100 if rss_first else 0,
        arguments.rss_growth_budget_bytes,
    )
    rss_within_budget = rss_delta is not None and rss_delta <= rss_budget
    latency_samples = load["latency_sample_count"]
    p99_seconds = load["latency_seconds"]["p99"]
    return {
        "repetition": repetition,
        "service_startup_seconds": startup_seconds,
        "load": load,
        "saturation": saturation,
        "rss": {"steady": steady_rss, "saturation": saturation_rss},
        "acceptance": {
            "latency_samples_required": arguments.minimum_latency_samples,
            "latency_samples_observed": latency_samples,
            "p99_sample_count_sufficient": latency_samples >= arguments.minimum_latency_samples,
            "successful_requests_positive": load["successful_requests"] > 0,
            "p99_within_request_budget": (
                p99_seconds is not None and p99_seconds <= arguments.request_timeout_ms / 1000
            ),
            "saturation_probe_completed": (
                saturation["requests"] > 0 and saturation["saturated_requests"] > 0
            ),
            "rss_growth_budget_bytes": rss_budget,
            "rss_growth_within_provisional_budget": rss_within_budget,
            "rss_sampling_succeeded": (
                steady_rss["sampler_error"] is None
                and saturation_rss["sampler_error"] is None
            ),
            "steady_load_not_saturated": load["saturated_requests"] == 0,
            "request_errors_zero": load["request_errors"] == 0 and saturation["request_errors"] == 0,
            "bounded_worker_process_count": (
                steady_rss["process_count_maximum"] <= arguments.workers + 1
                and saturation_rss["process_count_maximum"] <= arguments.workers + 1
            ),
            "clean_shutdown": shutdown_exit_code == 0 and not living_descendants,
        },
        "shutdown": {
            "exit_code": shutdown_exit_code,
            "elapsed_seconds": shutdown_seconds,
            "clean": shutdown_exit_code == 0 and not living_descendants,
            "descendants_running_after_server_exit": living_descendants,
        },
    }


def measure(arguments: argparse.Namespace) -> dict[str, Any]:
    if os.name == "nt":
        raise RuntimeError("service RSS campaigns currently support Linux and macOS")
    try:
        aiohttp_version = version("aiohttp")
    except PackageNotFoundError as error:
        raise RuntimeError("install the optional service dependency with `python -m pip install '.[service]'`") from error
    with tempfile.TemporaryDirectory(prefix="wysna-service-load-") as temporary:
        directory = Path(temporary)
        worker, build_seconds, generated_c_source_sha256 = _build_worker(directory)
        worker_binary_sha256 = hashlib.sha256(worker.read_bytes()).hexdigest()
        runs = []
        for repetition in range(1, arguments.repetitions + 1):
            run = _run_campaign_repetition(worker, directory, arguments, repetition)
            runs.append(run)
            print(
                f"Completed service measurement repetition {repetition}/{arguments.repetitions}: "
                f"{run['load']['successful_requests']} accepted requests, "
                f"{run['load']['successful_requests_per_second']:.1f} requests/s, "
                f"p95={_format_seconds(run['load']['latency_seconds']['p95'])}, "
                f"p99={_format_seconds(run['load']['latency_seconds']['p99'])}, "
                f"{run['saturation']['saturated_requests']} saturation responses, "
                f"RSS growth within budget={run['acceptance']['rss_growth_within_provisional_budget']}, "
                f"clean shutdown={run['acceptance']['clean_shutdown']}",
                flush=True,
            )

    compiler = compiler_command()
    compiler_version = subprocess.run(
        compiler + ["--version"], capture_output=True, text=True, timeout=10,
    ) if compiler else None
    compiler_target = subprocess.run(
        compiler + ["-dumpmachine"], capture_output=True, text=True, timeout=10,
    ) if compiler else None
    compiler_sysroot = subprocess.run(
        compiler + ["--print-sysroot"], capture_output=True, text=True, timeout=10,
    ) if compiler else None
    worker_source = ROOT / "examples" / "service" / "sqlite_worker.wysna"
    sqlite_adapter = ROOT / "examples" / "service" / "sqlite_adapter.c"
    source_hashes = {
        "measurement_harness_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "worker_source_sha256": hashlib.sha256(worker_source.read_bytes()).hexdigest(),
        "sqlite_adapter_sha256": hashlib.sha256(sqlite_adapter.read_bytes()).hexdigest(),
        "generated_c_source_sha256": generated_c_source_sha256,
        "worker_binary_sha256": worker_binary_sha256,
        "dataset_sha256": FIXED_DATASET_SHA256,
    }
    run_throughput = [run["load"]["successful_requests_per_second"] for run in runs]
    run_p95 = [run["load"]["latency_seconds"]["p95"] for run in runs]
    run_p99 = [run["load"]["latency_seconds"]["p99"] for run in runs]
    report = {
        "schema_version": 1,
        "commit": subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip(),
        "tree": subprocess.run(
            ["git", "-C", str(ROOT), "rev-parse", "HEAD^{tree}"],
            capture_output=True, text=True, check=True,
        ).stdout.strip(),
        "platform": platform.platform(),
        "machine": platform.machine(),
        "cpu_model": _cpu_model(),
        "python": platform.python_version(),
        "wysna": WYSNA_VERSION,
        "aiohttp": aiohttp_version,
        "sqlite": sqlite3.sqlite_version,
        "compiler": {
            "command": compiler,
            "version": compiler_version.stdout.splitlines()[0]
            if compiler_version and compiler_version.stdout else "unknown",
            "target_triple": compiler_target.stdout.strip()
            if compiler_target and compiler_target.stdout.strip() else "unknown",
            "sysroot": compiler_sysroot.stdout.strip()
            if compiler_sysroot and compiler_sysroot.stdout.strip() else "toolchain default",
            "flags": ["-std=c99", "-O2", f"-I{ROOT}"],
            "link_libraries": ["sqlite3"],
        },
        "source_hashes": source_hashes,
        "workload": {
            "endpoints": ["GET /health", "GET /measurements"],
            "working_set": f"{FIXED_DATASET_ROWS} deterministic SQLite rows; read-only requests and stable responses",
            "dataset_rows": FIXED_DATASET_ROWS,
            "worker_model": "one fresh native Wysna process per accepted request",
            "load_model": "closed-loop clients at fixed concurrency",
            "repetitions": arguments.repetitions,
            "warmup_seconds_per_repetition": arguments.warmup_seconds,
            "steady_seconds_per_repetition": arguments.duration_seconds,
            "clients": arguments.clients,
            "workers": arguments.workers,
            "queue_size": arguments.queue_size,
            "saturation_clients": arguments.saturation_clients,
            "saturation_seconds": arguments.saturation_seconds,
            "worker_plus_queue_capacity": arguments.workers + arguments.queue_size,
            "request_timeout_ms": arguments.request_timeout_ms,
            "shutdown_timeout_ms": DEFAULT_SHUTDOWN_TIMEOUT_MS,
            "minimum_latency_samples_per_repetition": arguments.minimum_latency_samples,
            "maximum_latency_samples_per_repetition": MAX_LATENCY_SAMPLES,
            "provisional_rss_growth_budget": {
                "percent": arguments.rss_growth_budget_percent,
                "bytes": arguments.rss_growth_budget_bytes,
                "comparison": "last vs first steady-load window median; use the larger of percent or byte budget",
            },
        },
        "setup_seconds": {"worker_build": build_seconds},
        "summary": {
            "successful_requests_per_second_median": _percentile(run_throughput, 0.50),
            "p95_latency_seconds_median": _percentile([value for value in run_p95 if value is not None], 0.50),
            "p99_latency_seconds_median": _percentile([value for value in run_p99 if value is not None], 0.50),
            "saturation_responses_total": sum(run["saturation"]["saturated_requests"] for run in runs),
            "all_repetitions_passed": all(
                all(
                    run["acceptance"][key]
                    for key in (
                        "p99_sample_count_sufficient",
                        "successful_requests_positive",
                        "p99_within_request_budget",
                        "saturation_probe_completed",
                        "steady_load_not_saturated",
                        "rss_growth_within_provisional_budget",
                        "rss_sampling_succeeded",
                        "request_errors_zero",
                        "bounded_worker_process_count",
                        "clean_shutdown",
                    )
                )
                for run in runs
            ),
        },
        "runs": runs,
        "interpretation": "A repeatable development pilot measurement, not a production SLO or deployment qualification.",
    }
    report["passed"] = report["summary"]["all_repetitions_passed"]
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-seconds", type=int, default=600)
    parser.add_argument("--warmup-seconds", type=int, default=60)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--clients", type=int, default=4)
    parser.add_argument("--saturation-clients", type=int, default=48)
    parser.add_argument("--saturation-seconds", type=int, default=1)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--queue-size", type=int, default=32)
    parser.add_argument("--request-timeout-ms", type=int, default=15000)
    parser.add_argument("--minimum-latency-samples", type=int, default=10000)
    parser.add_argument("--rss-growth-budget-percent", type=float, default=5.0)
    parser.add_argument("--rss-growth-budget-bytes", type=int, default=8 * 1024 * 1024)
    parser.add_argument(
        "--sample-interval-ms",
        type=int,
        default=None,
        help="RSS sample interval in milliseconds (default: 1000)",
    )
    parser.add_argument(
        "--report", type=Path,
        default=ROOT / ".wysna_cache" / "service-load" / "report.json",
    )
    arguments = parser.parse_args()
    if not 1 <= arguments.duration_seconds <= 3600:
        parser.error("duration must be between 1 and 3600 seconds")
    if not 0 <= arguments.warmup_seconds <= 300:
        parser.error("warm-up must be between 0 and 300 seconds")
    if not 1 <= arguments.repetitions <= 10:
        parser.error("repetitions must be between 1 and 10")
    if not 1 <= arguments.clients <= 64:
        parser.error("clients must be between 1 and 64")
    if not 1 <= arguments.saturation_clients <= 64:
        parser.error("saturation clients must be between 1 and 64")
    if not 1 <= arguments.saturation_seconds <= 30:
        parser.error("saturation duration must be between 1 and 30 seconds")
    if not 1 <= arguments.workers <= 8:
        parser.error("workers must be between 1 and 8")
    if not 1 <= arguments.queue_size <= 32:
        parser.error("queue size must be between 1 and 32")
    if not 1 <= arguments.request_timeout_ms <= 60000:
        parser.error("request timeout must be between 1 and 60000 milliseconds")
    if not 0 <= arguments.minimum_latency_samples <= MAX_LATENCY_SAMPLES:
        parser.error(f"minimum latency samples must be between 0 and {MAX_LATENCY_SAMPLES}")
    if not 0 <= arguments.rss_growth_budget_percent <= 100:
        parser.error("RSS growth budget percent must be between 0 and 100")
    if arguments.rss_growth_budget_bytes < 0:
        parser.error("RSS growth budget bytes cannot be negative")
    if arguments.sample_interval_ms is None:
        arguments.sample_interval_ms = 1000
    if not 100 <= arguments.sample_interval_ms <= 5000:
        parser.error("RSS sample interval must be between 100 and 5000 milliseconds")
    try:
        report = measure(arguments)
    except Exception as error:
        print(f"service load measurement failed: {type(error).__name__}: {error}", file=sys.stderr)
        failure_report = {
            "schema_version": 1,
            "passed": False,
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "commit": subprocess.run(
                ["git", "-C", str(ROOT), "rev-parse", "HEAD"],
                capture_output=True, text=True,
            ).stdout.strip() or "unknown",
            "failure": {"type": type(error).__name__, "message": str(error)},
            "workload": {
                "repetitions": arguments.repetitions,
                "warmup_seconds_per_repetition": arguments.warmup_seconds,
                "steady_seconds_per_repetition": arguments.duration_seconds,
                "clients": arguments.clients,
                "workers": arguments.workers,
                "queue_size": arguments.queue_size,
                "request_timeout_ms": arguments.request_timeout_ms,
            },
            "interpretation": "The campaign failed before producing complete performance measurements.",
        }
        arguments.report.parent.mkdir(parents=True, exist_ok=True)
        temporary_report = arguments.report.with_suffix(arguments.report.suffix + ".tmp")
        temporary_report.write_text(json.dumps(failure_report, indent=2) + "\n", encoding="utf-8")
        temporary_report.replace(arguments.report)
        return 1
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    temporary_report = arguments.report.with_suffix(arguments.report.suffix + ".tmp")
    temporary_report.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    temporary_report.replace(arguments.report)
    summary = report["summary"]
    p95 = summary["p95_latency_seconds_median"]
    p99 = summary["p99_latency_seconds_median"]
    p95_text = _format_seconds(p95)
    p99_text = _format_seconds(p99)
    print(
        "Service campaign: "
        f"{len(report['runs'])}/{report['workload']['repetitions']} repetitions completed, "
        f"all passed={summary['all_repetitions_passed']}, "
        f"median throughput={summary['successful_requests_per_second_median']:.1f} requests/s, "
        f"median p95={p95_text}, median p99={p99_text}, "
        f"saturation responses={summary['saturation_responses_total']}"
    )
    print(f"Report: {arguments.report}")
    if not report["passed"]:
        print("Service load measurement did not meet its lifecycle and response checks", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
