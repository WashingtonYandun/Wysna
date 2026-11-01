"""Pinned-target WASI compilation through the external wasi-sdk C toolchain."""
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import tempfile
import time

from common.WasmModule import inspect_wasm, validate_wasi_command


WASI_STACK_SIZE_BYTES = 1_048_576


class WasiCompilationError(Exception):
    """A WASI SDK setup, compilation, or target validation failure."""

    def __init__(self, phase, message, returncode=None):
        super().__init__(message)
        self.phase = phase
        self.stderr = message
        self.returncode = returncode


@dataclass(frozen=True)
class WasiBuildResult:
    output_path: str
    compile_ms: float
    sdk_path: str
    compiler_path: str
    compiler_version: str
    imports: tuple
    exports: tuple


class WasiToolchain:
    """Compile Seed-generated C99 for the wasi-command target profile."""

    def __init__(self, repo_root, sdk_path=None):
        self.repo_root = Path(repo_root).resolve()
        selected = sdk_path or os.environ.get("WASI_SDK_PATH") or "/opt/wasi-sdk"
        self.sdk_path = Path(selected).expanduser().resolve()

    def _compiler(self):
        compiler = self.sdk_path / "bin" / ("clang.exe" if os.name == "nt" else "clang")
        sysroot = self.sdk_path / "share" / "wasi-sysroot"
        if not compiler.is_file() or not sysroot.is_dir():
            raise WasiCompilationError(
                "configuration",
                "WASI SDK was not found. Install wasi-sdk 34 and set WASI_SDK_PATH to its directory.",
            )
        try:
            result = subprocess.run([str(compiler), "--version"], capture_output=True,
                                    text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise WasiCompilationError("configuration", f"cannot inspect WASI compiler: {error}") from error
        if result.returncode != 0:
            raise WasiCompilationError("configuration", result.stderr or "cannot inspect WASI compiler",
                                       result.returncode)
        version = (result.stdout or result.stderr).splitlines()[0].strip()
        return compiler, sysroot, version

    def build(self, c_source, output):
        compiler, sysroot, compiler_version = self._compiler()
        output_path = Path(output).expanduser()
        if output_path.suffix.lower() != ".wasm":
            output_path = output_path.with_suffix(".wasm")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="wysna-wasi-") as temporary:
            source_path = Path(temporary) / "program.c"
            candidate = Path(temporary) / "program.wasm"
            source_path.write_text(c_source, encoding="utf-8")
            command = [str(compiler), "--target=wasm32-wasip1",
                       f"--sysroot={sysroot}", "-std=c99", "-O2",
                       f"-Wl,-z,stack-size={WASI_STACK_SIZE_BYTES}", "-Wl,--stack-first",
                       f"-I{self.repo_root}", str(source_path), "-o", str(candidate)]
            started = time.perf_counter()
            try:
                result = subprocess.run(command, capture_output=True, text=True, timeout=120)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise WasiCompilationError("compile", f"WASI SDK invocation failed: {error}") from error
            compile_ms = (time.perf_counter() - started) * 1000.0
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip() or "WASI compilation failed"
                raise WasiCompilationError("compile", detail, result.returncode)
            try:
                info = inspect_wasm(candidate)
                validate_wasi_command(info)
            except ValueError as error:
                raise WasiCompilationError("target-validation", str(error)) from error
            os.replace(candidate, output_path)
        return WasiBuildResult(str(output_path), compile_ms, str(self.sdk_path),
                               str(compiler), compiler_version,
                               info.imports, info.exports)
