"""Bootstrap an installed Seed distribution with an isolated, private runtime."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import sys
import uuid

ROOT = Path(__file__).resolve().parent


def file_hash(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def installation(full=False):
    record = json.loads((ROOT / 'installation.json').read_text(encoding='utf-8'))
    if record['schema'] != 'wysna.installation/1' or record['implementation'] != 'seed':
        raise ValueError('Unsupported installation manifest')
    required = {'main.py', 'common/Version.py', 'compiler/CompilerService.py',
                'tooling/ToolResult.py', 'wysna_entry.py', 'runtime/wysna_runtime.h',
                'stdlib/json.wysna', record['launcher'], record['python'], record['compiler']}
    if not required <= record['files'].keys():
        raise ValueError('Installation manifest omits a required component')
    for relative in record['files'] if full else required:
        name = PurePosixPath(relative)
        if name.is_absolute() or '..' in name.parts or '\\' in relative:
            raise ValueError('Unsafe installation path')
        path = ROOT / relative
        if not path.resolve().is_relative_to(ROOT) or file_hash(path) != record['files'][relative]:
            raise ValueError('Missing or damaged installation component: ' + relative)
    return record


def fail(error):
    # The package builder substitutes the canonical ToolResult bootstrap template.
    result = BOOTSTRAP_ERROR.copy()
    result['invocation_id'] = str(uuid.uuid4())
    result['diagnostics'] = [dict(BOOTSTRAP_ERROR['diagnostics'][0], message='Invalid Wysna installation: ' + str(error))]
    arguments = sys.argv[1:]
    if '--output-format=json-v1' in arguments or '--output-format' in arguments:
        print(json.dumps(result, separators=(',', ':')))
    else:
        print('[W5001] Invalid Wysna installation: ' + str(error) + '. Repair or reinstall Wysna.', file=sys.stderr)
    return 3


def main():
    try:
        doctor = bool(sys.argv[1:] and sys.argv[1] == 'doctor')
        record = installation(full=doctor)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return fail(error)
    sys.path.insert(0, str(ROOT))
    if doctor:
        from tooling.ToolResult import diagnostic_record, tool_result, write_tool_result
        if sys.argv[2:] not in ([], ['--output-format=json-v1'], ['--output-format', 'json-v1']):
            result = tool_result('doctor', 'usage_error', 2,
                                 diagnostics=[diagnostic_record('W5002', 'options', 'doctor accepts only --output-format=json-v1')])
            write_tool_result(result, sys.stdout)
            return 2
        payload = {'schema': 'wysna.installation-health/1', 'status': 'ok',
                   'version': record['version'], 'platform': record['platform'],
                   'source_commit': record['source_commit'], 'root': str(ROOT),
                   'verified_files': len(record['files']), 'host_python_required': False,
                   'host_c_compiler_required': False}
        if len(sys.argv) > 2:
            result = tool_result('doctor', 'ok', 0)
            result['result'] = payload
            write_tool_result(result, sys.stdout)
        else:
            print('Wysna installation is healthy (' + record['version'] + ', ' + record['platform'] + ').')
        return 0
    if not os.environ.get('CC'):
        os.environ['CC'] = shlex.join([str(ROOT / record['compiler']), 'cc', '-target', record['target'], '-mcpu=baseline'])
    from main import main as run
    sys.argv[0] = 'wysna'
    run()
    return 0


BOOTSTRAP_ERROR = __WYSNA_BOOTSTRAP_ERROR__

if __name__ == '__main__':
    raise SystemExit(main())
