"""Evaluation Harness for Wysna AI Authoring and Repair Benchmark (SPEC-002).

Manages isolated execution workspaces, compiler static checks, hidden oracle
verification, and multi-turn diagnostic feedback repair loops (up to 3 repairs).
"""

from __future__ import annotations

import json
import ntpath
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Callable, Dict, List, Optional, Tuple

from bench.ai_eval.models import (
    SCHEMA_VERSION,
    AttemptRecord,
    EvaluationSummary,
    Task,
    TaskEvaluationResult,
    TaskTestCase,
)
from bench.ai_eval.providers import LlmProvider
from common.Version import WYSNA_VERSION


SYSTEM_PROMPT = (
    f"You are an expert programmer writing software in Wysna v{WYSNA_VERSION}, "
    "a statically-typed systems programming language with value semantics and "
    "copy-on-write collections."
    """

Key Wysna Grammar and Rules:
1. Entrypoint: `fn main() -> unit { ... }`
2. Functions: `fn name(param: type) -> ret_type { ... }` (public functions use `pub fn`)
3. Variables:
   - `let x = 10` (immutable)
   - `let mut y = 20` (mutable)
4. Types: `int`, `float`, `str`, `bool`, `unit`, `list[T]`, `dict[K, V]`, `tuple[T1, T2]`, `T?` (nullable), `T ! E` (error union)
5. Control Flow:
   - `if cond { ... } else { ... }`
   - `for item in list_expr { ... }` (for dicts iterate with `for k, v in d.items()` or `for k in d.keys()`)
   - `for i in 0..limit { ... }` (exclusive) or `1..=limit` (inclusive)
   - `while cond { ... }`
6. Standard Prelude Functions:
   - `print(...)`: space-separated print with newline
   - `len(x)`: code-point count for `str` or element count for collections
   - `dict_items(d)` / `d.items()`: returns `list[tuple[K, V]]` of `(key, value)` pairs
   - `args()`: returns `list[str]` of CLI arguments (`args()[0]` is runner/script, flags/args start at `args()[1]`)
   - `read_file(path) -> str ! IoError`, `write_file(path, content) -> unit ! IoError`
   - `str(x)`, `int(x)`, `float(x)`, `chr(c)`, `ord(s)`, `slice(x, start, end)`, `join_strings(parts, sep)`, `bytes(s)`
7. Collections & Strings:
   - `str`: `.trim()`, `.split(sep)`, `.to_lower()`, `.to_upper()`, `.starts_with(p)`, `.ends_with(s)`, `.replace(old, new)`, `.contains(sub)`, `.is_empty()`
   - `list[T]`: `.append(item)`, `.pop()`, `.clear()`, `.contains(item)`, `.is_empty()`
   - `dict[K, V]`: `.get(key)` (returns `V?`), `.keys()`, `.values()`, `.items()`, `.contains(key)`, `.remove(key)`, `.clear()`, `.is_empty()`
8. Error Handling:
   - `let content = read_file(path)?` (postfix propagation)
   - `let content = read_file(path) else err { return "fallback" }`
   - `let host = env("HOST") ?? "localhost"` (coalescing)
9. Structs & Classes:
   - `struct Name { field: type }` initialized as `Name { field: value }`
   - Classes instantiate without `new`: `MyClass(arg1)` (or module-qualified `mod.MyClass(arg1)`)
10. Standard Library & Imports:
   - `import "std/io" as io`: `read_line() -> Line ! IoError`, `read_all() -> str ! IoError`, `flush() -> unit ! IoError`
   - `import "std/fs" as fs`: `exists`, `is_dir`, `is_file`, `list_dir`, `mkdir_all`, `copy`, `rename`, `remove_file`
   - `import "std/path" as path`: `join`, `basename`, `dirname`, `ext`
   - `import "std/csv" as csv`: `parse_rows(text) -> list[list[str]] ! CsvError`, `parse_records(text) -> list[dict[str, str]] ! CsvError`, `stringify(rows)`
   - `import "std/process" as proc`: `run(exe, argv, timeout_ms)`, `capture(exe, argv, timeout_ms, max_output_bytes) -> ProcessOutput ! ProcessError`
   - `import "std/json" as json`, `import "std/http" as http`, `from "std/algorithms" use { map, filter, fold, any, all, sort }`
11. Comments:
   - Line comments begin with `#` and documentation comments begin with `##`. Never use `//`.

Always output valid Wysna code enclosed in a ```wysna ... ``` code block.
If multiple files are required, prefix each file with `### filename.wysna`.
"""
)


class EvaluationHarness:
    """Executes the AI evaluation benchmark against a compiler instance."""

    def __init__(
        self,
        repo_root: Optional[str] = None,
        compiler_cmd: Optional[List[str]] = None,
        max_repair_cycles: int = 3,
        timeout_seconds: float = 15.0,
    ):
        self.repo_root = repo_root or os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..")
        )
        self.compiler_cmd = compiler_cmd or [sys.executable, os.path.join(self.repo_root, "main.py")]
        self.max_repair_cycles = max_repair_cycles
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _resolve_confined_path(workspace_dir: str, relative_path: str) -> Path:
        """Resolve a workspace-relative path and reject any directory traversal or symlink escape."""
        if not relative_path or "\x00" in relative_path:
            raise ValueError(f"Invalid empty or NUL path: {relative_path!r}")
        normalized = relative_path.replace("\\", "/")
        drive, _ = ntpath.splitdrive(normalized)
        if drive or normalized.startswith("/"):
            raise ValueError(f"Absolute path escapes workspace: {relative_path!r}")
        rel_obj = Path(normalized)
        if rel_obj.is_absolute() or ".." in rel_obj.parts:
            raise ValueError(f"Relative path escapes workspace: {relative_path!r}")
        root_resolved = Path(workspace_dir).resolve()
        candidate = (root_resolved / rel_obj).resolve()
        if candidate == root_resolved or not candidate.is_relative_to(root_resolved):
            raise ValueError(f"Resolved path escapes workspace: {relative_path!r}")
        return candidate

    def extract_code_blocks(self, text: str) -> Dict[str, str]:
        """Extracts file contents from markdown code blocks or file markers."""
        files: Dict[str, str] = {}

        # Check for multi-file header markers: `### filename.wysna\n```wysna ... ```
        pattern = re.compile(r"###\s+([\w\.\-_/]+\.wysna)\s*```(?:wysna|w)?\n(.*?)```", re.DOTALL)
        matches = pattern.findall(text)
        if matches:
            for filename, content in matches:
                files[filename.strip()] = content
            return files

        # Fallback: extract single ```wysna code block
        single_pattern = re.compile(r"```(?:wysna|w)?\n(.*?)```", re.DOTALL)
        single_matches = single_pattern.findall(text)
        if single_matches:
            return {"main.wysna": single_matches[0]}

        # If no markdown fences found, treat raw text as main.wysna if it contains fn main
        if "fn main" in text:
            return {"main.wysna": text}

        return {}

    def classify_error(self, stderr: str, diagnostics: List[Dict[str, Any]]) -> str:
        """Classifies compiler or test failure into a standard failure taxonomy."""
        combined = stderr.lower() + " ".join(d.get("message", "").lower() for d in diagnostics)

        if "syntaxerror" in combined or any(d.get("code", "").startswith("W0") for d in diagnostics):
            return "syntax"
        if "type mismatch" in combined or "typeerror" in combined or any(d.get("code", "").startswith("W2") for d in diagnostics):
            return "type"
        if "circular import" in combined or any(d.get("code", "") == "W1003" for d in diagnostics):
            return "circular_dependency"
        if any(tok in combined for tok in ["no module named", "undefined identifier", "unresolved", "not found in module"]):
            return "api_hallucination"
        if "traceback" in stderr.lower():
            return "runtime_panic"
        return "oracle_mismatch"

    def run_check(self, workspace_dir: str, entrypoint: str) -> Tuple[bool, List[Dict[str, Any]], str]:
        """Runs `wysna check --output-format=json-v1 <entrypoint>` in the isolated workspace."""
        cmd = self.compiler_cmd + ["check", "--output-format=json-v1", entrypoint]
        try:
            env = os.environ.copy()
            env["PYTHONPATH"] = self.repo_root
            proc = subprocess.run(
                cmd,
                cwd=workspace_dir,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
                env=env,
            )
            raw_stdout = proc.stdout.strip()
            raw_stderr = proc.stderr.strip()

            diagnostics = []
            if raw_stdout:
                try:
                    parsed = json.loads(raw_stdout)
                    if isinstance(parsed, dict):
                        if parsed.get("status") == "ok":
                            return True, [], raw_stderr
                        if "diagnostics" in parsed:
                            diagnostics = parsed["diagnostics"]
                        else:
                            diagnostics = [parsed]
                    elif isinstance(parsed, list):
                        diagnostics = parsed
                except json.JSONDecodeError:
                    pass

            check_passed = proc.returncode == 0
            return check_passed, diagnostics, raw_stderr
        except subprocess.TimeoutExpired:
            return False, [{"message": "Typecheck timed out"}], "TIMEOUT"
        except Exception as e:
            return False, [{"message": str(e)}], str(e)

    def run_auto_fix(self, workspace_dir: str, files: Dict[str, str]) -> bool:
        """Applies deterministic compiler machine fixes (`wysna fix --apply`) in place."""
        from tooling.FixCLI import fix_source
        changed_any = False
        for fname in list(files.keys()):
            try:
                target_path = self._resolve_confined_path(workspace_dir, fname)
            except ValueError:
                continue
            if not target_path.is_file():
                continue
            try:
                src = target_path.read_text(encoding="utf-8")
                fixed = fix_source(str(target_path), src)
                if fixed.get("changed"):
                    target_path.write_text(fixed["fixed_source"], encoding="utf-8")
                    files[fname] = fixed["fixed_source"]
                    changed_any = True
            except Exception:
                continue
        return changed_any

    @staticmethod
    def format_diagnostic_for_prompt(diag: Dict[str, Any]) -> str:
        """Formats a `wysna.tool-result/1` diagnostic with structured data and hints."""
        code = diag.get("code", "ERR")
        msg = diag.get("message", "")
        data = diag.get("data") or {}
        line_info = f"line {diag['line']}" if "line" in diag else ""
        details = []
        if "expected" in data and "found" in data:
            details.append(f"expected={data['expected']}, found={data['found']}")
        if "conversion_hint" in data:
            details.append(f"hint: wrap with {data['conversion_hint']}")
        if "unwrap_hint" in data:
            details.append(f"hint: unwrap with '{data['unwrap_hint']}'")
        if "help" in data:
            details.append(f"help: {data['help']}")
        for rel in diag.get("related", ()):
            rel_line = rel.get("line", "?")
            details.append(f"related (line {rel_line}): {rel.get('message', '')}")
        loc_suffix = f" ({line_info})" if line_info else ""
        detail_suffix = f" [{' | '.join(details)}]" if details else ""
        return f"- [{code}] {msg}{loc_suffix}{detail_suffix}"

    def run_oracle_tests(
        self,
        workspace_dir: str,
        entrypoint: str,
        test_cases: List[TaskTestCase],
    ) -> Tuple[bool, Optional[str]]:
        """Executes test cases against `wysna run <entrypoint> -- <args>`."""
        env = os.environ.copy()
        env["PYTHONPATH"] = self.repo_root

        for tc in test_cases:
            cmd = self.compiler_cmd + ["run", entrypoint]
            if tc.args:
                cmd += ["--"] + tc.args

            # Each case and repair starts from the same declared persistence state.
            try:
                for name, content in tc.preserved_files.items():
                    path = self._resolve_confined_path(workspace_dir, name)
                    path.unlink(missing_ok=True)
                    if content is not None:
                        path.parent.mkdir(parents=True, exist_ok=True)
                        with path.open("xb") as fixture:
                            fixture.write(content.encode("utf-8"))
            except (OSError, ValueError) as error:
                return False, f"Test '{tc.name}' could not prepare its fixture: {error}"

            try:
                proc = subprocess.run(
                    cmd,
                    cwd=workspace_dir,
                    input=tc.stdin,
                    capture_output=True,
                    text=True,
                    timeout=self.timeout_seconds,
                    env=env,
                )
            except subprocess.TimeoutExpired:
                return False, f"Test '{tc.name}' timed out after {self.timeout_seconds}s"
            except Exception as e:
                return False, f"Test '{tc.name}' execution failed: {e}"

            for name, content in tc.preserved_files.items():
                try:
                    path = self._resolve_confined_path(workspace_dir, name)
                    if content is None:
                        unchanged = not os.path.lexists(Path(workspace_dir) / name.replace("\\", "/"))
                    elif path.is_file():
                        expected = content.encode("utf-8")
                        with path.open("rb") as observed:
                            unchanged = observed.read(len(expected) + 1) == expected
                    else:
                        unchanged = False
                except (OSError, ValueError):
                    unchanged = False
                if not unchanged:
                    return False, f"Persistence invariant violated: test '{tc.name}' changed '{name}'"

            # Check exit code
            if proc.returncode != tc.expected_exit:
                return (
                    False,
                    f"Test '{tc.name}' exit code mismatch: expected {tc.expected_exit}, got {proc.returncode}. Stderr: {proc.stderr}",
                )

            # Check stdout (normalized strip)
            actual_stdout = proc.stdout.replace("\r\n", "\n")
            expected_stdout = tc.expected_stdout.replace("\r\n", "\n")
            if actual_stdout.strip() != expected_stdout.strip():
                return (
                    False,
                    f"Test '{tc.name}' stdout mismatch:\n--- Expected:\n{expected_stdout.strip()}\n--- Actual:\n{actual_stdout.strip()}",
                )

            if tc.expected_stderr is not None and proc.stderr != tc.expected_stderr:
                return False, f"Test '{tc.name}' stderr mismatch: expected {tc.expected_stderr!r}, got {proc.stderr!r}"

            # Check file assertions if any
            for rel_path, expected_content in tc.file_assertions.items():
                try:
                    full_path = self._resolve_confined_path(workspace_dir, rel_path)
                except ValueError:
                    return False, f"Test '{tc.name}' output path '{rel_path}' escapes workspace."
                if not full_path.exists():
                    return False, f"Test '{tc.name}' expected output file '{rel_path}' was not created."
                actual_file_content = full_path.read_text(encoding="utf-8")
                if actual_file_content.strip() != expected_content.strip():
                    return (
                        False,
                        f"Test '{tc.name}' file content mismatch in '{rel_path}': expected '{expected_content.strip()}', got '{actual_file_content.strip()}'",
                    )

        return True, None

    def generate_completion(self, provider, prompt, history, workspace):
        return provider.generate(SYSTEM_PROMPT, prompt, history)

    def workspace_context(self, fixtures):
        return "".join(f"### {name}\n```wysna\n{content}\n```\n" for name, content in fixtures.items())

    def evaluate_task(self, task: Task, provider: LlmProvider) -> TaskEvaluationResult:
        """Evaluates a single task with initial attempt and up to max_repair_cycles."""
        if task.status == "blocked":
            return TaskEvaluationResult(
                task_id=task.id,
                domain=task.domain,
                title=task.title,
                status="blocked",
                blocked_reason=task.blocked_reason,
            )

        task_wall_start = time.perf_counter()
        total_provider_latency_ms = 0.0
        total_tokens = 0
        attempts: List[AttemptRecord] = []
        pass_at_attempt: Optional[int] = None
        final_error: Optional[str] = None

        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. Setup fixtures in workspace
            for rel_path, content in task.fixtures.items():
                dest = self._resolve_confined_path(tmpdir, rel_path)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_text(content, encoding="utf-8")

            conversation_history: List[Dict[str, str]] = []
            user_prompt = (
                f"Task ID: {task.id}\n"
                f"Title: {task.title}\n"
                f"Instructions:\n{task.instruction}\n"
            )
            if task.fixtures:
                user_prompt += "\nExisting Workspace Files:\n"
                user_prompt += self.workspace_context(task.fixtures)

            for cycle in range(self.max_repair_cycles + 1):
                attempt_wall_start = time.perf_counter()
                completion, tokens, lat_ms = self.generate_completion(provider, user_prompt, conversation_history, tmpdir)
                total_provider_latency_ms += lat_ms
                prompt_tok = tokens.get("prompt_tokens", 0)
                comp_tok = tokens.get("completion_tokens", 0)
                total_tokens += prompt_tok + comp_tok

                extracted_files = self.extract_code_blocks(completion)
                if not extracted_files:
                    extracted_files = {task.entrypoint: completion}

                entrypoint_code = extracted_files.get(task.entrypoint, completion)
                try:
                    resolved_targets = [
                        (self._resolve_confined_path(tmpdir, fname), fcontent)
                        for fname, fcontent in extracted_files.items()
                    ]
                except ValueError as path_err:
                    err_msg = str(path_err)
                    record = AttemptRecord(
                        attempt_index=cycle,
                        code=entrypoint_code,
                        check_passed=False,
                        raw_check_passed=False,
                        post_autofix_check_passed=False,
                        autofix_applied=False,
                        check_diagnostics=[{"code": "W5002", "message": err_msg}],
                        raw_stderr=err_msg,
                        error_category="unsafe_path",
                        latency_ms=lat_ms,
                        provider_latency_ms=lat_ms,
                        wall_time_ms=(time.perf_counter() - attempt_wall_start) * 1000.0,
                        prompt_tokens=prompt_tok,
                        completion_tokens=comp_tok,
                    )
                    final_error = "unsafe_path"
                    attempts.append(record)
                    if cycle < self.max_repair_cycles:
                        conversation_history.append({"role": "user", "content": user_prompt})
                        conversation_history.append({"role": "assistant", "content": completion})
                        user_prompt = (
                            f"[Task: {task.id}] Rejected unsafe output path outside workspace:\n"
                            f"{err_msg}\n\n"
                            f"Output relative workspace paths only in ```wysna code blocks."
                        )
                    continue

                # Write confined files to workspace
                for target_path, fcontent in resolved_targets:
                    target_path.parent.mkdir(parents=True, exist_ok=True)
                    target_path.write_text(fcontent, encoding="utf-8")

                # Run raw static typecheck before any compiler autofix, then post-autofix check
                raw_check_passed, raw_diagnostics, raw_stderr = self.run_check(tmpdir, task.entrypoint)
                post_autofix_check_passed = raw_check_passed
                diagnostics = raw_diagnostics
                autofix_applied = False
                if not raw_check_passed and any(
                    fix.get("applicability") == "machine"
                    for d in raw_diagnostics
                    for fix in d.get("fixes", ())
                ):
                    if self.run_auto_fix(tmpdir, extracted_files):
                        autofix_applied = True
                        entrypoint_code = extracted_files.get(task.entrypoint, entrypoint_code)
                        post_autofix_check_passed, diagnostics, raw_stderr = self.run_check(tmpdir, task.entrypoint)

                check_passed = post_autofix_check_passed
                record = AttemptRecord(
                    attempt_index=cycle,
                    code=entrypoint_code,
                    check_passed=check_passed,
                    raw_check_passed=raw_check_passed,
                    post_autofix_check_passed=post_autofix_check_passed,
                    autofix_applied=autofix_applied,
                    check_diagnostics=diagnostics,
                    raw_stderr=raw_stderr,
                    latency_ms=lat_ms,
                    provider_latency_ms=lat_ms,
                    prompt_tokens=prompt_tok,
                    completion_tokens=comp_tok,
                )

                if not check_passed:
                    err_cat = self.classify_error(raw_stderr, diagnostics)
                    record.error_category = err_cat
                    record.wall_time_ms = (time.perf_counter() - attempt_wall_start) * 1000.0
                    final_error = err_cat
                    attempts.append(record)

                    if cycle < self.max_repair_cycles:
                        diag_summary = "\n".join(
                            self.format_diagnostic_for_prompt(d)
                            for d in diagnostics
                        )
                        if not diag_summary:
                            diag_summary = raw_stderr

                        conversation_history.append({"role": "user", "content": user_prompt})
                        conversation_history.append({"role": "assistant", "content": completion})
                        user_prompt = (
                            f"[Task: {task.id}] The Wysna compiler rejected your program with the following diagnostics:\n"
                            f"{diag_summary}\n\n"
                            f"Please correct the error and output the complete revised Wysna code in a ```wysna code block."
                        )
                    continue

                # Check passed -> run hidden oracle tests
                record.run_passed = True
                oracle_passed, oracle_failure = self.run_oracle_tests(
                    tmpdir, task.entrypoint, task.test_cases
                )
                record.oracle_passed = oracle_passed
                record.oracle_failure_details = oracle_failure
                record.wall_time_ms = (time.perf_counter() - attempt_wall_start) * 1000.0

                if oracle_passed:
                    record.error_category = "none"
                    pass_at_attempt = cycle + 1
                    final_error = None
                    attempts.append(record)
                    break
                else:
                    err_cat = "oracle_mismatch"
                    record.error_category = err_cat
                    final_error = err_cat
                    attempts.append(record)

                    if cycle < self.max_repair_cycles:
                        conversation_history.append({"role": "user", "content": user_prompt})
                        conversation_history.append({"role": "assistant", "content": completion})
                        user_prompt = (
                            f"[Task: {task.id}] Your code compiled, but failed functional oracle tests:\n"
                            f"{oracle_failure}\n\n"
                            f"Please fix the logic and output the revised code in a ```wysna code block."
                        )

            task_wall_ms = (time.perf_counter() - task_wall_start) * 1000.0
            first_attempt = attempts[0] if attempts else None
            raw_first_check = bool(first_attempt and first_attempt.raw_check_passed)
            post_autofix_first_check = bool(first_attempt and first_attempt.post_autofix_check_passed)
            first_autofix = bool(first_attempt and first_attempt.autofix_applied)
            first_oracle = bool(first_attempt and first_attempt.oracle_passed)
            raw_pass_at_1 = bool(
                pass_at_attempt == 1
                and raw_first_check
                and not first_autofix
                and first_oracle
            )
            status = "pass" if pass_at_attempt is not None else "fail"
            return TaskEvaluationResult(
                task_id=task.id,
                domain=task.domain,
                title=task.title,
                status=status,
                pass_at_attempt=pass_at_attempt,
                raw_first_check_passed=raw_first_check,
                post_autofix_first_check_passed=post_autofix_first_check,
                first_attempt_autofix_applied=first_autofix,
                first_attempt_oracle_passed=first_oracle,
                raw_pass_at_1=raw_pass_at_1,
                model_turns=len(attempts),
                attempts=attempts,
                total_latency_ms=total_provider_latency_ms,
                provider_latency_ms=total_provider_latency_ms,
                wall_time_ms=task_wall_ms,
                total_tokens=total_tokens,
                final_error_category=final_error,
            )

    def run_benchmark(
        self,
        tasks: List[Task],
        provider: LlmProvider,
        provider_name: str = "generic",
        model_name: str = "unknown",
        task_filter: Optional[str] = None,
        completed_results: Optional[List[TaskEvaluationResult]] = None,
        on_task_complete: Optional[Callable[[TaskEvaluationResult, List[TaskEvaluationResult]], None]] = None,
    ) -> EvaluationSummary:
        """Runs the benchmark over the given tasks collection."""
        selected_tasks = tasks
        if task_filter:
            selected_tasks = [t for t in tasks if t.id == task_filter or task_filter in t.domain]

        results: List[TaskEvaluationResult] = list(completed_results or [])
        completed_task_ids = {result.task_id for result in results}
        error_dist: Dict[str, int] = {}
        raw_first_check_pass_count = 0
        post_autofix_first_check_pass_count = 0
        raw_pass_at_1 = 0
        autofix_pass_at_1 = 0
        pass_at_1 = 0
        pass_at_3 = 0
        resolved_count = 0
        supported_count = 0
        blocked_count = 0
        total_provider_lat = 0.0
        total_wall_ms = 0.0
        total_turns = 0
        total_tok = 0

        # Retrieve compiler git commit
        git_commit = "unknown"
        try:
            p = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=self.repo_root, capture_output=True, text=True)
            if p.returncode == 0:
                git_commit = p.stdout.strip()
        except Exception:
            pass

        for task in selected_tasks:
            if task.id in completed_task_ids:
                continue
            res = self.evaluate_task(task, provider)
            results.append(res)
            completed_task_ids.add(task.id)
            if on_task_complete is not None:
                on_task_complete(res, results)

        for res in results:
            if res.status == "blocked":
                blocked_count += 1
            else:
                supported_count += 1
                total_provider_lat += res.provider_latency_ms
                total_wall_ms += res.wall_time_ms
                total_turns += res.model_turns
                total_tok += res.total_tokens

                if res.raw_first_check_passed:
                    raw_first_check_pass_count += 1
                if res.post_autofix_first_check_passed:
                    post_autofix_first_check_pass_count += 1
                if res.raw_pass_at_1:
                    raw_pass_at_1 += 1
                if res.pass_at_attempt == 1:
                    pass_at_1 += 1
                    if res.first_attempt_autofix_applied:
                        autofix_pass_at_1 += 1
                if res.pass_at_attempt is not None and res.pass_at_attempt <= 3:
                    pass_at_3 += 1
                if res.status == "pass":
                    resolved_count += 1

                if res.final_error_category:
                    error_dist[res.final_error_category] = error_dist.get(res.final_error_category, 0) + 1

        denom = supported_count if supported_count > 0 else 1
        max_attempts = self.max_repair_cycles + 1
        denominators = {
            "total_tasks": len(selected_tasks),
            "supported_tasks": supported_count,
            "blocked_tasks": blocked_count,
            "pass_at_1_max_attempts": 1,
            "pass_at_3_max_attempts": min(3, max_attempts),
            "budget_max_attempts": max_attempts,
        }
        return EvaluationSummary(
            schema_version=SCHEMA_VERSION,
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            compiler_version=WYSNA_VERSION,
            compiler_commit=git_commit,
            provider_name=provider_name,
            model_name=model_name,
            token_counting_method=getattr(provider, "token_counting_method", "unknown"),
            max_repair_cycles=self.max_repair_cycles,
            max_attempts=max_attempts,
            total_tasks=len(selected_tasks),
            supported_tasks=supported_count,
            blocked_tasks=blocked_count,
            denominators=denominators,
            raw_first_check_pass_count=raw_first_check_pass_count,
            raw_first_check_pass_rate=raw_first_check_pass_count / denom,
            post_autofix_first_check_pass_count=post_autofix_first_check_pass_count,
            post_autofix_first_check_pass_rate=post_autofix_first_check_pass_count / denom,
            raw_pass_at_1_count=raw_pass_at_1,
            raw_pass_at_1_rate=raw_pass_at_1 / denom,
            autofix_pass_at_1_count=autofix_pass_at_1,
            autofix_pass_at_1_rate=autofix_pass_at_1 / denom,
            pass_at_1_count=pass_at_1,
            pass_at_3_count=pass_at_3,
            pass_at_1_rate=pass_at_1 / denom,
            pass_at_3_rate=pass_at_3 / denom,
            resolved_count=resolved_count,
            resolved_rate=resolved_count / denom,
            error_distribution=error_dist,
            avg_latency_ms=total_provider_lat / denom,
            avg_provider_latency_ms=total_provider_lat / denom,
            avg_wall_time_ms=total_wall_ms / denom,
            total_provider_latency_ms=total_provider_lat,
            total_wall_time_ms=total_wall_ms,
            avg_model_turns=total_turns / denom,
            total_model_turns=total_turns,
            total_tokens=total_tok,
            results=results,
        )
