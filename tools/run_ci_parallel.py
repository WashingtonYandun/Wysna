#!/usr/bin/env python3
"""Run independent CI unit modules or pytest targets in parallel with fail-fast disabled."""
import argparse
import concurrent.futures
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as error:
        raise argparse.ArgumentTypeError(f"Expected positive integer, got {raw!r}") from error
    if value < 1:
        raise argparse.ArgumentTypeError(f"Expected positive integer >= 1, got {value}")
    return value


def resolve_jobs(jobs_arg: int | None) -> int:
    """Resolve worker pool size from CLI flag, WYSNA_CI_JOBS, or host CPU count."""
    if jobs_arg is not None:
        return jobs_arg
    env_raw = os.environ.get("WYSNA_CI_JOBS", "").strip()
    if env_raw:
        return _positive_int(env_raw)
    return min(4, max(1, os.cpu_count() or 2))


def build_tasks(
    mode: str,
    groups: list[str],
    targets: list[str],
    basetemp_root: Path | None = None,
) -> list[tuple[int, str, list[tuple[str, list[str]]]]]:
    """Build deterministic task specs; --group items execute sequentially inside one worker slot."""
    tasks: list[tuple[int, str, list[tuple[str, list[str]]]]] = []
    for raw_group in groups:
        items = [item.strip() for item in raw_group.split(",") if item.strip()]
        if not items:
            raise ValueError(f"Empty --group specification: {raw_group!r}")
        idx = len(tasks)
        label = f"group[{','.join(items)}]"
        if mode == "module":
            commands = [(item, [sys.executable, "-m", item]) for item in items]
        else:
            cmd = [sys.executable, "-m", "pytest", "-q"]
            if basetemp_root is not None:
                cmd.append(f"--basetemp={basetemp_root / f'task-{idx}'}")
            cmd.extend(items)
            commands = [(label, cmd)]
        tasks.append((idx, label, commands))

    for raw_target in targets:
        target = raw_target.strip()
        if not target:
            continue
        idx = len(tasks)
        if mode == "module":
            commands = [(target, [sys.executable, "-m", target])]
        else:
            cmd = [sys.executable, "-m", "pytest", "-q"]
            if basetemp_root is not None:
                cmd.append(f"--basetemp={basetemp_root / f'task-{idx}'}")
            cmd.append(target)
            commands = [(target, cmd)]
        tasks.append((idx, target, commands))
    return tasks


def _execute_task(
    task: tuple[int, str, list[tuple[str, list[str]]]],
    env: dict[str, str],
    print_lock: threading.Lock,
) -> tuple[int, str, int, float, list[tuple[str, int, str, str]]]:
    idx, label, commands = task
    started = time.monotonic()
    failures: list[tuple[str, int, str, str]] = []
    returncode = 0
    for step_label, command in commands:
        completed = subprocess.run(command, env=env, capture_output=True, text=True)
        if completed.returncode != 0:
            if returncode == 0:
                returncode = completed.returncode
            failures.append((step_label, completed.returncode, completed.stdout, completed.stderr))
    elapsed = time.monotonic() - started
    tag = "PASS" if returncode == 0 else "FAIL"
    with print_lock:
        print(f"[{tag} {elapsed:.2f}s] {label}", flush=True)
    return idx, label, returncode, elapsed, failures


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Execute independent unit modules or pytest suites in parallel (fail-fast disabled).",
    )
    parser.add_argument(
        "--mode",
        choices=("module", "pytest"),
        required=True,
        help="Execution mode: 'module' runs python -m <target>, 'pytest' runs python -m pytest -q <target>.",
    )
    parser.add_argument(
        "--jobs",
        type=_positive_int,
        default=None,
        help="Worker pool size (defaults to WYSNA_CI_JOBS or min(4, cpu_count)).",
    )
    parser.add_argument(
        "--group",
        action="append",
        default=[],
        help="Comma-separated targets that must run sequentially inside one task.",
    )
    parser.add_argument("targets", nargs="*", help="Independent targets to run in parallel.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        jobs = resolve_jobs(args.jobs)
    except argparse.ArgumentTypeError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="wysna-ci-parallel-") as temp_dir:
        basetemp_root = Path(temp_dir) if args.mode == "pytest" else None
        try:
            tasks = build_tasks(args.mode, args.group, args.targets, basetemp_root=basetemp_root)
        except ValueError as error:
            print(f"error: {error}", file=sys.stderr)
            return 2

        if not tasks:
            print("error: no targets or --group provided", file=sys.stderr)
            return 2

        workers = min(jobs, len(tasks))
        env = os.environ.copy()
        print_lock = threading.Lock()
        results = []
        if workers <= 1:
            for task in tasks:
                results.append(_execute_task(task, env, print_lock))
        else:
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_execute_task, task, env, print_lock) for task in tasks]
                for future in concurrent.futures.as_completed(futures):
                    results.append(future.result())

    results.sort(key=lambda row: row[0])
    failed = [row for row in results if row[2] != 0]
    if failed:
        print("\n=== Parallel CI failures (declaration order) ===", flush=True)
        for _, label, returncode, elapsed, step_failures in failed:
            print(f"\n--- FAIL {label} (rc={returncode}, {elapsed:.2f}s) ---", flush=True)
            for step_label, step_rc, stdout, stderr in step_failures:
                print(f"[step: {step_label}, rc={step_rc}]", flush=True)
                if stdout:
                    print(stdout, end="" if stdout.endswith("\n") else "\n", flush=True)
                if stderr:
                    print(stderr, end="" if stderr.endswith("\n") else "\n", file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
