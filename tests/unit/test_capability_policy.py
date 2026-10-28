"""Capability policy refuses modeled and uncertified execution before side effects."""

from pathlib import Path
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
