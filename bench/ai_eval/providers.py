"""LLM Providers for Wysna AI Evaluation Benchmark.

Zero external dependencies: uses Python standard library `urllib.request`
to communicate with Gemini API, OpenAI-compatible endpoints (Ollama, LM Studio),
or deterministic Mock fixtures for automated CI runs.
"""

from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Tuple


class LlmProvider(ABC):
    """Abstract interface for LLM completion providers."""

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
        self.ssl_context = self._build_ssl_context()

    def _build_ssl_context(self):
        import ssl
        if os.path.exists("/etc/ssl/cert.pem"):
            return ssl.create_default_context(cafile="/etc/ssl/cert.pem")
        try:
            import certifi
            return ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            return ssl.create_default_context()

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
    """Generic OpenAI-compatible provider (Ollama, LM Studio, vLLM, OpenAI)."""

    def __init__(
        self,
        base_url: str = "http://localhost:1234/v1",
        api_key: Optional[str] = None,
        model: str = "local-model",
        temperature: float = 0.2,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key or os.getenv("OPENAI_API_KEY", "not-needed")
        self.model = model
        self.temperature = temperature
        self.endpoint = f"{self.base_url}/chat/completions"

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
            "max_tokens": 4096,
        }

        req_data = json.dumps(payload).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self.api_key}",
        }
        req = urllib.request.Request(self.endpoint, data=req_data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=120) as response:
                res_body = response.read().decode("utf-8")
                data = json.loads(res_body)
        except urllib.error.HTTPError as e:
            err_msg = e.read().decode("utf-8")
            raise RuntimeError(f"OpenAI API HTTP {e.code}: {err_msg}") from e
        except Exception as e:
            raise RuntimeError(f"OpenAI API connection error: {e}") from e

        latency_ms = (time.perf_counter() - start) * 1000.0

        choices = data.get("choices", [])
        if not choices:
            raise RuntimeError("OpenAI API returned no choices.")

        completion = choices[0].get("message", {}).get("content", "")
        usage = data.get("usage", {})
        tokens = {
            "prompt_tokens": usage.get("prompt_tokens", 0),
            "completion_tokens": usage.get("completion_tokens", 0),
        }
        return completion, tokens, latency_ms
