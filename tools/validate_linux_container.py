#!/usr/bin/env python3
"""Verify SDK installation and isolation using a real Linux Docker engine."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

RUNNER = Path(__file__).with_name('run_isolated.py')
PROBE = """
import "std/fs" as fs
import "std/environment" as env

fn write_denied(path: str) -> bool {
    let _ = fs.mkdir(path) else error { return true }
    return false
}

print(write_denied("/project/probe"))
print(write_denied("/sdk/probe"))
print(env.get("WYSNA_HOST_ONLY") == None)
"""


def verify(image: str, project: Path, timeout: int, *arguments: str) -> dict:
    command = [
        sys.executable, str(RUNNER), '--image', image, '--project', str(project),
        '--timeout', str(timeout), '--', *arguments,
    ]
    result = subprocess.run(
        command, env=dict(os.environ, WYSNA_HOST_ONLY='host sentinel'),
        capture_output=True, text=True, timeout=timeout + 100,
    )
    report = json.loads(result.stdout)
    assert result.returncode == report['exit_code'], report
    assert report['schema'] == 'wysna.isolated-run/1' and report['container_removed'], report
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--cold-timeout', type=int, default=180)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='wysna-isolation-') as path:
        project = Path(path)
        project.chmod(0o777)
        (project / 'main.wysna').write_text(PROBE, encoding='utf-8')
        report = verify(args.image, project, 30, 'check', '/project/main.wysna')
        assert report['status'] == 'exited' and report['exit_code'] == 0, report
        report = verify(args.image, project, args.cold_timeout, 'build', '/project/main.wysna',
                        '-o', '/work/program')
        assert report['status'] == 'exited' and report['exit_code'] == 0, report
        report = verify(args.image, project, args.cold_timeout, 'run', '/project/main.wysna')
        assert report['status'] == 'exited' and report['exit_code'] == 0, report
        assert report['stdout'] == 'true\ntrue\ntrue\n', report
        assert not (project / 'probe').exists()
        (project / 'timeout.wysna').write_text('print("started")\nwhile true { }\n', encoding='utf-8')
        report = verify(args.image, project, args.cold_timeout, 'run', '/project/timeout.wysna')
        assert report['status'] == 'timeout' and report['exit_code'] == 124, report
        assert report['stdout'] == 'started\n', report
    print('Linux container: check, build, run, read-only mounts, environment and deadline passed')


if __name__ == '__main__':
    main()
