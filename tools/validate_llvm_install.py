"""Exercise the installed optional LLVM backend away from repository imports."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile


def main():
    with tempfile.TemporaryDirectory(prefix='wysna installed llvm ') as directory:
        root = Path(directory)
        source = root / 'hello.wysna'
        source.write_text('fn twice(value: int) -> int { value * 2 }\nprint("LLVM", twice(21))\n', encoding='utf-8')
        environment = {**os.environ, 'PYTHONPATH': '', 'WYSNA_MEM_CHECK': '1'}
        binary = root / 'hello'
        result = subprocess.run([sys.executable, '-c',
                                 'from tooling.CLI import main; main()', 'build',
                                 '--backend=llvm-experimental', '--profile=release',
                                 str(source), '-o', str(binary)], cwd=root, env=environment,
                                capture_output=True, text=True, timeout=120)
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)
        execution = subprocess.run([str(binary)], cwd=root, env=environment,
                                   capture_output=True, text=True, timeout=30)
        assert (execution.returncode, execution.stdout, execution.stderr) == (0, 'LLVM 42\n', ''), execution
        print('Installed LLVM wheel: optional tools, packaged runtime bridge, native execution and cleanup passed')


if __name__ == '__main__':
    main()
