"""Keep self-host diagnostics and runtime source locations tied to imported files."""

import os
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_imported_module_diagnostics_and_runtime_locations_use_dependency_path():
    from main import ensure_selfhost_compiler_binary

    compiler = ensure_selfhost_compiler_binary(str(ROOT))
    with tempfile.TemporaryDirectory(prefix="selfhost-source-provenance-") as directory:
        root = Path(directory)
        entry = root / "main.wysna"
        dependency = root / "dependency.wysna"
        output_c = root / "program.c"

        entry.write_text('import "dependency.wysna"\n')
        dependency.write_text("pub fn broken() -> int { return missing }\n")
        rejected = subprocess.run(
            [compiler, str(entry), str(output_c)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert rejected.returncode != 0, "Wysna Compiler accepted an unknown name in an imported module"
        assert f"{dependency}:1:" in rejected.stderr, rejected.stderr
        assert f"{entry}:" not in rejected.stderr, rejected.stderr
        assert not output_c.exists(), "Wysna Compiler must reject invalid imported source before emitting C"

        entry.write_text('import "dependency.wysna"\nprint(dependency.divide())\n')
        dependency.write_text("pub fn divide() -> int { return 1 / 0 }\n")
        built = subprocess.run(
            [compiler, str(entry), str(output_c)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert built.returncode == 0, built.stderr

        binary = root / "program"
        compiled = subprocess.run(
            [os.environ.get("CC", "cc"), "-std=c99", f"-I{ROOT}", str(output_c), "-o", str(binary)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert compiled.returncode == 0, compiled.stderr

        executed = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
        assert executed.returncode != 0, "division by zero should fail at runtime"
        assert f"{dependency}:1:" in executed.stderr, executed.stderr


if __name__ == "__main__":
    test_imported_module_diagnostics_and_runtime_locations_use_dependency_path()
    print("Wysna Compiler imported-source provenance passed")
