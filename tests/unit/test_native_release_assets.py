"""Only publish the exact native archive exercised by installed acceptance."""
import json

import pytest

from tools.prepare_release_assets import digest, stage_native_sdks


def test_native_release_requires_matching_archive_and_installed_checks(tmp_path):
    source = "a" * 40
    output = tmp_path / "output"
    output.mkdir()
    checks = [
        {"name": name, "status": 0}
        for name in (
            "ops-project-check", "ops-project-build", "ops-project-run", "ops-project-test",
            "ops-graph-cli", "ops-graph-mcp", "rebuilt-native-engine-emits-identical-C",
            "project-new", "scaffold-project-lock", "scaffold-project-test",
        )
    ]
    for runner, platform, extension in (
        ("macos-latest", "aarch64-macos", "tar.gz"),
        ("ubuntu-latest", "x86_64-linux", "tar.gz"),
        ("windows-latest", "x86_64-windows", "zip"),
    ):
        archive = tmp_path / f"native-sdk-{runner}" / f"wysna-native.{extension}"
        archive.parent.mkdir()
        archive.write_text(platform, encoding="utf-8")
        report = tmp_path / f"native-sdk-acceptance-{runner}" / "native-install-report.json"
        report.parent.mkdir()
        report.write_text(json.dumps({
            "success": True, "source_commit": source, "version": "0.8.0", "platform": platform,
            "archive_sha256": digest(archive), "checks": checks,
        }), encoding="utf-8")

    staged = stage_native_sdks(tmp_path, source, "0.8.0", output)
    assert len(staged) == 3
    assert digest(output / staged["x86_64-windows"]["name"]) == staged["x86_64-windows"]["sha256"]

    archive.write_bytes(b"changed after validation")
    with pytest.raises(ValueError, match="x86_64-windows"):
        stage_native_sdks(tmp_path, source, "0.8.0", output)
