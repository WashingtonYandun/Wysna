"""Text-protocol tool-using agent loop and extensible tool registry for bench/agent_scale."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from bench.agent_scale.generate import DEFAULT_REPO_ROOT, build_compiler_command
from bench.agent_scale.metrics import (
    PROMPT_ATTRIBUTION_METHOD,
    CacheCostConfig,
    attribute_prompt_tokens,
    compute_turn_effective_tokens,
)


DEFAULT_MAX_OUTPUT_BYTES = 8192
MAX_TOOL_TURNS_DEFAULT = 30

TOOL_PROTOCOL_DOC = """Tool Call Protocol:
- Emit EXACTLY ONE tool call per message wrapped in a ```tool_call ... ``` fenced JSON object:
  ```tool_call
  {"tool": "<name>", "args": { ... }}
  ```
- Wait for the tool output before calling the next tool.
- Call `{"tool": "done", "args": {"summary": "..."}}` when finished and verified.
- All paths are relative to the workspace root (`.` for root)."""


def resolve_confined_path(root: Path | str, relative_path: str) -> Path:
    """Resolve a path strictly inside ``root`` and reject escapes or external symlinks."""
    if not isinstance(relative_path, str) or not relative_path.strip():
        raise ValueError("Path must be a non-empty relative path")
    candidate_rel = Path(relative_path)
    if candidate_rel.is_absolute():
        raise ValueError(f"Absolute paths are forbidden: {relative_path!r}")
    root_path = Path(root).resolve()
    joined = root_path / candidate_rel
    # Check symlink hops before resolve() as well as final resolved destination.
    cur = root_path
    for part in candidate_rel.parts:
        cur = cur / part
        if cur.is_symlink():
            target = cur.resolve()
            if target != root_path and not target.is_relative_to(root_path):
                raise ValueError(f"Symlink escapes workspace: {relative_path!r}")
    resolved = joined.resolve()
    if resolved != root_path and not resolved.is_relative_to(root_path):
        raise ValueError(f"Resolved path escapes workspace: {relative_path!r}")
    return resolved


def truncate_output(text: str, max_bytes: int = DEFAULT_MAX_OUTPUT_BYTES) -> str:
    """Truncate UTF-8 text safely at ``max_bytes`` with an explicit omitted byte count."""
    raw = text.encode("utf-8")
    if len(raw) <= max_bytes:
        return text
    omitted = len(raw) - max_bytes
    clipped = raw[:max_bytes].decode("utf-8", errors="ignore")
    return f"{clipped}\n[truncated {omitted} bytes]"


ToolHandler = Callable[[Path, Dict[str, Any]], str]


class ToolRegistry:
    """Extensible name -> handler registry enabling A_baseline, B_wysna, and future C_context."""

    def __init__(self) -> None:
        self._tools: Dict[str, Tuple[str, ToolHandler]] = {}

    def register(self, name: str, description: str, handler: ToolHandler) -> None:
        self._tools[name] = (description, handler)

    def names(self) -> List[str]:
        return sorted(self._tools)

    def describe_for_prompt(self) -> str:
        return "\n".join(f"- `{name}`: {self._tools[name][0]}" for name in self.names())

    def execute(self, workspace: Path, name: str, args: Dict[str, Any], max_bytes: int) -> Tuple[str, int, bool]:
        if name not in self._tools:
            out = f"Error: unknown tool '{name}'. Available: {', '.join(self.names())}"
            return truncate_output(out, max_bytes), len(out.encode("utf-8")), False
        _, handler = self._tools[name]
        try:
            raw = handler(workspace, args or {})
        except Exception as exc:  # noqa: BLE001 - surface tool/confinement errors back to agent turn
            raw = f"ToolError: {exc}"
        capped = truncate_output(raw, max_bytes)
        return capped, len(capped.encode("utf-8")), name == "done"


def _tool_list_dir(ws: Path, args: Dict[str, Any]) -> str:
    rel = str(args.get("path", "."))
    target = resolve_confined_path(ws, rel)
    if not target.is_dir():
        return f"Not a directory: {rel}"
    entries = sorted(
        f"{p.name}/" if p.is_dir() else p.name
        for p in target.iterdir()
        if not p.name.startswith(".")
    )
    return "\n".join(entries)


def _tool_read_file(ws: Path, args: Dict[str, Any]) -> str:
    path = resolve_confined_path(ws, str(args.get("path", "")))
    if not path.is_file():
        return f"File not found: {args.get('path')}"
    lines = path.read_text(encoding="utf-8").splitlines()
    start = max(1, int(args.get("start_line", 1)))
    end = min(len(lines), int(args.get("end_line", len(lines))))
    return "\n".join(f"{idx}: {lines[idx - 1]}" for idx in range(start, end + 1))


def _tool_grep(ws: Path, args: Dict[str, Any]) -> str:
    pattern = str(args.get("pattern", ""))
    if not pattern:
        return "Error: pattern must be non-empty"
    scope_rel = str(args.get("path", "src"))
    scope = resolve_confined_path(ws, scope_rel)
    regex = re.compile(pattern)
    files = [scope] if scope.is_file() else sorted(scope.rglob("*.wysna"))
    hits: List[str] = []
    for fpath in files:
        rel = fpath.relative_to(ws.resolve()).as_posix()
        for idx, line in enumerate(fpath.read_text(encoding="utf-8").splitlines(), start=1):
            if regex.search(line):
                hits.append(f"{rel}:{idx}:{line}")
    return "\n".join(hits) if hits else "(no matches)"


def _tool_write_file(ws: Path, args: Dict[str, Any]) -> str:
    path = resolve_confined_path(ws, str(args.get("path", "")))
    content = str(args.get("content", ""))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return f"Wrote {len(content.encode('utf-8'))} bytes to {args.get('path')}"


def _tool_apply_edit(ws: Path, args: Dict[str, Any]) -> str:
    path = resolve_confined_path(ws, str(args.get("path", "")))
    if not path.is_file():
        return f"File not found: {args.get('path')}"
    old_text = str(args.get("old_text", ""))
    new_text = str(args.get("new_text", ""))
    if not old_text:
        return "Error: old_text must be non-empty"
    src = path.read_text(encoding="utf-8")
    count = src.count(old_text)
    if count != 1:
        return f"Error: old_text matched {count} times in {args.get('path')} (expected 1)"
    path.write_text(src.replace(old_text, new_text, 1), encoding="utf-8")
    return f"Edited {args.get('path')}"


def _run_wysna_cli(
    ws: Path,
    cli_args: List[str],
    repo_root: Path,
    wysna_bin: Optional[str],
    timeout_s: int = 120,
) -> str:
    cmd = build_compiler_command(repo_root, wysna_bin) + cli_args
    proc = subprocess.run(cmd, cwd=ws, capture_output=True, text=True, timeout=timeout_s)
    combined = (proc.stdout or "") + (f"\n[stderr]\n{proc.stderr}" if proc.stderr else "")
    return f"exit={proc.returncode}\n{combined.strip()}"


def build_registry(
    condition: str,
    *,
    repo_root: Path = DEFAULT_REPO_ROOT,
    wysna_bin: Optional[str] = None,
) -> ToolRegistry:
    """Create the tool registry for ``A_baseline`` or ``B_wysna`` (extensible for ``C_context``)."""
    reg = ToolRegistry()
    reg.register("list_dir", '{"path": "."}', _tool_list_dir)
    reg.register("read_file", '{"path": "src/file.wysna", "start_line": 1, "end_line": 80}', _tool_read_file)
    reg.register("grep", '{"pattern": "regex", "path": "src"}', _tool_grep)
    reg.register("write_file", '{"path": "src/file.wysna", "content": "..."}', _tool_write_file)
    reg.register("apply_edit", '{"path": "src/file.wysna", "old_text": "...", "new_text": "..."}', _tool_apply_edit)
    reg.register("done", '{"summary": "what changed and check status"}', lambda _ws, args: f"DONE: {args.get('summary', '')}")

    if condition == "A_baseline":
        return reg
    if condition != "B_wysna":
        raise ValueError(f"Unsupported condition {condition!r} (expected 'A_baseline' or 'B_wysna')")

    def tool_check(ws: Path, args: Dict[str, Any]) -> str:
        rel = str(args.get("path", "")).strip()
        if not rel or rel == "wysna.toml":
            manifest = resolve_confined_path(ws, "wysna.toml")
            return _run_wysna_cli(ws, ["project", "check", "--manifest", str(manifest), "--output-format=json-v1"], repo_root, wysna_bin)
        src = resolve_confined_path(ws, rel)
        return _run_wysna_cli(ws, ["check", str(src), "--output-format=json-v1"], repo_root, wysna_bin)

    def tool_graph(ws: Path, args: Dict[str, Any]) -> str:
        cli_args = ["graph"]
        if args.get("source"):
            cli_args.append(str(resolve_confined_path(ws, str(args["source"]))))
        else:
            cli_args.extend(["--manifest", str(resolve_confined_path(ws, "wysna.toml"))])
        if args.get("symbol"):
            cli_args.extend(["--symbol", str(args["symbol"])])
        if args.get("rel"):
            cli_args.extend(["--rel", str(args["rel"])])
        cli_args.append("--output-format=json-v1")
        return _run_wysna_cli(ws, cli_args, repo_root, wysna_bin)

    def tool_inspect(ws: Path, args: Dict[str, Any]) -> str:
        src = resolve_confined_path(ws, str(args.get("source", "src/main.wysna")))
        cli_args = ["inspect", str(src)]
        if args.get("symbol"):
            cli_args.extend(["--symbol", str(args["symbol"])])
        if args.get("symbols"):
            cli_args.extend(["--symbols", str(args["symbols"])])
        if args.get("with_deps"):
            cli_args.append("--with-deps")
        if args.get("max_bytes"):
            cli_args.extend(["--max-bytes", str(int(args["max_bytes"]))])
        cli_args.append("--output-format=json-v1")
        return _run_wysna_cli(ws, cli_args, repo_root, wysna_bin)

    def tool_patch(ws: Path, args: Dict[str, Any]) -> str:
        src = resolve_confined_path(ws, str(args.get("source", "")))
        sym = str(args.get("symbol", ""))
        repl = str(args.get("replacement", ""))
        if not sym or not repl:
            return "Error: wysna_patch requires 'source', 'symbol', and 'replacement'"
        cli_args = ["patch", str(src), "--symbol", sym, "--with", repl, "--apply", "--output-format=json-v1"]
        if args.get("expect_graph_hash"):
            cli_args.extend(["--expect-graph-hash", str(args["expect_graph_hash"])])
        return _run_wysna_cli(ws, cli_args, repo_root, wysna_bin)

    reg.register("wysna_check", '{"path": "wysna.toml or src/file.wysna"}', tool_check)
    reg.register("wysna_graph", '{"source"?: "src/f.wysna", "symbol"?: "name", "rel"?: "impact|callers|callees|usages"}', tool_graph)
    reg.register("wysna_inspect", '{"source": "src/f.wysna", "symbol"?: "fn", "with_deps"?: true, "max_bytes"?: 2048}', tool_inspect)
    reg.register("wysna_patch", '{"source": "src/f.wysna", "symbol": "fn", "replacement": "code", "expect_graph_hash"?: "graph:..."}', tool_patch)
    return reg


def parse_tool_call(response_text: str) -> Tuple[Optional[str], Dict[str, Any], Optional[str]]:
    """Extract a single JSON tool call from ```tool_call ... ``` or plain JSON object."""
    fence = re.search(r"```tool_call\s*\n(.*?)```", response_text, re.DOTALL)
    candidate = fence.group(1).strip() if fence else response_text.strip()
    if not fence and not (candidate.startswith("{") and candidate.endswith("}")):
        return None, {}, "No ```tool_call``` JSON block found."
    try:
        parsed = json.loads(candidate)
    except json.JSONDecodeError as exc:
        return None, {}, f"Invalid tool_call JSON: {exc}"
    if not isinstance(parsed, dict) or not isinstance(parsed.get("tool"), str):
        return None, {}, "tool_call JSON must contain string field 'tool'"
    args = parsed.get("args") or {}
    if not isinstance(args, dict):
        return None, {}, "tool_call 'args' must be a JSON object"
    return parsed["tool"], args, None


@dataclass
class AgentTurnRecord:
    turn_index: int
    tool_name: Optional[str]
    tool_output_bytes: int
    prompt_tokens: int
    completion_tokens: int
    effective_tokens: float
    prompt_breakdown: Dict[str, int]
    latency_ms: float


def run_agent_on_workspace(
    workspace: Path,
    task_id: str,
    instruction: str,
    condition: str,
    provider: Any,
    *,
    max_turns: int = MAX_TOOL_TURNS_DEFAULT,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    repo_root: Path = DEFAULT_REPO_ROOT,
    wysna_bin: Optional[str] = None,
    cache_config: Optional[CacheCostConfig] = None,
) -> Dict[str, Any]:
    """Execute the multi-turn tool loop over ``workspace`` and return raw telemetry."""
    reg = build_registry(condition, repo_root=repo_root, wysna_bin=wysna_bin)
    system_prompt = (
        f"You are an autonomous Wysna software engineer working inside a workspace.\n\n"
        f"{TOOL_PROTOCOL_DOC}\n\n"
        f"Available tools ({condition}):\n{reg.describe_for_prompt()}"
    )
    task_prompt = f"Task ID: {task_id}\nInstruction:\n{instruction}\n\nStart by calling one tool."

    history: List[Dict[str, str]] = []
    current_prompt = task_prompt
    turns: List[AgentTurnRecord] = []
    tool_calls_by_name: Dict[str, int] = {}
    prev_prompt_tokens = 0
    wall_start = time.perf_counter()
    finished_reason = "max_turns"

    for turn_idx in range(max_turns):
        prior_tool_chars = sum(
            len(item["content"]) for idx, item in enumerate(history)
            if item["role"] == "user" and idx > 0
        ) + (len(current_prompt) if turn_idx > 0 else 0)
        prior_assistant_chars = sum(len(item["content"]) for item in history if item["role"] == "assistant")
        char_shares = {
            "system": len(system_prompt),
            "task": len(task_prompt),
            "tool_results": prior_tool_chars,
            "prior_assistant": prior_assistant_chars,
        }

        resp_text, usage, lat_ms = provider.generate(system_prompt, current_prompt, history)
        p_tok = int(usage.get("prompt_tokens", 0))
        c_tok = int(usage.get("completion_tokens", 0))
        eff = compute_turn_effective_tokens(p_tok, c_tok, prev_prompt_tokens, cache_config)
        prev_prompt_tokens = p_tok
        breakdown = attribute_prompt_tokens(p_tok, char_shares)

        tool_name, args, parse_err = parse_tool_call(resp_text)
        if parse_err:
            tool_out = f"Protocol error: {parse_err}"
            out_bytes = len(tool_out.encode("utf-8"))
            is_done = False
        else:
            assert tool_name is not None
            tool_calls_by_name[tool_name] = tool_calls_by_name.get(tool_name, 0) + 1
            tool_out, out_bytes, is_done = reg.execute(workspace, tool_name, args, max_output_bytes)

        turns.append(AgentTurnRecord(
            turn_index=turn_idx,
            tool_name=tool_name,
            tool_output_bytes=out_bytes,
            prompt_tokens=p_tok,
            completion_tokens=c_tok,
            effective_tokens=eff,
            prompt_breakdown=breakdown,
            latency_ms=round(float(lat_ms), 2),
        ))

        if is_done:
            finished_reason = "done"
            break

        history.append({"role": "user", "content": current_prompt})
        history.append({"role": "assistant", "content": resp_text})
        current_prompt = f"Tool result ({tool_name or 'error'}):\n{tool_out}"

    wall_ms = (time.perf_counter() - wall_start) * 1000.0
    total_prompt = sum(t.prompt_tokens for t in turns)
    total_completion = sum(t.completion_tokens for t in turns)
    return {
        "condition": condition,
        "task_id": task_id,
        "turns": len(turns),
        "finished_reason": finished_reason,
        "prompt_tokens": total_prompt,
        "completion_tokens": total_completion,
        "total_tokens": total_prompt + total_completion,
        "effective_tokens": round(sum(t.effective_tokens for t in turns), 2),
        "total_tool_output_bytes": sum(t.tool_output_bytes for t in turns),
        "tool_calls_by_name": dict(sorted(tool_calls_by_name.items())),
        "wall_time_ms": round(wall_ms, 2),
        "prompt_attribution_method": PROMPT_ATTRIBUTION_METHOD,
        "turn_records": [
            {
                "turn_index": t.turn_index,
                "tool_name": t.tool_name,
                "tool_output_bytes": t.tool_output_bytes,
                "prompt_tokens": t.prompt_tokens,
                "completion_tokens": t.completion_tokens,
                "effective_tokens": t.effective_tokens,
                "prompt_breakdown": t.prompt_breakdown,
                "latency_ms": t.latency_ms,
            }
            for t in turns
        ],
    }
