"""Preserve CI coverage while assigning contracts and conformance separate budgets."""
import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
CONTRACT_MODULES = (
    'test_ci_harness', 'test_execution_context', 'test_hir_return_paths',
    'test_f03_error_unions', 'test_compilation_session', 'test_compiler_service',
    'test_effect_analysis', 'test_architecture_contract', 'test_native_toolchain', 'test_expansion_contracts',
    'test_audit_remediation', 'test_mir', 'test_selfhost_mir', 'test_dictionary_key_parity', 'test_native_mir_pipeline',
    'test_selfhost_compilation_context', 'test_distribution_contract', 'test_installers',
    'test_audit_findings', 'test_standard_toolkit', 'test_http_client',
)
MATRIX = {'ubuntu-latest,clang', 'ubuntu-latest,gcc', 'macos-latest,clang'}


def test_ci_workflow_contracts():
    source = (ROOT / '.github/workflows/ci.yml').read_text(encoding='utf-8')
    assert 'group: ${{ github.workflow }}-${{ github.ref }}' in source
    assert 'cancel-in-progress: true' in source
    expected_branches = {
        'push': {'main'},
        'pull_request': {'main', 'develop'},
    }
    for event, expected in expected_branches.items():
        trigger = re.search(r'^  ' + event + r':\n    branches: \[([^\]]+)\]',
                            source, re.MULTILINE)
        assert trigger, event + ' branch filters must declare the integration path'
        branches = {branch.strip().strip('"') for branch in trigger.group(1).split(',')}
        assert branches == expected, (event, branches)
    body = source.split('\njobs:\n', 1)[1]
    headings = list(re.finditer(r'^  ([a-z][a-z0-9-]*):\n', body, re.MULTILINE))
    jobs = {heading.group(1): body[heading.end():headings[index + 1].start()
                                  if index + 1 < len(headings) else len(body)]
            for index, heading in enumerate(headings)}
    service_campaign = jobs['service-load-campaign']
    assert 'timeout-minutes: 15' in service_campaign
    assert '--warmup-seconds 15 --duration-seconds 120' in service_campaign
    assert '--repetitions 3' in service_campaign
    assert '--minimum-latency-samples 1000' in service_campaign
    performance_source = (ROOT / '.github/workflows/service-performance.yml').read_text(encoding='utf-8')
    assert 'workflow_dispatch:' in performance_source and 'schedule:' in performance_source
    assert 'tags:' in performance_source and '- "v*"' in performance_source
    assert '--warmup-seconds 60 --duration-seconds 600' in performance_source
    assert '--repetitions 3' in performance_source
    llvm = jobs['llvm-smoke']
    assert 'runs-on: ubuntu-latest' in llvm
    assert 'needs: wheel-build' in llvm
    assert 'python -m tests.unit.test_llvm_backend -v' in llvm
    assert 'python tools/validate_llvm_install.py' in llvm
    assert '${wheel}[llvm]' in llvm
    assert 'qualify_llvm.py' not in llvm
    full_llvm_source = (ROOT / '.github/workflows/llvm-qualification.yml').read_text(encoding='utf-8')
    assert 'schedule:' in full_llvm_source and '- cron: "0 8 * * 3"' in full_llvm_source
    assert 'workflow_dispatch:' in full_llvm_source
    assert 'tags:' in full_llvm_source and '- "v*"' in full_llvm_source
    full_body = full_llvm_source.split('\njobs:\n', 1)[1]
    full_headings = list(re.finditer(r'^  ([a-z][a-z0-9-]*):\n', full_body, re.MULTILINE))
    full_jobs = {heading.group(1): full_body[heading.end():full_headings[index + 1].start()
                                              if index + 1 < len(full_headings) else len(full_body)]
                 for index, heading in enumerate(full_headings)}
    full_llvm = full_jobs['llvm-experimental']
    assert 'os: [ubuntu-latest, macos-latest]' in full_llvm
    assert 'python -m tests.unit.test_llvm_backend -v' in full_llvm
    assert 'python tools/validate_llvm_install.py' in full_llvm
    assert '${wheel}[llvm]' in full_llvm
    assert '--sanitize --output .wysna_cache/llvm-sanitized' in full_llvm
    sdk_build, sdk_consumer = full_jobs['llvm-sdk-build'], full_jobs['installed-llvm-sdk']
    assert 'package_llvm_sdk.py' in sdk_build and 'benchmark_llvm.py' not in sdk_build
    comparison = full_jobs['llvm-comparison']
    assert 'needs: llvm-sdk-build' in comparison and 'profile: [dev, release, size]' in comparison
    assert 'benchmark_llvm.py' in comparison and '--profiles ${{ matrix.profile }}' in comparison
    assert 'llvm-sdk-qualification-${{ matrix.os }}' in comparison
    assert 'qualify_llvm.py' in sdk_build and '--source-commit "$GITHUB_SHA"' in sdk_build
    assert 'actions/checkout' not in sdk_consumer
    assert 'needs: llvm-sdk-build' in sdk_consumer and 'validate_llvm_sdk.py' in sdk_consumer
    native_build, native_consumer = jobs['native-sdk-build'], jobs['installed-native-sdk']
    for job in (native_build, native_consumer):
        assert 'os: [ubuntu-latest, macos-latest, windows-latest]' in job
        assert 'fail-fast: false' in job
        assert 'actions/upload-artifact@v4' in job
    assert 'package_native.py' in native_build and '--source-commit "$GITHUB_SHA"' in native_build
    assert 'actions/checkout' not in native_consumer
    assert 'needs: native-sdk-build' in native_consumer
    assert '--strict-source --compiler-source' in native_consumer
    assert 'validate_native_sdk.py' in native_consumer
    assert 'compiler-contracts' in jobs, 'Compiler contracts need their own execution budget'
    contracts, conformance = jobs['compiler-contracts'], jobs['conformance']
    runtime = jobs['runtime-conformance']
    integration = jobs['integration-conformance']
    assert 'PYTHONUNBUFFERED: "1"' in source
    for job in (contracts, runtime, integration):
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
        './wysna test --differential 20', './wysna test --fuzz 100',
        'python3 -m tests.unit.test_service_pilot',
        'python3 -m tests.unit.test_service_measurement',
        'python3 -m tests.unit.test_browser_library',
        'python3 tools/validate_sqlite_ffi.py --sanitize',
        'python3 tools/validate_sqlite_import.py --sanitize --rows 1000 --samples 1',
    ):
        assert command in integration, command
    full_suite = './wysna test 2>&1 | tee .wysna_cache/ci-artifacts/conformance.log'
    assert full_suite in runtime and full_suite not in integration
    assert '    needs:' not in runtime and '    needs:' not in integration
    assert 'actions/checkout' not in conformance
    assert 'runs-on: ubuntu-latest' in conformance
    assert 'timeout-minutes: 5' in conformance
    rows = re.findall(r'- \{os: ([\w-]+), cc: ([\w-]+)\}', conformance)
    assert {os_name + ',' + compiler for os_name, compiler in rows} == MATRIX
    assert len(rows) == len(MATRIX)
    assert 'name: Conformance (${{ matrix.os }}, ${{ matrix.cc }})' in conformance
    assert re.search(r'^    needs: \[compiler-contracts, runtime-conformance, integration-conformance, llvm-smoke, seed-installers, windows-installer\]
    assert 'package_distribution.py' in jobs['seed-installers']
    assert 'validate_unix_lifecycle.py' in jobs['seed-installers']
    assert 'fedora:44' in jobs['seed-installers']
    assert 'package_distribution.py' in jobs['windows-installer']
    assert '-UpgradeFrom' in jobs['windows-installer']
    assert 'path: .wysna_cache/installation-wheel' in jobs['installed-wheel']
    assert 'python .wysna_cache/installation-wheel/validate_wheel.py --wheel .wysna_cache/installation-wheel/*.whl' in jobs['installed-wheel']
    assert re.search(r'^    if: always\(\)$', conformance, re.MULTILINE)
    match = re.search(r'- name: Require compiler, runtime, integration and LLVM smoke success\n[\s\S]*?        run: \|\n'
                      r'((?:          [^\n]*\n)+)', conformance)
    assert match, 'Required conformance statuses must reject failed or skipped contracts'
    script = '\n'.join(line[10:] for line in match.group(1).splitlines())
    variables = ('CONTRACT_RESULT','RUNTIME_RESULT','INTEGRATION_RESULT','LLVM_SMOKE_RESULT',
                 'SEED_INSTALLER_RESULT','WINDOWS_INSTALLER_RESULT')
    # Exercise every required gate failing independently, including absent results.
    # A Cartesian product repeats the same conjunction thousands of times.
    cases = [('success',) * len(variables)]
    for index in range(len(variables)):
        for status in ('failure','cancelled','skipped',''):
            row = ['success'] * len(variables)
            row[index] = status
            cases.append(tuple(row))
    for results in cases:
        result = subprocess.run(
            ['bash', '-c', script], capture_output=True, text=True,
            env=dict(os.environ, **dict(zip(variables,results))), timeout=10)
        assert (result.returncode == 0) == all(status == 'success' for status in results), (results, result)




if __name__ == '__main__':
    test_ci_workflow_contracts()
    print('CI workflow: coverage, matrices, budgets and failure propagation contracts passed')
, conformance, re.MULTILINE)
    assert 'package_distribution.py' in jobs['seed-installers']
    assert 'validate_unix_lifecycle.py' in jobs['seed-installers']
    assert 'fedora:44' in jobs['seed-installers']
    assert 'package_distribution.py' in jobs['windows-installer']
    assert '-UpgradeFrom' in jobs['windows-installer']
    assert 'path: .wysna_cache/installation-wheel' in jobs['installed-wheel']
    assert 'python .wysna_cache/installation-wheel/validate_wheel.py --wheel .wysna_cache/installation-wheel/*.whl' in jobs['installed-wheel']
    assert re.search(r'^    if: always\(\)$', conformance, re.MULTILINE)
    match = re.search(r'- name: Require compiler, runtime and integration conformance success\n[\s\S]*?        run: \|\n'
                      r'((?:          [^\n]*\n)+)', conformance)
    assert match, 'Required conformance statuses must reject failed or skipped contracts'
    script = '\n'.join(line[10:] for line in match.group(1).splitlines())
    variables = ('CONTRACT_RESULT','RUNTIME_RESULT','INTEGRATION_RESULT','LLVM_RESULT',
                 'LLVM_SDK_RESULT','LLVM_COMPARISON_RESULT','SEED_INSTALLER_RESULT','WINDOWS_INSTALLER_RESULT')
    # Exercise every required gate failing independently, including absent results.
    # A Cartesian product repeats the same conjunction thousands of times.
    cases = [('success',) * len(variables)]
    for index in range(len(variables)):
        for status in ('failure','cancelled','skipped',''):
            row = ['success'] * len(variables)
            row[index] = status
            cases.append(tuple(row))
    for results in cases:
        result = subprocess.run(
            ['bash', '-c', script], capture_output=True, text=True,
            env=dict(os.environ, **dict(zip(variables,results))), timeout=10)
        assert (result.returncode == 0) == all(status == 'success' for status in results), (results, result)




if __name__ == '__main__':
    test_ci_workflow_contracts()
    print('CI workflow: coverage, matrices, budgets and failure propagation contracts passed')
, conformance, re.MULTILINE)
    assert 'package_distribution.py' in jobs['seed-installers']
    assert 'validate_unix_lifecycle.py' in jobs['seed-installers']
    assert 'fedora:44' in jobs['seed-installers']
    assert 'package_distribution.py' in jobs['windows-installer']
    assert '-UpgradeFrom' in jobs['windows-installer']
    assert 'path: .wysna_cache/installation-wheel' in jobs['installed-wheel']
    assert 'python .wysna_cache/installation-wheel/validate_wheel.py --wheel .wysna_cache/installation-wheel/*.whl' in jobs['installed-wheel']
    assert re.search(r'^    if: always\(\)$', conformance, re.MULTILINE)
    match = re.search(r'- name: Require compiler, runtime, integration and LLVM smoke success\n[\s\S]*?        run: \|\n'
                      r'((?:          [^\n]*\n)+)', conformance)
    assert match, 'Required conformance statuses must reject failed or skipped contracts'
    script = '\n'.join(line[10:] for line in match.group(1).splitlines())
    variables = ('CONTRACT_RESULT','RUNTIME_RESULT','INTEGRATION_RESULT','LLVM_SMOKE_RESULT',
                 'SEED_INSTALLER_RESULT','WINDOWS_INSTALLER_RESULT')
    # Exercise every required gate failing independently, including absent results.
    # A Cartesian product repeats the same conjunction thousands of times.
    cases = [('success',) * len(variables)]
    for index in range(len(variables)):
        for status in ('failure','cancelled','skipped',''):
            row = ['success'] * len(variables)
            row[index] = status
            cases.append(tuple(row))
    for results in cases:
        result = subprocess.run(
            ['bash', '-c', script], capture_output=True, text=True,
            env=dict(os.environ, **dict(zip(variables,results))), timeout=10)
        assert (result.returncode == 0) == all(status == 'success' for status in results), (results, result)




if __name__ == '__main__':
    test_ci_workflow_contracts()
    print('CI workflow: coverage, matrices, budgets and failure propagation contracts passed')
, conformance, re.MULTILINE)
    assert 'package_distribution.py' in jobs['seed-installers']
    assert 'validate_unix_lifecycle.py' in jobs['seed-installers']
    assert 'fedora:44' in jobs['seed-installers']
    assert 'package_distribution.py' in jobs['windows-installer']
    assert '-UpgradeFrom' in jobs['windows-installer']
    assert 'path: .wysna_cache/installation-wheel' in jobs['installed-wheel']
    assert 'python .wysna_cache/installation-wheel/validate_wheel.py --wheel .wysna_cache/installation-wheel/*.whl' in jobs['installed-wheel']
    assert re.search(r'^    if: always\(\)$', conformance, re.MULTILINE)
    match = re.search(r'- name: Require compiler, runtime and integration conformance success\n[\s\S]*?        run: \|\n'
                      r'((?:          [^\n]*\n)+)', conformance)
    assert match, 'Required conformance statuses must reject failed or skipped contracts'
    script = '\n'.join(line[10:] for line in match.group(1).splitlines())
    variables = ('CONTRACT_RESULT','RUNTIME_RESULT','INTEGRATION_RESULT','LLVM_RESULT',
                 'LLVM_SDK_RESULT','LLVM_COMPARISON_RESULT','SEED_INSTALLER_RESULT','WINDOWS_INSTALLER_RESULT')
    # Exercise every required gate failing independently, including absent results.
    # A Cartesian product repeats the same conjunction thousands of times.
    cases = [('success',) * len(variables)]
    for index in range(len(variables)):
        for status in ('failure','cancelled','skipped',''):
            row = ['success'] * len(variables)
            row[index] = status
            cases.append(tuple(row))
    for results in cases:
        result = subprocess.run(
            ['bash', '-c', script], capture_output=True, text=True,
            env=dict(os.environ, **dict(zip(variables,results))), timeout=10)
        assert (result.returncode == 0) == all(status == 'success' for status in results), (results, result)




if __name__ == '__main__':
    test_ci_workflow_contracts()
    print('CI workflow: coverage, matrices, budgets and failure propagation contracts passed')
