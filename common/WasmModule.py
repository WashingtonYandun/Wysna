"""Strict inspection and capability validation for core WebAssembly modules."""
from dataclasses import dataclass
from pathlib import Path


_MAGIC = b"\x00asm\x01\x00\x00\x00"
_WASI_MODULE = "wasi_snapshot_preview1"
_WASI_IMPORTS = frozenset({
    "args_get", "args_sizes_get", "clock_res_get", "clock_time_get",
    "environ_get", "environ_sizes_get", "fd_advise", "fd_allocate",
    "fd_close", "fd_datasync", "fd_fdstat_get", "fd_fdstat_set_flags",
    "fd_fdstat_set_rights", "fd_filestat_get", "fd_filestat_set_size",
    "fd_filestat_set_times", "fd_pread", "fd_prestat_dir_name",
    "fd_prestat_get", "fd_pwrite", "fd_read", "fd_readdir", "fd_renumber",
    "fd_seek", "fd_sync", "fd_tell", "fd_write", "path_create_directory",
    "path_filestat_get", "path_filestat_set_times", "path_link", "path_open",
    "path_readlink", "path_remove_directory", "path_rename", "path_symlink",
    "path_unlink_file", "poll_oneoff", "proc_exit", "random_get", "sched_yield",
})


class WasmModuleError(ValueError):
    """A malformed module or a module outside the selected Wysna target contract."""


@dataclass(frozen=True)
class WasmModuleInfo:
    imports: tuple
    exports: tuple
    memories: tuple = ()


class _Reader:
    def __init__(self, data, start=0, end=None):
        self.data = memoryview(data)
        self.position = start
        self.end = len(self.data) if end is None else end

    def _require(self, count):
        if count < 0 or self.position + count > self.end:
            raise WasmModuleError("truncated WebAssembly section")

    def byte(self):
        self._require(1)
        value = self.data[self.position]
        self.position += 1
        return value

    def uleb(self):
        value = 0
        shift = 0
        for _ in range(10):
            byte = self.byte()
            value |= (byte & 0x7f) << shift
            if byte < 0x80:
                return value
            shift += 7
        raise WasmModuleError("invalid WebAssembly integer encoding")

    def string(self):
        length = self.uleb()
        self._require(length)
        raw = self.data[self.position:self.position + length].tobytes()
        self.position += length
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise WasmModuleError("invalid UTF-8 name in WebAssembly module") from error

    def skip_limits(self):
        flags = self.uleb()
        self.uleb()
        if flags & 1:
            self.uleb()

    def memory_limits(self):
        flags = self.uleb()
        if flags & ~7:
            raise WasmModuleError("invalid WebAssembly memory limits flags")
        minimum = self.uleb()
        maximum = self.uleb() if flags & 1 else None
        if maximum is not None and maximum < minimum:
            raise WasmModuleError("WebAssembly memory maximum is smaller than its minimum")
        if flags & 2 and maximum is None:
            raise WasmModuleError("shared WebAssembly memory must declare a maximum")
        return {
            "minimum_pages": minimum,
            "maximum_pages": maximum,
            "shared": bool(flags & 2),
            "memory64": bool(flags & 4),
        }

    def require_end(self):
        if self.position != self.end:
            raise WasmModuleError("invalid trailing bytes in WebAssembly section")


def inspect_wasm(path):
    """Read import, export, and memory sections needed for target policy checks."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as error:
        raise WasmModuleError(f"cannot read WebAssembly module '{path}': {error}") from error
    if not data.startswith(_MAGIC):
        raise WasmModuleError("output is not a WebAssembly 1.0 core module")
    reader = _Reader(data, len(_MAGIC))
    imports, exports, memories = [], [], []
    seen_sections = set()
    while reader.position < reader.end:
        section_id = reader.byte()
        section_size = reader.uleb()
        section_end = reader.position + section_size
        if section_end > reader.end:
            raise WasmModuleError("truncated WebAssembly section payload")
        if section_id in (2, 5, 7):
            if section_id in seen_sections:
                raise WasmModuleError("duplicate WebAssembly import, memory, or export section")
            seen_sections.add(section_id)
            section = _Reader(data, reader.position, section_end)
            count = section.uleb()
            if count > 1_000_000:
                raise WasmModuleError("WebAssembly section exceeds the supported item limit")
            for _ in range(count):
                if section_id == 2:
                    module_name = section.string()
                    import_name = section.string()
                    kind = section.byte()
                    if kind == 0:
                        section.uleb()
                        kind_name = "function"
                    elif kind == 1:
                        section.byte()
                        section.skip_limits()
                        kind_name = "table"
                    elif kind == 2:
                        limits = section.memory_limits()
                        memories.append({
                            "imported": True,
                            "module": module_name,
                            "name": import_name,
                            **limits,
                        })
                        kind_name = "memory"
                    elif kind == 3:
                        section.byte()
                        section.byte()
                        kind_name = "global"
                    elif kind == 4:
                        section.byte()
                        section.uleb()
                        kind_name = "tag"
                    else:
                        raise WasmModuleError(f"unknown WebAssembly import kind {kind}")
                    imports.append({"module": module_name, "name": import_name, "kind": kind_name})
                elif section_id == 5:
                    memories.append({"imported": False, **section.memory_limits()})
                else:
                    exports.append(section.string())
                    section.byte()
                    section.uleb()
            section.require_end()
            seen_sections.add(section_id)
        reader.position = section_end
    if len(set(exports)) != len(exports):
        raise WasmModuleError("duplicate WebAssembly export names")
    return WasmModuleInfo(tuple(imports), tuple(exports), tuple(memories))


def validate_wasi_command(info):
    """Require one command entry point and the bounded WASI Preview 1 host ABI."""
    if "_start" not in info.exports:
        raise WasmModuleError("WASI command artifact does not export '_start'")
    for item in info.imports:
        if item["module"] != _WASI_MODULE:
            raise WasmModuleError(
                f"WASI command artifact imports unsupported host module '{item['module']}'"
            )
        if item["name"] not in _WASI_IMPORTS:
            raise WasmModuleError(
                f"WASI command artifact imports unsupported capability '{item['name']}'"
            )
