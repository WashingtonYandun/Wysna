"""Capability policy refuses modeled and uncertified execution before side effects."""

from pathlib import Path
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[2]


def invoke(directory, source, *options):
    return subprocess.run(
        [sys.executable, str(ROOT / 'main.py'), 'run', *options, str(source)],
        cwd=directory, capture_output=True, text=True, timeout=120,
    )


class CapabilityPolicyTests(unittest.TestCase):
    def test_trusted_host_effects_have_interpreter_bindings(self):
        from compiler.EffectModel import HOST_EFFECTS
        from runtime.HostAbi import FUNCTIONS
        self.assertLessEqual(set(HOST_EFFECTS), set(FUNCTIONS))

    def test_unused_foreign_declaration_is_refused_before_binding(self):
        with tempfile.TemporaryDirectory(prefix='wysna-policy-') as temporary:
            directory = Path(temporary).resolve()
            source = directory / 'foreign.wysna'
            source.write_text('extern "C" fn system(command: opaque) -> int\n'
                              'print("ran")\n', encoding='utf-8')
            result = invoke(directory, source, '--deny=filesystem.write')
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('W5006', result.stderr)
            self.assertIn('untrusted foreign declarations', result.stderr)
            self.assertEqual(result.stdout, '')

    def test_bundled_host_declarations_are_still_recognized(self):
        with tempfile.TemporaryDirectory(prefix='wysna-policy-') as temporary:
            directory = Path(temporary).resolve()
            source = directory / 'bundled.wysna'
            source.write_text('import "std/files" as files\nprint("ok")\n', encoding='utf-8')
            result = invoke(directory, source, '--allow=stdio')
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, 'ok\n')

    @unittest.skipIf(os.name == 'nt' or shutil.which('clang') is None,
                     'shared-library constructor test requires a Unix C compiler')
    def test_explicit_library_cannot_run_a_constructor_before_policy(self):
        with tempfile.TemporaryDirectory(prefix='wysna-policy-') as temporary:
            directory = Path(temporary).resolve()
            source = directory / 'foreign.wysna'
            source.write_text('extern "C" fn harmless() -> int\nprint("ran")\n', encoding='utf-8')
            c_source = directory / 'library.c'
            c_source.write_text(
                '#include <stdio.h>\n'
                '__attribute__((constructor)) static void init(void) {'
                ' FILE *f = fopen("marker.txt", "w"); if (f) fclose(f); }\n'
                'long harmless(void) { return 1; }\n', encoding='utf-8')
            library = directory / ('library.dylib' if sys.platform == 'darwin' else 'library.so')
            flags = ['-dynamiclib'] if sys.platform == 'darwin' else ['-shared', '-fPIC']
            subprocess.run(['clang', *flags, str(c_source), '-o', str(library)],
                           check=True, capture_output=True, text=True, timeout=60)
            result = invoke(directory, source, '--deny=filesystem.write', '--link-lib', str(library))
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertIn('--link-lib cannot be combined', result.stderr)
            self.assertEqual(result.stdout, '')
            self.assertFalse((directory / 'marker.txt').exists())

    def test_deny_refuses_modeled_write_before_side_effects(self):
        with tempfile.TemporaryDirectory(prefix='wysna-policy-') as temporary:
            directory = Path(temporary).resolve()
            source = directory / 'write.wysna'
            source.write_text(
                'write_file("marker.txt", "created") else error { print("failed") }\n'
                'print("ran")\n', encoding='utf-8')
            refused = invoke(directory, source, '--deny=filesystem.write')
            self.assertEqual(refused.returncode, 1, refused.stderr)
            self.assertIn('W5006', refused.stderr)
            self.assertIn('filesystem.write', refused.stderr)
            self.assertNotIn('ran', refused.stdout)
            self.assertFalse((directory / 'marker.txt').exists())

            partial = invoke(directory, source, '--allow=filesystem.write')
            self.assertEqual(partial.returncode, 1, partial.stderr)
            self.assertIn('W5006', partial.stderr)
            self.assertIn('stdio', partial.stderr)
            self.assertFalse((directory / 'marker.txt').exists())

            allowed = invoke(directory, source, '--allow=filesystem.write,stdio')
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertEqual(allowed.stdout, 'ran\n')
            self.assertEqual((directory / 'marker.txt').read_text(encoding='utf-8'),
                             'created')

    def test_allowlist_and_denylist_gate_modeled_standard_output(self):
        with tempfile.TemporaryDirectory(prefix='wysna-policy-') as temporary:
            directory = Path(temporary).resolve()
            source = directory / 'hello.wysna'
            source.write_text('print("hello")\n', encoding='utf-8')
            allowed = invoke(directory, source, '--allow=stdio')
            self.assertEqual(allowed.returncode, 0, allowed.stderr)
            self.assertEqual(allowed.stdout, 'hello\n')
            refused = invoke(directory, source, '--deny=stdio')
            self.assertEqual(refused.returncode, 1, refused.stderr)
            self.assertIn('W5006', refused.stderr)
            self.assertEqual(refused.stdout, '')

    def test_policy_error_does_not_invent_a_source_location(self):
        with tempfile.TemporaryDirectory(prefix='wysna-policy-') as temporary:
            directory = Path(temporary).resolve()
            source = directory / 'main.wysna'
            source.write_text('let harmless = 1\nprint("blocked")\n', encoding='utf-8')
            report = directory / 'report.json'
            result = invoke(directory, source, '--deny=stdio', '--report-json', str(report))
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertIn('[W5006]', result.stderr)
            self.assertNotIn(':1:1', result.stderr)
            self.assertNotIn('1 | let harmless', result.stderr)
            payload = json.loads(report.read_text(encoding='utf-8'))
            self.assertEqual(payload['diagnostics'][0]['code'], 'W5006')
            self.assertIsNone(payload['diagnostics'][0]['primary_span'])

    def test_indirect_call_cannot_escape_the_policy(self):
        with tempfile.TemporaryDirectory(prefix='wysna-policy-') as temporary:
            directory = Path(temporary).resolve()
            source = directory / 'callback.wysna'
            source.write_text('let callback = || => print("hidden")\ncallback()\n',
                              encoding='utf-8')
            for options in (('--deny=stdio',), ('--allow=stdio',)):
                refused = invoke(directory, source, *options)
                self.assertEqual(refused.returncode, 1, refused.stderr)
                self.assertIn('W5006', refused.stderr)
                self.assertIn('complete effect analysis', refused.stderr)
                self.assertNotIn('hidden', refused.stdout)

    def test_policy_options_reject_invalid_names_and_non_run_commands(self):
        with tempfile.TemporaryDirectory(prefix='wysna-policy-') as temporary:
            directory = Path(temporary).resolve()
            source = directory / 'hello.wysna'
            source.write_text('print("hello")\n', encoding='utf-8')
            for options in (('--deny=everything',), ('--allow=',),
                            ('--allow=stdio,unknown',),
                            ('--deny=stdio', '--allow=stdio'),
                            ('--deny=stdio,stdio',)):
                result = invoke(directory, source, *options)
                self.assertEqual(result.returncode, 2, (options, result.stderr))
                self.assertNotIn('hello', result.stdout)
            checked = subprocess.run(
                [sys.executable, str(ROOT / 'main.py'), 'check', '--deny=stdio', str(source)],
                cwd=directory, capture_output=True, text=True, timeout=120,
            )
            self.assertEqual(checked.returncode, 2, checked.stderr)
            self.assertIn('only by interpreted run', checked.stderr)
            for option in ('--backend=ast', '--backend=selfhost', '--backend=c99',
                           '--selfhost', '--hir'):
                selected = invoke(directory, source, '--deny=stdio', option)
                self.assertEqual(selected.returncode, 2, (option, selected.stderr))
                self.assertIn('only by interpreted run', selected.stderr)
                self.assertNotIn('hello', selected.stdout)


if __name__ == '__main__':
    unittest.main()
