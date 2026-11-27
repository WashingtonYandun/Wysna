"""Contract checks for precompiled WASI runtime and Native SDK --target=web wrapper emitter."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_package_native_precompiles_wasi_runtime():
    src = (ROOT / "tools" / "package_native.py").read_text(encoding="utf-8")
    assert "libwysna_runtime_wasi.a" in src
    assert "wasm32-wasi" in src


def test_native_driver_supports_web_wasm_bundle_and_precompiled_wasi():
    driver = (ROOT / "packaging" / "native" / "driver.c").read_text(encoding="utf-8")
    for token in (
        "lib/libwysna_runtime_wasi.a",
        "sdk_emit_web_wrapper",
        '"web-wasm"',
        'javascript_module',
        'typescript_declarations',
        "export async function run(",
        "export interface WysnaRunResult",
    ):
        assert token in driver, token
