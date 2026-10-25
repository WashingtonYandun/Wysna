#!/usr/bin/env python3
"""Build an offline native SDK; Python is a build dependency, never a packaged runtime."""
import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.Version import WYSNA_VERSION
from common.Platform import WINDOWS_NATIVE_STACK_BYTES, native_stack_link_args
from compiler.Bootstrap import bootstrap_fingerprint, ensure_selfhost_compiler_binary
from tools.package_components import download, extract, sha256

COMPONENTS = json.loads((ROOT / "packaging/native/components.json").read_text(encoding="utf-8"))
COMMANDS = ["check", "emit-c", "emit-mir", "build", "run"]


def host_platform():
    system = {"Darwin": "macos", "Linux": "linux", "Windows": "windows"}.get(platform.system())
    architecture = {"arm64": "aarch64", "aarch64": "aarch64", "AMD64": "x86_64", "x86_64": "x86_64"}.get(platform.machine())
    name = (architecture or "unknown") + "-" + (system or "unknown")
    if name not in COMPONENTS["zig"]:
        raise ValueError("No native SDK toolchain is pinned for " + name)
    return name


def assemble(output, cache, *, source_commit=None):
    output, cache = Path(output).resolve(), Path(cache).resolve()
    if output.exists():
        raise ValueError("Choose a new SDK output directory; existing installations are never replaced")
    host = host_platform()
    cache.mkdir(parents=True, exist_ok=True)
    component = COMPONENTS["zig"][host]
    archive = download(component, cache)
    print("Verifying native compiler bootstrap", file=sys.stderr, flush=True)
    compiler = Path(ensure_selfhost_compiler_binary(str(ROOT)))
    bootstrap = json.loads((compiler.parent / "bootstrap.json").read_text(encoding="utf-8"))
    if len(set(bootstrap["native_c_sha256"])) != 1:
        raise ValueError("Native SDK requires a verified compiler fixed point")
    driver_digest = sha256(ROOT / "packaging/native/driver.c")
    platform_digest = sha256(ROOT / "packaging/native/platform.h")
    output.parent.mkdir(parents=True, exist_ok=True)
    suffix = ".exe" if os.name == "nt" else ""
    with tempfile.TemporaryDirectory(prefix="wysna-native-package-", dir=output.parent) as temporary:
        directory = Path(temporary)
        stage = directory / "sdk"
        (stage / "bin").mkdir(parents=True)
        (stage / "libexec").mkdir()
        extract(archive, directory / "toolchain")
        roots = list((directory / "toolchain").iterdir())
        if len(roots) != 1 or not roots[0].is_dir():
            raise ValueError("Expected one pinned toolchain root")
        (stage / "toolchain").mkdir()
        shutil.move(str(roots[0]), stage / "toolchain/zig")
        zig = stage / ("toolchain/zig/zig" + suffix)
        if not zig.is_file() or not (stage / "toolchain/zig/LICENSE").is_file():
            raise ValueError("Toolchain is missing its compiler or license")
        (stage / "runtime").mkdir()
        for header in (ROOT / "runtime").glob("*.h"):
            shutil.copy2(header, stage / "runtime" / header.name)
        (stage / "stdlib").mkdir()
        for source in (ROOT / "stdlib").glob("*.wysna"):
            shutil.copy2(source, stage / "stdlib" / source.name)
        environment = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(directory / "zig-local"),
                           ZIG_GLOBAL_CACHE_DIR=str(cache / "zig-global"))
        base = [str(zig), "cc", "-target", component["target"], "-std=c99", "-O2", "-mcpu=baseline", "-I" + str(ROOT)]
        stack_flags = native_stack_link_args(base)
        base += stack_flags
        for source, target in [(ROOT / "packaging/native/driver.c", stage / ("bin/wysna" + suffix)),
                               (compiler.parent / "compiler2.c", stage / ("libexec/wysna-compiler" + suffix))]:
            defines = ['-DWYSNA_NATIVE_TARGET="' + component["target"] + '"',
                       '-DWYSNA_NATIVE_VERSION="' + WYSNA_VERSION + '"'] if source.name == "driver.c" else []
            print("Compiling SDK component: " + target.name, file=sys.stderr, flush=True)
            if stack_flags and source.name == "driver.c":
                defines.append('-DWYSNA_NATIVE_STACK_LINK_ARGUMENT="' + stack_flags[0] + '"')
            built = subprocess.run(base + defines + [str(source), "-lc", "-lm", "-o", str(target)],
                                   capture_output=True, text=True, timeout=300, env=environment)
            if built.returncode:
                raise RuntimeError("Native SDK compilation failed for " + source.name + ":\n" + built.stderr)
        if os.name == "nt":
            (stage / "open-terminal.cmd").write_text(
                '@echo off\nset "PATH=%~dp0bin;%PATH%"\ncd /d "%USERPROFILE%"\ncmd /k\n', encoding="utf-8")
        shutil.copy2(ROOT / "LICENSE", stage / "LICENSE")
        shutil.copy2(ROOT / "docs/NATIVE_SDK.md", stage / "README.md")
        for document in ("MIR_DESIGN.md", "ENGINE_PARITY_MATRIX.md"):
            shutil.copy2(ROOT / "docs" / document, stage / document)
        (stage / "THIRD_PARTY.txt").write_text(
            "This SDK includes Zig/LLVM and bundled libraries under their original licenses.\n"
            "The complete toolchain and license notices are retained in toolchain/zig.\n"
            "No CPython runtime or Wysna Seed implementation is included.\n", encoding="utf-8")
        if (bootstrap_fingerprint(ROOT)[0] != bootstrap["fingerprint"]
                or sha256(ROOT / "packaging/native/driver.c") != driver_digest
                or sha256(ROOT / "packaging/native/platform.h") != platform_digest):
            raise ValueError("SDK inputs changed during packaging; artifacts were not accepted")
        actual_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, check=True,
                                       capture_output=True, text=True).stdout.strip()
        dirty = bool(subprocess.run(["git", "status", "--porcelain"], cwd=ROOT, check=True,
                                    capture_output=True, text=True).stdout.strip())
        if source_commit and source_commit != actual_commit:
            raise ValueError("Requested source commit does not match the checkout")
        files = {path.relative_to(stage).as_posix(): sha256(path)
                 for path in sorted(stage.rglob("*")) if path.is_file()}
        manifest = {"format": 1, "version": WYSNA_VERSION, "platform": host,
                    "profile": "native-core-v1", "target": component["target"], "commands": COMMANDS,
                    "stack_reserve_bytes": WINDOWS_NATIVE_STACK_BYTES if os.name == "nt" else None,
                    "source_commit": actual_commit, "source_dirty": dirty,
                    "compiler_fingerprint": bootstrap["fingerprint"],
                    "compiler_source_sha256": bootstrap["provenance"]["inputs"]["selfhost/compiler.wysna"],
                    "bootstrap_seed_backend": bootstrap["seed_backend"],
                    "bootstrap_seed_c_sha256": bootstrap["seed_c_sha256"],
                    "native_c_sha256": bootstrap["native_c_sha256"],
                    "driver_source_sha256": driver_digest, "platform_source_sha256": platform_digest,
                    "components": {"zig": component}, "files": files}
        (stage / "native-sdk.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        stage.rename(output)
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, default=ROOT / ".wysna_cache/native-downloads")
    parser.add_argument("--source-commit")
    parser.add_argument("--archive", action="store_true")
    options = parser.parse_args()
    manifest = assemble(options.output, options.cache, source_commit=options.source_commit)
    archive = None
    if options.archive:
        output = options.output.resolve()
        archive = shutil.make_archive(str(output), "zip" if os.name == "nt" else "gztar",
                                      root_dir=output.parent, base_dir=output.name)
    print(json.dumps({"sdk": str(options.output.resolve()), "archive": archive,
                      "platform": manifest["platform"], "version": manifest["version"],
                      "profile": manifest["profile"], "fingerprint": manifest["compiler_fingerprint"]}, indent=2))


if __name__ == "__main__":
    main()
