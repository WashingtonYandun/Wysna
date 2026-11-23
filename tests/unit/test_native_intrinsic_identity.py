"""Native runtime substitutions follow trusted declarations through specialization."""
from pathlib import Path
import subprocess
import tempfile
import unittest

from compiler.Bootstrap import ensure_selfhost_compiler_binary
from common.Platform import compiler_command, executable_name

ROOT = Path(__file__).resolve().parents[2]


class NativeIntrinsicIdentityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.compiler = Path(ensure_selfhost_compiler_binary(str(ROOT)))

    def test_blank_lines_do_not_disable_async_or_parallel_intrinsics(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            stdlib = root / 'stdlib'
            stdlib.mkdir()
            for name in ('async', 'algorithms'):
                (stdlib / (name + '.wysna')).write_text('\n' * 23 +
                    (ROOT / 'stdlib' / (name + '.wysna')).read_text(encoding='utf-8'), encoding='utf-8')
            source = root / 'main.wysna'
            source.write_text('import "std/async" as a\nimport "std/algorithms" as alg\n'
                'fn slow()->int { let _ = a.wait(a.sleep_ms(80)); 1 }\n'
                'fn fast()->int { let _ = a.wait(a.sleep_ms(2)); 2 }\n'
                'let slow_task = a.spawn[int](||=>slow())\nlet fast_task = a.spawn[int](||=>fast())\n'
                'let winner = a.race[int]([slow_task,fast_task]) else err {-1}\n'
                'let mapped = alg.parallel_map[int,int]([1,2,3],|x:int|=>x*2)\n'
                'print(winner,mapped[2])\n', encoding='utf-8')
            generated = root / 'main.c'
            emitted = subprocess.run([str(self.compiler), '--stdlib-root', str(stdlib),
                str(source), str(generated)], capture_output=True, text=True, timeout=60)
            self.assertEqual(emitted.returncode, 0, emitted.stdout + emitted.stderr)
            c_source = generated.read_text(encoding='utf-8')
            self.assertIn('return wysna_parallel_map(', c_source)
            self.assertIn('wysna_async_spawn_handle(', c_source)
            binary = root / executable_name('main')
            compiled = subprocess.run([*compiler_command(), '-std=c99', '-O1', f'-I{ROOT}',
                str(generated), '-o', str(binary)], capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            run = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
            self.assertEqual(run.returncode, 0, run.stderr)
            self.assertEqual(run.stdout, '2 6\n')

    def test_user_module_with_stdlib_basename_keeps_its_function_body(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # Match the old line-based trigger while using a different declaration.
            (root / 'algorithms.wysna').write_text('\n' * 112 +
                'pub fn unrelated[T,R](values:list[T], f:fn(T)->R)->list[R]{[]}\n', encoding='utf-8')
            source = root / 'main.wysna'
            source.write_text('import "algorithms.wysna" as local\n'
                'print(len(local.unrelated[int,int]([1,2],|x:int|=>x)))\n', encoding='utf-8')
            generated = root / 'main.c'
            emitted = subprocess.run([str(self.compiler), '--stdlib-root', str(ROOT / 'stdlib'),
                str(source), str(generated)], capture_output=True, text=True, timeout=60)
            self.assertEqual(emitted.returncode, 0, emitted.stdout + emitted.stderr)
            self.assertNotIn('return wysna_parallel_map(', generated.read_text(encoding='utf-8'))
