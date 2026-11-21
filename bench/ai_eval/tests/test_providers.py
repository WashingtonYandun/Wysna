"""Offline provider safety and quota contracts; no requests reach the network."""

import io
import json
import urllib.error
from unittest.mock import Mock

import pytest

from bench.ai_eval.providers import DailyQuotaExceeded, OpenAICompatibleProvider


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
