"""Compile-time semantic relationship graph, LSP implementation navigation, and Zerolang-inspired agent tools."""
import json
from pathlib import Path
import subprocess
import sys

from common.Version import WYSNA_VERSION
from tooling.LanguageServer import LanguageServer, position_at
from tooling.McpServer import McpServer


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

class MemoryLogger implements Logger {
    fn log(msg: str) -> str {
        return "[mem] " + msg
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
    let console = new ConsoleLogger("SYS")
    let svc = new Service(console)
    return svc.execute(name)
}

let out = await run_pipeline("boot")
print(out)
"""


def _run_cli(*args):
    result = subprocess.run(
        [sys.executable, str(ROOT / "main.py"), *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    return result.returncode, json.loads(result.stdout) if result.stdout.lstrip().startswith("{") else result.stdout, result.stderr


def _mcp_call(server, name, arguments):
    resp = server.handle({
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })
    result = resp["result"]
    return result["isError"], json.loads(result["content"][0]["text"])


def test_semantic_graph_tracks_interfaces_implementations_usages_and_impact(tmp_path):
    target = tmp_path / "app.wysna"
    target.write_text(SAMPLE_OOP, encoding="utf-8")

    code, payload, stderr = _run_cli("graph", str(target), "--output-format=json-v1")
    assert code == 0, stderr
    assert payload["status"] == "ok"
    graph = payload["result"]
    assert graph["schema_version"] == "wysna.graph/1"
    assert graph["graph_hash"].startswith("graph:")

    nodes = {n["id"]: n for n in graph["nodes"]}
    logger_node = nodes["interface:Logger"]
    assert logger_node["implemented_by"] == ["class:ConsoleLogger", "class:MemoryLogger"]
    assert "class:Service" in logger_node["used_by"]
    assert "method:Service.init" in logger_node["used_by"]

    console_node = nodes["class:ConsoleLogger"]
    assert console_node["implements"] == ["interface:Logger"]
    assert "fn:run_pipeline" in console_node["instantiated_by"]

    pipeline_node = nodes["fn:run_pipeline"]
    assert pipeline_node["is_async"] is True
    assert "class:ConsoleLogger" in pipeline_node["instantiates"]
    assert "class:Service" in pipeline_node["instantiates"]
    assert "method:Service.execute" in pipeline_node["calls"]

    # Query filtered projection around Logger --rel impact (blast radius)
    code, impact_payload, _ = _run_cli(
        "graph", str(target), "--symbol", "Logger", "--rel", "impact", "--output-format=json-v1"
    )
    assert code == 0
    impact = impact_payload["result"]["impact"]
    assert impact["impact_count"] >= 4
    assert "class:ConsoleLogger" in impact["transitive_dependents"]
    assert "class:Service" in impact["transitive_dependents"]
    assert "fn:run_pipeline" in impact["transitive_dependents"]

    # Query --rel implements
    code, impl_payload, _ = _run_cli(
        "graph", str(target), "--symbol", "Logger", "--rel", "implements", "--output-format=json-v1"
    )
    assert code == 0
    impl_edges = impl_payload["result"]["edges"]
    assert {(e["from"], e["to"]) for e in impl_edges} == {
        ("class:ConsoleLogger", "interface:Logger"),
        ("class:MemoryLogger", "interface:Logger"),
    }


def test_cross_module_semantic_graph_and_lsp_implementation(tmp_path):
    iface_file = tmp_path / "contracts.wysna"
    iface_file.write_text(
        "pub interface Store {\n    fn save(key: str) -> int\n}\n",
        encoding="utf-8",
    )
    app_file = tmp_path / "main.wysna"
    app_source = (
        'from "contracts.wysna" use { Store }\n'
        "class DiskStore implements Store {\n"
        "    public fn save(key: str) -> int {\n"
        "        return len(key)\n"
        "    }\n"
        "}\n"
        "fn persist(store: Store, k: str) -> int {\n"
        "    return store.save(k)\n"
        "}\n"
        'print(persist(new DiskStore(), "abc"))\n'
    )
    app_file.write_text(app_source, encoding="utf-8")

    code, payload, stderr = _run_cli("graph", str(app_file), "--output-format=json-v1")
    assert code == 0, stderr
    nodes = {n["qualified_name"]: n for n in payload["result"]["nodes"]}
    store_key = next(k for k in nodes if k.endswith("::Store"))
    assert "class:DiskStore" in nodes[store_key]["implemented_by"]

    # Verify LSP initialize advertises implementationProvider + textDocument/implementation works
    messages = []
    server = LanguageServer(messages.append)
    server.dispatch({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
    caps = messages[-1]["result"]["capabilities"]
    assert caps["implementationProvider"] is True

    app_uri = app_file.as_uri()
    server.update(app_uri, app_source, 1)
    # Hover on DiskStore includes Implemented interface relation
    disk_hover = server.hover(app_uri, position_at(app_source, app_source.index("DiskStore")))
    assert disk_hover is not None
    assert "Implements:" in disk_hover["contents"]["value"]

    # Jump to implementations from contracts.Store parameter annotation
    pos = position_at(app_source, app_source.index("Store, k:"))
    locs = server.implementation(app_uri, pos)
    assert len(locs) == 1
    assert locs[0]["uri"] == app_uri
    start_line = locs[0]["range"]["start"]["line"]
    assert "class DiskStore" in app_source.splitlines()[start_line]


def test_inspect_and_hash_guarded_checked_patch_workflow(tmp_path):
    target = tmp_path / "svc.wysna"
    target.write_text(SAMPLE_OOP, encoding="utf-8")

    # 1. Inspect index (no symbol)
    code, idx_payload, stderr = _run_cli("inspect", str(target), "--output-format=json-v1")
    assert code == 0, stderr
    assert idx_payload["result"]["schema_version"] == "wysna.inspect/1"
    names = {s["name"] for s in idx_payload["result"]["symbols"]}
    assert {"Logger", "ConsoleLogger", "Service", "run_pipeline"} <= names

    # 2. Inspect specific symbol with dependencies (preserves `pub async` prefix)
    code, sym_payload, _ = _run_cli(
        "inspect", str(target), "--symbol", "run_pipeline", "--with-deps", "--output-format=json-v1"
    )
    assert code == 0
    res = sym_payload["result"]
    assert res["source"].startswith("pub async fn run_pipeline")
    assert res["source"].endswith("}")
    dep_ids = {d["id"] for d in res["dependencies"]}
    assert "class:ConsoleLogger" in dep_ids
    assert "class:Service" in dep_ids
    current_hash = res["graph_hash"]

    # 3. Stale graph hash rejects patch with W5005 without touching file
    code, stale_payload, _ = _run_cli(
        "patch", str(target),
        "--symbol", "run_pipeline",
        "--expect-graph-hash", "graph:0000000000000000",
        "--with", "pub async fn run_pipeline(name: str) -> str { return name }",
        "--apply",
        "--output-format=json-v1",
    )
    assert code == 1
    assert stale_payload["diagnostics"][0]["code"] == "W5005"
    assert target.read_text(encoding="utf-8") == SAMPLE_OOP

    # 4. Invalid patch breaking return type is rejected in memory (disk untouched)
    code, invalid_payload, _ = _run_cli(
        "patch", str(target),
        "--symbol", "ConsoleLogger.log",
        "--expect-graph-hash", current_hash,
        "--with", "public fn log(msg: str) -> int { return 42 }",
        "--apply",
        "--output-format=json-v1",
    )
    assert code == 1
    assert invalid_payload["status"] == "diagnostic_error"
    assert target.read_text(encoding="utf-8") == SAMPLE_OOP

    # 5. Valid dry-run checks cleanly without modifying disk
    new_log = 'public fn log(msg: str) -> str {\n        return "[" + this.prefix + "] " + msg\n    }'
    code, dry_payload, _ = _run_cli(
        "patch", str(target),
        "--symbol", "ConsoleLogger.log",
        "--expect-graph-hash", current_hash,
        "--with", new_log,
        "--output-format=json-v1",
    )
    assert code == 0
    assert dry_payload["result"]["written"] is False
    assert dry_payload["result"]["graph_hash_before"] == current_hash
    assert dry_payload["result"]["graph_hash_after"] != current_hash
    assert target.read_text(encoding="utf-8") == SAMPLE_OOP

    # 6. Apply patch writes verified code to disk
    code, apply_payload, _ = _run_cli(
        "patch", str(target),
        "--symbol", "ConsoleLogger.log",
        "--expect-graph-hash", current_hash,
        "--with", new_log,
        "--apply",
        "--output-format=json-v1",
    )
    assert code == 0
    assert apply_payload["result"]["written"] is True
    assert '"[" + this.prefix + "] "' in target.read_text(encoding="utf-8")


def test_skills_explain_code_and_mcp_agent_tools(tmp_path):
    # CLI skills
    code, skills_payload, _ = _run_cli("skills", "agent-loop", "--output-format=json-v1")
    assert code == 0
    assert skills_payload["result"]["compiler_version"] == WYSNA_VERSION
    assert "wysna graph" in skills_payload["result"]["content"]
    assert "wysna patch" in skills_payload["result"]["content"]

    # CLI explain --code
    code, explain_payload, _ = _run_cli("explain", "--code", "W5005", "--output-format=json-v1")
    assert code == 0
    assert explain_payload["result"]["code"] == "W5005"
    assert "graph-hash" in explain_payload["result"]["bad_example"]

    # MCP server integration
    file_path = tmp_path / "mod.wysna"
    file_path.write_text("fn double(x: int) -> int { return x * 2 }\nprint(double(3))\n", encoding="utf-8")
    server = McpServer(tmp_path)

    failed, graph_res = _mcp_call(server, "wysna_graph", {"path": "mod.wysna"})
    assert not failed
    graph_hash = graph_res["result"]["graph_hash"]

    failed, insp_res = _mcp_call(server, "wysna_inspect", {"path": "mod.wysna", "symbol": "double"})
    assert not failed
    assert "return x * 2" in insp_res["result"]["source"]

    failed, patch_res = _mcp_call(
        server, "wysna_patch",
        {
            "path": "mod.wysna",
            "symbol": "double",
            "replacement": "fn double(x: int) -> int { return x + x }",
            "expect_graph_hash": graph_hash,
            "apply": True,
        },
    )
    assert not failed
    assert patch_res["result"]["written"] is True
    assert "x + x" in file_path.read_text(encoding="utf-8")

    # MCP path confinement holds on wysna_patch
    failed, bad_path = _mcp_call(
        server, "wysna_patch",
        {"path": "../escape.wysna", "symbol": "x", "replacement": "fn x() {}"},
    )
    assert failed and "outside the server root" in bad_path["error"]

    failed, skill_mcp = _mcp_call(server, "wysna_skills", {"topic": "graph"})
    assert not failed
    assert "blast radius" in skill_mcp["result"]["content"]


def test_project_impact_reports_cli_browser_service_and_test_paths():
    manifest = "examples/shared-browser-app/wysna.toml"
    symbol = "quote_validation_code"
    code, envelope, stderr = _run_cli(
        "graph", "--manifest", manifest, "--symbol", symbol, "--rel", "impact", "--output-format=json-v1",
    )
    assert code == 0, stderr
    impact = envelope["result"]
    assert impact["schema_version"] == "wysna.project-impact/1"
    entries = {entry["name"]: entry for entry in impact["entries"]}
    assert set(entries) == {"app", "service", "web", "shared-pricing-contract"}
    assert all(entry["affected"] for entry in entries.values())
    assert entries["service"]["unanalyzed_boundaries"] == ["c-ffi"]
    assert entries["web"]["unanalyzed_boundaries"] == ["javascript"]
    assert entries["app"]["paths"][0]["chain"][-1]["to"].endswith("::" + symbol)
    assert any(path["chain"] == [] for path in entries["web"]["paths"])

    failed, response = _mcp_call(
        McpServer(ROOT), "wysna_graph", {"manifest": manifest, "symbol": symbol, "relation": "impact"},
    )
    assert not failed, response
    assert response["result"] == impact

    failed, _ = _mcp_call(
        McpServer(ROOT / "examples"), "wysna_graph", {"manifest": "../wysna.toml", "symbol": symbol, "relation": "impact"},
    )
    assert failed
