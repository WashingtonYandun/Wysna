#!/usr/bin/env python3
"""Run a native SDK command in a disposable, offline Linux container."""

import argparse
import csv
import io
import json
import re
import subprocess
import sys
import threading
import uuid
from pathlib import Path

OUTPUT_LIMIT = 64 * 1024
TOOL_TIMEOUT_SECONDS = 30
PROXY_VARIABLES = ('HTTP_PROXY', 'HTTPS_PROXY', 'FTP_PROXY', 'NO_PROXY', 'ALL_PROXY')


def project_mount(project: Path) -> str:
    fields = (
        'type=bind',
        f'src={project}',
        'dst=/project',
        'readonly',
        'bind-recursive=disabled',
    )
    encoded = io.StringIO()
    csv.writer(encoded).writerow(fields)
    return encoded.getvalue().removesuffix('\r\n')


def create_command(name: str, image: str, project: Path, arguments: list[str]) -> list[str]:
    # The Docker client otherwise applies proxies from its user configuration.
    proxy_overrides = [f'--env={spelling}=' for key in PROXY_VARIABLES
                       for spelling in (key, key.lower())]
    return [
        'docker', 'create', '--name', name, '--pull=never',
        '--read-only', '--network=none', '--ipc=none', '--user=65532:65532',
        '--cap-drop=ALL', '--security-opt=no-new-privileges',
        '--pids-limit=128', '--memory=1g', '--memory-swap=1g', '--cpus=1',
        '--log-driver=none', '--no-healthcheck',
        '--tmpfs=/tmp:rw,exec,nosuid,nodev,size=256m,mode=1777',
        '--tmpfs=/work:rw,exec,nosuid,nodev,size=512m,mode=1777',
        '--mount', project_mount(project),
        *proxy_overrides,
        '--entrypoint=/sdk/bin/wysna', image, *arguments,
    ]


def read_output(stream, output: bytearray) -> None:
    with stream:
        while chunk := stream.read(8192):
            # Drain excess bytes so a full pipe cannot block the child.
            remaining = OUTPUT_LIMIT + 1 - len(output)
            if remaining:
                output.extend(chunk[:remaining])


def tool(command: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        command,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=TOOL_TIMEOUT_SECONDS,
    )


def run(image: str, project: Path, arguments: list[str], timeout: int) -> dict:
    name = f'wysna-isolated-{uuid.uuid4().hex}'
    report = {
        'schema': 'wysna.isolated-run/1',
        'image': image,
        'status': 'engine_error',
        'exit_code': 125,
        'container_removed': False,
    }
    stdout = bytearray()
    stderr = bytearray()
    readers = []
    child = None
    created = False
    try:
        result = tool(create_command(name, image, project, arguments))
        if result.returncode:
            stderr.extend(result.stderr[:OUTPUT_LIMIT + 1])
        else:
            created = True
            child = subprocess.Popen(
                ['docker', 'start', '--attach', name],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for stream, output in ((child.stdout, stdout), (child.stderr, stderr)):
                reader = threading.Thread(target=read_output, args=(stream, output), daemon=True)
                reader.start()
                readers.append(reader)
            child.wait(timeout=timeout)
            result = tool([
                'docker', 'inspect', '--type=container',
                '--format={{json .State}}', name,
            ])
            if result.returncode:
                report['detail'] = result.stderr.decode('utf-8', errors='replace').strip()
            else:
                state = json.loads(result.stdout)
                if state['Status'] == 'exited':
                    report['exit_code'] = state['ExitCode']
                    report['status'] = 'out_of_memory' if state['OOMKilled'] else 'exited'
                else:
                    report['detail'] = f"Unexpected container state: {state['Status']}"
    except subprocess.TimeoutExpired:
        if child is None or child.returncode is not None:
            report['detail'] = 'Docker control operation timed out'
        else:
            report['status'] = 'timeout'
            report['exit_code'] = 124
    except KeyboardInterrupt:
        report['status'] = 'interrupted'
        report['exit_code'] = 130
    except (OSError, ValueError, KeyError) as error:
        report['detail'] = str(error)
    finally:
        if created:
            try:
                removed = tool(['docker', 'rm', '--force', '--volumes', name])
                report['container_removed'] = removed.returncode == 0
            except (OSError, subprocess.TimeoutExpired):
                pass
            if not report['container_removed']:
                report['status'] = 'cleanup_failed'
                report['exit_code'] = 125
                report['container'] = name
        if child is not None and child.poll() is None:
            child.kill()
            child.wait()
        for reader in readers:
            reader.join()
    for field, output in (('stdout', stdout), ('stderr', stderr)):
        report[field] = output[:OUTPUT_LIMIT].decode('utf-8', errors='replace')
        report[f'{field}_truncated'] = len(output) > OUTPUT_LIMIT
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, help='Local image ID from docker build --quiet')
    parser.add_argument('--project', required=True, type=Path, help='Directory exposed read-only at /project')
    parser.add_argument('--timeout', type=int, default=180, metavar='SECONDS')
    parser.add_argument('arguments', nargs=argparse.REMAINDER, help='SDK arguments after --')
    options = parser.parse_args()
    if not re.fullmatch(r'sha256:[a-f0-9]{64}', options.image):
        parser.error('--image must identify a local image by its full sha256 ID')
    if not 1 <= options.timeout <= 600:
        parser.error('--timeout must be between 1 and 600 seconds')
    try:
        project = options.project.resolve(strict=True)
    except OSError as error:
        parser.error(str(error))
    if project == project.parent or not project.is_dir():
        parser.error('--project must name a project directory')
    arguments = options.arguments
    if arguments and arguments[0] == '--':
        arguments = arguments[1:]
    if not arguments:
        parser.error('Provide an SDK command after --')
    report = run(options.image, project, arguments, options.timeout)
    print(json.dumps(report, ensure_ascii=True))
    return report['exit_code']


if __name__ == '__main__':
    sys.exit(main())
