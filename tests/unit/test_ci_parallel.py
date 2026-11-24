"""Unit tests for deterministic CI parallelization in run_ci_parallel and ci_conformance."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from tools import ci_conformance
from tools import run_ci_parallel


def _run_parallel_cli(cwd: Path, *args: str, env_extra: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(ROOT / "tools/run_ci_parallel.py"), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def test_run_ci_parallel_concurrent_and_sequential_groups(tmp_path: Path):
    (tmp_path / "mod_sleep_a.py").write_text("import time; time.sleep(0.35)\n", encoding="utf-8")
    (tmp_path / "mod_sleep_b.py").write_text("import time; time.sleep(0.35)\n", encoding="utf-8")
    (tmp_path / "grp_first.py").write_text(
        "from pathlib import Path; import time\n"
        "time.sleep(0.15)\n"
        "Path('group_order.txt').write_text('first\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    (tmp_path / "grp_second.py").write_text(
        "from pathlib import Path\n"
        "text = Path('group_order.txt').read_text(encoding='utf-8')\n"
        "assert text == 'first\\n', text\n"
        "Path('group_order.txt').write_text(text + 'second\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )

    started = time.monotonic()
    res = _run_parallel_cli(
        tmp_path,
        "--mode",
        "module",
        "--jobs",
        "3",
        "--group",
        "grp_first,grp_second",
        "mod_sleep_a",
        "mod_sleep_b",
    )
    elapsed = time.monotonic() - started

    assert res.returncode == 0, res.stdout + res.stderr
    assert elapsed < 0.70, f"Expected concurrent execution under 0.70s, took {elapsed:.2f}s"
    assert (tmp_path / "group_order.txt").read_text(encoding="utf-8") == "first\nsecond\n"
    assert "[PASS " in res.stdout
    assert "group[grp_first,grp_second]" in res.stdout


def test_run_ci_parallel_fail_fast_false_and_deterministic_report(tmp_path: Path):
    (tmp_path / "mod_fail_early.py").write_text(
        "import sys, time\n"
        "time.sleep(0.05)\n"
        "print('STDOUT_MARKER_FIRST')\n"
        "print('STDERR_MARKER_FIRST', file=sys.stderr)\n"
        "raise SystemExit(3)\n",
        encoding="utf-8",
    )
    (tmp_path / "mod_fail_late.py").write_text(
        "import sys, time\n"
        "time.sleep(0.12)\n"
        "print('STDOUT_MARKER_SECOND')\n"
        "raise SystemExit(4)\n",
        encoding="utf-8",
    )
    (tmp_path / "mod_sibling_slow.py").write_text(
        "from pathlib import Path; import time\n"
        "time.sleep(0.25)\n"
        "Path('sibling_completed.txt').write_text('done', encoding='utf-8')\n",
        encoding="utf-8",
    )

    res = _run_parallel_cli(
        tmp_path,
        "--mode",
        "module",
        "--jobs",
        "3",
        "mod_fail_early",
        "mod_fail_late",
        "mod_sibling_slow",
    )

    assert res.returncode != 0
    assert (tmp_path / "sibling_completed.txt").read_text(encoding="utf-8") == "done"
    assert "[PASS " in res.stdout and "mod_sibling_slow" in res.stdout
    assert "STDOUT_MARKER_FIRST" in res.stdout
    assert "STDOUT_MARKER_SECOND" in res.stdout
    assert res.stdout.index("STDOUT_MARKER_FIRST") < res.stdout.index("STDOUT_MARKER_SECOND")
    assert "STDERR_MARKER_FIRST" in res.stderr


def test_run_ci_parallel_pytest_mode_and_validation(tmp_path: Path):
    (tmp_path / "test_g1.py").write_text(
        "from pathlib import Path; import time\n"
        "def test_one():\n"
        "    time.sleep(0.1)\n"
        "    Path('pytest_seq.txt').write_text('1\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    (tmp_path / "test_g2.py").write_text(
        "from pathlib import Path\n"
        "def test_two():\n"
        "    cur = Path('pytest_seq.txt').read_text(encoding='utf-8')\n"
        "    assert cur == '1\\n', cur\n"
        "    Path('pytest_seq.txt').write_text(cur + '2\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    (tmp_path / "test_p1.py").write_text(
        "from pathlib import Path\n"
        "def test_independent(tmp_path):\n"
        "    (tmp_path / 'local.txt').write_text('ok', encoding='utf-8')\n"
        "    Path('p1_done.txt').write_text('ok', encoding='utf-8')\n",
        encoding="utf-8",
    )

    res = _run_parallel_cli(
        tmp_path,
        "--mode",
        "pytest",
        "--jobs",
        "2",
        "--group",
        "test_g1.py,test_g2.py",
        "test_p1.py",
    )
    assert res.returncode == 0, res.stdout + res.stderr
    assert (tmp_path / "pytest_seq.txt").read_text(encoding="utf-8") == "1\n2\n"
    assert (tmp_path / "p1_done.txt").read_text(encoding="utf-8") == "ok"

    empty_res = _run_parallel_cli(tmp_path, "--mode", "module")
    assert empty_res.returncode != 0

    bad_group = _run_parallel_cli(tmp_path, "--mode", "module", "--group", " , ")
    assert bad_group.returncode != 0

    bad_jobs = _run_parallel_cli(tmp_path, "--mode", "module", "--jobs", "0", "mod_a")
    assert bad_jobs.returncode != 0

    with patch.dict(os.environ, {"WYSNA_CI_JOBS": "3"}):
        assert run_ci_parallel.resolve_jobs(None) == 3
        assert run_ci_parallel.resolve_jobs(2) == 2

    built_tasks = run_ci_parallel.build_tasks("pytest", ["test_g1.py,test_g2.py"], ["test_p1.py"])
    assert len(built_tasks) == 2
    for _, _, commands in built_tasks:
        for _, cmd in commands:
            assert "-p" in cmd and "no:cacheprovider" in cmd, cmd


def test_ci_conformance_resolve_jobs_and_deterministic_order(tmp_path: Path, capsys):
    assert ci_conformance._resolve_jobs(["--profile=release", "--jobs=2"]) == 2
    assert ci_conformance._resolve_jobs(["--jobs=1"]) == 1
    with patch.dict(os.environ, {"WYSNA_CI_JOBS": "3"}):
        assert ci_conformance._resolve_jobs(["--sanitize"]) == 3
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("WYSNA_CI_JOBS", None)
        expected_default = min(4, max(1, os.cpu_count() or 2))
        expected_sanitize = min(2, max(1, os.cpu_count() or 2))
        assert ci_conformance._resolve_jobs(["--profile=release"]) == expected_default
        assert ci_conformance._resolve_jobs(["--sanitize"]) == expected_sanitize

    for bad_args in (["--jobs=0"], ["--jobs=-2"], ["--jobs=abc"], ["--unknown-flag"]):
        try:
            ci_conformance._resolve_jobs(bad_args)
        except SystemExit:
            pass
        else:
            raise AssertionError(f"Expected SystemExit for {bad_args!r}")

    with patch.dict(os.environ, {"WYSNA_CI_JOBS": "0"}):
        try:
            ci_conformance._resolve_jobs([])
        except SystemExit:
            pass
        else:
            raise AssertionError("Expected SystemExit for WYSNA_CI_JOBS=0")

    # Verify out-of-order completion still emits deterministic fixture order and summary.
    root_dir = tmp_path / "repo"
    pos_dir = root_dir / "tests/runtime"
    neg_dir = root_dir / "tests/errors"
    pos_dir.mkdir(parents=True)
    neg_dir.mkdir(parents=True)
    fix_a = pos_dir / "a_slow.wysna"
    fix_a.write_text("print(1)\n", encoding="utf-8")
    fix_a.with_suffix(".expected").write_text("1\n", encoding="utf-8")
    fix_b = pos_dir / "b_fast.wysna"
    fix_b.write_text("print(2)\n", encoding="utf-8")
    fix_b.with_suffix(".expected").write_text("2\n", encoding="utf-8")
    fix_c = neg_dir / "invalid_c.wysna"
    fix_c.write_text("let x: int = \"bad\"\n", encoding="utf-8")

    artifacts_dir = tmp_path / "artifacts"
    stage2 = tmp_path / "stage2"
    stage2.write_text("stub", encoding="utf-8")
    profiles = {
        "support_files": {},
        "fixtures": {
            "tests/runtime/a_slow.wysna": "core",
            "tests/runtime/b_fast.wysna": "core",
            "tests/errors/invalid_c.wysna": "core",
        },
        "features": {"core": {}},
    }
    prov = {"version": 1, "sha": "deadbeef", "inputs": {}, "digest": "00"}

    def fake_verify_case(source, label, selfhost_bin, directory, expected, sanitize, strict, is_neg):
        if "a_slow" in label:
            time.sleep(0.12)
        elif "b_fast" in label:
            time.sleep(0.01)
        return True, "OK", {
            "label": label,
            "feature": "core",
            "unsupported": {},
            "results": [{"engine": eng, "status": "passed"} for eng in ci_conformance.ENGINES],
        }

    class _FakeProc:
        stdout = "clang version test\n"

    with (
        patch.object(ci_conformance, "ROOT", root_dir),
        patch.object(ci_conformance, "PROFILES", profiles),
        patch.object(ci_conformance, "source_fingerprint", return_value=prov),
        patch.object(ci_conformance, "corpus_paths", return_value=([fix_a, fix_b], [fix_c])),
        patch.object(ci_conformance, "execute", return_value={"returncode": 0, "stdout": json.dumps(str(stage2))}),
        patch.object(ci_conformance, "bootstrap_binary", return_value=stage2),
        patch.object(ci_conformance, "verify_case", side_effect=fake_verify_case),
        patch.object(ci_conformance.subprocess, "run", return_value=_FakeProc()),
        patch.dict(os.environ, {"WYSNA_CI_ARTIFACTS": str(artifacts_dir)}),
        patch.object(sys, "argv", ["ci_conformance.py", "--profile=release", "--jobs=3"]),
    ):
        rc = ci_conformance.main()

    assert rc == 0
    captured = capsys.readouterr().out
    pass_lines = [line.strip() for line in captured.splitlines() if line.strip().startswith("[PASS]")]
    assert pass_lines == [
        "[PASS] tests/runtime/a_slow.wysna",
        "[PASS] tests/runtime/b_fast.wysna",
        "[PASS] tests/errors/invalid_c.wysna",
    ], pass_lines
    summary = json.loads((artifacts_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["passed_cases"] == 3
    assert summary["total_cases"] == 3
    assert summary["coverage"]["selfhost"]["passed"] == 3


def test_cached_sdk_binary_is_not_replaced(tmp_path: Path, monkeypatch):
    from tools.build_selfhost_dev import _copy_atomic

    source = tmp_path / "cached" / "wysna.exe"
    destination = tmp_path / "installed" / "wysna.exe"
    source.parent.mkdir()
    source.write_bytes(b"original")
    _copy_atomic(source, destination)

    replaced = []
    replace = Path.replace

    def record_replacement(temporary, target):
        replaced.append(target)
        return replace(temporary, target)

    monkeypatch.setattr(Path, "replace", record_replacement)
    _copy_atomic(source, destination)
    assert replaced == []

    source.write_bytes(b"updated")
    _copy_atomic(source, destination)
    assert replaced == [destination]
    assert destination.read_bytes() == b"updated"
