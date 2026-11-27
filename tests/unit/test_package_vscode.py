"""Tests for deterministic VS Code .vsix packaging."""
from __future__ import annotations

import json
from pathlib import Path
import zipfile

from tools.package_vscode import build_vsix


def test_package_vscode_is_deterministic_and_valid_zip(tmp_path: Path):
    out1 = build_vsix(tmp_path / "ext1.vsix")
    out2 = build_vsix(tmp_path / "ext2.vsix")
    assert out1.read_bytes() == out2.read_bytes()
    with zipfile.ZipFile(out1, "r") as zf:
        names = set(zf.namelist())
        assert "[Content_Types].xml" in names
        assert "extension.vsixmanifest" in names
        assert "extension/package.json" in names
        assert "extension/syntaxes/wysna.tmLanguage.json" in names
        manifest = zf.read("extension.vsixmanifest").decode("utf-8")
        pkg = json.loads(zf.read("extension/package.json").decode("utf-8"))
        assert f'Version="{pkg["version"]}"' in manifest
