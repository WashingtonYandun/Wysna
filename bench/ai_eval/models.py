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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "args": self.args,
            "stdin": self.stdin,
            "expected_stdout": self.expected_stdout,
            "expected_exit": self.expected_exit,
            "file_assertions": self.file_assertions,
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
    error_category: Optional[str] = None  # syntax | type | api_hallucination | runtime | oracle_mismatch | none
    latency_ms: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "attempt_index": self.attempt_index,
            "code": self.code,
            "check_passed": self.check_passed,
            "check_diagnostics": self.check_diagnostics,
            "raw_stderr": self.raw_stderr,
            "run_passed": self.run_passed,
            "oracle_passed": self.oracle_passed,
            "oracle_failure_details": self.oracle_failure_details,
            "error_category": self.error_category,
            "latency_ms": round(self.latency_ms, 2),
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
        }


@dataclass
class TaskEvaluationResult:
    task_id: str
    domain: str
    title: str
    status: str  # pass | fail | blocked
    blocked_reason: Optional[str] = None
    pass_at_attempt: Optional[int] = None  # 1 (first try), 2 (first repair), 3 (second repair), 4 (third repair), None
    attempts: List[AttemptRecord] = field(default_factory=list)
    total_latency_ms: float = 0.0
    total_tokens: int = 0
    final_error_category: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "domain": self.domain,
            "title": self.title,
            "status": self.status,
            "blocked_reason": self.blocked_reason,
            "pass_at_attempt": self.pass_at_attempt,
            "attempts": [a.to_dict() for a in self.attempts],
            "total_latency_ms": round(self.total_latency_ms, 2),
            "total_tokens": self.total_tokens,
            "final_error_category": self.final_error_category,
        }


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

    def to_dict(self) -> Dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "compiler_version": self.compiler_version,
            "compiler_commit": self.compiler_commit,
            "provider_name": self.provider_name,
            "model_name": self.model_name,
            "total_tasks": self.total_tasks,
            "supported_tasks": self.supported_tasks,
            "blocked_tasks": self.blocked_tasks,
            "pass_at_1_count": self.pass_at_1_count,
            "pass_at_3_count": self.pass_at_3_count,
            "pass_at_1_rate": round(self.pass_at_1_rate, 4),
            "pass_at_3_rate": round(self.pass_at_3_rate, 4),
            "error_distribution": self.error_distribution,
            "avg_latency_ms": round(self.avg_latency_ms, 2),
            "total_tokens": self.total_tokens,
            "results": [r.to_dict() for r in self.results],
        }
