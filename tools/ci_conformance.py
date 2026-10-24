#!/usr/bin/env python3
"""Run declared compiler coverage; the release profile rejects unsupported rows."""
import json
import hashlib
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from common.Platform import executable_name
from tests.engine_harness import ENGINES, PROFILES, corpus_paths, execute, verify_case


def source_fingerprint(root=ROOT):
    """Bind evidence to the commit and exact source, fixture and harness bytes."""
    roots = ('checker', 'codegen', 'common', 'compiler', 'error', 'formatter', 'hir',
             'lexer', 'mir', 'node', 'parser', 'runtime', 'selfhost/src', 'stdlib',
             'tooling', 'tools', 'tests', 'examples', 'tokens')
    suffixes = {'.py', '.wysna', '.c', '.h', '.expected', '.json', '.txt'}
    paths = {path for name in roots for path in (root / name).rglob('*')
             if path.is_file() and path.suffix in suffixes
             and (path.suffix != '.txt' or 'data' in path.parts)
             and not {'__pycache__', 'failures'}.intersection(path.parts)}
    paths.update(root / name for name in ('main.py', 'wysna', 'run_conformance.py', 'selfhost/compiler.wysna',
                                         'SPEC.md', 'README.md', 'docs/STDLIB.md', 'docs/OWNERSHIP_LIFETIME.md',
                                         'docs/KNOWN_LIMITS.md', 'docs/SERVICE_FOUNDATIONS.md',
                                         'pyproject.toml', '.github/workflows/ci.yml',
                                         'runtime/wysna_host.h', 'runtime/wysna_runtime.h')
                 if (root / name).is_file())
    inputs = {path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
              for path in sorted(paths)}
    git = subprocess.run(['git', '-C', str(root), 'rev-parse', 'HEAD'],
                         capture_output=True, text=True, timeout=30)
    return {'version': 1, 'sha': git.stdout.strip() if git.returncode == 0 else None,
            'inputs': inputs,
            'digest': hashlib.sha256(json.dumps(inputs, sort_keys=True).encode()).hexdigest()}


def stale_reason(measured, current):
    recorded = measured.get('provenance')
    if not recorded:
        return 'measurement has no source fingerprints'
    if recorded != current:
        changed = sorted(name for name in set(recorded.get('inputs', {})) | set(current['inputs'])
                         if recorded.get('inputs', {}).get(name) != current['inputs'].get(name))
        return 'source/fixture hashes or commit changed: ' + ', '.join(changed[:3])
    return ''


def _measured_summary():
    """Reads the per-engine results produced by the last verified run.

    The matrix used to be printed straight from `engine_profiles.json`: it never ran a
    compiler and emitted `✅ n/n` unconditionally, so the published parity table could
    not disagree with the declaration file it was derived from. It now reports only
    measured results, and says so explicitly when no run is available.
    """
    summary_path = Path(os.environ.get('WYSNA_CI_ARTIFACTS', ROOT / '.wysna_cache/ci-artifacts')) / 'summary.json'
    if not summary_path.exists():
        return None
    try:
        return json.loads(summary_path.read_text())
    except (OSError, ValueError):
        return None


def generate_markdown_matrix(measured=None) -> str:
    positives, negatives = corpus_paths()
    fixtures = positives + negatives
    if measured is None:
        measured = _measured_summary()
    invalidation = stale_reason(measured, source_fingerprint()) if measured else ''
    coverage = {} if invalidation else (measured or {}).get('coverage') or {}

    by_feature = {}
    for path in fixtures:
        label = path.relative_to(ROOT).as_posix()
        feat = PROFILES.get('fixtures', {}).get(label, 'core')
        by_feature.setdefault(feat, []).append(label)

    rows = []
    rows.append("# Wysna Engine Feature Parity Matrix")
    rows.append("")
    rows.append("| Feature Category | Fixtures | Wysna Seed (Interpreter) | Wysna Seed (AST C99) | Wysna Seed (HIR C99) | Wysna Compiler (Self-Hosted) | Status / Milestone |")
    rows.append("| :--- | :---: | :---: | :---: | :---: | :---: | :--- |")

    tot_fixtures = len(fixtures)
    seed_interp_tot = 0
    seed_ast_tot = 0
    seed_hir_tot = 0
    selfhost_tot = 0

    engine_columns = ('interpreter', 'ast', 'hir', 'selfhost')
    labels = {'interpreter': 'Wysna Seed (Interpreter)', 'ast': 'Wysna Seed (AST C99)',
              'hir': 'Wysna Seed (HIR C99)', 'selfhost': 'Wysna Compiler (Self-Hosted)'}

    def cell(engine, feature, count):
        if invalidation:
            return '—'
        stats = (coverage.get(engine) or {})
        if feature is not None:
            stats = ((measured or {}).get('features') or {}).get(feature, {}).get(engine) or {}
        passed = stats.get('passed')
        unsupported = stats.get('unsupported', 0)
        failed = stats.get('failed', 0)
        if passed is None:
            return '—'
        mark = '✅' if failed == 0 else '❌'
        if unsupported:
            return f"⏳ {passed}/{count} ({unsupported} declared unsupported)"
        return f"{mark} {passed}/{count}"

    feature_order = list(PROFILES['features'])
    for feat in feature_order:
        items = by_feature.get(feat, [])
        count = len(items)
        unsupported_selfhost = PROFILES.get('features', {}).get(feat, {}).get('selfhost', '')
        note = f"Pending {unsupported_selfhost.split(':')[0].strip()}" if unsupported_selfhost else "Declared supported"
        cells = [cell(engine, feat, count) for engine in engine_columns]
        rows.append(f"| **`{feat}`** | {count} | " + " | ".join(cells) + f" | {note} |")
        for engine in engine_columns:
            stats = ((measured or {}).get('features') or {}).get(feat, {}).get(engine) or {}
            if stats.get('passed'):
                if engine == 'selfhost':
                    selfhost_tot += stats['passed']
                else:
                    pass

    totals = []
    for engine in engine_columns:
        stats = coverage.get(engine) or {}
        passed = stats.get('passed')
        if passed is None:
            totals.append('—')
        else:
            totals.append(f"**{passed}/{tot_fixtures} ({passed / tot_fixtures * 100:.1f}%)**")
    stale_warning = False
    measured_labels = set((measured or {}).get('corpus') or [])
    current_labels = {path.relative_to(ROOT).as_posix() for path in fixtures}
    if invalidation:
        verdict = 'no verified run for current source tree; STALE: ' + invalidation
        stale_warning = True
    elif measured and measured_labels != current_labels:
        missing = sorted(current_labels - measured_labels)[:3]
        stale = sorted(measured_labels - current_labels)[:3]
        verdict = (f'STALE: summary.json covers a different corpus '
                   f'(missing {missing}, no longer present {stale}); rerun ci_conformance.py')
        stale_warning = True
    elif measured:
        verdict = 'measured'
    else:
        verdict = 'no verified run in this workspace; run ci_conformance.py first'
    rows.append(f"| **Total Verified** | **{tot_fixtures}** | " + " | ".join(totals) + f" | _{verdict}_ |")
    if stale_warning:
        rows.append("")
        rows.append(f"> **Warning:** {verdict}")
    rows.append("")
    rows.append("")
    rows.append("Counts above come from `summary.json`, produced by an actual run of "
                "`tools/ci_conformance.py` over the corpus. They are measurements, not a "
                "restatement of `tests/engine_profiles.json`.")
    rows.append("")
    rows.append("### Engine Definitions")
    rows.append("- **Wysna Seed Interpreter (`runtime/Interpreter.py`):** Canonical reference AST tree-walking interpreter.")
    rows.append("- **Wysna Seed AST C99 (`codegen/CCodeGen.py`):** Secondary C99 oracle for differential validation.")
    rows.append("- **Wysna Seed Typed HIR (`hir/HirCCodeGen.py`):** Primary compiler entry; verified MIR drives C99 emission (ADR-018).")
    rows.append("- **Wysna Compiler Self-Hosted (`selfhost/compiler.wysna`):** Native Stage-2 compiler binary compiled via 3-stage bootstrap fixed-point.")
    return "\n".join(rows)


def main():
    arguments = sys.argv[1:]
    if '--matrix' in arguments:
        print(generate_markdown_matrix())
        return 0

    sanitize = '--sanitize' in arguments
    profile = 'release' if '--profile=release' in arguments else 'integration'
    valid_flags = ('--sanitize', '--profile=release', '--profile=integration', '--matrix')
    if any(arg not in valid_flags for arg in arguments):
        raise SystemExit('Usage: ci_conformance.py [--sanitize] [--profile=integration|release] [--matrix]')
    artifacts = Path(os.environ.get('WYSNA_CI_ARTIFACTS', ROOT / '.wysna_cache/ci-artifacts'))
    artifacts.mkdir(parents=True, exist_ok=True)
    compiler = subprocess.run(shlex.split(os.environ.get('CC', 'cc')) + ['--version'],
                              capture_output=True, text=True, timeout=30, check=True)
    provenance = source_fingerprint()
    sha = provenance['sha']
    (artifacts / 'environment.json').write_text(json.dumps({
        'sha': sha, 'python': sys.version, 'platform': sys.platform,
        'compiler': compiler.stdout, 'profile': profile, 'sanitize': sanitize,
        'compile_timeout_seconds': 120, 'run_timeout_seconds': 30}, indent=2))
    # This subprocess is bounded separately because it builds the compiler itself.
    bootstrap = execute([sys.executable, '-c',
        'from compiler.Bootstrap import ensure_selfhost_compiler_binary; '
        'print(ensure_selfhost_compiler_binary(' + repr(str(ROOT)) + '))'],
        artifacts, 'bootstrap', 600)
    if bootstrap['returncode']:
        print(bootstrap['stderr'])
        return 1
    selfhost_bin = ROOT / '.wysna_cache' / executable_name('stage2')
    support_files = PROFILES['support_files']
    positives, negatives = corpus_paths()
    fixtures = positives + negatives
    labels = {path.relative_to(ROOT).as_posix() for path in fixtures}
    stale = set(PROFILES['fixtures']) - labels
    if stale or not positives or not negatives:
        raise RuntimeError(f'Empty corpus or stale profile entries: {stale}')
    passed = 0
    feature_coverage = {}
    coverage = {engine: {'passed': 0, 'failed': 0, 'unsupported': 0} for engine in ENGINES}
    with tempfile.TemporaryDirectory(prefix='wysna-ci-') as directory:
        for path in fixtures:
            label = path.relative_to(ROOT).as_posix()
            expected = path.with_suffix('.expected').read_text() if path in positives else None
            if path in positives and expected is None:
                raise RuntimeError(f'Missing expected output: {label}')
            ok, detail, report = verify_case(path.read_text(), label, selfhost_bin, directory, expected,
                                sanitize, profile == 'release', path in negatives)
            passed += int(ok)
            feature_rows = feature_coverage.setdefault(report['feature'], {
                engine: {'passed': 0, 'failed': 0, 'unsupported': 0} for engine in ENGINES})
            for engine in ENGINES:
                if engine in report['unsupported']:
                    state = 'unsupported'
                else:
                    state = next(row['status'] for row in report['results'] if row['engine'] == engine)
                coverage[engine][state] += 1
                feature_rows[engine][state] += 1
            print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + ('' if ok else ': ' + detail), flush=True)
    unchanged = provenance == source_fingerprint()
    summary = {'provenance': provenance, 'source_unchanged': unchanged, 'profile': profile, 'passed_cases': passed, 'total_cases': len(fixtures),
               'coverage': coverage, 'features': feature_coverage, 'support_files': support_files,
               'corpus': sorted(labels)}
    (artifacts / 'summary.json').write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))
    return 0 if passed == len(fixtures) and unchanged else 1


if __name__ == '__main__':
    sys.exit(main())
