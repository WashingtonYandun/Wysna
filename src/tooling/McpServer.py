"""Model Context Protocol server exposing Wysna's static tools to agents.

Transport: newline-delimited JSON-RPC 2.0 over stdio. The server is a long-lived
process holding a persistent in-memory ``CompilerService`` session, so an agent
pays interpreter start-up once and avoids temporary file I/O on inline checks.

Only static, side-effect-free tools are exposed: check, fix, fmt, api, explain,
schema and capabilities. There is deliberately no ``run`` tool: Wysna has no
OS-level sandbox yet, so executing agent-written programs is not offered through
this channel. File access is confined to ``--root`` (default: the current
directory); ``fix`` only writes when ``apply`` is true and the target is inside
that root.
"""

import contextlib
import hashlib
import io
import json
import os
import sys

from checker.Lints import lint_discarded_expressions
from common.Version import WYSNA_VERSION
from compiler.CompilerService import CompilerService
from error.Error import WysnaError
from formatter.Formatter import Formatter
from tooling.SourceWrite import read_source_snapshot, write_verified_source
from tooling.ToolResult import diagnostic_from_error, tool_result

PROTOCOL_VERSION = "2025-06-18"
_SOURCE_OR_PATH = {
    "path": {"type": "string", "description": "Path to a .wysna file inside the server root."},
    "source": {"type": "string", "description": "Inline Wysna source text (alternative to path)."},
}

TOOLS = [
    {"name": "wysna_check",
     "description": "Type-check Wysna code. Returns every independent error with byte spans, "
                    "expected/found facts and candidate fixes (wysna.tool-result/1).",
     "inputSchema": {"type": "object", "properties": dict(_SOURCE_OR_PATH)}},
    {"name": "wysna_fix",
     "description": "Compute (and optionally apply) machine-applicable fixes for diagnostics. "
                    "Returns the corrected source and the diagnostics that remain.",
     "inputSchema": {"type": "object", "properties": {
         **_SOURCE_OR_PATH,
         "apply": {"type": "boolean", "description": "Write the fix to 'path' (default false)."},
         "include_maybe": {"type": "boolean", "description": "Also use heuristic 'maybe' fixes."}}}},
    {"name": "wysna_fmt",
     "description": "Return the canonical formatting of Wysna code.",
     "inputSchema": {"type": "object", "properties": dict(_SOURCE_OR_PATH)}},
    {"name": "wysna_explain",
     "description": "Static effect and capability report for a program.",
     "inputSchema": {"type": "object", "properties": dict(_SOURCE_OR_PATH)}},
    {"name": "wysna_schema",
     "description": "Emit JSON Schema (Draft 2020-12) for structs/enums and LLM tool definitions "
                    "with static effect/idempotency metadata for functions.",
     "inputSchema": {"type": "object", "properties": {
         **_SOURCE_OR_PATH,
         "all": {"type": "boolean", "description": "Include private top-level functions."},
         "function": {"type": "string", "description": "Emit tool schema for a single function."}}}},
    {"name": "wysna_api",
     "description": "Discover standard-library modules and public symbols.",
     "inputSchema": {"type": "object", "properties": {
         "module": {"type": "string", "description": "Optional module such as std/json."}}}},
    {"name": "wysna_capabilities",
     "description": "Installed commands, targets and their capability contracts.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "wysna_graph",
     "description": "Compile-time semantic relationship graph (interfaces, classes, usages, calls, blast radius).",
     "inputSchema": {"type": "object", "properties": {
         **_SOURCE_OR_PATH,
         "manifest": {"type": "string", "description": "Project manifest for an impact or architecture role query."},
         "role": {"type": "string", "description": "Configured architecture role to list within a project target."},
         "target": {"type": "string", "description": "Optional project target for the role query."},
         "symbol": {"type": "string", "description": "Optional symbol name or node ID filter."},
         "relation": {"type": "string", "description": "Optional relation filter: implements|hierarchy|usages|callers|callees|impact."}}}},
    {"name": "wysna_inspect",
     "description": "Surgical, token-efficient symbol source slice (single or batch), contract, effects, call resolution, and dependency signatures.",
     "inputSchema": {"type": "object", "properties": {
         **_SOURCE_OR_PATH,
         "symbol": {"type": "string", "description": "Symbol name to extract (omit for compact index)."},
         "symbols": {"type": "array", "items": {"type": "string"}, "description": "Batch list of symbols to extract under one shared max_bytes budget."},
         "with_deps": {"type": "boolean", "description": "Include signatures of referenced types and callees."},
         "max_bytes": {"type": "integer", "minimum": 1, "description": "Maximum UTF-8 byte budget for returned source/context."},
         "source_offset": {"type": "integer", "minimum": 0, "description": "UTF-8 byte offset within target symbol source."},
         "offset": {"type": "integer", "minimum": 0, "description": "Starting index offset when listing symbols."}}}},
    {"name": "wysna_patch",
     "description": "Checked declaration-level or multi-file workspace patch (source, symbol, hunks, create, delete) guarded by graph/plan hash and in-memory typecheck.",
     "inputSchema": {"type": "object", "properties": {
         "path": {"type": "string", "description": "Path to a .wysna file inside the server root (single-symbol mode)."},
         "symbol": {"type": "string", "description": "Target declaration name or Class.method (single-symbol mode)."},
         "replacement": {"type": "string", "description": "Replacement declaration Wysna code (single-symbol mode)."},
         "expect_graph_hash": {"type": "string", "description": "Optional optimistic lock graph hash (single-symbol mode)."},
         "manifest": {"type": "string", "description": "Path to wysna.toml inside server root (workspace mode)."},
         "edits": {"type": "array", "description": "Array of workspace edit objects ({path, source|symbol+replacement|hunks|create+source|delete}) checked and applied together.",
                   "items": {"type": "object"}},
         "expect_plan_hash": {"type": "string", "description": "Plan hash from preview (required with apply=true in workspace mode)."},
         "discover": {"type": "boolean", "description": "Discover test files under tests/ (workspace mode)."},
         "frozen": {"type": "boolean", "description": "Verify wysna.lock before planning/applying (workspace mode)."},
         "apply": {"type": "boolean", "description": "Write verified patch to disk (default false)."}}}},
    {"name": "wysna_project_check",
     "description": "Static project-wide typecheck across wysna.toml targets and lockfile verification.",
     "inputSchema": {"type": "object", "properties": {
         "manifest": {"type": "string", "description": "Path to wysna.toml inside server root."},
         "target": {"type": "string", "description": "Optional target name from wysna.toml."},
         "frozen": {"type": "boolean", "description": "Verify wysna.lock before checking."}},
         "required": ["manifest"]}},
    {"name": "wysna_project_test",
     "description": "List test plans deterministically (list=true) or execute project tests when MCP server was started with --allow-exec.",
     "inputSchema": {"type": "object", "properties": {
         "manifest": {"type": "string", "description": "Path to wysna.toml inside server root."},
         "list": {"type": "boolean", "description": "Return static test plan and plan_hash without executing code."},
         "discover": {"type": "boolean", "description": "Discover test files under tests/."},
         "frozen": {"type": "boolean", "description": "Verify wysna.lock before listing or running tests."},
         "filter": {"type": "string", "description": "Substring filter on test ID/name."},
         "case": {"type": "string", "description": "Run only the specified test case ID."},
         "expect_plan_hash": {"type": "string", "description": "Verify test selection against a previously listed plan_hash."}},
         "required": ["manifest"]}},
    {"name": "wysna_skills",
     "description": "Version-matched concise coding and workflow guides embedded in Wysna.",
     "inputSchema": {"type": "object", "properties": {
         "topic": {"type": "string", "description": "overview, agent-loop, syntax, graph, patch, project-tests, contracts, stdlib."}}}},
]


class ToolError(Exception):
    """A tool failure reported to the agent as an in-band error result."""


def _run_cli(argv):
    """Run the in-process CLI, returning (exit_code, stdout, stderr)."""
    from tooling.CLI import main
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            main(list(argv))
        except SystemExit as stop:
            code = stop.code if isinstance(stop.code, int) else (0 if stop.code is None else 1)
    return code, out.getvalue(), err.getvalue()


class McpServer:
    def __init__(self, root=None, allow_exec=False):
        self.root = os.path.realpath(root or os.getcwd())
        self.allow_exec = bool(allow_exec)
        self.service = CompilerService(project_root=self.root)
        self._inline_path = os.path.join(self.root, "__mcp_inline__.wysna")

    # -- file confinement & in-memory source resolution -------------------
    def _confine(self, path):
        resolved = os.path.realpath(os.path.join(self.root, path))
        if os.path.commonpath([self.root, resolved]) != self.root:
            raise ToolError(f"path is outside the server root: {path}")
        return resolved

    @contextlib.contextmanager
    def _resolve_source(self, arguments):
        path, source = arguments.get("path"), arguments.get("source")
        if (path is None) == (source is None):
            raise ToolError("provide exactly one of 'path' or 'source'")
        if path is not None:
            resolved = self._confine(path)
            _, text, _ = read_source_snapshot(resolved)
            self.service.set_overlay(str(resolved), text)
            try:
                yield str(resolved), text
            finally:
                self.service.remove_overlay(str(resolved))
            return
        self.service.set_overlay(self._inline_path, source)
        try:
            yield self._inline_path, source
        finally:
            self.service.remove_overlay(self._inline_path)

    def _diagnostics_envelope(self, command, error, path, source):
        sources = {path: source, os.path.abspath(path): source, os.path.realpath(path): source}
        sources.update(self.service.source_texts())
        cache = {}
        items = getattr(error, "diagnostics", None) or [error]
        records = [diagnostic_from_error(item, sources, span_cache=cache) for item in items]
        return tool_result(command, "diagnostic_error", 1, diagnostics=records)

    # -- tools ------------------------------------------------------------
    def _json_cli(self, argv):
        code, out, err = _run_cli(argv)
        try:
            return json.loads(out)
        except ValueError:
            raise ToolError(f"tool produced no JSON (exit {code}): {(err or out)[:500]}")

    def tool_wysna_check(self, arguments):
        with self._resolve_source(arguments) as (path, source):
            try:
                parsed = self.service.parse(source, path, recover=True)
                checked = self.service.check_parsed(parsed)
            except WysnaError as error:
                return self._diagnostics_envelope("check", error, path, source)
            warnings = self.service.lint_warnings(checked, path)
            sources = {path: source, **self.service.source_texts()}
            cache = {}
            records = [
                diagnostic_from_error(w, sources, span_cache=cache)
                for w in warnings
            ]
            return tool_result("check", "ok", 0, diagnostics=records)

    def tool_wysna_fmt(self, arguments):
        with self._resolve_source(arguments) as (path, source):
            try:
                parsed = self.service.parse(source, path)
                formatted = Formatter().format(parsed._program)
            except WysnaError as error:
                return self._diagnostics_envelope("fmt", error, path, source)
            envelope = tool_result("fmt", "ok", 0)
            envelope["result"] = {
                "path": path,
                "changed": formatted != source,
                "written": False,
                "formatted_source": formatted,
            }
            return envelope

    def tool_wysna_explain(self, arguments):
        with self._resolve_source(arguments) as (path, source):
            try:
                checked = self.service.check(source, path)
                report = self.service.explain(checked)
            except WysnaError as error:
                return self._diagnostics_envelope("explain", error, path, source)
            envelope = tool_result("explain", "ok", 0)
            envelope["result"] = report
            return envelope

    def tool_wysna_schema(self, arguments):
        from tooling.SchemaCLI import generate_schemas
        with self._resolve_source(arguments) as (path, source):
            try:
                payload = generate_schemas(
                    path,
                    source,
                    include_all=bool(arguments.get("all")),
                    function_name=arguments.get("function"),
                    emit_codecs=bool(arguments.get("emit_codecs")),
                    service=self.service,
                )
            except WysnaError as error:
                return self._diagnostics_envelope("schema", error, path, source)
            except ValueError as error:
                raise ToolError(str(error)) from error
            envelope = tool_result("schema", "ok", 0)
            envelope["result"] = payload
            return envelope

    def tool_wysna_api(self, arguments):
        argv = ["api"] + ([arguments["module"]] if arguments.get("module") else [])
        return self._json_cli(argv + ["--output-format=json-v1"])

    def tool_wysna_capabilities(self, arguments):
        return self._json_cli(["capabilities", "--output-format=json-v1"])

    def tool_wysna_graph(self, arguments):
        if "role" in arguments:
            role, manifest, target = (arguments.get(key) for key in ("role", "manifest", "target"))
            if (not isinstance(role, str) or not role or not isinstance(manifest, str)
                    or (target is not None and (not isinstance(target, str) or not target))
                    or any(key in arguments for key in ("path", "source", "symbol", "relation"))):
                raise ToolError("provide manifest, role and optionally target for a role query")
            argv = ["graph", "--manifest", self._confine(manifest), "--role", role, "--output-format=json-v1"]
            if target:
                argv.extend(["--target", target])
            return self._run_project_subcommand(argv)
        if "manifest" in arguments:
            manifest = arguments["manifest"]
            symbol = arguments.get("symbol")
            if (not isinstance(manifest, str) or not isinstance(symbol, str) or not symbol
                    or "path" in arguments or "source" in arguments or arguments.get("relation") != "impact"):
                raise ToolError("provide manifest, symbol and relation=impact for a project query")
            from tooling.ProjectImpact import project_impact
            try:
                payload = project_impact(self._confine(manifest), symbol)
            except (WysnaError, ValueError) as error:
                raise ToolError(str(error)) from error
            envelope = tool_result("graph", "ok", 0)
            envelope["result"] = payload
            return envelope
        with self._resolve_source(arguments) as (path, source):
            try:
                checked = self.service.check(source, path)
                payload = self.service.graph(
                    checked,
                    symbol=arguments.get("symbol"),
                    relation=arguments.get("relation"),
                )
            except WysnaError as error:
                return self._diagnostics_envelope("graph", error, path, source)
            if arguments.get("symbol") and payload.get("found") is False:
                err = WysnaError(
                    f"Symbol '{arguments['symbol']}' not found in semantic graph",
                    file=path, code="W1001", stage="graph",
                )
                return self._diagnostics_envelope("graph", err, path, source)
            envelope = tool_result("graph", "ok", 0)
            envelope["result"] = payload
            return envelope

    def tool_wysna_inspect(self, arguments):
        from tooling.AgentGraphCLI import inspect_source
        with self._resolve_source(arguments) as (path, source):
            try:
                payload = inspect_source(
                    path,
                    symbol=arguments.get("symbol"),
                    symbols=arguments.get("symbols"),
                    with_deps=bool(arguments.get("with_deps")),
                    max_bytes=arguments.get("max_bytes"),
                    source_offset=arguments.get("source_offset", 0),
                    offset=arguments.get("offset", 0),
                    service=self.service,
                )
            except WysnaError as error:
                return self._diagnostics_envelope("inspect", error, path, source)
            except ValueError as error:
                raise ToolError(str(error)) from error
            envelope = tool_result("inspect", "ok", 0)
            envelope["result"] = payload
            return envelope

    def _run_project_subcommand(self, argv):
        code, stdout, stderr = _run_cli(argv)
        if not stdout.strip():
            raise ToolError((stderr or f"project command produced no JSON (exit {code})").strip())
        try:
            return json.loads(stdout)
        except ValueError as error:
            raise ToolError(f"invalid JSON from project command: {error}") from error

    def tool_wysna_project_check(self, arguments):
        manifest_raw = arguments.get("manifest")
        if not isinstance(manifest_raw, str) or not manifest_raw.strip():
            raise ToolError("'wysna_project_check' requires 'manifest'")
        confined = self._confine(manifest_raw)
        argv = ["project", "check", "--engine=seed", "--manifest", confined, "--output-format=json-v1"]
        target = arguments.get("target")
        if isinstance(target, str) and target.strip():
            argv.extend(["--target", target])
        if bool(arguments.get("frozen")):
            argv.append("--frozen")
        return self._run_project_subcommand(argv)

    def tool_wysna_project_test(self, arguments):
        manifest_raw = arguments.get("manifest")
        if not isinstance(manifest_raw, str) or not manifest_raw.strip():
            raise ToolError("'wysna_project_test' requires 'manifest'")
        confined = self._confine(manifest_raw)
        list_only = bool(arguments.get("list"))
        if not list_only and not self.allow_exec:
            from tooling.ToolResult import diagnostic_record
            return tool_result(
                "project.test",
                "usage_error",
                2,
                diagnostics=[diagnostic_record(
                    "W5002",
                    "usage",
                    "wysna_project_test execution requires starting MCP server with --allow-exec (pass list=true for static test discovery)",
                )],
            )
        argv = ["project", "test", "--engine=seed", "--manifest", confined, "--output-format=json-v1"]
        if list_only:
            argv.append("--list")
        if bool(arguments.get("discover")):
            argv.append("--discover")
        if bool(arguments.get("frozen")):
            argv.append("--frozen")
        for flag, key in (("--filter", "filter"), ("--case", "case"), ("--expect-plan-hash", "expect_plan_hash")):
            val = arguments.get(key)
            if isinstance(val, str) and val.strip():
                argv.extend([flag, val])
        return self._run_project_subcommand(argv)

    def tool_wysna_patch(self, arguments):
        if "edits" in arguments or "manifest" in arguments:
            from pathlib import Path
            from tooling.WorkspacePatch import plan_workspace_patch

            manifest_raw = arguments.get("manifest")
            edits = arguments.get("edits")
            if not isinstance(manifest_raw, str) or not manifest_raw or edits is None:
                raise ToolError("workspace 'wysna_patch' requires 'manifest' and 'edits'")
            if any(arguments.get(k) is not None for k in ("path", "symbol", "replacement", "expect_graph_hash")):
                raise ToolError("workspace 'wysna_patch' cannot combine single-symbol options with 'manifest'/'edits'")
            manifest_path = Path(self._confine(manifest_raw))
            try:
                payload = plan_workspace_patch(
                    manifest_path,
                    edits,
                    expected_hash=arguments.get("expect_plan_hash"),
                    apply=bool(arguments.get("apply")),
                    discover=bool(arguments.get("discover")),
                    frozen=bool(arguments.get("frozen")),
                )
            except WysnaError as error:
                items = getattr(error, "diagnostics", None) or [error]
                sources = dict(self.service.source_texts())
                sources.update(getattr(error, "source_texts", {}))
                root_dir = manifest_path.parent
                if isinstance(edits, list):
                    for edit in edits:
                        if isinstance(edit, dict) and isinstance(edit.get("path"), str) and isinstance(edit.get("source"), str):
                            sources.setdefault(str((root_dir / edit["path"]).resolve()), edit["source"])
                for item in items:
                    ifile = getattr(item, "file", None)
                    if ifile and ifile not in sources and os.path.isfile(ifile):
                        try:
                            with open(ifile, "r", encoding="utf-8") as fh:
                                sources[ifile] = fh.read()
                        except OSError:
                            pass
                records = [diagnostic_from_error(item, sources) for item in items]
                return tool_result("patch", "diagnostic_error", 1, diagnostics=records)
            except ValueError as error:
                raise ToolError(str(error)) from error
            envelope = tool_result("patch", "ok", 0)
            envelope["result"] = payload
            return envelope

        from tooling.AgentGraphCLI import patch_source
        raw_path = arguments.get("path")
        symbol = arguments.get("symbol")
        replacement = arguments.get("replacement")
        if not raw_path or not symbol or not replacement:
            raise ToolError("'wysna_patch' requires 'path', 'symbol', and 'replacement'")
        resolved = self._confine(raw_path)
        _, source, _ = read_source_snapshot(resolved)
        try:
            payload = patch_source(
                resolved,
                symbol,
                replacement,
                expect_graph_hash=arguments.get("expect_graph_hash"),
                apply=bool(arguments.get("apply")),
                service=self.service,
            )
        except WysnaError as error:
            return self._diagnostics_envelope("patch", error, resolved, source)
        except ValueError as error:
            raise ToolError(str(error)) from error
        result_body = {k: v for k, v in payload.items() if v is not None}
        envelope = tool_result("patch", "ok", 0)
        envelope["result"] = result_body
        return envelope

    def tool_wysna_skills(self, arguments):
        from tooling.AgentGraphCLI import get_skill_topic
        try:
            payload = get_skill_topic(arguments.get("topic") or "overview")
        except ValueError as error:
            raise ToolError(str(error)) from error
        envelope = tool_result("skills", "ok", 0)
        envelope["result"] = payload
        return envelope

    def tool_wysna_fix(self, arguments):
        from tooling.FixCLI import fix_source
        apply = bool(arguments.get("apply"))
        if apply and arguments.get("path") is None:
            raise ToolError("'apply' requires 'path'")
        with self._resolve_source(arguments) as (path, source):
            before_sha256 = hashlib.sha256(source.encode("utf-8")).hexdigest()
            result = fix_source(
                path,
                source,
                include_maybe=bool(arguments.get("include_maybe")),
                service=self.service,
            )
            written = False
            if apply and result["changed"]:
                try:
                    write_verified_source(
                        path,
                        result["fixed_source"],
                        expected_sha256=before_sha256,
                        stage="fix",
                    )
                except WysnaError as error:
                    return self._diagnostics_envelope("fix", error, path, source)
                written = True
            result["written"] = written
            return result

    # -- JSON-RPC ---------------------------------------------------------
    def handle(self, message):
        """Return the JSON-RPC response for ``message``, or None for notifications."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return self._error(None, -32600, "invalid request")
        method, request_id = message.get("method"), message.get("id")
        params = message.get("params") or {}
        if request_id is None:
            return None  # notification such as notifications/initialized
        if method == "initialize":
            return self._result(request_id, {
                "protocolVersion": params.get("protocolVersion", PROTOCOL_VERSION),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "wysna", "version": WYSNA_VERSION},
            })
        if method == "ping":
            return self._result(request_id, {})
        if method == "tools/list":
            return self._result(request_id, {"tools": TOOLS})
        if method == "tools/call":
            return self._call(request_id, params)
        return self._error(request_id, -32601, f"method not found: {method}")

    def _call(self, request_id, params):
        name = params.get("name")
        handler = getattr(self, "tool_" + str(name), None) if name in {t["name"] for t in TOOLS} else None
        if handler is None:
            return self._error(request_id, -32602, f"unknown tool: {name}")
        arguments = params.get("arguments") or {}
        if not isinstance(arguments, dict):
            return self._error(request_id, -32602, "arguments must be an object")
        try:
            payload, failed = handler(arguments), False
        except ToolError as error:
            payload, failed = {"error": str(error)}, True
        except (OSError, UnicodeDecodeError) as error:
            payload, failed = {"error": f"{type(error).__name__}: {error}"}, True
        text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        return self._result(request_id, {"content": [{"type": "text", "text": text}], "isError": failed})

    @staticmethod
    def _result(request_id, result):
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    @staticmethod
    def _error(request_id, code, message):
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def serve_mcp(argv=()):
    arguments = list(argv)
    root = None
    allow_exec = False
    i = 0
    while i < len(arguments):
        arg = arguments[i]
        if arg == "--root" and i + 1 < len(arguments) and root is None:
            root = arguments[i + 1]
            i += 2
        elif arg == "--allow-exec":
            allow_exec = True
            i += 1
        else:
            print("usage: wysna mcp [--root <dir>] [--allow-exec]", file=sys.stderr)
            return 2
    server = McpServer(root, allow_exec=allow_exec)
    protocol_out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            response = server.handle(json.loads(line))
        except ValueError:
            response = server._error(None, -32700, "parse error")
        if response is not None:
            protocol_out.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
            protocol_out.flush()
    return 0
