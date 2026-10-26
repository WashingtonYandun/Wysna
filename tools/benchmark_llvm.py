#!/usr/bin/env python3
"""Measure C99 and LLVM with equal profiles, caches, inputs and retained raw samples."""
import argparse
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import platform
import resource
import re
import shlex
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from bench.run_bench import BENCHMARK_ORDER
from tools.ci_conformance import source_fingerprint

COMPARISON_CASES = (*BENCHMARK_ORDER, 'startup.wysna', 'steady_state.wysna')


def bytes_rss(usage):
    return usage.ru_maxrss if sys.platform == 'darwin' else usage.ru_maxrss * 1024


def compile_worker(source, backend, profile, output):
    from compiler.CompilerService import CompilerService
    from compiler.NativeToolchain import NativeToolchain
    from compiler.LlvmToolchain import LlvmToolchain
    source = Path(source).resolve()
    started = time.perf_counter()
    selected_sdk = None
    if os.environ.get('WYSNA_LLVM_SDK') and backend == 'c99':
        from compiler.LlvmSdk import LlvmSdk
        selected_sdk = LlvmSdk(os.environ['WYSNA_LLVM_SDK'])
        os.environ['CC'] = shlex.join(selected_sdk.compiler())
        os.environ['ZIG_GLOBAL_CACHE_DIR'] = str(selected_sdk.zig_cache())
        os.environ['ZIG_LOCAL_CACHE_DIR'] = str(Path(output).parent / 'zig-local')
    libraries = (ROOT / 'bench/clock_adapter.c',) if source.name == 'steady_state.wysna' else ()
    service = CompilerService()
    parsed = service.parse(source.read_text(encoding='utf-8'), str(source))
    checked_start = time.perf_counter()
    checked = service.check_parsed(parsed)
    checked_ms = (time.perf_counter() - checked_start) * 1000
    if backend == 'llvm-experimental':
        emission = service.emit_llvm(checked, profile=profile)
        built = LlvmToolchain(profile=profile).build(emission, output, link_libraries=libraries)
        phases = dict(emission.timing_ms)
        phases.update(built.timing_ms)
        target = asdict(emission.target)
        toolchain = built.toolchain_identity
        debug_bytes = sum(path.stat().st_size for path in Path(str(output) + '.dSYM').rglob('*') if path.is_file())
    else:
        emission = service.emit_c(checked)
        built = NativeToolchain(ROOT).build(emission.source, output, profile=profile, link_libraries=libraries)
        phases = {**emission.timing_ms, 'c_compile': built.c_compile_ms, 'link': built.link_ms}
        target = {'profile': 'native-cli', 'optimization': profile, 'triple': selected_sdk.record['triple'] if selected_sdk else None}
        toolchain = built.toolchain_identity
        debug_bytes = 0
    phases.update(lex=parsed.timing_ms['lex'], parse=parsed.timing_ms['parse'], check=checked_ms)
    return {'backend': backend, 'profile': profile, 'target': target, 'toolchain': toolchain,
            'compile_ms': (time.perf_counter() - started) * 1000, 'phase_ms': phases,
            'parent_peak_bytes': bytes_rss(resource.getrusage(resource.RUSAGE_SELF)),
            'largest_child_peak_bytes': bytes_rss(resource.getrusage(resource.RUSAGE_CHILDREN)),
            'executable_bytes': Path(output).stat().st_size, 'debug_bundle_bytes': debug_bytes,
            'cache_hits': built.cache_hits}


def measured_compile(source, backend, profile, binary, cache, output):
    environment = {**os.environ, 'WYSNA_COMPILER_CACHE_HOME': str(cache), 'WYSNA_COMPILER_CACHE': 'on'}
    command = [sys.executable, str(Path(__file__).resolve()), '--worker', str(source), '--backend', backend,
               '--profile', profile, '--binary', str(binary)]
    process = subprocess.run(command, cwd=ROOT, env=environment, capture_output=True, text=True, timeout=180)
    record = {'command': command, 'exit_code': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr}
    output.write_text(json.dumps(record, indent=2), encoding='utf-8')
    if process.returncode:
        raise AssertionError('Compile measurement failed: ' + str(output))
    result = json.loads(process.stdout)
    if process.stderr:
        raise AssertionError('Compile measurement produced unexpected stderr: ' + process.stderr)
    return result


def measured_execution(binary, output, steady=False):
    command = [sys.executable, str(ROOT / 'tools/measure_process.py'), '--', str(binary)]
    process = subprocess.run(command, capture_output=True, text=True, timeout=180,
                             env={**os.environ, 'WYSNA_MEM_CHECK': '1'})
    output.write_text(json.dumps({'exit_code': process.returncode, 'stdout': process.stdout, 'stderr': process.stderr}, indent=2), encoding='utf-8')
    if process.returncode:
        raise AssertionError('Execution measurement failed: ' + process.stderr)
    result = json.loads(process.stdout)
    output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    if steady:
        timing = re.fullmatch(r'WYSNA_BENCHMARK_NS ([0-9]+)\n', result['stderr'])
        if timing is None or int(timing[1]) <= 0:
            raise AssertionError('Missing or invalid internal monotonic workload timing')
        result['workload_ms'] = int(timing[1]) / 1000000
        result['raw_stderr'], result['stderr'] = result['stderr'], ''
        output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    if result['exit_code'] or result['stderr']:
        raise AssertionError(result)
    return result


def decision(rows, policy):
    violations = []
    for row in rows:
        for metric, limit in policy['limits'].items():
            if metric in row['ratios'] and row['ratios'][metric] > limit:
                violations.append({'case': row['case'], 'profile': row['profile'], 'metric': metric,
                                   'ratio': row['ratios'][metric], 'limit': limit})
    return {'backend_default': 'c99', 'candidate_status': 'qualified-opt-in',
            'automatic_promotion': False, 'budget_violations': violations,
            'reason': 'Default promotion is a separate decision and requires equivalent installed dependency contracts; '
                      'all measured regressions are retained even when the candidate stays opt-in.'}


def require_qualification(qualified, provenance, profiles):
    from tests.engine_harness import corpus_paths
    positives, negatives = corpus_paths()
    selected = qualified.get('profiles', [])
    if len(selected) != len(set(selected)) or not set(profiles) <= set(selected):
        raise ValueError('Qualification does not cover the requested optimization profiles')
    expected = {(path.relative_to(ROOT).as_posix(), profile) for path in positives for profile in selected}
    expected.update((path.relative_to(ROOT).as_posix(), 'source-rejection') for path in negatives)
    rows = qualified.get('results', [])
    observed = [(row.get('case'), row.get('profile', row.get('kind'))) for row in rows]
    if (qualified.get('schema') != 'wysna.llvm-qualification/1' or qualified.get('failed') != 0
            or qualified.get('unsupported') != 0 or not qualified.get('source_unchanged')
            or qualified.get('provenance') != provenance or set(observed) != expected
            or len(observed) != len(expected) or qualified.get('rows') != len(expected)
            or qualified.get('passed') != len(expected) or any(row.get('status') != 'passed' for row in rows)
            or qualified.get('positive_cases') != len(positives) or qualified.get('negative_cases') != len(negatives)):
        raise ValueError('Qualification is incomplete, failed, modified or stale for this source digest')


def campaign(output, qualification, profiles, compile_samples=5, runtime_samples=15, cases=None):
    if not profiles or len(profiles) != len(set(profiles)):
        raise ValueError('Measurement profiles must be nonempty and unique')
    if compile_samples < 3 or runtime_samples < 5:
        raise ValueError('Use at least three compile and five runtime samples')
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Choose a new measurement directory to retain previous evidence')
    output.mkdir(parents=True)
    before = source_fingerprint()
    qualified = json.loads(Path(qualification).read_text(encoding='utf-8'))
    require_qualification(qualified, before, profiles)
    policy_path = ROOT / 'bench/llvm_policy.json'
    policy = json.loads(policy_path.read_text(encoding='utf-8'))
    rows = []
    for name in cases or COMPARISON_CASES:
        source = ROOT / 'bench' / name
        for profile in profiles:
            row = {'case': name, 'kind': 'startup' if name == 'startup.wysna' else 'steady' if name == 'steady_state.wysna' else 'workload', 'source_sha256': hashlib.sha256(source.read_bytes()).hexdigest(), 'profile': profile, 'engines': {}}
            expected_stdout = None
            for backend in ('c99', 'llvm-experimental'):
                directory = output / source.stem / profile / backend
                directory.mkdir(parents=True)
                binary = directory / 'program'
                measured_compile(source, backend, profile, binary, directory / 'toolchain-warmup-cache',
                                 directory / 'toolchain-warmup.json')
                cold = []
                for sample in range(compile_samples):
                    cold.append(measured_compile(source, backend, profile, binary, directory / ('cold-cache-' + str(sample)),
                                                 directory / ('cold-' + str(sample) + '.json')))
                warm_cache = directory / 'warm-cache'
                measured_compile(source, backend, profile, binary, warm_cache, directory / 'compile-warmup.json')
                warm = []
                for sample in range(compile_samples):
                    warm.append(measured_compile(source, backend, profile, binary, warm_cache,
                                                 directory / ('warm-' + str(sample) + '.json')))
                if any(any(item['cache_hits'].values()) for item in cold):
                    raise AssertionError('Cold measurements unexpectedly reused Wysna artifacts')
                if any(not item['cache_hits'] or not all(item['cache_hits'].values()) for item in warm):
                    raise AssertionError('Warm measurements did not reuse every Wysna object')
                measured_execution(binary, directory / 'runtime-warmup.json', row['kind'] == 'steady')
                executions = []
                for sample in range(runtime_samples):
                    execution = measured_execution(binary, directory / ('runtime-' + str(sample) + '.json'), row['kind'] == 'steady')
                    if expected_stdout is None:
                        expected_stdout = execution['stdout']
                    if execution['stdout'] != expected_stdout:
                        raise AssertionError('Benchmark output diverged: ' + name + ' ' + profile + ' ' + backend)
                    executions.append(execution)
                cold_times = [item['compile_ms'] for item in cold]
                warm_times = [item['compile_ms'] for item in warm]
                run_times = [item['elapsed_seconds'] * 1000 for item in executions]
                parent_peaks = [item['parent_peak_bytes'] for item in cold]
                runtime_peaks = [item['peak_memory_bytes'] for item in executions]
                row['engines'][backend] = {'cold_compile_median_ms': statistics.median(cold_times),
                                          'warm_compile_median_ms': statistics.median(warm_times),
                                          'runtime_median_ms': statistics.median(run_times),
                                          'compiler_parent_peak_bytes': max(parent_peaks),
                                          'runtime_peak_bytes': max(runtime_peaks),
                                          'executable_bytes': binary.stat().st_size,
                                          'compile_samples': {'cold': cold, 'warm': warm},
                                          'runtime_samples': executions}
                if row['kind'] == 'steady':
                    work_samples = [item['workload_ms'] for item in executions]
                    row['engines'][backend]['steady_work_median_ms'] = statistics.median(work_samples)
            baseline, candidate = row['engines']['c99'], row['engines']['llvm-experimental']
            metrics = {'runtime_median_ratio': 'runtime_median_ms', 'cold_compile_median_ratio': 'cold_compile_median_ms',
                       'warm_compile_median_ratio': 'warm_compile_median_ms', 'executable_bytes_ratio': 'executable_bytes',
                       'compiler_parent_peak_bytes_ratio': 'compiler_parent_peak_bytes', 'runtime_peak_bytes_ratio': 'runtime_peak_bytes'}
            row['ratios'] = {ratio: candidate[metric] / baseline[metric] for ratio, metric in metrics.items()}
            if row['kind'] == 'steady':
                row['ratios']['steady_work_median_ratio'] = candidate['steady_work_median_ms'] / baseline['steady_work_median_ms']
            rows.append(row)
            print(name + ' ' + profile + ' ' + json.dumps(row['ratios']), flush=True)
    after = source_fingerprint()
    report = {'schema': 'wysna.llvm-comparison/1', 'provenance': before, 'source_unchanged': before == after,
              'distribution': {'sdk_manifest': json.loads((Path(os.environ['WYSNA_LLVM_SDK']) / 'llvm-sdk.json').read_text()) if os.environ.get('WYSNA_LLVM_SDK') else None,
                               'mode': 'seed-sdk' if os.environ.get('WYSNA_LLVM_SDK') else 'seed-host-tools'},
              'host': {'system': platform.system(), 'machine': platform.machine(), 'python': platform.python_version()},
              'protocol': {'compile_samples': compile_samples, 'runtime_samples': runtime_samples, 'warmup_per_phase': 1, 'toolchain_cache_warmup': 1,
                           'cold_definition': 'New Wysna artifact cache; OS page cache is not flushed',
                           'warm_definition': 'Both backends reuse checksum- and dependency-validated object caches',
                           'timing_scope': 'Compile worker excludes Python startup; runtime includes native process launch',
                           'memory_scope': 'Parent compiler peak and largest child peak are separate; not aggregate process-tree RSS'},
              'policy_sha256': hashlib.sha256(policy_path.read_bytes()).hexdigest(), 'policy': policy,
              'rows': rows, 'decision': decision(rows, policy)}
    (output / 'comparison.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    if not report['source_unchanged']:
        raise AssertionError('Sources changed during measurement; keep this evidence as invalid and rerun')
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--qualification', type=Path)
    parser.add_argument('--profiles', nargs='+', choices=('dev', 'release', 'size'), default=('dev', 'release', 'size'))
    parser.add_argument('--compile-samples', type=int, default=5)
    parser.add_argument('--runtime-samples', type=int, default=15)
    parser.add_argument('--cases', nargs='+', choices=COMPARISON_CASES)
    parser.add_argument('--worker', type=Path, help=argparse.SUPPRESS)
    parser.add_argument('--backend', choices=('c99', 'llvm-experimental'), help=argparse.SUPPRESS)
    parser.add_argument('--profile', choices=('dev', 'release', 'size'), help=argparse.SUPPRESS)
    parser.add_argument('--binary', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.worker:
        print(json.dumps(compile_worker(args.worker, args.backend, args.profile, args.binary)))
    elif args.output and args.qualification:
        campaign(args.output, args.qualification, args.profiles, args.compile_samples, args.runtime_samples, args.cases)
    else:
        parser.error('--output and --qualification are required')


if __name__ == '__main__':
    main()
