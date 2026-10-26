"""Native SDK packaging boundaries and private filesystem ABI contracts."""
import io
import json
import os
import subprocess
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from common.Platform import compiler_command, executable_name, native_stack_link_args
from runtime import HostAbi
from tools.package_components import extract
from tools.package_native import COMPONENTS, assemble, host_platform
from tools.validate_native_sdk import isolated_environment, terminal_bytes


class NativeSdkContractTests(unittest.TestCase):
    def test_expected_terminal_bytes_preserve_NUL_and_C_text_mode(self):
        with patch('tools.validate_native_sdk.os.name', 'nt'):
            self.assertEqual(terminal_bytes(b'a\0\n'), b'a\0\r\n')
        with patch('tools.validate_native_sdk.os.name', 'posix'):
            self.assertEqual(terminal_bytes(b'a\0\n'), b'a\0\n')

    def test_windows_linkers_share_an_explicit_stack_reserve(self):
        with patch('common.Platform.os.name', 'nt'):
            self.assertEqual(native_stack_link_args(['clang']), ['-Wl,/STACK:8388608'])
            self.assertEqual(native_stack_link_args(['zig.exe', 'cc', '-target', 'x86_64-windows-gnu']), ['-Wl,--stack,8388608'])
            self.assertEqual(native_stack_link_args(['x86_64-w64-mingw32-gcc']), ['-Wl,--stack,8388608'])
        with patch('common.Platform.os.name', 'posix'):
            self.assertEqual(native_stack_link_args(['clang']), [])

    def test_pinned_platforms_declare_explicit_portable_targets(self):
        self.assertEqual(set(COMPONENTS['zig']), {'aarch64-macos', 'x86_64-macos', 'aarch64-linux', 'x86_64-linux', 'x86_64-windows'})
        for component in COMPONENTS['zig'].values():
            self.assertRegex(component['sha256'], r'^[0-9a-f]{64}$')
            self.assertTrue(component['url'].startswith('https://ziglang.org/download/0.15.2/'))
            self.assertNotIn('native', component['target'])
        self.assertIn(host_platform(), COMPONENTS['zig'])

    def test_existing_installation_is_preserved_before_any_download(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            marker = output / 'project.wysna'
            marker.write_text('print(1)')
            with patch('tools.package_native.download', side_effect=AssertionError('Unexpected download')):
                with self.assertRaisesRegex(ValueError, 'existing installations'):
                    assemble(output, output / 'cache')
            self.assertEqual(marker.read_text(), 'print(1)')

    def test_tar_validation_precedes_every_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'component.tar.xz'
            for name, kind in [('../escape', tarfile.REGTYPE), ('/absolute', tarfile.REGTYPE),
                               ('C:/escape', tarfile.REGTYPE), ('..\\escape', tarfile.REGTYPE),
                               ('link', tarfile.SYMTYPE), ('hard', tarfile.LNKTYPE), ('device', tarfile.CHRTYPE)]:
                with self.subTest(name=name):
                    with tarfile.open(archive, 'w:xz') as packed:
                        safe = tarfile.TarInfo('safe'); safe.size = 2
                        packed.addfile(safe, io.BytesIO(b'ok'))
                        invalid = tarfile.TarInfo(name); invalid.type = kind; invalid.linkname = '../outside'
                        packed.addfile(invalid)
                    destination = root / 'output'
                    with self.assertRaises(ValueError):
                        extract(archive, destination)
                    self.assertFalse(destination.exists())

    def test_tar_preserves_executable_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive = root / 'component.tar.xz'
            with tarfile.open(archive, 'w:xz') as packed:
                binary = tarfile.TarInfo('component/bin/compiler'); binary.size = 2; binary.mode = 0o755
                packed.addfile(binary, io.BytesIO(b'ok'))
            extract(archive, root / 'output')
            target = root / 'output/component/bin/compiler'
            self.assertEqual(target.read_bytes(), b'ok')
            if os.name != 'nt':
                self.assertTrue(target.stat().st_mode & 0o111)

    def test_consumer_environment_cannot_discover_host_toolchains(self):
        with tempfile.TemporaryDirectory() as temporary:
            with patch.dict(os.environ, {'CC': 'host', 'PythonPath': 'host', 'CPATH': 'host',
                                         'SDKROOT': 'host', 'DEVELOPER_DIR': 'host', 'SystemRoot': temporary}, clear=True):
                environment = isolated_environment(Path(temporary))
            for name in ('CC', 'PYTHONPATH', 'CPATH', 'SDKROOT', 'DEVELOPER_DIR'):
                self.assertNotIn(name, environment)
            expected = str(Path(temporary) / 'System32') if os.name == 'nt' else ''
            self.assertEqual(environment['PATH'], expected)

    def test_platform_parent_preserves_filename_and_drive_root_contracts(self):
        code = r'''#ifndef _WIN32
#define _XOPEN_SOURCE 700
#ifdef __APPLE__
#define _DARWIN_C_SOURCE 1
#endif
#endif
#include "runtime/wysna_runtime.h"
#include "packaging/native/platform.h"
#include <assert.h>
int main(void) {
    char parent[SDK_PATH_CAP];
    assert(sdk_parent(parent, "dir/name\\value"));
#ifdef _WIN32
    assert(strcmp(parent, "dir/name") == 0);
    assert(sdk_parent(parent, "C:\\program.exe"));
    assert(strcmp(parent, "C:\\") == 0);
#else
    assert(strcmp(parent, "dir") == 0);
#endif
    return 0;
}
'''
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            source = directory / 'paths.c'
            source.write_text(code)
            binary = directory / executable_name('paths')
            math_flags = [] if os.name == 'nt' else ['-lm']
            built = subprocess.run(compiler_command() + ['-std=c99', '-I' + str(root), str(source)] +
                                   math_flags + ['-o', str(binary)], capture_output=True, text=True, timeout=120)
            self.assertEqual(built.returncode, 0, built.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)

    def test_canonical_path_and_precancelled_process_C_contract(self):
        code = r'''#ifndef _WIN32
#define _POSIX_C_SOURCE 200809L
#endif
#include <stdlib.h>
#include <assert.h>
#include <signal.h>
#include "runtime/wysna_runtime.h"
int main(int argc, char** argv) {
    wysna_init_args(argc, argv);
    const char* source = wysna_g_argv[1];
    void* path = wysna_host_buffer_new();
    void* output = wysna_host_buffer_new();
    for (size_t index = 0; index < strlen(source); index++)
        assert(wysna_host_buffer_append(path, (unsigned char)source[index]) == 0);
    assert(wysna_host_fs_canonical(path, output) > 0);
    assert(wysna_host_buffer_size(output) > 0);
    assert(wysna_host_fs_canonical(path, output) == -1);
    assert(wysna_host_fs_canonical(NULL, output) == -1);
    WysnaProcessCancellation cancelled = {0};
    wysna_host_process_request_cancel(&cancelled, SIGINT);
    /* Cancellation precedes handle validation and therefore cannot launch a child. */
    assert(wysna_host_process_run_cancellable(NULL, NULL, 1000, &cancelled) == -1);
    assert(wysna_host_process_cancelled(&cancelled) == SIGINT);
    wysna_host_close(path); wysna_host_close(output);
    wysna_runtime_cleanup();
    return 0;
}
'''
        root = Path(__file__).resolve().parents[2]
        with tempfile.TemporaryDirectory(prefix='wysna C ABI á ') as temporary:
            directory = Path(temporary)
            path = directory / 'source á.wysna'
            path.write_text('print(1)')
            fixture = directory / 'contract.c'
            fixture.write_text(code)
            binary = directory / executable_name('contract')
            math_flags = [] if os.name == 'nt' else ['-lm']
            command = compiler_command() + ['-std=c99', '-O1', '-I' + str(root), str(fixture)] + math_flags + ['-o', str(binary)]
            if os.name != 'nt':
                command += ['-fsanitize=address,undefined', '-fno-omit-frame-pointer']
            compiled = subprocess.run(command, capture_output=True, text=True, timeout=120)
            self.assertEqual(compiled.returncode, 0, compiled.stderr)
            executed = subprocess.run([str(binary), str(path)], capture_output=True, text=True, timeout=10,
                                      env=dict(os.environ, WYSNA_MEM_CHECK='1', UBSAN_OPTIONS='halt_on_error=1'))
            self.assertEqual(executed.returncode, 0, executed.stderr)
            self.assertEqual(executed.stderr, '')

    def test_canonical_path_reference_rejects_invalid_handles_and_nonempty_output(self):
        with tempfile.TemporaryDirectory(prefix='wysna canonical á ') as temporary:
            source = Path(temporary) / 'source á.wysna'
            source.write_text('print(1)')
            incoming = HostAbi.allocate('buffer', bytearray(str(source).encode()))
            outgoing = HostAbi.allocate('buffer', bytearray())
            try:
                result = HostAbi.canonical_path(incoming, outgoing)
                self.assertEqual(result, len(str(source.resolve()).encode()))
                self.assertEqual(bytes(HostAbi.find(outgoing, 'buffer')), str(source.resolve()).encode())
                self.assertEqual(HostAbi.canonical_path(incoming, outgoing), -1)
                self.assertEqual(HostAbi.canonical_path(0, outgoing), -1)
            finally:
                HostAbi.close(incoming)
                HostAbi.close(outgoing)


if __name__ == '__main__':
    unittest.main()
