"""LLM Providers for Wysna AI Evaluation Benchmark.

Zero external dependencies: uses Python standard library `urllib.request`
to communicate with Gemini API, OpenAI-compatible endpoints (Ollama, LM Studio),
or deterministic Mock fixtures for automated CI runs.
"""

from __future__ import annotations

import json
import os
import ssl
import time
import urllib.request
import urllib.error
import threading
from urllib.parse import urlsplit
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple


_openrouter_rate_lock = threading.Lock()
_openrouter_next_request_at = 0.0


class DailyQuotaExceeded(RuntimeError):
    """A daily free-tier limit cannot be recovered by short retry delays."""

    def __init__(self, reset_at: Optional[str] = None):
        self.reset_at = reset_at
        super().__init__("OpenRouter free daily quota exhausted; checkpoint preserved. "
                         + (f"Reset: {reset_at}. " if reset_at else "")
                         + "Resume after the daily reset.")


def _build_ssl_context():
    if os.path.exists("/etc/ssl/cert.pem"):
        return ssl.create_default_context(cafile="/etc/ssl/cert.pem")
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


class LlmProvider(ABC):
    """Abstract interface for LLM completion providers."""

    token_counting_method: str = "unknown"

    @abstractmethod
    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> Tuple[str, Dict[str, int], float]:
        """Generates completion text from model.

        Returns:
            (completion_text, token_counts, latency_ms)
        """
        pass


class MockProvider(LlmProvider):
    """Deterministic mock provider for CI, verification, and regression testing."""

    token_counting_method: str = "estimated_whitespace"

    def __init__(self, canned_responses: Optional[Dict[str, List[str]]] = None):
        # canned_responses: {task_id_or_keyword: [response_attempt_0, response_attempt_1, ...]}
        self.canned_responses = canned_responses or {}
        self.call_history: List[Dict[str, Any]] = []
        self._counter: Dict[str, int] = {}

    def set_task_response(self, task_id: str, responses: List[str]) -> None:
        self.canned_responses[task_id] = responses

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> Tuple[str, Dict[str, int], float]:
        start = time.perf_counter()
        self.call_history.append({
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "history": conversation_history or [],
        })

        matched_key = "default"
        history_text = " ".join(msg.get("content", "") for msg in (conversation_history or []))
        for key in self.canned_responses:
            if key in user_prompt or key in system_prompt or key in history_text:
                matched_key = key
                break

        responses = self.canned_responses.get(matched_key, [
            "```wysna\nfn main() -> unit {\n    print(\"MOCK_FALLBACK\")\n}\n```"
        ])
        idx = self._counter.get(matched_key, 0)
        resp = responses[min(idx, len(responses) - 1)]
        self._counter[matched_key] = idx + 1

        latency_ms = (time.perf_counter() - start) * 1000.0
        tokens = {
            "prompt_tokens": len(user_prompt.split()) + len(system_prompt.split()),
            "completion_tokens": len(resp.split()),
        }
        return resp, tokens, latency_ms


class GeminiProvider(LlmProvider):
    """Google Gemini API provider using urllib with SSL support and backoff."""

    token_counting_method: str = "provider_usage"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-flash-lite-latest",
        temperature: float = 0.2,
        min_interval_sec: float = 4.5,
    ):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        if not self.api_key:
            raise ValueError(
                "GeminiProvider requires GEMINI_API_KEY or GOOGLE_API_KEY environment variable."
            )
        self.model = model
        self.temperature = temperature
        self.min_interval_sec = min_interval_sec
        self.last_call_time = 0.0
        self.endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent?key={self.api_key}"
        )
        self.ssl_context = _build_ssl_context()

    def _build_ssl_context(self):
        return _build_ssl_context()

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> Tuple[str, Dict[str, int], float]:
        start = time.perf_counter()

        contents: List[Dict[str, Any]] = []
        if conversation_history:
            for msg in conversation_history:
                role = "user" if msg["role"] == "user" else "model"
                contents.append({
                    "role": role,
                    "parts": [{"text": msg["content"]}],
                })

        contents.append({
            "role": "user",
            "parts": [{"text": user_prompt}],
        })

        payload = {
            "contents": contents,
            "systemInstruction": {
                "parts": [{"text": system_prompt}],
            },
            "generationConfig": {
                "temperature": self.temperature,
                "maxOutputTokens": 4096,
            },
        }

        req_data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            self.endpoint,
            data=req_data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        # Rate-limiting and retry loop for free-tier quotas (15 RPM)
        max_retries = 5
        data = None
        for attempt in range(max_retries):
            # Enforce minimum spacing between calls
            elapsed = time.time() - self.last_call_time
            if elapsed < self.min_interval_sec:
                time.sleep(self.min_interval_sec - elapsed)
            self.last_call_time = time.time()

            try:
                with urllib.request.urlopen(req, context=self.ssl_context, timeout=30) as response:
                    res_body = response.read().decode("utf-8")
                    data = json.loads(res_body)
                    break
            except urllib.error.HTTPError as e:
                err_msg = e.read().decode("utf-8")
                if e.code in (429, 503) and attempt < max_retries - 1:
                    delay = 35.0
                    try:
                        parsed_err = json.loads(err_msg)
                        for detail in parsed_err.get("error", {}).get("details", []):
                            if detail.get("@type", "").endswith("RetryInfo"):
                                delay_str = detail.get("retryDelay", "35s").rstrip("s")
                                delay = float(delay_str) + 3.0
                                break
                    except Exception:
                        pass
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"Gemini API HTTP {e.code}: {err_msg}") from e
            except Exception as e:
                if attempt < max_retries - 1:
                    time.sleep(2.0)
                    continue
                raise RuntimeError(f"Gemini API connection error: {e}") from e

        latency_ms = (time.perf_counter() - start) * 1000.0

        candidates = data.get("candidates", [])
        if not candidates:
            raise RuntimeError("Gemini API returned no candidates.")

        text_parts = []
        for part in candidates[0].get("content", {}).get("parts", []):
            if "text" in part:
                text_parts.append(part["text"])

        completion = "".join(text_parts)
        usage = data.get("usageMetadata", {})
        tokens = {
            "prompt_tokens": usage.get("promptTokenCount", 0),
            "completion_tokens": usage.get("candidatesTokenCount", 0),
        }
        return completion, tokens, latency_ms


class OpenAICompatibleProvider(LlmProvider):
    """Generic OpenAI-compatible provider (OpenRouter, Ollama, LM Studio, vLLM, OpenAI)."""

    token_counting_method: str = "provider_usage"

    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        api_key: Optional[str] = None,
        model: str = "local-model",
        temperature: float = 0.2,
        max_tokens: int = 8192,
        min_interval_sec: Optional[float] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.is_openrouter = urlsplit(self.base_url).hostname == "openrouter.ai"
        if self.is_openrouter and not model.endswith(":free"):
            raise ValueError("OpenRouter evaluation accepts only explicit :free model IDs.")
        environment_key = os.getenv("OPENROUTER_API_KEY") if self.is_openrouter else os.getenv("OPENAI_API_KEY")
        self.api_key = api_key or environment_key or ("" if self.is_openrouter else "not-needed")
        if self.is_openrouter and not self.api_key:
            raise ValueError("OpenRouter requires OPENROUTER_API_KEY or --api-key.")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.min_interval_sec = (3.5 if self.is_openrouter else 0.0) if min_interval_sec is None else min_interval_sec
        self.endpoint = f"{self.base_url}/chat/completions"
        self.ssl_context = _build_ssl_context()

    def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        conversation_history: Optional[List[Dict[str, str]]] = None,
    ) -> Tuple[str, Dict[str, int], float]:
        start = time.perf_counter()

        messages: List[Dict[str, str]] = [{"role": "system", "content": system_prompt}]
        if conversation_history:
            messages.extend(conversation_history)
        messages.append({"role": "user", "content": user_prompt})

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
        }

        req_data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        if self.is_openrouter:
            headers["HTTP-Referer"] = "https://github.com/WashingtonYandun/Wysna"
            headers["X-Title"] = "Wysna AI Eval"

        backoff_schedule = (10.0, 20.0, 35.0, 45.0)
        max_retries = 5
        data = None
        for attempt in range(max_retries):
            if self.is_openrouter and self.min_interval_sec > 0.0:
                global _openrouter_next_request_at
                with _openrouter_rate_lock:
                    now = time.monotonic()
                    scheduled_at = max(now, _openrouter_next_request_at)
                    _openrouter_next_request_at = scheduled_at + self.min_interval_sec
                if scheduled_at > now:
                    time.sleep(scheduled_at - now)

            req = urllib.request.Request(self.endpoint, data=req_data, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, context=self.ssl_context, timeout=120) as response:
                    res_body = response.read().decode("utf-8")
                    data = json.loads(res_body)
                    break
            except urllib.error.HTTPError as e:
                err_msg = e.read().decode("utf-8", errors="replace")
                if self.is_openrouter and e.code == 429:
                    try:
                        detail = json.loads(err_msg).get("error", {})
                        metadata = detail.get("metadata") or {}
                        daily_limit = (metadata.get("limit_source") == "openrouter_free_tier_daily"
                                       or "free-models-per-day" in detail.get("message", ""))
                    except (ValueError, AttributeError, TypeError):
                        daily_limit = False
                        metadata = {}
                    if daily_limit:
                        reset = (metadata.get("headers") or {}).get("X-RateLimit-Reset")
                        raise DailyQuotaExceeded(reset) from e
                if e.code in (429, 502, 503, 504) and attempt < max_retries - 1:
                    delay = backoff_schedule[min(attempt, len(backoff_schedule) - 1)]
                    retry_after = e.headers.get("Retry-After") if e.headers else None
                    if retry_after:
                        try:
                            delay = max(float(retry_after) + 1.0, 1.0)
                        except ValueError:
                            pass
                    time.sleep(delay)
                    continue
                raise RuntimeError(f"OpenAI API HTTP {e.code}: {err_msg}") from e
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                if attempt < max_retries - 1:
                    time.sleep(2.0)
                    continue
                raise RuntimeError(f"OpenAI API connection error: {e}") from e

        latency_ms = (time.perf_counter() - start) * 1000.0

        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("OpenAI API returned no choices.")

        message = choices[0].get("message", {}) or {}
        completion = message.get("content") or ""
        usage = data.get("usage", {})
        tokens = {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
        }
        return completion, tokens, latency_ms
