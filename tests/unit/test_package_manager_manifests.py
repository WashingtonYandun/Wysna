"""Unit tests for Winget and Homebrew manifest generation."""
from __future__ import annotations

from pathlib import Path
import pytest

from tools.generate_package_managers import generate


def test_generates_valid_winget_and_homebrew_manifests(tmp_path: Path):
    sums = tmp_path / "SHA256SUMS"
    win_sha = "a" * 64
    mac_sha = "b" * 64
    linux_sha = "c" * 64
    sums.write_text(
        f"{win_sha}  wysna-1.1.0-native-x86_64-windows-setup.exe\n"
        f"{mac_sha}  wysna-1.1.0-native-aarch64-macos.tar.gz\n"
        f"{linux_sha}  wysna-1.1.0-native-x86_64-linux.tar.gz\n",
        encoding="utf-8",
    )
    out = tmp_path / "manifests"
    generate("1.1.0", sums, out)

    installer = (out / "winget" / "WySofia.Wysna.installer.yaml").read_text(encoding="utf-8")
    assert "PackageIdentifier: WySofia.Wysna" in installer
    assert "PackageVersion: 1.1.0" in installer
    assert f"InstallerSha256: {win_sha.upper()}" in installer
    assert "Scope: user" in installer

    formula = (out / "homebrew" / "wysna.rb").read_text(encoding="utf-8")
    assert "class Wysna < Formula" in formula
    assert f'sha256 "{mac_sha}"' in formula
    assert f'sha256 "{linux_sha}"' in formula
    assert 'bin.install_symlink libexec/"bin/wysna"' in formula


def test_rejects_missing_platform_checksum(tmp_path: Path):
    sums = tmp_path / "SHA256SUMS"
    sums.write_text(f"{'a' * 64}  wysna-1.1.0-native-aarch64-macos.tar.gz\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Missing"):
        generate("1.1.0", sums, tmp_path / "out")
