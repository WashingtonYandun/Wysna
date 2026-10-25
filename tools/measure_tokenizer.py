#!/usr/bin/env python3
"""Measure the actual Wysna Compiler lexer with owned UTF-8 source at geometric input sizes."""
import argparse
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]


def measure(samples=5):
    prefix = 'struct Token { kind: str, val: str, line: int, col: int, file: str }\nlet mut g_in_path = ""\nlet mut g_diag_file = ""\n'
    source = prefix + (ROOT / 'selfhost/src/02_lexer.wysna').read_text()
    source += '\nfn measure() -> unit ! IoError { let text = read_file(args()[1])?\nprint(len(tokenize(text, "measure.wysna"))) }\nmeasure() else err { print(err.message) }\n'
    report = {'samples': samples, 'platform': sys.platform, 'compiler': os.environ.get('CC', 'cc'),
              'workload': 'unmodified Wysna Compiler tokenizer; alpha + beta newline; owned source read_file', 'rows': []}
    with tempfile.TemporaryDirectory(prefix='wysna-tokenizer-scaling-') as temporary:
        directory = Path(temporary)
        path, binary = directory / 'lexer.wysna', directory / 'lexer'
        path.write_text(source)
        subprocess.run([sys.executable, str(ROOT / 'main.py'), 'build', str(path), '-o', str(binary)],
                       cwd=ROOT, check=True, capture_output=True, timeout=120)
        for repetitions in (500, 1000, 2000, 4000, 8000, 16000):
            corpus = directory / 'input.txt'
            corpus.write_text('alpha + beta\n' * repetitions)
            durations = []
            for sample in range(samples + 1):
                started = time.perf_counter()
                result = subprocess.run([str(binary), str(corpus)], capture_output=True, text=True,
                                        env=dict(os.environ, WYSNA_MEM_CHECK='1'), check=True, timeout=30)
                elapsed = (time.perf_counter() - started) * 1000
                assert not result.stderr, result.stderr
                assert int(result.stdout) == repetitions * 4 + 1, result.stdout
                if sample:
                    durations.append(elapsed)
            report['rows'].append({'bytes': corpus.stat().st_size, 'tokens': int(result.stdout),
                                   'median_ms': statistics.median(durations), 'samples_ms': durations})
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--samples', type=int, default=5)
    parser.add_argument('--report', type=Path, default=ROOT / '.wysna_cache/tokenizer-scaling.json')
    arguments = parser.parse_args()
    if not 1 <= arguments.samples <= 100:
        parser.error('samples must be 1..100')
    report = measure(arguments.samples)
    arguments.report.parent.mkdir(parents=True, exist_ok=True)
    arguments.report.write_text(json.dumps(report, indent=2))
    for row in report['rows']:
        print(f"{row['bytes']} bytes: {row['median_ms']:.3f} ms ({row['tokens']} tokens)")
    print(f'Report: {arguments.report}')


if __name__ == '__main__':
    main()
