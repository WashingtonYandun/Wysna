"""Shared precompiled runtime object cache for native C99 test harnesses."""
import hashlib
import os
from pathlib import Path
import platform
import subprocess
import sys
import uuid

from common.Platform import cache_lock

HEADER_RUNTIME_MARKERS = (
    "WYSNA_HAS_HTTP",
    "WYSNA_SANDBOX_ENFORCED",
    "WYSNA_DENY_CAP_",
    "WYSNA_DEFAULT_MAX_",
    "W_STRING_METRICS",
)

_SESSION_CACHE: dict[tuple[str, tuple[str, ...], tuple[str, ...]], tuple[Path, list[str], list[str]] | None] = {}
_FAILED_SENTINEL = object()


def needs_header_runtime(c_path: Path | str) -> bool:
    """Return True when generated C code defines compile-time runtime macros."""
    try:
        with Path(c_path).open("rb") as stream:
            prefix = stream.read(8192).decode("utf-8", errors="ignore")
    except OSError:
        return True
    return any(marker in prefix for marker in HEADER_RUNTIME_MARKERS)


def _runtime_cache_dir(root: Path) -> Path:
    configured = os.environ.get("WYSNA_CACHE_HOME", "").strip()
    cache_root = Path(configured).expanduser() if configured else root / ".wysna_cache"
    cache_dir = cache_root / "runtime-obj"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


def _runtime_object_key(root: Path, compile_cmd: list[str], base_flags: list[str], extra_flags: list[str]) -> str:
    digest = hashlib.sha256()
    parts = [
        "wysna-test-runtime-obj/1",
        platform.system(),
        platform.machine(),
        *compile_cmd,
        *base_flags,
        *extra_flags,
    ]
    for part in parts:
        digest.update(part.encode("utf-8"))
        digest.update(b"\0")
    runtime_dir = root / "runtime"
    inputs = sorted(
        path for path in runtime_dir.iterdir()
        if path.is_file() and (path.suffix in (".h", ".def") or path.name == "wysna_runtime_lib.c")
    )
    for path in inputs:
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def ensure_runtime_object(
    repo_root: Path | str,
    compile_cmd: list[str],
    base_flags: list[str],
) -> tuple[Path, list[str], list[str]] | None:
    """Compile runtime/wysna_runtime_lib.c once per (compiler, flags, runtime headers) tuple."""
    if os.name == "nt" or os.environ.get("WYSNA_DISABLE_LINKED_RUNTIME_TEST") == "1":
        return None
    root = Path(repo_root).resolve()
    runtime_src = root / "runtime/wysna_runtime_lib.c"
    if not runtime_src.is_file():
        return None

    session_key = (str(root), tuple(compile_cmd), tuple(base_flags))
    cached = _SESSION_CACHE.get(session_key, _FAILED_SENTINEL)
    if cached is not _FAILED_SENTINEL:
        if cached is None or cached[0].is_file():
            return cached

    is_linux = sys.platform.startswith("linux")
    extra_rt_flags = ["-pthread", "-DWYSNA_LINK_PTHREAD"] if is_linux else []
    compile_defines = ["-DWYSNA_RUNTIME_LINKED", *extra_rt_flags]
    link_tail = [*extra_rt_flags, "-lm"]

    try:
        cache_dir = _runtime_cache_dir(root)
        key = _runtime_object_key(root, compile_cmd, base_flags, extra_rt_flags)
        final_obj = cache_dir / f"{key}.o"
        if not final_obj.is_file():
            with cache_lock(str(cache_dir / "runtime-obj.lock")):
                if not final_obj.is_file():
                    tmp_obj = cache_dir / f".tmp-{key}-{uuid.uuid4().hex}.o"
                    try:
                        cmd = (
                            list(compile_cmd)
                            + list(base_flags)
                            + extra_rt_flags
                            + ["-c", str(runtime_src), "-o", str(tmp_obj)]
                        )
                        built = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=120)
                        if built.returncode != 0 or not tmp_obj.is_file():
                            _SESSION_CACHE[session_key] = None
                            return None
                        tmp_obj.replace(final_obj)
                    finally:
                        tmp_obj.unlink(missing_ok=True)
    except OSError:
        return None

    result = (final_obj, compile_defines, link_tail)
    _SESSION_CACHE[session_key] = result
    return result
