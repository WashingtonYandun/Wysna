"""Deterministic test selection, snapshot plans and portable CI reports."""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import tempfile
import threading
import time
import xml.etree.ElementTree as ET

from common.Version import WYSNA_VERSION
from project.Manifest import ManifestError, ProjectTest
from project.Resolver import _portable_key

MAX_TEST_CASES = 4096
MAX_CAPTURE_BYTES = 1_048_576


class TestPlanMismatch(ManifestError):
    """The requested selection snapshot no longer matches its declared inputs."""


def build_test_plan(resolver, filter_text=None, discover=False, case_id=None):
    manifest = resolver.manifest
    cases = list(manifest.tests)
    if discover:
        directory = manifest.root / 'tests'
        if directory.exists():
            if directory.is_symlink() or not directory.is_dir():
                raise ManifestError('test discovery requires a regular tests directory')
            seen = {case.entry for case in cases}
            portable = {}
            for path in sorted(directory.rglob('*')):
                if path.is_symlink():
                    raise ManifestError('test discovery does not follow symlinks')
                relative = path.relative_to(manifest.root).as_posix()
                key = _portable_key(relative)
                if key in portable and portable[key] != relative:
                    raise ManifestError('test discovery paths collide on portable filesystems')
                portable[key] = relative
                if (path.is_file() and path.suffix == '.wysna' and relative not in seen
                        and (path.name.startswith('test_') or path.name.endswith('_test.wysna'))):
                    cases.append(ProjectTest('auto:' + relative, relative, 10000))
    cases = [case for case in cases if filter_text is None or filter_text in case.name]
    if case_id is not None:
        cases = [case for case in cases if 'test:' + case.name == case_id]
        if not cases:
            raise ManifestError(f"test case '{case_id}' matched no cases")
    if filter_text is not None and not cases:
        raise ManifestError(f"test filter '{filter_text}' matched no cases")
    if len(cases) > MAX_TEST_CASES:
        raise ManifestError(f'test selection exceeds {MAX_TEST_CASES} cases')
    lock = resolver.lock_path()
    lock_sha = hashlib.sha256(lock.read_bytes()).hexdigest() if lock.is_file() else None
    entries = []
    for case in cases:
        entries.append({'id': 'test:' + case.name, 'name': case.name, 'entry': case.entry,
                        'entry_sha256': hashlib.sha256((manifest.root / case.entry).read_bytes()).hexdigest(),
                        'timeout_ms': case.timeout_ms})
    snapshot = {'manifest_sha256': manifest.raw_sha256, 'lock_sha256': lock_sha,
                'toolchain': WYSNA_VERSION, 'cases': entries}
    canonical = json.dumps(snapshot, ensure_ascii=False, separators=(',', ':'))
    plan = {'schema_version': 'wysna.project-test-plan/1', 'project': manifest.name,
            **snapshot, 'selected': len(cases),
            'plan_hash': hashlib.sha256(canonical.encode('utf-8')).hexdigest()}
    return cases, plan


def capture_test(command, root, timeout_ms):
    """Bound child streams and clean up the process tree and fixture on every exit."""
    from runtime.HostAbi import attach_process_tree, release_process_tree, terminate_process_tree
    captured = {'stdout': bytearray(), 'stderr': bytearray()}
    limited = threading.Event()
    process = None
    readers = []
    child_report = {}
    status = 'failed'
    previous_handlers = {}

    def cancel(signum, frame):
        raise KeyboardInterrupt

    def drain(stream, name):
        try:
            while True:
                chunk = stream.read(8192)
                if not chunk:
                    break
                remaining = MAX_CAPTURE_BYTES - len(captured[name])
                captured[name].extend(chunk[:remaining])
                if len(chunk) > remaining:
                    limited.set()
                    terminate_process_tree(process, force_group=True)
        finally:
            stream.close()

    with tempfile.TemporaryDirectory(prefix='wysna-test-fixture-') as fixture:
        run_report = Path(fixture) / 'run-report.json'
        environment = {**os.environ, 'WYSNA_TEST_TMPDIR': fixture}
        if threading.current_thread() is threading.main_thread():
            for kind in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[kind] = signal.signal(kind, cancel)
        deadline = time.perf_counter() + timeout_ms / 1000
        try:
            process = subprocess.Popen(
                [*command, '--report-json', str(run_report)], cwd=root, env=environment,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=(os.name != 'nt'),
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0))
            attach_process_tree(process)
            readers = [threading.Thread(target=drain, args=(process.stdout, 'stdout'), daemon=True),
                       threading.Thread(target=drain, args=(process.stderr, 'stderr'), daemon=True)]
            for reader in readers:
                reader.start()
            process.wait(timeout=max(0.0, deadline - time.perf_counter()))
            status = 'passed' if process.returncode == 0 else 'failed'
        except subprocess.TimeoutExpired:
            status = 'timed_out'
        except KeyboardInterrupt:
            status = 'cancelled'
        finally:
            # A second cancellation must not interrupt process/fixture cleanup.
            for kind in previous_handlers:
                signal.signal(kind, signal.SIG_IGN)
            try:
                if process is not None:
                    terminate_process_tree(process, force_group=True)
                    process.wait()
                    release_process_tree(process)
                for reader in readers:
                    reader.join()
                if run_report.is_file():
                    try:
                        child_report = json.loads(run_report.read_text(encoding='utf-8'))
                    except (ValueError, OSError):
                        pass
            finally:
                for kind, handler in previous_handlers.items():
                    signal.signal(kind, handler)
    if limited.is_set() and status != 'cancelled':
        status = 'output_limited'
    exit_code = process.returncode if status in ('passed', 'failed') else None
    return status, exit_code, bytes(captured['stdout']), bytes(captured['stderr']), child_report


def test_failure_kind(status, child_report):
    if status == 'passed':
        return None
    if status != 'failed':
        return status
    if child_report.get('status') in ('toolchain_error', 'internal_error'):
        return 'infrastructure_error'
    diagnostics = child_report.get('diagnostics', [])
    if any(item.get('code', '').startswith(('W0', 'W1', 'W2', 'W3', 'W5')) for item in diagnostics):
        return 'compile_error'
    return 'test_failure'


def xml_text(value):
    return ''.join(character if (character in '\t\n\r' or 0x20 <= ord(character) <= 0xD7FF
                                 or 0xE000 <= ord(character) <= 0xFFFD
                                 or 0x10000 <= ord(character) <= 0x10FFFF) else '\uFFFD'
                   for character in value)


def junit_report(report):
    suite = ET.Element('testsuite', name=report['project'], tests=str(len(report['cases'])),
                       failures=str(report['failed']), time=str(report['duration_ms'] / 1000))
    for record in report['cases']:
        case = ET.SubElement(suite, 'testcase', name=xml_text(record['name']),
                             classname=xml_text(report['project']), file=xml_text(record['entry']),
                             time=str(record['duration_ms'] / 1000))
        if record['status'] != 'passed':
            failure = ET.SubElement(case, 'failure', type=record['status'],
                                    message=xml_text(record.get('failure_kind') or record['status']))
            failure.text = xml_text(record.get('stderr', ''))
        ET.SubElement(case, 'system-out').text = xml_text(record.get('stdout', ''))
        ET.SubElement(case, 'system-err').text = xml_text(record.get('stderr', ''))
    return ET.tostring(suite, encoding='unicode', xml_declaration=True) + '\n'


def write_junit(report, destination):
    path = Path(destination).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent,
                                     prefix='.' + path.name, delete=False) as output:
        temporary = Path(output.name)
        try:
            output.write(junit_report(report))
            output.flush()
            output.close()
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
