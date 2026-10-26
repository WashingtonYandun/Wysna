"""Content-addressed compiler artifacts with checksum and dependency validation."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import time
from common.Platform import cache_lock


def digest(data):
    return hashlib.sha256(data).hexdigest()


def file_digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


class ArtifactCache:
    def __init__(self, namespace='llvm', root=None):
        configured = root or os.environ.get('WYSNA_COMPILER_CACHE_HOME')
        base = Path(configured) if configured else Path(os.environ.get('XDG_CACHE_HOME', Path.home() / '.cache')) / 'wysna/artifacts'
        self.root = base / namespace

    def obtain(self, category, contract, produce):
        """Return immutable bytes; a corrupt entry is rebuilt under its per-key lock."""
        if os.environ.get('WYSNA_COMPILER_CACHE') == 'off':
            started = time.perf_counter()
            with tempfile.TemporaryDirectory(prefix='wysna-uncached-') as temporary:
                data, _ = produce(Path(temporary))
            return data, False, (time.perf_counter() - started) * 1000
        key = digest(json.dumps(contract, sort_keys=True, separators=(',', ':')).encode())
        directory = self.root / category
        directory.mkdir(parents=True, exist_ok=True)
        destination = directory / key
        started = time.perf_counter()
        with cache_lock(directory / (key + '.lock'), timeout=120):
            try:
                record = json.loads((destination / 'record.json').read_text(encoding='utf-8'))
                data = (destination / 'artifact.o').read_bytes()
                valid = (record['schema'] == 1 and record['key'] == key and record['sha256'] == digest(data)
                         and isinstance(record.get('dependencies'), dict)
                         and all(file_digest(path) == expected for path, expected in record['dependencies'].items()))
                if valid:
                    return data, True, (time.perf_counter() - started) * 1000
            except (OSError, ValueError, KeyError, TypeError):
                pass
            with tempfile.TemporaryDirectory(prefix='pending-', dir=directory) as temporary:
                pending = Path(temporary)
                data, dependencies = produce(pending)
                record = {'schema': 1, 'key': key, 'sha256': digest(data),
                          'dependencies': {str(Path(path).resolve()): file_digest(path) for path in dependencies}}
                (pending / 'artifact.o').write_bytes(data)
                (pending / 'record.json').write_text(json.dumps(record, sort_keys=True), encoding='utf-8')
                if destination.exists():
                    obsolete = pending / 'obsolete'
                    destination.replace(obsolete)
                    shutil.rmtree(obsolete)
                published = pending / 'published'
                published.mkdir()
                (pending / 'artifact.o').replace(published / 'artifact.o')
                (pending / 'record.json').replace(published / 'record.json')
                published.replace(destination)
            return data, False, (time.perf_counter() - started) * 1000
