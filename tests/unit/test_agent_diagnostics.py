"""Agent-first diagnostic contract: every independent error, exact spans, repairable fixes."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from error.Error import DIAGNOSTIC_CODES, normalize_diagnostic_code
from tooling.FixCLI import apply_edits, collect_diagnostics, fix_source, plan_edits
from tooling.McpServer import McpServer

ROOT = Path(__file__).resolve().parents[2]

MULTI_ERROR = """\
struct User { name: str, age: int }
fn greet(u: User) -> str {
    let x: int = "hi"
    return u.nme
}
fn main() {
    let y = greet(User { name: "a" })
    print(undefined_var)
}
"""


def run_cli(*args, cwd=ROOT):
    return subprocess.run([sys.executable, str(ROOT / "main.py"), *map(str, args)],
                          cwd=cwd, capture_output=True, text=True, timeout=60)


def codes(diagnostics):
    return [item["code"] for item in diagnostics]


def test_checker_reports_every_independent_error_in_one_pass(tmp_path):
    path = tmp_path / "multi.wysna"
    path.write_text(MULTI_ERROR, encoding="utf-8")

    diagnostics = collect_diagnostics(str(path), MULTI_ERROR)

    assert codes(diagnostics) == ["W2001", "W3002", "W2007", "W1001"]
    assert all(item["primary_span"] is not None for item in diagnostics)


def test_primary_diagnostic_is_still_the_first_error(tmp_path):
    path = tmp_path / "multi.wysna"
    path.write_text(MULTI_ERROR, encoding="utf-8")

    result = run_cli("check", "--output-format=json-v1", path)

    payload = json.loads(result.stdout)
    assert result.returncode == 1
    assert payload["diagnostics"][0]["code"] == "W2001"
    assert len(payload["diagnostics"]) == 4


def test_failed_let_does_not_cascade_into_follow_up_errors(tmp_path):
    source = ("fn f() -> int {\n"
              "    let a = missing_one\n"
              "    let b = a + 1\n"
              "    return b\n"
              "}\n")
    diagnostics = collect_diagnostics(str(tmp_path / "c.wysna"), source)

    assert codes(diagnostics) == ["W1001"]
    assert "missing_one" in diagnostics[0]["message"]


def test_skipped_return_is_not_reported_as_missing_return(tmp_path):
    source = ("fn f() -> int {\n"
              "    return nothing_here\n"
              "}\n")
    diagnostics = collect_diagnostics(str(tmp_path / "r.wysna"), source)

    assert codes(diagnostics) == ["W1001"]


def test_valid_program_has_no_diagnostics(tmp_path):
    assert collect_diagnostics(str(tmp_path / "ok.wysna"), "fn main() { print(1) }\n") == []


def test_type_mismatch_exposes_expected_and_found(tmp_path):
    source = 'fn main() {\n    let x: int = "hi"\n}\n'
    (diagnostic,) = collect_diagnostics(str(tmp_path / "t.wysna"), source)

    assert diagnostic["data"]["kind"] == "type_mismatch"
    assert diagnostic["data"]["expected"] == "int"
    assert diagnostic["data"]["found"] == "str"


def test_unknown_field_offers_a_hash_bound_machine_fix(tmp_path):
    source = ("struct User { name: str }\n"
              "fn f(u: User) -> str {\n"
              "    return u.nme\n"
              "}\n")
    (diagnostic,) = collect_diagnostics(str(tmp_path / "f.wysna"), source)

    (fix,) = diagnostic["fixes"]
    (edit,) = fix["edits"]
    assert fix["applicability"] == "machine"
    assert edit["replacement"] == "name"
    assert source.encode()[edit["start_byte"]:edit["end_byte"]] == b"nme"
    assert edit["source_sha256"] == hashlib.sha256(source.encode()).hexdigest()


def test_unknown_identifier_suggestion_without_close_match_has_no_fix(tmp_path):
    (diagnostic,) = collect_diagnostics(str(tmp_path / "u.wysna"),
                                        "fn main() {\n    print(zzzzzzzz)\n}\n")

    assert diagnostic["fixes"] == []


def test_fix_source_repairs_what_it_can_and_reports_the_rest(tmp_path):
    result = fix_source(str(tmp_path / "multi.wysna"), MULTI_ERROR)

    assert result["changed"] is True
    assert "u.name" in result["fixed_source"]
    assert codes(result["remaining_diagnostics"]) == ["W2001", "W2007", "W1001"]


def test_stale_fix_is_rejected_when_source_changed(tmp_path):
    source = "struct U { name: str }\nfn f(u: U) -> str { return u.nme }\n"
    diagnostics = collect_diagnostics(str(tmp_path / "s.wysna"), source)

    chosen, _ = plan_edits(diagnostics, source + "# edited\n")

    assert chosen == []


def test_overlapping_edits_are_not_merged():
    edit = {"source_sha256": hashlib.sha256(b"abcdef").hexdigest(), "start_byte": 0, "end_byte": 3,
            "replacement": "X"}
    other = dict(edit, start_byte=2, end_byte=5)
    diagnostics = [
        {"code": "W1", "fixes": [{"applicability": "machine", "description": "a", "edits": [edit]}]},
        {"code": "W2", "fixes": [{"applicability": "machine", "description": "b", "edits": [other]}]},
    ]

    chosen, skipped = plan_edits(diagnostics, "abcdef")

    assert len(chosen) == 1 and len(skipped) == 1
    assert apply_edits("abcdef", chosen) == "Xdef"


def test_fix_command_applies_only_with_flag(tmp_path):
    path = tmp_path / "fix.wysna"
    original = "struct U { name: str }\nfn f(u: U) -> str { return u.nme }\nfn main() { print(f(U { name: \"a\" })) }\n"
    path.write_text(original, encoding="utf-8")

    dry = run_cli("fix", path)
    assert dry.returncode == 0
    assert path.read_text(encoding="utf-8") == original

    applied = run_cli("fix", "--apply", path)
    assert applied.returncode == 0
    assert "u.name" in path.read_text(encoding="utf-8")
    assert run_cli("check", path).returncode == 0


def test_syntax_error_spans_survive_symlinked_paths(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    os.symlink(real, link)
    source = real / "bad.wysna"
    source.write_text("fn f( {\n}\n", encoding="utf-8")

    result = run_cli("check", "--output-format=json-v1", link / "bad.wysna")

    diagnostics = json.loads(result.stdout)["diagnostics"]
    assert diagnostics and all(item["primary_span"] is not None for item in diagnostics)


def test_parser_errors_carry_expected_and_found(tmp_path):
    (first, *_) = collect_diagnostics(str(tmp_path / "p.wysna"), "fn f( {\n}\n")

    assert first["data"]["kind"] == "expected_token"
    assert first["data"]["found"] == "LBRACE"


def test_discarded_expression_after_newline_is_linted_not_silent(tmp_path):
    path = tmp_path / "asi.wysna"
    path.write_text("fn f(a: int, b: int) -> int {\n    let x = a\n    -b\n    return x\n}\n"
                    "fn main() { print(f(5, 3)) }\n", encoding="utf-8")

    result = run_cli("check", "--output-format=json-v1", path)

    payload = json.loads(result.stdout)
    assert result.returncode == 0 and payload["status"] == "ok"
    (warning,) = payload["diagnostics"]
    assert warning["code"] == "W6001" and warning["severity"] == "warning"
    assert "end of the previous line" in warning["data"]["help"]


def test_calls_and_assignments_are_not_linted(tmp_path):
    path = tmp_path / "ok.wysna"
    path.write_text("fn g() -> int { return 1 }\nfn main() {\n    let mut n = 0\n    g()\n    n = 2\n    print(n)\n}\n",
                    encoding="utf-8")

    payload = json.loads(run_cli("check", "--output-format=json-v1", path).stdout)

    assert payload["diagnostics"] == []


def test_cycle_warning_is_reported_by_default_in_json(tmp_path):
    path = tmp_path / "cycle.wysna"
    path.write_text("class A { public b: B? }\nclass B { public a: A? }\nfn main() { print(1) }\n",
                    encoding="utf-8")

    payload = json.loads(run_cli("check", "--output-format=json-v1", path).stdout)

    assert [d["code"] for d in payload["diagnostics"]] == ["W6002"]
    assert payload["diagnostics"][0]["severity"] == "warning"


def test_legacy_diagnostic_codes_are_gone():
    assert normalize_diagnostic_code("T008") == "W0001"
    assert "W6001" in DIAGNOSTIC_CODES and "W6002" in DIAGNOSTIC_CODES


# -- MCP ------------------------------------------------------------------

def rpc(server, method, params=None, request_id=1):
    return server.handle({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}})


def call(server, name, arguments):
    response = rpc(server, "tools/call", {"name": name, "arguments": arguments})
    result = response["result"]
    return result["isError"], json.loads(result["content"][0]["text"])


def test_mcp_lists_only_static_tools(tmp_path):
    tools = {tool["name"] for tool in rpc(McpServer(tmp_path), "tools/list")["result"]["tools"]}

    assert tools == {"wysna_check", "wysna_fix", "wysna_fmt", "wysna_explain", "wysna_schema",
                     "wysna_api", "wysna_capabilities"}
    assert not any("run" in name for name in tools)


def test_mcp_notifications_get_no_response(tmp_path):
    assert McpServer(tmp_path).handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None


def test_mcp_check_inline_source_returns_all_diagnostics(tmp_path):
    failed, payload = call(McpServer(tmp_path), "wysna_check", {"source": MULTI_ERROR})

    assert failed is False
    assert payload["status"] == "diagnostic_error"
    assert [d["code"] for d in payload["diagnostics"]] == ["W2001", "W3002", "W2007", "W1001"]


def test_mcp_confines_file_access_to_root(tmp_path):
    failed, payload = call(McpServer(tmp_path), "wysna_check", {"path": "/etc/passwd"})

    assert failed is True and "outside the server root" in payload["error"]
    failed, _ = call(McpServer(tmp_path), "wysna_check", {"path": "../outside.wysna"})
    assert failed is True


def test_mcp_requires_exactly_one_source_argument(tmp_path):
    failed, payload = call(McpServer(tmp_path), "wysna_check", {})

    assert failed is True and "exactly one" in payload["error"]


def test_mcp_fix_writes_only_when_apply_is_true(tmp_path):
    target = tmp_path / "m.wysna"
    original = "struct U { name: str }\nfn f(u: U) -> str { return u.nme }\n"
    target.write_text(original, encoding="utf-8")
    server = McpServer(tmp_path)

    _, preview = call(server, "wysna_fix", {"path": "m.wysna"})
    assert preview["written"] is False and target.read_text(encoding="utf-8") == original

    _, applied = call(server, "wysna_fix", {"path": "m.wysna", "apply": True})
    assert applied["written"] is True and "u.name" in target.read_text(encoding="utf-8")


def test_mcp_unknown_tool_and_method_are_protocol_errors(tmp_path):
    server = McpServer(tmp_path)

    assert rpc(server, "tools/call", {"name": "wysna_run"})["error"]["code"] == -32602
    assert rpc(server, "nope")["error"]["code"] == -32601


def test_mcp_stdio_round_trip(tmp_path):
    requests = "\n".join(json.dumps(item) for item in [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]) + "\n"

    result = subprocess.run([sys.executable, str(ROOT / "main.py"), "mcp", "--root", str(tmp_path)],
                            input=requests, capture_output=True, text=True, timeout=30)

    lines = [json.loads(line) for line in result.stdout.splitlines()]
    assert [line["id"] for line in lines] == [1, 2]
    assert lines[0]["result"]["serverInfo"]["name"] == "wysna"


# -- Enriched Diagnostics, Mutability Auto-Fix & Schema Generation --------

def test_immutable_assignment_attaches_related_span_and_machine_fix(tmp_path):
    source = ("fn main() {\n"
              "    let count = 1\n"
              "    count = 2\n"
              "    print(count)\n"
              "}\n")
    path = tmp_path / "mut.wysna"
    (diagnostic,) = collect_diagnostics(str(path), source)

    assert diagnostic["code"] == "W3001"
    assert len(diagnostic["related"]) == 1
    assert diagnostic["related"][0]["line"] == 2
    rel_span = diagnostic["related"][0]["span"]
    assert source.encode()[rel_span["start_byte"]:rel_span["end_byte"]] == b"let"
    assert len(diagnostic["fixes"]) == 1
    assert diagnostic["fixes"][0]["applicability"] == "machine"

    fixed = fix_source(str(path), source)
    assert fixed["changed"] is True
    assert "let mut count = 1" in fixed["fixed_source"]
    assert fixed["remaining_diagnostics"] == []


def test_duplicate_declaration_attaches_related_previous_span(tmp_path):
    source = ("fn main() {\n"
              "    let x = 1\n"
              "    let x = 2\n"
              "}\n")
    (diagnostic,) = collect_diagnostics(str(tmp_path / "dup.wysna"), source)

    assert diagnostic["code"] == "W1002"
    assert diagnostic["data"]["kind"] == "duplicate_declaration"
    assert len(diagnostic["related"]) == 1
    assert diagnostic["related"][0]["line"] == 2
    rel_span = diagnostic["related"][0]["span"]
    assert source.encode()[rel_span["start_byte"]:rel_span["end_byte"]] == b"let"


def test_type_mismatch_exposes_conversion_and_unwrap_hints(tmp_path):
    source = ("fn maybe_num() -> int? { return 1 }\n"
              "fn main() {\n"
              "    let s: str = 42\n"
              "    let n: int = maybe_num()\n"
              "}\n")
    d1, d2 = collect_diagnostics(str(tmp_path / "hints.wysna"), source)

    assert d1["data"]["conversion_hint"] == "str(...)"
    assert d2["data"]["unwrap_hint"] == "?"


def test_multi_error_recovery_across_class_methods_and_generic_templates(tmp_path):
    source = (
        "class Counter {\n"
        "    fn bad_one() -> int { return \"not_int\" }\n"
        "    fn bad_two() -> str { return 123 }\n"
        "}\n"
        "fn gen_bad_one[T](x: T) -> T { return x + true }\n"
        "fn gen_bad_two[U](y: U) -> U { return y - \"str\" }\n"
        "fn main() { print(1) }\n"
    )
    diagnostics = collect_diagnostics(str(tmp_path / "cls_gen.wysna"), source)

    assert codes(diagnostics) == ["W2001", "W2001", "W2004", "W2004"]


def test_schema_cli_and_mcp_emit_json_schema_and_tool_metadata(tmp_path):
    source = (
        "## Status of an invoice.\n"
        "pub enum Status { Pending, Paid(int) }\n"
        "## Invoice record.\n"
        "pub struct Invoice { id: int, amount: float, note: str?, status: Status }\n"
        "pub struct BillingError { reason: str }\n"
        "## Compute invoice total purely.\n"
        "pub fn compute_total(inv: Invoice, tax_rate: float) -> float ! BillingError {\n"
        "    return Ok(inv.amount * (1.0 + tax_rate))\n"
        "}\n"
        "## Log invoice to stdout.\n"
        "pub fn log_invoice(inv: Invoice) {\n"
        "    print(inv.id)\n"
        "}\n"
    )
    path = tmp_path / "billing.wysna"
    path.write_text(source, encoding="utf-8")

    cli_res = run_cli("schema", "--output-format=json-v1", path)
    assert cli_res.returncode == 0
    envelope = json.loads(cli_res.stdout)
    assert envelope["status"] == "ok"
    schema = envelope["result"]

    assert schema["schema_version"] == "wysna.schema/1"
    assert "Invoice" in schema["$defs"] and "Status" in schema["$defs"]
    inv_def = schema["$defs"]["Invoice"]
    assert inv_def["description"] == "Invoice record."
    assert set(inv_def["required"]) == {"id", "amount", "status"}
    assert inv_def["properties"]["status"] == {"$ref": "#/$defs/Status"}

    tools_by_name = {t["name"]: t for t in schema["tools"]}
    assert set(tools_by_name) == {"compute_total", "log_invoice"}

    compute = tools_by_name["compute_total"]
    assert compute["description"] == "Compute invoice total purely."
    assert compute["fallible"] is True
    assert compute["output_schema"] == {"type": "number"}
    assert compute["error_schema"] == {"$ref": "#/$defs/BillingError"}
    assert compute["pure"] is True
    assert compute["idempotent"] is True
    assert compute["capabilities"] == []

    log_fn = tools_by_name["log_invoice"]
    assert log_fn["pure"] is False
    assert log_fn["idempotent"] is False
    assert "stdio" in log_fn["capabilities"]

    failed, mcp_payload = call(McpServer(tmp_path), "wysna_schema",
                               {"source": source, "function": "compute_total"})
    assert failed is False
    assert [t["name"] for t in mcp_payload["result"]["tools"]] == ["compute_total"]


def test_interprocedural_closure_effect_resolution_certifies_pure_higher_order_tools(tmp_path):
    source = (
        'import "std/algorithms" as alg\n'
        "pub fn double_positives(xs: list[int]) -> list[int] {\n"
        "    let pos = alg.filter[int](xs, |x: int| => x > 0)\n"
        "    return alg.map[int, int](pos, |x: int| => x * 2)\n"
        "}\n"
        "print(len(double_positives([-1, 2, 3])))\n"
    )
    path = tmp_path / "higher_order.wysna"
    path.write_text(source, encoding="utf-8")

    cli_res = run_cli("schema", "--output-format=json-v1", path)
    assert cli_res.returncode == 0
    schema = json.loads(cli_res.stdout)["result"]
    (tool,) = schema["tools"]
    assert tool["name"] == "double_positives"
    assert tool["complete_within_model"] is True
    assert tool["capabilities"] == []
    assert tool["pure"] is True
    assert tool["idempotent"] is True


def test_sandbox_flag_enforces_capabilities_on_run_emit_c_and_native_build(tmp_path):
    pure_src = (
        'import "std/algorithms" as alg\n'
        "let xs = alg.map[int, int]([1, 2, 3], |x: int| => x + 10)\n"
        "print(xs[0], xs[2])\n"
    )
    pure_path = tmp_path / "pure_agent.wysna"
    pure_path.write_text(pure_src, encoding="utf-8")

    # 1. Sandboxed run succeeds when closures resolve to allowed stdio capability
    run_res = run_cli("run", "--sandbox", pure_path)
    assert run_res.returncode == 0
    assert run_res.stdout.strip() == "11 13"

    # 2. Sandboxed emit-c injects C99 capability guard macros
    c_out = tmp_path / "pure_agent.c"
    emit_res = run_cli("emit-c", "--sandbox", pure_path, "-o", c_out)
    assert emit_res.returncode == 0
    c_text = c_out.read_text(encoding="utf-8")
    assert "#define WYSNA_SANDBOX_ENFORCED 1" in c_text
    assert "#define WYSNA_DENY_CAP_FILESYSTEM_WRITE 1" in c_text

    # 3. Sandboxed build rejects disallowed filesystem.write capability
    impure_src = (
        'fn save() -> unit ! IoError {\n'
        '    write_file("forbidden.txt", "data")?\n'
        '}\n'
        'let _ = save() else err { print(err.message) }\n'
    )
    impure_path = tmp_path / "impure_agent.wysna"
    impure_path.write_text(impure_src, encoding="utf-8")
    bin_out = tmp_path / "impure_bin"
    build_res = run_cli("build", "--sandbox", "--output-format=json-v1", impure_path, "-o", bin_out)
    assert build_res.returncode == 1
    payload = json.loads(build_res.stdout)
    assert payload["diagnostics"][0]["code"] == "W5006"
    assert "filesystem.write" in payload["diagnostics"][0]["message"]


def test_schema_emit_codecs_synthesizes_executable_typed_json_round_trip(tmp_path):
    types_src = (
        "pub enum Role { Admin, Agent, Guest }\n"
        "pub enum Action { Ping, Execute(str) }\n"
        "pub struct TaskSpec {\n"
        "    id: int,\n"
        "    title: str,\n"
        "    active: bool,\n"
        "    role: Role,\n"
        "    action: Action,\n"
        "    tags: list[str],\n"
        "    note: str?,\n"
        "}\n"
    )
    types_path = tmp_path / "types.wysna"
    types_path.write_text(types_src, encoding="utf-8")

    res = run_cli("schema", "--emit-codecs", "--output-format=json-v1", types_path)
    assert res.returncode == 0
    codecs_src = json.loads(res.stdout)["result"]["codecs_source"]
    assert "pub fn to_json_TaskSpec" in codecs_src
    assert "pub fn from_json_TaskSpec" in codecs_src

    prog_src = (
        'import "std/json"\n'
        + types_src
        + "\n"
        + codecs_src.replace('import "std/json"\n', "")
        + "\n"
        "fn main() -> unit ! json.JsonError {\n"
        "    let item = TaskSpec {\n"
        "        id: 7,\n"
        '        title: "audit",\n'
        "        active: true,\n"
        "        role: Role.Agent,\n"
        '        action: Action.Execute("wysna fix"),\n'
        '        tags: ["a", "b"],\n'
        '        note: Some("ok"),\n'
        "    }\n"
        "    let encoded = to_json_TaskSpec(item)?\n"
        "    let round = from_json_TaskSpec(encoded)?\n"
        '    print(round.id, round.title, round.active, len(round.tags), round.note ?? "none")\n'
        "}\n"
        "let _ = main() else err { print(err.message) }\n"
    )
    prog_path = tmp_path / "round_trip.wysna"
    prog_path.write_text(prog_src, encoding="utf-8")
    exec_res = run_cli("run", prog_path)
    assert exec_res.returncode == 0
    assert exec_res.stdout.strip() == "7 audit true 2 ok"


def test_algorithms_parallel_map_filter_map_partition_and_flat_map(tmp_path):
    source = (
        'import "std/algorithms" as alg\n'
        "let xs = [1, 2, 3, 4]\n"
        "let sq = alg.parallel_map[int, int](xs, |x: int| => x * x)\n"
        "let evens_doubled = alg.filter_map[int, int](xs, |x: int| -> int? {\n"
        "    if x % 2 == 0 { return Some(x * 10) }\n"
        "    return none\n"
        "})\n"
        "let parts = alg.partition[int](xs, |x: int| => x <= 2)\n"
        "let expanded = alg.flat_map[int, int]([1, 2], |x: int| => [x, x * 10])\n"
        "match parts {\n"
        "    (small, large) => print(sq[3], len(evens_doubled), evens_doubled[1], len(small), len(large), len(expanded), expanded[3]),\n"
        "}\n"
    )
    path = tmp_path / "alg_primitives.wysna"
    path.write_text(source, encoding="utf-8")
    res = run_cli("run", path)
    assert res.returncode == 0
    assert res.stdout.strip() == "16 2 40 2 2 4 20"


def test_mir_local_cow_uniqueness_eliminates_false_detaches_on_read_then_mutate(tmp_path):
    from compiler.CompilerService import CompilerService
    from compiler.NativeToolchain import NativeToolchain

    source = (
        "fn main() -> unit {\n"
        "    let mut xs: list[int] = [10, 20, 30]\n"
        "    let n = len(xs)\n"
        "    let first = xs[0]\n"
        "    xs.append(n + first)\n"
        "    xs[0] = 99\n"
        '    eprint("__COW_CHECKPOINT__:unique_local")\n'
        "    let alias = xs\n"
        "    xs.append(77)\n"
        '    eprint("__COW_CHECKPOINT__:with_live_alias")\n'
        "    print(xs[0], xs[3], len(alias), len(xs))\n"
        "}\n"
        "main()\n"
    )
    svc = CompilerService()
    checked = svc.check(source, str(tmp_path / "cow_uniq.wysna"))
    c_code = svc.emit_c(checked).source
    bin_path = str(tmp_path / "cow_uniq_bin")
    NativeToolchain(str(ROOT)).build(c_code, bin_path)

    import os
    env = dict(os.environ, WYSNA_COW_METRICS="1")
    proc = subprocess.run([bin_path], env=env, capture_output=True, text=True, timeout=30)
    assert proc.returncode == 0
    assert proc.stdout.strip() == "99 13 4 5"
    stderr_lines = proc.stderr.splitlines()
    unique_line = next(line for line in stderr_lines if "unique_local" in line)
    alias_line = next(line for line in stderr_lines if "with_live_alias" in line)
    assert "detach_list=0" in unique_line
    assert "detach_list=1" in alias_line


