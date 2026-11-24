"""Measure three cumulative Wysna JSON stages against the same fixture."""
import argparse
import json
import os
from pathlib import Path
import shutil
import tempfile

from run import CASES, assert_output, build, fixture_set, run, scoped_environment, summarize

READ_ONLY = """fn main() {
    let text = read_file(args()[1]) else err { exit(2) return }
    print(len(text))
}
main()
"""
DECODE = """import "std/json" as json

fn decode(source: str) -> int ! json.JsonError {
    let records = json.to_array(json.parse(source)?)?
    return len(records)
}

fn main() {
    let text = read_file(args()[1]) else err { exit(2) return }
    let count = decode(text) else err { exit(2) return }
    print(count)
}
main()
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wysna", default=shutil.which("wysna"))
    parser.add_argument("--output", required=True, type=Path)
    options = parser.parse_args()
    options.rustc = ""
    options.cargo_home = None
    complete = (CASES / "wysna" / "json" / "main.wysna").read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="wysna-json-stages-") as temporary:
        work = Path(temporary)
        fixture = fixture_set(work)["json_10000"]
        env, caches = scoped_environment(options, work)
        configurations = {
            "read": (READ_ONLY, str(fixture["bytes"])),
            "decode": (DECODE, str(fixture["records"])),
            "aggregate": (complete, fixture["expected"]),
        }
        measured = {stage: [] for stage in configurations}
        for stage, (source, _) in configurations.items():
            (work / "main.wysna").write_text(source, encoding="utf-8")
            build(options, "wysna", "json", work, work / stage, env, caches)
        order = tuple(configurations)
        for round_index in range(33):
            shift = round_index % len(order)
            for stage in order[shift:] + order[:shift]:
                sample = run([str(work / stage), *fixture["args"]], work, env)
                assert_output(sample, configurations[stage][1])
                if round_index >= 3:
                    measured[stage].append(sample)
        report = {
            "fixture_sha256": fixture["sha256"],
            "stages": {stage: summarize(samples) for stage, samples in measured.items()},
        }
        options.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
