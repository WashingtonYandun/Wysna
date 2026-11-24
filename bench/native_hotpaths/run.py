#!/usr/bin/env python3
"""Compare checked native workloads between two locally installed Wysna SDKs."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import tempfile
import time

SORT_SIZE = 16384
DICT_SIZE = 50000
DICT_PROBES = 120000
LITERAL_SIZE = 4096

SORT = f"""import "std/algorithms" as algorithms
let mut items: list[int] = []
for i in 0..{SORT_SIZE} {{ items.append((i * 137 + 41) % 4093) }}
let ordered = algorithms.sort[int](items, |left: int, right: int| => left < right)
let mut checksum = 0
for item in ordered {{ checksum = (checksum * 131 + item) % 1000000007 }}
print(len(ordered), checksum)
"""

INTEGER_DICT = f"""let mut values: dict[int, int] = {{}}
for i in 0..{DICT_SIZE} {{ values[i] = i + 1 }}
let mut checksum = 0
for i in 0..{DICT_PROBES} {{
    let key = (i * 17) % 70000
    if values.contains(key) {{ checksum = checksum + values[key] }}
}}
print(len(values), checksum)
"""


def fixture_sources(work):
    items = sorted((index * 137 + 41) % 4093 for index in range(SORT_SIZE))
    checksum = 0
    for item in items:
        checksum = (checksum * 131 + item) % 1000000007
    keys = [(index * 17) % 70000 for index in range(DICT_PROBES)]
    dict_sum = sum(key + 1 for key in keys if key < DICT_SIZE)
    cases = {
        "startup": ('print("ready")\n', "ready"),
        "stable_sort": (SORT, f"{SORT_SIZE} {checksum}"),
        "integer_dictionary": (INTEGER_DICT, f"{DICT_SIZE} {dict_sum}"),
    }
    outputs = {}
    for name, (source, expected) in cases.items():
        path = work / f"{name}.wysna"
        path.write_text(source, encoding="utf-8")
        outputs[name] = (path, [], expected)
    return outputs


def trial(command, directory, environment, expected):
    started = time.perf_counter_ns()
    completed = subprocess.run(command, cwd=directory, env=environment, capture_output=True,
                               text=True, encoding="utf-8", timeout=90)
    elapsed = (time.perf_counter_ns() - started) / 1e6
    if completed.returncode != 0 or completed.stdout.strip() != expected:
        raise RuntimeError((command, completed.returncode, completed.stdout, completed.stderr))
    return elapsed, completed.stderr


def measure(commands, directory, environment, expected, samples):
    captured = {label: [] for label in commands}
    labels = list(commands)
    for sample in range(samples + 3):
        order = labels if sample % 2 == 0 else reversed(labels)
        for label in order:
            elapsed, _ = trial(commands[label], directory, environment, expected)
            if sample >= 3:
                captured[label].append(elapsed)
    return {
        label: {
            "median_ms": statistics.median(timings),
            "p95_ms": statistics.quantiles(timings, n=100, method="inclusive")[94],
            "samples_ms": [round(value, 5) for value in timings],
        }
        for label, timings in captured.items()
    }


def options():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True,
                        help="path to the cross-language ledger main.wysna")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--samples", type=int, default=30)
    parser.add_argument("--compiler-samples", type=int, default=30)
    args = parser.parse_args()
    if min(args.samples, args.compiler_samples) < 3:
        parser.error("use at least three samples per toolchain")
    return args


def main():
    args = options()
    sdks = {"reference": args.reference.resolve(), "candidate": args.candidate.resolve()}
    cpu = platform.processor() or platform.machine()
    if sys.platform == "darwin":
        probe = subprocess.run(["/usr/sbin/sysctl", "-n", "machdep.cpu.brand_string"],
                               capture_output=True, text=True, timeout=5)
        if probe.returncode == 0 and probe.stdout.strip():
            cpu = probe.stdout.strip()
    result = {"architecture": platform.machine(), "cpu": cpu, "system": platform.platform(),
              "runtime": {}, "compiler": {}, "memory_stats": {}, "binary_bytes": {}, "sources": {}}
    for label, executable in sdks.items():
        manifest = json.loads((executable.parent.parent / "native-sdk.json").read_text(encoding="utf-8"))
        result["sources"][label] = manifest["source_commit"]
        if manifest["source_dirty"]:
            raise RuntimeError(f"{label} SDK has uncommitted source changes")
    keep = {"PATH", "LANG", "LC_ALL", "TMPDIR", "SDKROOT", "DEVELOPER_DIR"}
    env = {name: value for name, value in os.environ.items() if name in keep}
    with tempfile.TemporaryDirectory(prefix="wysna-hotpaths-") as temporary:
        work = Path(temporary)
        cache = work / "cache"
        cache.mkdir()
        env["WYSNA_CACHE_HOME"] = str(cache)
        cases = fixture_sources(work)
        cases["rollback_40000"] = (args.ledger.resolve(), ["20000"], "20001 10010007 true")
        for name, (source, run_args, expected) in cases.items():
            commands = {}
            sizes = {}
            for label, sdk in sdks.items():
                binary = work / f"{label}-{name}"
                trial([str(sdk), "build", str(source), "--profile", "release", "-o", str(binary)], work, env, "")
                commands[label] = [str(binary), *run_args]
                sizes[label] = binary.stat().st_size
            result["binary_bytes"][name] = sizes
            print(f"Timing {name}", flush=True)
            result["runtime"][name] = measure(commands, work, env, expected, args.samples)
            telemetry = {}
            for label, command in commands.items():
                _, stderr = trial(command, work, dict(env, WYSNA_MEM_STATS="1", WYSNA_MEM_CHECK="1"), expected)
                line = next(message for message in stderr.splitlines() if message.startswith("[WYSNA_MEM_STATS]"))
                telemetry[label] = {key: int(value) for key, value in re.findall(r"(\w+)=(\d+)", line)}
                if telemetry[label]["live"]:
                    raise RuntimeError(f"{name} leaked {telemetry[label]['live']} objects")
            result["memory_stats"][name] = telemetry

        literal = work / "array_literal.wysna"
        literal.write_text("let data = [" + ", ".join(str(index) for index in range(LITERAL_SIZE)) +
                           "]\nprint(len(data))\n", encoding="utf-8")
        paths = {label: work / f"{label}-array.c" for label in sdks}
        compiler_commands = {
            "check": {label: [str(sdk), "check", str(literal)] for label, sdk in sdks.items()},
            "emit_c": {label: [str(sdk), "emit-c", str(literal), "-o", str(paths[label])]
                       for label, sdk in sdks.items()},
        }
        for phase, commands in compiler_commands.items():
            print(f"Timing {phase}", flush=True)
            result["compiler"][phase] = measure(commands, work, env, "", args.compiler_samples)
        old = paths["reference"].read_bytes()
        new = paths["candidate"].read_bytes()
        if old != new:
            raise RuntimeError("the array literal emitted different C bytes")
        result["literal"] = {"elements": LITERAL_SIZE, "c_bytes": len(new),
                             "c_sha256": hashlib.sha256(new).hexdigest()}
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
