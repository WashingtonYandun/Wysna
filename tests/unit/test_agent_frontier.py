"""Tests for the 6 Agent-First Frontier Pillars in Wysna."""

import io
import json
import pytest

from compiler.CompilerService import CompilerService
from error.Error import TypeError, WysnaRuntimeError
from formatter.Formatter import Formatter
from runtime.ExecutionContext import ExecutionContext
from tooling.CLI import main as cli_main


def _run_cli(args):
    try:
        rc = cli_main(args)
        return 0 if rc is None else int(rc)
    except SystemExit as exc:
        return 0 if exc.code is None else int(exc.code)


# ============================================================================
# Pillar 1: Deterministic Execution Quotas (--max-steps, --max-heap-bytes, W4004)
# ============================================================================

def test_interpreter_step_quota_halts_infinite_loop():
    source = """
fn main() {
    let mut x = 0
    while true {
        x = x + 1
    }
}
"""
    service = CompilerService()
    checked = service.check(source, "quota_steps.wysna")
    ctx = ExecutionContext.for_program("quota_steps.wysna", max_steps=50)
    with pytest.raises(WysnaRuntimeError) as exc_info:
        service.interpret(checked, execution_context=ctx)
    assert exc_info.value.code == "W4004"
    assert "Execution step quota exceeded" in exc_info.value.message


def test_interpreter_heap_quota_halts_runaway_allocation():
    source = """
fn main() {
    let mut items: list[str] = []
    let mut i = 0
    while i < 1000 {
        items.append("allocating_string_payload_for_heap_budget_test")
        i = i + 1
    }
}
"""
    service = CompilerService()
    checked = service.check(source, "quota_heap.wysna")
    ctx = ExecutionContext.for_program("quota_heap.wysna", max_heap_bytes=512)
    with pytest.raises(WysnaRuntimeError) as exc_info:
        service.interpret(checked, execution_context=ctx)
    assert exc_info.value.code == "W4004"
    assert "Execution heap memory quota exceeded" in exc_info.value.message


def test_cli_run_and_emit_c_execution_quotas(tmp_path, capsys):
    prog = tmp_path / "loop.wysna"
    prog.write_text(
        "fn main() {\n    let mut i = 0\n    while true {\n        i = i + 1\n    }\n}\n",
        encoding="utf-8",
    )
    code = _run_cli(["run", "--json", "--max-steps", "40", str(prog)])
    captured = capsys.readouterr()
    assert code == 1
    payload = json.loads(captured.err.strip() or captured.out.strip())
    assert payload["code"] == "W4004"

    # Verify C99 emission embeds WYSNA_DEFAULT_MAX_STEPS and wysna_step_tick()
    c_out = tmp_path / "loop.c"
    code_c = _run_cli(
        ["emit-c", "--max-steps", "100", "--max-heap-mb", "4", str(prog), "-o", str(c_out)],
    )
    capsys.readouterr()
    assert code_c == 0
    c_text = c_out.read_text(encoding="utf-8")
    assert "#define WYSNA_DEFAULT_MAX_STEPS 100LL" in c_text
    assert "#define WYSNA_DEFAULT_MAX_HEAP_BYTES 4194304LL" in c_text
    assert "wysna_step_tick();" in c_text


# ============================================================================
# Pillar 2: Deterministic Effect Recording & Replay (Cassette Runtime)
# ============================================================================

def test_effect_trace_record_and_replay_cli(tmp_path, capsys):
    data_file = tmp_path / "input.txt"
    data_file.write_text("live-value-42", encoding="utf-8")
    trace_file = tmp_path / "cassette.json"
    prog = tmp_path / "agent_io.wysna"
    prog.write_text(
        f"""
fn main() -> unit ! IoError {{
    let content = read_file({json.dumps(str(data_file))})?
    let t = unix_time()
    print(content + ":" + str(t > 0))
}}
""",
        encoding="utf-8",
    )

    rc1 = _run_cli(["run", "--record-trace", str(trace_file), str(prog)])
    cap1 = capsys.readouterr()
    assert rc1 == 0
    recorded_output = cap1.out.strip()
    assert recorded_output == "live-value-42:true"
    assert trace_file.exists()

    trace_payload = json.loads(trace_file.read_text(encoding="utf-8"))
    assert trace_payload["schema"] == "wysna.effect.trace/1"
    assert len(trace_payload["events"]) == 2

    # Delete the underlying file; replay must succeed purely from the cassette without touching disk!
    data_file.unlink()
    rc2 = _run_cli(["run", "--replay-trace", str(trace_file), str(prog)])
    cap2 = capsys.readouterr()
    assert rc2 == 0
    assert cap2.out.strip() == recorded_output


# ============================================================================
# Pillar 3: First-Class Function & Struct Contracts (requires / ensures, W4005)
# ============================================================================

def test_contracts_parse_format_typecheck_and_runtime():
    source = """
struct Account {
    balance: int requires balance >= 0,
}

fn withdraw(amount: int, current: int) -> int
    requires amount > 0
    requires amount <= current
    ensures result >= 0 and result == current - amount
{
    return current - amount
}

fn main() {
    let acc = Account { balance: 100 }
    let rem = withdraw(40, acc.balance)
    print(rem)
}
"""
    service = CompilerService()
    checked = service.check(source, "contracts_ok.wysna")
    buf = io.StringIO()
    service.interpret(checked, stdout=buf)
    assert buf.getvalue().strip() == "60"

    # Formatter round-trip preserves requires and ensures
    formatted = Formatter().format(checked.ast)
    assert "requires balance >= 0" in formatted
    assert "requires amount > 0" in formatted
    assert "ensures result >= 0" in formatted


def test_contract_violation_raises_w4005_in_interpreter_and_c99(tmp_path):
    source = """
fn buggy_double(x: int) -> int
    requires x > 0
    ensures result > x
{
    return x - 1
}

fn main() {
    print(buggy_double(5))
}
"""
    service = CompilerService()
    checked = service.check(source, "contracts_fail.wysna")
    with pytest.raises(WysnaRuntimeError) as exc_info:
        service.interpret(checked)
    assert exc_info.value.code == "W4005"
    assert "ensures" in exc_info.value.message

    # Also verify C99 emission lowers contracts to wysna_builtin_contract_assert_at
    emission = service.emit_c(checked)
    assert "wysna_builtin_contract_assert_at" in emission.source


def test_struct_field_contract_violation_raises_w4005():
    source = """
struct Percentage {
    value: int requires value >= 0 and value <= 100,
}

fn main() {
    let p = Percentage { value: 150 }
    print(p.value)
}
"""
    service = CompilerService()
    checked = service.check(source, "struct_contract_fail.wysna")
    with pytest.raises(WysnaRuntimeError) as exc_info:
        service.interpret(checked)
    assert exc_info.value.code == "W4005"


def test_contract_non_bool_rejected_by_typechecker():
    source = """
fn bad(x: int) -> int requires x + 1 {
    return x
}
"""
    service = CompilerService()
    with pytest.raises(TypeError) as exc_info:
        service.check(source, "bad_contract.wysna")
    assert exc_info.value.code == "W2001"


def test_schema_cli_exports_contract_constraints(tmp_path, capsys):
    src = tmp_path / "tools.wysna"
    src.write_text(
        """
pub struct Pagination {
    limit: int requires limit >= 1 and limit <= 100,
    query: str requires len(query) >= 3,
}

# Search items with bounded pagination.
pub fn search_items(limit: int, tag: str) -> int
    requires limit >= 1 and limit <= 50
    requires len(tag) >= 2 and len(tag) <= 32
    ensures result >= 0
{
    return limit
}
""",
        encoding="utf-8",
    )
    rc = _run_cli(["schema", str(src)])
    captured = capsys.readouterr()
    assert rc == 0
    payload = json.loads(captured.out)
    assert payload["schema_version"] == "wysna.schema/1"
    defs = payload["$defs"]
    pag_props = defs["Pagination"]["properties"]
    assert pag_props["limit"]["minimum"] == 1
    assert pag_props["limit"]["maximum"] == 100
    assert pag_props["query"]["minLength"] == 3

    tool = payload["tools"][0]
    assert tool["name"] == "search_items"
    assert tool["pure"] is True
    in_props = tool["input_schema"]["properties"]
    assert in_props["limit"]["minimum"] == 1
    assert in_props["limit"]["maximum"] == 50
    assert in_props["tag"]["minLength"] == 2
    assert in_props["tag"]["maxLength"] == 32
    assert tool["output_schema"]["minimum"] == 0
    assert len(tool["requires"]) == 2
    assert len(tool["ensures"]) == 1


# ============================================================================
# Pillar 4: Scalar List Specialization & Unboxed Container Fast Paths
# ============================================================================

def test_scalar_list_unboxed_indexing_and_iteration_in_c_codegen():
    source = """
fn sum_ints(xs: list[int]) -> int {
    let mut total = 0
    for x in xs {
        total = total + x
    }
    return total + xs[0]
}

fn main() {
    let values = [10, 20, 30]
    print(sum_ints(values))
}
"""
    service = CompilerService()
    checked = service.check(source, "scalar_list.wysna")
    emission = service.emit_c(checked)
    assert "wysna_list_iteration_get_int_at" in emission.source
    assert "wysna_list_get_int_at" in emission.source


# ============================================================================
# Pillar 5: Native Selfhost CLI (--version, --check, --check-json)
# ============================================================================

def test_selfhost_cli_check_json_and_version(tmp_path, capsys):
    sample = tmp_path / "sample.wysna"
    sample.write_text("fn add(a: int, b: int) -> int { return a + b }\nfn main() { print(add(2, 3)) }\n", encoding="utf-8")

    # Run selfhost/compiler.wysna through the seed interpreter with --check-json
    rc = _run_cli(["run", "selfhost/compiler.wysna", "--", "--check-json", str(sample)])
    captured = capsys.readouterr()
    assert rc == 0, captured.err
    envelope = json.loads(captured.out.strip())
    assert envelope["schema_version"] == "wysna.cli.result/1"
    assert envelope["command"] == "check"
    assert envelope["ok"] is True
    assert envelope["data"]["file"] == str(sample)
    assert envelope["data"]["functions"] >= 2


# ============================================================================
# Pillar 6: Structured Deterministic Concurrency (std/algorithms & std/http)
# ============================================================================

def test_structured_parallel_batch_and_http_batch():
    source = """
import "std/algorithms"
import "std/http"

fn square(x: int) -> int {
    return x * x
}

fn is_even(x: int) -> bool {
    return x % 2 == 0
}

fn add(a: int, b: int) -> int {
    return a + b
}

fn main() {
    let xs = [1, 2, 3, 4, 5]
    let batched = algorithms.parallel_batch[int, int](xs, 2, square)
    let evens = algorithms.parallel_filter[int](xs, is_even)
    let total = algorithms.parallel_reduce[int, int](xs, 0, square, add)
    print(batched[4], len(evens), total)
}
"""
    service = CompilerService()
    checked = service.check(source, "parallel_algo.wysna")
    buf = io.StringIO()
    service.interpret(checked, stdout=buf)
    assert buf.getvalue().strip() == "25 2 55"


# ============================================================================
# Native backends: quotas and contracts must hold outside the interpreter
# ============================================================================

import subprocess
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]


def _wysna(*args):
    return subprocess.run([sys.executable, str(_REPO / "main.py"), *args], cwd=_REPO,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=300)


def _native(tmp_path, source, backend, *flags):
    """Run through the real native pipeline: LLVM via `run`, C99 via `build` + execute."""
    prog = tmp_path / "program.wysna"
    prog.write_text(source, encoding="utf-8")
    if backend == "llvm":
        return _wysna("run", "--backend=llvm", *flags, str(prog))
    binary = tmp_path / "program.bin"
    built = _wysna("build", "--backend=c99", *flags, str(prog), "-o", str(binary))
    assert built.returncode == 0, built.stderr
    return subprocess.run([str(binary)], capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, timeout=60)


@pytest.mark.parametrize("backend", ["c99", "llvm"])
def test_native_run_enforces_step_quota(tmp_path, backend):
    source = "fn main() {\n    let mut i = 0\n    while true {\n        i = i + 1\n    }\n}\n"
    result = _native(tmp_path, source, backend, "--max-steps", "300")
    assert result.returncode != 0
    assert "W4004" in result.stderr + result.stdout


@pytest.mark.parametrize("backend", ["c99", "llvm"])
def test_native_run_enforces_contracts(tmp_path, backend):
    source = ("fn half(x: int) -> int\n    requires x > 0\n    ensures result < x\n{\n    return x\n}\n"
              "fn main() {\n    print(half(4))\n}\n")
    result = _native(tmp_path, source, backend)
    assert result.returncode != 0
    assert "W4005" in result.stderr + result.stdout
