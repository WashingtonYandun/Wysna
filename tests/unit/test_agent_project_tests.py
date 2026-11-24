"""Agent test plans must be selectable, reproducible and independent of terminal text."""
import hashlib
import json
import os
import signal
import subprocess
import sys
import time

import pytest
from pathlib import Path
import xml.etree.ElementTree as ET

from project.Resolver import ProjectResolver
from common.Version import WYSNA_VERSION
from project.Scaffold import create_project
from tests.unit.test_project_scaffold import run_cli


def project(tmp_path, source='print("hello")\n'):
    root, _ = create_project('agent-tests', tmp_path)
    (root / 'tests/example.wysna').write_text(source, encoding='utf-8')
    return root


def machine(root, *arguments):
    result = run_cli(root, 'project', 'test', '--engine=seed', '--output-format=json-v1', *arguments)
    payload = json.loads(result.stdout)
    assert payload['schema_version'] == 'wysna.tool-result/1'
    assert result.stderr == ''
    return result, payload


def test_listing_is_a_deterministic_nonexecuting_snapshot(tmp_path):
    root = project(tmp_path, 'this is not valid Wysna\n')
    first, payload = machine(root, '--list')
    second, again = machine(root, '--list')
    assert first.returncode == second.returncode == 0
    assert payload['result'] == again['result']
    plan = payload['result']
    assert plan['schema_version'] == 'wysna.project-test-plan/1'
    assert plan['cases'][0]['id'] == 'test:example'
    assert plan['cases'][0]['entry_sha256'] == hashlib.sha256((root / 'tests/example.wysna').read_bytes()).hexdigest()
    assert len(plan['plan_hash']) == 64


def test_exact_case_selection_and_bounded_streams(tmp_path):
    root = project(tmp_path, 'print("a\\0b")\neprint("details")\n')
    result, payload = machine(root, '--case', 'test:example')
    assert result.returncode == 0
    case = payload['result']['cases'][0]
    assert case['id'] == 'test:example'
    assert case['stdout'] == 'a\0b' + os.linesep and case['stderr'] == 'details' + os.linesep
    assert case['stdout_sha256'] == hashlib.sha256(b'a\0b' + os.linesep.encode()).hexdigest()
    failed, failure = machine(root, '--case', 'example')
    assert failed.returncode == 2 and failure['status'] == 'usage_error'


def test_stale_plan_never_starts_the_selected_entry(tmp_path):
    root = project(tmp_path)
    _, payload = machine(root, '--list')
    (root / 'tests/example.wysna').write_text('write_file("executed.txt", "unsafe")\n')
    result, error = machine(root, '--expect-plan-hash', payload['result']['plan_hash'])
    assert result.returncode == 2
    assert error['diagnostics'][0]['code'] == 'W5005'
    assert not (root / 'executed.txt').exists()


def test_test_plan_covers_transitive_project_sources(tmp_path):
    root = project(tmp_path, 'import "helper.wysna" as helper\nprint(helper.value())\n')
    helper = root / 'src/helper.wysna'
    helper.write_text('pub fn value() -> int { 1 }\n', encoding='utf-8')
    _, original = machine(root, '--list')
    verified, report = machine(root, '--expect-plan-hash', original['result']['plan_hash'])
    assert verified.returncode == 0, report
    assert report['result']['workspace_revision'] == original['result']['workspace_revision']
    helper.write_text('pub fn value() -> int { 2 }\n', encoding='utf-8')
    result, payload = machine(root, '--expect-plan-hash', original['result']['plan_hash'])
    assert result.returncode == 2
    assert payload['diagnostics'][0]['code'] == 'W5005'


def test_discovery_is_opt_in_and_has_stable_ids(tmp_path):
    root = project(tmp_path)
    (root / 'tests/z_test.wysna').write_text('print(42)\n')
    (root / 'tests/test_a.wysna').write_text('print(43)\n')
    (root / 'tests/helper.wysna').write_text('this is a helper, not a test')
    _, basic = machine(root, '--list')
    _, extended = machine(root, '--list', '--discover')
    assert basic['result']['selected'] == 1
    assert [case['id'] for case in extended['result']['cases']] == [
        'test:example', 'test:auto:tests/test_a.wysna', 'test:auto:tests/z_test.wysna']
    result, run = machine(root, '--discover', '--case', 'test:auto:tests/z_test.wysna')
    assert result.returncode == 0 and run['result']['cases'][0]['stdout'] == '42' + os.linesep


def test_compile_failures_have_structured_diagnostics(tmp_path):
    root = project(tmp_path, 'let value: int = "bad"\n')
    result, payload = machine(root)
    case = payload['result']['cases'][0]
    assert result.returncode == 1 and case['failure_kind'] == 'compile_error'
    assert case['diagnostics'][0]['code'] == 'W2001'


def test_failed_suite_keeps_json_and_junit_reports(tmp_path):
    root = project(tmp_path, 'eprint("bad <tag> & value")\nexit(7)\n')
    result, payload = machine(root, '--report-json', 'result.json', '--report-junit', 'result.xml')
    report = payload['result']
    assert result.returncode == 1 and report['failed'] == 1
    assert json.loads((root / 'result.json').read_text()) == report
    xml = ET.parse(root / 'result.xml').getroot()
    assert xml.attrib['failures'] == '1' and xml.attrib['tests'] == '1'
    assert xml.find('testcase/failure').text == 'bad <tag> & value\n'


def test_report_destinations_are_checked_before_execution(tmp_path):
    root = project(tmp_path, 'write_file("executed.txt", "unsafe")\n')
    result, _ = machine(root, '--report-junit', 'tests/example.wysna')
    assert result.returncode == 2 and not (root / 'executed.txt').exists()
    result, _ = machine(root, '--report-json', 'same.json', '--report-junit', 'same.json')
    assert result.returncode == 2 and not (root / 'executed.txt').exists()


def test_tests_are_independent_of_application_targets(tmp_path):
    root = project(tmp_path)
    manifest = root / 'wysna.toml'
    text = manifest.read_text().replace('[targets.app]', '[targets.first]')
    text += '\n[targets.second]\nentry = "src/main.wysna"\nprofile = "native-cli"\n'
    manifest.write_text(text)
    result, payload = machine(root)
    assert result.returncode == 0 and payload['result']['passed'] == 1


def test_fixture_directory_is_unique_and_removed(tmp_path):
    root = project(tmp_path, 'import "std/environment" as env\nprint(env.get("WYSNA_TEST_TMPDIR") ?? "missing")\n')
    first, one = machine(root)
    second, two = machine(root)
    paths = [Path(item['result']['cases'][0]['stdout'].strip()) for item in (one, two)]
    assert first.returncode == second.returncode == 0
    assert paths[0] != paths[1] and all(not path.exists() for path in paths)


def test_empty_suite_is_not_successful_coverage(tmp_path):
    root = project(tmp_path)
    manifest = root / 'wysna.toml'
    manifest.write_text(manifest.read_text().split('[[tests]]')[0])
    result, payload = machine(root)
    assert result.returncode == 2 and payload['result']['status'] == 'empty'
    assert payload['result']['selected'] == 0


def test_plan_records_frozen_lock(tmp_path):
    root = project(tmp_path)
    ProjectResolver.load(root / 'wysna.toml').write_lock()
    result, payload = machine(root, '--list', '--frozen')
    assert result.returncode == 0 and payload['result']['lock_sha256'] is not None
    locked = run_cli(root, 'project', 'lock', '--output-format=json-v1')
    assert locked.returncode == 0 and json.loads(locked.stdout)['command'] == 'project.lock'


@pytest.fixture(scope='module')
def native_sdk():
    from tests.unit.test_phase6_v08_autonomy_and_dogfood import Phase6V08AutonomyAndDogfoodTests
    suite = Phase6V08AutonomyAndDogfoodTests
    suite.setUpClass()
    try:
        yield suite()
    finally:
        suite.tearDownClass()


def native_machine(sdk, root, *arguments):
    scratch = root / '_scratch'
    scratch.mkdir(exist_ok=True)
    result = sdk._run_sdk(['project', 'test', '--output-format=json-v1', *arguments], root, scratch)
    assert result.stderr == '', result.stderr
    return result, json.loads(result.stdout)


@pytest.mark.parametrize('topic', ['project-tests', 'syntax'])
def test_installed_agent_discovery_matches_seed_guidance(native_sdk, tmp_path, topic):
    root = project(tmp_path)
    scratch = root / '_scratch'
    scratch.mkdir()
    seed = json.loads(run_cli(root, 'skills', topic, '--output-format=json-v1').stdout)
    result = native_sdk._run_sdk(['skills', topic, '--output-format=json-v1'], root, scratch)
    assert result.returncode == 0
    assert json.loads(result.stdout)['result'] == seed['result']
    seed_caps = json.loads(run_cli(root, 'capabilities', '--output-format=json-v1').stdout)
    native_caps = native_sdk._run_sdk(['capabilities', '--output-format=json-v1'], root, scratch)
    for payload in (seed_caps, json.loads(native_caps.stdout)):
        assert 'project.test' in payload['result']['machine_output']['commands']


def test_native_listing_matches_seed_and_preserves_path_encoding(native_sdk, tmp_path):
    root = project(tmp_path)
    if os.name != 'nt':
        special = root.with_name('project %09\tline\nend')
        root.rename(special)
        root = special
    (root / 'tests/test_more.wysna').write_text('print(42)\n')
    (root / 'tests/z_test.wysna').write_text('print(43)\n')
    ProjectResolver.load(root / 'wysna.toml').write_lock()
    _, seed = machine(root, '--list', '--discover', '--frozen')
    result, native = native_machine(native_sdk, root, '--list', '--discover', '--frozen')
    assert result.returncode == 0, native
    agree = ('schema_version', 'project', 'manifest_sha256', 'lock_sha256', 'toolchain',
             'workspace_sha256', 'cases', 'selected')
    assert {key: native['result'][key] for key in agree} == {key: seed['result'][key] for key in agree}
    assert len(native['result']['sdk_fingerprint']) == len(seed['result']['sdk_fingerprint']) == 64
    assert native['result']['plan_hash'] != seed['result']['plan_hash']
    stale, error = native_machine(native_sdk, root, '--expect-plan-hash', '0' * 64)
    assert stale.returncode == 2 and error['diagnostics'][0]['code'] == 'W5005'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX test compiler shim; real Windows SDK acceptance covers execution')
def test_native_machine_execution_reports_and_diagnostics(native_sdk, tmp_path):
    root = project(tmp_path, 'print("a\\0b")\neprint("details")\n')
    listed, plan = native_machine(native_sdk, root, '--case', 'test:example', '--list')
    assert listed.returncode == 0, plan
    result, payload = native_machine(native_sdk, root, '--case', 'test:example',
                                     '--expect-plan-hash', plan['result']['plan_hash'],
                                     '--report-json', 'test.json', '--report-junit', 'test.xml')
    assert result.returncode == 0, payload
    report = payload['result']
    assert report['workspace_revision'] == plan['result']['workspace_revision']
    assert report['cases'][0]['stdout'] == 'a\0b\n'
    assert json.loads((root / 'test.json').read_text()) == report
    assert ET.parse(root / 'test.xml').find('testcase/system-out').text == 'a\ufffdb\n'
    assert len(payload['artifacts']) == 2
    for artifact in payload['artifacts']:
        assert artifact['sha256'] == hashlib.sha256(Path(artifact['path']).read_bytes()).hexdigest()
    (root / 'tests/example.wysna').write_text('let invalid: int = "bad"\n')
    failed, errors = native_machine(native_sdk, root)
    assert failed.returncode == 1, errors
    case = errors['result']['cases'][0]
    assert case['failure_kind'] == 'compile_error' and case['diagnostics'][0]['code'] == 'W2001'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX test compiler shim; real Windows SDK acceptance covers execution')
def test_native_std_test_fixture_and_output_limit(native_sdk, tmp_path):
    root = project(tmp_path, 'import "std/test" as test\n'
                   'test.expect_equal([1, 2], [1, 2], "equal")\n'
                   'let directory = test.fixture_directory() else error { exit(9) }\n'
                   'print(directory)\n')
    result, payload = native_machine(native_sdk, root)
    assert result.returncode == 0, payload
    assert not Path(payload['result']['cases'][0]['stdout'].strip()).exists()
    (root / 'tests/example.wysna').write_text('while true { print("0123456789abcdef") }\n')
    result, payload = native_machine(native_sdk, root)
    case = payload['result']['cases'][0]
    assert result.returncode == 1 and case['status'] == 'output_limited', payload
    assert len(case['stdout'].encode()) == 1048576
    assert case['stdout_sha256'] == hashlib.sha256(case['stdout'].encode()).hexdigest()


@pytest.mark.skipif(os.name == 'nt', reason='POSIX test compiler shim; installed SDK covers real Windows execution')
def test_native_timeout_includes_compilation(native_sdk, tmp_path):
    root = project(tmp_path, 'while true {}\n')
    manifest = root / 'wysna.toml'
    manifest.write_text(manifest.read_text().replace('timeout_ms = 10000', 'timeout_ms = 500'))
    result, payload = native_machine(native_sdk, root, '--report-junit', 'timeout.xml')
    case = payload['result']['cases'][0]
    assert result.returncode == 1 and case['status'] == case['failure_kind'] == 'timed_out'
    assert case['exit_code'] is None and case['duration_ms'] < 5000


@pytest.mark.skipif(os.name == 'nt', reason='POSIX SIGTERM contract')
def test_native_cancellation_keeps_partial_report_and_cleans_fixture(native_sdk, tmp_path):
    root = project(tmp_path, 'import "std/test" as test\n'
                   'let directory = test.fixture_directory() else error { exit(9) }\n'
                   'write_file("started.txt", directory) else error { exit(8) }\n'
                   'while true {}\n')
    (root / 'tests/later_test.wysna').write_text('write_file("later.txt", "bad")\n')
    scratch = root / '_scratch'
    scratch.mkdir()
    process = subprocess.Popen([str(native_sdk.driver_bin), 'project', 'test', '--discover',
                                '--output-format=json-v1', '--report-json', 'cancel.json'],
                               cwd=root, env=native_sdk._clean_env(scratch),
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 20
        while not (root / 'started.txt').exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (root / 'started.txt').exists()
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        payload = json.loads(stdout)
        assert process.returncode == 130 and stderr == b'', (stdout, stderr)
        assert payload['status'] == payload['result']['status'] == 'cancelled'
        assert payload['result']['selected'] == 2 and payload['result']['executed'] == 1
        assert not Path((root / 'started.txt').read_text()).exists()
        assert not (root / 'later.txt').exists()
        assert json.loads((root / 'cancel.json').read_text()) == payload['result']
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def test_std_test_helpers_and_fixture_work_in_user_project(tmp_path):
    root = project(tmp_path, '''import "std/test" as test
let present_value: int? = Some(42)
let absent_value: int? = None
test.expect_equal([1, 2], [1, 2], "structural equality")
test.expect_not_equal("left", "right", "inequality")
test.expect_close(0.1 + 0.2, 0.3, 0.000001, "float tolerance")
test.expect_some(present_value, "present")
test.expect_none(absent_value, "absent")
let directory = test.fixture_directory() else error { exit(9) }
write_file(directory + "/fixture.txt", "fixture") else error { exit(8) }
print(directory)
''')
    result, payload = machine(root)
    assert result.returncode == 0, payload
    assert not Path(payload['result']['cases'][0]['stdout'].strip()).exists()


def test_timeout_is_structured_and_still_publishes_reports(tmp_path):
    root = project(tmp_path, 'while true {}\n')
    manifest = root / 'wysna.toml'
    manifest.write_text(manifest.read_text().replace('timeout_ms = 10000', 'timeout_ms = 500'))
    result, payload = machine(root, '--report-junit', 'timeout.xml')
    case = payload['result']['cases'][0]
    assert result.returncode == 1 and case['status'] == case['failure_kind'] == 'timed_out'
    assert case['exit_code'] is None
    assert ET.parse(root / 'timeout.xml').getroot().attrib['failures'] == '1'


def test_output_limit_preserves_only_bounded_prefix(tmp_path):
    root = project(tmp_path, 'while true { print("0123456789abcdef") }\n')
    result, payload = machine(root)
    case = payload['result']['cases'][0]
    assert result.returncode == 1 and case['failure_kind'] == 'output_limited'
    assert len(case['stdout'].encode()) == case['output_limit_bytes_per_stream'] == 1048576
    assert case['stdout_sha256'] == hashlib.sha256(case['stdout'].encode()).hexdigest()


def test_junit_replaces_xml_control_characters(tmp_path):
    root = project(tmp_path, 'print("a\\0b")\n')
    result, payload = machine(root, '--report-junit', 'control.xml')
    assert result.returncode == 0
    assert payload['result']['cases'][0]['stdout'].startswith('a\0b')
    assert ET.parse(root / 'control.xml').find('testcase/system-out').text.startswith('a\ufffdb')
    artifact = payload['artifacts'][0]
    assert artifact['sha256'] == hashlib.sha256((root / 'control.xml').read_bytes()).hexdigest()


def test_report_failure_keeps_the_executed_result(tmp_path):
    root = project(tmp_path)
    (root / 'blocked').write_text('preserve')
    result, payload = machine(root, '--report-json', 'blocked/report.json')
    assert result.returncode == 3 and payload['status'] == 'toolchain_error'
    assert payload['diagnostics'][0]['code'] == 'W5004'
    assert payload['result']['passed'] == 1
    assert (root / 'blocked').read_text() == 'preserve'


@pytest.mark.skipif(os.name == 'nt', reason='POSIX signal contract; Windows is covered by installed SDK acceptance')
def test_cancellation_cleans_fixture_and_does_not_start_later_tests(tmp_path):
    root = project(tmp_path, '''import "std/test" as test
let directory = test.fixture_directory() else error { exit(9) }
write_file("started.txt", directory) else error { exit(8) }
while true {}
''')
    (root / 'tests/later_test.wysna').write_text('write_file("later.txt", "bad")\n')
    command = [sys.executable, str(Path(__file__).resolve().parents[2] / 'main.py'),
               'project', 'test', '--discover', '--output-format=json-v1', '--report-json', 'cancel.json']
    process = subprocess.Popen(command, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 20
        while not (root / 'started.txt').exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert (root / 'started.txt').exists()
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
        payload = json.loads(stdout)
        assert stderr == b'' and process.returncode == 130
        assert payload['status'] == payload['result']['status'] == 'cancelled'
        assert payload['result']['selected'] == 2 and payload['result']['executed'] == 1
        assert not (root / 'later.txt').exists()
        assert not Path((root / 'started.txt').read_text()).exists()
        assert json.loads((root / 'cancel.json').read_text()) == payload['result']
    finally:
        if process.poll() is None:
            process.kill()
        process.communicate()


def test_bootstrap_fingerprint_tracks_runtime_headers(tmp_path):
    from compiler.Bootstrap import SELFHOST_BOOTSTRAP_INPUTS, bootstrap_fingerprint
    for name in SELFHOST_BOOTSTRAP_INPUTS:
        destination = tmp_path / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b'initial')
    before, provenance = bootstrap_fingerprint(tmp_path)
    key = str(Path('runtime/wysna_host_process.h'))
    assert key in provenance['inputs']
    (tmp_path / 'runtime/wysna_host_process.h').write_bytes(b'updated capture contract')
    after, changed = bootstrap_fingerprint(tmp_path)
    assert after != before
    assert changed['inputs'][key] != provenance['inputs'][key]


def test_native_report_utf8_replacement_matches_seed(tmp_path):
    from common.Platform import compiler_command, executable_name, native_stack_link_args
    root = Path(__file__).resolve().parents[2]
    sequences = [b'a\0b\r\n\t"\\', b'\xc2', b'\xe2\x82', b'\xf0\x9f\x92',
                 b'\xed\xa0\x80', b'\xf4\x90\x80\x80', b'\xe0\x80\x80', b'\x80\xff',
                 'á😀<&>'.encode()]
    source = '#define main wysna_sdk_main_under_test\n#include "' + (root / 'packaging/native/driver.c').as_posix() + '"\n#undef main\nint main(void) { puts("[");\n'
    for index, data in enumerate(sequences):
        source += 'const char data' + str(index) + '[] = {' + ','.join(str(value) for value in data) + '};\n'
        source += f'sdk_test_text(stdout, data{index}, sizeof(data{index}), 0);\n'
        if index + 1 < len(sequences):
            source += 'puts(",");\n'
    source += 'puts("]"); return 0; }\n'
    path = tmp_path / 'report-text.c'
    path.write_text(source)
    binary = tmp_path / executable_name('report-text')
    command = compiler_command()
    if os.name == 'nt':
        stack_flags = native_stack_link_args(command)
        flags = ['-D_CRT_SECURE_NO_WARNINGS', f'-DWYSNA_NATIVE_STACK_LINK_ARGUMENT="{stack_flags[0]}"', *stack_flags]
    else:
        flags = ['-lm']
    built = subprocess.run([*command, '-std=c99', '-Wno-unused-function',
                            f'-DWYSNA_NATIVE_VERSION="{WYSNA_VERSION}"', '-DWYSNA_NATIVE_TARGET="native-host"',
                            '-I' + str(root), str(path), *flags, '-o', str(binary)],
                           capture_output=True, timeout=60)
    assert built.returncode == 0, built.stderr
    result = subprocess.run([str(binary)], capture_output=True, timeout=10)
    assert result.returncode == 0 and result.stderr == b''
    assert json.loads(result.stdout) == [data.decode('utf-8', errors='replace') for data in sequences]
