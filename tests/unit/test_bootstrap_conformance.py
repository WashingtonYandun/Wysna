import glob
import hashlib
import os
import subprocess
import sys
import tempfile
from parser.Parser import Parser
from checker.TypeChecker import TypeChecker
from codegen.CCodeGen import CCodeGen
from tests.engine_harness import EngineResult, compare, diagnostic, unsupported_reason


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        h.update(f.read())
    return h.hexdigest()


def run_bootstrap_test() -> bool:
    repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    compiler_src_path = os.path.join(repo_root, "selfhost", "compiler.wysna")

    print("==================================================")
    print("Wysna Self-Hosting Bootstrap Suite (SPEC 11)")
    print("==================================================")

    with open(compiler_src_path, "r", encoding="utf-8") as f:
        src = f.read()

    with tempfile.TemporaryDirectory() as tmpdir:
        from compiler.Bootstrap import bootstrap_compiler
        artifacts = bootstrap_compiler(repo_root, tmpdir)
        stage2_bin = str(artifacts.compiler)
        for index, digest in enumerate(artifacts.hashes):
            print(f"  SHA256(native compiler{index}.c) = {digest}")
        assert len(set(artifacts.hashes)) == 1
        print("  [PASS] Seed AST bootstrap followed by three identical native MIR emissions")

        # 5. Stage 2 Native Binary Compiles & Runs Full Runtime Suite
        wysna_files = sorted(glob.glob(os.path.join(repo_root, "tests", "runtime", "*.wysna")))
        selfhost_passed = 0
        selfhost_total = 0
        for wysna_file in wysna_files:
            exp_file = wysna_file[:-6] + ".expected"
            if not os.path.exists(exp_file):
                continue
            reason = unsupported_reason(os.path.relpath(wysna_file, repo_root), 'selfhost')
            if reason:
                print(f'  [UNSUPPORTED SELFHOST] {os.path.relpath(wysna_file, repo_root)}: {reason}')
                continue
            selfhost_total += 1
            with open(exp_file, "r", encoding="utf-8") as ef:
                expected = ef.read()
            base = os.path.basename(wysna_file)[:-6]
            out_c = os.path.join(tmpdir, f"selfhost_{base}.c")
            out_bin = os.path.join(tmpdir, f"selfhost_{base}")

            r_selfhost = subprocess.run([stage2_bin, wysna_file, out_c], timeout=120, capture_output=True, text=True, cwd=repo_root)
            if r_selfhost.returncode != 0:
                print(f"  [FAIL SELFHOST] {base}.wysna compile failed: {r_selfhost.stderr}")
                return False
            r_cc = subprocess.run([os.environ.get("CC", "cc"), "-std=c99", f"-I{repo_root}", out_c, "-o", out_bin], timeout=120, capture_output=True, text=True)
            if r_cc.returncode != 0:
                print(f"  [FAIL SELFHOST CC] {base}.c failed: {r_cc.stderr}")
                return False
            r_run = subprocess.run([out_bin], timeout=30, capture_output=True, text=True, cwd=repo_root)
            result = EngineResult(
                'selfhost', stdout=r_run.stdout, stderr=r_run.stderr, exit_code=r_run.returncode,
                runtime_ok=r_run.returncode == 0, diag_code=diagnostic(r_run.stderr),
            )
            ok, detail = compare([result], expected)
            if not ok:
                print(f"  [FAIL SELFHOST RUN] {base}.wysna mismatch: {detail}")
                return False
            selfhost_passed += 1

        temp_file = os.path.join(repo_root, "tests", "runtime", "temp_file.txt")
        if os.path.exists(temp_file):
            os.remove(temp_file)

        print(f"  [PASS] Stage 2 stage2 compiled & verified {selfhost_passed}/{selfhost_total} runtime conformance programs")
        print("==================================================")
        return True


def test_bootstrap():
    assert run_bootstrap_test()


if __name__ == "__main__":
    ok = run_bootstrap_test()
    sys.exit(0 if ok else 1)
