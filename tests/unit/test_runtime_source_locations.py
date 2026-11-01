"""Native runtime panics must point at the Wysna operation that caused them."""

import os
from pathlib import Path
import subprocess
import sys
import tempfile


CASES = [
    ("division", "fn main() {\n    print(12 / 0)\n}\nmain()\n", 2, "/", "Division by zero"),
    (
        "nested_division",
        "fn main() {\n    print(12 /\n        (4 / 0))\n}\nmain()\n",
        3,
        "/",
        "Division by zero",
    ),
    ("float_division", "fn main() {\n    print(12.0 / 0.0)\n}\nmain()\n", 2, "/", "Division by zero"),
    ("modulo", "fn main() {\n    print(12 % 0)\n}\nmain()\n", 2, "%", "Modulo by zero"),
    (
        "index_read",
        "fn main() {\n    let values = [1]\n    print(values[3])\n}\nmain()\n",
        3,
        "[",
        "List index out of bounds",
    ),
    (
        "string_index",
        'fn main() {\n    let text = "w"\n    print(text[2])\n}\nmain()\n',
        3,
        "[",
        "String index out of bounds",
    ),
    (
        "dictionary_index",
        'fn main() {\n    let values = {"key": 1}\n    print(values["missing"])\n}\nmain()\n',
        3,
        "[",
        "Dictionary key not found",
    ),
    (
        "index_write",
        "fn main() {\n    let mut values = [1]\n    values[3] = 4\n}\nmain()\n",
        3,
        "[",
        "List index out of bounds",
    ),
    (
        "empty_pop",
        "fn main() {\n    let mut values = [1]\n    values.clear()\n    values.pop()\n}\nmain()\n",
        4,
        "(",
        "pop from empty list",
    ),
]


def _emit_c(root: Path, source: Path, c_file: Path, backend: str, selfhost_compiler: str) -> None:
    if backend == "selfhost":
        result = subprocess.run(
            [selfhost_compiler, str(source), str(c_file)],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    elif backend == "ast":
        result = subprocess.run(
            [sys.executable, str(root / "tests/emit_ast_oracle.py"), str(source), str(c_file)],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    else:
        result = subprocess.run(
            [
                sys.executable,
                "main.py",
                "emit-c",
                str(source),
                "-o",
                str(c_file),
            ],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=120,
        )
    assert result.returncode == 0, (backend, result.stdout, result.stderr)


def test_native_runtime_panics_report_exact_wysna_source_location():
    root = Path(__file__).resolve().parents[2]
    compiler = os.environ.get("CC", "cc")
    from main import ensure_selfhost_compiler_binary

    selfhost_compiler = ensure_selfhost_compiler_binary(str(root))

    with tempfile.TemporaryDirectory(prefix="wysna-f02-locations-") as directory:
        temp = Path(directory)
        for backend in ("ast", "hir", "selfhost"):
            for name, source_text, line, marker, message in CASES:
                source = temp / f"{name}.wysna"
                c_file = temp / f"{name}-{backend}.c"
                binary = temp / f"{name}-{backend}"
                source.write_text(source_text, encoding="utf-8")

                _emit_c(root, source, c_file, backend, selfhost_compiler)
                built = subprocess.run(
                    [compiler, "-std=c99", f"-I{root}", str(c_file), "-o", str(binary)],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                assert built.returncode == 0, (backend, built.stderr)

                column = source_text.splitlines()[line - 1].index(marker) + 1
                # All engines report canonical module paths; on macOS the temporary
                # directory may be reached through the /var -> /private/var symlink.
                source_name = source.resolve()
                error_code = "W4001" if "zero" in message.lower() else "W4002"
                expected = f"[{error_code}] {source_name}:{line}:{column} - {message}\n"
                env = os.environ.copy()
                env.pop("WYSNA_MEM_CHECK", None)
                result = subprocess.run(
                    [str(binary)],
                    cwd=root,
                    env=env,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                assert result.returncode != 0, (backend, name, result.stdout, result.stderr)
                assert result.stderr == expected, (backend, name, result.stderr, expected)
