"""Contract checks for the service load and resident-memory measurement helpers."""

import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import threading
import time
from unittest.mock import patch

import tools.measure_service as service_measurement
from tools.measure_service import (
    ClientMetrics,
    ProcessTreeSampler,
    _abort_client_connections,
    _merge_metrics,
    _percentile,
)


def test_percentile_uses_nearest_rank_and_handles_empty_samples():
    assert _percentile([], 0.95) is None
    assert _percentile([4.0, 1.0, 3.0, 2.0], 0.50) == 2.0
    assert _percentile([4.0, 1.0, 3.0, 2.0], 0.95) == 4.0


def test_metrics_aggregate_statuses_throughput_and_bounded_latency_samples():
    client = ClientMetrics(latency_sample_limit=2)
    client.requests = 3
    client.response_bytes = 87
    client.status_counts.update({200: 2, 503: 1})
    client.endpoint_counts.update({"/health": 2, "/measurements": 1})
    client.record_latency(0.01)
    client.record_latency(0.03)
    client.record_latency(0.05)

    report = _merge_metrics([client], elapsed_seconds=2.0)

    assert report["successful_requests"] == 2
    assert report["saturated_requests"] == 1
    assert report["successful_requests_per_second"] == 1.0
    assert report["all_responses_per_second"] == 1.5
    assert report["response_bytes"] == 87
    assert report["endpoint_counts"] == {"/health": 2, "/measurements": 1}
    assert report["latency_sample_count"] == 2
    assert report["latency_sample_limit_reached"] is True
    assert report["latency_seconds"]["p50"] == 0.01
    assert report["latency_seconds"]["p99"] == 0.03


def test_http_client_records_one_latency_sample_per_response():
    class FakeResponse:
        def __init__(self, body):
            self.status = 200
            self.will_close = False
            self.body = body

        def read(self):
            return self.body

    class FakeConnection:
        def __init__(self, host, port, timeout):
            self.path = None

        def request(self, method, path, headers):
            assert method == "GET"
            assert path in ("/health", "/measurements")
            assert headers == {"Accept": "application/json"}
            self.path = path

        def getresponse(self):
            body = (
                service_measurement.HEALTH_BODY
                if self.path == "/health"
                else service_measurement.MEASUREMENTS_BODY
            )
            return FakeResponse(body)

        def close(self):
            pass

    ticks = iter((0.0, 0.0, 2.0))
    original_time = service_measurement.time
    original_connection = service_measurement.http.client.HTTPConnection

    class FixedClock:
        @staticmethod
        def monotonic():
            return next(ticks)

        @staticmethod
        def perf_counter():
            return original_time.perf_counter()

        @staticmethod
        def sleep(seconds):
            pass

    gate = threading.Event()
    gate.set()
    service_measurement.time = FixedClock
    service_measurement.http.client.HTTPConnection = FakeConnection
    try:
        metrics = service_measurement._client_loop(
            "127.0.0.1", 0, gate, [1.0], threading.Barrier(1), 1.0, 10,
            threading.Event(), (threading.Lock(), []),
        )
    finally:
        service_measurement.time = original_time
        service_measurement.http.client.HTTPConnection = original_connection

    assert metrics.requests == 2
    assert len(metrics.latencies_seconds) == metrics.requests
    assert metrics.endpoint_counts == {"/health": 1, "/measurements": 1}


def test_client_abort_shuts_down_active_sockets_and_closes_connections():
    events = []

    class FakeSocket:
        def shutdown(self, how):
            events.append(("shutdown", how))

    class FakeConnection:
        sock = FakeSocket()

        def close(self):
            events.append(("close",))

    _abort_client_connections([FakeConnection()])

    assert events == [("shutdown", service_measurement.socket.SHUT_RDWR), ("close",)]


def test_fixed_database_contains_the_declared_deterministic_working_set():
    with tempfile.TemporaryDirectory(prefix="wysna-measurement-db-") as temporary:
        database = Path(temporary) / "measurements.sqlite"
        service_measurement._prepare_fixed_database(database)
        with sqlite3.connect(database) as connection:
            count = connection.execute("SELECT count(*) FROM measurements").fetchone()[0]
            total = connection.execute("SELECT sum(value) FROM measurements").fetchone()[0]

    assert count == service_measurement.FIXED_DATASET_ROWS
    assert total == sum(row_id % 1000 for row_id in range(1, count + 1))


def test_process_tree_sampler_records_root_rss_on_supported_platforms():
    if os.name == "nt" or not (sys.platform.startswith("linux") or sys.platform == "darwin"):
        return

    sampler = ProcessTreeSampler(os.getpid(), interval_seconds=0.01)
    try:
        start = sampler.sample_now()
    except PermissionError:
        print("[SKIP] process RSS sampling is blocked by the local execution sandbox")
        return
    time.sleep(0.02)
    end = sampler.sample_now()
    report = sampler.report(start, end)

    assert report["samples"] == 2
    assert len(report["raw_samples"]) == 2
    assert report["tree_rss_bytes"]["maximum"] > 0
    assert report["process_count_maximum"] >= 1
    assert report["sampler_error"] is None


def test_linux_process_tree_sampler_tolerates_exiting_process_races():
    if not sys.platform.startswith("linux"):
        return

    sampler = ProcessTreeSampler(os.getpid(), interval_seconds=0.01)
    original_iterdir = Path.iterdir

    def process_exits_before_task_scan(path):
        if path.name == "task":
            raise ProcessLookupError("process exited during RSS sampling")
        return original_iterdir(path)

    with patch.object(Path, "iterdir", process_exits_before_task_scan):
        snapshot = sampler._linux_snapshot()

    assert os.getpid() in snapshot

    original_read_text = Path.read_text

    def thread_exits_before_child_scan(path, *args, **kwargs):
        if path.name == "children":
            raise ProcessLookupError("thread exited during RSS sampling")
        return original_read_text(path, *args, **kwargs)

    with patch.object(Path, "read_text", thread_exits_before_child_scan):
        snapshot = sampler._linux_snapshot()

    assert os.getpid() in snapshot


def test_service_measurement_contract():
    test_percentile_uses_nearest_rank_and_handles_empty_samples()
    test_metrics_aggregate_statuses_throughput_and_bounded_latency_samples()
    test_http_client_records_one_latency_sample_per_response()
    test_client_abort_shuts_down_active_sockets_and_closes_connections()
    test_fixed_database_contains_the_declared_deterministic_working_set()
    test_process_tree_sampler_records_root_rss_on_supported_platforms()
    test_linux_process_tree_sampler_tolerates_exiting_process_races()


if __name__ == "__main__":
    test_service_measurement_contract()
    print("[OK] service load measurement contract")
