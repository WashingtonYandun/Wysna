"""Target profile metadata shared by project validation and build reporting."""
from dataclasses import dataclass
from enum import Enum


class ArtifactKind(str, Enum):
    NATIVE_EXECUTABLE = "native_executable"
    WASM_MODULE = "wasm_module"


@dataclass(frozen=True)
class TargetRequirement:
    name: str
    version: str | None = None
    provided_by: str | None = None


@dataclass(frozen=True)
class TargetSpec:
    profile: str
    triple: str | None
    artifact_kind: ArtifactKind
    suffix: str
    capabilities: tuple[str, ...]
    engine: str
    backend: str
    build_requirements: tuple[TargetRequirement, ...]
    run_requirements: tuple[TargetRequirement, ...]


TARGET_SPECS = {
    "native-cli": TargetSpec(
        "native-cli", None, ArtifactKind.NATIVE_EXECUTABLE, "",
        ("stdio", "filesystem", "environment", "processes", "network", "native-ffi"),
        "wysna-seed", "c99",
        (TargetRequirement("c99-compiler", provided_by="Seed distribution bundles Zig 0.15.2"),),
        (TargetRequirement("host-operating-system"),),
    ),
    "wasi-command": TargetSpec(
        "wasi-command", "wasm32-wasip1", ArtifactKind.WASM_MODULE, ".wasm",
        ("stdio", "arguments", "clock", "random", "manifest-declared-preopens"),
        "wysna-seed", "c99", (TargetRequirement("wasi-sdk", "34"),),
        (TargetRequirement("wasmtime-python", "50.0.1"),),
    ),
    "browser-library": TargetSpec(
        "browser-library", "wasm32-unknown-emscripten", ArtifactKind.WASM_MODULE, ".mjs",
        ("linear-memory", "generated-utf8-abi", "bigint-int64"),
        "wysna-seed", "c99", (TargetRequirement("emscripten", "5.0.1"),),
        (TargetRequirement("browser-or-nodejs"),),
    ),
}


def target_spec(profile):
    """Return the immutable target contract for a manifest profile."""
    try:
        return TARGET_SPECS[profile]
    except KeyError as error:
        supported = ", ".join(sorted(TARGET_SPECS))
        raise ValueError(f"unsupported target profile '{profile}'; expected one of: {supported}") from error
