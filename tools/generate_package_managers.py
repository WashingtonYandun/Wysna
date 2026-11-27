#!/usr/bin/env python3
"""Generate deterministic Winget and Homebrew package manifests from a release SHA256SUMS."""
from __future__ import annotations

import argparse
from pathlib import Path
import re

REPO_BASE = "https://github.com/WashingtonYandun/Wysna/releases/download"


def parse_sha256sums(raw: str) -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?([^\s/\\]+)", stripped)
        if not match:
            raise ValueError(f"Invalid SHA256SUMS line: {line!r}")
        digest, name = match.groups()
        entries[name] = digest.lower()
    return entries


def render_winget_manifests(version: str, checksums: dict[str, str]) -> dict[str, str]:
    exe_name = f"wysna-{version}-native-x86_64-windows-setup.exe"
    if exe_name not in checksums:
        raise ValueError(f"Missing Windows Native SDK installer checksum for {exe_name}")
    exe_url = f"{REPO_BASE}/v{version}/{exe_name}"
    sha_upper = checksums[exe_name].upper()

    version_yaml = (
        "# yaml-language-server: $schema=https://aka.ms/winget-manifest.version.1.6.0.schema.json\n"
        "PackageIdentifier: WySofia.Wysna\n"
        f"PackageVersion: {version}\n"
        "DefaultLocale: en-US\n"
        "ManifestType: version\n"
        "ManifestVersion: 1.6.0\n"
    )
    installer_yaml = (
        "# yaml-language-server: $schema=https://aka.ms/winget-manifest.installer.1.6.0.schema.json\n"
        "PackageIdentifier: WySofia.Wysna\n"
        f"PackageVersion: {version}\n"
        "Platform:\n"
        "  - Windows.Desktop\n"
        "MinimumOSVersion: 10.0.17763.0\n"
        "InstallerType: inno\n"
        "Scope: user\n"
        "InstallModes:\n"
        "  - interactive\n"
        "  - silent\n"
        "  - silentWithProgress\n"
        "InstallerSwitches:\n"
        "  Silent: /VERYSILENT /SUPPRESSMSGBOXES /NORESTART\n"
        "  SilentWithProgress: /SILENT /SUPPRESSMSGBOXES /NORESTART\n"
        "UpgradeBehavior: install\n"
        "Commands:\n"
        "  - wysna\n"
        "Installers:\n"
        "  - Architecture: x64\n"
        f"    InstallerUrl: {exe_url}\n"
        f"    InstallerSha256: {sha_upper}\n"
        "ManifestType: installer\n"
        "ManifestVersion: 1.6.0\n"
    )
    locale_yaml = (
        "# yaml-language-server: $schema=https://aka.ms/winget-manifest.defaultLocale.1.6.0.schema.json\n"
        "PackageIdentifier: WySofia.Wysna\n"
        f"PackageVersion: {version}\n"
        "PackageLocale: en-US\n"
        "Publisher: WySofia S.A.S.\n"
        "PublisherUrl: https://www.wysofia.com/\n"
        "PackageName: Wysna\n"
        "PackageUrl: https://wysna.wysofia.com/\n"
        "License: Apache-2.0\n"
        "LicenseUrl: https://github.com/WashingtonYandun/Wysna/blob/main/LICENSE\n"
        "ShortDescription: Statically typed compiled programming language and AI-agent Native SDK.\n"
        "Moniker: wysna\n"
        "Tags:\n"
        "  - compiler\n"
        "  - language\n"
        "  - cli\n"
        "  - mcp\n"
        "  - lsp\n"
        "ManifestType: defaultLocale\n"
        "ManifestVersion: 1.6.0\n"
    )
    return {
        "WySofia.Wysna.yaml": version_yaml,
        "WySofia.Wysna.installer.yaml": installer_yaml,
        "WySofia.Wysna.locale.en-US.yaml": locale_yaml,
    }


def render_homebrew_formula(version: str, checksums: dict[str, str]) -> str:
    mac_name = f"wysna-{version}-native-aarch64-macos.tar.gz"
    linux_name = f"wysna-{version}-native-x86_64-linux.tar.gz"
    for name in (mac_name, linux_name):
        if name not in checksums:
            raise ValueError(f"Missing Native SDK archive checksum for {name}")
    mac_url = f"{REPO_BASE}/v{version}/{mac_name}"
    linux_url = f"{REPO_BASE}/v{version}/{linux_name}"
    return (
        "class Wysna < Formula\n"
        '  desc "Statically typed compiled programming language and Native SDK built for AI agents"\n'
        '  homepage "https://wysna.wysofia.com/"\n'
        f'  version "{version}"\n'
        '  license "Apache-2.0"\n\n'
        "  on_macos do\n"
        "    on_arm do\n"
        f'      url "{mac_url}"\n'
        f'      sha256 "{checksums[mac_name]}"\n'
        "    end\n"
        "  end\n\n"
        "  on_linux do\n"
        "    on_intel do\n"
        f'      url "{linux_url}"\n'
        f'      sha256 "{checksums[linux_name]}"\n'
        "    end\n"
        "  end\n\n"
        "  def install\n"
        '    root = (buildpath/"wysna-native").exist? ? buildpath/"wysna-native" : buildpath\n'
        '    libexec.install Dir[root/"*"]\n'
        '    bin.install_symlink libexec/"bin/wysna"\n'
        "  end\n\n"
        "  test do\n"
        '    assert_match "Wysna", shell_output("#{bin}/wysna --version")\n'
        "  end\n"
        "end\n"
    )


def generate(version: str, checksums_path: Path, output: Path) -> None:
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise ValueError(f"Invalid semantic version: {version!r}")
    checksums = parse_sha256sums(checksums_path.read_text(encoding="utf-8"))
    winget_dir = output / "winget"
    brew_dir = output / "homebrew"
    winget_dir.mkdir(parents=True, exist_ok=True)
    brew_dir.mkdir(parents=True, exist_ok=True)
    for filename, content in render_winget_manifests(version, checksums).items():
        (winget_dir / filename).write_text(content, encoding="utf-8")
    (brew_dir / "wysna.rb").write_text(render_homebrew_formula(version, checksums), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--checksums", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    generate(args.version, args.checksums, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
