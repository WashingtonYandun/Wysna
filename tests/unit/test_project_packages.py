"""PKG-A01: Offline native-only USTAR v1 package acquisition, security bounds, and frozen builds.

Adapts all 19 rescued security/lifecycle tests to execute directly against the Selfhost native binary
(`wysna packages pack|fetch|add|remove|restore`) without any Python package manager implementation.
"""
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.Platform import executable_name
from tools.build_selfhost_dev import assemble_dev_sdk


@pytest.fixture(scope="module")
def native_driver() -> Path:
    report = assemble_dev_sdk(["driver", "zig-shim", "project", "compiler"])
    return Path(report["driver"])


def _run(driver: Path, cwd: Path, *args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [str(driver), *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
    )


def _run_json(driver: Path, cwd: Path, *args: str, env_extra: dict | None = None) -> tuple[int, dict]:
    res = _run(driver, cwd, *args, "--output-format=json-v1", env_extra=env_extra)
    assert res.stderr == "", f"unexpected stderr: {res.stderr!r} (stdout={res.stdout!r})"
    return res.returncode, json.loads(res.stdout)


def _create_project(driver: Path, parent: Path, name: str) -> Path:
    rc, payload = _run_json(driver, parent, "project", "new", name)
    assert rc == 0 and payload["status"] == "ok", payload
    return parent / name


def _pack_dependency(driver: Path, tmp_path: Path) -> tuple[Path, Path, str, int]:
    root = _create_project(driver, tmp_path, "dependency")
    (root / "src/values.wysna").write_text("pub fn answer() -> int { return 42 }\n", encoding="utf-8")
    (root / "data.bin").write_bytes(bytes(range(256)))
    artifact = tmp_path / "dependency.tar"
    rc, payload = _run_json(driver, root, "packages", "pack", "--manifest", str(root / "wysna.toml"), "-o", str(artifact))
    assert rc == 0 and payload["status"] == "ok", payload
    digest = payload["result"]["archive_sha256"]
    assert digest == hashlib.sha256(artifact.read_bytes()).hexdigest()
    return root, artifact, digest, payload["result"]["files"]


def test_archive_is_deterministic_canonical_and_binary_safe(native_driver, tmp_path):
    root, artifact, digest, file_count = _pack_dependency(native_driver, tmp_path)
    (root / "build").mkdir()
    (root / "build/ignored").write_text("ignored", encoding="utf-8")
    second = tmp_path / "dependency_again.tar"
    rc, payload = _run_json(
        native_driver, root, "packages", "pack", "--manifest", str(root / "wysna.toml"), "-o", str(second)
    )
    assert rc == 0 and payload["result"]["archive_sha256"] == digest
    assert second.read_bytes() == artifact.read_bytes()
    assert file_count == 5 and len(digest) == 64

    # Verify byte-for-byte compatibility with Python tarfile USTAR v1 reader & binary safety
    with tarfile.open(fileobj=io.BytesIO(artifact.read_bytes()), mode="r:") as tf:
        extracted = {m.name: tf.extractfile(m).read() for m in tf.getmembers()}
    assert extracted["data.bin"] == bytes(range(256))
    assert sorted(extracted) == list(extracted)


def test_fetch_add_lock_restore_and_remove_are_offline(native_driver, tmp_path):
    _, archive, digest, _ = _pack_dependency(native_driver, tmp_path)
    root = _create_project(native_driver, tmp_path, "consumer")
    manifest = root / "wysna.toml"
    before = manifest.read_bytes()
    cache = tmp_path / "cache"

    rc, fetched = _run_json(
        native_driver, root, "packages", "fetch",
        "--manifest", str(manifest), "--sha256", digest, "--archive", str(archive), "--cache", str(cache),
    )
    assert rc == 0 and fetched["result"]["created"] is True
    assert manifest.read_bytes() == before

    rc, added = _run_json(
        native_driver, root, "packages", "add", "values",
        "--manifest", str(manifest), "--path", "vendor/" + digest,
        "--expect-manifest-sha256", hashlib.sha256(before).hexdigest(),
    )
    assert rc == 0 and added["result"]["changed"] is True

    # Idempotent re-add with same path is a no-op
    rc, again = _run_json(
        native_driver, root, "packages", "add", "values",
        "--manifest", str(manifest), "--path", "vendor/" + digest,
    )
    assert rc == 0 and again["result"]["changed"] is False

    # Lock, delete vendor & original archive, restore purely from offline cache, and verify frozen lock
    rc_lock, _ = _run_json(native_driver, root, "project", "lock", "--manifest", str(manifest))
    assert rc_lock == 0
    locked = (root / "wysna.lock").read_bytes()
    shutil.rmtree(root / "vendor")
    archive.unlink()

    rc_res, restored = _run_json(
        native_driver, root, "packages", "restore", "--manifest", str(manifest), "--cache", str(cache)
    )
    assert rc_res == 0 and restored["result"]["packages"][0]["created"] is True
    assert (root / "wysna.lock").read_bytes() == locked
    assert (root / "vendor" / digest / "data.bin").read_bytes() == bytes(range(256))

    rc_chk, chk_payload = _run_json(
        native_driver, root, "project", "check", "--manifest", str(manifest), "--frozen"
    )
    assert rc_chk == 0 and chk_payload["status"] == "ok"

    # Non-destructive remove updates manifest while keeping vendor directory intact
    rc_rm, rm_payload = _run_json(native_driver, root, "packages", "remove", "values", "--manifest", str(manifest))
    assert rc_rm == 0 and rm_payload["result"]["changed"] is True
    assert (root / "vendor" / digest / "data.bin").is_file()
    assert "[dependencies.values]" not in manifest.read_text(encoding="utf-8")


def test_bad_pin_and_cache_corruption_preserve_user_data(native_driver, tmp_path):
    _, archive, digest, _ = _pack_dependency(native_driver, tmp_path)
    root = _create_project(native_driver, tmp_path, "consumer")
    cache = tmp_path / "cache"

    rc_bad, bad = _run_json(
        native_driver, root, "packages", "fetch",
        "--root", str(root), "--sha256", "0" * 64, "--archive", str(archive), "--cache", str(cache),
    )
    assert rc_bad == 2 and bad["diagnostics"][0]["code"] == "W5005"
    assert "SHA-256" in bad["diagnostics"][0]["message"]
    assert not cache.exists()

    rc_ok, _ = _run_json(
        native_driver, root, "packages", "fetch",
        "--root", str(root), "--sha256", digest, "--archive", str(archive), "--cache", str(cache),
    )
    assert rc_ok == 0
    (cache / (digest + ".tar")).write_text("corrupt", encoding="utf-8")

    rc_corrupt, corrupt = _run_json(
        native_driver, root, "packages", "fetch",
        "--root", str(root), "--sha256", digest, "--archive", str(archive), "--cache", str(cache),
    )
    assert rc_corrupt == 2 and corrupt["diagnostics"][0]["code"] == "W5005"
    assert "corrupt" in corrupt["diagnostics"][0]["message"]
    assert (cache / (digest + ".tar")).read_text(encoding="utf-8") == "corrupt"


def test_fetch_never_replaces_modified_vendor_inputs(native_driver, tmp_path):
    _, archive, digest, _ = _pack_dependency(native_driver, tmp_path)
    root = _create_project(native_driver, tmp_path, "consumer")
    cache = tmp_path / "cache"

    rc, _ = _run_json(
        native_driver, root, "packages", "fetch",
        "--root", str(root), "--sha256", digest, "--archive", str(archive), "--cache", str(cache),
    )
    assert rc == 0
    destination = root / "vendor" / digest / "data.bin"
    destination.write_bytes(b"local edits")

    rc2, err = _run_json(
        native_driver, root, "packages", "fetch",
        "--root", str(root), "--sha256", digest, "--archive", str(archive), "--cache", str(cache),
    )
    assert rc2 == 2 and err["diagnostics"][0]["code"] == "W5005"
    assert "differs" in err["diagnostics"][0]["message"]
    assert destination.read_bytes() == b"local edits"


@pytest.mark.parametrize(
    "name",
    ["../escape", "/absolute", "C:/escape", "a\\b", "a/../b", "a//b", "CON", "a./b", "a/b ", "a\nb"],
)
def test_archive_rejects_nonportable_paths_before_materialization(native_driver, tmp_path, name):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        for path in ("wysna.toml", name):
            info = tarfile.TarInfo(path)
            info.size, info.mode, info.uid, info.gid, info.mtime = 1, 0o644, 0, 0, 0
            archive.addfile(info, io.BytesIO(b"x"))
    data = output.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    bad_tar = tmp_path / "bad.tar"
    bad_tar.write_bytes(data)
    work = tmp_path / "work"
    work.mkdir()
    cache = tmp_path / "cache"

    rc, payload = _run_json(
        native_driver, work, "packages", "fetch",
        "--root", str(work), "--sha256", digest, "--archive", str(bad_tar), "--cache", str(cache),
    )
    assert rc == 2 and payload["diagnostics"][0]["code"] == "W5002"
    assert list(work.iterdir()) == []
    assert not cache.exists()


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.CHRTYPE])
def test_archive_rejects_links_and_devices(native_driver, tmp_path, kind):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        info = tarfile.TarInfo("wysna.toml")
        info.type, info.linkname, info.mode, info.uid, info.gid, info.mtime = kind, "outside", 0o644, 0, 0, 0
        archive.addfile(info)
    data = output.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    bad_tar = tmp_path / "bad_kind.tar"
    bad_tar.write_bytes(data)
    work = tmp_path / "work"
    work.mkdir()

    rc, payload = _run_json(
        native_driver, work, "packages", "fetch",
        "--root", str(work), "--sha256", digest, "--archive", str(bad_tar), "--cache", str(tmp_path / "cache"),
    )
    assert rc == 2 and payload["diagnostics"][0]["code"] == "W5002"
    assert list(work.iterdir()) == []


def test_manifest_precondition_failure_keeps_bytes(native_driver, tmp_path):
    root = _create_project(native_driver, tmp_path, "consumer")
    manifest = root / "wysna.toml"
    before = manifest.read_bytes()

    rc, payload = _run_json(
        native_driver, root, "packages", "add", "value",
        "--manifest", str(manifest), "--path", "vendor/missing", "--expect-manifest-sha256", "0" * 64,
    )
    assert rc == 2 and payload["diagnostics"][0]["code"] == "W5005"
    assert "precondition" in payload["diagnostics"][0]["message"]
    assert manifest.read_bytes() == before


def test_invalid_package_manifest_does_not_publish_vendor(native_driver, tmp_path):
    root = _create_project(native_driver, tmp_path, "consumer")
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w", format=tarfile.USTAR_FORMAT) as archive:
        content = b"invalid TOML"
        info = tarfile.TarInfo("wysna.toml")
        info.size, info.mode, info.uid, info.gid, info.mtime = len(content), 0o644, 0, 0, 0
        archive.addfile(info, io.BytesIO(content))
    data = output.getvalue()
    digest = hashlib.sha256(data).hexdigest()
    bad_pkg = tmp_path / "invalid_manifest.tar"
    bad_pkg.write_bytes(data)

    rc, payload = _run_json(
        native_driver, root, "packages", "fetch",
        "--root", str(root), "--sha256", digest, "--archive", str(bad_pkg), "--cache", str(tmp_path / "cache"),
    )
    assert rc == 2 and payload["diagnostics"][0]["code"] == "W5002"
    assert not (root / "vendor" / digest).exists()
    assert list((root / "vendor").iterdir()) == []


def test_restore_missing_lockfile_returns_structured_w5004(native_driver, tmp_path):
    root = _create_project(native_driver, tmp_path, "consumer")
    rc, payload = _run_json(
        native_driver, root, "packages", "restore", "--manifest", str(root / "wysna.toml")
    )
    assert rc == 3 and payload["status"] == "toolchain_error"
    assert payload["diagnostics"][0]["code"] == "W5004"
