"""Native compiler processes and temporary artifacts, independent of CLI output."""
from dataclasses import dataclass
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile
import time

from common.Platform import compiler_command, executable_name, native_stack_link_args


@dataclass(frozen=True)
class NativeBuildResult:
    output_path: str
    c_compile_ms: float
    link_ms: float
    cache_hits: dict | None = None
    toolchain_identity: dict | None = None


@dataclass(frozen=True)
class SelfhostEmissionResult:
    source: str
    codegen_ms: float


class NativeCompilationError(Exception):
    """A failed process with the legacy label retained for presentation adapters."""

    def __init__(self, phase, label, stderr, returncode):
        super().__init__(stderr)
        self.phase = phase
        self.label = label
        self.stderr = stderr
        self.returncode = returncode


class NativeToolchain:
    def __init__(self, repo_root):
        self.repo_root = os.fspath(repo_root)

    def build(self, c_source, output, sanitize=False, link_libraries=(), *, profile=None):
        """Compile C and explicit adapters, then link without owning caller inputs."""
        uses_http = 'wysna_host_http_request' in c_source
        if uses_http and 'WYSNA_HAS_HTTP' not in c_source:
            include = '#include "runtime/wysna_runtime.h"'
            if include in c_source:
                c_source = c_source.replace(include, '#define WYSNA_HAS_HTTP 1\n' + include, 1)
            else:
                c_source = '#define WYSNA_HAS_HTTP 1\n' + c_source
        curl_cflags, curl_libs = self._curl_flags() if uses_http else ([], [])
        sanitizer_flags = (
            ['-fsanitize=address,undefined', '-fno-omit-frame-pointer'] if sanitize else []
        )
        command = compiler_command()
        if profile not in (None, 'dev', 'release', 'size'):
            raise ValueError('Unknown C99 optimization profile: ' + str(profile))
        optimization = {'dev': '-O0', 'release': '-O2', 'size': '-Os'}.get(profile, '-O2')
        profile_flags = ['-g', '-ffunction-sections', '-fdata-sections'] if profile else []
        base = (command + ['-std=c99', optimization, f'-I{self.repo_root}'] + profile_flags
                + curl_cflags + sanitizer_flags)
        output_path = executable_name(os.fspath(output))
        libraries = tuple(os.fspath(library) for library in link_libraries)
        object_suffix = '.obj' if os.name == 'nt' else '.o'
        with tempfile.TemporaryDirectory(prefix='wysna-native-') as temporary:
            source_path = Path(temporary) / 'program.c'
            source_path.write_text(c_source, encoding='utf-8')
            object_path = str(Path(temporary) / ('program' + object_suffix))
            source_objects = {
                library: str(Path(temporary) / ('adapter-' + str(index) + object_suffix))
                for index, library in enumerate(libraries)
                if library.lower().endswith('.c') and os.path.isfile(library)
            }
            cache_hits = {}
            identity = None
            cache = None
            if profile and os.name != 'nt':
                from compiler.ArtifactCache import ArtifactCache
                from compiler.CObjectCache import compiler_identity, compile_cached_c
                identity = compiler_identity(command)
                cache = ArtifactCache('c99')
            def compile_step(arguments, phase):
                result = subprocess.run(command + arguments, capture_output=True, text=True, timeout=120)
                if result.returncode:
                    raise NativeCompilationError(phase, 'C Compiler Error', result.stderr, result.returncode)
                return result
            started = time.perf_counter()
            for source, compiled in [(str(source_path), object_path)] + list(source_objects.items()):
                if cache is not None:
                    hit, _ = compile_cached_c(cache, source, compiled, base[len(command):], identity, compile_step)
                    cache_hits['program' if source == str(source_path) else source] = hit
                else:
                    compile_step(base[len(command):] + ['-c', source, '-o', compiled], 'c_compile')
            c_compile_ms = (time.perf_counter() - started) * 1000.0
            linker_inputs = [
                source_objects[library] if library in source_objects else
                library if '/' in library or '\\' in library else '-l' + library
                for library in libraries
            ] + curl_libs
            started = time.perf_counter()
            section_flags = []
            if profile and os.name != 'nt':
                section_flags = ['-Wl,-dead_strip'] if sys.platform == 'darwin' else ['-Wl,--gc-sections']
            link_command = base + [object_path, '-o', output_path] + linker_inputs + native_stack_link_args(command) + section_flags
            result = subprocess.run(link_command, capture_output=True, text=True, timeout=120)
            link_ms = (time.perf_counter() - started) * 1000.0
            if result.returncode != 0:
                raise NativeCompilationError(
                    'link', 'C Linker Error', result.stderr, result.returncode,
                )
        return NativeBuildResult(output_path, c_compile_ms, link_ms, cache_hits, identity)

    @staticmethod
    def _curl_flags():
        """Resolve the platform libcurl development flags only for HTTP programs."""
        config = shutil.which('curl-config')
        if not config:
            raise NativeCompilationError(
                'dependency', 'HTTP Dependency Error',
                'std/http native builds require libcurl development files and curl-config; '
                'install libcurl development files and ensure curl-config is on PATH',
                127,
            )
        try:
            version = subprocess.run(
                [config, '--checkfor', '8.3.0'], capture_output=True, text=True, timeout=10,
            )
            cflags = subprocess.run(
                [config, '--cflags'], capture_output=True, text=True, timeout=10,
            )
            libs = subprocess.run(
                [config, '--libs'], capture_output=True, text=True, timeout=10,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise NativeCompilationError(
                'dependency', 'HTTP Dependency Error', str(error), 127,
            ) from error
        if version.returncode != 0:
            raise NativeCompilationError(
                'dependency', 'HTTP Dependency Error',
                version.stdout.strip() or version.stderr.strip()
                or 'std/http requires libcurl 8.3.0 or newer',
                version.returncode,
            )
        if cflags.returncode != 0 or libs.returncode != 0:
            stderr = cflags.stderr if cflags.returncode != 0 else libs.stderr
            raise NativeCompilationError(
                'dependency', 'HTTP Dependency Error',
                stderr.strip() or 'curl-config could not provide libcurl compiler flags',
                cflags.returncode or libs.returncode or 127,
            )
        try:
            compiler_flags, linker_flags = shlex.split(cflags.stdout), shlex.split(libs.stdout)
        except ValueError as error:
            raise NativeCompilationError(
                'dependency', 'HTTP Dependency Error',
                'curl-config returned invalid compiler flags', 127,
            ) from error
        if sys.platform == 'darwin' and os.path.realpath(config) == '/usr/bin/curl-config':
            # Apple's curl-config omits its SDK because Apple Clang finds it implicitly.
            # The private compiler requires the same development inputs explicitly.
            try:
                sdk = os.environ.get('SDKROOT')
                if not sdk:
                    sdk = subprocess.run(['/usr/bin/xcrun', '--show-sdk-path'], capture_output=True,
                                         text=True, timeout=10, check=True).stdout.strip()
            except (OSError, subprocess.SubprocessError) as error:
                raise NativeCompilationError('dependency', 'HTTP Dependency Error',
                    'Apple libcurl requires an installed macOS SDK; install its development inputs '
                    'or select a separate libcurl through PATH', 127) from error
            if not (Path(sdk) / 'usr/include/curl/curl.h').is_file():
                raise NativeCompilationError('dependency', 'HTTP Dependency Error',
                                             'The selected macOS SDK has no libcurl headers', 127)
            compiler_flags += ['-isysroot', sdk, '-idirafter', str(Path(sdk) / 'usr/include')]
            linker_flags += ['-L' + str(Path(sdk) / 'usr/lib')]
        return compiler_flags, linker_flags

    def emit_selfhost(self, compiler_path, file):
        """Use the independent compiler directly, resolving imports beside its input."""
        input_path = os.path.abspath(os.fspath(file))
        input_directory = os.path.dirname(input_path) or os.getcwd()
        with tempfile.TemporaryDirectory(prefix='wysna-selfhost-') as temporary:
            output_path = str(Path(temporary) / 'program.c')
            started = time.perf_counter()
            result = subprocess.run(
                [os.fspath(compiler_path), input_path, output_path],
                capture_output=True, text=True, cwd=input_directory, timeout=120,
            )
            codegen_ms = (time.perf_counter() - started) * 1000.0
            if result.returncode != 0:
                raise NativeCompilationError(
                    'selfhost', 'Wysna Compiler Error', result.stderr, result.returncode,
                )
            source = Path(output_path).read_text(encoding='utf-8')
            if 'wysna_host_http_request' in source and 'WYSNA_HAS_HTTP' not in source:
                include = '#include "runtime/wysna_runtime.h"'
                if include in source:
                    source = source.replace(include, '#define WYSNA_HAS_HTTP 1\n' + include, 1)
                else:
                    source = '#define WYSNA_HAS_HTTP 1\n' + source
        return SelfhostEmissionResult(source, codegen_ms)
