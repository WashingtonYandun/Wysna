#!/usr/bin/env python3
"""Qualify the declared native LLVM profile against the existing C99 corpus."""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from compiler.CompilerService import CompilerService
from compiler.LlvmToolchain import LlvmToolchain
from compiler.NativeToolchain import NativeToolchain
from error.Error import WysnaError
from tests.engine_harness import corpus_paths
from tools.ci_conformance import source_fingerprint


def execution(binary, directory, sanitize):
    environment = {**os.environ, 'WYSNA_MEM_CHECK': '1'}
    if sanitize:
        environment.update(ASAN_OPTIONS='detect_leaks=' + ('1' if sys.platform.startswith('linux') else '0'),
                           UBSAN_OPTIONS='halt_on_error=1:print_stacktrace=1')
    process = subprocess.run([str(binary)], cwd=ROOT, env=environment, capture_output=True, timeout=30)
    record = {'exit_code': process.returncode, 'stdout': process.stdout.decode('utf-8'),
              'stderr': process.stderr.decode('utf-8')}
    (directory / 'execution.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    markers = ('AddressSanitizer', 'LeakSanitizer', 'runtime error:', 'Memory leak detected')
    if any(marker in record['stderr'] for marker in markers):
        raise AssertionError('Sanitizer or ownership failure: ' + record['stderr'])
    if process.returncode < 0:
        raise AssertionError('Application terminated by signal: ' + str(process.returncode))
    return record


def qualify(output, profiles=('dev', 'release', 'size'), sanitize=False):
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Choose a new qualification directory to preserve previous evidence')
    if not profiles or len(profiles) != len(set(profiles)):
        raise ValueError('Qualification profiles must be nonempty and unique')
    output.mkdir(parents=True)
    before = source_fingerprint()
    positives, negatives = corpus_paths()
    records = []
    started = time.perf_counter()
    with tempfile.TemporaryDirectory(prefix='wysna-llvm-qualification-') as temporary:
        temporary = Path(temporary)
        for number, path in enumerate(positives, 1):
            label = path.relative_to(ROOT).as_posix()
            try:
                service = CompilerService()
                checked = service.check_file(path)
                c_source = service.emit_c(checked).source
            except Exception as error:
                records.extend({'case': label, 'profile': profile, 'sanitize': sanitize, 'status': 'failed',
                                'error': str(error)} for profile in profiles)
                print('FAIL ' + label + ': ' + str(error), flush=True)
                continue
            for profile in profiles:
                directory = output / profile / path.relative_to(ROOT).with_suffix('')
                directory.mkdir(parents=True, exist_ok=True)
                row = {'case': label, 'profile': profile, 'sanitize': sanitize}
                try:
                    reference = NativeToolchain(ROOT).build(c_source, temporary / 'reference',
                                                           profile=profile, sanitize=sanitize)
                    emission = service.emit_llvm(checked, profile=profile)
                    toolchain = LlvmToolchain(profile=profile)
                    (directory / 'program.ll').write_text(emission.source, encoding='utf-8')
                    candidate = toolchain.build(emission, temporary / 'candidate', sanitize=sanitize)
                    reference_dir, candidate_dir = directory / 'c99', directory / 'llvm'
                    reference_dir.mkdir(exist_ok=True)
                    candidate_dir.mkdir(exist_ok=True)
                    expected = execution(reference.output_path, reference_dir, sanitize)
                    actual = execution(candidate.output_path, candidate_dir, sanitize)
                    if actual != expected:
                        raise AssertionError({'c99': expected, 'llvm': actual})
                    row.update(status='passed', target=asdict(emission.target), cache_hits=candidate.cache_hits)
                except Exception as error:
                    row.update(status='failed', error=str(error))
                    print('FAIL ' + label + ' ' + profile + ': ' + str(error)[:1000], flush=True)
                records.append(row)
            print(str(number) + '/' + str(len(positives)) + ' ' + label, flush=True)
        for path in negatives:
            label = path.relative_to(ROOT).as_posix()
            row = {'case': label, 'kind': 'source-rejection'}
            try:
                service = CompilerService()
                service.emit_llvm(service.check_file(path))
            except WysnaError as error:
                row.update(status='passed' if error.code != 'W9999' else 'failed', diagnostic=error.code)
            except Exception as error:
                row.update(status='failed', error=str(error))
            else:
                row.update(status='failed', error='Invalid source was accepted')
            records.append(row)
    after = source_fingerprint()
    summary = {'schema': 'wysna.llvm-qualification/1', 'provenance': before,
               'source_unchanged': before == after, 'profiles': list(profiles), 'sanitize': sanitize,
               'positive_cases': len(positives), 'negative_cases': len(negatives),
               'rows': len(records), 'passed': sum(row['status'] == 'passed' for row in records),
               'unsupported': 0, 'failed': sum(row['status'] != 'passed' for row in records),
               'elapsed_ms': (time.perf_counter() - started) * 1000, 'results': records}
    (output / 'summary.json').write_text(json.dumps(summary, indent=2), encoding='utf-8')
    if summary['failed'] or not summary['source_unchanged']:
        raise AssertionError('LLVM qualification failed; inspect ' + str(output / 'summary.json'))
    print(json.dumps({key: value for key, value in summary.items() if key not in ('results', 'provenance')}))
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--profiles', nargs='+', choices=('dev', 'release', 'size'), default=('dev', 'release', 'size'))
    parser.add_argument('--sanitize', action='store_true')
    args = parser.parse_args()
    qualify(args.output, args.profiles, args.sanitize)


if __name__ == '__main__':
    main()
