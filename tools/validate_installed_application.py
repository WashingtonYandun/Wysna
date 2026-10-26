#!/usr/bin/env python3
"""Build and exercise bounded JSON ingestion using only the installed C toolchain."""
import argparse
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shlex
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from threading import Thread
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.package_measurement_pilot import SQLITE_ARCHIVE, SQLITE_SHA3, sqlite_archive


def validate(installation, report, *, http=False, archive=None):
    installation = Path(installation).resolve()
    record = json.loads((installation / 'installation.json').read_text(encoding='utf-8'))
    cli = installation / record['launcher']
    with tempfile.TemporaryDirectory(prefix='wysna installed application á ') as temporary:
        work = Path(temporary).resolve()
        for name in ('ingest.wysna', 'fetch.wysna'):
            shutil.copy2(ROOT / 'examples/automation-sync' / name, work / name)
        for name in ('adapter.c', 'adapter.h'):
            shutil.copy2(ROOT / 'examples/ffi/sqlite' / name, work / name)
        with zipfile.ZipFile(sqlite_archive(archive)) as packed:
            for name in ('sqlite3.c', 'sqlite3.h'):
                (work / name).write_bytes(packed.read(SQLITE_ARCHIVE + '/' + name))
        environment = {name: value for name, value in os.environ.items()
                       if name.upper() not in {'CC', 'PYTHONHOME', 'PYTHONPATH', 'VIRTUAL_ENV',
                                               'INCLUDE', 'LIB', 'LIBPATH', 'VCINSTALLDIR'}}
        empty = work / 'no-host-tools'
        empty.mkdir()
        environment['PATH'] = str(Path(os.environ['SystemRoot']) / 'System32') if os.name == 'nt' else str(empty)
        environment.update(ZIG_GLOBAL_CACHE_DIR=str(work / 'zig-global'),
                           ZIG_LOCAL_CACHE_DIR=str(work / 'zig-local'), WYSNA_MEM_CHECK='1',
                           WYSNA_SQLITE_DB=str(work / 'measurements.sqlite'))
        environment['CC'] = shlex.join([str(installation / record['compiler']), 'cc',
            '-target', record['target'], '-mcpu=baseline', '-I' + str(work)])
        checks = report['checks']

        def run(name, command, *, code=0, env=environment):
            result = subprocess.run(list(map(str, command)), env=env, cwd=work, capture_output=True,
                                    text=True, encoding='utf-8', timeout=300)
            checks.append({'name': name, 'exit_code': result.returncode,
                           'stdout': result.stdout, 'stderr': result.stderr})
            if result.returncode != code or (code == 0 and result.stderr):
                raise AssertionError(checks[-1])
            return result

        binary = work / ('measurement-sync.exe' if os.name == 'nt' else 'measurement-sync')
        command = [cli, 'build', work / 'ingest.wysna', '--link-lib', work / 'adapter.c',
                   '--link-lib', work / 'sqlite3.c', '-o', binary]
        if sys.platform.startswith('linux'):
            command += ['--link-lib', 'pthread', '--link-lib', 'dl', '--link-lib', 'm']
        run('build-with-installed-private-compiler', command)
        report['build_uses_host_c_compiler'] = False
        explanation = json.loads(run('explain-external-effects', [cli, 'explain', work / 'ingest.wysna',
                                                               '--output-format=json-v1']).stdout)
        assert 'native-ffi' in explanation['result']['program']['capabilities'], explanation
        assert not explanation['result']['program']['complete_within_model'], explanation
        input_path = work / 'measurements.json'

        def snapshot():
            with closing(sqlite3.connect(environment['WYSNA_SQLITE_DB'])) as connection:
                rows = connection.execute('SELECT id,value FROM measurements ORDER BY id').fetchall()
                journal = connection.execute('SELECT row_count,input_bytes FROM imports ORDER BY id').fetchall()
                return rows, journal

        input_path.write_text('[{"id":1,"value":7},{"id":2,"value":42}]', encoding='utf-8')
        result = run('atomic-json-ingestion', [binary, input_path])
        assert json.loads(result.stdout) == {'imported':2, 'count':2, 'total':49, 'imports':1, 'live_resources':0}
        baseline = snapshot()
        assert baseline == ([(1, 7), (2, 42)], [(2, input_path.stat().st_size)]), baseline
        for name, source, code in (
            ('duplicate-rollback', '[{"id":3,"value":5},{"id":1,"value":9}]', 3),
            ('invalid-row-rollback', '[{"id":3,"value":5},{"id":4,"value":-1}]', 3),
            ('int64-overflow-rollback', '[{"id":3,"value":9223372036854775808}]', 3),
            ('invalid-json', '{', 3), ('empty-array', '[]', 3),
            ('row-limit', json.dumps([{'id':3,'value':5}] * 1001), 3),
            ('input-byte-limit', ' ' * 65537, 2), ('invalid-UTF8', b'\xff', 2),
        ):
            input_path.write_bytes(source if isinstance(source, bytes) else source.encode('utf-8'))
            run(name, [binary, input_path], code=code)
            assert snapshot() == baseline, name + ' changed persistent data'
        broken = dict(environment, WYSNA_SQLITE_DB=str(work / 'missing/database.sqlite'))
        input_path.write_text('[{"id":3,"value":5}]', encoding='utf-8')
        run('database-open-error', [binary, input_path], code=3, env=broken)
        run('argument-error', [binary], code=2)

        if http:
            # Only declared libcurl discovery is available; no host C compiler is used.
            config = shutil.which('curl-config')
            if not config:
                raise ValueError('--http requires declared libcurl development inputs and curl-config')
            discovery = empty / 'curl-config'
            discovery.symlink_to(config)
            for name in ('cut', 'sed', 'dirname', 'uname', 'tr', 'awk', 'grep', 'expr', 'head', 'cat'):
                utility = shutil.which(name)
                if utility:
                    (empty / name).symlink_to(utility)
            downloader = work / 'measurement-fetch'
            run('build-installed-http-client', [cli, 'build', work / 'fetch.wysna', '-o', downloader])
            payload = b'[{"id":3,"value":5}]'

            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *args):
                    pass

                def do_GET(self):
                    status, body = (503, b'[]') if self.path == '/unavailable' else (200, payload)
                    if self.path == '/malformed':
                        body = b'{'
                    if self.path == '/large':
                        body = b' ' * 65537
                    self.send_response(status)
                    self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(body)))
                    self.end_headers()
                    try:
                        self.wfile.write(body)
                    except (BrokenPipeError, ConnectionResetError):
                        pass

            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                endpoint = 'http://127.0.0.1:' + str(server.server_port)
                run('bounded-http-download', [downloader, endpoint + '/measurements', input_path])
                assert input_path.read_bytes() == payload
                result = run('http-json-sqlite-workflow', [binary, input_path])
                assert json.loads(result.stdout) == {'imported':1,'count':3,'total':54,'imports':2,'live_resources':0}
                baseline = snapshot()
                for path, code in (('/unavailable',3),('/malformed',4),('/large',3)):
                    run('http-rejects' + path, [downloader, endpoint + path, input_path], code=code)
                    assert input_path.read_bytes() == payload and snapshot() == baseline
            finally:
                server.shutdown()
                server.server_close()
                thread.join(timeout=5)
        report.update(passed=True, platform=record['platform'], source_commit=record['source_commit'],
                      source_dirty=record['source_dirty'], http_qualified=http,
                      sqlite_archive_sha3_256=SQLITE_SHA3, persisted_rows=snapshot()[0])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--installation', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--sqlite-archive', type=Path)
    parser.add_argument('--http', action='store_true')
    args = parser.parse_args()
    report = {'schema':'wysna.installed-application/1','passed':False,'checks':[]}
    try:
        validate(args.installation, report, http=args.http, archive=args.sqlite_archive)
    finally:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks'])}))


if __name__ == '__main__':
    main()
