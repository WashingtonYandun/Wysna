"""Pinned, opt-in LLVM verification/object emission and host runtime linking."""
from dataclasses import dataclass, asdict
import os
from pathlib import Path
import platform
import re
import subprocess
import tempfile
import time
from common.Platform import compiler_command
from common.Paths import get_repo_root
from compiler.ArtifactCache import ArtifactCache, digest, file_digest
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
    binding_version: str | None = BINDING_VERSION
    runtime_abi: int = ABI_VERSION
    source_profile: str = PROFILE
    driver: str = "llvmlite"
    sdk_manifest_sha256: str | None = None


@dataclass(frozen=True)
class LlvmEmission:
    source: str
    target: LlvmTargetSpec
    source_files: tuple
    timing_ms: dict
    runtime_adapters: str = ""
    needs_http: bool = False


@dataclass(frozen=True)
class LlvmBuildResult:
    output_path: str
    timing_ms: dict
    c_compiler: tuple
    c_compiler_version: str
    debug_symbols: str | None = None
    cache_hits: dict | None = None
    instrumentation: str | None = None
    toolchain_identity: dict | None = None


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
        self.sdk = None
        self.sdk_host_flags = list(self.host_flags)
        if os.environ.get('WYSNA_LLVM_SDK'):
            from compiler.LlvmSdk import LlvmSdk, query_layout
            self.sdk = LlvmSdk(os.environ['WYSNA_LLVM_SDK'])
            self.host_flags = []
            self.llvm = None
            layout = query_layout(self.sdk.tool('opt'), triple)
            if layout != self.sdk.record['data_layout']:
                raise NativeCompilationError('configuration', 'LLVM SDK Error', 'SDK target layout does not match its tools', 1)
            self.target = LlvmTargetSpec(triple, layout, profile, binding_version=None,
                                         driver='sdk-tools', sdk_manifest_sha256=self.sdk.manifest_digest)
            return
        try:
            import llvmlite
            import llvmlite.binding as llvm
        except (ImportError, OSError) as error:
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
        if self.sdk is not None:
            self.sdk.verify(source, self.target.triple, self.target.data_layout)
            return (time.perf_counter() - started) * 1000
        try:
            with self.llvm.create_context() as context, self._module(source, context) as module:
                module.verify()
        except RuntimeError as error:
            raise NativeCompilationError('llvm_verify', 'LLVM Verification Error', str(error), 1) from error
        return (time.perf_counter() - started) * 1000

    def _protect_sdk_output(self, output):
        if self.sdk is not None and Path(output).resolve().is_relative_to(self.sdk.root):
            raise WysnaError('Output must not modify the installed LLVM SDK', code='W5002', stage='options')

    def write_ir(self, emission, output):
        if emission.target != self.target:
            raise NativeCompilationError('configuration', 'LLVM Toolchain Error', 'Emission belongs to a different LLVM target/profile', 1)
        self.verify(emission.source)
        self._protect_sdk_output(output)
        output = protect_output(output, emission.source_files)
        output.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='.wysna-llvm-', dir=output.parent) as temporary:
            staged = Path(temporary) / 'program.ll'
            staged.write_text(emission.source, encoding='utf-8')
            protect_output(output, emission.source_files)
            staged.replace(output)
        return str(output)

    def object(self, source, *, sanitize=False, command=None):
        if sanitize:
            return self._sanitized_object(source, command or compiler_command())
        if self.sdk is not None:
            verified = self.verify(source)
            data, timings = self.sdk.object(source, self.profile)
            return data, {'llvm_verify': verified, **timings}
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

    def _sanitized_object(self, source, command):
        timings = {'llvm_verify': self.verify(source), 'llvm_optimize': 0.0}
        source = re.sub(r'^(define [^\n]+?)( !dbg !\d+)? \{$',
                        lambda match: match[1] + ' sanitize_address' + (match[2] or '') + ' {',
                        source, flags=re.MULTILINE)
        with tempfile.TemporaryDirectory(prefix='wysna-asan-ir-') as temporary:
            source_path = Path(temporary) / 'program.ll'
            object_path = Path(temporary) / 'program.o'
            source_path.write_text(source, encoding='utf-8')
            started = time.perf_counter()
            try:
                target_flags = self.sdk_host_flags if self.sdk else self.host_flags
                process = subprocess.run(command + target_flags + ['-x', 'ir', '-g', PROFILES[self.profile][2],
                                         '-fsanitize=address,undefined', '-fno-sanitize-recover=all',
                                         '-fno-omit-frame-pointer', '-c', str(source_path), '-o', str(object_path)],
                                         capture_output=True, text=True, timeout=120)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise NativeCompilationError('instrumentation', 'LLVM Sanitizer Error', str(error), 127) from error
            if process.returncode:
                raise NativeCompilationError('instrumentation', 'LLVM Sanitizer Error', process.stderr, process.returncode)
            timings['object_emit'] = (time.perf_counter() - started) * 1000
            return object_path.read_bytes(), timings

    def build(self, emission, output, *, link_libraries=(), sanitize=False):
        if emission.target != self.target:
            raise NativeCompilationError('configuration', 'LLVM Toolchain Error', 'Emission belongs to a different LLVM target/profile', 1)
        self._protect_sdk_output(output)
        output = protect_output(output, emission.source_files)
        libraries = tuple(os.fspath(library) for library in link_libraries)
        protect_output(output, emission.source_files + tuple(library for library in libraries if Path(library).is_file()))
        if self.sdk is not None and sanitize and not os.environ.get('CC'):
            raise NativeCompilationError('configuration', 'LLVM SDK Error',
                                         'Instrumented SDK builds require an explicit host Clang in CC; offline core builds use bundled Zig', 1)
        command = self.sdk.compiler() if self.sdk is not None and not sanitize else compiler_command()
        target_flags = self.sdk_host_flags if self.sdk is not None and sanitize else self.host_flags
        from compiler.CObjectCache import compiler_identity, compile_cached_c
        identity = compiler_identity(command)
        version = identity['version']
        if sanitize and 'clang' not in version.lower():
            raise NativeCompilationError('configuration', 'LLVM Sanitizer Error', 'LLVM instrumentation requires CC to select Clang', 1)
        timings = {}
        cache = ArtifactCache('llvm')
        cache_hits = {}
        self.verify(emission.source)
        def program_object(pending):
            data, measured = self.object(emission.source, sanitize=sanitize, command=command)
            timings.update(measured)
            return data, ()
        object_, cache_hits['program'], timings['object_cache'] = cache.obtain(
            'objects', {'target': asdict(self.target), 'source': digest(emission.source.encode()),
                        'sanitize': sanitize, 'instrumentor': identity if sanitize else None}, program_object)
        for phase in ('llvm_verify', 'llvm_optimize', 'object_emit'):
            timings.setdefault(phase, 0.0)
        output.parent.mkdir(parents=True, exist_ok=True)
        flags = target_flags + ['-std=c99', PROFILES[self.profile][2], '-g', '-ffunction-sections', '-fdata-sections', '-I' + str(self.root), '-I' + str(self.root / 'runtime'),
                                  '-DWYSNA_LLVM_EXPECTED_ABI=' + str(ABI_VERSION),
                                  '-DWYSNA_LLVM_VALUE_BYTES=' + str(VALUE_BYTES),
                                  '-DWYSNA_LLVM_LITERAL_BYTES=' + str(LITERAL_BYTES),
                                  '-DWYSNA_LLVM_ALIGNMENT=' + str(ALIGNMENT)]
        from compiler.NativeToolchain import NativeToolchain
        curl_cflags, curl_libs = NativeToolchain._curl_flags() if emission.needs_http else ([], [])
        sanitizer_flags = ['-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-fno-omit-frame-pointer'] if sanitize else []
        flags += sanitizer_flags + curl_cflags + (['-DWYSNA_HAS_HTTP=1'] if emission.needs_http else [])
        def run(arguments, phase, executable=None):
            started = time.perf_counter()
            try:
                environment = None
                if self.sdk is not None:
                    environment = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(temporary / 'zig-local'),
                                       ZIG_GLOBAL_CACHE_DIR=str(self.sdk.zig_cache()))
                result = subprocess.run((executable or command) + arguments, capture_output=True, text=True, timeout=120,
                                        **({'env': environment} if environment is not None else {}))
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
            bridge_source = (self.root / 'runtime/wysna_llvm_runtime.c').read_text(encoding='utf-8') + '\n' + emission.runtime_adapters
            bridge = temporary / 'runtime.c'
            bridge.write_text(bridge_source, encoding='utf-8')
            def compile_runtime(arguments, phase):
                return run(arguments, 'runtime_' + phase)
            cache_hits['runtime'], timings['runtime_cache'] = compile_cached_c(
                cache, bridge, runtime, flags, identity, compile_runtime, extra_contract=asdict(self.target))
            timings['runtime_compile'] = timings.pop('runtime_c_compile', 0.0)
            linked = []
            for index, library in enumerate(libraries):
                if library.lower().endswith('.c') and Path(library).is_file():
                    adapter = temporary / ('foreign-' + str(index) + '.o')
                    def foreign(arguments, phase):
                        return run(arguments, 'foreign_' + str(index) + '_' + phase)
                    hit, elapsed = compile_cached_c(cache, Path(library), adapter, flags, identity, foreign, extra_contract=asdict(self.target))
                    cache_hits['foreign:' + library] = hit
                    timings['foreign_cache_' + str(index)] = elapsed
                    linked.append(str(adapter))
                else:
                    linked.append(library if '/' in library or '\\' in library else '-l' + library)
            sections = ['-Wl,-dead_strip'] if platform.system() == 'Darwin' else ['-Wl,--gc-sections']
            run(target_flags + sanitizer_flags + [str(program), str(runtime)] + linked + curl_libs + sections + ['-lm', '-o', str(staged)], 'link')
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
                run([str(staged), '-o', str(symbols)] if self.sdk else ['dsymutil', str(staged), '-o', str(symbols)],
                    'debug_symbols', executable=[str(self.sdk.tool('dsymutil'))] if self.sdk else ['xcrun'])
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
        return LlvmBuildResult(str(output), timings, tuple(command), version, debug_symbols, cache_hits, version if sanitize else None, identity)
