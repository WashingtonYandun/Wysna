"""Measure project impact against observable test changes in real Wysna projects."""

import argparse
from dataclasses import dataclass
import io
import json
from pathlib import Path
import re
import shutil
import statistics
import subprocess
import sys
import tarfile
import tempfile
import time


ROOT = Path(__file__).resolve().parents[2]
SEED = [sys.executable, str(ROOT / "main.py")]


@dataclass(frozen=True)
class Mutation:
    project: str
    source: str
    symbol: str
    before: str
    after: str


MUTATIONS = (
    Mutation("shared-browser-app", "src/pricing.wysna", "calculate_total_cents",
             "return quantity * unit_price_cents", "return quantity * unit_price_cents + 1"),
    Mutation("shared-browser-app", "src/pricing.wysna", "quote_validation_code",
             "quantity > 1000", "quantity > 999"),
    Mutation("wysofia-ops", "src/audit.wysna", "compute_risk_points",
             "points += 30", "points += 31"),
    Mutation("wysofia-ops", "src/audit.wysna", "severity_label",
             'Severity.Critical => "critical"', 'Severity.Critical => "critical-escalated"'),
    Mutation("wysofia-ops", "src/audit.wysna", "render_summary_json",
             '"total_events": json.from_int(summary.total_events)',
             '"processed_events": json.from_int(summary.total_events)'),
)


def timed_run(command, cwd, *, allowed=(0,)):
    started = time.perf_counter()
    result = subprocess.run(command, cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=120)
    elapsed_ms = (time.perf_counter() - started) * 1000
    if result.returncode not in allowed:
        raise RuntimeError(f"{result.returncode} from {command!r}\n{result.stderr.strip()}\n{result.stdout[-1000:]}")
    return result.stdout, elapsed_ms


def test_report(project, output, test_filter=None):
    args = SEED + ["project", "test", "--manifest", "wysna.toml", "--report-json", str(output)]
    if test_filter is not None:
        args += ["--filter", test_filter]
    _, elapsed_ms = timed_run(args, project, allowed=(0, 1))
    report = json.loads(output.read_text(encoding="utf-8"))
    return {case["name"]: case for case in report["cases"]}, elapsed_ms


def test_outcomes(before, after):
    comparable = {
        name for name in before.keys() & after.keys()
        if before[name]["entry_sha256"] == after[name]["entry_sha256"]
        and before[name]["status"] == "passed"
        and not after[name]["output_limited"]
        and after[name]["status"] in ("passed", "failed")
    }
    changed = {
        name for name in comparable
        if (before[name]["exit_code"], before[name]["stdout_sha256"])
        != (after[name]["exit_code"], after[name]["stdout_sha256"])
    }
    return comparable, changed


def lexical_tokens(text):
    return len(re.findall(r"\w+|[^\w\s]", text))


def context_size(compiler, project, source, symbol, entries):
    files = sorted(project.rglob("*.wysna"))
    full_context = "\n".join(file.read_text(encoding="utf-8") for file in files)
    raw, inspect_ms = timed_run(
        compiler + ["inspect", str(source), "--symbol", symbol, "--with-deps", "--output-format=json-v1"],
        project,
    )
    inspection = json.loads(raw)["result"]
    graph_context = json.dumps({"entries": entries, "inspection": inspection}, ensure_ascii=False)
    return {
        "project_files": len(files),
        "project_lexical_tokens": lexical_tokens(full_context),
        "graph_lexical_tokens": lexical_tokens(graph_context),
        "inspect_ms": round(inspect_ms, 2),
    }


def measure(compiler, project, source, symbol, repetitions, comparable, changed, after, all_ms, report):
    graph_durations = []
    for _ in range(repetitions):
        raw, elapsed_ms = timed_run(
            compiler + ["graph", "--manifest", "wysna.toml", "--symbol", symbol,
                        "--rel", "impact", "--output-format=json-v1"], project,
        )
        graph_durations.append(elapsed_ms)
    entries = json.loads(raw)["result"]["entries"]
    states = {entry["name"]: entry["impact_status"] for entry in entries if entry["kind"] == "test"}
    if set(states) != set(after):
        raise ValueError(f"Graph and test runner disagree on the manifest's test identities: {states!r}")
    affected = {name for name in comparable if states[name] == "affected"}
    uncertain = {name for name in comparable if states[name] == "uncertain"}
    selected = {name for name in after if states[name] in ("affected", "uncertain")}
    selected_ms = all_ms
    if selected != set(after):
        selected_ms = 0
        for name in sorted(selected):
            cases, duration = test_report(project, report, test_filter=name)
            if set(cases) != {name}:
                raise ValueError(f"Test filter for {name!r} was ambiguous")
            selected_ms += duration
    query_ms = statistics.median(graph_durations)
    return {
        "test_states": states,
        "confirmed_hits": sorted(changed & affected),
        "uncertain_hits": sorted(changed & uncertain),
        "missed_changed_tests": sorted(changed - selected),
        "uncertain_tests": sorted(uncertain),
        "selected_tests": len(selected),
        "total_tests": len(after),
        "all_tests_wall_ms": round(all_ms, 2),
        "graph_wall_ms_median": round(query_ms, 2),
        "selected_tests_wall_ms": round(selected_ms, 2),
        "graph_and_selected_tests_wall_ms": round(query_ms + selected_ms, 2),
        **context_size(compiler, project, source, symbol, entries),
    }


def copy_revision(project, revision, output):
    if revision == "WORKTREE":
        shutil.copytree(ROOT / project, output)
        return
    archive = subprocess.check_output(["git", "archive", revision, "--", project.as_posix()], cwd=ROOT)
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:") as source:
        for member in source:
            if member.isfile():
                relative = Path(member.name).relative_to(project)
                target = output / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.extractfile(member) as content:
                    target.write_bytes(content.read())


def evaluate_case(base, candidate, source, symbols, repetitions, destination, compilers):
    for project in (base, candidate):
        timed_run(SEED + ["project", "check", "--manifest", "wysna.toml"], project)
    before, _ = test_report(base, destination)
    after, all_ms = test_report(candidate, destination)
    comparable, changed = test_outcomes(before, after)
    return {
        "comparable_tests": sorted(comparable),
        "incomparable_tests": sorted((before.keys() | after.keys()) - comparable),
        "observed_changed_tests": sorted(changed),
        "queries": [
            {"symbol": symbol, "engine": engine,
             **measure(compiler, candidate, candidate / source, symbol, repetitions,
                       comparable, changed, after, all_ms, destination)}
            for symbol in symbols
            for engine, compiler in compilers.items()
        ],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", type=Path, help="Repository-relative project for a revision comparison")
    parser.add_argument("--base")
    parser.add_argument("--head", default="WORKTREE")
    parser.add_argument("--entry", type=Path, help="Project-relative entry containing or importing the changed symbols")
    parser.add_argument("--symbol", action="append")
    parser.add_argument("--native-compiler", type=Path, help="Standalone compiler binary for parallel graph measurements")
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.repetitions < 1:
        parser.error("--repetitions must be positive")
    comparison = any((args.project, args.base, args.entry, args.symbol))
    if comparison and not all((args.project, args.base, args.entry, args.symbol)):
        parser.error("A revision comparison requires --project, --base, --entry and --symbol")
    if comparison and (args.project.is_absolute() or args.entry.is_absolute()
                       or ".." in args.project.parts or ".." in args.entry.parts):
        parser.error("Project and entry must be contained relative paths")
    compilers = {"seed": SEED}
    if args.native_compiler:
        compilers["selfhost"] = [str(args.native_compiler.resolve()), "--stdlib-root", str(ROOT / "stdlib")]
    cases = []
    with tempfile.TemporaryDirectory(prefix="wysna-impact-benchmark-") as temporary:
        work = Path(temporary)
        destination = work / "test-report.json"
        if comparison:
            base, candidate = work / "before", work / "after"
            copy_revision(args.project, args.base, base)
            copy_revision(args.project, args.head, candidate)
            case = evaluate_case(base, candidate, args.entry, args.symbol, args.repetitions, destination, compilers)
            cases.append({"project": args.project.as_posix(), "base": args.base, "head": args.head, **case})
        else:
            for index, mutation in enumerate(MUTATIONS):
                project = f"examples/{mutation.project}"
                base, candidate = work / f"before-{index}", work / f"after-{index}"
                for directory in (base, candidate):
                    copy_revision(Path(project), "WORKTREE", directory)
                changed_file = candidate / mutation.source
                source = changed_file.read_text(encoding="utf-8")
                if source.count(mutation.before) != 1:
                    raise ValueError(f"Mutation anchor for {mutation.symbol} is no longer unique")
                changed_file.write_text(source.replace(mutation.before, mutation.after), encoding="utf-8")
                case = evaluate_case(base, candidate, mutation.source, [mutation.symbol],
                                     args.repetitions, destination, compilers)
                cases.append({"project": project, "mutation": mutation.symbol, **case})
    output = {
        "schema_version": "wysna.impact-benchmark/1",
        "mode": "revisions" if comparison else "mutations",
        "repetitions": args.repetitions,
        "token_counting_method": "lexical-proxy",
        "cases": cases,
    }
    text = json.dumps(output, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    else:
        print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
