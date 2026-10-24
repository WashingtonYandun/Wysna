"""Preserve CI coverage while assigning contracts and conformance separate budgets."""
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_MODULES = (
    'test_ci_harness', 'test_execution_context', 'test_hir_return_paths',
    'test_f03_error_unions', 'test_compilation_session', 'test_compiler_service',
    'test_architecture_contract', 'test_native_toolchain', 'test_expansion_contracts',
    'test_audit_remediation', 'test_mir', 'test_selfhost_mir',
    'test_selfhost_compilation_context', 'test_distribution_contract',
    'test_audit_findings', 'test_standard_toolkit', 'test_http_client',
)
MATRIX = {'ubuntu-latest,clang', 'ubuntu-latest,gcc', 'macos-latest,clang'}


def test_ci_workflow_contracts():
    source = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
    for event in ('push', 'pull_request'):
        trigger = re.search(r'^  ' + event + r':\n    branches: \[([^\]]+)\]',
                            source, re.MULTILINE)
        assert trigger, event + ' branch filters must declare the integration path'
        branches = {branch.strip().strip('"') for branch in trigger.group(1).split(',')}
        assert {'main', 'develop'} <= branches, (event, branches)
    body = source.split('\njobs:\n', 1)[1]
    headings = list(re.finditer(r'^  ([a-z][a-z0-9-]*):\n', body, re.MULTILINE))
    jobs = {heading.group(1): body[heading.end():headings[index + 1].start()
                                  if index + 1 < len(headings) else len(body)]
            for index, heading in enumerate(headings)}
    assert 'compiler-contracts' in jobs, 'Compiler contracts need their own execution budget'
    contracts, conformance = jobs['compiler-contracts'], jobs['conformance']
    for job in (contracts, conformance):
        rows = re.findall(r'- \{os: ([\w-]+), cc: ([\w-]+)\}', job)
        assert {os_name + ',' + compiler for os_name, compiler in rows} == MATRIX
        assert len(rows) == len(MATRIX)
        assert 'timeout-minutes: 30' in job
        assert 'fail-fast: false' in job
        assert 'CC: ${{ matrix.cc }}' in job
        assert "pip install -e '.[http,service]'" in job
        assert 'actions/upload-artifact@v4' in job
    modules = re.findall(r'^\s+python3 -m tests\.unit\.(\w+)\s*$', contracts, re.MULTILINE)
    assert sorted(modules) == sorted(CONTRACT_MODULES), modules
    assert not re.search(r'python3 -m tests\.unit\.test_ci_harness', conformance)
    for command in (
        "python3 tools/ci_conformance.py --profile=${{ inputs.profile || 'release' }}",
        './wysna test 2>&1 | tee .wysna_cache/ci-artifacts/conformance.log',
        './wysna test --differential 20', './wysna test --fuzz 100',
        'python3 -m tests.unit.test_service_pilot',
        'python3 -m tests.unit.test_service_measurement',
        'python3 -m tests.unit.test_browser_library',
        'python3 tools/validate_sqlite_ffi.py --sanitize',
        'python3 tools/validate_sqlite_import.py --sanitize --rows 1000 --samples 1',
    ):
        assert command in conformance, command
    assert re.search(r'^    needs: compiler-contracts$', conformance, re.MULTILINE)
    assert re.search(r'^    if: always\(\)$', conformance, re.MULTILINE)
    match = re.search(r'- name: Require compiler contract success\n[\s\S]*?        run: \|\n'
                      r'((?:          [^\n]*\n)+)', conformance)
    assert match, 'Required conformance statuses must reject failed or skipped contracts'
    script = '\n'.join(line[10:] for line in match.group(1).splitlines())
    for status in ('success', 'failure', 'cancelled', 'skipped'):
        result = subprocess.run(['bash', '-c', script], capture_output=True, text=True,
                                env=dict(os.environ, CONTRACT_RESULT=status), timeout=10)
        assert (result.returncode == 0) == (status == 'success'), (status, result)


if __name__ == '__main__':
    test_ci_workflow_contracts()
    print('CI workflow: coverage, matrices, budgets and failure propagation contracts passed')
