"""Pinned Emscripten build path for Wysna browser-library artifacts."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

from codegen.BrowserBindings import generate_browser_bindings
from common.WasmModule import inspect_wasm


EMSCRIPTEN_VERSION = "5.0.1"
EMSCRIPTEN_RUNTIME_METHODS = ("HEAPU8",)


class BrowserLibraryCompilationError(Exception):
    """An Emscripten setup, compilation, or browser ABI validation failure."""

    def __init__(self, phase, message, returncode=None):
        super().__init__(message)
        self.phase = phase
        self.stderr = message
        self.returncode = returncode


@dataclass(frozen=True)
class BrowserLibraryBuildResult:
    output_path: str
    artifacts: tuple
    compile_ms: float
    compiler_path: str
    compiler_version: str
    wasm_exports: tuple
    wasm_imports: tuple
    memory_limit_bytes: int


class BrowserLibraryToolchain:
    """Compile generated C99 and a generated ES module facade for the browser-library profile."""

    def __init__(self, repo_root, compiler_path=None):
        self.repo_root = Path(repo_root).resolve()
        selected = compiler_path or os.environ.get("EMCC") or "emcc"
        candidate = Path(selected).expanduser()
        if candidate.is_file():
            self.compiler_path = str(candidate.resolve())
        else:
            self.compiler_path = shutil.which(str(candidate))

    def _compiler(self):
        if not self.compiler_path or not Path(self.compiler_path).is_file():
            raise BrowserLibraryCompilationError(
                "configuration",
                f"Emscripten {EMSCRIPTEN_VERSION} was not found; install emsdk and set EMCC to emcc.",
            )
        try:
            result = subprocess.run([self.compiler_path, "--version"], capture_output=True,
                                    text=True, timeout=15)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise BrowserLibraryCompilationError(
                "configuration", f"cannot inspect Emscripten compiler: {error}") from error
        if result.returncode != 0:
            raise BrowserLibraryCompilationError(
                "configuration", result.stderr or "cannot inspect Emscripten compiler",
                result.returncode)
        version = (result.stdout or result.stderr).splitlines()[0].strip()
        if EMSCRIPTEN_VERSION not in version:
            raise BrowserLibraryCompilationError(
                "configuration",
                f"browser-library requires Emscripten {EMSCRIPTEN_VERSION}; found '{version}'.",
            )
        return version

    @staticmethod
    def _output_paths(output):
        requested = Path(output).expanduser()
        base = requested.with_suffix("") if requested.suffix else requested
        wrapper = base.with_suffix(".mjs")
        runtime_module = base.with_name(base.name + ".runtime.mjs")
        wasm = runtime_module.with_suffix(".wasm")
        return wrapper, runtime_module, wasm

    def build(self, c_source, output, abi_exports, *, memory_limit_mb=128):
        compiler_version = self._compiler()
        if not abi_exports:
            raise BrowserLibraryCompilationError(
                "target-validation", "browser-library requires at least one supported public function.")
        if isinstance(memory_limit_mb, bool) or not isinstance(memory_limit_mb, int) or not 8 <= memory_limit_mb <= 1024:
            raise BrowserLibraryCompilationError(
                "configuration", "browser-library memory limit must be between 8 and 1024 MiB.")

        wrapper_path, runtime_module_path, wasm_path = self._output_paths(output)
        wrapper_path.parent.mkdir(parents=True, exist_ok=True)
        memory_limit_bytes = memory_limit_mb * 1024 * 1024
        initial_memory_bytes = min(16, memory_limit_mb) * 1024 * 1024
        exported_symbols = [
            "_wysna_web_init", "_wysna_web_destroy", "_wysna_web_alloc", "_wysna_web_free",
            *("_" + item["symbol"] for item in abi_exports),
        ]
        command = [
            self.compiler_path, "-std=c99", "-O2", f"-I{self.repo_root}",
            "-Wl,--no-entry",
            "-sMODULARIZE=1", "-sEXPORT_ES6=1", "-sEXPORT_NAME=createWysnaModule",
            "-sENVIRONMENT=web,node", "-sNO_EXIT_RUNTIME=1", "-sFILESYSTEM=0",
            "-sWASM_BIGINT=1", "-sALLOW_MEMORY_GROWTH=1",
            "-sEXPORTED_RUNTIME_METHODS=" + ",".join(EMSCRIPTEN_RUNTIME_METHODS),
            f"-sINITIAL_MEMORY={initial_memory_bytes}",
            f"-sMAXIMUM_MEMORY={memory_limit_bytes}", "-sSTACK_SIZE=1048576",
            "-sERROR_ON_UNDEFINED_SYMBOLS=1",
            "-sEXPORTED_FUNCTIONS=" + json.dumps(exported_symbols, separators=(",", ":")),
        ]

        with tempfile.TemporaryDirectory(prefix="wysna-emscripten-", dir=wrapper_path.parent) as temporary:
            temporary_path = Path(temporary)
            source_path = temporary_path / "program.c"
            runtime_candidate = temporary_path / runtime_module_path.name
            wasm_candidate = runtime_candidate.with_suffix(".wasm")
            wrapper_candidate = temporary_path / wrapper_path.name
            source_path.write_text(c_source, encoding="utf-8")
            started = time.perf_counter()
            try:
                result = subprocess.run(command + [str(source_path), "-o", str(runtime_candidate)],
                                        capture_output=True, text=True, timeout=240)
            except (OSError, subprocess.TimeoutExpired) as error:
                raise BrowserLibraryCompilationError(
                    "compile", f"Emscripten invocation failed: {error}") from error
            compile_ms = (time.perf_counter() - started) * 1000.0
            if result.returncode != 0:
                detail = result.stderr.strip() or result.stdout.strip() or "Emscripten compilation failed"
                raise BrowserLibraryCompilationError("compile", detail, result.returncode)
            if not runtime_candidate.is_file() or not wasm_candidate.is_file():
                raise BrowserLibraryCompilationError(
                    "compile", "Emscripten did not produce both the ES module and WebAssembly module.")

            try:
                info = inspect_wasm(wasm_candidate)
                self._validate_module(info, exported_symbols, memory_limit_bytes)
            except ValueError as error:
                raise BrowserLibraryCompilationError("target-validation", str(error)) from error

            wrapper_candidate.write_text(
                generate_browser_bindings(runtime_module_path.name, abi_exports), encoding="utf-8")
            os.replace(wasm_candidate, wasm_path)
            os.replace(runtime_candidate, runtime_module_path)
            os.replace(wrapper_candidate, wrapper_path)

        return BrowserLibraryBuildResult(
            str(wrapper_path), (str(wrapper_path), str(runtime_module_path), str(wasm_path)),
            compile_ms, self.compiler_path, compiler_version, info.exports, info.imports,
            memory_limit_bytes,
        )

    @staticmethod
    def _validate_module(info, required_exports, memory_limit_bytes):
        missing = sorted(symbol.lstrip("_") for symbol in required_exports
                         if symbol.lstrip("_") not in info.exports)
        if missing:
            raise ValueError("browser-library module is missing required exports: " + ", ".join(missing))
        private = sorted(name for name in info.exports
                         if name.startswith(("wysna_fn_", "wysna_method_", "wysna_adapter_")))
        if private:
            raise ValueError("browser-library module exports private Wysna functions: " + ", ".join(private))
        unsupported_modules = sorted({item["module"] for item in info.imports} - {"env", "wasi_snapshot_preview1"})
        if unsupported_modules:
            raise ValueError("browser-library module imports unsupported host modules: "
                             + ", ".join(unsupported_modules))
        if len(info.memories) != 1:
            raise ValueError("browser-library module must define exactly one WebAssembly memory")
        memory = info.memories[0]
        if memory["imported"] or memory["memory64"] or memory["shared"]:
            raise ValueError("browser-library module must use one private wasm32 memory")
        maximum_pages = memory["maximum_pages"]
        if maximum_pages is None:
            raise ValueError("browser-library memory must declare a maximum size")
        if maximum_pages < memory["minimum_pages"]:
            raise ValueError("browser-library memory maximum is smaller than its minimum")
        maximum_bytes = maximum_pages * 65_536
        if maximum_bytes > memory_limit_bytes:
            raise ValueError("browser-library memory maximum exceeds the configured target limit")
