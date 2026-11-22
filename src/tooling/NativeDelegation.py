"""Minimal SIMP-R01 delegation: route `project`, `packages`, `mcp`, `lsp`, `schema` to the native Selfhost binary."""
import json
import os
from pathlib import Path
import subprocess
import sys

from common.Paths import get_repo_root
from common.Platform import executable_name
from tooling.ToolResult import diagnostic_record, tool_result, write_tool_result

DELEGATED_COMMANDS = frozenset({"project", "packages", "mcp", "lsp", "schema"})
_WASM_PROFILES = frozenset({"wasi-command", "browser-library"})


def strip_engine_flag(argv: list[str]) -> tuple[str | None, list[str]]:
    """Extract optional `--engine=seed|compiler` (or `--engine <val>`) before `--`."""
    sep = argv.index("--") if "--" in argv else len(argv)
    head = argv[:sep]
    tail = argv[sep:]
    engine = None
    cleaned: list[str] = []
    idx = 0
    while idx < len(head):
        arg = head[idx]
        if arg.startswith("--engine="):
            engine = arg.split("=", 1)[1]
            idx += 1
        elif arg == "--engine" and idx + 1 < len(head):
            engine = head[idx + 1]
            idx += 2
        else:
            cleaned.append(arg)
            idx += 1
    return engine, cleaned + tail


def _dev_sdk_driver() -> Path:
    explicit = os.environ.get("WYSNA_NATIVE_DRIVER")
    if explicit:
        return Path(explicit).expanduser().resolve()
    return Path(get_repo_root()) / ".wysna_cache/dev-sdk/bin" / executable_name("wysna")


def _project_uses_wasm_profile(subargs: list[str]) -> bool:
    """Keep WASI/browser project targets on Seed where WebAssembly lowering lives."""
    if not subargs or subargs[0] not in ("check", "build", "run"):
        return False
    if "--test-entry" in subargs:
        return True
    manifest_arg = None
    target_arg = None
    sep = subargs.index("--") if "--" in subargs else len(subargs)
    head = subargs[:sep]
    idx = 1
    while idx < len(head):
        arg = head[idx]
        if arg == "--manifest" and idx + 1 < len(head):
            manifest_arg = head[idx + 1]
            idx += 2
        elif arg.startswith("--manifest="):
            manifest_arg = arg.split("=", 1)[1]
            idx += 1
        elif arg == "--target" and idx + 1 < len(head):
            target_arg = head[idx + 1]
            idx += 2
        elif arg.startswith("--target="):
            target_arg = arg.split("=", 1)[1]
            idx += 1
        else:
            idx += 1
    if manifest_arg:
        manifest_path = Path(manifest_arg).expanduser().resolve()
        if manifest_path.is_dir():
            manifest_path = manifest_path / "wysna.toml"
    else:
        cwd = Path.cwd().resolve()
        manifest_path = next((directory / "wysna.toml" for directory in (cwd, *cwd.parents)
                              if (directory / "wysna.toml").is_file()), cwd / "wysna.toml")
    if not manifest_path.is_file():
        return False
    try:
        from project.Manifest import ProjectManifest
        manifest = ProjectManifest.load(manifest_path)
    except (OSError, ValueError):
        return False
    selected = target_arg or ("app" if "app" in manifest.targets else
                              next(iter(manifest.targets)) if len(manifest.targets) == 1 else None)
    target = manifest.targets.get(selected)
    return target is not None and target.profile in _WASM_PROFILES



def _packages_missing_driver_error(args: list[str]) -> int:
    json_v1 = "--output-format=json-v1" in args or (
        "--output-format" in args and args.index("--output-format") + 1 < len(args)
        and args[args.index("--output-format") + 1] == "json-v1"
    )
    msg = (
        "wysna packages runs exclusively on the native Selfhost toolchain; "
        "build the dev SDK first with 'python3 tools/build_selfhost_dev.py'"
    )
    if json_v1:
        diag = diagnostic_record("W5001", "toolchain", msg)
        write_tool_result(tool_result("packages", "toolchain_error", 3, diagnostics=[diag]), sys.stdout)
        return 3
    print(f"[W5001] {msg}", file=sys.stderr)
    return 3


def maybe_delegate_native(args: list[str]) -> int | None:
    """Return exit code when delegated to native Selfhost, or None to fall through to Seed."""
    if not args or args[0] not in DELEGATED_COMMANDS:
        return None
    command = args[0]
    engine_flag, cleaned = strip_engine_flag(args)
    env_engine = os.environ.get("WYSNA_ENGINE")
    env_delegate = os.environ.get("WYSNA_DELEGATE_NATIVE")

    if command != "packages":
        if engine_flag == "seed" or env_engine == "seed" or env_delegate == "0":
            return None
        if command == "project" and _project_uses_wasm_profile(cleaned[1:]):
            return None

    driver = _dev_sdk_driver()
    force_native = command == "packages" or engine_flag == "compiler" or env_engine == "compiler" or env_delegate == "1"
    if not driver.is_file():
        if not force_native:
            return None
        if env_delegate == "1" or engine_flag == "compiler" or env_engine == "compiler":
            from tools.build_selfhost_dev import assemble_dev_sdk
            assemble_dev_sdk(["driver", "zig-shim", "project", "mcp"])
        if not driver.is_file():
            if command == "packages":
                return _packages_missing_driver_error(cleaned)
            msg = f"Native dev SDK driver not found at {driver}; run 'python3 tools/build_selfhost_dev.py'"
            print(f"[W5001] {msg}", file=sys.stderr)
            return 3

    if os.name != "nt":
        sys.stdout.flush()
        sys.stderr.flush()
        os.execv(str(driver), [str(driver), *cleaned])
    completed = subprocess.run([str(driver), *cleaned], check=False)
    return completed.returncode
