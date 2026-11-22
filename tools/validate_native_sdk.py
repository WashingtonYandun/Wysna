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


def validate(sdk, report, strict_source=False, compiler_source=None, application=None, language_fixtures=None):
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
    report.update(platform=manifest['platform'], target=manifest['target'], version=manifest['version'],
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

        environment_source = project / 'environment.wysna'
        environment_source.write_text('import "std/environment" as environment\n'
                                      'let arguments = environment.arguments()\n'
                                      'let label = if len(arguments) > 1 { arguments[1] } else { "<no-arg>" }\n'
                                      'print(environment.get("WYSNA_NATIVE_SDK_TEST") ?? "<unset>", label)\n',
                                      encoding='utf-8')
        missing_environment = dict(environment)
        missing_environment.pop('WYSNA_NATIVE_SDK_TEST', None)
        run('environment-get-absent', ['run', environment_source, '--', 'cli-argument'],
            stdout=b'<unset> cli-argument\n', custom=missing_environment)
        run('environment-get-empty', ['run', environment_source, '--', 'cli-argument'], stdout=b' cli-argument\n',
            custom={**environment, 'WYSNA_NATIVE_SDK_TEST': ''})
        run('environment-get-utf8', ['run', environment_source, '--', 'CLI á界'],
            stdout='SDK á界 CLI á界\n'.encode('utf-8'),
            custom={**environment, 'WYSNA_NATIVE_SDK_TEST': 'SDK á界'})

        automation = project / 'automation.wysna'
        automation.write_text('import "std/environment" as environment\n'
                              'import "std/process" as process\n'
                              'import "std/time" as clock\n'
                              'let arguments = environment.arguments()\n'
                              'print("clock", clock.unix_seconds() > 0)\n'
                              'let status = process.run(arguments[1], ["--version"], 30000) else { exit(70) 0 }\n'
                              'exit(status)\n', encoding='utf-8')
        automation_environment = dict(environment, WYSNA_MEM_CHECK='1')
        result = run('automation-clock-and-process', ['run', automation, '--', cli],
                     custom=automation_environment)
        require(b'clock true' in result.stdout and
                ('Wysna ' + manifest['version'] + ' native SDK').encode() in result.stdout,
                ('automation output', result.stdout, result.stderr))

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

        library_directory = project / 'library directory á with spaces'
        library_directory.mkdir()
        library_source = library_directory / 'fixture.c'
        library_source.write_text('#include <stdint.h>\nint64_t wysna_sdk_library_value(void) { return 42; }\n',
                                  encoding='utf-8')
        library_object = library_directory / 'fixture.o'
        library_archive = library_directory / 'libwysna_sdk_fixture.a'
        zig = installed / ('toolchain/zig/zig' + suffix)
        zig_environment = dict(environment, ZIG_LOCAL_CACHE_DIR=str(work / 'fixture local cache'),
                               ZIG_GLOBAL_CACHE_DIR=str(work / 'fixture global cache'))
        compiled = subprocess.run([str(zig), 'cc', '-target', manifest['target'], '-c',
                                   str(library_source), '-o', str(library_object)], cwd=project,
                                  env=zig_environment, capture_output=True, timeout=180)
        require(compiled.returncode == 0, ('fixture object', compiled.stderr))
        archived = subprocess.run([str(zig), 'ar', 'rcs', str(library_archive), str(library_object)],
                                  cwd=project, env=zig_environment, capture_output=True, timeout=180)
        require(archived.returncode == 0, ('fixture archive', archived.stderr))
        library_program = project / 'library-consumer.wysna'
        library_program.write_text('extern "C" fn wysna_sdk_library_value() -> int64\n'
                                   'print(wysna_sdk_library_value())\n', encoding='utf-8')
        run('external-library-search-directory',
            ['run', library_program, '--library-dir', library_directory.name, '--library', 'wysna_sdk_fixture'],
            stdout=b'42\n')
        run('reject-missing-library-directory',
            ['build', library_program, '--library-dir', project / 'missing-library-directory'],
            expected=2, error='W5002')
        run('reject-file-as-library-directory', ['build', library_program, '--library-dir', library_source],
            expected=2, error='W5002')
        run('reject-library-directory-for-check', ['check', library_program, '--library-dir', library_directory],
            expected=2, error='W5002')
        too_many_library_dirs = ['build', library_program]
        for _ in range(17):
            too_many_library_dirs.extend(['--library-dir', library_directory])
        run('reject-excess-library-directories', too_many_library_dirs, expected=2, error='W5002')

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
        generic_methods = project / 'generic-methods.wysna'
        generic_methods.write_text('class Box[T] { value: T\n'
                                   'fn init(value: T) { this.value = value }\n'
                                   'fn get() -> T { this.value }\n'
                                   'fn set(value: T) { this.value = value } }\n'
                                   'let a: Box[int] = new Box[int](42)\n'
                                   'let b: Box[str] = new Box[str]("hello")\n'
                                   'print(a.get(), b.get())\n'
                                   'a.set(7)\nb.set("world")\nprint(a.get(), b.get())\n', encoding='utf-8')
        run('generic-class-methods', ['run', generic_methods], stdout=b'42 hello\n7 world\n')
        if language_fixtures:
            programs = project / 'language combinations'
            shutil.copytree(language_fixtures, programs)
            cases = [
                ('reexported-generic-errors', 'reexports/main.wysna', b'REEXPORT 10\n'),
                ('generic-method-errors', 'generic_methods.wysna', b'METHOD 42 7 2\n'),
                ('async-generic-errors', 'async_errors.wysna', b'ASYNC_OK 7\nASYNC_ERROR 2 second\n'),
                ('borrowed-host-paths', 'borrowed_paths.wysna', b'borrowed paths released\n'),
            ]
            memory_environment = dict(environment, WYSNA_MEM_CHECK='1')
            for name, relative, expected in cases:
                program = programs / relative
                run('check-' + name, ['check', program], stdout=b'')
                run(name, ['run', program], stdout=expected, custom=memory_environment)
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
        # A missing standard module must never be satisfied by a project-controlled
        # path, including the extensionless path used by the compiler's old fallback.
        project_std = project / 'std'
        project_std.mkdir()
        (project_std / 'ghost').write_text('pub fn marker() -> str { "shadowed" }\n', encoding='utf-8')
        shadow = project / 'shadow.wysna'
        shadow.write_text('import "std/ghost" as ghost\nprint(ghost.marker())\n', encoding='utf-8')
        run('reject-extensionless-CWD-stdlib-shadow', ['check', shadow], expected=1, error='W1003')
        escape = installed / 'escape.wysna'
        escape.write_text('pub fn marker() -> str { "escaped" }\n', encoding='utf-8')
        try:
            shadow.write_text('import "std/../escape" as ghost\nprint(ghost.marker())\n', encoding='utf-8')
            run('reject-stdlib-parent-traversal', ['check', shadow], expected=1, error='W1003')
        finally:
            escape.unlink()
        empty = installed / 'stdlib/empty.wysna'
        empty.write_text('', encoding='utf-8')
        try:
            shadow.write_text('import "std/empty" as empty\nprint("ok")\n', encoding='utf-8')
            run('accept-empty-standard-module', ['check', shadow], stdout=b'')
        finally:
            empty.unlink()
        if os.name != 'nt':
            external = project / 'external-module.wysna'
            external.write_text('pub fn marker() -> str { "escaped" }\n', encoding='utf-8')
            alias = installed / 'stdlib/escape.wysna'
            alias.symlink_to(external)
            try:
                shadow.write_text('import "std/escape" as ghost\nprint(ghost.marker())\n', encoding='utf-8')
                run('reject-stdlib-symlink-escape', ['check', shadow], expected=1, error='W1003')
            finally:
                alias.unlink()

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

        sk_res = run('agent-skills-json-v1', ['skills', '--output-format=json-v1'])
        sk_env = json.loads(sk_res.stdout.decode('utf-8'))
        require(sk_env.get('status') == 'ok' and sk_env.get('result', {}).get('schema_version') == 'wysna.skills/1', sk_env)

        sch_res = run('agent-schema-json-v1', ['schema', '--output-format=json-v1', source])
        sch_env = json.loads(sch_res.stdout.decode('utf-8'))
        require(sch_env.get('status') == 'ok' and sch_env.get('result', {}).get('schema_version') == 'wysna.schema/1', sch_env)

        api_res = run('agent-api-json-v1', ['api', 'std/json', '--output-format=json-v1'])
        api_env = json.loads(api_res.stdout.decode('utf-8'))
        require(api_env.get('status') == 'ok' and api_env.get('result', {}).get('schema_version') == 'wysna.api-index/1', api_env)
        require(any(m.get('identity') == 'std/json' for m in api_env.get('result', {}).get('modules', [])), api_env)

        proj_dir = work / 'native_project_smoke'
        (proj_dir / 'src').mkdir(parents=True)
        (proj_dir / 'tests').mkdir(parents=True)
        (proj_dir / 'wysna.toml').write_text(
            'manifest_version = 1\n\n[package]\nname = "native-smoke"\nversion = "0.1.0"\n'
            'language = "0.8"\n\n[toolchain]\ncompiler = "0.8.0"\nstdlib = "bundled"\n\n'
            '[sources]\nroot = "src"\n\n[targets.app]\nentry = "src/main.wysna"\nprofile = "native-cli"\n\n'
            '[[tests]]\nname = "unit-smoke"\nentry = "tests/smoke_test.wysna"\ntimeout_ms = 10000\n',
            encoding='utf-8',
        )
        (proj_dir / 'src' / 'logic.wysna').write_text('pub fn greet(who: str) -> str { return "hi " + who }\n', encoding='utf-8')
        (proj_dir / 'src' / 'main.wysna').write_text('import "logic.wysna" as logic\nprint(logic.greet("native"))\n', encoding='utf-8')
        (proj_dir / 'tests' / 'smoke_test.wysna').write_text('import "logic.wysna" as logic\nif logic.greet("ok") != "hi ok" { exit(1) }\n', encoding='utf-8')
        pcheck = run('project-check-json-v1', ['project', 'check', '--manifest', proj_dir / 'wysna.toml', '--output-format=json-v1'])
        require(json.loads(pcheck.stdout.decode('utf-8')).get('status') == 'ok', pcheck.stdout)
        pout = proj_dir / ('app' + suffix)
        run('project-build', ['project', 'build', '--manifest', proj_dir / 'wysna.toml', '-o', pout])
        require(pout.is_file(), 'Missing project build binary')
        run('project-run', ['project', 'run', '--manifest', proj_dir / 'wysna.toml'], stdout=b'hi native\n')
        run('project-test', ['project', 'test', '--manifest', proj_dir / 'wysna.toml'])
        if application is not None:
            scaffold = run('project-new', ['project', 'new', 'first-project', '--output-format=json-v1'])
            scaffold_result = json.loads(scaffold.stdout)
            require(scaffold_result['status'] == 'ok' and len(scaffold_result['artifacts']) == 3, scaffold_result)
            created = project / 'first-project' / 'wysna.toml'
            run('scaffold-project-lock', ['project', 'lock', '--manifest', created])
            run('scaffold-project-test', ['project', 'test', '--manifest', created, '--frozen'])

            app = project / 'wysofia-ops'
            shutil.copytree(application, app)
            manifest_path = app / 'wysna.toml'
            source_path = app / 'src/main.wysna'
            app_options = ['--manifest', manifest_path]
            run('ops-project-check', ['project', 'check', *app_options])
            run('ops-project-build', ['project', 'build', *app_options, '-o', project / ('ops' + suffix)])
            demo = run('ops-project-run', ['project', 'run', *app_options, '--', '--demo'])
            require(json.loads(demo.stdout) == {
                'critical_events': 1, 'health_score': 35, 'total_events': 4,
                'total_tokens': 1650, 'warning_events': 1,
            }, demo.stdout)
            ingest = project / ('ingest' + suffix)
            run('ops-ingest-build', ['project', 'build', *app_options, '--target', 'ingest', '-o', ingest])
            input_path = project / 'events.json'
            report_path = project / 'report.json'
            input_path.write_text('[{"service":"worker","status_code":503,"latency_ms":1800,"token_cost":40}]',
                                  encoding='utf-8')
            run('ops-ingest-valid', ['project', 'run', *app_options, '--target', 'ingest', '--', input_path, report_path])
            require(json.loads(report_path.read_text()) == {
                'critical_events': 1, 'health_score': 50, 'total_events': 1,
                'total_tokens': 40, 'warning_events': 0,
            }, report_path.read_text())
            previous_report = report_path.read_bytes()
            input_path.write_text('[{"service":"worker","status_code":600,"latency_ms":1,"token_cost":1}]',
                                  encoding='utf-8')
            run('ops-ingest-invalid-preserves-report',
                ['run', app / 'src/ingest.wysna', '--', input_path, report_path], expected=1, error='status_code')
            require(report_path.read_bytes() == previous_report, 'Invalid input replaced a valid report')
            tests = run('ops-project-test', ['project', 'test', *app_options])
            require(b'[PASS] audit-domain-contracts (native)' in tests.stdout, tests.stdout)
            graph = run('ops-graph-cli', ['graph', source_path, '--output-format=json-v1'])
            graph_payload = json.loads(graph.stdout)['result']
            require(any(node['name'] == 'parse_events_json' for node in graph_payload['nodes']), graph_payload)

            requests = [
                {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                 'params': {'protocolVersion': '2025-06-18'}},
                {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
                {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/call', 'params': {
                    'name': 'wysna_graph', 'arguments': {'path': 'src/main.wysna'},
                }},
            ]
            stdin = ''.join(json.dumps(request) + '\n' for request in requests).encode()
            mcp = run('ops-graph-mcp', ['mcp', '--root', app], stdin=stdin)
            responses = {item['id']: item for item in map(json.loads, mcp.stdout.splitlines())}
            answer = responses[2]['result']
            require(not answer['isError'], answer)
            mcp_graph = json.loads(answer['content'][0]['text'])['result']
            require(mcp_graph['graph_hash'] == graph_payload['graph_hash'], mcp_graph)

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
    parser.add_argument('--archive', type=Path)
    parser.add_argument('--application', type=Path)
    parser.add_argument('--language-fixtures', type=Path)
    options = parser.parse_args()
    report = {'checks': [], 'success': False}
    try:
        validate(options.sdk, report, options.strict_source, options.compiler_source,
                 options.application, options.language_fixtures)
        if options.archive is not None:
            report['archive_sha256'] = digest(options.archive)
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
