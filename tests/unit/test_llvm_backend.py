"""Executable parity and failure contracts for the optional LLVM source profile.

Run explicitly with the pinned LLVM extra; missing LLVM is a test failure.
"""
import builtins
import importlib.util
from contextlib import redirect_stdout, redirect_stderr
from dataclasses import replace
import io
import json
import os
import platform
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from common.Platform import compiler_command
from compiler.CompilerService import CompilerService
from compiler.LlvmToolchain import LlvmToolchain, protect_output
from compiler.NativeToolchain import NativeCompilationError, NativeToolchain
from error.Error import WysnaError
from mir.LlvmCodeGen import LlvmCodeGen
from mir.Nodes import dump
from tooling.SourceCommand import run_pipeline

ROOT = Path(__file__).resolve().parents[2]
PROGRAMS = {
    'control': '''
let mut count = 0
fn tick() -> bool { count = count + 1; true }
fn fib(n: int) -> int { if n < 2 { n } else { fib(n - 1) + fib(n - 2) } }
let a = false and tick()
let b = true or tick()
print(a, b, count, fib(10))
let mut total = 0
for x in -3..=3 { if x == 0 { continue }; total = total + x; if x == 2 { break } }
let mut n = 0
while n < 3 { n = n + 1; total = total + n }
print(total)
''',
    'integers': '''
let max = 9223372036854775807
let min = -9223372036854775808
print(max + 1, min - 1, max * 2, -min)
print(min / -1, min % -1, -7 / 3, -7 % 3)
print(7 < 8, 7 <= 7, 8 > 7, 8 >= 8, 7 == 7, 7 != 8)
''',
    'strings': '''
fn decorate(value: str) -> str { "[" + value + "]" }
fn recurse(n: int, value: str) -> str {
    if n == 0 { value } else { recurse(n - 1, value + "x") }
}
let mut n = 0
let mut text = ""
while n < 30 {
    text = if n % 2 == 0 { decorate(str(n)) } else { recurse(3, "hé") }
    n = n + 1
}
print(text, len(text), chr(233), ord("é"))
print("a\0b", len("a\0b"), "é" == "é", "same" == "same", "a" != "b")
print(int("42"), int(3.9), float("2.5"), str(true))
eprint("stderr", decorate("ok"))
''',
    'floats': '''
let x = float("nan")
let inf = float("inf")
print(x == x, x != x, x < 0.0, x <= 0.0, x > 0.0, x >= 0.0)
print(inf > 0.0, -inf < 0.0, -0.0 == 0.0)
print(2.5 + 1.5, 2.5 - 1.0, 2.5 * 2.0, 5.0 / 2.0, -2.5)
''',
    'unit': '''
fn nothing(value: unit) -> unit { value }
let value = nothing(print())
print(value, value == nothing(print()), not false, true == false)
print()
''',
}


@unittest.skipUnless((importlib.util.find_spec('llvmlite') is not None or os.environ.get('WYSNA_LLVM_SDK')) and
                     ((platform.system() == 'Darwin' and platform.machine().lower() in ('arm64', 'aarch64'))
                      or (platform.system() == 'Linux' and platform.machine().lower() in ('x86_64', 'amd64'))),
                     'Optional LLVM tools and a supported host are required; run this module explicitly to validate setup')
class LlvmBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.toolchain = LlvmToolchain()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='wysna llvm ü ')
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name).resolve()

    def checked(self, source):
        file = self.directory / 'main.wysna'
        file.write_text(source, encoding='utf-8')
        service = CompilerService(project_root=str(self.directory))
        return service, service.check_file(file)

    def execute(self, path):
        return subprocess.run([str(path)], capture_output=True, timeout=20,
                              env={**os.environ, 'WYSNA_MEM_CHECK': '1'})

    def c99(self, service, checked):
        result = NativeToolchain(ROOT).build(service.emit_c(checked).source, self.directory / 'c99')
        return self.execute(result.output_path)

    def test_executable_parity_at_every_optimization_level(self):
        for name, source in PROGRAMS.items():
            service, checked = self.checked(source)
            reference = self.c99(service, checked)
            self.assertEqual(reference.returncode, 0, (name, reference.stderr))
            output = io.StringIO()
            error = io.StringIO()
            with redirect_stderr(error):
                service.interpret(checked, stdout=output)
            self.assertEqual(reference.stdout.decode(), output.getvalue(), name)
            for profile in ('dev', 'release', 'size'):
                with self.subTest(program=name, profile=profile):
                    emission = service.emit_llvm(checked, profile=profile)
                    toolchain = LlvmToolchain(profile=profile)
                    result = toolchain.build(emission, self.directory / ('llvm-' + profile))
                    actual = self.execute(result.output_path)
                    self.assertEqual((actual.returncode, actual.stdout, actual.stderr),
                                     (reference.returncode, reference.stdout, reference.stderr))
                    self.assertGreaterEqual(result.timing_ms['object_emit'], 0)
                    self.assertEqual(result.timing_ms['object_emit'] == 0, result.cache_hits['program'])
                    self.assertTrue(result.c_compiler_version)

    def test_prelude_error_literals_match_c99(self):
        source = ('fn make() -> str ! NetError { return Err(NetError { message: "offline" }) }\n'
                  'let io = IoError { message: "io" }\n'
                  'print(make() else error { error.message }, io.message)\n')
        service, checked = self.checked(source)
        reference = self.c99(service, checked)
        self.assertEqual((reference.returncode, reference.stdout), (0, b'offline io\n'))
        emission = service.emit_llvm(checked, toolchain=self.toolchain)
        built = self.toolchain.build(emission, self.directory / 'llvm-prelude-errors')
        actual = self.execute(built.output_path)
        self.assertEqual((actual.returncode, actual.stdout, actual.stderr),
                         (reference.returncode, reference.stdout, reference.stderr))

    def test_explicit_application_exit_matches_c99(self):
        cases = [
            (0, 'print("before")\nexit(0)\nprint("unreachable")\n', b'before\n'),
            (7, 'print("before")\nexit(7)\nprint("unreachable")\n', b'before\n'),
            (7, 'fn stop() { let values = [1, 2]\nprint(values)\nexit(7) }\nstop()\n', b'[1, 2]\n'),
        ]
        for code, source, expected in cases:
            service, checked = self.checked(source)
            reference = self.c99(service, checked)
            self.assertEqual((reference.returncode, reference.stdout), (code, expected))
            emission = service.emit_llvm(checked)
            build = self.toolchain.build(emission, self.directory / 'exit-program')
            actual = self.execute(build.output_path)
            self.assertEqual((actual.returncode, actual.stdout, actual.stderr),
                             (reference.returncode, reference.stdout, reference.stderr))

    def test_runtime_faults_match_c99_including_source_locations(self):
        for expression in ('4 / 0', '4 % 0', '4.0 / -0.0', 'chr(1 / 1 + 1114112)'):
            service, checked = self.checked('fn fail() -> unit { print(' + expression + ') }\nfail()\n')
            reference = self.c99(service, checked)
            self.assertNotEqual(reference.returncode, 0)
            self.assertIn(b'W4', reference.stderr)
            for profile in ('dev', 'release', 'size'):
                with self.subTest(expression=expression, profile=profile):
                    emission = service.emit_llvm(checked, profile=profile)
                    build = LlvmToolchain(profile=profile).build(emission, self.directory / 'fault')
                    actual = self.execute(build.output_path)
                    self.assertEqual((actual.returncode, actual.stdout, actual.stderr),
                                     (reference.returncode, reference.stdout, reference.stderr))

    def test_existing_scalar_corpus_and_range_limits(self):
        for name in ('arithmetic', 'comparisons', 'recursion', 'generic_function',
                     'audit_bool_conversion', 'range_loop', 'range_inclusive',
                     'mir_range_overflow', 'mir_range_limit'):
            with self.subTest(fixture=name):
                service = CompilerService()
                checked = service.check_file(ROOT / 'tests/runtime' / (name + '.wysna'))
                reference = self.c99(service, checked)
                emission = service.emit_llvm(checked, profile='release')
                build = LlvmToolchain(profile='release').build(emission, self.directory / 'corpus')
                actual = self.execute(build.output_path)
                self.assertEqual((actual.returncode, actual.stdout, actual.stderr),
                                 (reference.returncode, reference.stdout, reference.stderr))

    def test_runtime_bridge_layout_and_sanitizer_contract(self):
        service, checked = self.checked(PROGRAMS['strings'])
        emission = service.emit_llvm(checked)
        object_bytes, _ = self.toolchain.object(emission.source)
        program = self.directory / 'program.o'
        program.write_bytes(object_bytes)
        runtime = ROOT / 'runtime/wysna_llvm_runtime.c'
        compiler = compiler_command()
        common = self.toolchain.host_flags + ['-std=c99', '-O1', '-g', '-I' + str(ROOT),
                                              '-DWYSNA_LLVM_EXPECTED_ABI=2', '-DWYSNA_LLVM_LITERAL_BYTES=48', '-DWYSNA_LLVM_ALIGNMENT=8']
        invalid = subprocess.run(compiler + common + ['-DWYSNA_LLVM_VALUE_BYTES=8', '-c', str(runtime),
                                                       '-o', str(self.directory / 'bad.o')],
                                 capture_output=True, text=True, timeout=60)
        self.assertNotEqual(invalid.returncode, 0)
        self.assertIn('WysnaLlvmValueSize', invalid.stderr)
        output = self.directory / 'sanitized'
        result = subprocess.run(compiler + common + ['-DWYSNA_LLVM_VALUE_BYTES=32',
                                '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                                str(runtime), str(program), '-lm', '-o', str(output)],
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr)
        expected = self.c99(service, checked)
        actual = self.execute(output)
        self.assertEqual((actual.returncode, actual.stdout, actual.stderr),
                         (expected.returncode, expected.stdout, expected.stderr))

    def test_debug_symbols_survive_temporary_object_cleanup(self):
        service, checked = self.checked('fn twice(value: int) -> int { value * 2 }\nprint(twice(21))\n')
        emission = service.emit_llvm(checked)
        build = self.toolchain.build(emission, self.directory / 'debug-program')
        self.assertFalse(list(self.directory.glob('.wysna-llvm-*')))
        if platform.system() == 'Darwin':
            self.assertTrue(Path(build.debug_symbols).is_file())
            verified = subprocess.run(['xcrun', 'dwarfdump', '--verify', build.debug_symbols],
                                      capture_output=True, text=True, timeout=30)
            self.assertEqual(verified.returncode, 0, verified.stderr + verified.stdout)
            lookup = subprocess.run(['lldb', '--batch', '-o', 'target create "' + build.output_path + '"',
                                     '-o', 'breakpoint set --file main.wysna --line 1'],
                                    capture_output=True, text=True, timeout=30)
            self.assertEqual(lookup.returncode, 0, lookup.stderr + lookup.stdout)
            self.assertNotIn('no locations', lookup.stdout)
            self.assertNotIn('pending', lookup.stdout)
            self.assertIn('Breakpoint 1:', lookup.stdout)
        else:
            lines = subprocess.run(['readelf', '--debug-dump=decodedline', build.output_path],
                                   capture_output=True, text=True, timeout=30)
            self.assertEqual(lines.returncode, 0, lines.stderr)
            self.assertIn('main.wysna', lines.stdout)
        # Rebuilding replaces the matching bundle together with the binary.
        self.toolchain.build(emission, self.directory / 'debug-program')
        self.assertEqual(self.execute(build.output_path).stdout, b'42\n')

    def test_import_snapshot_and_ir_consumer_isolation(self):
        library = self.directory / 'library.wysna'
        library.write_text('pub fn wrap(value: str) -> str { "<" + value + ">" }\n')
        service, checked = self.checked('import "library.wysna"\nprint(library.wrap("ok"))\n')
        module = service.lower_mir(checked)
        before = dump(module)
        generator = LlvmCodeGen(checked._interner)
        options = {'triple': self.toolchain.target.triple, 'data_layout': self.toolchain.target.data_layout}
        first = generator.generate(module, **options)
        self.assertEqual(first, generator.generate(module, **options))
        self.assertEqual(dump(module), before)
        self.assertIn('!DIFile(filename: "library.wysna"', first)
        self.assertIn('!DILocation(line:', first)
        self.assertNotIn('nsw ', first)
        self.assertNotIn('nuw ', first)
        emission = service.emit_llvm(checked)
        self.assertIn(str(library), emission.source_files)
        binary = self.toolchain.build(emission, self.directory / 'import')
        self.assertEqual(self.execute(binary.output_path).stdout, b'<ok>\n')
        alias = self.directory / 'dependency-output'
        alias.hardlink_to(library)
        original = library.read_bytes()
        for output in (library, alias):
            with self.assertRaises(WysnaError) as caught:
                self.toolchain.build(emission, output)
            self.assertEqual(caught.exception.code, 'W5002')
        self.assertEqual(library.read_bytes(), original)

    def test_llvm_codegen_optimizations_and_metadata(self):
        service, checked = self.checked('let a = 100 / 2\nprint(a)\n')
        emission = service.emit_llvm(checked)
        self.assertIn('!prof', emission.source)
        self.assertIn('!"branch_weights", i32 1, i32 1048576', emission.source)
        self.assertIn('declare i64 @wysna_llvm_int(ptr nocapture readonly) readonly', emission.source)
        self.assertIn('declare void @wysna_llvm_fail(ptr nocapture readonly, ptr nocapture readonly, ptr nocapture readonly, i32, i32) cold noreturn', emission.source)
        self.assertIn('declare i32 @wysna_llvm_range_count(i64, i64, i32) readnone', emission.source)
        self.assertIn('align 8', emission.source)
        self.toolchain.verify(emission.source)

    def test_complete_mir_profile_never_falls_back(self):
        for source in ('print([1, 2])', 'class Box { value: int }\nprint(1)',
                       'print([1, 2, 3][0])'):
            service, checked = self.checked(source)
            with patch.object(service, 'emit_c', side_effect=AssertionError('C fallback')):
                emission = service.emit_llvm(checked)
            self.toolchain.verify(emission.source)
        module = service.lower_mir(checked)
        module.library_mode = True
        with self.assertRaises(WysnaError) as caught:
            LlvmCodeGen(checked._interner).generate(module, triple=self.toolchain.target.triple,
                                                   data_layout=self.toolchain.target.data_layout)
        self.assertEqual(caught.exception.code, 'W2013')

    def test_malformed_mir_and_llvm_are_controlled_errors(self):
        service, checked = self.checked('print(1 + 2)')
        module = service.lower_mir(checked)
        module.functions[0].blocks[0].operations[0].opcode = 'invalid'
        with self.assertRaises(WysnaError) as caught:
            LlvmCodeGen(checked._interner).generate(module, triple=self.toolchain.target.triple,
                                                   data_layout=self.toolchain.target.data_layout)
        self.assertEqual(caught.exception.code, 'W9999')
        with self.assertRaises(NativeCompilationError):
            self.toolchain.verify('define void @broken( {')
        emission = service.emit_llvm(checked)
        with self.assertRaises(NativeCompilationError):
            self.toolchain.verify(emission.source.replace(self.toolchain.target.triple, 'wasm32-unknown-unknown'))

    def test_failed_build_preserves_existing_output(self):
        service, checked = self.checked('print(42)')
        emission = service.emit_llvm(checked)
        output = self.directory / 'existing'
        output.write_bytes(b'previous artifact')
        with patch('compiler.LlvmToolchain.compiler_command', return_value=['/missing/wysna-cc']):
            with self.assertRaises(NativeCompilationError):
                self.toolchain.build(emission, output)
        self.assertEqual(output.read_bytes(), b'previous artifact')
        self.assertFalse(list(self.directory.glob('.wysna-llvm-*')))
        with self.assertRaises(WysnaError):
            protect_output(checked.source.file, emission.source_files)
        with self.assertRaises(NativeCompilationError):
            self.toolchain.write_ir(replace(emission, source='invalid'), output)
        self.assertEqual(output.read_bytes(), b'previous artifact')

    def test_debug_publication_failure_preserves_previous_artifacts(self):
        if platform.system() != 'Darwin':
            with patch('compiler.LlvmToolchain.platform.system', return_value='Windows'):
                with self.assertRaises(WysnaError) as caught:
                    LlvmToolchain()
            self.assertEqual(caught.exception.code, 'W2013')
            return
        service, checked = self.checked('print(42)')
        emission = service.emit_llvm(checked)
        output = self.directory / 'published'
        first = self.toolchain.build(emission, output)
        executable = output.read_bytes()
        symbols = Path(first.debug_symbols).read_bytes()
        run = subprocess.run
        def reject_symbols(command, **options):
            if command[0] == 'xcrun':
                return subprocess.CompletedProcess(command, 1, stdout='', stderr='debug tool unavailable')
            return run(command, **options)
        with patch('compiler.LlvmToolchain.subprocess.run', side_effect=reject_symbols):
            with self.assertRaises(NativeCompilationError):
                self.toolchain.build(emission, output)
        self.assertEqual(output.read_bytes(), executable)
        self.assertEqual(Path(first.debug_symbols).read_bytes(), symbols)
        collision = self.directory / 'collision'
        bundle = Path(str(collision) + '.dSYM')
        bundle.mkdir()
        note = bundle / 'user-file'
        note.write_text('preserve this directory')
        with self.assertRaises(WysnaError):
            self.toolchain.build(emission, collision)
        self.assertEqual(note.read_text(), 'preserve this directory')
        self.assertFalse(collision.exists())

    def test_optional_dependency_and_version_are_explicit(self):
        original = builtins.__import__
        def without_llvm(name, *args, **kwargs):
            if name.startswith('llvmlite'):
                raise ImportError('LLVM intentionally unavailable')
            return original(name, *args, **kwargs)
        with patch('builtins.__import__', side_effect=without_llvm):
            service, checked = self.checked('print(1)')
            self.assertIn('wysna_program', service.emit_c(checked).source)
            with self.assertRaises(NativeCompilationError) as caught:
                service.emit_llvm(checked)
            self.assertIn('optional LLVM', caught.exception.stderr)
        with patch('llvmlite.__version__', '0.0.0'):
            with self.assertRaises(NativeCompilationError):
                LlvmToolchain()

    def cli(self, *args):
        return subprocess.run([sys.executable, str(ROOT / 'main.py'), *map(str, args)],
                              capture_output=True, text=True, timeout=60, cwd=ROOT)

    def test_unusable_compiler_cache_is_a_filesystem_error(self):
        _, checked = self.checked('print("hello")')
        cache_file = self.directory / 'not-a-directory'
        cache_file.write_text('occupied', encoding='utf-8')
        with patch.dict(os.environ, {'WYSNA_COMPILER_CACHE_HOME': str(cache_file),
                                     'WYSNA_COMPILER_CACHE': 'on'}):
            for json_mode in (False, True):
                args = ['build', '--backend=llvm-experimental', checked.source.file,
                        '-o', self.directory / 'program']
                if json_mode:
                    args.append('--output-format=json-v1')
                result = self.cli(*args)
                self.assertEqual(result.returncode, 3, (result.stdout, result.stderr))
                if json_mode:
                    payload = json.loads(result.stdout)
                    self.assertEqual(payload['status'], 'toolchain_error')
                    self.assertEqual(payload['diagnostics'][0]['code'], 'W5004')
                    self.assertEqual(result.stderr, '')
                else:
                    self.assertIn('[W5004]', result.stderr)
                    self.assertNotIn('[Internal Error]', result.stderr)

    def test_cli_outputs_and_rejected_combinations(self):
        service, checked = self.checked('print("CLI", 42)')
        source = checked.source.file
        for command in ('check', 'emit-llvm', 'build'):
            with self.subTest(command=command):
                output = self.directory / ('artifact.ll' if command == 'emit-llvm' else 'artifact')
                args = [command, '--backend=llvm-experimental', '--output-format=json-v1', source]
                if command != 'check':
                    args.extend(['-o', output])
                result = self.cli(*args)
                self.assertEqual(result.returncode, 0, result.stderr + result.stdout)
                envelope = json.loads(result.stdout)
                self.assertEqual(envelope['target']['profile'], 'llvm-native-v1')
                self.assertEqual(envelope['status'], 'ok')
                self.assertEqual(result.stderr, '')
                if command == 'build':
                    self.assertEqual(self.execute(output).stdout, b'CLI 42\n')
                if command == 'emit-llvm':
                    self.toolchain.verify(output.read_text())
        for args in ([ 'run', '--backend=llvm-experimental'],
                     ['emit-c', '--backend=llvm-experimental'],
                     ['check', '--backend=llvm-experimental', '--sanitize'],
                     ['check', '--backend=llvm-experimental', '--link-lib', 'foreign'],
                     ['check', '--profile=release'], ['emit-llvm', '--backend=c99'],
                     ['build', '--backend=llvm-experimental', '--profile=fast']):
            with self.subTest(args=args):
                result = self.cli(*args, source, '--output-format=json-v1')
                self.assertEqual(result.returncode, 2, result.stderr + result.stdout)
                self.assertEqual(json.loads(result.stdout)['diagnostics'][0]['code'], 'W5002')
        result = self.cli('check', '--backend=llvm-experimental', '--stats', source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)['target']['llvm_version'], [20, 1, 8])
        with redirect_stderr(io.StringIO()):
            self.assertEqual(run_pipeline('print(1)', backend='llvm-experimental', engine='compiler'), 1)

    def test_cli_reuses_one_validated_provider_for_emission_and_linking(self):
        calls = []
        original = LlvmToolchain.__init__
        def configure(toolchain, *args, **kwargs):
            calls.append(kwargs.get('profile', 'dev'))
            original(toolchain, *args, **kwargs)
        output = self.directory / 'single-provider'
        with patch.object(LlvmToolchain, '__init__', configure), redirect_stdout(io.StringIO()):
            self.assertEqual(run_pipeline('print(42)', backend='llvm-experimental',
                                          build_bin_path=str(output)), 0)
        self.assertEqual(calls, ['dev'])
        self.assertEqual(self.execute(output).stdout, b'42\n')

    def test_optional_arguments_use_callee_representation(self):
        source = '''fn add(first: int?, second: int?) -> int {
    (first ?? 0) + (second ?? 0)
}
print(add(20, 22), add(none, 42), add(42, none))
'''
        service, checked = self.checked(source)
        expected = self.c99(service, checked)
        for profile in ('dev', 'release', 'size'):
            emission = service.emit_llvm(checked, profile=profile)
            build = LlvmToolchain(profile=profile).build(emission, self.directory / 'optional', sanitize=True)
            actual = self.execute(build.output_path)
            self.assertEqual((actual.returncode, actual.stdout, actual.stderr),
                             (expected.returncode, expected.stdout, expected.stderr))
        self.assertEqual(expected.stdout, b'42 42 42\n')

    def test_foreign_resources_and_scalar_abi(self):
        for name, libraries in (('scalars', ()), ('resources', (ROOT / 'tests/ffi/resource_adapter.c',))):
            service = CompilerService()
            checked = service.check_file(ROOT / 'tests/ffi' / (name + '.wysna'))
            reference = NativeToolchain(ROOT).build(service.emit_c(checked).source, self.directory / 'reference',
                                                   link_libraries=libraries, sanitize=True)
            emission = service.emit_llvm(checked)
            with patch.object(service, 'emit_c', side_effect=AssertionError('Program C fallback')):
                build = self.toolchain.build(emission, self.directory / 'foreign', link_libraries=libraries, sanitize=True)
            expected, actual = self.execute(reference.output_path), self.execute(build.output_path)
            self.assertEqual((actual.returncode, actual.stdout, actual.stderr),
                             (expected.returncode, expected.stdout, expected.stderr))
            self.assertEqual(actual.returncode, 0, actual.stderr)

    def test_sanitizer_detects_errors_in_llvm_program_instructions(self):
        service, checked = self.checked('print(42)')
        emission = service.emit_llvm(checked)
        source = '\n'.join(['target triple = "' + emission.target.triple + '"',
                            'target datalayout = "' + emission.target.data_layout + '"',
                            'define i32 @main() {',
                            '  %buffer = alloca [8 x i8], align 8',
                            '  %outside = getelementptr i8, ptr %buffer, i64 16',
                            '  store volatile i8 42, ptr %outside', '  ret i32 0', '}'])
        probe = replace(emission, source=source)
        build = self.toolchain.build(probe, self.directory / 'sanitizer-probe', sanitize=True)
        actual = self.execute(build.output_path)
        self.assertNotEqual(actual.returncode, 0)
        self.assertIn(b'AddressSanitizer', actual.stderr)
        self.assertIn(b'stack-buffer-overflow', actual.stderr)

    def test_cache_hits_and_corruption_rebuild(self):
        from compiler.ArtifactCache import ArtifactCache
        cache = ArtifactCache('test', root=self.directory / 'cache')
        header = self.directory / 'dependency.h'
        header.write_text('first')
        calls = []
        def produce(pending):
            calls.append(True)
            return header.read_bytes(), (header,)
        first = cache.obtain('objects', {'profile': 'dev'}, produce)
        second = cache.obtain('objects', {'profile': 'dev'}, produce)
        self.assertEqual((first[0], first[1], second[1], len(calls)), (b'first', False, True, 1))
        header.write_text('second')
        rebuilt = cache.obtain('objects', {'profile': 'dev'}, produce)
        self.assertEqual((rebuilt[0], rebuilt[1], len(calls)), (b'second', False, 2))
        object_path = next(cache.root.glob('objects/*/artifact.o'))
        object_path.write_bytes(b'corrupted')
        rebuilt = cache.obtain('objects', {'profile': 'dev'}, produce)
        self.assertEqual((rebuilt[0], rebuilt[1], len(calls)), (b'second', False, 3))
        with patch.dict(os.environ, WYSNA_COMPILER_CACHE='off'):
            self.assertFalse(cache.obtain('objects', {'profile': 'dev'}, produce)[1])
            self.assertFalse(cache.obtain('objects', {'profile': 'dev'}, produce)[1])
        self.assertEqual(len(calls), 5)

    def test_zig_identity_queries_version_without_losing_target_flags(self):
        from compiler.CObjectCache import compiler_identity
        zig = self.directory / 'zig'
        zig.write_text('#!/bin/sh\nif [ "$#" = 2 ] && [ "$1" = cc ] && [ "$2" = --version ]; then\n'
                       '  echo "clang version 20.1.2"\n  exit 0\nfi\necho "invalid GNU version" >&2\nexit 1\n')
        zig.chmod(0o755)
        command = [str(zig), 'cc', '-target', 'x86_64-linux-gnu.2.28', '-mcpu=baseline']
        identity = compiler_identity(command)
        self.assertEqual(identity['command'], command)
        self.assertEqual(identity['version'], 'clang version 20.1.2')
        self.assertEqual(len(identity['executable_sha256']), 64)

    def test_c_cache_tracks_real_header_changes(self):
        from compiler.ArtifactCache import ArtifactCache
        from compiler.CObjectCache import compiler_identity, compile_cached_c
        header = self.directory / 'answer.h'
        header.write_text('#define ANSWER 42\n')
        source = self.directory / 'probe.c'
        source.write_text('#include "answer.h"\nint main(void) { return ANSWER; }\n')
        output = self.directory / 'probe.o'
        command = compiler_command()
        identity = compiler_identity(command)
        cache = ArtifactCache('test-c', root=self.directory / 'cache')
        calls = []
        def compile_(arguments, phase):
            calls.append(phase)
            return subprocess.run(command + arguments, capture_output=True, text=True, check=True, timeout=60)
        flags = self.toolchain.host_flags + ['-std=c99', '-I' + str(self.directory)]
        first = compile_cached_c(cache, source, output, flags, identity, compile_)
        second = compile_cached_c(cache, source, output, flags, identity, compile_)
        self.assertFalse(first[0])
        self.assertTrue(second[0])
        header.write_text('#define ANSWER 43\n')
        third = compile_cached_c(cache, source, output, flags, identity, compile_)
        self.assertFalse(third[0])
        binary = self.directory / 'cached-c'
        subprocess.run(command + self.toolchain.host_flags + [str(output), '-o', str(binary)], check=True, timeout=60)
        self.assertEqual(self.execute(binary).returncode, 43)
        self.assertEqual(calls.count('c_compile'), 2)
        sibling = self.directory / 'other source'
        sibling.mkdir()
        (sibling / 'answer.h').write_text('#define ANSWER 44\n')
        relocated = sibling / source.name
        relocated.write_bytes(source.read_bytes())
        fourth = compile_cached_c(cache, relocated, output, flags, identity, compile_)
        self.assertFalse(fourth[0])
        subprocess.run(command + self.toolchain.host_flags + [str(output), '-o', str(binary)], check=True, timeout=60)
        self.assertEqual(self.execute(binary).returncode, 44)


    def test_debug_variables_and_shadowing_with_live_breakpoints(self):
        source = '''fn inspect(value: int) -> int {
    let current = value + 1
    if true {
        let current = 99
        print(current)
    }
    print(current)
    current
}
print(inspect(41))
'''
        service, checked = self.checked(source)
        emission = service.emit_llvm(checked)
        self.assertIn('!DILocalVariable(name: "value"', emission.source)
        self.assertIn('!DILexicalBlock(', emission.source)
        build = self.toolchain.build(emission, self.directory / 'debug-values')
        arguments = ['lldb', '--batch', '-o', 'settings set target.disable-aslr false',
                     '-o', 'target create "' + build.output_path + '"',
                     '-o', 'breakpoint set --file main.wysna --line 5',
                     '-o', 'breakpoint set --file main.wysna --line 7', '-o', 'run',
                     '-o', 'frame variable value current', '-o', 'continue',
                     '-o', 'frame variable value current', '-o', 'bt']
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('value = 41', result.stdout)
        self.assertIn('current = 99', result.stdout)
        self.assertIn('current = 42', result.stdout)
        self.assertIn('main.wysna:7', result.stdout)
        self.assertNotIn('optimized out', result.stdout)

    def test_debug_sidecar_cannot_cross_mir_modules(self):
        from mir.Debug import DebugModule
        from mir.Lowering import Lowering
        service, checked = self.checked('let answer = 42\nprint(answer)')
        debug = DebugModule()
        module = Lowering(checked._interner, checked.source.file, debug=debug).lower(service.lower_hir(checked))
        other = service.lower_mir(checked)
        with self.assertRaises(WysnaError) as caught:
            LlvmCodeGen(checked._interner).generate(other, triple=self.toolchain.target.triple,
                                                   data_layout=self.toolchain.target.data_layout, debug_info=debug)
        self.assertEqual(caught.exception.code, 'W9999')
        source = LlvmCodeGen(checked._interner).generate(module, triple=self.toolchain.target.triple,
                                                        data_layout=self.toolchain.target.data_layout, debug_info=debug)
        self.toolchain.verify(source)

    def test_cli_native_run_arguments_reports_and_timeout(self):
        service, checked = self.checked('print(args()[1])\nexit(7)\n')
        report = self.directory / 'run.json'
        result = self.cli('run', '--backend=llvm-experimental', checked.source.file,
                          '--report-json', report, '--', 'argument with spaces')
        self.assertEqual((result.returncode, result.stdout, result.stderr), (7, 'argument with spaces\n', ''))
        payload = json.loads(report.read_text())
        self.assertEqual(payload['process_exit_code'], 7)
        self.assertEqual(payload['tool_exit_code'], 0)
        self.assertEqual(payload['target']['profile'], 'llvm-native-v1')
        service, checked = self.checked('while true {}')
        result = self.cli('run', '--backend=llvm-experimental', checked.source.file,
                          '--timeout-ms', '50', '--report-json', report)
        self.assertEqual(result.returncode, 5, result.stdout + result.stderr)
        payload = json.loads(report.read_text())
        self.assertEqual(payload['execution']['kind'], 'timeout')
        self.assertEqual(payload['status'], 'execution_error')

    def test_empty_process_group_does_not_mask_cancellation(self):
        import signal
        from common.ProcessTree import terminate_tree
        with patch('common.ProcessTree._children', return_value=[]), \
                patch('common.ProcessTree.os.kill') as kill, \
                patch('common.ProcessTree.os.killpg', side_effect=PermissionError('terminated group')) as group:
            terminate_tree(12345)
        self.assertEqual(kill.call_args_list, [unittest.mock.call(12345, signal.SIGSTOP),
                                              unittest.mock.call(12345, signal.SIGKILL)])
        group.assert_called_once_with(12345, signal.SIGKILL)

    def test_cancellation_includes_descendants_with_separate_process_groups(self):
        import time
        import io
        from compiler.NativeExecution import run_native
        from runtime.ExecutionContext import ExecutionContext
        worker = self.directory / 'worker.py'
        marker = self.directory / 'orphan'
        ready = self.directory / 'ready'
        trigger = self.directory / 'trigger'
        grandchild = ('import time\nfrom pathlib import Path\n'
                      + 'Path(' + repr(str(ready)) + ').write_text("ready")\n'
                      + 'deadline = time.monotonic() + 20\n'
                      + 'while not Path(' + repr(str(trigger)) + ').exists() and time.monotonic() < deadline: time.sleep(0.02)\n'
                      + 'if Path(' + repr(str(trigger)) + ').exists(): Path(' + repr(str(marker)) + ').write_text("orphan")\n')
        worker.write_text('import subprocess,sys,time\nsubprocess.Popen([sys.executable,"-c",'
                          + repr(grandchild) + '],start_new_session=True)\ntime.sleep(20)\n')
        for backend, outer_timeout in (('llvm', True), ('llvm', False), ('c99', False)):
            with self.subTest(backend=backend, outer_timeout=outer_timeout):
                for path in (ready, marker, trigger):
                    path.unlink(missing_ok=True)
                limit = 15000 if outer_timeout else 5000
                source = ('import "std/process" as process\nprocess.run(' + json.dumps(sys.executable)
                          + ', [' + json.dumps(str(worker), ensure_ascii=False) + '], ' + str(limit)
                          + ') else error { print("timed out"); -1 }\n')
                service, checked = self.checked(source)
                if backend == 'llvm':
                    build = self.toolchain.build(service.emit_llvm(checked), self.directory / 'tree')
                else:
                    build = NativeToolchain(ROOT).build(service.emit_c(checked).source, self.directory / 'tree')
                context = ExecutionContext.for_program('wysna', stdin=io.StringIO(),
                                                       stdout=io.StringIO(), stderr=io.StringIO())
                result = run_native(build.output_path, context, 5000 if outer_timeout else 12000)
                self.assertEqual(result.timed_out, outer_timeout)
                self.assertTrue(ready.exists(), 'The detached descendant must have started before cancellation')
                trigger.write_text('release')
                time.sleep(0.4)
                self.assertFalse(marker.exists(), 'A detached worker survived cancellation')
                if not outer_timeout:
                    self.assertEqual(context.stdout.getvalue(), 'timed out\n')

    def test_cli_backend_llvm_alias(self):
        service, checked = self.checked('print("LLVM ALIAS", 99)')
        source = checked.source.file
        result_run = self.cli('run', '--backend=llvm', source)
        self.assertEqual(result_run.returncode, 0, result_run.stderr)
        self.assertEqual(result_run.stdout, 'LLVM ALIAS 99\n')

        result_check = self.cli('check', '--backend=llvm', '--stats', source)
        self.assertEqual(result_check.returncode, 0, result_check.stderr)
        self.assertIn('"backend": "llvm-experimental"', result_check.stdout)

        output_bin = self.directory / 'alias_prog'
        result_build = self.cli('build', '--backend=llvm', '--profile=dev', source, '-o', output_bin)
        self.assertEqual(result_build.returncode, 0, result_build.stderr)
        self.assertEqual(self.execute(output_bin).stdout, b'LLVM ALIAS 99\n')



if __name__ == '__main__':
    LlvmToolchain()  # Explicit execution must fail instead of silently skipping a missing toolchain.
    unittest.main()
