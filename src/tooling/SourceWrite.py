"""Consistent, lock-serialized, atomic source file writes for CLI and MCP tools."""
import hashlib
import os
from pathlib import Path
import stat
import tempfile

from common.Platform import cache_lock
from error.Error import WysnaError


def read_source_snapshot(path: str | os.PathLike[str]) -> tuple[Path, str, str]:
    """Read exact UTF-8 source bytes without newline translation and compute their SHA-256."""
    target = Path(path).resolve()
    raw_bytes = target.read_bytes()
    return target, raw_bytes.decode("utf-8"), hashlib.sha256(raw_bytes).hexdigest()


def _lock_path_for(target: Path) -> Path:
    lock_dir = Path(tempfile.gettempdir()) / "wysna-source-locks"
    lock_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(os.path.realpath(target).encode("utf-8")).hexdigest()[:24]
    return lock_dir / f"{digest}.lock"


def write_verified_source(
    path: str | os.PathLike[str],
    new_text: str,
    *,
    expected_sha256: str,
    stage: str = "write",
    before_lock_hook=None,
) -> str:
    """Serialize cooperating writers, re-verify disk bytes under lock, and replace atomically.

    Preserves exact UTF-8 bytes, line endings, and POSIX permission bits. External editors
    that write directly without taking Wysna's cooperative lock still trigger W5005 when
    their edit lands before the lock is acquired, though concurrent direct writes from
    non-cooperating tools remain outside this lock's mutual exclusion.
    """
    target = Path(path).resolve()
    new_bytes = new_text.encode("utf-8")
    if before_lock_hook is not None:
        before_lock_hook()

    lock_path = _lock_path_for(target)
    with cache_lock(lock_path, timeout=30):
        try:
            current_stat = target.stat()
            current_bytes = target.read_bytes()
        except FileNotFoundError as error:
            raise WysnaError(
                f"Source file '{target}' no longer exists before write",
                file=str(target),
                code="W5005",
                stage=stage,
            ) from error
        current_sha256 = hashlib.sha256(current_bytes).hexdigest()
        if current_sha256 != expected_sha256:
            raise WysnaError(
                f"Stale source snapshot for '{target}': expected sha256 '{expected_sha256[:12]}', "
                f"current is '{current_sha256[:12]}'",
                file=str(target),
                code="W5005",
                stage=stage,
            )

        descriptor = None
        temporary_path = None
        try:
            descriptor, temporary_path = tempfile.mkstemp(
                prefix=f".{target.name}.",
                suffix=".tmp",
                dir=str(target.parent),
            )
            view = memoryview(new_bytes)
            written = 0
            while written < len(new_bytes):
                chunk = os.write(descriptor, view[written:])
                if chunk <= 0:
                    raise OSError("short write while staging verified source")
                written += chunk
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            if os.name != "nt":
                os.chmod(temporary_path, stat.S_IMODE(current_stat.st_mode))
            os.replace(temporary_path, target)
            temporary_path = None
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary_path is not None:
                try:
                    os.unlink(temporary_path)
                except FileNotFoundError:
                    pass
    return hashlib.sha256(new_bytes).hexdigest()
