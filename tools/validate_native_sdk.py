#!/usr/bin/env python3
"""Validate an extracted native SDK outside the checkout with an isolated tool PATH."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile
import time


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def isolated_environment(work):
    environment = {name.upper(): value for name, value in os.environ.items()}
    for name in ('CC', 'CXX', 'PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'INCLUDE', 'LIB',
                 'LIBPATH', 'LIBRARY_PATH', 'CPATH', 'C_INCLUDE_PATH', 'CPLUS_INCLUDE_PATH',
                 'VCINSTALLDIR', 'VCTOOLSINSTALLDIR', 'WINDOWSSDKDIR', 'WINDOWSSDKVERSION',
                 'SDKROOT', 'DEVELOPER_DIR', 'ZIG_LOCAL_CACHE_DIR', 'ZIG_GLOBAL_CACHE_DIR'):
        environment.pop(name, None)
    environment['PATH'] = str(Path(environment['SYSTEMROOT']) / 'System32') if os.name == 'nt' else ''
    environment['WYSNA_CACHE_HOME'] = str(work / 'compiler cache á')
    temporary = work / 'temporary á'
    temporary.mkdir()
    environment.update(TMPDIR=str(temporary), TMP=str(temporary), TEMP=str(temporary))
    return environment


def terminal_bytes(value):
    """Expected C text-stream bytes, preserving embedded NUL and platform newlines."""
    return value.replace(b'\n', b'\r\n') if os.name == 'nt' else value


def require(condition, detail):
    if not condition:
        raise AssertionError(detail)


def validate(sdk, report, strict_source=False, compiler_source=None):
    sdk = Path(sdk).resolve()
    manifest = json.loads((sdk / 'native-sdk.json').read_text(encoding='utf-8'))
    require(manifest['profile'] == 'native-core-v1', 'Unexpected compiler profile')
    require(len(manifest['native_c_sha256']) == 3 and len(set(manifest['native_c_sha256'])) == 1,
            'Native fixed point is missing')
    require(not strict_source or manifest['source_dirty'] is False, 'CI package source was dirty')
    require(manifest['target'] == manifest['components']['zig']['target'], 'Target provenance mismatch')
    for name, expected in manifest['files'].items():
        path = sdk / name
        require(path.is_relative_to(sdk) and '..' not in Path(name).parts, 'Invalid manifest path: ' + name)
        require(path.is_file() and digest(path) == expected, 'File checksum mismatch: ' + name)
    require(not list(sdk.rglob('*.py')) and not (sdk / 'checker').exists(), 'Seed/Python sources were packaged')
    report.update(platform=manifest['platform'], target=manifest['target'],
                  source_commit=manifest['source_commit'], fingerprint=manifest['compiler_fingerprint'])
    checks = report['checks']
    checks.append({'name': 'manifest-and-files', 'files': len(manifest['files'])})
    with tempfile.TemporaryDirectory(prefix='wysna installed SDK á ') as temporary:
        work = Path(temporary).resolve()
        installed = work / 'installation á with spaces'
        shutil.copytree(sdk, installed)
        suffix = '.exe' if os.name == 'nt' else ''
        cli = installed / ('bin/wysna' + suffix)
        project = work / 'project á with spaces'
        project.mkdir()
        environment = isolated_environment(work)
        source = project / 'hello á & spaces.wysna'
        source.write_text('import "std/text" as text\nimport "helper.wysna" as helper\n'
                          'print(text.join(["hello", helper.label()], " "))\n', encoding='utf-8')
        (project / 'helper.wysna').write_text('pub fn label() -> str { "Wysna á" }\n', encoding='utf-8')
        (project / 'stdlib').mkdir()
        (project / 'stdlib/text.wysna').write_text('not valid Wysna', encoding='utf-8')
        (project / 'runtime').mkdir()
        (project / 'runtime/wysna_runtime.h').write_text('#error Wrong runtime selected\n', encoding='utf-8')

        def run(name, arguments, expected=0, stdout=None, error=None, custom=None, timeout=180, stdin=None):
            result = subprocess.run([str(cli)] + [str(value) for value in arguments], cwd=project,
                                    env=custom or environment, capture_output=True, timeout=timeout, input=stdin)
            require(result.returncode == expected, (name, result.returncode,
                    result.stdout.decode('utf-8', 'replace')[-4000:], result.stderr.decode('utf-8', 'replace')[-4000:]))
            if stdout is not None:
                require(result.stdout == terminal_bytes(stdout), (name, result.stdout, terminal_bytes(stdout)))
            if error:
                require(error.encode() in result.stderr, (name, result.stderr))
            checks.append({'name': name, 'status': result.returncode})
            return result

        run('version', ['--version'], stdout=('Wysna ' + manifest['version'] + ' native SDK\n').encode())
        run('help', ['--help'])
        run('quiet-check-with-installed-stdlib', ['check', source], stdout=b'')
        mir = project / 'hello.mir.json'
        run('emit-mir', ['emit-mir', source, '-o', mir], stdout=b'')
        require(json.loads(mir.read_text(encoding='utf-8'))['module']['functions'], 'No MIR functions')
        generated = project / 'hello.c'
        run('emit-c', ['emit-c', source, '-o', generated], stdout=b'')
        require('#include "runtime/wysna_runtime.h"' in generated.read_text(), 'Missing runtime include')
        if os.name != 'nt':
            literal_path = project / r'literal\name.c'
            run('literal-backslash-output', ['emit-c', source, '-o', literal_path], stdout=b'')
            require(literal_path.read_bytes() == generated.read_bytes(), 'POSIX filename was treated as a separator')
        output = project / ('application á' + suffix)
        run('build-without-host-tools', ['build', source, '-o', output], stdout=b'')
        result = subprocess.run([str(output)], cwd=project, env=environment, capture_output=True, timeout=10)
        require(result.returncode == 0 and result.stdout == terminal_bytes('hello Wysna á\n'.encode()), result)
        checks.append({'name': 'independent-application', 'status': result.returncode})
        run('run-without-host-tools', ['run', source], stdout='hello Wysna á\n'.encode())

        adapter = project / 'adapter á.c'
        adapter.write_text('#include <stdint.h>\n#include <stdio.h>\n'
                           'int64_t wysna_sdk_double(int32_t value) { return (int64_t)value * 2; }\n'
                           'int64_t wysna_sdk_input(void) { return (int64_t)getchar(); }\n', encoding='utf-8')
        foreign = project / 'foreign.wysna'
        foreign.write_text('extern "C" fn wysna_sdk_double(value: int32) -> int64\n'
                           'extern "C" fn wysna_sdk_input() -> int64\n'
                           'print(wysna_sdk_double(21), wysna_sdk_input())\n', encoding='utf-8')
        run('external-C-adapter-widths-and-stdin', ['run', foreign, '--link', adapter],
            stdout=b'42 65\n', stdin=b'A')

        semantics = project / 'semantics.wysna'
        semantics.write_text('struct Box[T] { value: T }\n'
                             'struct Box__0 { label: str }\n'
                             'fn twice[T](value: T) -> T { value + value }\n'
                             'let mut values = [1, 2]\nlet snapshot = values\nvalues.append(3)\n'
                             'let offset = 4\nlet work = |value: int| => twice(value) + offset\n'
                             'let box: Box[int] = Box { value: work(3) }\n'
                             'let ordinary = Box__0 { label: "distinct" }\n'
                             'print(box.value, ordinary.label, snapshot, values)\n', encoding='utf-8')
        run('generic-closure-cow-identities', ['run', semantics], stdout=b'10 distinct [1, 2] [1, 2, 3]\n')
        strings = project / 'strings.wysna'
        strings.write_text('let value = "á\\0界"\nprint(len(value), value)\n', encoding='utf-8')
        run('unicode-and-nul', ['run', strings], stdout='3 á\0界\n'.encode())
        arguments = project / 'arguments.wysna'
        arguments.write_text('let values = args()\nfor index in 1..len(values) { print(values[index]) }\n'
                             'write_file("application cwd.txt", "cwd preserved") else { exit(8) }\n'
                             'eprint("stderr preserved")\nexit(7)\n', encoding='utf-8')
        payload = ['two words', 'quote"inside', 'slash\\tail\\', '$(touch sentinel)', 'á界', '']
        result = run('argv-streams-cwd-exit', ['run', arguments, '--'] + payload,
                     expected=7, stdout=('\n'.join(payload) + '\n').encode())
        require(result.stderr == terminal_bytes(b'stderr preserved\n') and not (project / 'sentinel').exists(), result)
        require((project / 'application cwd.txt').read_text() == 'cwd preserved', 'CWD changed')

        panic = project / 'panic.wysna'
        panic.write_text('let values = [1]\nprint(values[2])\n', encoding='utf-8')
        for profile in ('dev', 'release', 'size'):
            run('checked-index-' + profile, ['run', panic, '--profile', profile], expected=1, error='W4002')
        invalid = project / 'invalid.wysna'
        invalid.write_text('let value: int = "wrong"\n', encoding='utf-8')
        saved = output.read_bytes()
        run('preserve-output-on-source-error', ['build', invalid, '-o', output], expected=1, error='W2001')
        require(output.read_bytes() == saved, 'Failed build overwrote existing output')
        broken = project / 'broken.c'
        broken.write_text('#error deliberate C compilation failure\n', encoding='utf-8')
        result = subprocess.run([str(cli), 'build', str(source), '-o', str(output), '--link', str(broken)],
                                cwd=project, env=environment, capture_output=True, timeout=180)
        require(result.returncode != 0 and output.read_bytes() == saved, 'Failed C build published output')
        checks.append({'name': 'preserve-output-on-C-error', 'status': result.returncode})
        run('reject-source-output', ['emit-c', source, '-o', source], expected=2, error='W5002')
        hardlink = project / 'source alias.txt'
        os.link(source, hardlink)
        run('reject-hardlink-output', ['emit-c', source, '-o', hardlink], expected=2, error='W5002')
        for arguments in (['check', source, '--profile', 'dev'], ['build', source, '--timeout-ms', '1'],
                          ['run', source, '--timeout-ms', '0'], ['run', source, '--profile', 'release', '--profile', 'dev']):
            run('reject-invalid-options', arguments, expected=2, error='W5002')

        text = installed / 'stdlib/text.wysna'
        backup = text.with_suffix('.hidden')
        text.rename(backup)
        try:
            run('no-CWD-stdlib-fallback', ['check', source], expected=1, error='W1003')
        finally:
            backup.rename(text)
        engine = installed / ('libexec/wysna-compiler' + suffix)
        hidden = engine.with_suffix('.hidden')
        engine.rename(hidden)
        try:
            run('missing-engine-no-fallback', ['check', source], expected=1, error='W5001')
        finally:
            hidden.rename(engine)
        zig = installed / ('toolchain/zig/zig' + suffix)
        hidden = zig.with_suffix('.hidden')
        zig.rename(hidden)
        try:
            run('missing-toolchain-no-fallback', ['build', source], expected=1, error='W5001')
        finally:
            hidden.rename(zig)

        looping = project / 'loop.wysna'
        looping.write_text('write_file("started.txt", "started") else { exit(9) }\nwhile true {}\n', encoding='utf-8')
        run('bounded-application-timeout', ['run', looping, '--timeout-ms', '100'], expected=1, error='W5001')
        if os.name != 'nt':
            for interruption in (signal.SIGINT, signal.SIGTERM):
                (project / 'started.txt').unlink(missing_ok=True)
                child = subprocess.Popen([str(cli), 'run', str(looping)], cwd=project, env=environment,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                try:
                    deadline = time.monotonic() + 180
                    while not (project / 'started.txt').exists() and child.poll() is None and time.monotonic() < deadline:
                        time.sleep(0.05)
                    require((project / 'started.txt').exists(), 'Application did not start for cancellation test')
                    child.send_signal(interruption)
                    stdout, stderr = child.communicate(timeout=10)
                    require(child.returncode == 128 + interruption, (child.returncode, stdout, stderr))
                    checks.append({'name': 'cancel-tree-' + interruption.name, 'status': child.returncode})
                finally:
                    if child.poll() is None:
                        child.kill()
                        child.communicate(timeout=10)
            alias = project / 'source symlink.txt'
            alias.symlink_to(source)
            run('reject-symlink-output', ['emit-c', source, '-o', alias], expected=2, error='W5002')
            imported_alias = project / 'helper alias.wysna'
            imported_alias.symlink_to(project / 'helper.wysna')
            aliases = project / 'aliases.wysna'
            aliases.write_text('import "helper.wysna" as first\nimport "helper alias.wysna" as second\n'
                               'print(first.label(), second.label())\n', encoding='utf-8')
            run('canonical-module-identity', ['run', aliases], stdout='Wysna á Wysna á\n'.encode())
            modes = {path: path.stat().st_mode for path in installed.rglob('*')}
            modes[installed] = installed.stat().st_mode
            try:
                for path, mode in modes.items():
                    path.chmod(mode & ~0o222)
                run('read-only-relocatable-installation', ['run', source], stdout='hello Wysna á\n'.encode())
            finally:
                for path, mode in modes.items():
                    path.chmod(mode)
        require(not list(project.glob('.wysna-*')), 'Output staging directories leaked')
        require(not list((work / 'temporary á').iterdir()), 'Temporary compilation artifacts leaked')
        checks.append({'name': 'cleanup-after-success-error-timeout-cancellation', 'status': 0})
        if compiler_source:
            require(digest(Path(compiler_source)) == manifest["compiler_source_sha256"], "Selfbuild source does not match SDK provenance")
            fixture = project / 'compiler.wysna'
            shutil.copy2(compiler_source, fixture)
            rebuilt = project / ('rebuilt-compiler' + suffix)
            run('selfbuild-without-Python', ['build', fixture, '-o', rebuilt, '--profile', 'dev'], timeout=300)
            target = project / 'rebuilt.c'
            result = subprocess.run([str(rebuilt), '--stdlib-root', str(installed / 'stdlib'), '--emit-c-mir',
                                     str(source), str(target)], cwd=project, env=environment,
                                    capture_output=True, timeout=120)
            require(result.returncode == 0 and target.read_bytes() == generated.read_bytes(), result)
            checks.append({'name': 'rebuilt-native-engine-emits-identical-C', 'status': result.returncode})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--strict-source', action='store_true')
    parser.add_argument('--compiler-source', type=Path)
    options = parser.parse_args()
    report = {'checks': [], 'success': False}
    try:
        validate(options.sdk, report, options.strict_source, options.compiler_source)
        report['success'] = True
    except Exception as error:
        report['error'] = str(error)
        raise
    finally:
        options.report.parent.mkdir(parents=True, exist_ok=True)
        options.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
        print(json.dumps({'success': report['success'], 'checks': len(report['checks']), 'report': str(options.report)}))


if __name__ == '__main__':
    main()
