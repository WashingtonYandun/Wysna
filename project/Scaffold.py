"""Create a minimal project without replacing existing user files."""

from pathlib import Path
import os
import stat

from common.Version import WYSNA_VERSION
from project.Manifest import ManifestError, validate_package_name


class ScaffoldError(OSError):
    """A filesystem failure while creating a new project."""


def create_project(name, parent):
    validate_package_name(name)
    root = Path(parent).resolve() / name
    language = ".".join(WYSNA_VERSION.split(".")[:2])
    files = {
        "wysna.toml": (
            'manifest_version = 1\n\n[package]\n'
            f'name = "{name}"\nversion = "0.1.0"\nlanguage = "{language}"\n\n'
            f'[toolchain]\ncompiler = "{WYSNA_VERSION}"\nstdlib = "bundled"\n\n'
            '[sources]\nroot = "src"\n\n[targets.app]\n'
            'entry = "src/main.wysna"\nprofile = "native-cli"\n\n'
            '[[tests]]\nname = "example"\nentry = "tests/example.wysna"\n'
            'timeout_ms = 10000\n'
        ),
        "src/main.wysna": 'fn main() -> unit {\n    print("Hello, Wysna!")\n}\n',
        "tests/example.wysna": (
            'import "std/test"\n\nfn main() -> unit {\n'
            '    test.expect_equal_int(1 + 1, 2, "addition")\n}\n'
        ),
    }
    try:
        root.mkdir()
    except FileExistsError as error:
        raise ManifestError(f"project destination already exists: '{root}'") from error
    except OSError as error:
        raise ScaffoldError(f"cannot create project directory '{root}': {error}") from error

    created = []
    try:
        (root / "src").mkdir()
        (root / "tests").mkdir()
        for relative_name, text in files.items():
            path = root / relative_name
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                info = os.fstat(stream.fileno())
                created.append((path, info.st_dev, info.st_ino, text.encode("utf-8")))
                stream.write(text)
    except BaseException as error:
        # Remove only unchanged regular files that this invocation created. If
        # another process changed anything, preserve it and report the remainder.
        for path, device, inode, content in reversed(created):
            try:
                info = path.lstat()
                if (stat.S_ISREG(info.st_mode) and info.st_dev == device and info.st_ino == inode
                        and path.read_bytes() == content):
                    path.unlink()
            except OSError:
                pass
        for path in (root / "tests", root / "src", root):
            try:
                path.rmdir()
            except OSError:
                pass
        if isinstance(error, OSError):
            remaining = f"; inspect the partially created directory '{root}'" if root.exists() else ""
            raise ScaffoldError(f"cannot create project '{root}': {error}{remaining}") from error
        raise
    return root, tuple(root / relative_name for relative_name in files)
