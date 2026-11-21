#!/usr/bin/env python3
"""Compare file ingestion in Seed and an isolated installed native SDK, without network."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.validate_native_sdk import isolated_environment


def validate(sdk: Path) -> dict:
    provenance = json.loads((sdk / "native-sdk.json").read_text(encoding="utf-8"))
    report = {"schema": "wysna.ops-workflow/1", "success": False,
              "sdk_source_commit": provenance["source_commit"], "checks": []}
    with tempfile.TemporaryDirectory(prefix="wysna ingestion á ") as temporary:
        work = Path(temporary)
        application = work / "application with spaces"
        shutil.copytree(ROOT / "examples/wysofia-ops", application)
        environment = isolated_environment(work)
        executable = sdk / ("bin/wysna.exe" if os.name == "nt" else "bin/wysna")
        manifest = application / "wysna.toml"
        binary = work / ("ingest.exe" if os.name == "nt" else "ingest")
        for command in ("check", "build"):
            arguments = [str(executable), "project", command, "--manifest", str(manifest), "--target", "ingest"]
            if command == "build":
                arguments += ["-o", str(binary)]
            result = subprocess.run(arguments, cwd=work, env=environment, capture_output=True, timeout=180)
            if result.returncode:
                raise AssertionError((command, result.stdout, result.stderr))
        cases = [
            ("valid", [{"service": "worker", "status_code": 503, "latency_ms": 1800, "token_cost": 40}], 0, None),
            ("empty", [], 0, None),
            ("invalid-status", [{"service": "worker", "status_code": 600, "latency_ms": 1, "token_cost": 1}], 1, "status_code"),
            ("negative-cost", [{"service": "worker", "status_code": 200, "latency_ms": 1, "token_cost": -1}], 1, "token_cost"),
            ("malformed", "{broken", 1, "invalid batch"),
            ("missing-input", None, 2, "cannot read input"),
        ]
        for name, payload, expected_status, diagnostic in cases:
            source = work / (name + ".json")
            if payload is not None:
                source.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")
            observations = []
            for engine in ("seed", "native"):
                output = work / (engine + "-report.json")
                output.write_text("preserve existing report\n", encoding="utf-8")
                command = ([sys.executable, "-I", str(ROOT / "wysna"), "run",
                            str(application / "src/ingest.wysna"), "--engine=seed", "--"]
                           if engine == "seed" else [str(binary)])
                result = subprocess.run(command + [str(source), str(output)], cwd=work,
                                        env=None if engine == "seed" else environment,
                                        capture_output=True, timeout=180)
                if result.returncode != expected_status:
                    raise AssertionError((engine, name, result.returncode, result.stdout, result.stderr))
                persisted = output.read_bytes()
                if expected_status:
                    if persisted != b"preserve existing report\n" or diagnostic.encode() not in result.stderr:
                        raise AssertionError((engine, name, persisted, result.stderr))
                else:
                    if json.loads(persisted) != json.loads(result.stdout):
                        raise AssertionError((engine, name, persisted, result.stdout))
                    expected = {"critical_events": 1 if name == "valid" else 0,
                                "health_score": 50 if name == "valid" else 100,
                                "total_events": 1 if name == "valid" else 0,
                                "total_tokens": 40 if name == "valid" else 0, "warning_events": 0}
                    if json.loads(persisted) != expected:
                        raise AssertionError((engine, name, persisted, expected))
                observations.append((result.returncode, result.stdout, persisted))
            if observations[0] != observations[1]:
                raise AssertionError((name, observations))
            report["checks"].append({"name": name, "seed_native_equal": True,
                                     "preserves_output_on_rejection": expected_status != 0})
        report["success"] = True
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sdk", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    result = validate(args.sdk.resolve())
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"success": result["success"], "cases": len(result["checks"])}))
