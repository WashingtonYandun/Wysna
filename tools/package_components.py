"""Verified, bounded archives shared by native and Windows distributions."""
import ast
import base64
import configparser
import csv
from email.parser import Parser
import hashlib
import io
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import ssl
import stat
import tarfile
import tempfile
import urllib.request
import zipfile
import re

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 uses the bundled TOML parser.
    from common._vendor import tomli as tomllib


def _wheel_source_inventory(source_root, configuration):
    """Resolve the explicitly declared setuptools inputs, without executing them."""
    setup = configuration['tool']['setuptools']
    paths = {Path(name + '.py') for name in setup['py-modules']}
    for package in setup['packages']:
        directory = source_root / package.replace('.', '/')
        paths.update(path.relative_to(source_root) for path in directory.glob('*.py') if path.is_file())
    for package, patterns in setup['package-data'].items():
        directory = source_root / package.replace('.', '/')
        for pattern in patterns:
            paths.update(path.relative_to(source_root) for path in directory.glob(pattern) if path.is_file())
    if not paths or any(not (source_root / path).is_file() for path in paths):
        raise ValueError('Declared wheel source inputs are missing')
    return {path.as_posix() for path in paths}


def _declared_version(source_root, configuration):
    project = configuration['project']
    if 'version' in project:
        return project['version']
    attribute = configuration['tool']['setuptools']['dynamic']['version']['attr'].split('.')
    module = source_root.joinpath(*attribute[:-1]).with_suffix('.py')
    tree = ast.parse(module.read_text(encoding='utf-8'))
    for statement in tree.body:
        if isinstance(statement, ast.Assign) and any(
                isinstance(target, ast.Name) and target.id == attribute[-1] for target in statement.targets):
            value = ast.literal_eval(statement.value)
            if isinstance(value, str):
                return value
    raise ValueError('Cannot resolve declared wheel version')


def _requirement(value):
    """Normalize the simple named requirements declared by this project."""
    match = re.fullmatch(r'\s*([A-Za-z0-9_.-]+)\s*([^;\[]*)\s*(?:;\s*extra\s*==\s*[\'"]([A-Za-z0-9_.-]+)[\'"]\s*)?', value)
    if not match:
        raise ValueError('Unsupported wheel requirement: ' + value)
    name = re.sub(r'[-_.]+', '-', match.group(1)).lower()
    constraint_text = match.group(2).strip()
    # Core Metadata 2.1 permits parenthesized version specifiers, as emitted by
    # setuptools 61. They have the same meaning as the modern spelling.
    if constraint_text.startswith('(') and constraint_text.endswith(')'):
        constraint_text = constraint_text[1:-1]
    constraints = tuple(sorted(part.strip() for part in constraint_text.split(',') if part.strip()))
    return name, constraints, match.group(3) or ''


def _validate_wheel(packed, source_root, members):
    configuration = tomllib.loads((source_root / 'pyproject.toml').read_text(encoding='utf-8'))
    source_names = _wheel_source_inventory(source_root, configuration)
    files = {member.filename for member in members if not member.is_dir()}
    metadata_roots = {name.split('/', 1)[0] for name in files if '.dist-info/' in name}
    if len(metadata_roots) != 1:
        raise ValueError('Expected exactly one wheel metadata directory')
    metadata_root = metadata_roots.pop()
    project = configuration['project']
    version = _declared_version(source_root, configuration)
    expected_root = project['name'].replace('-', '_') + '-' + version + '.dist-info'
    if metadata_root != expected_root:
        raise ValueError('Wheel metadata name or version does not match checkout')
    metadata_names = {'METADATA', 'WHEEL', 'RECORD', 'top_level.txt'}
    if project.get('scripts'):
        metadata_names.add('entry_points.txt')
    license_file = project.get('license', {}).get('file')
    if license_file:
        licenses = {'licenses/' + license_file, license_file}
        present = {name for name in licenses if metadata_root + '/' + name in files}
        if len(present) != 1:
            raise ValueError('Wheel must contain exactly one declared license')
        license_member = present.pop()
        metadata_names.add(license_member)
    expected_files = source_names | {metadata_root + '/' + name for name in metadata_names}
    if files != expected_files:
        raise ValueError('Wheel inventory does not match checkout: missing ' + repr(sorted(expected_files - files))
                         + '; unexpected ' + repr(sorted(files - expected_files)))
    for name in source_names:
        if (source_root / name).read_bytes() != packed.read(name):
            raise ValueError('Seed wheel does not match checkout: ' + name)
    metadata = Parser().parsestr(packed.read(metadata_root + '/METADATA').decode('utf-8'))
    if metadata.get_all('Name') != [project['name']] or metadata.get_all('Version') != [version]:
        raise ValueError('Wheel project metadata does not match checkout')
    expected_headers = {'metadata-version', 'name', 'version'}
    if project.get('readme'):
        expected_headers.add('description-content-type')
        if metadata.get_all('Description-Content-Type') != ['text/markdown']:
            raise ValueError('Wheel description content type does not match checkout')
    if project.get('description'):
        expected_headers.add('summary')
        if metadata.get_all('Summary') != [project['description']]:
            raise ValueError('Wheel summary does not match checkout')
    if project.get('authors'):
        expected_headers.add('author')
        if metadata.get_all('Author') != [', '.join(author['name'] for author in project['authors'])]:
            raise ValueError('Wheel authors do not match checkout')
    if project.get('keywords'):
        expected_headers.add('keywords')
        if metadata.get_all('Keywords') != [','.join(project['keywords'])]:
            raise ValueError('Wheel keywords do not match checkout')
    if project.get('requires-python'):
        expected_headers.add('requires-python')
        if metadata.get_all('Requires-Python') != [project['requires-python']]:
            raise ValueError('Wheel Python requirement does not match checkout')
    # Older setuptools emits this informational field when no platform is set.
    if metadata.get_all('Platform'):
        expected_headers.add('platform')
        if metadata.get_all('Platform') != ['UNKNOWN']:
            raise ValueError('Wheel platform metadata does not match checkout')
    if license_file:
        expected_headers.add('license')
        if metadata.get_all('License-File'):
            expected_headers.add('license-file')
        if metadata.get_all('Dynamic'):
            expected_headers.add('dynamic')
        license_text = '\n'.join(line.strip() for line in str(metadata['License']).splitlines()).strip()
        if (len(metadata.get_all('License', [])) != 1
                or license_text != (source_root / license_file).read_text(encoding='utf-8').strip()
                or metadata.get_all('License-File', []) not in ([], [license_file])
                or metadata.get_all('Dynamic', []) not in ([], ['license-file'])):
            raise ValueError('Wheel license metadata does not match checkout')
    extras = project.get('optional-dependencies', {})
    expected_requirements = [_requirement(value) for value in project.get('dependencies', [])]
    expected_requirements.extend(_requirement(value + '; extra == "' + extra + '"')
                                 for extra, values in extras.items() for value in values)
    if extras:
        expected_headers.add('provides-extra')
    if expected_requirements:
        expected_headers.add('requires-dist')
    if (metadata.get_all('Metadata-Version') not in (['2.1'], ['2.2'], ['2.3'], ['2.4'])
            or sorted(metadata.get_all('Provides-Extra', [])) != sorted(extras)
            or sorted(_requirement(value) for value in metadata.get_all('Requires-Dist', []))
            != sorted(expected_requirements)
            or {name.lower() for name in metadata.keys()} != expected_headers):
        raise ValueError('Wheel dependency or metadata headers do not match checkout')
    if project.get('readme'):
        description = (source_root / project['readme']).read_text(encoding='utf-8')
        # Older bdist_wheel appends two newlines to the metadata body.
        if metadata.get_payload() not in (description, description + '\n\n'):
            raise ValueError('Wheel description does not match checkout')
    if license_file and packed.read(metadata_root + '/' + license_member) != (source_root / license_file).read_bytes():
        raise ValueError('Wheel license does not match checkout')
    if project.get('scripts'):
        entrypoints = configparser.ConfigParser(interpolation=None)
        entrypoints.read_string(packed.read(metadata_root + '/entry_points.txt').decode('utf-8'))
        if entrypoints.sections() != ['console_scripts'] or dict(entrypoints['console_scripts']) != project['scripts']:
            raise ValueError('Wheel entry points do not match checkout')
    wheel = Parser().parsestr(packed.read(metadata_root + '/WHEEL').decode('utf-8'))
    if ({name.lower() for name in wheel.keys()} != {'wheel-version', 'generator', 'root-is-purelib', 'tag'}
            or wheel.get_all('Wheel-Version') != ['1.0'] or len(wheel.get_all('Generator', [])) != 1
            or wheel.get_all('Root-Is-Purelib') != ['true'] or wheel.get_all('Tag') != ['py3-none-any']):
        raise ValueError('Wheel format does not match the declared pure Python distribution')
    setup = configuration['tool']['setuptools']
    top_level = {name.split('.')[0] for name in setup['packages']} | set(setup['py-modules'])
    declared_top_level = packed.read(metadata_root + '/top_level.txt').decode('utf-8').splitlines()
    if len(declared_top_level) != len(top_level) or set(declared_top_level) != top_level:
        raise ValueError('Wheel top-level modules do not match checkout')
    record_name = metadata_root + '/RECORD'
    record = list(csv.reader(io.StringIO(packed.read(record_name).decode('utf-8'), newline='')))
    if any(len(row) != 3 for row in record) or len(record) != len(files) or {row[0] for row in record} != files:
        raise ValueError('Wheel RECORD inventory does not match archive')
    for name, digest, size in record:
        if name == record_name:
            if digest or size:
                raise ValueError('Wheel RECORD must not hash itself')
            continue
        data = packed.read(name)
        expected_digest = 'sha256=' + base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode('ascii')
        if digest != expected_digest or size != str(len(data)):
            raise ValueError('Wheel RECORD does not match archive: ' + name)


def unpack_seed(wheel, destination, source_root=None):
    """Validate a Seed wheel against source before extracting a fresh package root."""
    source_root = Path(source_root or Path(__file__).resolve().parents[1])
    destination = Path(destination)
    if destination.exists():
        raise ValueError('Seed extraction requires a new destination')
    with zipfile.ZipFile(wheel) as packed:
        members = packed.infolist()
        if len(members) > 100000 or sum(member.file_size for member in members) > 2 * 1024 ** 3:
            raise ValueError('Seed wheel exceeds the distribution size limit')
        names = set()
        for member in members:
            name = PurePosixPath(member.filename)
            if (name.is_absolute() or PureWindowsPath(member.filename).drive or '..' in name.parts
                    or '\\' in member.filename or stat.S_ISLNK(member.external_attr >> 16)
                    or name in names):
                raise ValueError('Unsafe or duplicate Seed wheel member')
            names.add(name)
            if member.is_dir():
                continue
        _validate_wheel(packed, source_root, members)
        packed.extractall(destination)

def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def download(component, cache):
    """Reject mismatched cached or downloaded bytes before unpacking anything."""
    target = cache / component['url'].rsplit('/', 1)[1]
    if not target.exists():
        context = ssl.create_default_context()
        if Path('/etc/ssl/cert.pem').exists():
            context.load_verify_locations('/etc/ssl/cert.pem')
        with urllib.request.urlopen(component['url'], context=context, timeout=60) as response:
            with tempfile.NamedTemporaryFile(dir=cache, delete=False) as stream:
                temporary = Path(stream.name)
                try:
                    shutil.copyfileobj(response, stream)
                except BaseException:
                    stream.close()
                    temporary.unlink(missing_ok=True)
                    raise
        try:
            if sha256(temporary) != component['sha256']:
                raise ValueError('Downloaded component checksum mismatch: ' + target.name)
            temporary.replace(target)
        finally:
            temporary.unlink(missing_ok=True)
    if sha256(target) != component['sha256']:
        raise ValueError('Cached component checksum mismatch: ' + target.name)
    return target


def _extract_zip(archive, destination):
    with zipfile.ZipFile(archive) as zipped:
        for member in zipped.infolist():
            name = member.filename
            if (PureWindowsPath(name).drive or PureWindowsPath(name).is_absolute()
                    or PurePosixPath(name).is_absolute() or '\\' in name
                    or '..' in PurePosixPath(name).parts
                    or stat.S_ISLNK(member.external_attr >> 16)):
                raise ValueError('Unsafe archive member: ' + name)
        if sum(member.file_size for member in zipped.infolist()) > 2 * 1024 ** 3:
            raise ValueError('Archive exceeds the distribution size limit')
        zipped.extractall(destination)


def extract(archive, destination):
    """Validate every path before writing, including archives from a build cache."""
    if zipfile.is_zipfile(archive):
        return _extract_zip(archive, destination)
    with tarfile.open(archive, mode="r:*") as packed:
        members = packed.getmembers()
        if len(members) > 100000 or sum(member.size for member in members) > 2 * 1024 ** 3:
            raise ValueError("Archive exceeds the distribution size limit")
        for member in members:
            name = member.name
            if (PureWindowsPath(name).drive or PureWindowsPath(name).is_absolute()
                    or PurePosixPath(name).is_absolute() or "\\" in name
                    or ".." in PurePosixPath(name).parts
                    or not (member.isfile() or member.isdir())):
                raise ValueError("Unsafe archive member: " + name)
        destination = Path(destination)
        for member in members:
            target = destination / member.name
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with packed.extractfile(member) as source, target.open("wb") as output:
                shutil.copyfileobj(source, output)
            target.chmod(member.mode & 0o777)
