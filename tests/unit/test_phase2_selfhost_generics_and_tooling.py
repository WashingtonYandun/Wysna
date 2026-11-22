"""Tests for Phase 2B native Agent-First CLI capabilities in selfhost compiler and driver.c."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

from compiler.Bootstrap import ensure_selfhost_compiler_binary
from common.Platform import executable_name
from common.Version import WYSNA_VERSION


ROOT = Path(__file__).resolve().parents[2]


SAMPLE_OOP = """interface Logger {
    fn log(msg: str) -> str
}

class ConsoleLogger implements Logger {
    prefix: str

    fn init(prefix: str) {
        this.prefix = prefix
    }

    public fn log(msg: str) -> str {
        return this.prefix + ": " + msg
    }
}

class Service {
    logger: Logger

    fn init(logger: Logger) {
        this.logger = logger
    }

    fn execute(task: str) -> str {
        return this.logger.log(task)
    }
}

pub async fn run_pipeline(name: str) -> str {
    let console = ConsoleLogger("SYS")
    let svc = Service(console)
    let out = svc.execute(name)
    print(out)
    return out
}

let out = await run_pipeline("boot")
"""


def _compiler_bin() -> str:
    return ensure_selfhost_compiler_binary(str(ROOT))


def _run_compiler(*args, cwd=ROOT):
    return subprocess.run(
        [_compiler_bin(), "--stdlib-root", str(ROOT / "stdlib"), *map(str, args)],
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_selfhost_check_and_fmt_json_v1_valid_and_invalid(tmp_path):
    good = tmp_path / "good.wysna"
    good.write_text("fn  add(x: int) -> int {\nreturn x + 1\n}\n", encoding="utf-8")

    res_ok = _run_compiler("check", "--output-format=json-v1", good)
    assert res_ok.returncode == 0, res_ok.stderr
    ok_payload = json.loads(res_ok.stdout)
    assert ok_payload["schema_version"] == "wysna.tool-result/1"
    assert ok_payload["status"] == "ok"
    assert ok_payload["command"] == "check"
    assert ok_payload["tool_exit_code"] == 0

    res_fmt = _run_compiler("fmt", "--output-format=json-v1", good)
    assert res_fmt.returncode == 0, res_fmt.stderr
    fmt_payload = json.loads(res_fmt.stdout)
    assert fmt_payload["schema_version"] == "wysna.tool-result/1"
    assert fmt_payload["command"] == "fmt"
    art = fmt_payload["artifacts"][0]
    assert art["kind"] == "formatted_source"
    assert art["sha256"] == hashlib.sha256(art["content"].encode("utf-8")).hexdigest()
    assert "    return x + 1\n" in art["content"]

    bad = tmp_path / "bad.wysna"
    bad_src = 'fn main() {\n    let x: int = "oops"\n}\n'
    bad.write_text(bad_src, encoding="utf-8")

    res_bad = _run_compiler("check", "--output-format=json-v1", bad)
    assert res_bad.returncode == 1
    bad_payload = json.loads(res_bad.stdout)
    assert bad_payload["schema_version"] == "wysna.tool-result/1"
    assert bad_payload["status"] == "diagnostic_error"
    assert bad_payload["diagnostics"][0]["code"] == "W2001"
    span = bad_payload["diagnostics"][0]["primary_span"]
    assert span is not None
    assert span["source_sha256"] == hashlib.sha256(bad_src.encode("utf-8")).hexdigest()
    assert span["end_byte"] > span["start_byte"]

    res_legacy = _run_compiler("--check-json", bad)
    assert res_legacy.returncode == 1
    legacy_payload = json.loads(res_legacy.stdout)
    assert legacy_payload["schema_version"] == "wysna.cli.result/1"
    assert legacy_payload["ok"] is False
    assert legacy_payload["diagnostics"][0]["code"] == "W2001"


def test_selfhost_graph_inspect_patch_and_explain_end_to_end(tmp_path):
    target = tmp_path / "svc.wysna"
    target.write_text(SAMPLE_OOP, encoding="utf-8")

    # 1. Native graph JSON-v1 + blast radius impact
    res_graph = _run_compiler("graph", target, "--output-format=json-v1")
    assert res_graph.returncode == 0, res_graph.stderr
    g_env = json.loads(res_graph.stdout)
    assert g_env["status"] == "ok"
    graph = g_env["result"]
    assert graph["schema_version"] == "wysna.graph/1"
    assert graph["graph_hash"].startswith("graph:")
    nodes = {n["id"]: n for n in graph["nodes"]}
    assert "class:ConsoleLogger" in nodes["interface:Logger"]["implemented_by"]
    assert "class:Service" in nodes["interface:Logger"]["used_by"]
    assert nodes["fn:run_pipeline"]["is_async"] is True
    assert "stdio.write" in nodes["fn:run_pipeline"]["effects"]

    res_impact = _run_compiler("graph", target, "--symbol", "Logger", "--rel", "impact", "--output-format=json-v1")
    assert res_impact.returncode == 0, res_impact.stderr
    imp = json.loads(res_impact.stdout)["result"]["impact"]
    assert "class:ConsoleLogger" in imp["transitive_dependents"]
    assert "class:Service" in imp["transitive_dependents"]
    assert "fn:run_pipeline" in imp["transitive_dependents"]

    # 2. Native inspect index & symbol with dependencies
    res_idx = _run_compiler("inspect", target, "--output-format=json-v1")
    assert res_idx.returncode == 0, res_idx.stderr
    idx_data = json.loads(res_idx.stdout)["result"]
    assert idx_data["schema_version"] == "wysna.inspect/1"
    assert {"Logger", "ConsoleLogger", "Service", "run_pipeline"} <= {s["name"] for s in idx_data["symbols"]}

    res_sym = _run_compiler("inspect", target, "--symbol", "run_pipeline", "--with-deps", "--output-format=json-v1")
    assert res_sym.returncode == 0, res_sym.stderr
    sym_res = json.loads(res_sym.stdout)["result"]
    assert sym_res["source"].startswith("pub async fn run_pipeline")
    assert sym_res["source"].endswith("}")
    dep_ids = {d["id"] for d in sym_res["dependencies"]}
    assert "class:ConsoleLogger" in dep_ids
    assert "class:Service" in dep_ids
    current_hash = sym_res["graph_hash"]

    # 3. Stale graph hash rejected with W5005; disk untouched
    res_stale = _run_compiler(
        "patch", target,
        "--symbol", "run_pipeline",
        "--expect-graph-hash", "graph:0000000000000000",
        "--with", "pub async fn run_pipeline(name: str) -> str { return name }",
        "--apply",
        "--output-format=json-v1",
    )
    assert res_stale.returncode == 1
    stale_payload = json.loads(res_stale.stdout)
    assert stale_payload["diagnostics"][0]["code"] == "W5005"
    assert target.read_text(encoding="utf-8") == SAMPLE_OOP

    # 4. Type-invalid patch rejected in memory; disk untouched
    res_invalid = _run_compiler(
        "patch", target,
        "--symbol", "ConsoleLogger.log",
        "--expect-graph-hash", current_hash,
        "--with", "public fn log(msg: str) -> int { return 42 }",
        "--apply",
        "--output-format=json-v1",
    )
    assert res_invalid.returncode == 1
    invalid_payload = json.loads(res_invalid.stdout)
    assert invalid_payload["status"] == "diagnostic_error"
    assert invalid_payload["diagnostics"][0]["code"] == "W3003"
    assert target.read_text(encoding="utf-8") == SAMPLE_OOP

    # 5. Dry-run patch checks cleanly without writing disk
    new_log = 'public fn log(msg: str) -> str {\n        return "[" + this.prefix + "] " + msg\n    }'
    res_dry = _run_compiler(
        "patch", target,
        "--symbol", "ConsoleLogger.log",
        "--expect-graph-hash", current_hash,
        "--with", new_log,
        "--output-format=json-v1",
    )
    assert res_dry.returncode == 0, res_dry.stderr
    dry_res = json.loads(res_dry.stdout)["result"]
    assert dry_res["written"] is False
    assert dry_res["graph_hash_before"] == current_hash
    assert dry_res["graph_hash_after"] != current_hash
    assert target.read_text(encoding="utf-8") == SAMPLE_OOP

    # 6. Apply patch writes verified code to disk
    res_apply = _run_compiler(
        "patch", target,
        "--symbol", "ConsoleLogger.log",
        "--expect-graph-hash", current_hash,
        "--with", new_log,
        "--apply",
        "--output-format=json-v1",
    )
    assert res_apply.returncode == 0, res_apply.stderr
    apply_res = json.loads(res_apply.stdout)["result"]
    assert apply_res["written"] is True
    assert '"[" + this.prefix + "] "' in target.read_text(encoding="utf-8")

    # 7. Native explain --code and file capabilities
    res_code = _run_compiler("explain", "--code", "W5005", "--output-format=json-v1")
    assert res_code.returncode == 0, res_code.stderr
    code_payload = json.loads(res_code.stdout)["result"]
    assert code_payload["schema_version"] == "wysna.explain-code/1"
    assert code_payload["code"] == "W5005"

    res_eff = _run_compiler("explain", target, "--output-format=json-v1")
    assert res_eff.returncode == 0, res_eff.stderr
    eff_payload = json.loads(res_eff.stdout)["result"]
    assert "stdio" in eff_payload["program"]["capabilities"]


def test_selfhost_cross_module_graph_and_native_driver_c_passthrough(tmp_path):
    # Cross-module graph
    contracts = tmp_path / "contracts.wysna"
    contracts.write_text("pub interface Store {\n    fn save(key: str) -> int\n}\n", encoding="utf-8")
    app = tmp_path / "main.wysna"
    app.write_text(
        'from "contracts.wysna" use { Store }\n'
        "class DiskStore implements Store {\n"
        "    public fn save(key: str) -> int { return len(key) }\n"
        "}\n"
        "fn main() { let s = DiskStore()\nprint(s.save(\"hi\")) }\n",
        encoding="utf-8",
    )
    res_cross = _run_compiler("graph", app, "--output-format=json-v1")
    assert res_cross.returncode == 0, res_cross.stderr
    cross_nodes = {n["qualified_name"]: n for n in json.loads(res_cross.stdout)["result"]["nodes"]}
    assert "class:DiskStore" in cross_nodes["contracts::Store"]["implemented_by"]

    # Build real driver.c binary in an isolated SDK directory layout (bin/wysna + libexec/wysna-compiler + stdlib)
    sdk_root = tmp_path / "sdk"
    bin_dir = sdk_root / "bin"
    libexec_dir = sdk_root / "libexec"
    bin_dir.mkdir(parents=True)
    libexec_dir.mkdir(parents=True)
    shutil.copy2(_compiler_bin(), libexec_dir / executable_name("wysna-compiler"))
    shutil.copytree(ROOT / "stdlib", sdk_root / "stdlib")

    driver_bin = bin_dir / executable_name("wysna")
    cc = subprocess.run(
        [
            "cc", "-std=c99", "-Wall", "-Wextra", "-Werror", "-Wno-unused-function",
            f'-DWYSNA_NATIVE_VERSION="{WYSNA_VERSION}"',
            '-DWYSNA_NATIVE_TARGET="native-host"',
            f"-I{ROOT}",
            str(ROOT / "packaging/native/driver.c"),
            "-o", str(driver_bin),
        ],
        capture_output=True, text=True, timeout=60,
    )
    assert cc.returncode == 0, cc.stderr

    # Invoke explain, graph, inspect, patch, and check --output-format=json-v1 via native driver.c without Python!
    r_exp = subprocess.run([str(driver_bin), "explain", "--code", "W2001", "--output-format=json-v1"],
                           capture_output=True, text=True, timeout=30)
    assert r_exp.returncode == 0, r_exp.stderr
    assert json.loads(r_exp.stdout)["result"]["code"] == "W2001"

    r_chk = subprocess.run([str(driver_bin), "check", "--output-format=json-v1", str(app)],
                           capture_output=True, text=True, timeout=30)
    assert r_chk.returncode == 0, r_chk.stderr
    assert json.loads(r_chk.stdout)["status"] == "ok"

    r_grp = subprocess.run([str(driver_bin), "graph", str(app), "--output-format=json-v1"],
                           capture_output=True, text=True, timeout=30)
    assert r_grp.returncode == 0, r_grp.stderr
    assert json.loads(r_grp.stdout)["result"]["summary"]["classes"] >= 1

    r_ins = subprocess.run([str(driver_bin), "inspect", str(app), "--symbol", "DiskStore", "--output-format=json-v1"],
                           capture_output=True, text=True, timeout=30)
    assert r_ins.returncode == 0, r_ins.stderr
    assert "implements Store" in json.loads(r_ins.stdout)["result"]["source"]

    r_fmt = subprocess.run([str(driver_bin), "fmt", "--write", "--output-format=json-v1", str(app)],
                           capture_output=True, text=True, timeout=30)
    assert r_fmt.returncode == 0, r_fmt.stderr
    assert json.loads(r_fmt.stdout)["command"] == "fmt"
