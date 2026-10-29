"""
test_d01_parity_matrix.py - Conformance suite for Milestone D01:
Public Engine Feature Parity Matrix, Boundary Transparency, and Standalone Wysna Compiler Validation.

Invariants verified:
1. Every fixture in the corpus is classified in tests/engine_profiles.json without stale entries.
2. Every unsupported engine feature has an explicit, non-empty tracking reason with a milestone identifier.
3. Standalone Wysna Compiler binary executes directly on core fixtures without Seed pre-filtering.
4. Standalone Wysna Compiler binary accepts generic functions directly and emits C without Seed pre-filtering.
5. ci_conformance.py --matrix generates a valid Markdown parity matrix matching current counts.
6. docs/ENGINE_PARITY_MATRIX.md matches the actual engine status reported by the harness.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _run_cmd(cmd, cwd=None):
    res = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    return res.returncode, res.stdout, res.stderr


def test_fixture_accounting_and_staleness():
    """Verify that every fixture in the corpus is tracked and no stale entries exist."""
    print("  [TEST] Fixture accounting and profile freshness...")
    profiles_path = ROOT / "tests/engine_profiles.json"
    assert profiles_path.exists(), "engine_profiles.json must exist"
    profiles = json.loads(profiles_path.read_text())

    from tests.engine_harness import corpus_paths
    positives, negatives = corpus_paths()
    fixtures = positives + negatives

    labels = {p.relative_to(ROOT).as_posix() for p in fixtures}
    stale = set(profiles.get("fixtures", {}).keys()) - labels
    assert not stale, f"Found stale fixture entries in engine_profiles.json: {stale}"

    # Verify every fixture is mapped to a declared feature
    declared_features = set(profiles.get("features", {}).keys())
    for label in labels:
        feat = profiles.get("fixtures", {}).get(label, "core")
        assert feat in declared_features, f"Fixture '{label}' mapped to unknown feature '{feat}'"


def test_unsupported_reasons_and_milestones():
    """Verify that all unsupported engine entries have clear, non-empty tracking reasons."""
    print("  [TEST] Explicit tracking reasons for unsupported engine features...")
    profiles = json.loads((ROOT / "tests/engine_profiles.json").read_text())
    features = profiles.get("features", {})
    assert profiles.get("fixtures", {}).get("tests/runtime/generic_class_methods.wysna") == "generic_class_methods"
    assert features.get("generic_class_methods") == {}, "Concrete generic class methods must be measured across all engines"

    # Core must be supported across all engines
    core = features.get("core", {})
    assert not core, f"Core feature must have no engine limitations, found: {core}"

    # Active milestone planning is maintained in the project Brain, not in public docs.
    # Keep the engine profile self-contained and require actionable identifiers.
    milestone_pattern = re.compile(r"\b[A-Z]\d{2}\b")
    for feat_name, limitations in features.items():
        for engine, reason in limitations.items():
            assert reason, f"Feature '{feat_name}' engine '{engine}' has empty unsupported reason"
            assert milestone_pattern.search(reason), (
                f"Feature '{feat_name}' engine '{engine}' reason must reference a milestone identifier: {reason}"
            )


def test_standalone_selfhost_execution_contracts():
    """Verify standalone Wysna Compiler core and generic compilation without Seed pre-filtering."""
    print("  [TEST] Standalone Wysna Compiler execution contracts (core and generic compilation)...")
    from main import ensure_selfhost_compiler_binary
    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    assert os.path.exists(selfhost_bin), f"Wysna Compiler binary must exist at {selfhost_bin}"

    with tempfile.TemporaryDirectory() as td:
        # 1. Core fixture: must compile cleanly standalone
        core_c = os.path.join(td, "core_out.c")
        core_src = str(ROOT / "tests/runtime/arithmetic.wysna")
        rc, out, err = _run_cmd([selfhost_bin, core_src, core_c], cwd=str(ROOT))
        assert rc == 0, f"Standalone Wysna Compiler failed on core fixture '{core_src}': {err}"
        assert os.path.exists(core_c), "Wysna Compiler must emit C file for core fixture"

        # 2. Generic fixture: must compile directly without passing through Seed.
        gen_c = os.path.join(td, "gen_out.c")
        gen_src = str(ROOT / "tests/runtime/generic_function.wysna")
        rc_gen, out_gen, err_gen = _run_cmd([selfhost_bin, gen_src, gen_c], cwd=str(ROOT))
        assert rc_gen == 0, f"Standalone Wysna Compiler failed on generic fixture '{gen_src}': {err_gen or out_gen}"
        assert os.path.exists(gen_c), "Wysna Compiler must emit C for a valid generic fixture"


def test_ci_matrix_generation_tooling():
    """Verify that ci_conformance.py --matrix emits expected table accurately."""
    print("  [TEST] ci_conformance.py --matrix generator...")
    rc, out, err = _run_cmd([sys.executable, str(ROOT / "tools/ci_conformance.py"), "--matrix"], cwd=str(ROOT))
    assert rc == 0, f"--matrix command failed: {err}"
    assert "# Wysna Engine Feature Parity Matrix" in out, "Output must contain matrix header"
    assert "| **`core`** |" in out, "Output must contain core row"
    assert "| **`generics`** |" in out, "Output must contain generics row"
    profile = json.loads((ROOT / 'tests/engine_profiles.json').read_text())
    error_union_count = sum(feature == 'error_unions' for feature in profile['fixtures'].values())
    assert f"| **`error_unions`** | {error_union_count} |" in out, "Output must report all F03 fixtures"
    assert "| **Total Verified** |" in out, "Output must contain total row"
    # The matrix must report measured per-engine numbers, not a restatement of the
    # declaration file. Previously this test asserted the literal strings "91/91" and
    # "0/15", which passed even when no compiler was ever executed.
    import re as _re
    from tests.engine_harness import corpus_paths
    assert "measurements, not a" in out, "Matrix must state that counts are measured"
    totals = _re.search(r"\| \*\*Total Verified\*\* \| \*\*(\d+)\*\* \|(.+)\|", out)
    assert totals, "Matrix must contain a parseable total row"
    corpus = int(totals.group(1))
    assert corpus == len(corpus_paths()[0]) + len(corpus_paths()[1]), \
        f"Matrix corpus size {corpus} does not match the real corpus"
    header = out.splitlines()[2]
    for column in ("Wysna Seed (Interpreter)", "Wysna Seed (AST C99)", "Wysna Seed (HIR C99)", "Wysna Compiler (Self-Hosted)"):
        assert column in header, f"Matrix header is missing the '{column}' column"
    if "\u2014" in totals.group(2):
        # No verified run available in this workspace; that is reported, not faked.
        assert "no verified run" in out, "Missing run must be reported explicitly"


def test_documentation_consistency():
    """Keep published engine and expansion limits aligned with the measured corpus."""
    print("  [TEST] Documentation consistency in docs/ENGINE_PARITY_MATRIX.md...")
    doc_path = ROOT / "docs/ENGINE_PARITY_MATRIX.md"
    assert doc_path.exists(), "docs/ENGINE_PARITY_MATRIX.md must exist"
    content = doc_path.read_text()

    # The doc is a report, so the check is that it stays internally consistent rather
    # than that it repeats a number copied into this test.
    from tests.engine_harness import corpus_paths
    import re as _re
    corpus_size = len(corpus_paths()[0]) + len(corpus_paths()[1])
    total_row = _re.search(r"\| \*\*Total Verified\*\* \| \*\*(\d+)\*\*", content)
    assert total_row, "Doc must contain a generated Total Verified row"
    assert int(total_row.group(1)) == corpus_size, (
        f"Doc reports a corpus of {total_row.group(1)} but the real corpus is {corpus_size}; "
        f"rerun: python3 tools/ci_conformance.py --matrix")
    from collections import Counter
    from tests.engine_harness import feature_for
    expected_counts = Counter(feature_for(path.relative_to(ROOT).as_posix())
                              for group in corpus_paths() for path in group)
    documented_counts = {name: int(count) for name, count in _re.findall(
        r'\| \*\*`([^`]+)`\*\* \| (\d+) \|', content)}
    assert documented_counts == dict(expected_counts), 'Feature rows must match the current corpus, not only its total'
    assert f"{corpus_size}/{corpus_size} (100.0%)" in content, "Doc must record current measured Wysna Compiler corpus parity"
    assert "error_unions" in content, "Doc must record error-union coverage"
    profile = json.loads((ROOT / "tests/engine_profiles.json").read_text())
    error_union_fixtures = [
        label for label, feature in profile.get("fixtures", {}).items()
        if feature == "error_unions"
    ]
    error_union_row = _re.search(r"\| \*\*`error_unions`\*\* \| (\d+) \|", content)
    assert error_union_row and int(error_union_row.group(1)) == len(error_union_fixtures), (
        "Parity matrix error-union count must match fixture classifications"
    )
    expected_error_union_paths = list((ROOT / 'tests/runtime').glob('error_union_*.wysna'))
    expected_error_union_paths += list((ROOT / 'tests/types/invalid').glob('error_union_*.wysna'))
    expected_error_union_fixtures = {path.relative_to(ROOT).as_posix() for path in expected_error_union_paths}
    assert set(error_union_fixtures) == expected_error_union_fixtures, "F03 fixtures must be classified together"
    assert "Bootstrap Fixed-Point vs. Core Corpus Parity" in content, (
        "Doc must distinguish bootstrap fixed-point from core-corpus parity"
    )

    # Public manuals describe contracts; private plans and implementation reports are not test inputs.
    from urllib.parse import unquote
    documents = [ROOT / 'README.md', ROOT / 'SPEC.md'] + sorted((ROOT / 'docs').glob('*.md'))
    for document in documents:
        for destination in re.findall(r'\[[^\]]+\]\(([^)]+)\)', document.read_text()):
            target = destination.split('#', 1)[0].strip('<>')
            if not target or re.match(r'[a-zA-Z][a-zA-Z0-9+.-]*:', target):
                continue
            assert not target.startswith('/'), f'Public manual contains an absolute local path: {document}: {target}'
            resolved = (document.parent / unquote(target)).resolve()
            assert resolved.is_relative_to(ROOT) and resolved.exists(), f'Broken public documentation link: {document}: {target}'

    limits = (ROOT / "docs/KNOWN_LIMITS.md").read_text()
    assert "Lambda and callable syntax" not in limits, (
        "Wysna Compiler supports first-class calls and closures in the tested expansion cases"
    )


def run_all() -> bool:
    print("==================================================")
    print("Wysna D01 Parity Matrix Suite")
    print("==================================================")
    try:
        test_fixture_accounting_and_staleness()
        test_unsupported_reasons_and_milestones()
        test_standalone_selfhost_execution_contracts()
        test_ci_matrix_generation_tooling()
        test_documentation_consistency()
        print("==================================================")
        print("ALL D01 PARITY MATRIX TESTS PASSED.")
        print("==================================================")
        return True
    except AssertionError as e:
        print(f"[FAIL] Assertion error: {e}")
        return False
    except Exception as e:
        print(f"[FAIL] Unexpected error: {e}")
        return False


if __name__ == "__main__":
    ok = run_all()
    sys.exit(0 if ok else 1)
