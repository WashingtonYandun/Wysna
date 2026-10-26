"""Wasmtime host for capability-limited ``wasi-command`` programs."""
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
import tempfile
import time

from project.Manifest import WasiPreopen
from common.WasmModule import inspect_wasm, validate_wasi_command


WASMTIME_VERSION = "50.0.1"
_MAX_CAPTURE_BYTES = 1_048_576


def _read_capped_output(path):
    with Path(path).open("rb") as output_file:
        content = output_file.read(_MAX_CAPTURE_BYTES + 1)
    truncated = len(content) > _MAX_CAPTURE_BYTES
    return content[:_MAX_CAPTURE_BYTES], truncated


class WasiRuntimeError(RuntimeError):
    """A missing runtime, invalid module, denied capability, or guest trap."""


@dataclass(frozen=True)
class WasiRunResult:
    exit_code: int
    stdout: bytes
    stderr: bytes
    stdout_truncated: bool
    stderr_truncated: bool
    fuel_remaining: int
    elapsed_ms: float


class WasiRunner:
    def __init__(self, *, expected_version=WASMTIME_VERSION):
        try:
            import wasmtime
        except ImportError as error:
            raise WasiRuntimeError(
                f"Wasmtime is required to run WASI projects; install wasmtime=={expected_version}"
            ) from error
        try:
            actual_version = version("wasmtime")
        except PackageNotFoundError:
            actual_version = getattr(wasmtime, "__version__", None)
        if actual_version != expected_version:
            raise WasiRuntimeError(
                f"wasi-command requires Wasmtime {expected_version}; found {actual_version or 'unknown'}"
            )
        self.wasmtime = wasmtime

    def run(self, module_path, *, project_root, arguments=(), preopens=(), fuel=100_000_000,
            memory_limit_mb=128, capture_output=False):
        module_path = Path(module_path).resolve()
        project_root = Path(project_root).resolve()
        try:
            info = inspect_wasm(module_path)
            validate_wasi_command(info)
        except ValueError as error:
            raise WasiRuntimeError(f"invalid WASI command module: {error}") from error
        wasmtime = self.wasmtime
        config = wasmtime.Config()
        config.consume_fuel = True
        engine = wasmtime.Engine(config)
        capture_directory = None
        try:
            module = wasmtime.Module.from_file(engine, str(module_path))
            store = wasmtime.Store(engine)
            store.set_limits(memory_size=memory_limit_mb * 1024 * 1024,
                             table_elements=100_000, instances=1, tables=4, memories=1)
            store.set_fuel(fuel)
            wasi = wasmtime.WasiConfig()
            wasi.argv = ["wysna", *arguments]
            wasi.env = []
            wasi.inherit_stdin()
            if capture_output:
                capture_directory = tempfile.TemporaryDirectory(prefix="wysna-wasi-output-")
                stdout_path = Path(capture_directory.name) / "stdout.bin"
                stderr_path = Path(capture_directory.name) / "stderr.bin"
                wasi.stdout_file = str(stdout_path)
                wasi.stderr_file = str(stderr_path)
            else:
                wasi.inherit_stdout()
                wasi.inherit_stderr()
            for preopen in preopens:
                if not isinstance(preopen, WasiPreopen):
                    raise WasiRuntimeError("project manifest contains an invalid WASI preopen")
                try:
                    preopen.host_path.resolve().relative_to(project_root)
                except ValueError as error:
                    raise WasiRuntimeError(
                        f"WASI preopen escapes the project root: '{preopen.path}'"
                    ) from error
                guest_path = "/" + preopen.path.replace("\\", "/")
                wasi.preopen_dir(str(preopen.host_path), guest_path,
                                 fs_mutable=preopen.access == "read-write")
            store.set_wasi(wasi)
            linker = wasmtime.Linker(engine)
            linker.define_wasi()
            instance = linker.instantiate(store, module)
            start = instance.exports(store)["_start"]
            started = time.perf_counter()
            exit_code = 0
            try:
                start(store)
            except wasmtime.ExitTrap as error:
                exit_code = error.code
            except (wasmtime.Trap, wasmtime.WasmtimeError) as error:
                raise WasiRuntimeError(f"WASI guest trapped: {error}") from error
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            remaining = store.get_fuel()
            if capture_directory is None:
                stdout = stderr = b""
                stdout_truncated = stderr_truncated = False
            else:
                stdout, stdout_truncated = _read_capped_output(stdout_path)
                stderr, stderr_truncated = _read_capped_output(stderr_path)
            return WasiRunResult(exit_code, stdout, stderr, stdout_truncated,
                                 stderr_truncated, remaining, elapsed_ms)
        except WasiRuntimeError:
            raise
        except (OSError, wasmtime.Trap, wasmtime.WasmtimeError,
                KeyError, TypeError, ValueError) as error:
            raise WasiRuntimeError(f"cannot execute WASI module: {error}") from error
        finally:
            if capture_directory is not None:
                capture_directory.cleanup()
