#!/usr/bin/env python3
"""Validate accepted CI installer artifacts and stage immutable release assets."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil


PLATFORMS = {
    "windows": {
        "platform": "x86_64-windows",
        "artifact": "wysna-windows-x64-installer",
        "extensions": {".exe"},
    },
    "macos": {
        "platform": "aarch64-macos",
        "artifact": "wysna-seed-installers-macOS-ARM64",
        "extensions": {".pkg"},
    },
    "linux": {
        "platform": "x86_64-linux",
        "artifact": "wysna-seed-installers-Linux-X64",
        "extensions": {".deb", ".rpm"},
    },
}


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def parse_sha256sums(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64})  ([^/\\]+)", line)
        if not match:
            raise ValueError(f"Invalid checksum line in {path}: {line!r}")
        checksum, name = match.groups()
        if name in entries:
            raise ValueError(f"Duplicate checksum entry in {path}: {name}")
        entries[name] = checksum.lower()
    if not entries:
        raise ValueError(f"No checksum entries in {path}")
    return entries


def verify_platform(name: str, root: Path, expected_sha: str, version: str,
                    ci_run_id: str, output: Path) -> dict[str, object]:
    config = PLATFORMS[name]
    manifests = list(root.rglob("installers.json"))
    if len(manifests) != 1:
        raise ValueError(f"{name}: expected one installers.json, found {len(manifests)}")
    manifest_path = manifests[0]
    package_root = manifest_path.parent.resolve()
    if package_root != root.resolve() and root.resolve() not in package_root.parents:
        raise ValueError(f"{name}: manifest escaped the downloaded artifact")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_fields = {
        "schema": "wysna.installers/1",
        "version": version,
        "source_commit": expected_sha,
        "source_dirty": False,
        "platform": config["platform"],
        "signed": False,
        "notarized": False,
    }
    for field, expected in expected_fields.items():
        if manifest.get(field) != expected:
            raise ValueError(f"{name}: {field} must be {expected!r}, got {manifest.get(field)!r}")

    listed = manifest.get("files")
    if not isinstance(listed, list) or not listed:
        raise ValueError(f"{name}: installer manifest has no package files")
    package_files: dict[str, dict[str, object]] = {}
    for item in listed:
        if not isinstance(item, dict):
            raise ValueError(f"{name}: invalid installer file entry")
        filename = item.get("name")
        if not isinstance(filename, str) or Path(filename).name != filename:
            raise ValueError(f"{name}: unsafe package filename {filename!r}")
        if filename in package_files:
            raise ValueError(f"{name}: duplicate package filename {filename}")
        package_files[filename] = item

    checksums_path = package_root / "SHA256SUMS"
    checksums = parse_sha256sums(checksums_path)
    if set(checksums) != set(package_files):
        raise ValueError(f"{name}: SHA256SUMS does not match installers.json")
    for filename, item in package_files.items():
        package = package_root / filename
        if not package.is_file() or package.is_symlink():
            raise ValueError(f"{name}: package is missing or not a regular file: {filename}")
        actual = digest(package)
        if actual != item.get("sha256") or actual != checksums[filename]:
            raise ValueError(f"{name}: checksum mismatch for {filename}")
        if item.get("bytes") != package.stat().st_size:
            raise ValueError(f"{name}: recorded size mismatch for {filename}")
        if package.suffix.lower() not in config["extensions"]:
            raise ValueError(f"{name}: unsupported package type: {filename}")

    extensions = {Path(filename).suffix.lower() for filename in package_files}
    if extensions != config["extensions"]:
        raise ValueError(f"{name}: expected package types {sorted(config['extensions'])}, got {sorted(extensions)}")
    expected_names = {"installers.json", "SHA256SUMS", *package_files}
    portable_name: str | None = None
    if name == "windows":
        portable = list(root.rglob("*-portable.zip"))
        if len(portable) != 1 or portable[0].is_symlink():
            raise ValueError("windows: expected one regular portable ZIP")
        portable_path = portable[0]
        if portable_path.parent.resolve() != package_root:
            raise ValueError("windows: portable ZIP and installer manifest must share a directory")
        portable_name = portable_path.name
        expected_names.add(portable_name)
        windows_checksums = package_root / "CHECKSUMS.sha256"
        actual_checksums = {
            path.name: digest(path)
            for path in package_root.iterdir()
            if path.is_file() and path != windows_checksums
        }
        actual_files = set(actual_checksums)
        if parse_sha256sums(windows_checksums) != actual_checksums:
            raise ValueError("windows: CHECKSUMS.sha256 does not match the artifact files")
        if actual_files != expected_names:
            raise ValueError(f"windows: unexpected artifact file set: {sorted(actual_files ^ expected_names)}")
    else:
        actual_files = {path.name for path in package_root.iterdir() if path.is_file()}
        if actual_files != expected_names:
            raise ValueError(f"{name}: unexpected artifact file set: {sorted(actual_files ^ expected_names)}")

    output.mkdir(parents=True, exist_ok=True)
    for filename in package_files:
        shutil.copyfile(package_root / filename, output / filename)
    if portable_name is not None:
        shutil.copyfile(package_root / portable_name, output / portable_name)
    shutil.copyfile(manifest_path, output / f"installers-{name}.json")
    shutil.copyfile(checksums_path, output / f"SHA256SUMS-{name}.txt")
    provenance = {
        "schema": "wysna.release-provenance/1",
        "version": version,
        "source_commit": expected_sha,
        "source_dirty": False,
        "platform": config["platform"],
        "installer_artifact": config["artifact"],
        "ci_workflow_run_id": int(ci_run_id),
        "ci_workflow_run_url": f"https://github.com/WashingtonYandun/Wysna/actions/runs/{ci_run_id}",
        "installers": [
            {"name": filename, "sha256": package_files[filename]["sha256"],
             "bytes": package_files[filename]["bytes"]}
            for filename in sorted(package_files)
        ],
        "signed": False,
        "notarized": False,
    }
    (output / f"provenance-{name}.json").write_text(
        json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return provenance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--ci-run-id", required=True)
    parser.add_argument("--windows-artifact", type=Path, required=True)
    parser.add_argument("--macos-artifact", type=Path, required=True)
    parser.add_argument("--linux-artifact", type=Path, required=True)
    parser.add_argument("--release-notes", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    if not re.fullmatch(r"\d+\.\d+\.\d+", args.version):
        parser.error("version must be a stable semantic version")
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        parser.error("source commit must be a full lowercase Git SHA")
    if not args.ci_run_id.isdecimal():
        parser.error("CI run ID must be numeric")
    for path in (args.windows_artifact, args.macos_artifact, args.linux_artifact):
        if not path.is_dir():
            parser.error(f"artifact directory does not exist: {path}")
    if args.output.exists():
        parser.error(f"output directory already exists: {args.output}")

    args.output.mkdir(parents=True)
    provenance = {}
    try:
        provenance["windows"] = verify_platform("windows", args.windows_artifact,
                                                args.source_commit, args.version,
                                                args.ci_run_id, args.output)
        provenance["macos"] = verify_platform("macos", args.macos_artifact,
                                              args.source_commit, args.version,
                                              args.ci_run_id, args.output)
        provenance["linux"] = verify_platform("linux", args.linux_artifact,
                                              args.source_commit, args.version,
                                              args.ci_run_id, args.output)
        (args.output / "release-provenance.json").write_text(
            json.dumps({"schema": "wysna.release-provenance/1", "version": args.version,
                        "source_commit": args.source_commit, "source_dirty": False,
                        "ci_workflow_run_id": int(args.ci_run_id), "platforms": provenance},
                       indent=2, sort_keys=True) + "\n", encoding="utf-8")
        checksums = []
        for asset in sorted(args.output.iterdir()):
            if asset.is_file() and asset.name != "SHA256SUMS":
                checksums.append(f"{digest(asset)}  {asset.name}\n")
        (args.output / "SHA256SUMS").write_text("".join(checksums), encoding="utf-8")
        notes = args.release_notes.read_text(encoding="utf-8")
        notes = notes.replace("{{SOURCE_COMMIT}}", args.source_commit)
        notes = notes.replace("{{CI_RUN_ID}}", args.ci_run_id)
        if "{{" in notes or "}}" in notes:
            raise ValueError("release notes contain an unresolved placeholder")
        (args.output.parent / "release-notes.md").write_text(notes, encoding="utf-8")
    except Exception:
        shutil.rmtree(args.output, ignore_errors=True)
        raise
    print(json.dumps({"version": args.version, "source_commit": args.source_commit,
                      "ci_workflow_run_id": int(args.ci_run_id),
                      "asset_count": sum(path.is_file() for path in args.output.iterdir())}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
