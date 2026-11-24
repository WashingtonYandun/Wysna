#!/usr/bin/env python3
"""Compare JSON serialization with two installed Wysna SDKs."""
import argparse
from argparse import Namespace
import json
from pathlib import Path
import shutil
import tempfile

from run import CASES, assert_output, build, run, scoped_environment, summarize

FIXTURES = {
    "unescaped_unicode": "texto aéreo 中 😀 0123456789 " * 1600,
    "mixed_escapes": ('texto aéreo 中 😀' * 8 + '"\\\t\n\x01') * 200,
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", required=True, type=Path)
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix="wysna-json-encoding-") as directory:
        root = Path(directory)
        shutil.copy2(CASES / "wysna/encode/main.wysna", root)
        env, caches = scoped_environment(Namespace(rustc="", cargo_home=None), root)
        engines = {"reference": args.reference.resolve(), "candidate": args.candidate.resolve()}
        for label, executable in engines.items():
            options = Namespace(wysna=str(executable))
            build(options, "wysna", "encode", root, root / label, env, caches)
        metadata = {}
        for label, executable in engines.items():
            manifest = json.loads((executable.parent.parent / "native-sdk.json").read_text())
            metadata[label] = manifest["source_commit"]
        records = {}
        for name, data in FIXTURES.items():
            fixture = root / name
            fixture.write_text(data, encoding="utf-8")
            expected = json.dumps(data, ensure_ascii=False)
            samples = {label: [] for label in engines}
            order = list(engines)
            for iteration in range(33):
                for label in order:
                    result = run([str(root / label), str(fixture)], root, env)
                    assert_output(result, expected)
                    if iteration >= 3:
                        samples[label].append(result)
                order.reverse()
            records[name] = {label: summarize(values) for label, values in samples.items()}
            records[name]["input_bytes"] = fixture.stat().st_size
        result = {"toolchain_revisions": metadata, "fixtures": records}
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
