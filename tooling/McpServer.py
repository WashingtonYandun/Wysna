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
import io
import json
import os
import sys

from checker.Lints import lint_discarded_expressions
from common.Version import WYSNA_VERSION
from compiler.CompilerService import CompilerService
from error.Error import WysnaError
from formatter.Formatter import Formatter
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
    def __init__(self, root=None):
        self.root = os.path.realpath(root or os.getcwd())
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
            with open(resolved, "r", encoding="utf-8") as handle:
                text = handle.read()
            self.service.set_overlay(resolved, text)
            try:
                yield resolved, text
            finally:
                self.service.remove_overlay(resolved)
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
            warnings = list(lint_discarded_expressions(checked.ast, path))
            warnings.extend(self.service.analyze_reference_cycles(checked))
            sources = {path: source, **self.service.source_texts()}
            cache = {}
            records = [
                diagnostic_from_error(w, sources, severity="warning", span_cache=cache)
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

    def tool_wysna_fix(self, arguments):
        from tooling.FixCLI import fix_source
        apply = bool(arguments.get("apply"))
        if apply and arguments.get("path") is None:
            raise ToolError("'apply' requires 'path'")
        with self._resolve_source(arguments) as (path, source):
            result = fix_source(
                path,
                source,
                include_maybe=bool(arguments.get("include_maybe")),
                service=self.service,
            )
            written = False
            if apply and result["changed"]:
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(result["fixed_source"])
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
    if arguments[:1] == ["--root"] and len(arguments) == 2:
        root = arguments[1]
    elif arguments:
        print("usage: wysna mcp [--root <dir>]", file=sys.stderr)
        return 2
    server = McpServer(root)
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
