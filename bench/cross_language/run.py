#!/usr/bin/env python3
"""Measure equivalent smoke, JSON and rollback workloads on a single host."""
import argparse
from collections import defaultdict
import hashlib
import json
import os
from pathlib import Path
import random
import shutil
import statistics
import subprocess
import platform
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parent
CASES = ROOT / "cases"
LANGUAGES = ("wysna", "go", "rust", "python", "javascript", "java")
BASELINE = "wysna_baseline"
COMPILED = ("wysna", BASELINE, "go", "rust", "java")
JACKSON_SHA256 = "b64b5874162b503a0e58a8f7758266e8dd9f91bf49e3a59ae0b5f47589a231b7"
FILES = {"wysna": "main.wysna", "go": "main.go", "rust": "main.rs",
         "python": "main.py", "javascript": "main.cjs", "java": "Main.java", BASELINE: "main.wysna"}
CARGO_BINARIES = {"startup": "hello", "json": "payload", "ledger": "ledger_bench"}
NATIVE_WORKLOADS = ("startup", "json", "ledger")


def arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    executables = ("wysna", "go", "rustc", "cargo", "python", "node", "java", "javac")
    for name in executables:
        parser.add_argument("--" + name,
                            default=sys.executable if name == "python" else shutil.which(name))
    parser.add_argument("--cargo-home", type=Path)
    parser.add_argument("--baseline-wysna", metavar="EXECUTABLE")
    parser.add_argument("--jackson", required=True, type=Path, help="path to jackson-core-2.21.5.jar")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--samples", default=30, type=int)
    parser.add_argument("--build-samples", default=5, type=int)
    options = parser.parse_args()
    if options.samples < 3 or options.build_samples < 3:
        parser.error("use at least three samples")
    if not all(getattr(options, name) for name in executables):
        parser.error("provide executable paths for all six languages")
    if not hasattr(os, "wait4"):
        parser.error("the benchmark requires wait4 for process RSS measurements")
    options.jackson = options.jackson.resolve()
    try:
        with options.jackson.open("rb") as dependency:
            checksum = hashlib.file_digest(dependency, "sha256").hexdigest()
    except OSError as error:
        parser.error(str(error))
    if checksum != JACKSON_SHA256:
        parser.error("Jackson dependency does not match the pinned SHA-256")
    for name in (*executables, "baseline_wysna"):
        value = getattr(options, name)
        if value:
            executable = shutil.which(value)
            if not executable:
                parser.error(f"executable not found: {value}")
            setattr(options, name, str(Path(executable).absolute()))
    return options


def run(command, cwd, env, stdin=None):
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        start = time.perf_counter_ns()
        child = subprocess.Popen(command, cwd=cwd, env=env, stdin=stdin, stdout=stdout, stderr=stderr)
        _, status, usage = os.wait4(child.pid, 0)
        elapsed = (time.perf_counter_ns() - start) / 1e6
        child.returncode = os.waitstatus_to_exitcode(status)
        stdout.seek(0)
        stderr.seek(0)
        multiplier = 1 if sys.platform == "darwin" else 1024
        return {"ms": elapsed, "rss": usage.ru_maxrss * multiplier, "exit": child.returncode,
                "stdout": stdout.read().decode(errors="replace"), "stderr": stderr.read().decode(errors="replace")}


def assert_output(sample, output, status=0):
    assert sample["exit"] == status and sample["stdout"].strip() == output, sample


def pct(values, level):
    return statistics.quantiles(values, n=100, method="inclusive")[level - 1]


def summarize(samples):
    times = [sample["ms"] for sample in samples]
    return {
        "n": len(samples), "p50_ms": statistics.median(times), "p95_ms": pct(times, 95),
        "min_ms": min(times), "p50_rss_bytes": statistics.median(sample["rss"] for sample in samples),
        "samples": [[round(sample["ms"], 5), sample["rss"]] for sample in samples],
    }


def fixture_set(directory):
    fixtures = {}
    for count in (1000, 10000):
        records = [
            {"id": index if index % 17 else -index, "units": index * 137 % 1201 - 100, "active": index % 7 != 0}
            for index in range(1, count + 1)
        ]
        valid = [row for row in records if row["id"] > 0 and row["active"] and 0 <= row["units"] <= 1000]
        path = directory / f"payload-{count}.json"
        payload = json.dumps(records, separators=(",", ":")).encode("ascii")
        path.write_bytes(payload)
        fixtures[f"json_{count}"] = {
            "suite": "json", "args": [str(path)], "expected": f"{len(valid)} {count-len(valid)} {sum(row['units'] for row in valid)}",
            "records": count, "bytes": len(payload), "sha256": hashlib.sha256(payload).hexdigest(),
        }
    for count in (1000, 20000):
        fixtures[f"ledger_{count}"] = {
            "suite": "ledger", "args": [str(count)], "expected": f"{count+1} {7+sum(index%1000+1 for index in range(1,count+1))} true",
            "records": count * 2,
        }
    fixtures["startup"] = {"suite": "startup", "args": [], "expected": "ready", "records": 0}
    for name, source in (
        ("truncated", '[{"id":1,"units":8,"active":true}'),
        ("typed", '[{"id":1,"units":"8","active":true}]'),
    ):
        (directory / f"invalid-{name}.json").write_text(source, encoding="utf-8")
    return fixtures


def scoped_environment(options, work):
    keep = {"PATH", "LANG", "LC_ALL", "TMPDIR", "TMP", "TEMP", "SDKROOT", "DEVELOPER_DIR"}
    env = {key: value for key, value in os.environ.items() if key in keep}
    caches = {key: work / name for key, name in [
        ("GOCACHE", "go-cache"), ("CARGO_TARGET_DIR", "cargo-target"),
        ("ZIG_GLOBAL_CACHE_DIR", "zig-global"), ("ZIG_LOCAL_CACHE_DIR", "zig-local"),
        ("WYSNA_CACHE_HOME", "wysna-cache"),
        ("WYSNA_BASELINE_CACHE", "wysna-baseline-cache"),
        ("PYTHONPYCACHEPREFIX", "pycache"),
    ]}
    for path in caches.values():
        path.mkdir()
    env.update({key: str(path) for key, path in caches.items()})
    env.update(GOTOOLCHAIN="local", GOPROXY="off", GOSUMDB="off", GOENV="off", GOWORK="off",
               RUSTC=str(options.rustc))
    if options.cargo_home:
        env["CARGO_HOME"] = str(options.cargo_home.resolve())
    return env, caches


def java_classpath(options, suite, output):
    paths = [str(output)]
    if suite == "json":
        paths.append(str(options.jackson))
    return os.pathsep.join(paths)


def launch_command(options, language, suite, directory, output):
    if language == "java":
        return [options.java, "-cp", java_classpath(options, suite, output), "Main"]
    if language in COMPILED:
        return [str(output)]
    executable = options.python if language == "python" else options.node
    return [executable, str(directory / FILES[language])]


def build(options, language, case, directory, output, env, caches):
    if language in ("wysna", BASELINE):
        executable = options.wysna if language == "wysna" else options.baseline_wysna
        if language == BASELINE:
            env = {**env, "WYSNA_CACHE_HOME": str(caches["WYSNA_BASELINE_CACHE"])}
        command = [executable, "build", "main.wysna", "--profile", "release", "-o", str(output)]
    elif language == "go":
        command = [options.go, "build", "-o", str(output), "." if case == "ledger" else "main.go"]
    elif language == "java":
        output.mkdir(exist_ok=True)
        sources = sorted(path.name for path in directory.glob("*.java"))
        command = [options.javac, "-encoding", "UTF-8", "-cp", java_classpath(options, case, output),
                   "-d", str(output), *sources]
    else:
        command = [options.cargo, "build", "--release", "--locked", "--offline"]
    sample = run(command, directory, env)
    assert sample["exit"] == 0, (language, case, sample)
    if language == "rust":
        shutil.copy2(caches["CARGO_TARGET_DIR"] / "release" / CARGO_BINARIES[case], output)
    assert (output / "Main.class" if language == "java" else output).is_file(), (language, case)
    return sample


def startup_edit(options, language, directory, output, env, caches):
    source = directory / FILES[language]
    original = source.read_text(encoding="utf-8")
    assert original.count("ready") == 1
    do_build = language in COMPILED
    results = []
    try:
        for sample_id in range(options.build_samples + 1):
            message = f"ready-{sample_id + 100}"
            source.write_text(original.replace("ready", message), encoding="utf-8")
            if do_build:
                sample = build(options, language, "startup", directory, output, env, caches)
                command = launch_command(options, language, "startup", directory, output)
                checked = run(command, directory, env)
                assert_output(checked, message)
            elif language == "python":
                sample = run([options.python, "-m", "py_compile", str(source)], directory, env)
            else:
                sample = run([options.node, "--check", str(source)], directory, env)
            assert sample["exit"] == 0, sample
            results.append(sample)
    finally:
        source.write_text(original, encoding="utf-8")
    kind = "release_rebuild" if do_build else "syntax_check"
    if language == "java":
        kind = "bytecode_rebuild"
    return {
        "kind": kind,
        **summarize(results[1:]),
    }


def diagnostics(options, work, env):
    directory = work / "diagnostics"
    directory.mkdir()
    sources = {
        "wysna": 'let value: int = "wrong"\nprint(value)\n',
        "go": 'package main\nvar value int = "wrong"\nfunc main() {}\n',
        "rust": 'fn main() { let value: i64 = "wrong"; println!("{value}"); }\n',
        "python": 'value: int = "wrong"\nprint(value)\n',
        "javascript": 'const value = "wrong";\nconsole.log(value);\n',
        "java": 'public final class Main { int value = "wrong"; }\n',
    }
    filenames = {language: directory / FILES[language] for language in LANGUAGES}
    for language, file in filenames.items():
        file.write_text(sources[language], encoding="utf-8")
    commands = {
        "wysna": [options.wysna, "check", FILES["wysna"], "--output-format=json-v1"],
        "go": [options.go, "build", "-json", FILES["go"]],
        "rust": [options.rustc, "--crate-name", "bad_type", "--emit=metadata", "--error-format=json",
                 FILES["rust"]],
        "python": [options.python, "-m", "py_compile", FILES["python"]],
        "javascript": [options.node, "--check", FILES["javascript"]],
        "java": [options.javac, "-XDrawDiagnostics", FILES["java"]],
    }
    results = {}
    for language in LANGUAGES:
        result = run(commands[language], directory, env)
        assert result["exit"] == (0 if language in ("python", "javascript") else 1), (language, result)
        for stream in ("stdout", "stderr"):
            result[stream] = result[stream].replace(str(work), "<workspace>")
        results[language] = {**result, "command": commands[language][1:], "source": sources[language]}
    return results


def main():
    options = arguments()
    variants = (*LANGUAGES, BASELINE) if options.baseline_wysna else LANGUAGES
    with tempfile.TemporaryDirectory(prefix="wysna-cross-language-run-") as temporary:
        work = Path(temporary)
        codes = work / "cases"
        shutil.copytree(CASES, codes)
        if options.baseline_wysna:
            shutil.copytree(codes / "wysna", codes / BASELINE)
        bin_dir = work / "bin"
        bin_dir.mkdir()
        inputs = work / "inputs"
        inputs.mkdir()
        fixtures = fixture_set(inputs)
        env, caches = scoped_environment(options, work)
        randomizer = random.Random(31973)

        cold = {}
        warm = {}
        sizes = defaultdict(dict)
        launch = {}
        source_sizes = defaultdict(dict)
        for language in variants:
            for suite in NATIVE_WORKLOADS:
                files = [path for path in (codes / language / suite).rglob("*")
                         if path.suffix in (".wysna", ".go", ".rs", ".py", ".cjs", ".java")]
                source_sizes[language][suite] = {
                    "bytes": sum(path.stat().st_size for path in files),
                    "nonblank_lines": sum(sum(bool(line.strip()) for line in path.read_text().splitlines())
                                          for path in files),
                    "files": len(files),
                }

        cold_order = [language for language in variants if language in COMPILED]
        randomizer.shuffle(cold_order)
        for language in cold_order:
            directory = codes / language / "json"
            output = bin_dir / f"{language}-json"
            cold[language] = build(options, language, "json", directory, output, env, caches)["ms"]
            print(f"{language} JSON cold build: {cold[language]:.0f} ms", flush=True)

        order = list(variants)
        randomizer.shuffle(order)
        for language in order:
            for suite in NATIVE_WORKLOADS:
                directory = codes / language / suite
                output = bin_dir / f"{language}-{suite}"
                if language in COMPILED:
                    if suite != "json":
                        build(options, language, suite, directory, output, env, caches)
                    file_paths = list(output.glob("*.class")) if language == "java" else [output]
                    sizes[language][suite] = sum(path.stat().st_size for path in file_paths)
                launch[(language, suite)] = launch_command(options, language, suite, directory, output)
            warm[language] = startup_edit(options, language, codes / language / "startup",
                                          bin_dir / f"{language}-edited", env, caches)
            print(f"{language} edited CLI p50: {warm[language]['p50_ms']:.2f} ms", flush=True)

        validations = {}
        for language in variants:
            count = 0
            for fixture in fixtures.values():
                command = [*launch[(language, fixture["suite"])], *fixture["args"]]
                assert_output(run(command, work, env), fixture["expected"])
                count += 1
            for invalid in ("truncated", "typed"):
                command = [*launch[(language, "json")], str(inputs / f"invalid-{invalid}.json")]
                assert_output(run(command, work, env), "", 2)
                count += 1
            assert_output(run([*launch[(language, "ledger")], "0"], work, env), "", 2)
            count += 1
            validations[language] = count
        print(f"All {len(variants)} toolchain variants passed eight output and exit-code checks.", flush=True)

        series = defaultdict(lambda: defaultdict(list))
        runtime_first_orders = {}
        for name, fixture in fixtures.items():
            order_seed = list(variants)
            randomizer.shuffle(order_seed)
            runtime_first_orders[name] = order_seed
            for repetition in range(options.samples + 3):
                shift = repetition % len(order_seed)
                order = order_seed[shift:] + order_seed[:shift]
                for language in order:
                    command = [*launch[(language, fixture["suite"])], *fixture["args"]]
                    sample = run(command, work, env)
                    assert_output(sample, fixture["expected"])
                    if repetition >= 3:
                        series[name][language].append(sample)
            print(f"{name}: {options.samples} recorded runs per language", flush=True)

        executables = dict(zip(LANGUAGES, (options.wysna, options.go, options.rustc,
                                           options.python, options.node, options.java), strict=True))
        if options.baseline_wysna:
            executables[BASELINE] = options.baseline_wysna
        versions = {}
        for language, executable in executables.items():
            flag = "version" if language == "go" else "--version"
            version = run([executable, flag], work, env)
            assert version["exit"] == 0, (language, version)
            versions[language] = (version["stdout"] or version["stderr"]).strip()
        probes = diagnostics(options, work, env)
        if sys.platform == "darwin":
            host = run(["/usr/sbin/sysctl", "machdep.cpu.brand_string", "hw.model", "hw.ncpu", "hw.memsize"], work, env)
            os_info = run(["/usr/bin/sw_vers"], work, env)
            hardware = host["stdout"].strip() if host["exit"] == 0 else platform.machine()
            system = os_info["stdout"].strip() if os_info["exit"] == 0 else platform.platform()
        else:
            hardware = f"{platform.machine()}, {os.cpu_count()} logical CPUs"
            system = platform.platform()
        sdks = {}
        for language in ("wysna", BASELINE):
            if language not in executables:
                continue
            manifest_path = Path(executables[language]).resolve().parent.parent / "native-sdk.json"
            manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {}
            sdks[language] = {key: manifest.get(key) for key in ("source_commit", "source_dirty", "target")}
        result = {
            "hardware": hardware, "os": system, "versions": versions, "wysna_sdks": sdks,
            "java_dependency": {"package": "com.fasterxml.jackson.core:jackson-core:2.21.5",
                                "bytes": options.jackson.stat().st_size, "sha256": JACKSON_SHA256},
            "protocol": {"samples": options.samples, "build_samples": options.build_samples,
                         "warmups": 3, "order_seed": 31973, "cold_json_order": cold_order,
                         "runtime_first_orders": runtime_first_orders},
            "validation_checks": validations,
            "fixtures": {name: {key: val for key, val in fixture.items() if key != "args"}
                         for name, fixture in fixtures.items()},
            "runtime": {name: {language: summarize(values) for language, values in languages.items()}
                        for name, languages in series.items()},
            "cold_json_build_ms": cold, "edited_cli": warm,
            "java_class_bytes": sizes.pop("java"), "binary_bytes": sizes,
            "source": source_sizes, "diagnostics": probes,
        }
        options.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
