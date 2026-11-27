"""Only publish the exact native archive exercised by installed acceptance."""
import json

import pytest

from tools.prepare_release_assets import digest, stage_native_sdks, stage_vscode_vsix


def test_stage_vscode_vsix_copies_and_hashes(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    vsix = tmp_path / "wysna.vsix"
    vsix.write_bytes(b"PK\x03\x04vsix-content")
    staged = stage_vscode_vsix(vsix, "1.1.0", output)
    assert staged["name"] == "wysna-1.1.0-vscode.vsix"
    assert staged["sha256"] == digest(vsix)
    assert (output / "wysna-1.1.0-vscode.vsix").read_bytes() == b"PK\x03\x04vsix-content"
    with pytest.raises(ValueError, match="VS Code VSIX"):
        stage_vscode_vsix(tmp_path / "missing.vsix", "1.1.0", output)


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
            "reexported-generic-errors", "generic-method-errors", "async-generic-errors", "borrowed-host-paths",
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
        if runner == "windows-latest":
            (tmp_path / f"native-sdk-{runner}" / "wysna-native-setup.exe").write_bytes(b"MZ-setup-exe")

    staged = stage_native_sdks(tmp_path, source, "0.8.0", output)
    assert len(staged) == 3
    assert digest(output / staged["x86_64-windows"]["name"]) == staged["x86_64-windows"]["sha256"]
    installer = staged["x86_64-windows"]["installer"]
    assert installer["name"] == "wysna-0.8.0-native-x86_64-windows-setup.exe"
    assert digest(output / installer["name"]) == installer["sha256"]

    accepted_report = report.read_text(encoding="utf-8")
    incomplete = json.loads(accepted_report)
    incomplete['checks'] = [check for check in incomplete['checks'] if check['name'] != 'async-generic-errors']
    report.write_text(json.dumps(incomplete), encoding="utf-8")
    with pytest.raises(ValueError, match="x86_64-windows"):
        stage_native_sdks(tmp_path, source, "0.8.0", output)
    report.write_text(accepted_report, encoding="utf-8")

    archive.write_bytes(b"changed after validation")
    with pytest.raises(ValueError, match="x86_64-windows"):
        stage_native_sdks(tmp_path, source, "0.8.0", output)
