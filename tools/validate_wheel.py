#!/usr/bin/env python3
"""Verify an installed Wysna wheel without importing or reading its source checkout."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tempfile
import venv
import zipfile


def clean_environment():
    environment = dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1')
    for name in ('PYTHONPATH', 'PYTHONHOME', 'PYTHONSTARTUP'):
        environment.pop(name, None)
    return environment


def install_guard(site_packages, forbidden, denied):
    # The client denies source access and writes to its installed package. This
    # works on Windows too, where chmod does not make a directory read-only.
    source = '''import json, os
ROOT = {root!r}
FORBIDDEN = {forbidden!r}
DENIED = {denied!r}
def inside(path, root):
    return path == root or path.startswith(root + os.sep)
def audit(event, args):
    paths, writing = (), False
    if event == 'open':
        paths = args[:1]
        mode = args[1] or ''
        writing = any(flag in mode for flag in 'wax+') or bool(args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND))
    elif event in ('os.mkdir', 'os.remove', 'os.rmdir', 'os.rename'):
        paths, writing = args[:2] if event == 'os.rename' else args[:1], True
    elif event in ('os.listdir', 'os.scandir', 'os.chdir'):
        paths = args[:1]
    for value in paths:
        if not isinstance(value, (str, bytes, os.PathLike)):
            continue
        path = os.path.normcase(os.path.abspath(os.fsdecode(value)))
        if inside(path, FORBIDDEN) or (writing and inside(path, ROOT)):
            with open(DENIED, 'a', encoding='utf-8') as stream:
                stream.write(json.dumps({{'event': event, 'path': path}}) + '\\n')
            raise PermissionError('Installed-wheel isolation rejected: ' + path)
os.environ['WYSNA_WHEEL_GUARD'] = 'active'
import sys
sys.addaudithook(audit)
'''.format(root=os.path.normcase(str(site_packages)), forbidden=os.path.normcase(str(forbidden)), denied=str(denied))
    (site_packages / 'sitecustomize.py').write_text(source, encoding='utf-8')


def lsp_messages(output):
    messages = []
    while output:
        header, output = output.split(b'\r\n\r\n', 1)
        assert header.startswith(b'Content-Length: '), header
        size = int(header.split(b':', 1)[1])
        payload, output = output[:size], output[size:]
        assert len(payload) == size
        messages.append(json.loads(payload))
    return messages


def validate(wheel, forbidden, report):
    with zipfile.ZipFile(wheel) as archive:
        inventory = archive.namelist()
        assert {'main.py', 'runtime/wysna_runtime.h', 'stdlib/text.wysna', 'stdlib/json.wysna'} <= set(inventory)
        assert not any(Path(name).is_absolute() or '..' in Path(name).parts for name in inventory)
    report['inventory'] = inventory
    with tempfile.TemporaryDirectory(prefix='w installed client ') as temporary:
        directory = Path(temporary).resolve()
        prefix = directory / 'python environment'
        venv.EnvBuilder(with_pip=True).create(prefix)
        executables = prefix / ('Scripts' if os.name == 'nt' else 'bin')
        python = executables / ('python.exe' if os.name == 'nt' else 'python')
        cli = executables / ('wysna.exe' if os.name == 'nt' else 'wysna')
        wysna_cli = executables / ('wysna.exe' if os.name == 'nt' else 'wysna')
        environment = clean_environment()
        subprocess.run([str(python), '-m', 'pip', 'install', '--no-index', '--no-deps', str(wheel)],
                       env=environment, capture_output=True, text=True, check=True, timeout=120)
        installed = subprocess.run([str(python), '-I', '-c', 'import main;print(main.get_repo_root())'],
                                   env=environment, capture_output=True, text=True, check=True, timeout=30)
        site_packages = Path(installed.stdout.strip()).resolve()
        assert directory in site_packages.parents, site_packages
        assert cli.is_file()
        work = directory / 'project with spaces and á'
        work.mkdir()
        (work / 'hello.wysna').write_text('print("hello installed")\n', encoding='utf-8')
        (work / 'library.wysna').write_text(
            'import "std/text" as text\nimport "std/json" as json\n'
            'fn exercise() {\n'
            '    print(text.join(["hello", "stdlib"], " "))\n'
            '    let value = json.parse("{\\"ready\\":true}") else error { return }\n'
            '    let encoded = json.stringify(value) else error { return }\n'
            '    print(encoded)\n'
            '    json.parse_with_limits("[1,2]", 5, 32, 2) else error {\n'
            '        print("json node budget", error.offset)\n'
            '        json.null()\n'
            '    }\n'
            '}\n'
            'exercise()\n', encoding='utf-8')
        (work / 'invalid.wysna').write_text('let value: int = "wrong"\n', encoding='utf-8')
        denied = directory / 'denied-access.jsonl'
        install_guard(site_packages, forbidden, denied)
        probe = subprocess.run([str(python), '-I', '-B', '-c',
                                'import json,main,os;print(json.dumps({"root":main.get_repo_root(),"guard":os.getenv("WYSNA_WHEEL_GUARD")}))'],
                               env=environment, cwd=work, capture_output=True, text=True, check=True, timeout=30)
        assert json.loads(probe.stdout)['guard'] == 'active', probe
        assert not probe.stderr, probe.stderr
        report['installed_root'] = str(site_packages)
        report['forbidden_source'] = str(forbidden)
        checks = report['checks']

        def run(name, arguments, code=0, expected=None, custom_environment=None, input_text=None):
            result = subprocess.run([str(cli)] + arguments, cwd=work,
                                    env=custom_environment or environment, input=input_text,
                                    capture_output=True, text=True, timeout=120)
            checks.append({'name': name, 'arguments': arguments, 'exit_code': result.returncode,
                           'stdout': result.stdout, 'stderr': result.stderr})
            assert result.returncode == code, checks[-1]
            if expected is not None:
                assert result.stdout == expected, checks[-1]
            if code == 0:
                assert not result.stderr, checks[-1]
            assert 'Traceback' not in result.stderr and '[Internal Error]' not in result.stderr, checks[-1]
            return result

        run('version', ['version'])
        run('help', ['--help'])
        created = json.loads(run('project-new', ['project', 'new', 'new-project', '--output-format=json-v1']).stdout)
        assert created['status'] == 'ok' and len(created['artifacts']) == 3, created
        new_manifest = str(work / 'new-project' / 'wysna.toml')
        run('new-project-check', ['project', 'check', '--manifest', new_manifest])
        run('new-project-test', ['project', 'test', '--manifest', new_manifest])
        run('check', ['check', 'hello.wysna'])
        rejected = run('invalid', ['check', '--json', 'invalid.wysna'], code=1)
        assert json.loads(rejected.stdout)['code'] == 'W2001'
        human = run('invalid-human-diagnostic', ['check', 'invalid.wysna'], code=1)
        assert 'let value: int = "wrong"' in human.stderr and '^^^^^^^' in human.stderr
        structured = json.loads(run('invalid-json-v1-diagnostic',
                                    ['check', '--output-format=json-v1', 'invalid.wysna'], code=1).stdout)
        span = structured['diagnostics'][0]['primary_span']
        source_bytes = (work / 'invalid.wysna').read_bytes()
        assert source_bytes[span['start_byte']:span['end_byte']] == b'"wrong"'
        run('run', ['run', 'hello.wysna'], expected='hello installed\n')
        run('stdlib', ['run', 'library.wysna'], expected='hello stdlib\n{"ready":true}\njson node budget 3\n')
        (work / 'prelude-errors.wysna').write_text(
            'let issue = NetError { message: "offline" }\n'
            'let io = IoError { message: "io" }\n'
            'print(issue.message, io.message)\n', encoding='utf-8')
        run('prelude-errors-run', ['run', 'prelude-errors.wysna'], expected='offline io\n')
        prelude_binary = work / ('prelude-errors.exe' if os.name == 'nt' else 'prelude-errors')
        run('prelude-errors-build', ['build', 'prelude-errors.wysna', '-o', str(prelude_binary)])
        prelude = subprocess.run([str(prelude_binary)], cwd=work, env=environment,
                                 capture_output=True, text=True, timeout=30)
        assert prelude.returncode == 0 and prelude.stdout == 'offline io\n' and not prelude.stderr, prelude
        checks.append({'name': 'prelude-errors-native', 'exit_code': prelude.returncode,
                       'stdout': prelude.stdout})
        formatted = run('fmt', ['fmt', 'library.wysna'])
        (work / 'formatted.wysna').write_text(formatted.stdout, encoding='utf-8')
        run('fmt-idempotence', ['fmt', 'formatted.wysna'], expected=formatted.stdout)
        for command in ('tokens', 'ast', 'mir'):
            run(command, [command, 'hello.wysna'])
        run('emit-c', ['emit-c', 'library.wysna', '-o', 'library.c'])
        assert '#include "runtime/wysna_runtime.h"' in (work / 'library.c').read_text()
        binary = work / ('program.exe' if os.name == 'nt' else 'program')
        run('build', ['build', 'library.wysna', '-o', str(binary)])
        native = subprocess.run([str(binary)], cwd=work, env=environment, capture_output=True, text=True, timeout=30)
        assert native.returncode == 0 and native.stdout == 'hello stdlib\n{"ready":true}\njson node budget 3\n' and not native.stderr, native
        checks.append({'name': 'native-execution', 'exit_code': native.returncode, 'stdout': native.stdout})
        repl_input = 'fn twice(value: int) -> int {\nreturn value * 2\n}\nlet value = 21\nprint(twice(value))\nexit\n'
        repl = run('repl', ['repl'], input_text=repl_input)
        assert '42' in repl.stdout and '[W1001]' not in repl.stdout, repl.stdout
        for command in ('test', 'bench'):
            unavailable = run(command + '-source-only', [command], code=1)
            expected = 'wysna ' + command + ' requires a source checkout\n'
            if command == 'test':
                expected += "To run project tests, use 'wysna project test'.\n"
            assert unavailable.stderr == expected
        selfhost = run('selfhost-source-only', ['emit-c', '--backend=selfhost', 'hello.wysna', '-o', 'selfhost.c'], code=1)
        assert '[W2013]' in selfhost.stderr
        assert not (site_packages / '.wysna_cache').exists()
        no_compiler = dict(environment, CC=str(directory / 'missing compiler'), PATH='')
        for command in ('check', 'run', 'fmt', 'ast', 'tokens', 'mir'):
            run(command + '-without-c', [command, 'hello.wysna'], custom_environment=no_compiler)
        run('emit-c-without-c', ['emit-c', 'hello.wysna', '-o', 'without.c'], custom_environment=no_compiler)
        unavailable = run('build-without-c', ['build', '--json', 'hello.wysna', '-o', str(binary)],
                          code=1, custom_environment=no_compiler)
        diagnostic = json.loads(unavailable.stdout)
        assert diagnostic['code'] == 'W5001' and diagnostic['stage'] == 'toolchain', diagnostic
        packets = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
            {'jsonrpc': '2.0', 'method': 'textDocument/didOpen', 'params': {'textDocument': {
                'uri': (work / 'live.wysna').as_uri(), 'version': 1, 'languageId': 'wysna', 'text': 'let value: int = "wrong"\n'}}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'shutdown'},
            {'jsonrpc': '2.0', 'method': 'exit'}]
        data = b''
        for packet in packets:
            payload = json.dumps(packet).encode('utf-8')
            data += b'Content-Length: ' + str(len(payload)).encode('ascii') + b'\r\n\r\n' + payload
        lsp = subprocess.run([str(cli), 'lsp'], input=data, env=no_compiler, cwd=work,
                             capture_output=True, timeout=30)
        assert lsp.returncode == 0 and not lsp.stderr, lsp
        messages = lsp_messages(lsp.stdout)
        assert next(item for item in messages if item.get('id') == 1)['result']['capabilities']['definitionProvider']
        diagnostic = next(item for item in messages if item.get('method') == 'textDocument/publishDiagnostics')['params']['diagnostics'][0]
        assert diagnostic['code'] == 'W2001' and diagnostic['source'] == 'Wysna'
        assert diagnostic['range']['start'] == {'line': 0, 'character': 17}
        assert next(item for item in messages if item.get('id') == 2)['result'] is None
        checks.append({'name': 'lsp-without-c', 'messages': messages})
        assert not denied.exists(), denied.read_text() if denied.exists() else ''
        report['isolation'] = {'source_access_denied': True, 'installed_writes_denied': True,
                               'guard_active': True, 'denied_attempts': 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--wheel', type=Path, required=True)
    parser.add_argument('--forbid-source', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    wheel = args.wheel.resolve()
    report = {'wheel': wheel.name, 'sha256': hashlib.sha256(wheel.read_bytes()).hexdigest(),
              'platform': sys.platform, 'machine': platform.machine(), 'python': sys.version, 'checks': [], 'passed': False}
    try:
        validate(wheel, args.forbid_source.resolve(), report)
        report['passed'] = True
        print('Installed wheel:', len(report['checks']), 'checks passed on', report['platform'], report['machine'])
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
