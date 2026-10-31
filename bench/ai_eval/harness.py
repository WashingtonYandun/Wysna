"""Evaluation Harness for Wysna AI Authoring and Repair Benchmark (SPEC-002).

Manages isolated execution workspaces, compiler static checks, hidden oracle
verification, and multi-turn diagnostic feedback repair loops (up to 3 repairs).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
import time
from typing import Any, Dict, List, Optional, Tuple

from bench.ai_eval.models import (
    AttemptRecord,
    EvaluationSummary,
    Task,
    TaskEvaluationResult,
    TaskTestCase,
)
from bench.ai_eval.providers import LlmProvider


SYSTEM_PROMPT = """You are an expert programmer writing software in Wysna v0.5.0, a statically-typed systems programming language with value semantics and copy-on-write collections.

Key Wysna Grammar and Rules:
1. Entrypoint: `fn main() -> unit { ... }`
2. Functions: `fn name(param: type) -> ret_type { ... }` (public functions use `pub fn`)
3. Variables:
   - `let x = 10` (immutable)
   - `let mut y = 20` (mutable)
4. Types: `int`, `float`, `str`, `bool`, `unit`, `list[T]`, `dict[K, V]`, `tuple[T1, T2]`, `T?` (nullable), `T ! E` (error union)
5. Control Flow:
   - `if cond { ... } else { ... }`
   - `for item in list_expr { ... }` (Note: for loops require a list; to iterate a dict use `for k, v in dict_items(d)` or `for k in d.keys()`)
   - `for i in 0..limit { ... }` (exclusive) or `1..=limit` (inclusive)
   - `while cond { ... }`
6. Standard Prelude Functions:
   - `print(...)`: space-separated print with newline
   - `len(x)`: code-point count for `str` or element count for collections
   - `dict_items(d)`: returns `list[tuple[K, V]]` of `(key, value)` pairs for dictionary iteration
   - `args()`: returns `list[str]` of command-line arguments. In `wysna run file.wysna -- a b`, `args()[0]` is runner/script, arguments start at `args()[1]`.
   - `read_file(path)`: returns `str ! IoError`
   - `write_file(path, content)`: returns `unit ! IoError`
   - `str(x)`, `int(x)`, `float(x)`: explicit casts
   - `chr(c)`: converts integer code point to single-character string (e.g. `chr(65)` -> `"A"`)
   - `ord(s)`: returns Unicode integer code point of first character
   - `slice(x, start, end)`: substring or sublist slice
   - `join_strings(parts: list[str], separator: str) -> str`
   - `bytes(s: str) -> list[int]`: returns unsigned byte values 0..=255
7. Collections:
   - `list[T]`: `.append(item)`, `.pop()`, `.clear()`
   - `dict[K, V]`: `.get(key)` (returns `V?`), `.keys()`, `.values()`
8. Error Handling:
   - `let content = read_file(path)?` (postfix propagation)
   - `let content = read_file(path) else err { return "fallback" }`
   - `let host = env.get("HOST") ?? "localhost"` (coalescing)
9. Structs:
   - `struct Name { field: type }`
   - Instantiation requires named fields: `Name { field: value }`
10. Imports and Standard Library:
   - `import "other_module.wysna" as alias` or selective `from "other_module.wysna" use { symbol }`
   - `from "std/iter" use { map, filter, fold, any, all }`
   - `from "std/text" use { starts_with, ends_with, trim, to_lower, to_upper, split, contains }`
11. Comments:
   - Line comments begin with `#` and documentation comments begin with `##`. Never use `//`.

Always output valid Wysna code enclosed in a ```wysna ... ``` code block.
If multiple files are required, prefix each file with `### filename.wysna`.
"""


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
            target_path = os.path.join(workspace_dir, fname)
            if not os.path.isfile(target_path):
                continue
            try:
                with open(target_path, "r", encoding="utf-8") as handle:
                    src = handle.read()
                fixed = fix_source(target_path, src)
                if fixed.get("changed"):
                    with open(target_path, "w", encoding="utf-8") as handle:
                        handle.write(fixed["fixed_source"])
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

            # Check file assertions if any
            for rel_path, expected_content in tc.file_assertions.items():
                full_path = os.path.join(workspace_dir, rel_path)
                if not os.path.exists(full_path):
                    return False, f"Test '{tc.name}' expected output file '{rel_path}' was not created."
                with open(full_path, "r", encoding="utf-8") as f:
                    actual_file_content = f.read()
                if actual_file_content.strip() != expected_content.strip():
                    return (
                        False,
                        f"Test '{tc.name}' file content mismatch in '{rel_path}': expected '{expected_content.strip()}', got '{actual_file_content.strip()}'",
                    )

        return True, None

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

        total_latency_ms = 0.0
        total_tokens = 0
        attempts: List[AttemptRecord] = []
        pass_at_attempt: Optional[int] = None
        final_error: Optional[str] = None

        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. Setup fixtures in workspace
            for rel_path, content in task.fixtures.items():
                dest = os.path.join(tmpdir, rel_path)
                os.makedirs(os.path.dirname(dest), exist_ok=True)
                with open(dest, "w", encoding="utf-8") as f:
                    f.write(content)

            conversation_history: List[Dict[str, str]] = []
            user_prompt = (
                f"Task ID: {task.id}\n"
                f"Title: {task.title}\n"
                f"Instructions:\n{task.instruction}\n"
            )
            if task.fixtures:
                user_prompt += "\nExisting Workspace Files:\n"
                for fname, fcontent in task.fixtures.items():
                    user_prompt += f"### {fname}\n```wysna\n{fcontent}\n```\n"

            for cycle in range(self.max_repair_cycles + 1):
                completion, tokens, lat_ms = provider.generate(
                    system_prompt=SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    conversation_history=conversation_history,
                )
                total_latency_ms += lat_ms
                total_tokens += tokens.get("prompt_tokens", 0) + tokens.get("completion_tokens", 0)

                extracted_files = self.extract_code_blocks(completion)
                if not extracted_files:
                    extracted_files = {task.entrypoint: completion}

                # Write files to workspace
                for fname, fcontent in extracted_files.items():
                    target_path = os.path.join(tmpdir, fname)
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    with open(target_path, "w", encoding="utf-8") as f:
                        f.write(fcontent)

                entrypoint_code = extracted_files.get(task.entrypoint, completion)

                # Run static typecheck (with zero-turn compiler auto-fix if machine fixes exist)
                check_passed, diagnostics, raw_stderr = self.run_check(tmpdir, task.entrypoint)
                if not check_passed and any(
                    fix.get("applicability") == "machine"
                    for d in diagnostics
                    for fix in d.get("fixes", ())
                ):
                    if self.run_auto_fix(tmpdir, extracted_files):
                        entrypoint_code = extracted_files.get(task.entrypoint, entrypoint_code)
                        check_passed, diagnostics, raw_stderr = self.run_check(tmpdir, task.entrypoint)

                record = AttemptRecord(
                    attempt_index=cycle,
                    code=entrypoint_code,
                    check_passed=check_passed,
                    check_diagnostics=diagnostics,
                    raw_stderr=raw_stderr,
                    latency_ms=lat_ms,
                    prompt_tokens=tokens.get("prompt_tokens", 0),
                    completion_tokens=tokens.get("completion_tokens", 0),
                )

                if not check_passed:
                    err_cat = self.classify_error(raw_stderr, diagnostics)
                    record.error_category = err_cat
                    final_error = err_cat
                    attempts.append(record)

                    if cycle < self.max_repair_cycles:
                        # Prepare repair prompt
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

                if oracle_passed:
                    record.error_category = "none"
                    pass_at_attempt = cycle + 1
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

            status = "pass" if pass_at_attempt is not None else "fail"
            return TaskEvaluationResult(
                task_id=task.id,
                domain=task.domain,
                title=task.title,
                status=status,
                pass_at_attempt=pass_at_attempt,
                attempts=attempts,
                total_latency_ms=total_latency_ms,
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
    ) -> EvaluationSummary:
        """Runs the benchmark over the given tasks collection."""
        selected_tasks = tasks
        if task_filter:
            selected_tasks = [t for t in tasks if t.id == task_filter or task_filter in t.domain]

        results: List[TaskEvaluationResult] = []
        error_dist: Dict[str, int] = {}
        pass_at_1 = 0
        pass_at_3 = 0
        supported_count = 0
        blocked_count = 0
        total_lat = 0.0
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
            res = self.evaluate_task(task, provider)
            results.append(res)

            if res.status == "blocked":
                blocked_count += 1
            else:
                supported_count += 1
                total_lat += res.total_latency_ms
                total_tok += res.total_tokens

                if res.pass_at_attempt == 1:
                    pass_at_1 += 1
                if res.pass_at_attempt is not None and res.pass_at_attempt <= 4:
                    pass_at_3 += 1

                if res.final_error_category:
                    error_dist[res.final_error_category] = error_dist.get(res.final_error_category, 0) + 1

        denom = supported_count if supported_count > 0 else 1
        return EvaluationSummary(
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime()),
            compiler_version="0.5.0",
            compiler_commit=git_commit,
            provider_name=provider_name,
            model_name=model_name,
            total_tasks=len(selected_tasks),
            supported_tasks=supported_count,
            blocked_tasks=blocked_count,
            pass_at_1_count=pass_at_1,
            pass_at_3_count=pass_at_3,
            pass_at_1_rate=pass_at_1 / denom,
            pass_at_3_rate=pass_at_3 / denom,
            error_distribution=error_dist,
            avg_latency_ms=total_lat / denom if denom > 0 else 0.0,
            total_tokens=total_tok,
            results=results,
        )
