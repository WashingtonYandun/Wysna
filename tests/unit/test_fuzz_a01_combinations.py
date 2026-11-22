"""FUZZ-A01: Multi-file differential fuzzing, delta reduction, and v0.8 reference project compatibility."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tests.engine_harness import ENGINES, compare, run_engine
from tests.fuzz.multifile_fuzzer import (
    MultiFileCase,
    NEGATIVE_KINDS,
    generate_multifile_positive,
    reduce_multifile_failure,
    run_multifile_case,
    run_multifile_fuzz_campaign,
    save_divergence_artifact,
)


def _selfhost_compiler() -> Path:
    from compiler.Bootstrap import ensure_selfhost_compiler_binary
    return Path(ensure_selfhost_compiler_binary(str(ROOT)))


def _run_cli(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    return subprocess.run(
        [sys.executable, str(ROOT / "main.py"), *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )


class TestFuzzA01Combinations(unittest.TestCase):
    def test_fuzz_a01_multifile_differential_campaign(self):
        """Seeded multi-file positive + all 12 negative classes agree across all 4 engines with zero W9999."""
        summary = run_multifile_fuzz_campaign(
            positive_count=6,
            negative_count=len(NEGATIVE_KINDS),
            base_seed=9300,
            selfhost_bin=_selfhost_compiler(),
            sanitize=False,
        )
        self.assertEqual(summary["divergences"], [], json.dumps(summary["divergences"][:2], indent=2))

    def test_fuzz_a01_regression_qualified_struct_spread_requires_and_generic_enum(self):
        """Permanent regression for the 3 bugs discovered by FUZZ-A01 across all 4 engines."""
        with tempfile.TemporaryDirectory(prefix="wysna-fuzz-reg-") as td:
            tmp_path = Path(td)
            case = MultiFileCase(
                seed=1,
                files={
                    "types.wysna": (
                        "pub struct Metric {\n"
                        "    label: str\n"
                        "    value: int requires value >= -1000\n"
                        "    tags: list[str]\n"
                        "}\n"
                        "pub struct Boxed[T] {\n"
                        "    tag: str\n"
                        "    item: T\n"
                        "}\n"
                        "pub enum Fault[T] {\n"
                        "    Empty\n"
                        "    Rejected(T)\n"
                        "}\n"
                    ),
                    "main.wysna": (
                        'import "types.wysna" as t\n'
                        "fn score(f: t.Fault[int]) -> int {\n"
                        "    return match f {\n"
                        "        t.Fault.Empty => 0,\n"
                        "        t.Fault.Rejected(v) => v * 2,\n"
                        "    }\n"
                        "}\n"
                        "fn score_box(f: t.Fault[t.Boxed[int]]) -> int {\n"
                        "    return match f {\n"
                        "        t.Fault.Empty => -1,\n"
                        "        t.Fault.Rejected(b) => b.item,\n"
                        "    }\n"
                        "}\n"
                        "fn main() {\n"
                        '    let m1 = t.Metric { label: "a", value: 10, tags: ["x"] }\n'
                        "    let m2 = t.Metric { ..m1, value: 25 }\n"
                        '    let b1 = t.Boxed[int] { tag: "k", item: 7 }\n'
                        "    let b2 = t.Boxed[int] { ..b1, item: 9 }\n"
                        "    let e1 = t.Fault[int].Empty\n"
                        "    let e2 = t.Fault[int].Rejected(11)\n"
                        "    let e3 = t.Fault[t.Boxed[int]].Rejected(b2)\n"
                        "    print(m1.value, len(m1.tags), m2.value, b1.item, b2.item, score(e1), score(e2), score_box(e3))\n"
                        "}\n"
                        "main()\n"
                    ),
                },
                entry="main.wysna",
            )
            results = run_multifile_case(case, _selfhost_compiler(), tmp_path)
            eq, reason = compare(results)
            self.assertTrue(eq, reason)
            for res in results:
                self.assertTrue(res.compile_ok and res.runtime_ok and res.exit_code == 0, str(res))
                self.assertEqual(res.stdout.strip(), "10 1 25 7 9 0 22 9", str(res))

    def test_fuzz_a01_multifile_delta_reducer_and_artifact_saving(self):
        """Multi-file delta-debugging minimizes redundant lines while preserving failure and saves repro artifacts."""
        with tempfile.TemporaryDirectory(prefix="wysna-fuzz-red-") as td:
            tmp_path = Path(td)
            case = MultiFileCase(
                seed=42,
                files={
                    "helper.wysna": (
                        "# noise 1\n"
                        "# noise 2\n"
                        "pub fn trigger() -> int { return 1 }\n"
                        "# noise 3\n"
                    ),
                    "main.wysna": (
                        "# banner\n"
                        'import "helper.wysna" as h\n'
                        "fn main() {\n"
                        "    let unused = 0\n"
                        "    print(h.trigger())\n"
                        "}\n"
                    ),
                },
                entry="main.wysna",
            )

            def oracle(files: dict[str, str]) -> bool:
                return "pub fn trigger()" in files["helper.wysna"] and "h.trigger()" in files["main.wysna"]

            reduced = reduce_multifile_failure(case, oracle, max_steps=30)
            self.assertTrue(oracle(reduced))
            before_lines = sum(len(v.splitlines()) for v in case.files.values())
            after_lines = sum(len(v.splitlines()) for v in reduced.values())
            self.assertLess(after_lines, before_lines)

            out_dir = save_divergence_artifact(
                {
                    "kind": "positive",
                    "seed": 42,
                    "reason": "synthetic test",
                    "files": reduced,
                    "results": [],
                },
                tmp_path / "failures",
            )
            self.assertTrue((out_dir / "helper.wysna").is_file())
            self.assertTrue((out_dir / "main.wysna").is_file())
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["seed"], 42)
            self.assertEqual(report["kind"], "positive")

    def test_fuzz_a01_v08_reference_projects_cli_and_json_contracts(self):
        """v0.8 reference project (wysofia-ops + multi-file generics/errors/closures/async) keeps CLI/JSON contracts."""
        with tempfile.TemporaryDirectory(prefix="wysna-fuzz-ref-") as td:
            tmp_path = Path(td)
            ops_copy = tmp_path / "wysofia-ops"
            shutil.copytree(ROOT / "examples/wysofia-ops", ops_copy)
            manifest = ops_copy / "wysna.toml"
            checked = _run_cli(ops_copy, "project", "check", "--manifest", str(manifest), "--output-format=json-v1")
            self.assertEqual(checked.returncode, 0, checked.stderr)
            check_payload = json.loads(checked.stdout)
            self.assertEqual(check_payload["schema_version"], "wysna.tool-result/1")
            self.assertEqual(check_payload["command"], "project.check")
            self.assertEqual(check_payload["status"], "ok")

            tested = _run_cli(ops_copy, "project", "test", "--manifest", str(manifest), "--output-format=json-v1")
            self.assertEqual(tested.returncode, 0, tested.stderr)
            test_payload = json.loads(tested.stdout)
            self.assertEqual(test_payload["schema_version"], "wysna.tool-result/1")
            self.assertEqual(test_payload["command"], "project.test")
            self.assertEqual(test_payload["status"], "ok")
            self.assertGreaterEqual(test_payload["result"]["passed"], 1)
            self.assertEqual(test_payload["result"]["failed"], 0)

            ref_dir = tmp_path / "ref_v08_combo"
            (ref_dir / "src").mkdir(parents=True)
            (ref_dir / "tests").mkdir(parents=True)
            case = generate_multifile_positive(9301)
            for name, src in case.files.items():
                (ref_dir / "src" / name).write_text(src, encoding="utf-8")
            (ref_dir / "tests/test_combo.wysna").write_text(
                'import "pipeline.wysna" as pipe\n'
                "fn main() {\n"
                '    let out = await pipe.run_pipeline("ok", 10)\n'
                '    if out != "ok:24" { exit(1) }\n'
                "}\n"
                "main()\n"
                if "pub async fn run_pipeline" in case.files["pipeline.wysna"] else
                'import "pipeline.wysna" as pipe\n'
                "fn main() {\n"
                '    let out = pipe.run_pipeline("ok", 10)\n'
                '    if out != "ok:21" { exit(1) }\n'
                "}\n"
                "main()\n",
                encoding="utf-8",
            )
            (ref_dir / "wysna.toml").write_text(
                'manifest_version = 1\n\n'
                '[package]\nname = "ref-v08-combo"\nversion = "0.1.0"\nlanguage = "0.8"\n\n'
                '[toolchain]\ncompiler = "0.8.0"\nstdlib = "bundled"\n\n'
                '[sources]\nroot = "src"\n\n'
                '[targets.app]\nentry = "src/main.wysna"\nprofile = "native-cli"\n\n'
                '[[tests]]\nname = "combo"\nentry = "tests/test_combo.wysna"\ntimeout_ms = 10000\n',
                encoding="utf-8",
            )
            ref_check = _run_cli(ref_dir, "project", "check", "--manifest", str(ref_dir / "wysna.toml"), "--output-format=json-v1")
            self.assertEqual(ref_check.returncode, 0, ref_check.stdout + ref_check.stderr)
            self.assertEqual(json.loads(ref_check.stdout)["status"], "ok")

            ref_test = _run_cli(ref_dir, "project", "test", "--manifest", str(ref_dir / "wysna.toml"), "--output-format=json-v1")
            self.assertEqual(ref_test.returncode, 0, ref_test.stdout + ref_test.stderr)
            ref_test_payload = json.loads(ref_test.stdout)
            self.assertEqual(ref_test_payload["status"], "ok")
            self.assertEqual(ref_test_payload["result"]["passed"], 1)


if __name__ == "__main__":
    unittest.main()
