#!/usr/bin/env python3
"""Accept an installed Seed product outside its checkout and host development tools."""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile


def isolated_environment(work):
    environment = dict(os.environ)
    removed = {'CC', 'PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'INCLUDE', 'LIB', 'LIBPATH',
               'VCINSTALLDIR', 'VCTOOLSINSTALLDIR', 'WINDOWSSDKDIR', 'WINDOWSSDKVERSION',
               'WYSNA_LLVM_SDK', 'DYLD_LIBRARY_PATH', 'LD_LIBRARY_PATH', 'LD_PRELOAD'}
    for name in list(environment):
        if name.upper() in removed:
            environment.pop(name)
    empty_path = work / 'empty-path'
    empty_path.mkdir()
    environment['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32') if os.name == 'nt' else str(empty_path)
    environment['ZIG_GLOBAL_CACHE_DIR'] = str(work / 'zig-global')
    environment['ZIG_LOCAL_CACHE_DIR'] = str(work / 'zig-local')
    return environment


def validate(installation, report, expected_commit=None):
    installation = Path(installation).resolve()
    manifest = json.loads((installation / 'installation.json').read_text(encoding='utf-8'))
    if manifest['schema'] != 'wysna.installation/1':
        raise ValueError('Unknown installed product schema')
    if expected_commit and (manifest['source_commit'] != expected_commit or manifest['source_dirty']):
        raise ValueError('Acceptance requires the exact clean requested source commit')
    cli = installation / manifest['launcher']
    checks = report['checks']
    with tempfile.TemporaryDirectory(prefix='wysna installed acceptance á ') as temporary:
        work = Path(temporary).resolve()
        environment = isolated_environment(work)

        def run(name, arguments, *, executable=cli, code=0, expected=None, cwd=work, input_text=None):
            result = subprocess.run([str(executable), *map(str, arguments)], cwd=cwd, env=environment,
                                    input=input_text, capture_output=True, text=True, encoding='utf-8', timeout=300)
            check = {'name': name, 'exit_code': result.returncode,
                     'stdout': result.stdout, 'stderr': result.stderr}
            checks.append(check)
            if result.returncode != code or (expected is not None and result.stdout != expected):
                raise AssertionError(check)
            if code == 0 and result.stderr:
                raise AssertionError(check)
            return result

        run('version', ['version'])
        repl_input = 'fn twice(value: int) -> int {\nreturn value * 2\n}\nlet value = 21\nprint(twice(value))\nexit\n'
        repl = run('repl-state', ['repl'], input_text=repl_input)
        assert '42' in repl.stdout and '[W1001]' not in repl.stdout, repl.stdout
        created = json.loads(run('project-new', ['project', 'new', 'new-project', '--output-format=json-v1']).stdout)
        assert created['status'] == 'ok' and len(created['artifacts']) == 3, created
        new_manifest = work / 'new-project' / 'wysna.toml'
        run('new-project-check', ['project', 'check', '--manifest', new_manifest])
        run('new-project-test', ['project', 'test', '--manifest', new_manifest])
        health = json.loads(run('full-integrity', ['doctor', '--output-format=json-v1']).stdout)
        assert health['result']['verified_files'] == len(manifest['files']), health
        assert health['result']['root'] == str(installation), health
        capabilities = json.loads(run('capabilities', ['capabilities', '--output-format=json-v1']).stdout)
        assert 'explain' in capabilities['result']['commands'], capabilities
        assert 'doctor' in capabilities['result']['commands'], capabilities
        assert 'doctor' in capabilities['result']['machine_output']['commands'], capabilities
        run('api-discovery', ['api', 'std/json', '--output-format=json-v1'])
        source = work / 'JSON á & spaces.wysna'
        source.write_text('import "std/json" as json\n'
                          'let value = json.number("42") else error { json.null() }\n'
                          'let output = json.stringify(json.object({"answer": value})) else error { "{}" }\n'
                          'print(output)\n', encoding='utf-8')
        expected = '{"answer":42}\n'
        run('check', ['check', source, '--output-format=json-v1'])
        run('reference-run', ['run', source], expected=expected)
        explanation = json.loads(run('effect-report', ['explain', source, '--output-format=json-v1']).stdout)
        assert explanation['result']['schema_version'] == 'wysna.effect-report/1', explanation
        assert 'stdio' in explanation['result']['program']['capabilities'], explanation
        native = work / ('native.exe' if os.name == 'nt' else 'native')
        run('native-build-without-host-compiler', ['build', source, '-o', native])
        run('native-execution', [], executable=native, expected=expected)
        prelude_source = work / 'prelude-errors.wysna'
        prelude_source.write_text('let issue = NetError { message: "offline" }\n'
                                  'let io = IoError { message: "io" }\n'
                                  'print(issue.message, io.message)\n', encoding='utf-8')
        run('prelude-errors-run', ['run', prelude_source], expected='offline io\n')
        prelude_binary = work / ('prelude-errors.exe' if os.name == 'nt' else 'prelude-errors')
        run('prelude-errors-build', ['build', prelude_source, '-o', prelude_binary])
        run('prelude-errors-native', [], executable=prelude_binary, expected='offline io\n')
        arguments = work / 'arguments.wysna'
        arguments.write_text('let arguments = args()\nprint(arguments[1])\n', encoding='utf-8')
        run('program-arguments', ['run', arguments, '--', '--output-format=json-v1'],
            expected='--output-format=json-v1\n')
        invalid = work / 'invalid.wysna'
        invalid.write_text('let value: int = "wrong"\n', encoding='utf-8')
        diagnostic = json.loads(run('structured-diagnostic', ['check', invalid, '--output-format=json-v1'], code=1).stdout)
        assert diagnostic['status'] == 'diagnostic_error', diagnostic
        span = diagnostic['diagnostics'][0]['primary_span']
        assert invalid.read_bytes()[span['start_byte']:span['end_byte']] == b'"wrong"', diagnostic
        human = run('human-diagnostic', ['check', invalid], code=1)
        assert 'let value: int = "wrong"' in human.stderr and '^^^^^^^' in human.stderr, human.stderr
        formatted = run('format', ['fmt', source]).stdout
        formatted_source = work / 'formatted.wysna'
        formatted_source.write_text(formatted, encoding='utf-8')
        run('format-idempotence', ['fmt', formatted_source], expected=formatted)

        project = work / 'project'
        (project / 'src').mkdir(parents=True)
        (project / 'tests').mkdir()
        manifest_path = project / 'wysna.toml'
        manifest_path.write_text('manifest_version = 1\n[package]\nname = "installed-acceptance"\n'
            'version = "0.1.0"\nlanguage = "0.5"\n[toolchain]\ncompiler = "' + manifest['version'] +
            '"\nstdlib = "bundled"\n[sources]\nroot = "src"\n[targets.app]\n'
            'entry = "src/main.wysna"\nprofile = "native-cli"\n'
            '[[tests]]\nname = "installed-argv"\nentry = "tests/argv.wysna"\ntimeout_ms = 10000\n', encoding='utf-8')
        project_source = project / 'src/main.wysna'
        project_source.write_text('fn main() -> unit { print("installed project") }\n', encoding='utf-8')
        (project / 'tests/argv.wysna').write_text('import "std/test"\nfn main() -> unit {\n'
            'test.expect_equal_int(len(args()), 1, "stable arguments")\n}\n', encoding='utf-8')
        run('project-lock', ['project', 'lock', '--manifest', manifest_path])
        run('project-check-frozen', ['project', 'check', '--manifest', manifest_path, '--frozen', '--output-format=json-v1'])
        run('project-explain-frozen', ['explain', project_source, '--manifest', manifest_path, '--frozen', '--output-format=json-v1'])
        run('project-run-frozen', ['project', 'run', '--manifest', manifest_path, '--frozen'], expected='installed project\n')
        run('project-test-frozen', ['project', 'test', '--manifest', manifest_path, '--frozen'])

        packets = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
                   {'jsonrpc': '2.0', 'id': 2, 'method': 'shutdown'}, {'jsonrpc': '2.0', 'method': 'exit'}]
        data = b''
        for packet in packets:
            payload = json.dumps(packet).encode('utf-8')
            data += b'Content-Length: ' + str(len(payload)).encode('ascii') + b'\r\n\r\n' + payload
        lsp = subprocess.run([str(cli), 'lsp'], input=data, env=environment, cwd=work,
                             capture_output=True, timeout=30)
        assert lsp.returncode == 0 and not lsp.stderr, lsp
        messages, remaining = [], lsp.stdout
        while remaining:
            header, remaining = remaining.split(b'\r\n\r\n', 1)
            size = int(header.split(b':', 1)[1])
            messages.append(json.loads(remaining[:size]))
            remaining = remaining[size:]
        assert next(item for item in messages if item.get('id') == 1)['result']['capabilities']['definitionProvider']
        assert next(item for item in messages if item.get('id') == 2)['result'] is None
        checks.append({'name': 'editor-LSP-stdio', 'messages': messages})
        run('integrity-after-project-subprocesses', ['doctor', '--output-format=json-v1'])
        actual_files = {path.relative_to(installation).as_posix()
                        for path in installation.rglob('*') if path.is_file()}
        installer_files = {name for name in actual_files if os.name == 'nt' and re.fullmatch(r'unins\d{3}\.(exe|dat|msg)', name)}
        assert actual_files - installer_files == set(manifest['files']) | {'installation.json'}, 'Installed tools wrote unrecorded files'

        relocated = work / 'relocated Wysna á'
        shutil.copytree(installation, relocated, symlinks=True)
        relocated_cli = relocated / manifest['launcher']
        modes = {}
        try:
            if os.name != 'nt':
                for path in [relocated, *relocated.rglob('*')]:
                    if not path.is_symlink():
                        modes[path] = path.stat().st_mode & 0o777
                        path.chmod(modes[path] & ~0o222)
            run('relocated-read-only-run', ['run', source], executable=relocated_cli, expected=expected)
            run('relocated-read-only-build', ['build', source, '-o', native], executable=relocated_cli)
            run('relocated-native', [], executable=native, expected=expected)
            run('relocated-read-only-project-tests', ['project', 'test', '--manifest', manifest_path, '--frozen'],
                executable=relocated_cli)
            run('relocated-read-only-integrity', ['doctor', '--output-format=json-v1'], executable=relocated_cli)
        finally:
            for path, mode in modes.items():
                path.chmod(mode)
        compiler = relocated / manifest['compiler']
        compiler.unlink()
        error = json.loads(run('missing-component-no-fallback', ['check', source, '--output-format=json-v1'],
                               executable=relocated_cli, code=3).stdout)
        assert error['status'] == 'toolchain_error' and error['diagnostics'][0]['code'] == 'W5001', error
    report.update(passed=True, manifest={key: manifest[key] for key in
                  ('version', 'platform', 'source_commit', 'source_dirty', 'wheel_sha256')},
                  host_development_tools_removed_from_path=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installation', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--source-commit')
    args = parser.parse_args()
    report = {'schema': 'wysna.installed-acceptance/1', 'passed': False, 'checks': []}
    try:
        validate(args.installation, report, args.source_commit)
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks'])}))


if __name__ == '__main__':
    main()
