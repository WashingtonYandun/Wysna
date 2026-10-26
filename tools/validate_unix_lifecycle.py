#!/usr/bin/env python3
"""Exercise native package lifecycle only on an explicitly disposable CI system."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.package_installers import build_installers


def validate(bundle, installer, report, *, application_http=False):
    if os.environ.get('CI') != 'true':
        raise ValueError('System package lifecycle acceptance requires a disposable CI=true runner')
    bundle, installer = Path(bundle).resolve(), Path(installer).resolve()
    record = json.loads((bundle / 'installation.json').read_text())
    macos = record['platform'] == 'aarch64-macos'
    package_format = installer.suffix.lstrip('.')
    if package_format not in (('pkg',) if macos else ('deb', 'rpm')):
        raise ValueError('Installer format does not match the bundle host')
    base = Path('/Library/Application Support/Wysna') if macos else Path('/opt/wysna')
    command = Path('/usr/local/bin/wysna') if macos else Path('/usr/bin/wysna')
    if base.exists() or base.is_symlink() or command.exists() or command.is_symlink():
        raise ValueError('Lifecycle acceptance requires an empty installation and command path')
    privilege = [] if os.geteuid() == 0 else ['sudo']
    report_directory = Path(report['report_directory'])
    report_directory.mkdir(parents=True, exist_ok=True)

    def run(name, args, *, privileged=False, code=0):
        result = subprocess.run([*(privilege if privileged else []), *map(str, args)],
                                capture_output=True, text=True, timeout=900)
        report['checks'].append({'name':name,'exit_code':result.returncode,
                                 'stdout':result.stdout,'stderr':result.stderr})
        if code is not None and result.returncode != code:
            raise AssertionError(report['checks'][-1])
        return result

    def install(path, name, *, code=0):
        args = ['/usr/sbin/installer','-pkg',path,'-target','/'] if macos else (
            ['dpkg','--install',path] if package_format == 'deb' else ['rpm','--upgrade','--replacepkgs',path])
        return run(name, args, privileged=True, code=code)

    def remove():
        if macos:
            current = base / 'current'
            run('uninstall', [current / 'python/bin/python3','-I','-B',current / 'macos_lifecycle.py',
                              'uninstall','--confirm'], privileged=True)
        else:
            run('uninstall', ['dpkg','--remove','wysna'] if package_format == 'deb' else ['rpm','--erase','wysna'], privileged=True)

    installed = False
    with tempfile.TemporaryDirectory(prefix='wysna lifecycle ') as temporary:
        work = Path(temporary)
        # This is a synthetic older-build fixture, never a publishable release.
        fixture = work / 'previous-build'
        shutil.copytree(bundle, fixture, symlinks=True)
        older = dict(record, source_commit='0' * 40, source_dirty=True)
        (fixture / 'installation.json').write_text(json.dumps(older, indent=2) + '\n')
        fixture_output = work / 'fixture-installer'
        files = build_installers(fixture, fixture_output, formats=[package_format] if not macos else None)
        project = work / 'user project.wysna'
        project.write_text('print("preserved project")\n')
        try:
            foreign = work / 'foreign-command'
            foreign.write_text('unmanaged command must survive\n')
            run('prepare-command-directory', ['mkdir','-p',command.parent], privileged=True)
            run('create-foreign-command', ['cp',foreign,command], privileged=True)
            try:
                denied = install(installer, 'reject-unmanaged-command', code=None)
                assert denied.returncode != 0 and 'unmanaged' in (denied.stdout + denied.stderr).lower(), denied
                assert command.read_text() == foreign.read_text(), 'Installer replaced an unmanaged command'
            finally:
                run('remove-foreign-fixture', ['rm',command], privileged=True)
            install(fixture_output / files[0]['name'], 'install-previous-build-fixture')
            installed = True
            run('previous-build-doctor', [command,'doctor','--output-format=json-v1'])
            previous_root = (base / 'current').resolve() if macos else base / (older['version'] + '-' + older['source_commit'][:12])
            run('simulate-damaged-file', ['rm',previous_root / 'stdlib/text.wysna'], privileged=True)
            install(fixture_output / files[0]['name'], 'repair')
            run('repaired-doctor', [command,'doctor','--output-format=json-v1'])
            install(installer, 'upgrade-to-candidate')
            candidate_root = (base / 'current').resolve() if macos else base / (record['version'] + '-' + record['source_commit'][:12])
            health = json.loads(run('candidate-doctor', [command,'doctor','--output-format=json-v1']).stdout)
            assert health['result']['source_commit'] == record['source_commit'], health
            run('installed-acceptance', [sys.executable,ROOT / 'tools/validate_distribution.py',
                '--installation',candidate_root,'--source-commit',record['source_commit'],
                '--report',report_directory / 'installed.json'])
            application = [sys.executable,ROOT / 'tools/validate_installed_application.py',
                '--installation',candidate_root,'--report',report_directory / 'application.json']
            if application_http:
                application.append('--http')
            run('installed-reference-application', application)
            note = candidate_root / 'user-notes.txt'
            run('create-unmanaged-installation-file', ['touch',note], privileged=True)
            if macos:
                app = Path('/Applications/Wysna Tools/Uninstall Wysna.app')
                assert (app / 'Contents/MacOS/applet').is_file(), 'Graphical uninstaller is missing'
                run('graphical-uninstaller-script', ['/usr/bin/osadecompile',app])
            remove()
            installed = False
            assert not command.exists() and not command.is_symlink(), 'Installed command remains after uninstall'
            assert note.is_file() and project.read_text() == 'print("preserved project")\n'
            install(installer, 'reinstall-after-uninstall-with-preserved-files')
            installed = True
            run('reinstalled-doctor', [command,'doctor','--output-format=json-v1'])
            assert note.is_file() and project.is_file()
            remove()
            installed = False
            assert not command.exists() and not command.is_symlink() and note.is_file()
            report.update(passed=True, format=package_format, source_commit=record['source_commit'],
                          synthetic_previous_build=True, graphical_package_built=True,
                          graphical_clicks_automated=False, user_files_preserved=True)
        finally:
            if installed:
                remove()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle',type=Path,required=True)
    parser.add_argument('--installer',type=Path,required=True)
    parser.add_argument('--reports',type=Path,required=True)
    parser.add_argument('--http',action='store_true')
    args = parser.parse_args()
    report = {'schema':'wysna.package-lifecycle/1','passed':False,'checks':[],
              'report_directory':str(args.reports.resolve())}
    try:
        validate(args.bundle,args.installer,report,application_http=args.http)
    finally:
        args.reports.mkdir(parents=True,exist_ok=True)
        (args.reports / 'lifecycle.json').write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps({'passed':report['passed'],'checks':len(report['checks'])}))


if __name__ == '__main__':
    main()
