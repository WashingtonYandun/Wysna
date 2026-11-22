"""Tests for Seed tooling parity, visibility accuracy, API scoping, demangling, and caching."""
import json
import subprocess
import sys
from pathlib import Path

from compiler.CompilerService import CompilerService


ROOT = Path(__file__).resolve().parents[2]


def test_explain_cache_reuses_effect_analysis(tmp_path):
    target = tmp_path / "cached.wysna"
    source = 'fn pure_add(a: int, b: int) -> int { return a + b }\nfn main() { print(pure_add(1, 2)) }\n'
    target.write_text(source, encoding="utf-8")
    service = CompilerService()
    checked = service.check(source, str(target))
    first = service.explain_with_foreign_bindings(checked)
    second = service.explain_with_foreign_bindings(checked)
    assert first is second
    assert checked._cached_explain is first


def test_semantic_graph_visibility_distinguishes_pub_and_private(tmp_path):
    target = tmp_path / "visibility.wysna"
    source = (
        "struct PrivStruct { x: int }\n"
        "pub struct PubStruct { y: int }\n"
        "fn helper() -> int { return 1 }\n"
        "pub fn exported_fn() -> int { return helper() }\n"
    )
    target.write_text(source, encoding="utf-8")
    service = CompilerService()
    graph = service.graph(service.check(source, str(target)))
    nodes_by_name = {node["name"]: node for node in graph["nodes"]}
    assert nodes_by_name["PrivStruct"]["visibility"] == "private"
    assert nodes_by_name["PubStruct"]["visibility"] == "public"
    assert nodes_by_name["helper"]["visibility"] == "private"
    assert nodes_by_name["exported_fn"]["visibility"] == "public"


def test_api_module_query_scopes_to_single_module_unless_with_deps():
    scoped = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "api", "std/http", "--output-format=json-v1"],
        capture_output=True, text=True, check=True, cwd=str(ROOT),
    )
    payload = json.loads(scoped.stdout)
    identities = [m["identity"] for m in payload["result"]["modules"]]
    assert identities == ["std/http"], identities
    request_sym = next(s for s in payload["result"]["modules"][0]["symbols"] if s["name"] == "request")
    assert request_sym["availability"]["selfhost"] == "requires-libcurl"

    fs_proc = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "api", "std/fs", "--output-format=json-v1"],
        capture_output=True, text=True, check=True, cwd=str(ROOT),
    )
    fs_payload = json.loads(fs_proc.stdout)
    fs_sym = fs_payload["result"]["modules"][0]["symbols"][0]
    assert fs_sym["availability"]["selfhost"] == "supported"

    expanded = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "api", "std/http", "--with-deps", "--output-format=json-v1"],
        capture_output=True, text=True, check=True, cwd=str(ROOT),
    )
    expanded_payload = json.loads(expanded.stdout)
    assert len(expanded_payload["result"]["modules"]) > 1


def test_seed_type_mismatch_demangles_imported_types_in_message_and_data(tmp_path):
    target = tmp_path / "mismatch.wysna"
    target.write_text(
        'import "std/json"\n'
        'fn parse_items(raw: str) -> list[json.Value] ! str {\n'
        '    return []\n'
        '}\n'
        'fn main() {\n'
        '    let bad: int = parse_items("[]")\n'
        '    print(bad)\n'
        '}\n',
        encoding="utf-8",
    )
    res = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "check", str(target), "--output-format=json-v1"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert res.returncode == 1
    payload = json.loads(res.stdout)
    diag = payload["diagnostics"][0]
    assert "__wysna_module_" not in diag["message"]
    assert diag["data"].get("kind") == "type_mismatch"
    assert "__wysna_module_" not in diag["data"].get("expected", "")
    assert "__wysna_module_" not in diag["data"].get("found", "")


def test_selfhost_formatter_matches_seed_on_inline_spacing_new_and_multiline_strings(tmp_path):
    from compiler.Bootstrap import ensure_selfhost_compiler_binary

    selfhost_bin = ensure_selfhost_compiler_binary(str(ROOT))
    parity_target = tmp_path / "fmt_parity.wysna"
    parity_source = (
        "class Counter {\n"
        "    public value: int\n"
        "    public fn init(v: int) -> unit {\n"
        "        this.value = v\n"
        "    }\n"
        "}\n"
        "\n"
        "fn add(a:int,b:int)->int{\n"
        "    let x=1+2\n"
        "    let neg = -5\n"
        "    let rng=0..10\n"
        "    let c = Counter(1)\n"
        "    let mut total = x + neg + c.value + a + b\n"
        "    for i in rng {\n"
        "        total = total + i\n"
        "    }\n"
        "    return total\n"
        "}\n"
    )
    parity_target.write_text(parity_source, encoding="utf-8")
    seed_res = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "fmt", "--engine=seed", str(parity_target)],
        capture_output=True, text=True, check=True, cwd=str(ROOT),
    )
    comp_res = subprocess.run(
        [selfhost_bin, "--stdlib-root", str(ROOT / "stdlib"), "fmt", str(parity_target)],
        capture_output=True, text=True, check=True, cwd=str(ROOT),
    )
    for out in (seed_res.stdout, comp_res.stdout):
        assert "fn add(a: int, b: int) -> int {" in out
        assert "    let x = 1 + 2" in out
        assert "    let neg = -5" in out
        assert "    let rng = 0..10" in out
        assert "    let c = Counter(1)" in out
        assert "new Counter" not in out
    assert comp_res.stdout.rstrip() == seed_res.stdout.rstrip()

    ml_target = tmp_path / "fmt_multiline.wysna"
    ml_source = (
        'fn banner() -> str {\n'
        'let x=1+2\n'
        'let msg = """\n'
        '  a+b = untouched\n'
        '    keep indent\n'
        '"""\n'
        'return msg + str(x)\n'
        '}\n'
    )
    ml_target.write_text(ml_source, encoding="utf-8")
    ml_comp = subprocess.run(
        [selfhost_bin, "--stdlib-root", str(ROOT / "stdlib"), "fmt", str(ml_target)],
        capture_output=True, text=True, check=True, cwd=str(ROOT),
    )
    assert "    let x = 1 + 2\n" in ml_comp.stdout
    assert '    let msg = """\n  a+b = untouched\n    keep indent\n"""\n' in ml_comp.stdout


def test_emit_c_line_directives_compiles_under_clang_g(tmp_path):
    src_path = tmp_path / "dbg_sample.wysna"
    c_path = tmp_path / "dbg_sample.c"
    bin_path = tmp_path / "dbg_sample_bin"
    src_path.write_text(
        "fn compute(n: int) -> int {\n"
        "    let mut acc = 0\n"
        "    for i in 1..=n {\n"
        "        acc = acc + i\n"
        "    }\n"
        "    return acc\n"
        "}\n"
        "print(compute(5))\n",
        encoding="utf-8",
    )
    res = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), "emit-c", "--line-directives", str(src_path), "-o", str(c_path)],
        capture_output=True, text=True, check=True, cwd=str(ROOT),
    )
    assert res.returncode == 0
    c_text = c_path.read_text(encoding="utf-8")
    assert f'#line 2 "{src_path}"' in c_text or f'#line 4 "{src_path}"' in c_text
    subprocess.run(
        ["clang", "-g", "-std=c99", "-I" + str(ROOT), str(c_path), "-o", str(bin_path)],
        check=True, capture_output=True, text=True,
    )
    run_res = subprocess.run([str(bin_path)], capture_output=True, text=True, check=True)
    assert run_res.stdout.strip() == "15"


