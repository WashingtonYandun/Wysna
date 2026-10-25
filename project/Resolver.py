"""Deterministic local dependency graph and import resolver for project mode."""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unicodedata

from project.Manifest import ManifestError, ProjectManifest


_IGNORED_DIRECTORY_NAMES = {".git", ".wysna_cache", "__pycache__", ".pytest_cache", "build", "dist"}


def _portable_key(value):
    return unicodedata.normalize("NFC", value).casefold()


def _hash_files(manifest):
    candidates = [manifest.path]
    for path in manifest.root.rglob("*"):
        relative_parts = path.relative_to(manifest.root).parts
        if relative_parts and relative_parts[0] in _IGNORED_DIRECTORY_NAMES:
            continue
        if path.name in {"wysna.lock", "wysna.lock.tmp"}:
            continue
        if path.is_symlink():
            raise ManifestError(f"dependency input '{path}' is a symlink; vendor its contents directly")
        if path.is_dir() or path == manifest.path:
            continue
        resolved = path.resolve()
        try:
            resolved.relative_to(manifest.root)
        except ValueError as error:
            raise ManifestError(f"dependency input '{path}' resolves outside its package") from error
        if not resolved.is_file():
            continue
        candidates.append(path)

    inputs = []
    seen_paths = {}
    seen_components = {}
    for path in candidates:
        logical = "wysna.toml" if path == manifest.path else path.relative_to(manifest.root).as_posix()
        key = _portable_key(logical)
        previous = seen_paths.get(key)
        if previous is not None and previous != logical:
            raise ManifestError(f"dependency contains paths that collide on portable filesystems: '{previous}' and '{logical}'")
        seen_paths[key] = logical
        parts = logical.split("/")
        for index in range(1, len(parts) + 1):
            component = "/".join(parts[:index])
            component_key = _portable_key(component)
            existing = seen_components.get(component_key)
            if existing is not None and existing != component:
                raise ManifestError(f"dependency path components collide on portable filesystems: '{existing}' and '{component}'")
            seen_components[component_key] = component
        try:
            hasher = hashlib.sha256()
            with path.resolve().open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    hasher.update(chunk)
            digest = hasher.hexdigest()
        except OSError as error:
            raise ManifestError(f"cannot hash dependency input '{path}': {error}") from error
        inputs.append({"path": logical, "sha256": digest})
    inputs.sort(key=lambda item: item["path"])
    content = json.dumps(inputs, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return inputs, hashlib.sha256(content).hexdigest()


@dataclass
class PackageNode:
    manifest: ProjectManifest
    dependencies: dict
    package_id: str


class ProjectResolver:
    """Resolve imports only through the package source roots declared by manifests."""

    def __init__(self, root_manifest, root_package, packages):
        self.manifest = root_manifest
        self.root_package = root_package
        self.packages = packages

    @classmethod
    def load(cls, manifest_path):
        root_manifest = ProjectManifest.load(manifest_path)
        packages_by_path = {}
        active = []

        def visit(manifest):
            key = str(manifest.root.resolve())
            if key in active:
                chain = " -> ".join(active + [key])
                raise ManifestError(f"dependency cycle detected: {chain}")
            existing = packages_by_path.get(key)
            if existing is not None:
                return existing
            active.append(key)
            cls._validate_module_tree(manifest)
            dependencies = {}
            for alias, path in manifest.dependencies.items():
                dependency_manifest = ProjectManifest.load(path / "wysna.toml")
                dependencies[alias] = visit(dependency_manifest)
            active.pop()
            inputs, content_hash = _hash_files(manifest) if manifest.root != root_manifest.root else ([], "")
            identity_source = json.dumps({
                "name": manifest.name,
                "version": manifest.version,
                "manifest_sha256": manifest.raw_sha256,
                "content_sha256": content_hash,
            }, sort_keys=True, separators=(",", ":")).encode("utf-8")
            package_id = manifest.name + "@" + manifest.version + "+" + hashlib.sha256(identity_source).hexdigest()[:16]
            node = PackageNode(manifest, dependencies, package_id)
            node.lock_inputs = inputs
            node.content_sha256 = content_hash
            packages_by_path[key] = node
            return node

        root_package = visit(root_manifest)
        return cls(root_manifest, root_package, packages_by_path)

    @staticmethod
    def _validate_module_tree(manifest):
        seen_components = {}
        for path in manifest.source_root.rglob("*"):
            if path.is_symlink():
                raise ManifestError(f"source module '{path}' is a symlink; place its contents inside sources.root")
            if not path.is_file() or path.suffix != ".wysna":
                continue
            resolved = path.resolve()
            try:
                resolved.relative_to(manifest.source_root.resolve())
            except ValueError as error:
                raise ManifestError(f"source module '{path}' escapes sources.root") from error
            identity = path.relative_to(manifest.source_root).as_posix()
            parts = identity.split("/")
            for index in range(1, len(parts) + 1):
                component = "/".join(parts[:index])
                key = _portable_key(component)
                previous = seen_components.get(key)
                if previous is not None and previous != component:
                    raise ManifestError(f"source modules collide on portable filesystems: '{previous}' and '{component}'")
                seen_components[key] = component

    def resolve_import(self, import_path, importer):
        """Return one physical module path, or None. CWD is never consulted."""
        if import_path.startswith("std/"):
            relative = import_path[4:]
            parts = relative.split("/")
            if (relative.startswith("/") or "\\" in relative or ":" in relative
                    or any(part in ("", ".", "..") for part in parts)):
                return None
            candidate = Path(__file__).resolve().parent.parent / "stdlib" / relative
            if not str(candidate).endswith(".wysna"):
                candidate = Path(str(candidate) + ".wysna")
            return candidate.resolve() if candidate.is_file() else None

        owner = self._owning_package(importer)
        module_root = owner.manifest.source_root
        module_path = import_path
        if import_path.startswith("pkg/"):
            pieces = import_path.split("/")
            if len(pieces) < 3 or any(part in ("", ".", "..") for part in pieces):
                return None
            dependency = owner.dependencies.get(pieces[1])
            if dependency is None:
                return None
            owner = dependency
            module_root = dependency.manifest.source_root
            module_path = "/".join(pieces[2:])
        elif (import_path.startswith("/") or "\\" in import_path or ":" in import_path
              or any(part in ("", ".", "..") for part in import_path.split("/"))):
            return None

        importer_path = Path(importer).resolve()
        try:
            importer_path.relative_to(module_root)
            base = importer_path.parent
        except ValueError:
            # Project test entries can import package sources through sources.root.
            base = module_root

        relative = Path(*module_path.split("/"))
        if "." in relative.name and not module_path.endswith(".wysna"):
            return None
        candidate = base / relative
        if not module_path.endswith(".wysna"):
            candidate = Path(str(candidate) + ".wysna")
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(module_root.resolve())
        except (OSError, ValueError):
            return None
        if not resolved.is_file():
            return None
        return resolved

    def _owning_package(self, importer):
        path = Path(importer).resolve()
        candidates = []
        for package in self.packages.values():
            try:
                path.relative_to(package.manifest.root)
                candidates.append(package)
            except ValueError:
                continue
        if not candidates:
            return self.root_package
        return max(candidates, key=lambda package: len(package.manifest.root.parts))

    def package_for(self, path):
        """Return the declared package containing a physical source path."""
        return self._owning_package(path)

    def lock_data(self):
        root = self.root_package.manifest
        lock_dependencies = []
        for package in self.packages.values():
            if package is self.root_package:
                continue
            lock_dependencies.append({
                "package_id": package.package_id,
                "name": package.manifest.name,
                "version": package.manifest.version,
                "path": package.manifest.root.relative_to(root.root).as_posix(),
                "manifest_sha256": package.manifest.raw_sha256,
                "content_sha256": package.content_sha256,
                "inputs": package.lock_inputs,
                "dependencies": [
                    {"alias": alias, "package_id": dependency.package_id}
                    for alias, dependency in sorted(package.dependencies.items())
                ],
            })
        lock_dependencies.sort(key=lambda item: (item["name"], item["version"], item["package_id"]))
        aliases = []
        for alias, package in sorted(self.root_package.dependencies.items()):
            aliases.append({"alias": alias, "package_id": package.package_id})
        return {
            "lock_version": 1,
            "compiler_version": root.compiler_version,
            "language_version": root.language_version,
            "stdlib": "bundled",
            "root": {
                "name": root.name,
                "version": root.version,
                "manifest_sha256": root.raw_sha256,
            },
            "dependencies": lock_dependencies,
            "aliases": aliases,
        }

    def lock_path(self):
        return self.manifest.root / "wysna.lock"

    def verify_lock(self):
        path = self.lock_path()
        if not path.is_file():
            raise ManifestError("wysna.lock is required by --frozen; run 'wysna project lock' first")
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as error:
            raise ManifestError(f"cannot read lockfile '{path}': {error}") from error
        expected = self.lock_data()
        if current != expected:
            raise ManifestError("wysna.lock is stale; update it with 'wysna project lock'")

    def write_lock(self):
        path = self.lock_path()
        serialized = json.dumps(self.lock_data(), ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        descriptor = None
        temporary = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(prefix=".wysna-lock-", suffix=".tmp",
                                                           dir=self.manifest.root)
            temporary = Path(temporary_name)
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                descriptor = None
                stream.write(serialized)
                stream.flush()
            os.replace(temporary, path)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary is not None and temporary.exists():
                temporary.unlink()
