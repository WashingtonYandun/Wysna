"""Unit tests for async fn and await syntax compiler integration (Phase 1.3)."""
import os
import shutil
import tempfile
from pathlib import Path
import pytest

from compiler.CompilerService import CompilerService
from tests.engine_harness import run_engine
from main import ensure_selfhost_compiler_binary


ROOT = Path(__file__).resolve().parents[2]


def test_async_fn_and_await_basic():
    source = '''async fn add_async(a: int, b: int) -> int {
    return a + b
}

fn main() -> unit {
    let res = await add_async(40, 2)
    print("RES", res)
}

main()
'''
    CompilerService(project_root=ROOT).check(source, file="test_async_fn.wysna")
    result = run_engine("interpreter", source, "test_async_fn", "", Path(tempfile.gettempdir()))
    assert result.exit_code == 0, f"Error: {result.stderr}"
    assert result.stdout.strip() == "RES 42"


def test_await_immediate_scalar():
    source = '''fn main() -> unit {
    let x = await 123
    let s = await "hello"
    print("IMMEDIATE", x, s)
}

main()
'''
    CompilerService(project_root=ROOT).check(source, file="test_immediate.wysna")
    result = run_engine("interpreter", source, "test_immediate", "", Path(tempfile.gettempdir()))
    assert result.exit_code == 0, f"Error: {result.stderr}"
    assert result.stdout.strip() == "IMMEDIATE 123 hello"


def test_await_stdlib_async_task():
    source = '''import "std/async"

fn main() -> unit {
    let t = async.ready(99)
    let val = await t
    print("AWAITED_TASK", val)
}

main()
'''
    CompilerService(project_root=ROOT).check(source, file="test_task_await.wysna")
    result = run_engine("interpreter", source, "test_task_await", "", Path(tempfile.gettempdir()))
    assert result.exit_code == 0, f"Error: {result.stderr}"
    assert result.stdout.strip() == "AWAITED_TASK 99"


def test_async_syntax_across_native_engines():
    source = '''import "std/async"

async fn calc(x: int) -> int {
    return x * 10
}

fn main() -> unit {
    let t = async.ready(5)
    let a = await t
    let b = await calc(a)
    print(b)
}

main()
'''
    CompilerService(project_root=ROOT).check(source, file="test_native_async.wysna")
    selfhost = ensure_selfhost_compiler_binary(str(ROOT))
    with tempfile.TemporaryDirectory(prefix="wysna-async-syntax-native-") as tmpdir:
        for engine in ("interpreter", "ast", "hir"):
            res = run_engine(engine, source, "async_syntax_native", selfhost, Path(tmpdir) / engine)
            assert res.exit_code == 0, f"{engine} failed: {res.stderr}"
            assert res.stdout.strip() == "50", f"{engine} mismatch: {res.stdout}"
