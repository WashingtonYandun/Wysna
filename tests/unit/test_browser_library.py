"""Contract checks for the browser-library target and generated scalar ABI."""

from pathlib import Path
import tempfile

from compiler.CompilerService import CompilerService
from compiler.BrowserLibraryToolchain import (
    EMSCRIPTEN_RUNTIME_METHODS,
    BrowserLibraryToolchain,
)
from error.Error import WysnaError
from project.Manifest import Target
from codegen.BrowserBindings import generate_browser_bindings
from tooling.TargetValidation import validate_checked_target
from common.WasmModule import inspect_wasm


ROOT = Path(__file__).resolve().parents[2]
TARGET = Target("web", "src/library.wysna", "browser-library", memory_limit_mb=32)


def _checked(source):
    temporary = tempfile.TemporaryDirectory(prefix="wysna-browser-library-")
    source_path = Path(temporary.name) / "library.wysna"
    source_path.write_text(source, encoding="utf-8")
    service = CompilerService(project_root=ROOT)
    checked = service.check_file(source_path)
    return temporary, service, checked


def _expect_target_error(source):
    temporary, _service, checked = _checked(source)
    try:
        try:
            validate_checked_target(checked, TARGET)
        except WysnaError as error:
            assert error.code == "W2013"
            return
        raise AssertionError("unsupported browser-library source was accepted")
    finally:
        temporary.cleanup()


def test_browser_library_emits_public_scalar_and_utf8_exports():
    source = '''pub fn add(left: int, right: int) -> int {
    return left + right
}

pub fn greet(name: str) -> str {
    return "Hello, " + name
}

pub fn ready() -> bool {
    return true
}
'''
    temporary, service, checked = _checked(source)
    try:
        validate_checked_target(checked, TARGET)
        emission = service.emit_c(checked, target_profile="browser-library")
        assert [item["name"] for item in emission.abi_exports] == ["add", "greet", "ready"]
        assert [item["params"] for item in emission.abi_exports] == [
            ["int", "int"], ["str"], [],
        ]
        assert [item["return"] for item in emission.abi_exports] == ["int", "str", "bool"]
        assert "int32_t wysna_web_init(void)" in emission.source
        assert "void wysna_web_destroy(void)" in emission.source
        assert "int main(int argc" not in emission.source
        assert "wysna_runtime_cleanup();" in emission.source
        bindings = generate_browser_bindings("library.runtime.mjs", emission.abi_exports)
        assert "BigInt values" in bindings
        assert "new TextEncoder()" in bindings
        assert "new TextDecoder(\"utf-8\", { fatal: true })" in bindings
        assert "linear-memory pointers" not in bindings
        assert "REENTRANT_CALL" in bindings
        assert "WASM_TRAP" in bindings
    finally:
        temporary.cleanup()


def test_browser_library_rejects_missing_exports_host_io_and_initializers():
    _expect_target_error("fn private_value() -> int { return 1 }\n")
    _expect_target_error('''pub fn output() -> unit {
    print("not available")
}
''')
    _expect_target_error('''let state = 1

pub fn value() -> int {
    return state
}
''')


def test_browser_library_rejects_generic_composite_and_fallible_exports():
    _expect_target_error("pub fn identity[T](value: T) -> T { return value }\n")
    _expect_target_error('''pub struct Point {
    x: int
}

pub fn origin() -> Point {
    return Point { x: 0 }
}
''')
    _expect_target_error('''pub fn fallible() -> int ! str {
    return Err("failure")
}
''')


def test_browser_library_requires_bounded_private_wasm32_memory():
    exports = (
        "wysna_web_init", "wysna_web_destroy", "wysna_web_alloc", "wysna_web_free",
        "wysna_web_call_0",
    )
    with tempfile.TemporaryDirectory(prefix="wysna-browser-wasm-") as directory:
        module_path = Path(directory) / "bounded.wasm"
        export_payload = bytearray([len(exports)])
        for index, name in enumerate(exports):
            encoded_name = name.encode("utf-8")
            export_payload.extend((len(encoded_name),))
            export_payload.extend(encoded_name)
            export_payload.extend((0, index))
        export_section = bytes((7, len(export_payload))) + bytes(export_payload)
        memory_section = b"\x05\x04\x01\x01\x01\x02"
        module_path.write_bytes(
            b"\x00asm\x01\x00\x00\x00" + export_section + memory_section
        )
        info = inspect_wasm(module_path)

    assert info.memories == ({
        "imported": False,
        "minimum_pages": 1,
        "maximum_pages": 2,
        "shared": False,
        "memory64": False,
    },)
    BrowserLibraryToolchain._validate_module(info, ["_" + name for name in exports], 2 * 65_536)

    emscripten_imports = info.__class__(
        ({"module": "wasi_snapshot_preview1", "name": "proc_exit", "kind": "function"},),
        info.exports,
        info.memories,
    )
    BrowserLibraryToolchain._validate_module(
        emscripten_imports, ["_" + name for name in exports], 2 * 65_536,
    )
    unsupported_imports = info.__class__(
        ({"module": "custom_host", "name": "invoke", "kind": "function"},),
        info.exports,
        info.memories,
    )
    try:
        BrowserLibraryToolchain._validate_module(
            unsupported_imports, ["_" + name for name in exports], 2 * 65_536,
        )
    except ValueError as error:
        assert "custom_host" in str(error)
    else:
        raise AssertionError("browser-library accepted a non-Emscripten host import")

    try:
        BrowserLibraryToolchain._validate_module(info, ["_" + name for name in exports], 65_536)
    except ValueError as error:
        assert "maximum exceeds" in str(error)
    else:
        raise AssertionError("browser-library accepted memory beyond its configured limit")

    imported_memory = info.__class__(
        info.imports,
        info.exports,
        ({"imported": True, "minimum_pages": 1, "maximum_pages": 2,
          "shared": False, "memory64": False},),
    )
    try:
        BrowserLibraryToolchain._validate_module(
            imported_memory, ["_" + name for name in exports], 2 * 65_536,
        )
    except ValueError as error:
        assert "private wasm32 memory" in str(error)
    else:
        raise AssertionError("browser-library accepted a host-provided WebAssembly memory")


def test_browser_toolchain_exports_the_memory_view_used_by_the_facade():
    assert EMSCRIPTEN_RUNTIME_METHODS == ("HEAPU8",)


def test_browser_library_contract():
    test_browser_library_emits_public_scalar_and_utf8_exports()
    test_browser_library_rejects_missing_exports_host_io_and_initializers()
    test_browser_library_rejects_generic_composite_and_fallible_exports()
    test_browser_library_requires_bounded_private_wasm32_memory()
    test_browser_toolchain_exports_the_memory_view_used_by_the_facade()


if __name__ == "__main__":
    test_browser_library_contract()
    print("[OK] browser-library target contract")
