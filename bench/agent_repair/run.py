"""Exercise diagnosed repairs against two installed SDKs with no model calls."""
import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import statistics
import subprocess
import tempfile
import time

REPO = Path(__file__).resolve().parents[2]
JSON = '--output-format=json-v1'
SUPPORT = 'import "pkg/repair-policy/policy" as repair_policy\n'
AUTOMATION_HELPER = SUPPORT + '''pub fn payload(values: list[str]) -> list[str] {
    slice(values, repair_policy.offset(), len(values))
}
'''
SUPPORT_CODE = 'pub fn offset() -> int { 2 }\npub fn risk_points() -> int { 30 }\n'


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def write(path, source):
    path.write_bytes(source.encode('utf-8'))


def read(path):
    return path.read_bytes().decode('utf-8')


class Pilot:
    def __init__(self, sdk, work):
        self.sdk = sdk.resolve()
        self.work = work
        self.env = {key: os.environ[key] for key in ('SYSTEMROOT', 'WINDIR') if key in os.environ}
        tmp = work / 'tmp'
        tmp.mkdir()
        self.env.update(PATH='', TMPDIR=str(tmp), TMP=str(tmp), TEMP=str(tmp),
                        WYSNA_CACHE_HOME=str(work.parent / f'cache-{sdk.parent.parent.name}'))
        self.calls = 0
        self.latency = {}

    def command(self, name, cwd, *args, expected=0, executable=None):
        started = time.perf_counter()
        result = subprocess.run([str(executable or self.sdk), *map(str, args)], cwd=cwd,
                                env=self.env, capture_output=True, timeout=120)
        if name:
            self.calls += 1
            self.latency[name] = round((time.perf_counter() - started) * 1000, 3)
        require(result.returncode == expected,
                (args, result.returncode, result.stdout[-3000:], result.stderr[-1500:]))
        return result.stdout

    def json(self, name, cwd, *args, expected=0):
        return json.loads(self.command(name, cwd, *args, JSON, expected=expected))

    def restore(self, app):
        self.command(None, self.work, 'project', 'new', 'policy')
        dependency = self.work / 'policy'
        write(dependency / 'src/policy.wysna', SUPPORT_CODE)
        archive = self.work / 'policy.tar'
        cache = self.work / 'package-cache'
        self.command(None, dependency, 'packages', 'pack', '-o', archive)
        pin = hashlib.sha256(archive.read_bytes()).hexdigest()
        self.command(None, app, 'packages', 'fetch', '--archive', archive, '--sha256', pin, '--cache', cache)
        self.command(None, app, 'packages', 'add', 'repair-policy', '--path', 'vendor/' + pin)
        self.command(None, app, 'project', 'lock')
        locked = (app / 'wysna.lock').read_bytes()
        shutil.rmtree(app / 'vendor')
        archive.unlink()
        shutil.rmtree(dependency)
        self.command('offline_restore', app, 'packages', 'restore', '--cache', cache)
        require((app / 'wysna.lock').read_bytes() == locked, 'Frozen lock changed during restore')

    def repair(self, app, bad_edits, good_edits, use_span, target):
        diagnosis = self.json('diagnose', app, 'project', 'check', '--target', target, '--frozen', expected=1)
        diagnostic = next(item for item in diagnosis['diagnostics'] if item['code'] == 'W2005')
        callee = next(item['span'] for item in diagnostic['related'] if item['span'])
        context_path = Path(callee['source_id'])
        require(context_path.resolve().is_relative_to(app), callee)
        if use_span:
            result = self.json('context', app, 'inspect', context_path, '--span', json.dumps(callee),
                               '--max-bytes', '256')['result']
            context_bytes = len(result['source'].encode('utf-8'))
            require(result['span'] == callee and context_bytes <= 256, result)
        else:
            started = time.perf_counter()
            context_bytes = len(context_path.read_bytes())
            self.latency['context'] = round((time.perf_counter() - started) * 1000, 3)
        self.context_bytes = context_bytes
        originals = {edit['path']: (app / edit['path']).read_bytes() for edit in good_edits}
        edits_path = self.work / 'edits.json'
        opts = ('patch', '--manifest', 'wysna.toml', '--edits', edits_path, '--frozen', '--test', '--discover')
        write(edits_path, json.dumps(bad_edits))
        rejected = self.json('reject_regression', app, *opts, expected=1)['result']
        require(not rejected['written'] and rejected['test_verification']['status'] == 'failed', rejected)
        require(all((app / path).read_bytes() == content for path, content in originals.items()), originals)
        write(edits_path, json.dumps(good_edits))
        checked = self.json('preview', app, *opts)['result']
        require(not checked['written'] and checked['test_verification']['status'] == 'passed', checked)
        applied = self.json('apply', app, *opts, '--apply', '--expect-plan-hash', checked['plan_hash'])['result']
        verification = applied['test_verification']
        require(applied['written'] and verification['plan_hash'] == checked['plan_hash'], applied)
        require(all((app / edit['path']).read_bytes() == edit['source'].encode('utf-8') for edit in good_edits),
                'Successful application did not publish both edits')
        self.phases = {}
        for phase, payload in (('reject', rejected), ('preview', checked), ('apply', applied)):
            evidence = payload['test_verification']
            timings = dict(payload.get('test_timing_ms', {}))
            case_timings = timings.pop('cases', [])
            timings['case_total'] = sum(case['duration_ms'] for case in evidence['cases'])
            for field in ('compile_ms', 'run_ms'):
                if case_timings:
                    timings[field] = sum(case[field] for case in case_timings)
            self.phases[phase] = timings


def automation(pilot, app, use_span):
    call = 'slice(arguments, 2, len(arguments))'
    main = 'import "launch_args.wysna" as launch_args\n' + read(app / 'src/main.wysna')
    good_main = main.replace(call, 'launch_args.payload(arguments)')
    write(app / 'src/main.wysna', main.replace(call, 'launch_args.payload(arguments, 2)'))
    write(app / 'src/launch_args.wysna', AUTOMATION_HELPER.replace('offset()', 'offset() - 1'))
    write(app / 'tests/test_launch_args.wysna', '''import "launch_args.wysna" as launch_args
if launch_args.payload(["runner", "child", "first"]) != ["first"] { exit(1) }
''')
    edits = [{'path': 'src/main.wysna', 'source': good_main},
             {'path': 'src/launch_args.wysna', 'source': AUTOMATION_HELPER}]
    bad = [edits[0], dict(edits[1], source=AUTOMATION_HELPER.replace('offset()', 'offset() - 2'))]
    pilot.repair(app, bad, edits, use_span, 'app')
    output = pilot.command('exercise', app, 'project', 'run', '--frozen', '--', pilot.sdk, 'skills', 'syntax')
    require(b'Syntax Quick Reference' in output, output[-1000:])


def sqlite(pilot, app, use_span, include_dir):
    call = 'audit.compute_risk_points(ev.status_code, ev.latency_ms)'
    cli = read(app / 'src/sqlite_cli.wysna')
    require(cli.count(call) == 1, 'SQLite call fixture changed')
    write(app / 'src/sqlite_cli.wysna', cli.replace(call, 'audit.compute_risk_points(ev.status_code)'))
    audit_source = read(app / 'src/audit.wysna')
    require(audit_source.count('points += 30') == 1, 'SQLite risk calculation fixture changed')
    audit = SUPPORT + audit_source.replace('points += 30', 'points += repair_policy.risk_points()')
    write(app / 'src/audit.wysna', audit.replace('points += repair_policy.risk_points()',
                                               'points += repair_policy.risk_points() + 1'))
    good_cli = cli.replace(call, 'audit.compute_risk_points(ev.status_code, ev.latency_ms + 0)')
    edits = [{'path': 'src/sqlite_cli.wysna', 'source': good_cli}, {'path': 'src/audit.wysna', 'source': audit}]
    bad = [edits[0], dict(edits[1], source=audit.replace('points += repair_policy.risk_points()',
                                                     'points += repair_policy.risk_points() + 2'))]
    pilot.repair(app, bad, edits, use_span, 'sqlite-audit')
    db = pilot.work / 'audit.db'
    output = pilot.work / ('audit.exe' if os.name == 'nt' else 'audit')
    library = ['--link', app / 'native/ops_sqlite.c', '--library', 'sqlite3']
    if include_dir:
        library += ['--include-dir', include_dir]
    pilot.command('build_sqlite', app, 'project', 'build', '--target', 'sqlite-audit', '--frozen', '-o', output, *library)
    pilot.env['WYSNA_OPS_DB'] = str(db)
    transcript = pilot.command('exercise', app, executable=output)
    require(b'sqlite_total=3 critical=1' in transcript, transcript)
    with closing(sqlite3.connect(db)) as connection:
        require(connection.execute('SELECT count(*) FROM audit_events').fetchone() == (3,), 'Expected 3 SQLite rows')


def late_slice(sdk, work):
    pilot = Pilot(sdk, work)
    file = work / 'context.wysna'
    prefix = 'pub fn detail() -> int {\n    # '
    write(file, prefix + 'á🦙' * 18000 + '\n    42\n}\n')
    anchor = pilot.json(None, work, 'inspect', file, '--symbol', 'detail', '--max-bytes', '1')['result']
    offset = len(prefix.encode()) + 6 * 17000 - anchor['node']['start_byte']
    body = pilot.json('inspect_late_utf8', work, 'inspect', file, '--symbol', 'detail',
                      '--source-offset', str(offset), '--max-bytes', '192')['result']
    require(body['source'].encode() == 'á🦙'.encode() * 32, body['budget'])
    return pilot.latency['inspect_late_utf8']


def run_trial(sdk, work, example, pilot_fn, use_span, include_dir):
    work.mkdir()
    pilot = Pilot(sdk, work)
    app = work / example
    shutil.copytree(REPO / 'examples' / example, app)
    pilot.restore(app)
    if pilot_fn is sqlite:
        pilot_fn(pilot, app, use_span, include_dir)
    else:
        pilot_fn(pilot, app, use_span)
    repair_steps = ('diagnose', 'context', 'reject_regression', 'preview', 'apply')
    return {'calls': pilot.calls, 'context_reads': 1, 'repair_attempts': 2,
            'context_bytes': pilot.context_bytes, 'calls_ms': pilot.latency, 'patch_phases_ms': pilot.phases,
            'repair_ms': round(sum(pilot.latency[step] for step in repair_steps), 3)}


def sdk_identity(executable):
    root = executable.resolve().parent.parent
    manifest = json.loads((root / 'native-sdk.json').read_text(encoding='utf-8'))
    return {key: manifest[key] for key in ('source_commit', 'compiler_fingerprint', 'version', 'target')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline-sdk', type=Path, required=True, help='Path to baseline bin/wysna')
    parser.add_argument('--candidate-sdk', type=Path, required=True, help='Path to candidate bin/wysna')
    parser.add_argument('--sqlite-include-dir', type=Path, help='Directory containing sqlite3.h')
    parser.add_argument('--repeats', type=int, default=3)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('--repeats must be positive')
    report = {'schema_version': 'wysna.agent-repair-benchmark/1',
              'platform': platform.system() + ' ' + platform.machine(),
              'repeats': args.repeats,
              'sdks': {'baseline': sdk_identity(args.baseline_sdk), 'candidate': sdk_identity(args.candidate_sdk)},
              'pilots': {}, 'late_utf8_inspect': {}}
    with tempfile.TemporaryDirectory(prefix='wysna-repair-bench-') as stage:
        root = Path(stage).resolve()
        for example, function in (('automation', automation), ('wysofia-ops', sqlite)):
            rows = report['pilots'][example] = {'baseline': [], 'candidate': []}
            for index in range(args.repeats + 1):
                for label, sdk in (('baseline', args.baseline_sdk), ('candidate', args.candidate_sdk)):
                    row = run_trial(sdk, root / f'{example}-{label}-{index}', example, function,
                                    label == 'candidate', args.sqlite_include_dir)
                    if index:
                        rows[label].append(row)
        for label, sdk in (('baseline', args.baseline_sdk), ('candidate', args.candidate_sdk)):
            values = []
            for index in range(args.repeats + 1):
                work = root / f'slice-{label}-{index}'
                work.mkdir()
                sample = late_slice(sdk, work)
                if index:
                    values.append(sample)
            report['late_utf8_inspect'][label] = {'samples_ms': values, 'median_ms': statistics.median(values)}
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(args.output)


if __name__ == '__main__':
    main()
