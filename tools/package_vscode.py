#!/usr/bin/env python3
"""Package editors/vscode-wysna into a deterministic .vsix archive without vsce."""
from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
import zipfile

ROOT = Path(__file__).resolve().parents[1]
EXT_DIR = ROOT / "editors" / "vscode-wysna"

FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)

CONTENT_TYPES_XML = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
    '  <Default Extension=".json" ContentType="application/json" />\n'
    '  <Default Extension=".js" ContentType="application/javascript" />\n'
    '  <Default Extension=".md" ContentType="text/markdown" />\n'
    '  <Default Extension=".vsixmanifest" ContentType="text/xml" />\n'
    "</Types>\n"
)


def render_manifest(pkg: dict) -> str:
    publisher = escape(str(pkg["publisher"]))
    name = escape(str(pkg["name"]))
    version = escape(str(pkg["version"]))
    display_name = escape(str(pkg.get("displayName", name)))
    description = escape(str(pkg.get("description", "")))
    return (
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<PackageManifest Version="2.0.0" xmlns="http://schemas.microsoft.com/developer/vsx-schema/2011">\n'
        "  <Metadata>\n"
        f'    <Identity Language="en-US" Id="{name}" Version="{version}" Publisher="{publisher}" />\n'
        f"    <DisplayName>{display_name}</DisplayName>\n"
        f'    <Description xml:space="preserve">{description}</Description>\n'
        "    <Categories>Programming Languages</Categories>\n"
        "  </Metadata>\n"
        "  <Installation>\n"
        '    <InstallationTarget Id="Microsoft.VisualStudio.Code" />\n'
        "  </Installation>\n"
        "  <Dependencies />\n"
        "  <Assets>\n"
        '    <Asset Type="Microsoft.VisualStudio.Code.Manifest" Path="extension/package.json" Addressable="true" />\n'
        "  </Assets>\n"
        "</PackageManifest>\n"
    )


def build_vsix(output: Path, ext_dir: Path = EXT_DIR) -> Path:
    pkg = json.loads((ext_dir / "package.json").read_text(encoding="utf-8"))
    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for arcname, payload in (
            ("[Content_Types].xml", CONTENT_TYPES_XML.encode("utf-8")),
            ("extension.vsixmanifest", render_manifest(pkg).encode("utf-8")),
        ):
            info = zipfile.ZipInfo(arcname, date_time=FIXED_ZIP_TIME)
            info.external_attr = 0o100644 << 16
            zf.writestr(info, payload)
        for path in sorted(ext_dir.rglob("*")):
            if not path.is_file():
                continue
            rel = path.relative_to(ext_dir).as_posix()
            info = zipfile.ZipInfo(f"extension/{rel}", date_time=FIXED_ZIP_TIME)
            info.external_attr = 0o100644 << 16
            zf.writestr(info, path.read_bytes())
    return output


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    out = build_vsix(args.output)
    print(json.dumps({"vsix": str(out), "bytes": out.stat().st_size}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
