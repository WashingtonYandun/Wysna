"""Activate verified macOS installations and preserve unmanaged files on removal."""
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import uuid

BASE = Path('/Library/Application Support/Wysna')
COMMAND = Path('/usr/local/bin/wysna')
APPLICATION = Path('/Applications/Wysna Tools/Uninstall Wysna.app')


def digest(path):
    checksum = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            checksum.update(block)
    return checksum.hexdigest()


def owned_command(command, base):
    return command.is_symlink() and os.readlink(command) == str(base / 'current/bin/wysna')


def activate(version, base=BASE, command=COMMAND):
    base, command, version = Path(base), Path(command), Path(version)
    if (version.parent != base / 'versions' or version.is_symlink() or not version.is_dir()
            or base.is_symlink() or (base / 'versions').is_symlink()):
        raise ValueError('Activation requires a directory inside the managed versions root')
    if (command.exists() or command.is_symlink()) and not owned_command(command, base):
        raise ValueError('An unmanaged wysna command already exists; it was preserved')
    current = base / 'current'
    if (current.exists() or current.is_symlink()) and (not current.is_symlink()
            or not current.resolve().is_relative_to((base / 'versions').resolve())):
        raise ValueError('The current version path is not a managed symbolic link')
    command.parent.mkdir(parents=True, exist_ok=True)
    pending = base / ('.activate-' + uuid.uuid4().hex)
    pending.symlink_to('versions/' + version.name)
    added_command = not command.is_symlink()
    try:
        if added_command:
            command.symlink_to(str(current / 'bin/wysna'))
        os.replace(pending, current)
    except BaseException:
        if added_command and owned_command(command, base):
            command.unlink()
        raise
    finally:
        pending.unlink(missing_ok=True)


def plan_removal(root, files):
    """Validate every ownership entry before scheduling any filesystem mutation."""
    root = Path(root).resolve()
    actions, preserved = [], []
    if not isinstance(files, dict):
        raise ValueError('Invalid ownership file index')
    for relative, expected in files.items():
        name = PurePosixPath(relative)
        if (not name.parts or name.is_absolute() or '..' in name.parts or '\\' in relative
                or not isinstance(expected, str) or not re.fullmatch('[0-9a-f]{64}', expected)):
            raise ValueError('Unsafe ownership manifest')
        path = root / name
        try:
            linked_parent = any(parent.is_symlink() for parent in path.parents if parent != root and parent.is_relative_to(root))
            removable = (not linked_parent and path.resolve().is_relative_to(root)
                         and path.is_file() and digest(path) == expected)
        except OSError:
            removable = False
        if removable:
            actions.append(path)
        elif path.exists() or path.is_symlink():
            preserved.append(str(path))
    return actions, preserved


def remove_planned(actions):
    # Determine all symlink digests before deleting any of their targets.
    for path in sorted(actions, key=lambda item: not item.is_symlink()):
        path.unlink(missing_ok=True)


def remove_empty_directories(root):
    if not root.exists() or root.is_symlink():
        return
    for path in sorted(root.rglob('*'), key=lambda item: len(item.parts), reverse=True):
        if path.is_dir() and not path.is_symlink():
            try:
                path.rmdir()
            except OSError:
                pass
    try:
        root.rmdir()
    except OSError:
        pass


def uninstall(base=BASE, command=COMMAND, application=APPLICATION):
    base, command, application = Path(base), Path(command), Path(application)
    if base.is_symlink() or not base.is_dir() or (base / 'versions').is_symlink():
        raise ValueError('The installation root is not a managed directory')
    preserved, plans = [], []
    versions = base / 'versions'
    for version in sorted(versions.iterdir()) if versions.is_dir() else ():
        manifest = version / 'installation.json'
        if version.is_symlink() or not version.is_dir() or not manifest.is_file():
            preserved.append(str(version))
            continue
        record = json.loads(manifest.read_text())
        if record.get('schema') != 'wysna.installation/1':
            raise ValueError('Unknown installed version ownership')
        actions, retained = plan_removal(version, record['files'])
        preserved.extend(retained)
        plans.append((version, actions, manifest))
    receipt = base / 'installer.json'
    if receipt.is_file() and not application.is_symlink():
        record = json.loads(receipt.read_text())
        if record.get('schema') != 'wysna.macos-installer/1':
            raise ValueError('Unknown application ownership receipt')
        actions, retained = plan_removal(application, record['application_files'])
        preserved.extend(retained)
        plans.append((application, actions, None))
    elif application.exists() or application.is_symlink():
        preserved.append(str(application))
    # A malformed receipt must leave even the current command untouched.
    if owned_command(command, base):
        command.unlink()
    elif command.exists() or command.is_symlink():
        preserved.append(str(command))
    current = base / 'current'
    if current.is_symlink() and current.resolve().is_relative_to(versions.resolve()):
        current.unlink()
    elif current.exists() or current.is_symlink():
        preserved.append(str(current))
    for root, actions, manifest in plans:
        remove_planned(actions)
        if manifest is not None:
            manifest.unlink()
        remove_empty_directories(root)
    remove_empty_directories(base)
    # Retain the managed-root receipt when user files remain, allowing a later
    # reinstall without treating preserved files as a foreign installation.
    if receipt.is_file() and set(base.iterdir()) == {receipt}:
        receipt.unlink()
        remove_empty_directories(base)
    try:
        application.parent.rmdir()
    except OSError:
        pass
    return preserved


def main():
    if sys.argv[1:] == ['activate']:
        activate(Path(__file__).resolve().parent)
        return 0
    if sys.argv[1:] != ['uninstall', '--confirm']:
        print('Use the Uninstall Wysna application to remove this installation.', file=sys.stderr)
        return 2
    # Standard-library helpers are loaded before the private interpreter is removed.
    preserved = uninstall()
    subprocess.run(['/usr/sbin/pkgutil', '--forget', 'com.wysofia.wysna'],
                   capture_output=True, text=True, check=True)
    print('Wysna was uninstalled. Your projects outside its installation were preserved.')
    if preserved:
        print('Preserved modified or unmanaged installation files: ' + str(len(preserved)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
