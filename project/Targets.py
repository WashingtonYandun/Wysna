"""Target profile metadata shared by project validation and build reporting."""
from dataclasses import dataclass
from enum import Enum


class ArtifactKind(str, Enum):
    NATIVE_EXECUTABLE = "native_executable"
    WASM_MODULE = "wasm_module"


@dataclass(frozen=True)
class TargetSpec:
    profile: str
    triple: str | None
    artifact_kind: ArtifactKind
    suffix: str
    capabilities: tuple[str, ...]


TARGET_SPECS = {
    "native-cli": TargetSpec(
        "native-cli", None, ArtifactKind.NATIVE_EXECUTABLE, "",
        ("stdio", "filesystem", "environment", "processes", "network", "native-ffi"),
    ),
    "wasi-command": TargetSpec(
        "wasi-command", "wasm32-wasip1", ArtifactKind.WASM_MODULE, ".wasm",
        ("stdio", "arguments", "clock", "random", "manifest-declared-preopens"),
    ),
    "browser-library": TargetSpec(
        "browser-library", "wasm32-unknown-emscripten", ArtifactKind.WASM_MODULE, ".mjs",
        ("linear-memory", "generated-utf8-abi", "bigint-int64"),
    ),
}


def target_spec(profile):
    """Return the immutable target contract for a manifest profile."""
    try:
        return TARGET_SPECS[profile]
    except KeyError as error:
        supported = ", ".join(sorted(TARGET_SPECS))
        raise ValueError(f"unsupported target profile '{profile}'; expected one of: {supported}") from error
