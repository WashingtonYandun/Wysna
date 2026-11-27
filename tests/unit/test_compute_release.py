"""Tests for branch-driven SemVer calculation and release file updates."""
import json
from pathlib import Path

from tools.compute_release import apply_release_plan, classify_pr, compute_release_plan


def test_branch_prefix_and_fallback_semver_classification():
    assert classify_pr("fix/cache-race", "Fix race") == "patch"
    assert classify_pr("perf/native-test-cache", "Faster tests") == "patch"
    assert classify_pr("feat/json-streaming", "Add streaming") == "minor"
    assert classify_pr("feature/http2", "Add HTTP/2") == "minor"
    assert classify_pr("breaking/abi-v4", "New ABI") == "major"
    assert classify_pr("feat/new-abi", "feat!: New ABI") == "major"
    assert classify_pr("docs/readme-en", "Update README") == "none"
    assert classify_pr("random-branch", "feat: allow fallback title") == "minor"
    assert classify_pr("random-branch", "fix(parser): handle empty file") == "patch"


def test_compute_release_plan_selects_highest_bump():
    prs = [
        {"number": 202, "head_ref": "perf/native-project-test-cache", "title": "Cache tests"},
        {"number": 203, "head_ref": "chore/repo-cleanup", "title": "Clean repo"},
        {"number": 205, "head_ref": "feat/package-signing", "title": "Sign packages"},
    ]
    plan = compute_release_plan("0.9.5", prs)
    assert plan["bump"] == "minor"
    assert plan["next_version"] == "0.10.0"
    assert plan["tag"] == "v0.10.0"
    assert plan["should_release"] is True

    docs_only = compute_release_plan("0.9.5", [{"number": 204, "head_ref": "docs/typo", "title": "Typo"}])
    assert docs_only["should_release"] is False
    assert docs_only["next_version"] == "0.9.5"

    override = compute_release_plan("0.9.5", prs, version_override="1.0.0")
    assert override["next_version"] == "1.0.0"
    assert override["tag"] == "v1.0.0"
    assert override["should_release"] is True


def test_apply_release_plan_updates_contract_docs_and_notes(tmp_path):
    (tmp_path / "src/common").mkdir(parents=True)
    (tmp_path / "docs/release-notes").mkdir(parents=True)
    (tmp_path / "src/common/agent_contract.json").write_text(
        json.dumps({"compiler_version": "0.9.5", "language_version": "0.9"}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "src/common/Version.py").write_text('WYSNA_VERSION = "0.9.5"\n', encoding="utf-8")
    (tmp_path / "docs/PROJECTS.md").write_text('language = "0.9"\ncompiler = "0.9.5"\n', encoding="utf-8")
    (tmp_path / "README.md").write_text('Uses **Wysna 0.9.5** with `toolchain.compiler = "0.9.5"`.\n', encoding="utf-8")
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\n## 0.9.5 — 2026-11-24\n- Previous\n", encoding="utf-8")
    (tmp_path / "editors/vscode-wysna").mkdir(parents=True)
    (tmp_path / "editors/vscode-wysna/package.json").write_text(
        json.dumps({"name": "wysna", "version": "0.9.5"}) + "\n",
        encoding="utf-8",
    )

    plan = compute_release_plan(
        "0.9.5",
        [{"number": 210, "head_ref": "feat/release-automation", "title": "Automate releases"}],
        version_override="1.0.0",
    )
    touched = apply_release_plan(tmp_path, plan, released_on="2026-11-26", sync_bundle=False)
    assert "CHANGELOG.md" in touched
    assert "docs/release-notes/v1.0.0.md" in touched
    assert "editors/vscode-wysna/package.json" in touched
    vscode_pkg = json.loads((tmp_path / "editors/vscode-wysna/package.json").read_text(encoding="utf-8"))
    assert vscode_pkg["version"] == "1.0.0"

    contract = json.loads((tmp_path / "src/common/agent_contract.json").read_text(encoding="utf-8"))
    assert contract["compiler_version"] == "1.0.0"
    assert contract["language_version"] == "1.0"
    assert 'WYSNA_VERSION = "1.0.0"' in (tmp_path / "src/common/Version.py").read_text(encoding="utf-8")
    assert 'compiler = "1.0.0"' in (tmp_path / "docs/PROJECTS.md").read_text(encoding="utf-8")
    assert 'language = "1.0"' in (tmp_path / "docs/PROJECTS.md").read_text(encoding="utf-8")
    notes = (tmp_path / "docs/release-notes/v1.0.0.md").read_text(encoding="utf-8")
    assert "{{SOURCE_COMMIT}}" in notes and "{{CI_RUN_ID}}" in notes
