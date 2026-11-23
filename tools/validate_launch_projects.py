#!/usr/bin/env python3
"""Qualify launch projects from an installed SDK and local package assets."""

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

from validate_native_sdk import digest, isolated_environment, require


def validate(sdk: Path, examples: Path) -> dict:
    sdk = sdk.resolve()
    examples = examples.resolve()
    suffix = '.exe' if os.name == 'nt' else ''
    driver = sdk / 'bin' / ('wysna' + suffix)
    manifest = json.loads((sdk / 'native-sdk.json').read_text(encoding='utf-8'))
    worker_output = [
        {'status': 'completed', 'value': 4},
        {'status': 'failed', 'reason': 'item_out_of_range'},
        {'status': 'completed', 'value': 9},
    ]

    with tempfile.TemporaryDirectory(prefix='wysna-launch-') as location:
        root = Path(location)
        environment = isolated_environment(root)

        def execute(command, cwd=root, expected=0):
            output = subprocess.run(
                [str(argument) for argument in command], cwd=cwd, env=environment,
                text=True, encoding='utf-8', capture_output=True, timeout=180,
            )
            require(output.returncode == expected, (
                command, output.returncode, output.stdout[-2000:], output.stderr[-2000:],
            ))
            return output

        def run(*arguments, cwd=root, expected=0):
            return execute([driver, *arguments], cwd=cwd, expected=expected)

        binaries = {}
        products = root / 'bin'
        products.mkdir()
        for name, target in (('automation', 'app'), ('wysofia-ops', 'ingest'), ('agent-worker', 'app')):
            project = root / name
            shutil.copytree(examples / name, project)
            manifest_path = project / 'wysna.toml'
            run('project', 'lock', '--manifest', manifest_path, '--offline')
            options = ('--manifest', manifest_path, '--frozen', '--offline')
            run('project', 'check', *options, '--target', target)
            test = run('project', 'test', *options, '--output-format=json-v1')
            require(json.loads(test.stdout)['status'] == 'ok', (name, test.stdout))
            binary = products / (name + suffix)
            run('project', 'build', *options, '--target', target, '-o', binary)
            require(binary.is_file(), name)
            binaries[name] = binary

        request = '[2,-1,3]'
        worker = execute([binaries['agent-worker'], request])
        require(json.loads(worker.stdout) == worker_output, worker.stdout)
        coupled = execute([binaries['automation'], binaries['agent-worker'], request])
        require(json.loads(coupled.stdout.splitlines()[-1]) == worker_output, coupled.stdout)

        data = root / 'events.json'
        output = root / 'result.json'
        data.write_text('[{"service":"worker","status_code":503,"latency_ms":1800,"token_cost":40}]',
                        encoding='utf-8')
        execute([binaries['wysofia-ops'], data, output])
        require(json.loads(output.read_text(encoding='utf-8')) == {
            'critical_events': 1, 'health_score': 50, 'total_events': 1,
            'total_tokens': 40, 'warning_events': 0,
        }, output.read_text(encoding='utf-8'))
        valid_report = output.read_bytes()
        data.write_text('[] invalid', encoding='utf-8')
        execute([binaries['wysofia-ops'], data, output], expected=1)
        require(output.read_bytes() == valid_report, 'invalid input replaced an existing report')

        archive = root / 'agent-worker.tar'
        package = run('packages', 'pack', '--manifest', root / 'agent-worker' / 'wysna.toml',
                      '-o', archive, '--output-format=json-v1')
        pin = json.loads(package.stdout)['result']['archive_sha256']
        require(digest(archive) == pin, 'package SHA-256 mismatch')
        run('project', 'new', 'consumer')
        consumer = root / 'consumer'
        consumer_manifest = consumer / 'wysna.toml'
        cache = root / 'local packages'
        run('packages', 'fetch', '--manifest', consumer_manifest, '--sha256', pin,
            '--archive', archive, '--cache', cache)
        run('packages', 'add', 'worker', '--manifest', consumer_manifest, '--path', 'vendor/' + pin)
        (consumer / 'src/main.wysna').write_text(
            'import "pkg/worker/batch.wysna" as worker\n'
            'print(worker.evaluate("[2,-1,3]") else error { error })\n', encoding='utf-8',
        )
        run('project', 'lock', '--manifest', consumer_manifest, '--offline')
        locked = (consumer / 'wysna.lock').read_bytes()
        shutil.rmtree(consumer / 'vendor')
        archive.unlink()
        run('packages', 'restore', '--manifest', consumer_manifest, '--cache', cache)
        require((consumer / 'wysna.lock').read_bytes() == locked, 'restore rewrote the frozen lock')
        imported = run('project', 'run', '--manifest', consumer_manifest, '--frozen', '--offline')
        require(json.loads(imported.stdout) == worker_output, imported.stdout)

        repair = root / 'repair.wysna'
        repair.write_text('let count = 0\ncount = 5\nprint(count)\n', encoding='utf-8')
        diagnostic = run('check', repair, '--output-format=json-v1', expected=1)
        require(any(item['code'] == 'W3001' for item in json.loads(diagnostic.stdout)['diagnostics']),
                diagnostic.stdout)
        run('fix', repair, '--apply', '--output-format=json-v1')
        run('check', repair, '--output-format=json-v1')
        require(run('run', repair).stdout.strip() == '5', 'repaired program failed its oracle')

    return {
        'schema': 'wysna.launch-acceptance/1',
        'source_commit': manifest['source_commit'],
        'projects': list(binaries),
        'package_sha256': pin,
        'package_restored_offline': True,
        'repair_diagnostic': 'W3001',
        'repair_oracle_passed': True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--examples', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    report = validate(args.sdk, args.examples)
    args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
