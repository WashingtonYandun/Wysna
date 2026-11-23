"""OPS-A01: End-to-end wysofia-ops integration with pinned offline packages, frozen tests, and write-failure recovery."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.Platform import executable_name
from common.Version import WYSNA_VERSION
from tools.build_selfhost_dev import assemble_dev_sdk


@pytest.fixture(scope="module")
def native_driver() -> Path:
    report = assemble_dev_sdk(["driver", "zig-shim", "project", "compiler"])
    return Path(report["driver"])


def _has_zig(driver: Path) -> bool:
    return (driver.parent.parent / "toolchain/zig" / executable_name("zig")).is_file()


def _run(driver: Path, cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(driver), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=180,
    )


def test_ops_a01_wysofia_ops_with_pinned_package_frozen_tests_and_write_recovery(native_driver, tmp_path):
    """Exercise wysofia-ops end-to-end from Native SDK: PKG-A01 pin/restore + frozen TEST-A01 + ingest recovery."""
    # 1. Create and pack a pinned offline policy dependency package (`ops-policy`)
    dep_root = tmp_path / "ops-policy"
    (dep_root / "src").mkdir(parents=True)
    (dep_root / "wysna.toml").write_text(
        'manifest_version = 1\n\n'
        f'[package]\nname = "ops-policy"\nversion = "0.1.0"\nlanguage = "{".".join(WYSNA_VERSION.split(".")[:2])}"\n\n'
        f'[toolchain]\ncompiler = "{WYSNA_VERSION}"\nstdlib = "bundled"\n\n'
        '[sources]\nroot = "src"\n\n'
        '[targets.app]\nentry = "src/rules.wysna"\nprofile = "native-cli"\n',
        encoding="utf-8",
    )
    (dep_root / "src/rules.wysna").write_text(
        "pub fn min_healthy_score() -> int { return 60 }\n"
        "pub fn is_healthy(score: int) -> bool { return score >= min_healthy_score() }\n",
        encoding="utf-8",
    )
    archive = tmp_path / "ops-policy.tar"
    pack_res = _run(native_driver, dep_root, "packages", "pack", "--manifest", str(dep_root / "wysna.toml"), "-o", str(archive), "--output-format=json-v1")
    assert pack_res.returncode == 0, pack_res.stdout + pack_res.stderr
    pack_payload = json.loads(pack_res.stdout)
    digest = pack_payload["result"]["archive_sha256"]

    # 2. Copy examples/wysofia-ops, pin `policy` via PKG-A01, wire a test using `policy`, and lock
    ops_dir = tmp_path / "wysofia-ops"
    shutil.copytree(ROOT / "examples/wysofia-ops", ops_dir)
    manifest = ops_dir / "wysna.toml"
    cache = tmp_path / "offline-cache"
    manifest_sha = hashlib.sha256(manifest.read_bytes()).hexdigest()

    add_res = _run(
        native_driver, ops_dir, "packages", "add", "policy",
        "--manifest", str(manifest), "--sha256", digest, "--archive", str(archive),
        "--cache", str(cache), "--expect-manifest-sha256", manifest_sha, "--output-format=json-v1",
    )
    assert add_res.returncode == 0, add_res.stdout + add_res.stderr

    # Add a project test exercising both wysofia-ops audit + pinned `policy` dependency
    (ops_dir / "tests/test_policy_pin.wysna").write_text(
        'import "audit.wysna" as audit\n'
        'import "pkg/policy/rules" as policy\n'
        "fn main() {\n"
        '    let raw = "[{\\"service\\":\\"api\\",\\"status_code\\":200,\\"latency_ms\\":120,\\"token_cost\\":10}]"\n'
        "    let events = audit.parse_events_json(raw) else err { exit(1) }\n"
        "    let summary = audit.evaluate_batch(events)\n"
        "    if not policy.is_healthy(summary.health_score) { exit(1) }\n"
        '    print("policy-ok:" + str(summary.health_score))\n'
        "}\n"
        "main()\n",
        encoding="utf-8",
    )

    lock_res = _run(native_driver, ops_dir, "project", "lock", "--manifest", str(manifest), "--output-format=json-v1")
    assert lock_res.returncode == 0, lock_res.stdout + lock_res.stderr

    # 3. Clean-directory offline restore: wipe vendor/ + source archive, restore strictly from offline cache
    shutil.rmtree(ops_dir / "vendor")
    archive.unlink()
    restore_res = _run(
        native_driver, ops_dir, "packages", "restore",
        "--manifest", str(manifest), "--cache", str(cache), "--output-format=json-v1",
    )
    assert restore_res.returncode == 0, restore_res.stdout + restore_res.stderr
    restore_payload = json.loads(restore_res.stdout)
    assert restore_payload["result"]["packages"][0]["archive_sha256"] == digest

    # 4. Verify frozen check + lock tamper detection
    chk_res = _run(native_driver, ops_dir, "project", "check", "--manifest", str(manifest), "--frozen", "--output-format=json-v1")
    assert chk_res.returncode == 0 and json.loads(chk_res.stdout)["status"] == "ok", chk_res.stdout + chk_res.stderr

    # Tamper with vendored file -> frozen check must reject!
    rules_vendored = ops_dir / "vendor" / digest / "src/rules.wysna"
    original_rules = rules_vendored.read_bytes()
    rules_vendored.write_text(original_rules.decode("utf-8") + "\n# tampered\n", encoding="utf-8")
    tamper_res = _run(native_driver, ops_dir, "project", "check", "--manifest", str(manifest), "--frozen", "--output-format=json-v1")
    assert tamper_res.returncode != 0, "frozen check must detect altered vendored dependency"
    rules_vendored.write_bytes(original_rules)

    if not _has_zig(native_driver):
        return

    # 5. Run frozen project test --discover --output-format=json-v1 (TEST-A01 + pinned dependency)
    test_report = tmp_path / "ops-tests.json"
    test_res = _run(
        native_driver, ops_dir, "project", "test", "--manifest", str(manifest),
        "--frozen", "--discover", "--report-json", str(test_report), "--output-format=json-v1",
    )
    assert test_res.returncode == 0, test_res.stdout + test_res.stderr
    test_payload = json.loads(test_res.stdout)
    assert test_payload["status"] == "ok"
    assert test_payload["result"]["passed"] == 2 and test_payload["result"]["failed"] == 0
    assert test_report.is_file()

    # 6. Build frozen `ingest` binary and exercise valid, invalid, missing, and simulated write-failure recovery
    ingest_bin = tmp_path / executable_name("ops-ingest")
    build_res = _run(
        native_driver, ops_dir, "project", "build", "--manifest", str(manifest),
        "--frozen", "--target", "ingest", "-o", str(ingest_bin), "--output-format=json-v1",
    )
    assert build_res.returncode == 0 and ingest_bin.is_file(), build_res.stdout + build_res.stderr

    valid_in = tmp_path / "valid.json"
    valid_in.write_text('[{"service":"worker","status_code":503,"latency_ms":1800,"token_cost":40}]', encoding="utf-8")
    invalid_in = tmp_path / "invalid.json"
    invalid_in.write_text('[{"service":"worker","status_code":600,"latency_ms":10,"token_cost":5}]', encoding="utf-8")
    report_out = tmp_path / "report.json"

    # Valid run writes report
    ok_run = subprocess.run([str(ingest_bin), str(valid_in), str(report_out)], capture_output=True, text=True, timeout=30)
    assert ok_run.returncode == 0, ok_run.stderr
    first_report = report_out.read_bytes()
    assert json.loads(first_report)["critical_events"] == 1

    # Invalid domain input exits 1 and preserves prior report untouched
    bad_run = subprocess.run([str(ingest_bin), str(invalid_in), str(report_out)], capture_output=True, text=True, timeout=30)
    assert bad_run.returncode == 1 and "status_code" in bad_run.stderr
    assert report_out.read_bytes() == first_report

    # Simulated write failure (target path is an existing directory) exits 3 with actionable diagnostic
    unwritable_dir = tmp_path / "blocked-report-dir"
    unwritable_dir.mkdir()
    fail_write = subprocess.run([str(ingest_bin), str(valid_in), str(unwritable_dir)], capture_output=True, text=True, timeout=30)
    assert fail_write.returncode == 3 and "cannot write report" in fail_write.stderr
    assert unwritable_dir.is_dir()

    # Recovery run to valid sibling path succeeds cleanly
    recovered_out = tmp_path / "recovered-report.json"
    recover = subprocess.run([str(ingest_bin), str(valid_in), str(recovered_out)], capture_output=True, text=True, timeout=30)
    assert recover.returncode == 0 and recovered_out.read_bytes() == first_report
