"""Installer ownership and archive boundaries must fail before destructive changes."""
import importlib.util
from contextlib import redirect_stdout
import io
import json
import os
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools.package_distribution import extract_python
from tools.package_installers import build_installers, inno_compiler, verify_bundle
from tooling.ApiIndex import run_capabilities_command

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('macos_lifecycle', ROOT / 'packaging/install/macos_lifecycle.py')
lifecycle = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(lifecycle)


class InstallerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.base = self.root / 'Application Support/Wysna'
        self.command = self.root / 'bin/wysna'
        self.application = self.root / 'Applications/Uninstall Wysna.app'

    def version(self, name):
        root = self.base / 'versions' / name
        (root / 'bin').mkdir(parents=True)
        (root / 'bin/wysna').write_text('owned command')
        (root / 'library.py').write_text('owned library')
        (root / 'alias.py').symlink_to('library.py')
        record = {'schema': 'wysna.installation/1', 'files': {
            path.relative_to(root).as_posix(): lifecycle.digest(path)
            for path in root.rglob('*') if path.is_file()}}
        (root / 'installation.json').write_text(json.dumps(record))
        return root

    def test_update_and_uninstall_preserve_modified_files_and_projects(self):
        first = self.version('0.5.0-first')
        second = self.version('0.5.1-second')
        lifecycle.activate(first, self.base, self.command)
        lifecycle.activate(second, self.base, self.command)
        self.assertEqual((self.base / 'current').resolve(), second)
        self.assertEqual(self.command.resolve(), second / 'bin/wysna')
        project = self.root / 'projects/user.wysna'
        project.parent.mkdir()
        project.write_text('print("user project")')
        modified = first / 'library.py'
        modified.write_text('user modification')
        unmanaged = second / 'notes.txt'
        unmanaged.write_text('user notes')
        preserved = lifecycle.uninstall(self.base, self.command, self.application)
        self.assertIn(str(modified), preserved)
        self.assertEqual(modified.read_text(), 'user modification')
        self.assertEqual(unmanaged.read_text(), 'user notes')
        self.assertTrue(project.is_file())
        self.assertFalse(self.command.is_symlink())
        self.assertFalse((second / 'bin/wysna').exists())
        self.assertFalse((second / 'alias.py').is_symlink())

    def test_invalid_later_receipt_cannot_partially_remove_an_installation(self):
        first = self.version('first')
        second = self.version('second')
        lifecycle.activate(first, self.base, self.command)
        manifest = second / 'installation.json'
        record = json.loads(manifest.read_text())
        record['files']['../outside'] = '0' * 64
        manifest.write_text(json.dumps(record))
        with self.assertRaises(ValueError):
            lifecycle.uninstall(self.base, self.command, self.application)
        self.assertTrue((first / 'bin/wysna').is_file())
        self.assertEqual(self.command.resolve(), first / 'bin/wysna')
        self.assertTrue(manifest.is_file())

    def test_activation_preserves_foreign_command_and_rolls_back_failed_switch(self):
        version = self.version('first')
        self.command.parent.mkdir()
        self.command.write_text('foreign executable')
        with self.assertRaises(ValueError):
            lifecycle.activate(version, self.base, self.command)
        self.assertEqual(self.command.read_text(), 'foreign executable')
        self.command.unlink()
        with patch.object(lifecycle.os, 'replace', side_effect=OSError('atomic switch failed')):
            with self.assertRaises(OSError):
                lifecycle.activate(version, self.base, self.command)
        self.assertFalse(self.command.is_symlink())
        self.assertFalse(list(self.base.glob('.activate-*')))

    def test_uninstall_preserves_foreign_links_and_does_not_follow_directory_links(self):
        version = self.version('first')
        outside = self.root / 'outside'
        outside.mkdir()
        file = outside / 'owned-name'
        file.write_text('keep')
        (version / 'linked').symlink_to(outside, target_is_directory=True)
        manifest = version / 'installation.json'
        record = json.loads(manifest.read_text())
        record['files']['linked/owned-name'] = lifecycle.digest(file)
        manifest.write_text(json.dumps(record))
        self.command.parent.mkdir()
        self.command.symlink_to(file)
        (self.base / 'current').symlink_to(outside)
        lifecycle.uninstall(self.base, self.command, self.application)
        self.assertEqual(file.read_text(), 'keep')
        self.assertEqual(self.command.resolve(), file)
        self.assertEqual((self.base / 'current').resolve(), outside)

    def test_python_archive_allows_internal_file_links_and_rejects_escape_before_extraction(self):
        for target in ('python3.14', '../../../outside', '/etc/passwd'):
            archive = self.root / ('archive-' + str(len(target)) + '.tar')
            with tarfile.open(archive, 'w') as packed:
                content = b'private Python'
                item = tarfile.TarInfo('python/bin/python3.14')
                item.size = len(content)
                packed.addfile(item, io.BytesIO(content))
                link = tarfile.TarInfo('python/bin/python3')
                link.type, link.linkname = tarfile.SYMTYPE, target
                packed.addfile(link)
            output = self.root / ('unpack-' + str(len(target)))
            if target == 'python3.14':
                extract_python(archive, output)
                self.assertEqual((output / 'python/bin/python3').read_bytes(), content)
            else:
                with self.assertRaises(ValueError):
                    extract_python(archive, output)
                self.assertFalse(output.exists())

    def test_installer_requires_exact_inventory_and_does_not_claim_unperformed_signing(self):
        bundle = self.root / 'bundle'
        bundle.mkdir()
        (bundle / 'command').write_text('owned')
        record = {'schema': 'wysna.installation/1', 'version': '0.5.0',
                  'source_commit': '0' * 40, 'platform': 'x86_64-linux',
                  'files': {'command': lifecycle.digest(bundle / 'command')}}
        (bundle / 'installation.json').write_text(json.dumps(record))
        self.assertEqual(verify_bundle(bundle)['version'], '0.5.0')
        output = self.root / 'output'
        with self.assertRaisesRegex(ValueError, 'macOS'):
            build_installers(bundle, output, signing_identity='unused identity')
        self.assertFalse(output.exists())
        (bundle / 'unrecorded').write_text('not inventoried')
        with self.assertRaisesRegex(ValueError, 'unrecorded'):
            verify_bundle(bundle)
        (bundle / 'unrecorded').unlink()
        (bundle / 'unrecorded-link').symlink_to('missing')
        with self.assertRaisesRegex(ValueError, 'symbolic link'):
            verify_bundle(bundle)

    def test_inno_compiler_discovery_supports_standard_installation_outside_path(self):
        compiler = self.root / 'Inno Setup 6/ISCC.exe'
        compiler.parent.mkdir()
        compiler.write_text('fixture')
        with patch('tools.package_installers.shutil.which', return_value=None):
            with patch.dict(os.environ, {'ProgramFiles(x86)': str(self.root)}):
                self.assertEqual(inno_compiler(), str(compiler))

    def test_managed_receipt_survives_only_when_user_files_require_reinstallation(self):
        version = self.version('first')
        receipt = self.base / 'installer.json'
        receipt.write_text(json.dumps({'schema':'wysna.macos-installer/1','application_files':{}}))
        note = version / 'user-notes.txt'
        note.write_text('preserved')
        lifecycle.activate(version,self.base,self.command)
        lifecycle.uninstall(self.base,self.command,self.application)
        self.assertTrue(receipt.is_file())
        self.assertEqual(note.read_text(),'preserved')
        note.unlink()
        lifecycle.uninstall(self.base,self.command,self.application)
        self.assertFalse(self.base.exists())

    def test_installed_command_discovery_does_not_advertise_unbundled_http_extras(self):
        with patch('tooling.ApiIndex._STDLIB_ROOT', self.root / 'stdlib'):
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(run_capabilities_command(['--output-format=json-v1']), 0)
            self.assertNotIn('doctor', json.loads(output.getvalue())['result']['commands'])
            (self.root / 'installation.json').write_text('{}')
            (self.root / 'wysna_entry.py').write_text('# Synthetic installed-context marker\n')
            output = io.StringIO()
            with redirect_stdout(output):
                self.assertEqual(run_capabilities_command(['--output-format=json-v1']), 0)
            result = json.loads(output.getvalue())['result']
            self.assertIn('doctor', result['machine_output']['commands'])
            self.assertIn('not bundled', result['requirements']['std/http']['interpreter'])


if __name__ == '__main__':
    unittest.main()
