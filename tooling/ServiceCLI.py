"""Command-line entry points for the experimental Wysna service host."""

import argparse
from pathlib import Path
import sys

from tooling.ServiceHost import (
    DEFAULT_QUEUE_SIZE,
    DEFAULT_REQUEST_TIMEOUT_MS,
    DEFAULT_WORKERS,
    ServiceConfig,
    ServiceHostError,
    run_service,
)


class _StrictParser(argparse.ArgumentParser):
    def error(self, message):
        raise ServiceHostError(message)


def _print_help() -> None:
    print("Usage: wysna service serve --worker PATH --database PATH [options]")
    print("Options:")
    print("  --host ADDRESS              Loopback IP address (default: 127.0.0.1)")
    print("  --port PORT                 Listen port (default: 8080; 0 selects an available port)")
    print(f"  --workers COUNT             Isolated worker processes (default: {DEFAULT_WORKERS}; max: 8)")
    print(f"  --queue-size COUNT          Waiting requests (default: {DEFAULT_QUEUE_SIZE}; max: 32)")
    print(f"  --request-timeout-ms MS     End-to-end worker budget (default: {DEFAULT_REQUEST_TIMEOUT_MS})")
    print("This experimental server is local-only and does not provide TLS or authentication.")


def run_service_command(argv) -> int:
    if not argv or argv[0] in ("--help", "-h"):
        _print_help()
        return 0
    if argv[0] != "serve":
        print("wysna service: expected 'serve'", file=sys.stderr)
        _print_help()
        return 2

    parser = _StrictParser(prog="wysna service serve", add_help=False)
    parser.add_argument("--help", "-h", action="store_true")
    parser.add_argument("--worker", required=False)
    parser.add_argument("--database", required=False)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    parser.add_argument("--workers", type=int, default=DEFAULT_WORKERS)
    parser.add_argument("--queue-size", type=int, default=DEFAULT_QUEUE_SIZE)
    parser.add_argument("--request-timeout-ms", type=int, default=DEFAULT_REQUEST_TIMEOUT_MS)
    try:
        args = parser.parse_args(argv[1:])
        if args.help:
            _print_help()
            return 0
        if not args.worker or not args.database:
            raise ServiceHostError("--worker and --database are required")
        config = ServiceConfig(
            worker=Path(args.worker),
            database=Path(args.database),
            host=args.host,
            port=args.port,
            workers=args.workers,
            queue_size=args.queue_size,
            request_timeout_ms=args.request_timeout_ms,
        ).validate()
        run_service(config)
        return 0
    except ServiceHostError as error:
        print(f"wysna service: {error}", file=sys.stderr)
        return 2
