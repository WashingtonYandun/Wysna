"""Offline provider safety and quota contracts; no requests reach the network."""

import io
import json
import urllib.error
from unittest.mock import Mock

import pytest

from bench.ai_eval.providers import AnthropicProvider, DailyQuotaExceeded, OpenAICompatibleProvider


@pytest.mark.parametrize("model", ["openai/gpt-4.1", "openrouter/free", "nvidia/model", ""])
def test_openrouter_rejects_non_free_models_before_requests(model, monkeypatch):
    request = Mock()
    monkeypatch.setattr("urllib.request.urlopen", request)
    with pytest.raises(ValueError, match=":free"):
        OpenAICompatibleProvider(base_url="https://openrouter.ai/api/v1", api_key="fixture", model=model)
    request.assert_not_called()


def test_daily_quota_stops_without_sleep_or_retry(monkeypatch):
    body = {"error": {"message": "Rate limit exceeded: free-models-per-day", "metadata": {
        "limit_source": "openrouter_free_tier_daily", "headers": {"X-RateLimit-Reset": "1795305600000"},
    }}}
    error = urllib.error.HTTPError("https://openrouter.ai/api/v1", 429, "quota", {},
                                   io.BytesIO(json.dumps(body).encode()))
    request = Mock(side_effect=error)
    sleep = Mock()
    monkeypatch.setattr("urllib.request.urlopen", request)
    monkeypatch.setattr("time.sleep", sleep)
    provider = OpenAICompatibleProvider(base_url="https://openrouter.ai/api/v1", api_key="fixture",
                                        model="nvidia/model:free", min_interval_sec=0)
    with pytest.raises(DailyQuotaExceeded) as stopped:
        provider.generate("system", "prompt")
    assert stopped.value.reset_at == "1795305600000"
    request.assert_called_once()
    sleep.assert_not_called()


def test_openrouter_hostname_is_not_detected_inside_a_path():
    provider = OpenAICompatibleProvider(base_url="http://localhost:1234/openrouter.ai/v1")
    assert not provider.is_openrouter


class _DummyHTTPResponse:
    def __init__(self, body: dict):
        self._raw = json.dumps(body).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def test_anthropic_requires_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        AnthropicProvider()


def test_anthropic_generate_formats_request_and_maps_usage(monkeypatch):
    captured = {}

    def fake_urlopen(req, context=None, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = {k.lower(): v for k, v in req.header_items()}
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        captured["timeout"] = timeout
        return _DummyHTTPResponse({
            "content": [
                {"type": "text", "text": "```wysna\n"},
                {"type": "thinking", "thinking": "ignored"},
                {"type": "text", "text": "fn main() -> unit {}\n```"},
            ],
            "usage": {"input_tokens": 120, "output_tokens": 35},
        })

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    provider = AnthropicProvider(api_key="fixture-key", min_interval_sec=0.0)
    assert provider.token_counting_method == "provider_usage"

    text, usage, latency_ms = provider.generate(
        "sys prompt",
        "next turn",
        conversation_history=[
            {"role": "user", "content": "first"},
            {"role": "model", "content": "reply"},
        ],
    )
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    assert captured["headers"]["x-api-key"] == "fixture-key"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"
    assert captured["payload"]["model"] == "claude-haiku-5-5"
    assert "temperature" not in captured["payload"]
    assert captured["payload"]["system"] == "sys prompt"
    assert captured["payload"]["messages"] == [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "next turn"},
    ]
    assert text == "```wysna\nfn main() -> unit {}\n```"
    assert usage == {"prompt_tokens": 120, "completion_tokens": 35}
    assert latency_ms >= 0.0


def test_anthropic_retries_429_and_529_then_succeeds(monkeypatch):
    err_429 = urllib.error.HTTPError(
        "https://api.anthropic.com/v1/messages",
        429,
        "rate limit",
        {"Retry-After": "1.5"},
        io.BytesIO(b'{"error": {"type": "rate_limit_error"}}'),
    )
    err_529 = urllib.error.HTTPError(
        "https://api.anthropic.com/v1/messages",
        529,
        "overloaded",
        {},
        io.BytesIO(b'{"error": {"type": "overloaded_error"}}'),
    )
    ok = _DummyHTTPResponse({
        "content": [{"type": "text", "text": "ok"}],
        "usage": {"input_tokens": 10, "output_tokens": 2},
    })
    request = Mock(side_effect=[err_429, err_529, ok])
    sleep = Mock()
    monkeypatch.setattr("urllib.request.urlopen", request)
    monkeypatch.setattr("time.sleep", sleep)

    provider = AnthropicProvider(api_key="fixture-key", min_interval_sec=0.0)
    text, usage, _ = provider.generate("sys", "user")
    assert text == "ok"
    assert usage == {"prompt_tokens": 10, "completion_tokens": 2}
    assert request.call_count == 3
    assert sleep.call_count == 2
    assert sleep.call_args_list[0].args[0] == pytest.approx(2.0)


def test_anthropic_fails_immediately_on_400(monkeypatch):
    err_400 = urllib.error.HTTPError(
        "https://api.anthropic.com/v1/messages",
        400,
        "bad request",
        {},
        io.BytesIO(b'{"error": {"type": "invalid_request_error"}}'),
    )
    request = Mock(side_effect=err_400)
    sleep = Mock()
    monkeypatch.setattr("urllib.request.urlopen", request)
    monkeypatch.setattr("time.sleep", sleep)

    provider = AnthropicProvider(api_key="fixture-key", min_interval_sec=0.0)
    with pytest.raises(RuntimeError, match="Anthropic API HTTP 400"):
        provider.generate("sys", "user")
    request.assert_called_once()
    sleep.assert_not_called()
