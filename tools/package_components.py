"""Verified, bounded archives shared by native and Windows distributions."""
import hashlib
from pathlib import Path, PurePosixPath, PureWindowsPath
import shutil
import ssl
import stat
import tarfile
import tempfile
import urllib.request
import zipfile


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
            source = source_root / name
            if source.is_file() and source.read_bytes() != packed.read(member):
                raise ValueError('Seed wheel does not match checkout: ' + str(name))
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
