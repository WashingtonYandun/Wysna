"""Bounded, observable execution of the four compiler engines."""
import hashlib
import json
import os
from pathlib import Path
import re
import shlex
import signal
import subprocess
import sys
from common.Platform import compiler_command, executable_name
from error.Error import DIAGNOSTIC_CODES

ROOT = Path(__file__).resolve().parents[1]
ENGINES = ('interpreter', 'ast', 'hir', 'selfhost')
PROFILES = json.loads((ROOT / 'tests/engine_profiles.json').read_text())
EXPANSION_DIRS = {'regressions', 'functions', 'ffi', 'stdlib', 'generic_oop'}


# A fixture whose .expected file starts with this marker is a *panic* case: every engine
# must report the same diagnostic code and terminate non-zero instead of producing output.
PANIC_MARKER = '# RUNTIME_PANIC:'


def panic_expectation(expected_text):
    """Returns the diagnostic code a panic fixture must produce, or None."""
    if not expected_text:
        return None
    first = expected_text.strip().splitlines()[0].strip() if expected_text.strip() else ''
    if first.startswith(PANIC_MARKER):
        return first[len(PANIC_MARKER):].strip()
    return None

def corpus_paths():
    """Phase D public corpus. Expansion fixtures live under dedicated directories."""
    positives = sorted((ROOT / 'tests/runtime').glob('*.wysna'))
    positives += sorted((ROOT / 'examples').glob('*.wysna'))
    positives += sorted((ROOT / 'tests/modules').glob('*/main.wysna'))
    for directory in ('functions', 'stdlib', 'regressions'):
        positives += sorted(path for path in (ROOT / 'tests' / directory).rglob('*.wysna')
                            if path.with_suffix('.expected').is_file())
    support_files = PROFILES.get('support_files', {})
    positives = [path for path in positives if path.relative_to(ROOT).as_posix() not in support_files]
    negatives = sorted((ROOT / 'tests').glob('*/invalid/*.wysna'))
    negatives += sorted((ROOT / 'tests/errors').glob('invalid_*.wysna'))
    negatives = [path for path in negatives if 'ffi' not in path.parts]
    return positives, negatives


def feature_for(label):
    return PROFILES['fixtures'].get(label.replace(os.sep, '/'), 'core')


def unsupported_reason(label, engine):
    feature = feature_for(label)
    return PROFILES.get('features', {}).get(feature, {}).get(engine, '')


class EngineResult:
    def __init__(self, engine, stdout='', stderr='', exit_code=0, compile_ok=True,
                 runtime_ok=True, diag_code='', infrastructure_error=''):
        self.engine = engine
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.compile_ok = compile_ok
        self.runtime_ok = runtime_ok
        self.diag_code = diag_code
        self.infrastructure_error = infrastructure_error


def asdict(result):
    return vars(result).copy()


def diagnostic(stderr):
    match = re.search(r'^\[(W\d+)\]', stderr, re.MULTILINE)
    return match.group(1) if match and match.group(1) in DIAGNOSTIC_CODES else ''


def execute(command, directory, phase, timeout, env=None):
    """Persist commands and output, including timeout and signal failures."""
    try:
        process = subprocess.Popen(command, cwd=ROOT, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, text=True, env=env,
                                   start_new_session=(os.name == 'posix'))
        try:
            stdout, stderr = process.communicate(timeout=timeout)
            record = {'command': command, 'returncode': process.returncode,
                      'stdout': stdout, 'stderr': stderr}
        except subprocess.TimeoutExpired:
            # Compiler drivers and bootstrap helpers can spawn descendants.
            # Kill the whole group so inherited pipes cannot outlive the timeout.
            try:
                if os.name == 'posix':
                    os.killpg(process.pid, signal.SIGKILL)
                else:
                    process.kill()
            except ProcessLookupError:
                pass
            stdout, stderr = process.communicate()
            record = {'command': command, 'returncode': 124, 'timeout': timeout,
                      'termination_returncode': process.returncode,
                      'stdout': stdout, 'stderr': stderr}
    except OSError as error:
        record = {'command': command, 'returncode': 127, 'stdout': '', 'stderr': str(error),
                  'launch_error': True}
    (directory / (phase + '.json')).write_text(json.dumps(record, indent=2), encoding='utf-8')
    return record


def run_engine(engine, source, label, selfhost_bin, directory, sanitize=False, run_timeout=30,
               extra_compile_flags=(), extra_link_flags=()):
    directory = Path(directory) / engine
    directory.mkdir(parents=True, exist_ok=True)
    source_path = directory / 'source.wysna'
    source_path.write_text(source, encoding='utf-8')
    c_path, binary = directory / 'program.c', directory / executable_name('program')
    driver = [sys.executable, str(ROOT / 'main.py')]
    if engine == 'interpreter':
        command = driver + ['run', str(source_path)]
    elif engine == 'selfhost':
        # Direct invocation: no Seed parser/checker runs on the input first.
        command = [str(selfhost_bin), str(source_path), str(c_path)]
    elif engine == 'ast':
        command = driver + ['emit-c', '--backend=ast', str(source_path), '-o', str(c_path)]
    elif engine == 'hir':
        command = driver + ['emit-c', '--backend=hir', str(source_path), '-o', str(c_path)]
    front = execute(command, directory, 'frontend', max(120, run_timeout) if engine == 'interpreter' else 120)
    result = EngineResult(engine)
    failure = ('timeout' in front or 'launch_error' in front or front['returncode'] < 0)
    if engine == 'interpreter' or front['returncode'] != 0:
        result.stdout, result.stderr, result.exit_code = front['stdout'], front['stderr'], front['returncode']
        result.diag_code = diagnostic(result.stderr)
        result.compile_ok = result.exit_code == 0 or (engine == 'interpreter' and result.diag_code.startswith('W4'))
        result.runtime_ok = result.exit_code == 0
        if failure or result.diag_code == 'W9999' or (result.exit_code and not result.diag_code):
            result.infrastructure_error = 'frontend failed without a structured diagnostic'
        return result
    flags = ['-std=c99', '-O1', '-g', '-I' + str(ROOT)]
    if sanitize and os.name != 'nt':
        flags += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
    compile_command = (compiler_command() + flags + list(extra_compile_flags)
                       + [str(c_path), '-o', str(binary)] + list(extra_link_flags))
    compiled = execute(compile_command, directory, 'compile', 120)
    if compiled['returncode'] != 0:
        return EngineResult(engine, stderr=compiled['stderr'], exit_code=compiled['returncode'],
                            compile_ok=False, runtime_ok=False, infrastructure_error='C compilation failed')
    env = dict(os.environ, WYSNA_MEM_CHECK='1')
    if sanitize:
        env['ASAN_OPTIONS'] = 'detect_leaks=' + ('1' if sys.platform.startswith('linux') else '0')
        env['UBSAN_OPTIONS'] = 'halt_on_error=1:print_stacktrace=1'
    ran = execute([str(binary)], directory, 'run', run_timeout, env)
    result.stdout, result.stderr, result.exit_code = ran['stdout'], ran['stderr'], ran['returncode']
    result.diag_code = diagnostic(result.stderr)
    result.runtime_ok = result.exit_code == 0
    if result.exit_code and (not result.diag_code or result.exit_code < 0 or 'timeout' in ran):
        result.infrastructure_error = 'native execution failed without a structured diagnostic'
    if any(marker in result.stderr for marker in ('AddressSanitizer', 'LeakSanitizer', 'runtime error:', 'Memory leak detected')):
        result.infrastructure_error = 'memory or sanitizer failure'
    return result


def compare(results, expected=None):
    panic_code = panic_expectation(expected)
    for result in results:
        if result.infrastructure_error:
            return False, f'{result.engine}: {result.infrastructure_error}: {result.stderr}'
        if panic_code is not None:
            # Every engine must report the declared diagnostic and exit non-zero. The
            # panic text itself is allowed to differ (the native runtime has no source
            # position to print), so only the code and the failure are compared.
            if result.exit_code == 0:
                return False, f'{result.engine}: expected a runtime panic, but the program exited 0'
            if result.diag_code != panic_code:
                return False, (f'{result.engine}: expected panic {panic_code}, '
                               f'got {result.diag_code or "no diagnostic"}: {result.stderr!r}')
            continue
        if expected is not None and (result.exit_code != 0 or result.stdout != expected):
            return False, f'{result.engine}: expected successful execution and exact fixture output, got {asdict(result)}'
    if not results:
        return False, 'no supported engines executed'
    if panic_code is not None:
        codes = {result.diag_code for result in results}
        if codes != {panic_code}:
            return False, f'panic diagnostic mismatch across engines: {sorted(codes)}'
        return True, 'OK (panic)'
    reference = results[0]
    for result in results[1:]:
        for field in ('compile_ok', 'runtime_ok', 'exit_code', 'stdout', 'diag_code'):
            if getattr(reference, field) != getattr(result, field):
                return False, f'{field} mismatch: {asdict(reference)} versus {asdict(result)}'
    if reference.exit_code == 0 and any(result.stderr != reference.stderr for result in results[1:]):
        return False, 'stderr mismatch on successful execution'
    return True, 'OK'


def verify_case(source, label, selfhost_bin, work_dir, expected=None, sanitize=False, strict=False, expect_rejection=False, run_timeout=30):
    original = ROOT / label
    if original.is_file():
        def qualify_import(match):
            requested = match.group(1)
            if requested.startswith('std/'):
                return match.group(0)
            candidate = original.parent / requested
            if not candidate.is_file():
                candidate = ROOT / requested
            return 'import ' + json.dumps(str(candidate.resolve()), ensure_ascii=False)
        source = re.sub(r'import\s+"([^"\n]+)"', qualify_import, source)
    artifacts = Path(os.environ.get('WYSNA_CI_ARTIFACTS', ROOT / '.wysna_cache/ci-artifacts'))
    case_id = hashlib.sha256(label.encode()).hexdigest()[:12]
    directory = Path(work_dir) / case_id
    directory.mkdir(parents=True, exist_ok=True)
    results, unsupported = [], {}
    for engine in ENGINES:
        reason = unsupported_reason(label, engine)
        if reason:
            unsupported[engine] = reason
        else:
            results.append(run_engine(engine, source, label, selfhost_bin, directory, sanitize, run_timeout))
    ok, detail = compare(results, expected)
    if expect_rejection and panic_expectation(expected) is None:
        for result in results:
            if result.compile_ok or result.exit_code != 1 or not result.diag_code:
                ok, detail = False, f'{result.engine}: invalid fixture was not rejected by the frontend: {asdict(result)}'
    if strict and unsupported:
        ok, detail = False, 'release profile requires all engines: ' + str(unsupported)
    rows = []
    for result in results:
        row_ok, _ = compare([results[0], result], expected)
        if expect_rejection and panic_expectation(expected) is None and (
                result.compile_ok or result.exit_code != 1 or not result.diag_code):
            row_ok = False
        rows.append(dict(asdict(result), status='passed' if row_ok else 'failed'))
    report = {'label': label, 'feature': feature_for(label), 'sanitize': sanitize, 'status': 'passed' if ok else 'failed',
              'unsupported': unsupported, 'results': rows}
    artifacts.mkdir(parents=True, exist_ok=True)
    (artifacts / (case_id + '.json')).write_text(json.dumps(report, indent=2), encoding='utf-8')
    if unsupported:
        print(f'  [UNSUPPORTED] {label}: {unsupported}')
    if not ok:
        import shutil
        shutil.copytree(directory, artifacts / case_id, dirs_exist_ok=True)
    return ok, detail, report


def verify(*args, **kwargs):
    ok, detail, _ = verify_case(*args, **kwargs)
    return ok, detail
