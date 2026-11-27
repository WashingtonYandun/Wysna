"""Data models for Wysna AI Authoring and Repair Evaluation (SPEC-002).

Defines structured models for tasks, test cases, repair attempts,
and aggregated benchmark evaluation results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class TaskTestCase:
    name: str
    args: List[str] = field(default_factory=list)
    stdin: str = ""
    expected_stdout: str = ""
    expected_exit: int = 0
    file_assertions: Dict[str, str] = field(default_factory=dict)
    expected_stderr: Optional[str] = None
    # UTF-8 fixture contents to seed and preserve exactly, or None for a missing file.
    preserved_files: Dict[str, Optional[str]] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "args": self.args,
            "stdin": self.stdin,
            "expected_stdout": self.expected_stdout,
            "expected_exit": self.expected_exit,
            "file_assertions": self.file_assertions,
            "expected_stderr": self.expected_stderr,
            "preserved_files": self.preserved_files,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskTestCase:
        return cls(
            name=data["name"],
            args=data.get("args", []),
            stdin=data.get("stdin", ""),
            expected_stdout=data.get("expected_stdout", ""),
            expected_exit=data.get("expected_exit", 0),
            file_assertions=data.get("file_assertions", {}),
            expected_stderr=data.get("expected_stderr"),
            preserved_files=data.get("preserved_files", {}),
        )


@dataclass
class Task:
    id: str
    domain: str  # cli_scripting | data_files | multi_module | system_sqlite
    title: str
    status: str  # supported | blocked
    instruction: str
    entrypoint: str = "main.wysna"
    blocked_reason: Optional[str] = None
    fixtures: Dict[str, str] = field(default_factory=dict)
    test_cases: List[TaskTestCase] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "domain": self.domain,
            "title": self.title,
            "status": self.status,
            "blocked_reason": self.blocked_reason,
            "instruction": self.instruction,
            "entrypoint": self.entrypoint,
            "fixtures": self.fixtures,
            "test_cases": [tc.to_dict() for tc in self.test_cases],
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> Task:
        return cls(
            id=data["id"],
            domain=data["domain"],
            title=data["title"],
            status=data.get("status", "supported"),
            blocked_reason=data.get("blocked_reason"),
            instruction=data["instruction"],
            entrypoint=data.get("entrypoint", "main.wysna"),
            fixtures=data.get("fixtures", {}),
            test_cases=[TaskTestCase.from_dict(tc) for tc in data.get("test_cases", [])],
        )


SCHEMA_VERSION = "wysna.ai-eval/2"


@dataclass
class AttemptRecord:
    attempt_index: int
    code: str
    check_passed: bool
    check_diagnostics: List[Dict[str, Any]] = field(default_factory=list)
    raw_stderr: str = ""
    run_passed: bool = False
    oracle_passed: bool = False
    oracle_failure_details: Optional[str] = None
    error_category: Optional[str] = None  # syntax | type | api_hallucination | runtime | oracle_mismatch | unsafe_path | none
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    raw_check_passed: bool = False
    post_autofix_check_passed: bool = False
    autofix_applied: bool = False
    provider_latency_ms: float = 0.0
    wall_time_ms: float = 0.0
    response: str = ""
    files: Dict[str, str] = field(default_factory=dict)
    # Estimated split of provider-reported prompt tokens by source (see harness.attribute_prompt_tokens).
    prompt_breakdown: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "attempt_index": self.attempt_index,
            "code": self.code,
            "check_passed": self.check_passed,
            "raw_check_passed": self.raw_check_passed,
            "post_autofix_check_passed": self.post_autofix_check_passed,
            "autofix_applied": self.autofix_applied,
            "check_diagnostics": self.check_diagnostics,
            "raw_stderr": self.raw_stderr,
            "run_passed": self.run_passed,
            "oracle_passed": self.oracle_passed,
            "oracle_failure_details": self.oracle_failure_details,
            "error_category": self.error_category,
            "latency_ms": round(self.latency_ms, 2),
            "provider_latency_ms": round(self.provider_latency_ms, 2),
            "wall_time_ms": round(self.wall_time_ms, 2),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "response": self.response,
            "files": self.files,
            "prompt_breakdown": self.prompt_breakdown,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> AttemptRecord:
        return cls(**{name: data[name] for name in cls.__dataclass_fields__ if name in data})


@dataclass
class TaskEvaluationResult:
    task_id: str
    domain: str
    title: str
    status: str  # pass | fail | blocked
    blocked_reason: Optional[str] = None
    pass_at_attempt: Optional[int] = None  # 1-based model submission index that passed oracle, or None
    attempts: List[AttemptRecord] = field(default_factory=list)
    total_latency_ms: float = 0.0
    total_tokens: int = 0
    final_error_category: Optional[str] = None
    raw_first_check_passed: bool = False
    post_autofix_first_check_passed: bool = False
    first_attempt_autofix_applied: bool = False
    first_attempt_oracle_passed: bool = False
    raw_pass_at_1: bool = False
    model_turns: int = 0
    provider_latency_ms: float = 0.0
    wall_time_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "domain": self.domain,
            "title": self.title,
            "status": self.status,
            "blocked_reason": self.blocked_reason,
            "pass_at_attempt": self.pass_at_attempt,
            "raw_first_check_passed": self.raw_first_check_passed,
            "post_autofix_first_check_passed": self.post_autofix_first_check_passed,
            "first_attempt_autofix_applied": self.first_attempt_autofix_applied,
            "first_attempt_oracle_passed": self.first_attempt_oracle_passed,
            "raw_pass_at_1": self.raw_pass_at_1,
            "model_turns": self.model_turns,
            "attempts": [a.to_dict() for a in self.attempts],
            "total_latency_ms": round(self.total_latency_ms, 2),
            "provider_latency_ms": round(self.provider_latency_ms, 2),
            "wall_time_ms": round(self.wall_time_ms, 2),
            "total_tokens": self.total_tokens,
            "final_error_category": self.final_error_category,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> TaskEvaluationResult:
        values = {name: data[name] for name in cls.__dataclass_fields__ if name in data and name != "attempts"}
        values["attempts"] = [AttemptRecord.from_dict(item) for item in data.get("attempts", [])]
        return cls(**values)


@dataclass
class EvaluationSummary:
    timestamp: str
    compiler_version: str
    compiler_commit: str
    provider_name: str
    model_name: str
    total_tasks: int
    supported_tasks: int
    blocked_tasks: int
    pass_at_1_count: int
    pass_at_3_count: int
    pass_at_1_rate: float
    pass_at_3_rate: float
    error_distribution: Dict[str, int]
    avg_latency_ms: float
    total_tokens: int
    results: List[TaskEvaluationResult] = field(default_factory=list)
    schema_version: str = SCHEMA_VERSION
    max_repair_cycles: int = 3
    max_attempts: int = 4
    raw_first_check_pass_count: int = 0
    raw_first_check_pass_rate: float = 0.0
    post_autofix_first_check_pass_count: int = 0
    post_autofix_first_check_pass_rate: float = 0.0
    raw_pass_at_1_count: int = 0
    raw_pass_at_1_rate: float = 0.0
    autofix_pass_at_1_count: int = 0
    autofix_pass_at_1_rate: float = 0.0
    resolved_count: int = 0
    resolved_rate: float = 0.0
    avg_provider_latency_ms: float = 0.0
    avg_wall_time_ms: float = 0.0
    total_provider_latency_ms: float = 0.0
    total_wall_time_ms: float = 0.0
    avg_model_turns: float = 0.0
    total_model_turns: int = 0
    token_counting_method: str = "unknown"
    denominators: Dict[str, int] = field(default_factory=dict)
    prompt_token_attribution: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "timestamp": self.timestamp,
            "compiler_version": self.compiler_version,
            "compiler_commit": self.compiler_commit,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "token_counting_method": self.token_counting_method,
            "max_repair_cycles": self.max_repair_cycles,
            "max_attempts": self.max_attempts,
            "total_tasks": self.total_tasks,
            "supported_tasks": self.supported_tasks,
            "blocked_tasks": self.blocked_tasks,
            "denominators": self.denominators,
            "raw_first_check_pass_count": self.raw_first_check_pass_count,
            "raw_first_check_pass_rate": round(self.raw_first_check_pass_rate, 4),
            "post_autofix_first_check_pass_count": self.post_autofix_first_check_pass_count,
            "post_autofix_first_check_pass_rate": round(self.post_autofix_first_check_pass_rate, 4),
            "raw_pass_at_1_count": self.raw_pass_at_1_count,
            "raw_pass_at_1_rate": round(self.raw_pass_at_1_rate, 4),
            "post_autofix_pass_at_1_rate": round(self.pass_at_1_rate, 4),
            "autofix_pass_at_1_count": self.autofix_pass_at_1_count,
            "autofix_pass_at_1_rate": round(self.autofix_pass_at_1_rate, 4),
            "pass_at_1_count": self.pass_at_1_count,
            "pass_at_3_count": self.pass_at_3_count,
            "pass_at_1_rate": round(self.pass_at_1_rate, 4),
            "pass_at_3_rate": round(self.pass_at_3_rate, 4),
            "resolved_count": self.resolved_count,
            "resolved_rate": round(self.resolved_rate, 4),
            "error_distribution": self.error_distribution,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "avg_provider_latency_ms": round(self.avg_provider_latency_ms, 2),
            "avg_wall_time_ms": round(self.avg_wall_time_ms, 2),
            "total_provider_latency_ms": round(self.total_provider_latency_ms, 2),
            "total_wall_time_ms": round(self.total_wall_time_ms, 2),
            "avg_model_turns": round(self.avg_model_turns, 2),
            "total_model_turns": self.total_model_turns,
            "total_tokens": self.total_tokens,
            "prompt_token_attribution": self.prompt_token_attribution,
            "results": [r.to_dict() for r in self.results],
        }
