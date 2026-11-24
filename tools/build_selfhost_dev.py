#!/usr/bin/env python3
"""Per-unit incremental dev builder for Selfhost tooling (<5 s rebuilds without touching compiler semantics).

Each unit emits and compiles only its own minimal source slice using a cached stage2
emitter, caching emitted C99 and compiled binaries by per-unit input SHA-256.
Assembles `.wysna_cache/dev-sdk/` for minimal SIMP-R01 delegation (`project`, `packages`, `mcp`, `lsp`, `schema`).
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from common.Platform import cache_lock, compiler_command, executable_name, native_stack_link_args
from common.Version import WYSNA_VERSION
from compiler.Bootstrap import ensure_selfhost_compiler_binary
from tools.bundle_selfhost import MODULE_ORDER
from tools.generate_agent_contract import generate
generate(check=True)

DEV_UNITS_DIR = ROOT / ".wysna_cache/dev-units"
DEV_SDK_DIR = ROOT / ".wysna_cache/dev-sdk"

_COMMON_HEAD = [
    "selfhost/src/01_ast.wysna",
    "selfhost/src/01_diagnostics.wysna",
    "selfhost/src/01_paths.wysna",
    "selfhost/src/01_tool_common.wysna",
    "selfhost/src/01_agent_contract.wysna",
]

UNIT_SOURCES = {
    "mcp": [
        *_COMMON_HEAD,
        "selfhost/src/09_mcp.wysna",
        "selfhost/dev_units/stubs_fix.wysna",
        "selfhost/dev_units/mcp_main.wysna",
    ],
    "project": [
        *_COMMON_HEAD,
        "selfhost/src/09_project.wysna",
        "selfhost/src/09_project_lock.wysna",
        "selfhost/src/09_project_tests.wysna",
        "selfhost/src/09_packages.wysna",
        "selfhost/dev_units/stubs_fix.wysna",
        "selfhost/dev_units/project_main.wysna",
    ],
    "compiler-core": [
        *[f"selfhost/src/{name}" for name in MODULE_ORDER if not name.startswith("09_")],
        "selfhost/dev_units/stubs_fix.wysna",
        "selfhost/dev_units/compiler_core_main.wysna",
    ],
    "compiler": [
        *[f"selfhost/src/{name}" for name in MODULE_ORDER],
    ],
}

_ZIG_SHIM_C = r'''
#define _POSIX_C_SOURCE 200809L
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

int main(int argc, char** argv) {
    char** forwarded = (char**)calloc((size_t)argc + 2, sizeof(char*));
    if (!forwarded) return 1;
    const char* current_path = getenv("PATH");
    if (!current_path || current_path[0] == '\0') {
        setenv("PATH", "/usr/bin:/bin", 1);
    }
    int out = 0;
    forwarded[out++] = (char*)HOST_CC_PATH;
    for (int i = 1; i < argc; i++) {
        if (i == 1 && strcmp(argv[i], "cc") == 0) continue;
        if (strcmp(argv[i], "-target") == 0 && i + 1 < argc) { i++; continue; }
        if (strcmp(argv[i], "-mcpu=baseline") == 0) continue;
        forwarded[out++] = argv[i];
    }
    forwarded[out] = NULL;
    execv(HOST_CC_PATH, forwarded);
    perror("execv host cc");
    return 127;
}
'''


def _hash_paths(paths: list[Path], extra: list[str] | None = None) -> str:
    digest = hashlib.sha256()
    for token in extra or []:
        digest.update(token.encode("utf-8"))
        digest.update(b"\0")
    for path in sorted(paths, key=lambda p: p.as_posix()):
        digest.update(path.relative_to(ROOT).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _runtime_headers() -> list[Path]:
    return sorted(p for p in (ROOT / "runtime").iterdir() if p.is_file() and p.suffix in (".h", ".def"))


def _copy_atomic(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=dst.parent, prefix=".dev-unit-", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        shutil.copy2(src, tmp_path)
        tmp_path.replace(dst)
    finally:
        tmp_path.unlink(missing_ok=True)


def _find_or_build_emitter(rebuild: bool = False) -> tuple[Path, dict]:
    """Return a cached full stage2 binary without forcing a 3-stage bootstrap on tooling-only edits."""
    t0 = time.perf_counter()
    pinned = DEV_UNITS_DIR / "emitter" / executable_name("stage2")
    if not rebuild and pinned.is_file():
        return pinned, {"unit": "emitter", "cache_hit": True, "total_s": round(time.perf_counter() - t0, 3)}
    if not rebuild:
        bootstrap_dir = ROOT / ".wysna_cache/bootstrap"
        if bootstrap_dir.is_dir():
            candidates = sorted(
                bootstrap_dir.glob(f"*/{executable_name('stage2')}"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
            if candidates:
                _copy_atomic(candidates[0], pinned)
                return pinned, {"unit": "emitter", "cache_hit": True, "total_s": round(time.perf_counter() - t0, 3)}
    built = Path(ensure_selfhost_compiler_binary(str(ROOT)))
    _copy_atomic(built, pinned)
    return pinned, {"unit": "emitter", "cache_hit": False, "total_s": round(time.perf_counter() - t0, 3)}


def build_driver_unit() -> tuple[Path, dict]:
    t0 = time.perf_counter()
    cc_cmd = compiler_command()
    inputs = sorted((ROOT / "packaging/native").glob("*.[ch]")) + _runtime_headers()
    key = _hash_paths(inputs, extra=["driver", WYSNA_VERSION, *cc_cmd])
    out_bin = DEV_UNITS_DIR / "driver" / key / executable_name("wysna")
    if out_bin.is_file():
        return out_bin, {"unit": "driver", "cache_hit": True, "cc_s": 0.0, "total_s": round(time.perf_counter() - t0, 3)}
    out_bin.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        *cc_cmd, "-std=c99", "-O0", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
        f'-DWYSNA_NATIVE_VERSION="{WYSNA_VERSION}"',
        '-DWYSNA_NATIVE_TARGET="native-host"',
        f"-I{ROOT}",
        f"-I{ROOT / 'packaging/native'}",
        str(ROOT / "packaging/native/driver.c"),
    ]
    if os.name == "nt":
        stack_flags = native_stack_link_args(cc_cmd)
        cmd += ["-D_CRT_SECURE_NO_WARNINGS", f'-DWYSNA_NATIVE_STACK_LINK_ARGUMENT="{stack_flags[0]}"', *stack_flags]
    else:
        cmd += ["-lm"]
    tmp_bin = out_bin.parent / (out_bin.name + ".tmp")
    cmd += ["-o", str(tmp_bin)]
    c0 = time.perf_counter()
    res = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    c1 = time.perf_counter()
    if res.returncode != 0:
        tmp_bin.unlink(missing_ok=True)
        raise RuntimeError(f"Failed compiling dev driver:\n{res.stderr}")
    tmp_bin.replace(out_bin)
    return out_bin, {
        "unit": "driver",
        "cache_hit": False,
        "cc_s": round(c1 - c0, 3),
        "total_s": round(time.perf_counter() - t0, 3),
    }


def build_zig_shim() -> tuple[Path | None, dict]:
    t0 = time.perf_counter()
    if os.name == "nt":
        found = shutil.which("zig")
        return (Path(found) if found else None), {"unit": "zig-shim", "cache_hit": True, "total_s": 0.0}
    cc_cmd = compiler_command()
    host_cc = shutil.which(cc_cmd[0]) or cc_cmd[0]
    key = hashlib.sha256((host_cc + "\0" + _ZIG_SHIM_C).encode("utf-8")).hexdigest()[:24]
    out_bin = DEV_UNITS_DIR / "zig-shim" / key / "zig"
    if out_bin.is_file():
        return out_bin, {"unit": "zig-shim", "cache_hit": True, "total_s": round(time.perf_counter() - t0, 3)}
    out_bin.parent.mkdir(parents=True, exist_ok=True)
    src_file = out_bin.parent / "zig_shim.c"
    src_file.write_text(_ZIG_SHIM_C, encoding="utf-8")
    tmp_bin = out_bin.parent / "zig.tmp"
    res = subprocess.run(
        [host_cc, "-std=c99", f'-DHOST_CC_PATH="{host_cc}"', str(src_file), "-o", str(tmp_bin)],
        capture_output=True, text=True, timeout=30,
    )
    if res.returncode != 0:
        tmp_bin.unlink(missing_ok=True)
        raise RuntimeError(f"Failed compiling dev zig shim:\n{res.stderr}")
    tmp_bin.replace(out_bin)
    return out_bin, {"unit": "zig-shim", "cache_hit": False, "total_s": round(time.perf_counter() - t0, 3)}


def build_wysna_unit(unit: str, emitter: Path, opt: str = "-O0") -> tuple[Path, dict]:
    t0 = time.perf_counter()
    source_paths = [ROOT / rel for rel in UNIT_SOURCES[unit]]
    cc_cmd = compiler_command()
    emitter_sha = hashlib.sha256(emitter.read_bytes()).hexdigest()[:16]
    key = _hash_paths(source_paths + _runtime_headers(), extra=[unit, opt, emitter_sha, *cc_cmd])
    unit_dir = DEV_UNITS_DIR / unit / key
    out_bin = unit_dir / executable_name(f"wysna-{unit}")
    if out_bin.is_file():
        return out_bin, {
            "unit": unit,
            "cache_hit": True,
            "lines": sum(len(p.read_text(encoding="utf-8").splitlines()) for p in source_paths),
            "emit_s": 0.0,
            "cc_s": 0.0,
            "total_s": round(time.perf_counter() - t0, 3),
        }
    unit_dir.mkdir(parents=True, exist_ok=True)
    combined = "".join(p.read_text(encoding="utf-8") for p in source_paths)
    lines = len(combined.splitlines())
    unit_wysna = unit_dir / f"{unit}.wysna"
    unit_c = unit_dir / f"{unit}.c"
    unit_wysna.write_text(combined, encoding="utf-8")

    e0 = time.perf_counter()
    emitted = subprocess.run(
        [str(emitter), "--emit-c-mir", str(unit_wysna), str(unit_c)],
        capture_output=True, text=True, timeout=120, cwd=str(ROOT),
    )
    e1 = time.perf_counter()
    if emitted.returncode != 0:
        raise RuntimeError(f"Failed emitting unit '{unit}':\n{emitted.stderr}\n{emitted.stdout}")

    c0 = time.perf_counter()
    cmd = [*cc_cmd, "-std=c99", opt, f"-I{ROOT}", str(unit_c)]
    if os.name == "nt":
        cmd += native_stack_link_args(cc_cmd)
    else:
        cmd += ["-lm"]
    tmp_bin = unit_dir / (out_bin.name + ".tmp")
    cmd += ["-o", str(tmp_bin)]
    built = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    c1 = time.perf_counter()
    if built.returncode != 0:
        tmp_bin.unlink(missing_ok=True)
        raise RuntimeError(f"Failed compiling C99 for unit '{unit}':\n{built.stderr}")
    tmp_bin.replace(out_bin)
    return out_bin, {
        "unit": unit,
        "cache_hit": False,
        "lines": lines,
        "c_kb": unit_c.stat().st_size // 1024,
        "emit_s": round(e1 - e0, 3),
        "cc_s": round(c1 - c0, 3),
        "total_s": round(time.perf_counter() - t0, 3),
    }


def _link_or_copy_dir(src: Path, dst: Path) -> None:
    if dst.is_symlink():
        if dst.resolve() == src.resolve():
            return
        dst.unlink()
    elif dst.exists():
        shutil.rmtree(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        dst.symlink_to(src)
    else:
        shutil.copytree(src, dst)


def assemble_dev_sdk(units: list[str], rebuild_emitter: bool = False, opt: str = "-O0") -> dict:
    overall_start = time.perf_counter()
    DEV_SDK_DIR.parent.mkdir(parents=True, exist_ok=True)
    with cache_lock(str(DEV_SDK_DIR.parent / "dev-sdk.lock")):
        emitter, emitter_meta = _find_or_build_emitter(rebuild=rebuild_emitter)
        results = [emitter_meta]

        bin_dir = DEV_SDK_DIR / "bin"
        libexec_dir = DEV_SDK_DIR / "libexec"
        toolchain_dir = DEV_SDK_DIR / "toolchain/zig"
        bin_dir.mkdir(parents=True, exist_ok=True)
        libexec_dir.mkdir(parents=True, exist_ok=True)
        toolchain_dir.mkdir(parents=True, exist_ok=True)

        _link_or_copy_dir(ROOT / "stdlib", DEV_SDK_DIR / "stdlib")
        _link_or_copy_dir(ROOT / "runtime", DEV_SDK_DIR / "runtime")

        compiler_dst = libexec_dir / executable_name("wysna-compiler")
        if rebuild_emitter or not compiler_dst.is_file():
            _copy_atomic(emitter, compiler_dst)

        if "driver" in units or not (bin_dir / executable_name("wysna")).is_file():
            driver_bin, driver_meta = build_driver_unit()
            _copy_atomic(driver_bin, bin_dir / executable_name("wysna"))
            results.append(driver_meta)

        if "zig-shim" in units or not (toolchain_dir / executable_name("zig")).is_file():
            shim_bin, shim_meta = build_zig_shim()
            if shim_bin is not None:
                _copy_atomic(shim_bin, toolchain_dir / executable_name("zig"))
            results.append(shim_meta)

        for unit in ("project", "mcp", "compiler-core", "compiler"):
            if unit in units:
                unit_bin, meta = build_wysna_unit(unit, emitter, opt=opt)
                _copy_atomic(unit_bin, libexec_dir / executable_name(f"wysna-{unit}"))
                results.append(meta)

        return {
            "sdk_root": str(DEV_SDK_DIR),
            "driver": str(bin_dir / executable_name("wysna")),
            "units": results,
            "total_s": round(time.perf_counter() - overall_start, 3),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build incremental per-unit Selfhost dev SDK.")
    parser.add_argument(
        "--unit",
        action="append",
        choices=["driver", "zig-shim", "project", "mcp", "compiler-core", "compiler", "all"],
        help="Unit(s) to build (default: driver, zig-shim, project, mcp).",
    )
    parser.add_argument("--rebuild-emitter", action="store_true", help="Refresh cached stage2 emitter via Bootstrap.")
    parser.add_argument("--opt", default="-O0", choices=["-O0", "-O1", "-O2"], help="C99 optimization level (default: -O0).")
    parser.add_argument("--json", action="store_true", help="Print build metrics as JSON.")
    args = parser.parse_args(argv)

    selected = args.unit or ["driver", "zig-shim", "project", "mcp"]
    if "all" in selected:
        selected = ["driver", "zig-shim", "project", "mcp", "compiler-core"]
    report = assemble_dev_sdk(selected, rebuild_emitter=args.rebuild_emitter, opt=args.opt)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        for item in report["units"]:
            status = "CACHE" if item.get("cache_hit") else "BUILT"
            extra = f" (emit={item['emit_s']}s cc={item['cc_s']}s)" if "emit_s" in item and not item["cache_hit"] else ""
            print(f"[{status}] {item['unit']}: {item['total_s']}s{extra}")
        print(f"[OK] Dev SDK ready at {report['driver']} ({report['total_s']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
