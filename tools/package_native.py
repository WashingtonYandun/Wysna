#!/usr/bin/env python3
"""Build an offline native SDK; Python is a build dependency, never a packaged runtime."""
import argparse
import concurrent.futures
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / 'src'
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.Version import WYSNA_VERSION
from common.AgentContract import native_sdk_commands
from common.Platform import WINDOWS_NATIVE_STACK_BYTES, native_stack_link_args
from compiler.Bootstrap import bootstrap_fingerprint, ensure_selfhost_compiler_binary
from tools.package_components import download, extract, sha256
from tools.split_c_tu import split_c_translation_unit

COMPONENTS = json.loads((ROOT / "packaging/native/components.json").read_text(encoding="utf-8"))
COMMANDS = native_sdk_commands()
SDK_COMPONENT_BUILD_TIMEOUT_SECONDS = 600


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
    source_inputs = {path.relative_to(ROOT).as_posix(): sha256(path)
                     for directory, suffixes in (("packaging/native", ('.c', '.h')),
                                                  ("runtime", ('.h', '.def')),
                                                  ("stdlib", ('.wysna',)))
                     for path in sorted((ROOT / directory).iterdir())
                     if path.is_file() and path.suffix in suffixes}
    runtime_lib_source = ROOT / "runtime/wysna_runtime_lib.c"
    source_inputs["runtime/wysna_runtime_lib.c"] = sha256(runtime_lib_source)
    driver_digest = source_inputs['packaging/native/driver.c']
    platform_digest = source_inputs['packaging/native/platform.h']
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
        for header in sorted(path for path in (ROOT / "runtime").iterdir() if path.suffix in (".h", ".def")):
            shutil.copy2(header, stage / "runtime" / header.name)
        (stage / "stdlib").mkdir()
        for source in (ROOT / "stdlib").glob("*.wysna"):
            shutil.copy2(source, stage / "stdlib" / source.name)
        environment = dict(os.environ, ZIG_LOCAL_CACHE_DIR=str(directory / "zig-local"),
                           ZIG_GLOBAL_CACHE_DIR=str(cache / "zig-global"))
        (stage / "lib").mkdir()
        rt_base = [str(zig), "cc", "-target", component["target"], "-std=c99", "-mcpu=baseline",
                   "-ffunction-sections", "-fdata-sections", "-I" + str(ROOT)]
        if host.endswith("-linux"):
            rt_base += ["-pthread", "-DWYSNA_LINK_PTHREAD"]
        for profile_name, profile_flags in (("dev", ["-O0", "-g"]),
                                            ("release", ["-O2", "-fno-sanitize=undefined"]),
                                            ("size", ["-Os", "-fno-sanitize=undefined"])):
            obj_path = directory / f"wysna_runtime_{profile_name}.o"
            archive_path = stage / "lib" / f"libwysna_runtime_{profile_name}.a"
            print("Compiling SDK runtime archive: " + archive_path.name, file=sys.stderr, flush=True)
            built_rt = subprocess.run(
                rt_base + profile_flags + ["-c", str(runtime_lib_source), "-o", str(obj_path)],
                capture_output=True, text=True, timeout=SDK_COMPONENT_BUILD_TIMEOUT_SECONDS, env=environment)
            if built_rt.returncode:
                raise RuntimeError("Native SDK runtime archive compilation failed (" + profile_name + "):\n" + built_rt.stderr)
            archived_rt = subprocess.run(
                [str(zig), "ar", "rcs", str(archive_path), str(obj_path)],
                capture_output=True, text=True, timeout=SDK_COMPONENT_BUILD_TIMEOUT_SECONDS, env=environment)
            if archived_rt.returncode:
                raise RuntimeError("Native SDK runtime archive creation failed (" + profile_name + "):\n" + archived_rt.stderr)
        wasi_obj = directory / "wysna_runtime_wasi.o"
        wasi_archive = stage / "lib" / "libwysna_runtime_wasi.a"
        print("Compiling SDK runtime archive: " + wasi_archive.name, file=sys.stderr, flush=True)
        built_wasi = subprocess.run(
            [str(zig), "cc", "-target", "wasm32-wasi", "-std=c99", "-Os",
             "-fno-sanitize=undefined", "-ffunction-sections", "-fdata-sections",
             "-I" + str(ROOT), "-c", str(runtime_lib_source), "-o", str(wasi_obj)],
            capture_output=True, text=True, timeout=SDK_COMPONENT_BUILD_TIMEOUT_SECONDS, env=environment)
        if built_wasi.returncode:
            raise RuntimeError("Native SDK runtime archive compilation failed (wasi):\n" + built_wasi.stderr)
        archived_wasi = subprocess.run(
            [str(zig), "ar", "rcs", str(wasi_archive), str(wasi_obj)],
            capture_output=True, text=True, timeout=SDK_COMPONENT_BUILD_TIMEOUT_SECONDS, env=environment)
        if archived_wasi.returncode:
            raise RuntimeError("Native SDK runtime archive creation failed (wasi):\n" + archived_wasi.stderr)
        base = [str(zig), "cc", "-target", component["target"], "-std=c99", "-fno-sanitize=undefined", "-mcpu=baseline", "-I" + str(ROOT)]
        stack_flags = native_stack_link_args(base)
        base += stack_flags
        release_archive = stage / "lib" / "libwysna_runtime_release.a"
        driver_target = stage / ("bin/wysna" + suffix)
        driver_defines = [
            '-DWYSNA_NATIVE_TARGET="' + component["target"] + '"',
            '-DWYSNA_NATIVE_VERSION="' + WYSNA_VERSION + '"',
        ]
        if stack_flags:
            driver_defines.append('-DWYSNA_NATIVE_STACK_LINK_ARGUMENT="' + stack_flags[0] + '"')
        print("Compiling SDK component: " + driver_target.name, file=sys.stderr, flush=True)
        built_driver = subprocess.run(
            base + ["-O2"] + driver_defines + [str(ROOT / "packaging/native/driver.c"), "-lc", "-lm", "-o", str(driver_target)],
            capture_output=True, text=True, timeout=SDK_COMPONENT_BUILD_TIMEOUT_SECONDS, env=environment)
        if built_driver.returncode:
            raise RuntimeError("Native SDK compilation failed for driver.c:\n" + built_driver.stderr)

        compiler_target = stage / ("libexec/wysna-compiler" + suffix)
        compiler_units_dir = directory / "compiler-units"
        chunk_count = min(8, max(4, os.cpu_count() or 4))
        _, unit_sources = split_c_translation_unit(compiler.parent / "compiler2.c", compiler_units_dir, chunks=chunk_count)
        extra_compile = ["-DWYSNA_RUNTIME_LINKED", "-ffunction-sections", "-fdata-sections", "-I" + str(compiler_units_dir)]
        if host.endswith("-linux"):
            extra_compile += ["-pthread", "-DWYSNA_LINK_PTHREAD"]
        extra_link = [str(release_archive)]
        if host.endswith("-macos"):
            extra_link.append("-Wl,-dead_strip")
        elif host.endswith("-linux"):
            extra_link.append("-Wl,--gc-sections")
        print(f"Compiling SDK component: {compiler_target.name} ({len(unit_sources)} -O2 units)", file=sys.stderr, flush=True)

        def compile_unit(c_source: Path) -> Path:
            obj_file = c_source.with_suffix(".o")
            res = subprocess.run(
                base + ["-O2"] + extra_compile + ["-c", str(c_source), "-o", str(obj_file)],
                capture_output=True, text=True, timeout=SDK_COMPONENT_BUILD_TIMEOUT_SECONDS, env=environment)
            if res.returncode:
                raise RuntimeError(f"Native SDK compilation failed for {c_source.name}:\n" + res.stderr)
            return obj_file

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(unit_sources)) as pool:
            unit_objects = list(pool.map(compile_unit, unit_sources))
        linked_compiler = subprocess.run(
            base + ["-O2"] + ([ "-pthread" ] if host.endswith("-linux") else []) + [str(obj) for obj in unit_objects]
            + extra_link + ["-lc", "-lm", "-o", str(compiler_target)],
            capture_output=True, text=True, timeout=SDK_COMPONENT_BUILD_TIMEOUT_SECONDS, env=environment)
        if linked_compiler.returncode:
            raise RuntimeError("Native SDK linking failed for compiler:\n" + linked_compiler.stderr)
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
                or any(sha256(ROOT / name) != expected for name, expected in source_inputs.items())):
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
                    "component_build_timeout_seconds": SDK_COMPONENT_BUILD_TIMEOUT_SECONDS,
                    "source_commit": actual_commit, "source_dirty": dirty,
                    "compiler_fingerprint": bootstrap["fingerprint"],
                    "compiler_source_sha256": bootstrap["provenance"]["inputs"]["selfhost/compiler.wysna"],
                    "bootstrap_seed_backend": bootstrap["seed_backend"],
                    "bootstrap_seed_c_sha256": bootstrap["seed_c_sha256"],
                    "native_c_sha256": bootstrap["native_c_sha256"],
                    "driver_source_sha256": driver_digest, "platform_source_sha256": platform_digest,
                    "distribution_inputs_sha256": source_inputs,
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
