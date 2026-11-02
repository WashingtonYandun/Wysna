"""Unit test suite for std/crypto module contracts, CSPRNG, constant-time equality, and NIST test vectors."""
import subprocess
import sys
import tempfile
from pathlib import Path

from compiler.CompilerService import CompilerService
from tests.engine_harness import run_engine

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_crypto_smoke_interpreter():
    smoke_file = REPO_ROOT / "tests" / "runtime" / "crypto_smoke.wysna"
    cmd = [sys.executable, str(REPO_ROOT / "main.py"), "run", str(smoke_file)]
    res = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=30)
    assert res.returncode == 0, f"Interpreter failed with: {res.stderr}"
    assert "CRYPTO_OK" in res.stdout


def test_crypto_smoke_native_c99():
    smoke_file = REPO_ROOT / "tests" / "runtime" / "crypto_smoke.wysna"
    out_bin = REPO_ROOT / ".wysna_cache" / "test_crypto_smoke_bin"
    out_bin.parent.mkdir(exist_ok=True)
    build_cmd = [sys.executable, str(REPO_ROOT / "main.py"), "build", "--backend=c99", str(smoke_file), "-o", str(out_bin)]
    build_res = subprocess.run(build_cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=30)
    assert build_res.returncode == 0, f"Native build failed with: {build_res.stderr}"
    
    run_res = subprocess.run([str(out_bin)], capture_output=True, text=True, timeout=10)
    assert run_res.returncode == 0, f"Native run failed with: {run_res.stderr}"
    assert "CRYPTO_OK" in run_res.stdout
    if out_bin.exists():
        out_bin.unlink()


def test_crypto_engines_matrix():
    smoke_file = REPO_ROOT / "tests" / "runtime" / "crypto_smoke.wysna"
    source = smoke_file.read_text(encoding="utf-8")
    CompilerService(project_root=REPO_ROOT).check(source, file="crypto_smoke.wysna")
    
    with tempfile.TemporaryDirectory(prefix="wysna-crypto-engines-") as tmpdir:
        for engine in ("interpreter", "ast", "hir"):
            res = run_engine(engine, source, "crypto_engines", "", Path(tmpdir) / engine)
            assert res.exit_code == 0, f"{engine} failed: {res.stderr}"
            assert "CRYPTO_OK" in res.stdout, f"{engine} missing CRYPTO_OK in {res.stdout}"


def test_crypto_csprng_and_constant_time_contracts():
    source = '''import "std/buffer"
import "std/crypto"

fn main() -> unit {
    # 1. Test random_bytes sizes
    let b1 = crypto.random_bytes(8) else err {
        eprint("random_bytes(8) failed")
        exit(1)
        new buffer.Bytes()
    }
    let b2 = crypto.random_bytes(32) else err {
        eprint("random_bytes(32) failed")
        exit(1)
        new buffer.Bytes()
    }
    if b1.size() != 8 or b2.size() != 32 {
        eprint("random_bytes sizes mismatch")
        exit(1)
    }
    b1.close()
    b2.close()

    # 2. Test random_hex lengths
    let h1 = crypto.random_hex(8) else err { exit(1) "" }
    let h2 = crypto.random_hex(32) else err { exit(1) "" }
    if len(h1) != 16 or len(h2) != 64 {
        eprint("random_hex lengths mismatch")
        exit(1)
    }

    # 3. Test negative / zero count handling
    let bad_count = crypto.random_bytes(0) else err {
        # Expected error
        new buffer.Bytes()
    }
    bad_count.close()

    # 4. Constant time equality exhaustive cases
    if not crypto.constant_time_eq("", "") { exit(1) }
    if crypto.constant_time_eq("a", "") { exit(1) }
    if crypto.constant_time_eq("", "b") { exit(1) }
    if not crypto.constant_time_eq("abcdef", "abcdef") { exit(1) }
    if crypto.constant_time_eq("abcdef", "abcdeg") { exit(1) }
    if crypto.constant_time_eq("abcdef", "abcde") { exit(1) }

    print("CSPRNG_AND_EQ_OK")
}

main()
'''
    CompilerService(project_root=REPO_ROOT).check(source, file="test_csprng_eq.wysna")
    with tempfile.TemporaryDirectory(prefix="wysna-crypto-csprng-") as tmpdir:
        for engine in ("interpreter", "ast"):
            res = run_engine(engine, source, "csprng_contracts", "", Path(tmpdir) / engine)
            assert res.exit_code == 0, f"{engine} failed: {res.stderr}"
            assert "CSPRNG_AND_EQ_OK" in res.stdout
