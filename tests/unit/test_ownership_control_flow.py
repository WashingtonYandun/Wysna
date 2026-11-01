"""B01: ownership and evaluation-order regressions across native backends."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def run_ownership_tests():
    root = Path(__file__).resolve().parents[2]
    source = root / 'tests/runtime/ownership_control_flow.wysna'
    expected = source.with_suffix('.expected').read_text()
    env = dict(os.environ, WYSNA_MEM_CHECK='1')
    env['ASAN_OPTIONS'] = 'detect_leaks=1' if sys.platform.startswith('linux') else 'detect_leaks=0'
    compiler = os.environ.get('CC', 'cc')
    print(f'B01 platform={sys.platform} compiler={compiler}', flush=True)
    interpreted = subprocess.run([sys.executable, 'main.py', 'run', str(source)],
                                 cwd=root, capture_output=True, text=True, timeout=30)
    assert interpreted.returncode == 0, interpreted.stderr
    assert interpreted.stdout == expected, interpreted.stdout
    with tempfile.TemporaryDirectory(prefix='wysna-b01-') as directory:
        for backend in ['ast', 'hir', 'selfhost']:
            c_path = Path(directory) / (backend + '.c')
            binary = Path(directory) / backend
            if backend == 'selfhost':
                from main import ensure_selfhost_compiler_binary
                selfhost_bin = ensure_selfhost_compiler_binary(str(root))
                command = [selfhost_bin, str(source), str(c_path)]
            elif backend == 'ast':
                command = [sys.executable, str(root / 'tests/emit_ast_oracle.py'),
                           str(source), str(c_path)]
            else:
                command = [sys.executable, 'main.py', 'emit-c']
                command += [str(source), '-o', str(c_path)]
            emitted = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=60)
            assert emitted.returncode == 0, (backend, emitted.stderr)
            built = subprocess.run([compiler, '-std=c99', '-g', '-fsanitize=address,undefined',
                                    '-fno-omit-frame-pointer', '-I' + str(root), str(c_path), '-o', str(binary)],
                                   capture_output=True, text=True, timeout=60)
            assert built.returncode == 0, (backend, built.stderr)
            result = subprocess.run([str(binary)], cwd=root, env=env,
                                    capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, (backend, result.stderr)
            assert result.stdout == expected, (backend, result.stdout, expected)
            print(f'  [PASS] B01 {backend}: stdout, ASan/UBSan and balanced allocations', flush=True)


if __name__ == '__main__':
    run_ownership_tests()
