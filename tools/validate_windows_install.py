#!/usr/bin/env python3
"""Exercise the installed Windows compiler with no host Python, C compiler or SDK."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile


def isolated_environment():
    # os.environ is case-insensitive on Windows, but a copied dict is not.
    environment = {name.upper(): value for name, value in os.environ.items()}
    for name in ('CC', 'PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV', 'INCLUDE', 'LIB', 'LIBPATH',
                 'VCINSTALLDIR', 'VCTOOLSINSTALLDIR', 'WINDOWSSDKDIR', 'WINDOWSSDKVERSION'):
        environment.pop(name, None)
    environment['PATH'] = str(Path(environment['SYSTEMROOT']) / 'System32')
    return environment


def validate(installation, report):
    installation = Path(installation).resolve()
    cli = installation / 'wysna.exe'
    manifest = json.loads((installation / 'windows-bundle.json').read_text(encoding='utf-8'))
    environment = isolated_environment()
    checks = report['checks']
    with tempfile.TemporaryDirectory(prefix='wysna installed project á ') as temporary:
        work = Path(temporary)
        source = work / 'hello á & spaces.wysna'
        source.write_text('import "std/text" as text\nprint(text.join(["hello", "Wysna"], " "))\n', encoding='utf-8')
        invalid = work / 'invalid.wysna'
        invalid.write_text('let value: int = "wrong"\n', encoding='utf-8')

        def run(name, arguments, expected=None, code=0, custom_environment=None, executable=cli):
            result = subprocess.run([str(executable)] + arguments, cwd=work,
                                    env=custom_environment or environment,
                                    capture_output=True, text=True, encoding='utf-8', timeout=300)
            checks.append({'name': name, 'exit_code': result.returncode,
                           'stdout': result.stdout, 'stderr': result.stderr})
            assert result.returncode == code, checks[-1]
            if expected is not None:
                assert result.stdout == expected, checks[-1]
            if code == 0:
                assert not result.stderr, checks[-1]
            return result

        run('version', ['version'])
        run('help', ['--help'])
        run('check', ['check', str(source)])
        run('run-stdlib-and-unicode-path', ['run', str(source)], 'hello Wysna\n')
        diagnostic = run('invalid-exit-status', ['check', '--json', str(invalid)], code=1)
        assert json.loads(diagnostic.stdout)['code'] == 'W2001', diagnostic
        run('emit-c', ['emit-c', str(source), '-o', str(work / 'generated.c')])
        run('build-without-host-toolchain', ['build', str(source), '-o', str(work / 'hello')])
        native = run('execute-native', [], 'hello Wysna\n', executable=work / 'hello.exe')
        assert native.returncode == 0
        broken_environment = dict(environment, CC=str(work / 'missing-compiler.exe'))
        diagnostic = run('explicit-CC-is-honored', ['build', '--json', str(source), '-o', str(work / 'bad')],
                         code=1, custom_environment=broken_environment)
        assert json.loads(diagnostic.stdout)['code'] == 'W5001', diagnostic
        run('run-without-compiler', ['run', str(source)], 'hello Wysna\n', custom_environment=broken_environment)
        for command in ('test', 'bench'):
            run(command + '-source-only', [command], code=1)
        run('selfhost-source-only', ['build', '--engine=compiler', str(source), '-o', str(work / 'selfhost')], code=1)
        formatted = run('format', ['fmt', str(source)])
        (work / 'formatted.wysna').write_text(formatted.stdout, encoding='utf-8')
        run('format-idempotence', ['fmt', str(work / 'formatted.wysna')], formatted.stdout)
        packets = [
            {'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'shutdown'},
            {'jsonrpc': '2.0', 'method': 'exit'},
        ]
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
        checks.append({'name': 'launcher-preserves-LSP-stdio', 'messages': messages})
        # A fresh shell resolves the installed command from the persisted user PATH.
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
            user_path = winreg.QueryValueEx(key, 'Path')[0]
        resolved_environment = dict(environment, PATH=os.path.expandvars(user_path) + ';' + environment['PATH'])
        resolved = shutil.which('wysna.exe', path=resolved_environment['PATH'])
        assert resolved and Path(resolved).resolve() == cli, resolved
        run('fresh-terminal-PATH', ['version'], custom_environment=resolved_environment, executable=Path(resolved))
        # A portable copy must not retain absolute installation paths.
        relocated = work / 'relocated Wysna á'
        shutil.copytree(installation, relocated)
        run('relocated-runtime', ['run', str(source)], 'hello Wysna\n', executable=relocated / 'wysna.exe')
        run('relocated-build', ['build', str(source), '-o', str(work / 'relocated')], executable=relocated / 'wysna.exe')
        run('relocated-native', [], 'hello Wysna\n', executable=work / 'relocated.exe')
    report.update(passed=True, manifest=manifest, host_dependencies_removed=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installation', required=True, type=Path)
    parser.add_argument('--report', required=True, type=Path)
    args = parser.parse_args()
    report = {'passed': False, 'checks': []}
    try:
        validate(args.installation, report)
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks'])}))


if __name__ == '__main__':
    main()
