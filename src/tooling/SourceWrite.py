"""Consistent, lock-serialized, atomic source file writes for CLI and MCP tools."""
from contextlib import ExitStack
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

        temporary = _stage_source(target, new_bytes, current_stat.st_mode)
        try:
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
    return hashlib.sha256(new_bytes).hexdigest()


def _stage_source(target: Path, data: bytes, mode: int) -> Path:
    descriptor, filename = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=target.parent)
    temporary = Path(filename)
    try:
        with os.fdopen(descriptor, "wb") as staged:
            staged.write(data)
            staged.flush()
            os.fsync(staged.fileno())
        if os.name != "nt":
            temporary.chmod(stat.S_IMODE(mode))
        return temporary
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def write_verified_sources(edits, verify_revision):
    """Stage a batch under the same per-file locks used by patch and fix."""
    ordered = sorted(edits, key=lambda edit: str(edit[0]))
    with ExitStack() as locks, ExitStack() as cleanup:
        for path, _, _ in ordered:
            locks.enter_context(cache_lock(_lock_path_for(path), timeout=30))
        verify_revision()
        staged = []
        retained = set()

        def remove_backup(path):
            if path not in retained:
                path.unlink(missing_ok=True)

        for path, before, after in ordered:
            mode = path.stat().st_mode
            backup = _stage_source(path, before, mode)
            cleanup.callback(remove_backup, backup)
            candidate = _stage_source(path, after, mode)
            cleanup.callback(candidate.unlink, missing_ok=True)
            staged.append((path, before, after, backup, candidate))
        verify_revision()
        applied = []
        try:
            for path, before, after, backup, candidate in staged:
                if path.read_bytes() != before:
                    raise WysnaError(f"Source changed before workspace apply: '{path}'",
                                     file=str(path), code="W5005", stage="patch")
                os.replace(candidate, path)
                applied.append((path, after, backup))
        except BaseException as error:
            rollback_failures = []
            for path, after, backup in reversed(applied):
                try:
                    if path.read_bytes() == after:
                        os.replace(backup, path)
                    else:
                        retained.add(backup)
                        rollback_failures.append(f"{path} (backup {backup})")
                except OSError:
                    retained.add(backup)
                    rollback_failures.append(f"{path} (backup {backup})")
            if rollback_failures:
                raise OSError("Workspace apply failed and could not restore: " + ", ".join(rollback_failures)) from error
            raise
