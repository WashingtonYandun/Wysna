"""Strict parser and validator for Wysna project manifest version 1."""
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
from pathlib import PureWindowsPath
import re

try:
    import tomllib
except ImportError:  # Python 3.10 ships without tomllib.
    from common._vendor import tomli as tomllib

from common.Version import WYSNA_VERSION


class ManifestError(ValueError):
    """A user-facing manifest or lockfile error."""


_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
_SEMVER_NUMBER = r"(?:0|[1-9][0-9]*)"
_SEMVER_PRERELEASE_ID = r"(?:0|[1-9][0-9]*|[0-9A-Za-z-]*[A-Za-z-][0-9A-Za-z-]*)"
_VERSION = re.compile(
    rf"^{_SEMVER_NUMBER}\.{_SEMVER_NUMBER}\.{_SEMVER_NUMBER}"
    rf"(?:-{_SEMVER_PRERELEASE_ID}(?:\.{_SEMVER_PRERELEASE_ID})*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_LANGUAGE_VERSION = ".".join(WYSNA_VERSION.split(".")[:2])
_MAX_TEST_TIMEOUT_MS = 600_000


def _keys(value, allowed, label):
    if not isinstance(value, dict):
        raise ManifestError(f"{label} must be a table")
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ManifestError(f"unknown key in {label}: {unknown[0]}")


def _required_string(value, label):
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{label} must be a non-empty string")
    return value


def _safe_relative_path(value, label):
    value = _required_string(value, label)
    if ("\\" in value or value.startswith("/") or ":" in value or "\x00" in value
            or PureWindowsPath(value).is_absolute()):
        raise ManifestError(f"{label} must be a relative path using '/' separators")
    parts = value.split("/")
    if any(part in ("", ".", "..") for part in parts):
        raise ManifestError(f"{label} must not contain empty, '.' or '..' path components")
    return value


@dataclass(frozen=True)
class ProjectTest:
    name: str
    entry: str
    timeout_ms: int


@dataclass(frozen=True)
class WasiPreopen:
    path: str
    access: str
    host_path: Path


@dataclass(frozen=True)
class Target:
    name: str
    entry: str
    profile: str
    preopens: tuple = ()
    fuel: int = 100_000_000
    memory_limit_mb: int = 128


@dataclass(frozen=True)
class ProjectManifest:
    path: Path
    root: Path
    name: str
    version: str
    language_version: str
    compiler_version: str
    source_root: Path
    targets: dict
    dependencies: dict
    tests: tuple
    raw_sha256: str
    raw: bytes

    @classmethod
    def load(cls, path):
        manifest_path = Path(path).expanduser().resolve()
        try:
            raw = manifest_path.read_bytes()
            data = tomllib.loads(raw.decode("utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            raise ManifestError(f"cannot read manifest '{manifest_path}': {error}") from error
        if not isinstance(data, dict):
            raise ManifestError("manifest root must be a table")
        _keys(data, {"manifest_version", "package", "toolchain", "sources", "targets", "dependencies", "tests"}, "manifest")
        if isinstance(data.get("manifest_version"), bool) or data.get("manifest_version") != 1:
            raise ManifestError("manifest_version must be exactly 1")

        package = data.get("package")
        _keys(package, {"name", "version", "language"}, "package")
        name = _required_string(package.get("name"), "package.name")
        version = _required_string(package.get("version"), "package.version")
        language = _required_string(package.get("language"), "package.language")
        if not _NAME.fullmatch(name):
            raise ManifestError("package.name must start with a lowercase letter and contain only lowercase letters, digits, or hyphens")
        if not _VERSION.fullmatch(version):
            raise ManifestError("package.version must be an exact semantic version")
        if language != _LANGUAGE_VERSION:
            raise ManifestError(f"package.language must be exactly '{_LANGUAGE_VERSION}' for this compiler")

        toolchain = data.get("toolchain")
        _keys(toolchain, {"compiler", "stdlib"}, "toolchain")
        compiler_version = _required_string(toolchain.get("compiler"), "toolchain.compiler")
        stdlib = _required_string(toolchain.get("stdlib"), "toolchain.stdlib")
        if compiler_version != WYSNA_VERSION:
            raise ManifestError(f"toolchain.compiler must exactly match this compiler ({WYSNA_VERSION})")
        if stdlib != "bundled":
            raise ManifestError("toolchain.stdlib must be 'bundled'")

        sources = data.get("sources")
        _keys(sources, {"root"}, "sources")
        source_rel = _safe_relative_path(sources.get("root"), "sources.root")
        root = manifest_path.parent.resolve()
        source_root = _contained_path(root, source_rel, "sources.root", require_exists=True)
        if not source_root.is_dir():
            raise ManifestError("sources.root must name an existing directory")

        raw_targets = data.get("targets")
        if not isinstance(raw_targets, dict) or not raw_targets:
            raise ManifestError("targets must contain at least one named target")
        targets = {}
        for target_name, raw_target in raw_targets.items():
            if not _NAME.fullmatch(target_name):
                raise ManifestError(f"invalid target name '{target_name}'")
            _keys(raw_target, {"entry", "profile", "preopens", "fuel", "memory_limit_mb"},
                  f"targets.{target_name}")
            entry = _safe_relative_path(raw_target.get("entry"), f"targets.{target_name}.entry")
            profile = _required_string(raw_target.get("profile"), f"targets.{target_name}.profile")
            if profile not in ("native-cli", "wasi-command", "browser-library"):
                raise ManifestError(
                    f"targets.{target_name}.profile must be 'native-cli', 'wasi-command', or 'browser-library' in manifest version 1"
                )
            raw_preopens = raw_target.get("preopens", [])
            if not isinstance(raw_preopens, list):
                raise ManifestError(f"targets.{target_name}.preopens must be an array of tables")
            preopens = []
            seen_preopens = set()
            for index, raw_preopen in enumerate(raw_preopens):
                label = f"targets.{target_name}.preopens[{index}]"
                _keys(raw_preopen, {"path", "access"}, label)
                relative = _safe_relative_path(raw_preopen.get("path"), f"{label}.path")
                access = _required_string(raw_preopen.get("access"), f"{label}.access")
                if access not in ("read-only", "read-write"):
                    raise ManifestError(f"{label}.access must be 'read-only' or 'read-write'")
                if relative in seen_preopens:
                    raise ManifestError(f"{label}.path duplicates another preopen")
                seen_preopens.add(relative)
                preopen_path = _contained_path(root, relative, f"{label}.path", require_exists=True)
                if not preopen_path.is_dir():
                    raise ManifestError(f"{label}.path must name an existing directory")
                preopens.append(WasiPreopen(relative, access, preopen_path))
            fuel = raw_target.get("fuel", 100_000_000)
            if isinstance(fuel, bool) or not isinstance(fuel, int) or not 1_000 <= fuel <= 10_000_000_000:
                raise ManifestError(f"targets.{target_name}.fuel must be between 1000 and 10000000000")
            memory_limit_mb = raw_target.get("memory_limit_mb", 128)
            if (isinstance(memory_limit_mb, bool) or not isinstance(memory_limit_mb, int)
                    or not 8 <= memory_limit_mb <= 1024):
                raise ManifestError(f"targets.{target_name}.memory_limit_mb must be between 8 and 1024")
            wasi_settings = {"preopens", "fuel"}.intersection(raw_target)
            memory_setting = "memory_limit_mb" in raw_target
            if profile == "native-cli" and (wasi_settings or memory_setting):
                raise ManifestError(
                    f"targets.{target_name} resource settings require a WASM target profile"
                )
            if profile == "browser-library" and wasi_settings:
                raise ManifestError(
                    f"targets.{target_name} preopens and fuel require profile = 'wasi-command'"
                )
            entry_path = _contained_path(root, entry, f"targets.{target_name}.entry", require_exists=True)
            if not entry_path.is_file() or entry_path.suffix != ".wysna":
                raise ManifestError(f"targets.{target_name}.entry must name an existing .wysna source file")
            targets[target_name] = Target(target_name, entry, profile, tuple(preopens), fuel,
                                          memory_limit_mb)

        raw_dependencies = data.get("dependencies", {})
        if not isinstance(raw_dependencies, dict):
            raise ManifestError("dependencies must be a table")
        dependencies = {}
        for alias, raw_dependency in raw_dependencies.items():
            if not _NAME.fullmatch(alias):
                raise ManifestError(f"invalid dependency alias '{alias}'")
            _keys(raw_dependency, {"path"}, f"dependencies.{alias}")
            dependency_rel = _safe_relative_path(raw_dependency.get("path"), f"dependencies.{alias}.path")
            dependency_path = _contained_path(root, dependency_rel, f"dependencies.{alias}.path", require_exists=True)
            if not dependency_path.is_dir():
                raise ManifestError(f"dependencies.{alias}.path must name a directory")
            dependencies[alias] = dependency_path

        raw_tests = data.get("tests", [])
        if not isinstance(raw_tests, list):
            raise ManifestError("tests must be an array of tables")
        tests, seen_names = [], set()
        for index, raw_test in enumerate(raw_tests):
            _keys(raw_test, {"name", "entry", "timeout_ms"}, f"tests[{index}]")
            test_name = _required_string(raw_test.get("name"), f"tests[{index}].name")
            if not _NAME.fullmatch(test_name) or test_name in seen_names:
                raise ManifestError(f"tests[{index}].name must be unique and use lowercase letters, digits, or hyphens")
            seen_names.add(test_name)
            entry = _safe_relative_path(raw_test.get("entry"), f"tests[{index}].entry")
            entry_path = _contained_path(root, entry, f"tests[{index}].entry", require_exists=True)
            if not entry_path.is_file() or entry_path.suffix != ".wysna":
                raise ManifestError(f"tests[{index}].entry must name an existing .wysna source file")
            timeout_ms = raw_test.get("timeout_ms", 10_000)
            if isinstance(timeout_ms, bool) or not isinstance(timeout_ms, int) or not 1 <= timeout_ms <= _MAX_TEST_TIMEOUT_MS:
                raise ManifestError(f"tests[{index}].timeout_ms must be between 1 and {_MAX_TEST_TIMEOUT_MS}")
            tests.append(ProjectTest(test_name, entry, timeout_ms))

        return cls(manifest_path, root, name, version, language, compiler_version,
                   source_root, targets, dependencies, tuple(tests),
                   hashlib.sha256(raw).hexdigest(), raw)


def _contained_path(root, relative, label, *, require_exists):
    root = Path(root).resolve()
    candidate = root.joinpath(*relative.split("/"))
    try:
        resolved = candidate.resolve(strict=require_exists)
        resolved.relative_to(root)
    except (OSError, ValueError) as error:
        raise ManifestError(f"{label} escapes the project root or cannot be resolved") from error
    if os.path.commonpath((str(root), str(resolved))) != str(root):
        raise ManifestError(f"{label} escapes the project root")
    return resolved
