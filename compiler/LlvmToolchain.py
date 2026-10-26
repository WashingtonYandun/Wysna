"""Pinned, opt-in LLVM verification/object emission and host runtime linking."""
from dataclasses import dataclass
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import time
from common.Platform import compiler_command
from common.Paths import get_repo_root
from compiler.NativeToolchain import NativeCompilationError
from error.Error import WysnaError
from mir.LlvmProfile import ABI_VERSION, VALUE_BYTES, LITERAL_BYTES, ALIGNMENT, PROFILE

BINDING_VERSION = '0.46.0'
LLVM_VERSION = (20, 1, 8)
PROFILES = {'dev': (0, 0, '-O0'), 'release': (2, 0, '-O2'), 'size': (2, 2, '-Os')}


@dataclass(frozen=True)
class LlvmTargetSpec:
    triple: str
    data_layout: str
    optimization: str
    llvm_version: tuple = LLVM_VERSION
    binding_version: str = BINDING_VERSION
    runtime_abi: int = ABI_VERSION
    source_profile: str = PROFILE


@dataclass(frozen=True)
class LlvmEmission:
    source: str
    target: LlvmTargetSpec
    source_files: tuple
    timing_ms: dict


@dataclass(frozen=True)
class LlvmBuildResult:
    output_path: str
    timing_ms: dict
    c_compiler: tuple
    c_compiler_version: str
    debug_symbols: str | None = None


def protect_output(output, source_files):
    output = Path(os.path.abspath(output))
    if output.suffix.lower() == '.wysna':
        raise WysnaError('Output must not replace a Wysna source file', file=str(output), code='W5002', stage='options')
    for source in source_files:
        source = Path(source)
        same = os.path.normcase(os.path.realpath(output)) == os.path.normcase(os.path.realpath(source))
        try:
            same = same or os.path.samefile(source, output)
        except OSError:
            pass
        if same:
            raise WysnaError('Output must not replace a source or dependency alias', file=str(output), code='W5002', stage='options')
    return output


class LlvmToolchain:
    def __init__(self, repo_root=None, profile='dev'):
        if profile not in PROFILES:
            raise WysnaError('Unknown LLVM optimization profile: ' + str(profile), code='W5002', stage='options')
        self.root = Path(repo_root or get_repo_root())
        self.profile = profile
        machine, system = platform.machine().lower(), platform.system()
        if system == 'Darwin' and machine in ('arm64', 'aarch64'):
            triple = 'arm64-apple-macosx13.0.0'
            self.host_flags = ['-target', triple]
        elif system == 'Linux' and machine in ('x86_64', 'amd64'):
            triple = 'x86_64-unknown-linux-gnu'
            self.host_flags = ['-m64']
        else:
            raise WysnaError(PROFILE + ' supports macOS arm64 and Linux x64 hosts', code='W2013', stage='llvm-target')
        try:
            import llvmlite
            import llvmlite.binding as llvm
        except ImportError as error:
            raise NativeCompilationError('configuration', 'LLVM Toolchain Error',
                                         'Install the optional LLVM tools: python -m pip install "wysna[llvm]"', 127) from error
        if llvmlite.__version__ != BINDING_VERSION or llvm.llvm_version_info != LLVM_VERSION:
            raise NativeCompilationError('configuration', 'LLVM Toolchain Error',
                                         'Expected llvmlite 0.46.0 with LLVM 20.1.8; found ' +
                                         llvmlite.__version__ + ' with ' + str(llvm.llvm_version_info), 127)
        self.llvm = llvm
        llvm.initialize_native_target()
        llvm.initialize_native_asmprinter()
        speed, _, _ = PROFILES[profile]
        self.machine = llvm.Target.from_triple(triple).create_target_machine(cpu='generic', features='', opt=speed, reloc='pic')
        self.target = LlvmTargetSpec(triple, str(self.machine.target_data), profile)

    def _module(self, source, context):
        module = self.llvm.parse_assembly(source, context=context)
        if module.triple != self.target.triple or module.data_layout != self.target.data_layout:
            module.close()
            raise NativeCompilationError('llvm_verify', 'LLVM Verification Error', 'LLVM target/layout does not match the selected host contract', 1)
        try:
            module.verify()
        except BaseException:
            module.close()
            raise
        return module

    def verify(self, source):
        started = time.perf_counter()
        try:
            with self.llvm.create_context() as context, self._module(source, context) as module:
                module.verify()
        except RuntimeError as error:
            raise NativeCompilationError('llvm_verify', 'LLVM Verification Error', str(error), 1) from error
        return (time.perf_counter() - started) * 1000

    def write_ir(self, emission, output):
        if emission.target != self.target:
            raise NativeCompilationError('configuration', 'LLVM Toolchain Error', 'Emission belongs to a different LLVM target/profile', 1)
        self.verify(emission.source)
        output = protect_output(output, emission.source_files)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.wysna-llvm-', dir=output.parent) as temporary:
            staged = Path(temporary) / 'program.ll'
            staged.write_text(emission.source, encoding='utf-8')
            protect_output(output, emission.source_files)
            staged.replace(output)
        return str(output)

    def object(self, source):
        timings = {}
        try:
            with self.llvm.create_context() as context, self._module(source, context) as module:
                started = time.perf_counter()
                module.verify()
                timings['llvm_verify'] = (time.perf_counter() - started) * 1000
                started = time.perf_counter()
                if self.profile != 'dev':
                    speed, size, _ = PROFILES[self.profile]
                    with self.llvm.create_pipeline_tuning_options(speed_level=speed, size_level=size) as options:
                        with self.llvm.create_pass_builder(self.machine, options) as builder:
                            with builder.getModulePassManager() as manager:
                                manager.run(module, builder)
                module.verify()
                timings['llvm_optimize'] = (time.perf_counter() - started) * 1000
                started = time.perf_counter()
                result = self.machine.emit_object(module)
                timings['object_emit'] = (time.perf_counter() - started) * 1000
        except RuntimeError as error:
            raise NativeCompilationError('llvm_verify', 'LLVM Verification Error', str(error), 1) from error
        return result, timings

    def build(self, emission, output):
        if emission.target != self.target:
            raise NativeCompilationError('configuration', 'LLVM Toolchain Error', 'Emission belongs to a different LLVM target/profile', 1)
        output = protect_output(output, emission.source_files)
        object_, timings = self.object(emission.source)
        output.parent.mkdir(parents=True, exist_ok=True)
        command = compiler_command()
        flags = self.host_flags + ['-std=c99', PROFILES[self.profile][2], '-g', '-I' + str(self.root),
                                  '-DWYSNA_LLVM_EXPECTED_ABI=' + str(ABI_VERSION),
                                  '-DWYSNA_LLVM_VALUE_BYTES=' + str(VALUE_BYTES),
                                  '-DWYSNA_LLVM_LITERAL_BYTES=' + str(LITERAL_BYTES),
                                  '-DWYSNA_LLVM_ALIGNMENT=' + str(ALIGNMENT)]
        def run(arguments, phase, executable=None):
            started = time.perf_counter()
            try:
                result = subprocess.run((executable or command) + arguments, capture_output=True, text=True, timeout=120)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise NativeCompilationError(phase, 'LLVM Toolchain Error', str(error), 127) from error
            if result.returncode:
                raise NativeCompilationError(phase, 'LLVM Toolchain Error', result.stdout + result.stderr, result.returncode)
            timings[phase] = (time.perf_counter() - started) * 1000
            return result
        with tempfile.TemporaryDirectory(prefix='.wysna-llvm-', dir=output.parent) as temporary:
            temporary = Path(temporary)
            program = temporary / 'program.o'
            runtime = temporary / 'runtime.o'
            staged = temporary / output.name
            program.write_bytes(object_)
            run(flags + ['-c', str(self.root / 'runtime/wysna_llvm_runtime.c'), '-o', str(runtime)], 'runtime_compile')
            run(self.host_flags + [str(program), str(runtime), '-lm', '-o', str(staged)], 'link')
            version = run(['--version'], 'compiler_version').stdout.splitlines()[0]
            debug_symbols = None
            if platform.system() == 'Darwin':
                bundle = Path(str(output) + '.dSYM')
                for source in emission.source_files:
                    if Path(source).resolve().is_relative_to(bundle.resolve()):
                        raise WysnaError('Debug output must not replace source dependencies', code='W5002', stage='options')
                if bundle.exists() and (bundle.is_symlink() or not bundle.is_dir()
                        or not (bundle / 'Contents/Info.plist').is_file()
                        or not (bundle / 'Contents/Resources/DWARF').is_dir()):
                    raise WysnaError('Debug output already exists and is not a dSYM bundle', code='W5002', stage='options')
                symbols = temporary / 'symbols.dSYM'
                run(['dsymutil', str(staged), '-o', str(symbols)], 'debug_symbols', executable=['xcrun'])
                # Keep the previous bundle until both outputs are ready to publish.
                backup = temporary / 'previous.dSYM'
                if bundle.exists():
                    bundle.replace(backup)
                try:
                    symbols.replace(bundle)
                    protect_output(output, emission.source_files)
                    staged.replace(output)
                except BaseException:
                    if bundle.exists():
                        bundle.replace(symbols)
                    if backup.exists():
                        backup.replace(bundle)
                    raise
                debug_symbols = str(bundle / 'Contents/Resources/DWARF' / output.name)
            else:
                protect_output(output, emission.source_files)
                staged.replace(output)
        return LlvmBuildResult(str(output), timings, tuple(command), version, debug_symbols)
