"""Unit tests for std/async cooperative runtime and functional combinators (Phase 1.2)."""
import os
import shutil
import tempfile
from pathlib import Path
import pytest

from compiler.CompilerService import CompilerService
from compiler.NativeToolchain import NativeToolchain
from tests.engine_harness import run_engine
from main import ensure_selfhost_compiler_binary


ROOT = Path(__file__).resolve().parents[2]


def test_async_task_ready_and_wait():
    source = '''import "std/async"

fn main() -> unit {
    let t1 = async.ready(42)
    let val = async.wait(t1) else err {
        print("ERR", err)
        return
    }
    print("READY_VAL", val)

    let t2: async.Task[int] = async.failed[int]("simulated failure")
    let res = async.wait(t2) else err {
        print("FAILED_OK", err)
        return
    }
    print("UNEXPECTED", res)
}

main()
'''
    CompilerService(project_root=ROOT).check(source, file="test_ready.wysna")
    result = run_engine("interpreter", source, "test_ready", "", Path(tempfile.gettempdir()))
    assert result.exit_code == 0, f"Error: {result.stderr}"
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "READY_VAL 42"
    assert lines[1] == "FAILED_OK simulated failure"


def test_async_all_combinator():
    source = '''import "std/async"

fn main() -> unit {
    let tasks = [
        async.ready(10),
        async.ready(20),
        async.ready(30)
    ]
    let results = async.all(tasks) else err {
        print("ERR", err)
        return
    }
    print("ALL_COUNT", len(results))
    print("ITEMS", results[0], results[1], results[2])

    let mixed = [
        async.ready(1),
        async.failed[int]("second_failed"),
        async.ready(3)
    ]
    let failed_res = async.all(mixed) else err {
        print("ALL_FAILED_EXPECTED", err)
        return
    }
    print("UNEXPECTED_SUCCESS", len(failed_res))
}

main()
'''
    CompilerService(project_root=ROOT).check(source, file="test_all.wysna")
    result = run_engine("interpreter", source, "test_all", "", Path(tempfile.gettempdir()))
    assert result.exit_code == 0, f"Error: {result.stderr}"
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "ALL_COUNT 3"
    assert lines[1] == "ITEMS 10 20 30"
    assert lines[2] == "ALL_FAILED_EXPECTED second_failed"


def test_async_race_combinator():
    source = '''import "std/async"

fn main() -> unit {
    let tasks = [
        async.ready(99),
        async.ready(100)
    ]
    let winner = async.race(tasks) else err {
        print("ERR", err)
        return
    }
    print("WINNER", winner)
}

main()
'''
    CompilerService(project_root=ROOT).check(source, file="test_race.wysna")
    result = run_engine("interpreter", source, "test_race", "", Path(tempfile.gettempdir()))
    assert result.exit_code == 0, f"Error: {result.stderr}"
    lines = result.stdout.strip().splitlines()
    assert lines[0] == "WINNER 99"


def test_async_all_native_compilation():
    source = '''import "std/async"

fn main() -> unit {
    let tasks = [
        async.ready(100),
        async.ready(200)
    ]
    let results = async.all(tasks) else err {
        print("ERR")
        return
    }
    print(results[0] + results[1])
}

main()
'''
    CompilerService(project_root=ROOT).check(source, file="test_native_all.wysna")
    selfhost = ensure_selfhost_compiler_binary(str(ROOT))
    with tempfile.TemporaryDirectory(prefix="wysna-async-all-native-") as tmpdir:
        for engine in ("interpreter", "ast", "hir"):
            res = run_engine(engine, source, "async_all_native", selfhost, Path(tmpdir) / engine)
            assert res.exit_code == 0, f"{engine} failed: {res.stderr}"
            assert res.stdout == "300\n", f"{engine} mismatch: {res.stdout}"
