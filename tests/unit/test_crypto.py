"""Unit test suite for std/crypto module contracts and NIST test vectors."""
import subprocess
import sys
from pathlib import Path

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
