#!/usr/bin/env python3
"""Build native GUI installers from a verified, complete Seed distribution."""
import argparse
import json
import os
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.package_components import sha256

MAC_BASE = Path('/Library/Application Support/Wysna')
MAC_APP = Path('/Applications/Wysna Tools/Uninstall Wysna.app')


def run(command, **kwargs):
    command = list(map(os.fspath, command))
    result = subprocess.run(command, capture_output=True, text=True, timeout=600, **kwargs)
    if result.returncode:
        raise RuntimeError('Package command failed: ' + shlex.join(command) + '\n' + result.stdout + result.stderr)
    return result


def verify_bundle(bundle):
    bundle = Path(bundle).resolve()
    record = json.loads((bundle / 'installation.json').read_text())
    if (record.get('schema') != 'wysna.installation/1'
            or not re.fullmatch(r'\d+\.\d+\.\d+', record['version'])
            or not re.fullmatch('[0-9a-f]{40}', record['source_commit'])):
        raise ValueError('Installer input requires a versioned distribution and exact source commit')
    for relative, expected in record['files'].items():
        name = PurePosixPath(relative)
        path = bundle / name
        if (name.is_absolute() or '..' in name.parts or '\\' in relative
                or not path.resolve().is_relative_to(bundle) or sha256(path) != expected):
            raise ValueError('Damaged or unsafe installer input: ' + relative)
    actual = {path.relative_to(bundle).as_posix() for path in bundle.rglob('*') if path.is_file()}
    if actual != set(record['files']) | {'installation.json'}:
        raise ValueError('Installer input contains missing or unrecorded files')
    if any(path.is_symlink() and not path.is_file() for path in bundle.rglob('*')):
        raise ValueError('Installer input contains an unrecorded directory or broken symbolic link')
    return record


def inno_compiler():
    compiler = shutil.which('iscc') or shutil.which('ISCC.exe')
    if compiler:
        return compiler
    for variable in ('ProgramFiles(x86)', 'ProgramFiles'):
        if os.environ.get(variable):
            candidate = Path(os.environ[variable]) / 'Inno Setup 6/ISCC.exe'
            if candidate.is_file():
                return str(candidate)
    raise ValueError('Inno Setup 6 is required to build the Windows installer')


def macos_payload(bundle, record, temporary):
    build = record['version'] + '-' + record['source_commit'][:12]
    version_root = MAC_BASE / 'versions' / build
    payload = temporary / 'payload'
    installed = payload / version_root.relative_to('/')
    shutil.copytree(bundle, installed, symlinks=True)
    shutil.copy2(ROOT / 'packaging/install/macos_lifecycle.py', installed / 'macos_lifecycle.py')
    manifest = json.loads((installed / 'installation.json').read_text())
    manifest['files']['macos_lifecycle.py'] = sha256(installed / 'macos_lifecycle.py')
    (installed / 'installation.json').write_text(json.dumps(manifest, indent=2) + '\n')
    application = payload / MAC_APP.relative_to('/')
    script = temporary / 'uninstall.applescript'
    command = shlex.join([str(MAC_BASE / 'current/python/bin/python3'), '-I', '-B',
                          str(MAC_BASE / 'current/macos_lifecycle.py'), 'uninstall', '--confirm'])
    script.write_text('display dialog "Uninstall Wysna? Your projects will be kept." buttons {"Cancel", "Uninstall"} default button "Cancel" with icon caution\n'
                      'set resultText to do shell script ' + json.dumps(command) + ' with administrator privileges\n'
                      'display dialog resultText buttons {"OK"} default button "OK"\n')
    application.parent.mkdir(parents=True, exist_ok=True)
    run(['osacompile', '-o', application, script])
    receipt = {'schema': 'wysna.macos-installer/1', 'source_commit': record['source_commit'],
               'application_files': {path.relative_to(application).as_posix(): sha256(path)
                                     for path in application.rglob('*') if path.is_file()}}
    (payload / MAC_BASE.relative_to('/') / 'installer.json').write_text(json.dumps(receipt, indent=2))
    scripts = temporary / 'scripts'
    scripts.mkdir()
    preinstall = scripts / 'preinstall'
    preinstall.write_text('#!/bin/sh\nset -eu\nbase=' + shlex.quote(str(MAC_BASE)) + '\n'
        'if [ -L "$base" ] || { [ -e "$base" ] && [ ! -f "$base/installer.json" ]; }; then\n'
        '  echo "An unmanaged Wysna installation directory exists. It was preserved." >&2\n  exit 1\nfi\n'
        'if [ -e /usr/local/bin/wysna ] || [ -L /usr/local/bin/wysna ]; then\n'
        '  if [ ! -L /usr/local/bin/wysna ] || [ "$(readlink /usr/local/bin/wysna)" != "$base/current/bin/wysna" ]; then\n'
        '    echo "An unmanaged wysna command exists. It was preserved." >&2\n    exit 1\n  fi\nfi\n')
    postinstall = scripts / 'postinstall'
    postinstall.write_text('#!/bin/sh\nset -eu\n' + shlex.join([str(version_root / 'bin/wysna'), 'doctor']) + '\n' +
        shlex.join([str(version_root / 'python/bin/python3'), '-I', '-B', str(version_root / 'macos_lifecycle.py'), 'activate']) + '\n')
    for script in (preinstall, postinstall):
        script.chmod(0o755)
    return payload, scripts, version_root


def package_macos(bundle, output, record, temporary, signing_identity=None):
    payload, scripts, version_root = macos_payload(bundle, record, temporary)
    component = temporary / 'component.pkg'
    run(['pkgbuild', '--root', payload, '--scripts', scripts, '--identifier', 'com.wysofia.wysna',
         '--version', record['version'], '--install-location', '/', '--ownership', 'recommended', component])
    distribution = temporary / 'Distribution.xml'
    run(['productbuild', '--synthesize', '--package', component, distribution])
    tree = ElementTree.parse(distribution)
    root = tree.getroot()
    title = ElementTree.Element('title')
    title.text = 'Wysna ' + record['version']
    root.insert(0, title)
    options = root.find('options')
    if options is None:
        options = ElementTree.SubElement(root, 'options')
    options.set('customize', 'never')
    ElementTree.SubElement(root, 'license', {'file': str(bundle / 'LICENSE')})
    tree.write(distribution, encoding='utf-8', xml_declaration=True)
    target = output / ('wysna-' + record['version'] + '-macos-arm64.pkg')
    command = ['productbuild', '--distribution', distribution, '--package-path', temporary]
    if signing_identity:
        command += ['--sign', signing_identity]
    run(command + [target])
    return [target]


def linux_payload(bundle, record, temporary):
    prefix = '/opt/wysna/' + record['version'] + '-' + record['source_commit'][:12]
    payload = temporary / 'linux-payload'
    installed = payload / prefix.lstrip('/')
    shutil.copytree(bundle, installed, symlinks=True)
    (payload / 'usr/bin').mkdir(parents=True)
    (payload / 'usr/bin/wysna').symlink_to(prefix + '/bin/wysna')
    applications = payload / 'usr/share/applications'
    applications.mkdir(parents=True)
    terminal = installed / 'open-terminal'
    terminal.write_text('#!/bin/sh\nexport PATH=' + shlex.quote(prefix + '/bin') + ':"$PATH"\ncd "$HOME"\nexec "${SHELL:-/bin/sh}" -i\n')
    terminal.chmod(0o755)
    manifest = json.loads((installed / 'installation.json').read_text())
    manifest['files']['open-terminal'] = sha256(terminal)
    (installed / 'installation.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (applications / 'wysna.desktop').write_text('[Desktop Entry]\nType=Application\nName=Wysna Terminal\n'
        'Comment=Use the installed Wysna compiler and tools\nExec=' + prefix + '/open-terminal\nTerminal=true\nCategories=Development;\n')
    return payload, prefix


def package_linux(bundle, output, record, temporary, formats):
    payload, prefix = linux_payload(bundle, record, temporary)
    results = []
    if 'deb' in formats:
        metadata = payload / 'DEBIAN'
        metadata.mkdir()
        (metadata / 'control').write_text('Package: wysna\nVersion: ' + record['version'] + '\nArchitecture: amd64\n'
            'Maintainer: Washington Yandun <81931984+WashingtonYandun@users.noreply.github.com>\n'
            'Section: devel\nPriority: optional\nDepends: libc6 (>= 2.28)\n'
            'Recommends: libcurl4-openssl-dev (>= 8.3), libsqlite3-dev\n'
            'Homepage: https://github.com/WashingtonYandun/Wysna\n'
            'Description: Wysna programming language and private toolchain\n'
            ' Static typing, native builds, project and editor tools.\n'
            ' Private Python and Zig are included; no host development setup is needed.\n')
        postinstall = metadata / 'postinst'
        postinstall.write_text('#!/bin/sh\nset -eu\nif [ "$1" = configure ]; then\n  ' +
                               shlex.join([prefix + '/bin/wysna', 'doctor']) + '\nfi\n')
        postinstall.chmod(0o755)
        target = output / ('wysna-' + record['version'] + '-linux-amd64.deb')
        run(['dpkg-deb', '--root-owner-group', '--build', payload, target])
        results.append(target)
        shutil.rmtree(metadata)
    if 'rpm' in formats:
        rpm = temporary / 'rpm'
        for name in ('BUILD', 'BUILDROOT', 'RPMS', 'SOURCES', 'SPECS', 'SRPMS'):
            (rpm / name).mkdir(parents=True)
        spec = rpm / 'SPECS/wysna.spec'
        spec.write_text('%global debug_package %{nil}\n%global __os_install_post %{nil}\n%global _build_id_links none\n'
            'Name: wysna\nVersion: ' + record['version'] + '\nRelease: 1\nSummary: Wysna language and private toolchain\n'
            'License: MIT AND Python-2.0 AND Apache-2.0 WITH LLVM-exception\n'
            'URL: https://github.com/WashingtonYandun/Wysna\nBuildArch: x86_64\nAutoReqProv: no\nRequires: glibc >= 2.28\n'
            'Recommends: libcurl-devel >= 8.3\nRecommends: sqlite-devel\n'
            '%description\nWysna compiler, project and editor tools with private Python and Zig.\n'
            '%prep\n%build\n%install\nmkdir -p "%{buildroot}"\ncp -a ' + shlex.quote(str(payload) + '/.') + ' "%{buildroot}/"\n'
            '%post\n' + shlex.join([prefix + '/bin/wysna', 'doctor']) + '\n'
            '%files\n' + prefix + '\n/usr/bin/wysna\n/usr/share/applications/wysna.desktop\n')
        run(['rpmbuild', '-bb', '--define', '_topdir ' + str(rpm), spec])
        packages = list((rpm / 'RPMS').rglob('*.rpm'))
        if len(packages) != 1:
            raise ValueError('Expected exactly one RPM package')
        target = output / ('wysna-' + record['version'] + '-linux-x86_64.rpm')
        shutil.copy2(packages[0], target)
        results.append(target)
    return results


def build_installers(bundle, output, *, formats=None, signing_identity=None):
    bundle, output = Path(bundle).resolve(), Path(output).resolve()
    record = verify_bundle(bundle)
    if output.exists():
        raise ValueError('Choose a fresh installer output directory')
    if record['platform'] != 'x86_64-linux' and formats:
        raise ValueError('--formats selects Linux package formats only')
    if signing_identity and record['platform'] != 'aarch64-macos':
        raise ValueError('--signing-identity applies only to macOS Installer packages')
    output.mkdir(parents=True)
    with tempfile.TemporaryDirectory(prefix='wysna-installer-') as temporary:
        temporary = Path(temporary)
        if record['platform'] == 'aarch64-macos':
            results = package_macos(bundle, output, record, temporary, signing_identity)
        elif record['platform'] == 'x86_64-linux':
            results = package_linux(bundle, output, record, temporary, formats or ('deb', 'rpm'))
        elif record['platform'] == 'x86_64-windows':
            run([inno_compiler(), '/DBundleDir=' + str(bundle), '/DWysnaVersion=' + record['version'],
                 '/DOutputDir=' + str(output), ROOT / 'packaging/windows/wysna.iss'])
            results = list(output.glob('*.exe'))
        else:
            raise ValueError('Unsupported installer platform')
    if not results:
        raise ValueError('Installer tools produced no artifacts')
    files = [{'name': path.name, 'sha256': sha256(path), 'bytes': path.stat().st_size} for path in results]
    (output / 'SHA256SUMS').write_text(''.join(item['sha256'] + '  ' + item['name'] + '\n' for item in files))
    (output / 'installers.json').write_text(json.dumps({'schema':'wysna.installers/1','version':record['version'],
        'source_commit':record['source_commit'],'source_dirty':record['source_dirty'],
        'platform':record['platform'],'gui_installation':True,'host_python_required':False,
        'host_c_compiler_required':False,'signed':bool(signing_identity),'notarized':False,
        'files':files},indent=2))
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--formats', nargs='+', choices=('deb','rpm'))
    parser.add_argument('--signing-identity')
    args = parser.parse_args()
    print(json.dumps(build_installers(args.bundle,args.output,formats=args.formats,
                                     signing_identity=args.signing_identity),indent=2))


if __name__ == '__main__':
    main()
