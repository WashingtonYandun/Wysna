#!/usr/bin/env python3
"""Branch-driven SemVer calculator and release metadata generator for Wysna."""
from __future__ import annotations

import argparse
from datetime import date
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

SEMVER_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)$")
BUMP_ORDER = {"none": 0, "patch": 1, "minor": 2, "major": 3}

BRANCH_PREFIX_BUMP = (
    ("breaking/", "major"),
    ("feat/", "minor"),
    ("feature/", "minor"),
    ("fix/", "patch"),
    ("hotfix/", "patch"),
    ("perf/", "patch"),
    ("refactor/", "patch"),
    ("docs/", "none"),
    ("chore/", "none"),
    ("ci/", "none"),
    ("test/", "none"),
    ("tests/", "none"),
)

BUMP_CATEGORY = {
    "major": "Breaking Changes",
    "minor": "Features",
    "patch": "Fixes & Performance",
    "none": "Maintenance & Documentation",
}


def parse_semver(raw: str) -> tuple[int, int, int]:
    """Parse X.Y.Z or vX.Y.Z into integer components."""
    match = SEMVER_RE.fullmatch(raw.strip())
    if not match:
        raise ValueError(f"Invalid semantic version: {raw!r}")
    return int(match.group(1)), int(match.group(2)), int(match.group(3))


def bump_version(current: str, bump: str) -> str:
    """Apply a SemVer bump level ('major', 'minor', 'patch', 'none') to current."""
    major, minor, patch = parse_semver(current)
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    if bump == "patch":
        return f"{major}.{minor}.{patch + 1}"
    if bump == "none":
        return f"{major}.{minor}.{patch}"
    raise ValueError(f"Unsupported bump kind: {bump!r}")


def classify_pr(head_ref: str, title: str = "", labels: list[str] | None = None) -> str:
    """Determine SemVer bump from branch prefix first, then labels and PR title."""
    ref = head_ref.strip().lower()
    text_labels = {item.strip().lower() for item in (labels or [])}
    normalized_title = title.strip()

    if "breaking" in text_labels or "major" in text_labels or "BREAKING CHANGE" in title:
        return "major"
    for prefix, level in BRANCH_PREFIX_BUMP:
        if ref.startswith(prefix):
            if level == "minor" and ("!" in normalized_title.split(":", 1)[0]):
                return "major"
            return level

    lowered_title = normalized_title.lower()
    if lowered_title.startswith(("feat!:", "fix!:", "perf!:", "refactor!:")):
        return "major"
    if re.match(r"^(feat|fix|perf|refactor)\([^)]+\)!:", lowered_title):
        return "major"
    if re.match(r"^feat(\([^)]+\))?:", lowered_title):
        return "minor"
    if re.match(r"^(fix|perf|refactor)(\([^)]+\))?:", lowered_title):
        return "patch"
    return "none"


def compute_release_plan(
    current_version: str,
    prs: list[dict],
    *,
    version_override: str | None = None,
) -> dict:
    """Compute target version, bump level, and categorized changelog entries."""
    highest = "none"
    sections: dict[str, list[dict]] = {label: [] for label in BUMP_CATEGORY.values()}

    for pr in prs:
        head_ref = str(pr.get("head_ref") or pr.get("headRefName") or "")
        title = str(pr.get("title") or "").strip()
        number = pr.get("number")
        labels = [
            str(lbl.get("name") if isinstance(lbl, dict) else lbl)
            for lbl in (pr.get("labels") or [])
        ]
        if head_ref.startswith("release/"):
            continue
        bump = classify_pr(head_ref, title, labels)
        if BUMP_ORDER[bump] > BUMP_ORDER[highest]:
            highest = bump
        category = BUMP_CATEGORY[bump]
        sections[category].append(
            {
                "number": number,
                "title": title or head_ref,
                "head_ref": head_ref,
                "bump": bump,
            }
        )

    if version_override:
        next_version = ".".join(str(part) for part in parse_semver(version_override))
        should_release = True
        highest = "override"
    elif highest != "none":
        next_version = bump_version(current_version, highest)
        should_release = True
    else:
        next_version = ".".join(str(part) for part in parse_semver(current_version))
        should_release = False

    return {
        "current_version": ".".join(str(part) for part in parse_semver(current_version)),
        "next_version": next_version,
        "tag": f"v{next_version}",
        "bump": highest,
        "should_release": should_release,
        "sections": {k: v for k, v in sections.items() if v},
    }


def _render_changelog_section(plan: dict, released_on: str) -> str:
    lines = [f"## {plan['next_version']} — {released_on}", ""]
    sections = plan.get("sections") or {}
    if not sections:
        lines.extend(["### Changes", f"- Automated release `{plan['tag']}`.", ""])
    else:
        for heading, items in sections.items():
            lines.append(f"### {heading}")
            for item in items:
                suffix = f" (#{item['number']})" if item.get("number") else ""
                lines.append(f"- {item['title']}{suffix}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_release_notes(plan: dict) -> str:
    ver = plan["next_version"]
    major, minor, _ = parse_semver(ver)
    lang_ver = f"{major}.{minor}"
    sections = plan.get("sections") or {}
    lines = [
        f"# Wysna {ver}",
        "",
        f"Automated release `{plan['tag']}` built and verified on GitHub Actions.",
        "",
        "Documentation & Handbook: [https://wysna.wysofia.com/](https://wysna.wysofia.com/)",
        "",
        "## Changes",
        "",
    ]
    if not sections:
        lines.append(f"- Release `{plan['tag']}` (`package.language = \"{lang_ver}\"`).")
    else:
        for heading, items in sections.items():
            lines.append(f"### {heading}")
            for item in items:
                suffix = f" (#{item['number']})" if item.get("number") else ""
                lines.append(f"- {item['title']}{suffix}")
            lines.append("")
    lines.extend([
        "",
        "## Distribution",
        "",
        f"- Windows x64 Native SDK: `wysna-{ver}-native-x86_64-windows.zip`",
        f"- macOS arm64 Native SDK: `wysna-{ver}-native-aarch64-macos.tar.gz`",
        f"- Linux x64 Native SDK: `wysna-{ver}-native-x86_64-linux.tar.gz`",
        "",
        "Seed installers bundle CPython and Zig in `.exe`, `.pkg`, `.deb`, and `.rpm` packages with manifests, provenance, and `SHA256SUMS`.",
        "",
        "## Provenance",
        "",
        "- Source commit: `{{SOURCE_COMMIT}}`",
        "- Main CI run: [{{CI_RUN_ID}}](https://github.com/WashingtonYandun/Wysna/actions/runs/{{CI_RUN_ID}})",
        "",
    ])
    return "\n".join(lines)


def apply_release_plan(
    root: Path,
    plan: dict,
    *,
    released_on: str | None = None,
    sync_bundle: bool = True,
) -> list[str]:
    """Update contract version, docs, changelog, release notes, and selfhost bundle."""
    next_version = plan["next_version"]
    old_version = plan["current_version"]
    major, minor, _ = parse_semver(next_version)
    lang_version = f"{major}.{minor}"
    released_date = released_on or date.today().isoformat()
    touched: list[str] = []

    contract_path = root / "src/common/agent_contract.json"
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    contract["compiler_version"] = next_version
    contract["language_version"] = lang_version
    contract_path.write_text(json.dumps(contract, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    touched.append("src/common/agent_contract.json")

    if sync_bundle:
        bundler = root / "tools/bundle_selfhost.py"
        if bundler.is_file():
            subprocess.run([sys.executable, str(bundler)], cwd=root, stdout=sys.stderr, check=True)
            touched.extend([
                "selfhost/src/01_agent_contract.wysna",
                "selfhost/compiler.wysna",
                "src/common/Version.py",
            ])
    else:
        ver_path = root / "src/common/Version.py"
        if ver_path.is_file():
            ver_path.write_text(
                f'# Generated by tools/generate_agent_contract.py.\nWYSNA_VERSION = "{next_version}"\n',
                encoding="utf-8",
            )
            touched.append("src/common/Version.py")

    projects_doc = root / "docs/PROJECTS.md"
    if projects_doc.is_file():
        text = projects_doc.read_text(encoding="utf-8")
        updated = re.sub(r'(language\s*=\s*")[^"]+(")', rf'\g<1>{lang_version}\g<2>', text, count=1)
        updated = re.sub(r'(compiler\s*=\s*")[^"]+(")', rf'\g<1>{next_version}\g<2>', updated, count=1)
        if updated != text:
            projects_doc.write_text(updated, encoding="utf-8")
            touched.append("docs/PROJECTS.md")

    examples_dir = root / "examples"
    if examples_dir.is_dir():
        main_entry = root / "src/main.py"
        lock_env = {**os.environ, "PYTHONPATH": str(root / "src")}
        for toml_path in sorted(examples_dir.glob("**/wysna.toml")):
            text = toml_path.read_text(encoding="utf-8")
            updated = re.sub(r'(language\s*=\s*")[^"]+(")', rf'\g<1>{lang_version}\g<2>', text, count=1)
            updated = re.sub(r'(compiler\s*=\s*")[^"]+(")', rf'\g<1>{next_version}\g<2>', updated, count=1)
            if updated != text:
                toml_path.write_text(updated, encoding="utf-8")
                touched.append(toml_path.relative_to(root).as_posix())
            lock_path = toml_path.parent / "wysna.lock"
            if sync_bundle and lock_path.is_file() and main_entry.is_file():
                subprocess.run(
                    [sys.executable, str(main_entry), "project", "lock", "--manifest", str(toml_path)],
                    cwd=root,
                    env=lock_env,
                    stdout=sys.stderr,
                    check=True,
                )
                touched.append(lock_path.relative_to(root).as_posix())

    readme = root / "README.md"
    if readme.is_file():
        text = readme.read_text(encoding="utf-8")
        updated = text.replace(f"**Wysna {old_version}**", f"**Wysna {next_version}**")
        updated = updated.replace(f'toolchain.compiler = "{old_version}"', f'toolchain.compiler = "{next_version}"')
        updated = updated.replace(f'package.language = "{contract.get("language_version")}"', f'package.language = "{lang_version}"')
        if updated != text:
            readme.write_text(updated, encoding="utf-8")
            touched.append("README.md")

    changelog_path = root / "CHANGELOG.md"
    if changelog_path.is_file():
        existing = changelog_path.read_text(encoding="utf-8")
        section = _render_changelog_section(plan, released_date)
        header_marker = f"## {next_version} "
        if header_marker not in existing:
            if existing.startswith("# Changelog\n"):
                rest = existing[len("# Changelog\n"):].lstrip("\n")
                changelog_path.write_text(f"# Changelog\n\n{section}\n{rest}", encoding="utf-8")
            else:
                changelog_path.write_text(f"# Changelog\n\n{section}\n{existing}", encoding="utf-8")
            touched.append("CHANGELOG.md")

    notes_path = root / f"docs/release-notes/v{next_version}.md"
    notes_path.parent.mkdir(parents=True, exist_ok=True)
    if not notes_path.is_file():
        notes_path.write_text(_render_release_notes(plan), encoding="utf-8")
        touched.append(f"docs/release-notes/v{next_version}.md")

    return touched


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--current-version", default=None)
    parser.add_argument("--version-override", default=None)
    parser.add_argument("--prs-json", type=Path, default=None, help="JSON list of merged PR objects")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--no-bundle", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    if args.current_version:
        current = args.current_version
    else:
        contract = json.loads((root / "src/common/agent_contract.json").read_text(encoding="utf-8"))
        current = contract["compiler_version"]

    prs = json.loads(args.prs_json.read_text(encoding="utf-8")) if args.prs_json else []
    plan = compute_release_plan(current, prs, version_override=args.version_override)
    if args.apply and plan["should_release"]:
        plan["touched"] = apply_release_plan(root, plan, sync_bundle=not args.no_bundle)
    print(json.dumps(plan, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
