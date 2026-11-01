"""Verified native LLVM tools and bundled C linker for the Seed LLVM SDK."""
from functools import lru_cache
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import subprocess
import tempfile
import time
from compiler.ArtifactCache import file_digest
from compiler.NativeToolchain import NativeCompilationError

LLVM_VERSION = '20.1.8'
PLATFORMS = {
    'aarch64-macos': {'triple': 'arm64-apple-macosx13.0.0', 'zig_target': 'aarch64-macos.13.0-none',
                     'llvm': {'version': LLVM_VERSION,
                              'url': 'https://github.com/llvm/llvm-project/releases/download/llvmorg-20.1.8/LLVM-20.1.8-macOS-ARM64.tar.xz',
                              'sha256': 'a9a22f450d35f1f73cd61ab6a17c6f27d8f6051d56197395c1eb397f0c9bbec4'},
                     'zig': {'version': '0.15.2', 'url': 'https://ziglang.org/download/0.15.2/zig-aarch64-macos-0.15.2.tar.xz',
                             'sha256': '3cc2bab367e185cdfb27501c4b30b1b0653c28d9f73df8dc91488e66ece5fa6b'}},
    'x86_64-linux': {'triple': 'x86_64-unknown-linux-gnu', 'zig_target': 'x86_64-linux-gnu.2.28',
                    'llvm': {'version': LLVM_VERSION,
                             'url': 'https://github.com/llvm/llvm-project/releases/download/llvmorg-20.1.8/LLVM-20.1.8-Linux-X64.tar.xz',
                             'sha256': '1ead36b3dfcb774b57be530df42bec70ab2d239fbce9889447c7a29a4ddc1ae6'},
                    'zig': {'version': '0.15.2', 'url': 'https://ziglang.org/download/0.15.2/zig-x86_64-linux-0.15.2.tar.xz',
                            'sha256': '02aa270f183da276e5b5920b1dac44a63f1a49e55050ebde3aecc9eb82f93239'}}
}


def host_platform():
    machine = {'arm64': 'aarch64', 'aarch64': 'aarch64', 'x86_64': 'x86_64', 'AMD64': 'x86_64'}.get(platform.machine())
    system = {'Darwin': 'macos', 'Linux': 'linux'}.get(platform.system())
    return str(machine) + '-' + str(system)


def run_tool(command, phase='llvm-sdk'):
    try:
        result = subprocess.run(list(map(os.fspath, command)), capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise NativeCompilationError(phase, 'LLVM SDK Error', str(error), 127) from error
    if result.returncode:
        raise NativeCompilationError(phase, 'LLVM SDK Error', result.stdout + result.stderr, result.returncode)
    return result


def query_layout(tool, triple):
    with tempfile.TemporaryDirectory(prefix='wysna-llvm-target-') as temporary:
        source = Path(temporary) / 'target.ll'
        output = Path(temporary) / 'target-layout.ll'
        source.write_text('define i32 @main() { ret i32 0 }\n', encoding='utf-8')
        run_tool([tool, '-S', '-mtriple=' + triple, '-passes=default<O0>', source, '-o', output])
        match = re.search(r'^target datalayout = "([^"]+)"$', output.read_text(), re.MULTILINE)
        if match is None:
            raise NativeCompilationError('target', 'LLVM SDK Error', 'LLVM did not report its target data layout', 1)
        return match[1]


@lru_cache(maxsize=32768)
def _immutable_file_hash(path, device, inode, size, modified, changed):
    return file_digest(path)


def checked_hash(path):
    stat = path.stat()
    return _immutable_file_hash(str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)


class LlvmSdk:
    def __init__(self, root):
        self.root = Path(root).resolve()
        try:
            manifest = self.root / 'llvm-sdk.json'
            raw = manifest.read_bytes()
            self.manifest_digest = hashlib.sha256(raw).hexdigest()
            self.record = json.loads(raw)
            host = host_platform()
            if (self.record['schema'] != 'wysna.llvm-sdk/1' or self.record['platform'] != host
                    or host not in PLATFORMS or self.record['llvm_version'] != LLVM_VERSION
                    or self.record.get('profile') != 'llvm-native-v1' or self.record.get('runtime_abi') != 2):
                raise ValueError('LLVM SDK platform, schema or version does not match this host')
            policy = PLATFORMS[host]
            if self.record['triple'] != policy['triple'] or self.record['zig_target'] != policy['zig_target']:
                raise ValueError('LLVM SDK target contract is not the pinned target')
            for name in ('llvm', 'zig'):
                if self.record['components'][name] != policy[name]:
                    raise ValueError('LLVM SDK component provenance is not pinned: ' + name)
            required = {'toolchain/llvm/bin/opt', 'toolchain/llvm/bin/llc', 'toolchain/zig/zig',
                        'toolchain/zig/LICENSE', 'THIRD_PARTY.txt', 'launcher.py', 'bin/wysna',
                        'seed/main.py', 'seed/compiler/LlvmToolchain.py', 'seed/compiler/LlvmSdk.py',
                        'seed/runtime/wysna_llvm_runtime.c', 'seed/runtime/wysna_llvm_operations.def'}
            if host.endswith('macos'):
                required.add('toolchain/llvm/bin/dsymutil')
            files = self.record['files']
            if not isinstance(files, dict) or not required <= files.keys():
                raise ValueError('LLVM SDK is missing a required component')
            for relative, expected in files.items():
                path = PurePosixPath(relative)
                if path.is_absolute() or '..' in path.parts or '\\' in relative:
                    raise ValueError('Unsafe LLVM SDK path: ' + relative)
                source = self.root / path
                if not source.resolve().is_relative_to(self.root) or checked_hash(source) != expected:
                    raise ValueError('LLVM SDK file is corrupt or missing: ' + relative)
            version = run_tool([self.tool('opt'), '--version']).stdout
            if not re.search(r'LLVM version ' + re.escape(LLVM_VERSION) + r'\b', version):
                raise ValueError('LLVM SDK executable version does not match its manifest')
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise NativeCompilationError('configuration', 'LLVM SDK Error', str(error), 1) from error

    def tool(self, name):
        return self.root / 'toolchain/llvm/bin' / name

    def zig_cache(self):
        base = Path(os.environ.get('WYSNA_CACHE_HOME', Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'wysna'))
        return base / 'zig-global'

    def compiler(self):
        return [str(self.root / 'toolchain/zig/zig'), 'cc', '-target', self.record['zig_target'], '-mcpu=baseline']

    def verify(self, source, triple, layout):
        with tempfile.TemporaryDirectory(prefix='wysna-llvm-verify-') as temporary:
            source_path = Path(temporary) / 'program.ll'
            output = Path(temporary) / 'verified.ll'
            source_path.write_text(source, encoding='utf-8')
            run_tool([self.tool('opt'), '-S', '-passes=verify', source_path, '-o', output], 'llvm_verify')
            verified = output.read_text(encoding='utf-8')
            for key, expected in (('triple', triple), ('datalayout', layout)):
                found = re.search(r'^target ' + key + r' = "([^"]+)"$', verified, re.MULTILINE)
                if found is None or found[1] != expected:
                    raise NativeCompilationError('llvm_verify', 'LLVM SDK Error', 'LLVM target/layout does not match the selected SDK', 1)

    def object(self, source, profile):
        speed = {'dev': 0, 'release': 2, 'size': 2}[profile]
        level = 'O0' if profile == 'dev' else 'Oz' if profile == 'size' else 'O2'
        with tempfile.TemporaryDirectory(prefix='wysna-llvm-object-') as temporary:
            source_path = Path(temporary) / 'program.ll'
            optimized = Path(temporary) / 'optimized.ll'
            object_path = Path(temporary) / 'program.o'
            source_path.write_text(source, encoding='utf-8')
            pipeline = 'verify,default<' + level + '>,verify'
            started = time.perf_counter()
            run_tool([self.tool('opt'), '-S', '-passes=' + pipeline, source_path, '-o', optimized], 'llvm_optimize')
            optimize_ms = (time.perf_counter() - started) * 1000
            started = time.perf_counter()
            run_tool([self.tool('llc'), '-filetype=obj', '-relocation-model=pic', '-mcpu=generic',
                      '-function-sections', '-data-sections',
                      '-O=' + str(speed), optimized, '-o', object_path], 'object_emit')
            return object_path.read_bytes(), {'llvm_optimize': optimize_ms, 'object_emit': (time.perf_counter() - started) * 1000}
