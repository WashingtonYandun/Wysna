"""Bootstrap and cache the independent Wysna Compiler from a source checkout."""
import os
import subprocess
from compiler.CompilerService import CompilerService
from common.Platform import cache_lock, compiler_command, executable_name
from common.Paths import get_repo_root


# Include every compiler implementation and runtime header in cache invalidation.
SELFHOST_BOOTSTRAP_INPUTS = ("selfhost/compiler.wysna", "main.py") + tuple(sorted(
    os.path.relpath(os.path.join(directory, name), get_repo_root())
    for package in ("lexer", "parser", "node", "checker", "compiler", "codegen", "common", "error", "hir", "mir", "tokens", "runtime")
    for directory, _, names in os.walk(os.path.join(get_repo_root(), package))
    for name in names
    if name.endswith(".py") or (package == "runtime" and name.endswith(".h"))
))


def _bootstrap_source_mtime(repo_root: str) -> float:
    """Newest modification time across every input to the compiler bootstrap."""
    newest = 0.0
    for relative in SELFHOST_BOOTSTRAP_INPUTS:
        path = os.path.join(repo_root, relative)
        if os.path.exists(path):
            newest = max(newest, os.path.getmtime(path))
    return newest



def ensure_selfhost_compiler_binary(repo_root: str) -> str:
    """Serialize cache rebuilds across independent compiler/test processes."""
    compiler_src_path = os.path.join(repo_root, "selfhost", "compiler.wysna")
    if not os.path.isfile(compiler_src_path):
        from error.Error import TypeError
        raise TypeError("Wysna Compiler bootstrap requires a source checkout", file=compiler_src_path, code="W2013")
    cache_dir = os.path.join(repo_root, ".wysna_cache")
    os.makedirs(cache_dir, exist_ok=True)
    with cache_lock(os.path.join(cache_dir, 'bootstrap.lock')):
        return _ensure_selfhost_compiler_binary_locked(repo_root)



def _ensure_selfhost_compiler_binary_locked(repo_root: str) -> str:
    cache_dir = os.path.join(repo_root, ".wysna_cache")
    os.makedirs(cache_dir, exist_ok=True)
    stage2_bin = os.path.join(cache_dir, executable_name("stage2"))
    compiler_src_path = os.path.join(repo_root, "selfhost", "compiler.wysna")

    needs_rebuild = not os.path.exists(stage2_bin)
    if not needs_rebuild and _bootstrap_source_mtime(repo_root) > os.path.getmtime(stage2_bin):
        needs_rebuild = True

    if not os.path.isfile(compiler_src_path):
        from error.Error import TypeError
        raise TypeError("Wysna Compiler bootstrap requires a source checkout", file=compiler_src_path, code="W2013")
    if needs_rebuild:
        with open(compiler_src_path, "r", encoding="utf-8") as wf:
            compiler_src = wf.read()
        service = CompilerService()
        checked = service.check(compiler_src, compiler_src_path)
        c0_code = service.emit_c(checked, backend="ast").source

        c0_path = os.path.join(cache_dir, "compiler0.c")
        c1_path = os.path.join(cache_dir, "compiler1.c")
        s1_bin = os.path.join(cache_dir, executable_name("stage1"))

        with open(c0_path, "w", encoding="utf-8") as cf:
            cf.write(c0_code)

        subprocess.run(
            compiler_command() + ["-std=c99", "-O2", f"-I{repo_root}", c0_path, "-o", s1_bin],
            check=True,
            timeout=120,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            [s1_bin, compiler_src_path, c1_path],
            check=True,
            timeout=120,
            capture_output=True,
            text=True,
            cwd=repo_root,
        )
        subprocess.run(
            compiler_command() + ["-std=c99", "-O2", f"-I{repo_root}", c1_path, "-o", stage2_bin],
            check=True,
            timeout=120,
            capture_output=True,
            text=True,
        )

    return stage2_bin
