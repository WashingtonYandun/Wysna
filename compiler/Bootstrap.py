"""Build and cache a native MIR compiler with explicit seed provenance."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import uuid

from common.Platform import cache_lock, compiler_command, executable_name, native_stack_link_args
from common.Paths import get_repo_root
from compiler.CompilerService import CompilerService


ROOT = Path(get_repo_root())
SELFHOST_BOOTSTRAP_INPUTS = ("selfhost/compiler.wysna", "tools/bundle_selfhost.py") + tuple(sorted(
    str(path.relative_to(ROOT))
    for package in ("lexer", "parser", "node", "checker", "compiler", "codegen", "common",
                    "error", "hir", "mir", "tokens", "runtime", "selfhost/src")
    for path in (ROOT / package).rglob("*")
    if path.is_file() and (path.suffix == ".py" or package == "runtime" and path.suffix in (".h", ".c")
                           or package == "selfhost/src" and path.suffix == ".wysna")
))


@dataclass(frozen=True)
class BootstrapArtifacts:
    directory: Path
    fingerprint: str
    hashes: tuple[str, str, str]

    @property
    def compiler(self):
        return self.directory / executable_name("stage2")


def _sha256(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def bootstrap_fingerprint(repo_root):
    root = Path(repo_root).resolve()
    inputs = {name: _sha256(root / name) for name in SELFHOST_BOOTSTRAP_INPUTS}
    provenance = {"format": 1, "source": str(root / "selfhost/compiler.wysna"),
                  "platform": platform.system(), "architecture": platform.machine(),
                  "compiler": compiler_command(), "optimization": "-O1", "inputs": inputs}
    encoded = json.dumps(provenance, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest(), provenance


def bootstrap_compiler(repo_root, output_directory):
    """Seed HIR/MIR builds a bootstrap executable; three native MIR emissions must agree."""
    root = Path(repo_root).resolve()
    directory = Path(output_directory).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    source_path = root / "selfhost/compiler.wysna"
    from tools.bundle_selfhost import bundle
    source = source_path.read_text(encoding="utf-8")
    if bundle(str(root / "selfhost/src")) != source:
        raise RuntimeError("The compiler bundle is stale; run tools/bundle_selfhost.py")
    fingerprint, provenance = bootstrap_fingerprint(root)
    service = CompilerService()
    seed_source = service.emit_c(service.check(source, source_path)).source
    bootstrap_c = directory / "bootstrap.c"
    bootstrap_c.write_text(seed_source, encoding="utf-8")

    def compile_c(path, name):
        binary = directory / executable_name(name)
        math_flags = [] if os.name == "nt" else ["-lm"]
        command = compiler_command()
        built = subprocess.run(command + ["-std=c99", "-O1", "-I" + str(root),
                               str(path)] + math_flags + native_stack_link_args(command) + ["-o", str(binary)],
                               capture_output=True, text=True, timeout=120, cwd=root)
        if built.returncode:
            from error.Error import ToolchainError
            raise ToolchainError("Bootstrap C compilation failed: " + built.stdout + built.stderr)
        return binary

    binary = compile_c(bootstrap_c, "bootstrap")
    for stage in range(3):
        emitted = directory / ("compiler" + str(stage) + ".c")
        subprocess.run([str(binary), str(source_path), str(emitted)], check=True,
                       capture_output=True, text=True, timeout=120, cwd=root)
        if stage < 2:
            binary = compile_c(emitted, "stage" + str(stage + 1))
    hashes = tuple(_sha256(directory / ("compiler" + str(stage) + ".c")) for stage in range(3))
    if bootstrap_fingerprint(root)[0] != fingerprint:
        raise RuntimeError("Compiler inputs changed during bootstrap; artifacts were not accepted")
    if len(set(hashes)) != 1:
        raise RuntimeError("Native MIR bootstrap fixed point failed: " + ", ".join(hashes))
    manifest = {"format": 1, "fingerprint": fingerprint, "provenance": provenance,
                "seed_backend": "hir", "seed_c_sha256": _sha256(bootstrap_c),
                "native_backend": "verified-mir-c99", "native_c_sha256": hashes,
                "stage1_sha256": _sha256(directory / executable_name("stage1")),
                "stage2_sha256": _sha256(directory / executable_name("stage2"))}
    (directory / "bootstrap.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    return BootstrapArtifacts(directory, fingerprint, hashes)


def _cached_bootstrap_matches(directory, manifest, fingerprint):
    hashes = manifest["native_c_sha256"]
    if not isinstance(hashes, list) or len(hashes) != 3 or not all(isinstance(value, str) for value in hashes):
        return False
    return (manifest["fingerprint"] == fingerprint and len(set(hashes)) == 1
            and manifest["seed_c_sha256"] == _sha256(directory / "bootstrap.c")
            and all(hashes[stage] == _sha256(directory / ("compiler" + str(stage) + ".c"))
                    for stage in range(3))
            and all(manifest["stage" + str(stage) + "_sha256"] ==
                    _sha256(directory / executable_name("stage" + str(stage))) for stage in (1, 2)))


def ensure_selfhost_compiler_binary(repo_root: str) -> str:
    """Publish a validated, versioned cache entry without replacing a running binary."""
    root = Path(repo_root).resolve()
    source = root / "selfhost/compiler.wysna"
    if not source.is_file():
        from error.Error import TypeError as WysnaTypeError
        raise WysnaTypeError("Wysna Compiler bootstrap requires a source checkout", file=str(source), code="W2013")
    configured_cache = os.environ.get("WYSNA_CACHE_HOME")
    cache_root = Path(configured_cache).expanduser() if configured_cache else root / ".wysna_cache"
    cache = cache_root / "bootstrap"
    cache.mkdir(parents=True, exist_ok=True)
    with cache_lock(str(cache / "bootstrap.lock")):
        fingerprint, _ = bootstrap_fingerprint(root)
        destination = cache / fingerprint
        index_path = cache / (fingerprint + ".json")
        if index_path.is_file():
            try:
                indexed = json.loads(index_path.read_text(encoding="utf-8"))["directory"]
                if isinstance(indexed, str) and indexed not in ("", ".", "..") and Path(indexed).name == indexed:
                    destination = cache / indexed
            except (ValueError, KeyError, OSError, TypeError):
                pass
        binary = destination / executable_name("stage2")
        manifest_path = destination / "bootstrap.json"
        if binary.is_file() and manifest_path.is_file():
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
                if _cached_bootstrap_matches(destination, manifest, fingerprint):
                    return str(binary)
            except (ValueError, KeyError, OSError, TypeError):
                pass
        if destination.exists():
            # A damaged cache is never used or overwritten while a binary may be open.
            destination = cache / (fingerprint + "-" + uuid.uuid4().hex)
        with tempfile.TemporaryDirectory(prefix="pending-", dir=cache) as temporary:
            artifacts = bootstrap_compiler(root, temporary)
            if artifacts.fingerprint != fingerprint:
                raise RuntimeError("Compiler inputs changed before bootstrap started")
            Path(temporary).rename(destination)
        index_temporary = cache / ("index-" + uuid.uuid4().hex + ".json")
        try:
            index_temporary.write_text(json.dumps({"directory": destination.name}) + "\n", encoding="utf-8")
            index_temporary.replace(index_path)
        finally:
            index_temporary.unlink(missing_ok=True)
        return str(destination / executable_name("stage2"))
