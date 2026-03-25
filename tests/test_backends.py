"""Tests for backend retry and error handling."""
from types import SimpleNamespace

from nexuscrew.backends import anthropic_backend, openai_backend
from nexuscrew.backends.anthropic_backend import AnthropicBackend
from nexuscrew.backends.openai_backend import OpenAIBackend


def _openai_response(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))]
    )


def _anthropic_response(text: str):
    return SimpleNamespace(
        content=[SimpleNamespace(text=text)]
    )


class FakeOpenAIRateLimitError(Exception):
    pass


class FakeOpenAIAPITimeoutError(Exception):
    pass


class FakeOpenAIAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class FakeAnthropicRateLimitError(Exception):
    pass


class FakeAnthropicAPITimeoutError(Exception):
    pass


class FakeAnthropicAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


def test_openai_backend_retries_rate_limit(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.calls = 0
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create)
            )

        def create(self, **kwargs):
            self.calls += 1
            if self.calls < 3:
                raise FakeOpenAIRateLimitError("slow down")
            return _openai_response("ok")

    monkeypatch.setattr(openai_backend.openai, "OpenAI", lambda **kwargs: FakeClient())
    monkeypatch.setattr(openai_backend.openai, "RateLimitError", FakeOpenAIRateLimitError)
    monkeypatch.setattr(openai_backend.openai, "APITimeoutError", FakeOpenAIAPITimeoutError)
    monkeypatch.setattr(openai_backend.openai, "APIError", FakeOpenAIAPIError)
    monkeypatch.setattr(openai_backend.time, "sleep", lambda *_: None)

    backend = OpenAIBackend("key", "https://example.com", "model")
    assert backend.complete([{"role": "user", "content": "hi"}]) == "ok"
    assert backend._client.calls == 3


def test_openai_backend_returns_error_string_for_client_error(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.chat = SimpleNamespace(
                completions=SimpleNamespace(create=self.create)
            )

        def create(self, **kwargs):
            raise FakeOpenAIAPIError("invalid key", status_code=401)

    monkeypatch.setattr(openai_backend.openai, "OpenAI", lambda **kwargs: FakeClient())
    monkeypatch.setattr(openai_backend.openai, "RateLimitError", FakeOpenAIRateLimitError)
    monkeypatch.setattr(openai_backend.openai, "APITimeoutError", FakeOpenAIAPITimeoutError)
    monkeypatch.setattr(openai_backend.openai, "APIError", FakeOpenAIAPIError)

    backend = OpenAIBackend("key", "https://example.com", "model")
    result = backend.complete([{"role": "user", "content": "hi"}])

    assert result.startswith("[OpenAI API Error after 3 retries]")
    assert "invalid key" in result


def test_anthropic_backend_retries_rate_limit(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.calls = 0
            self.messages = SimpleNamespace(create=self.create)

        def create(self, **kwargs):
            self.calls += 1
            if self.calls < 3:
                raise FakeAnthropicRateLimitError("slow down")
            return _anthropic_response("ok")

    monkeypatch.setattr(anthropic_backend.anthropic, "Anthropic", lambda **kwargs: FakeClient())
    monkeypatch.setattr(anthropic_backend.anthropic, "RateLimitError", FakeAnthropicRateLimitError)
    monkeypatch.setattr(anthropic_backend.anthropic, "APITimeoutError", FakeAnthropicAPITimeoutError)
    monkeypatch.setattr(anthropic_backend.anthropic, "APIError", FakeAnthropicAPIError)
    monkeypatch.setattr(anthropic_backend.time, "sleep", lambda *_: None)

    backend = AnthropicBackend("key", "model")
    assert backend.complete("system", [{"role": "user", "content": "hi"}]) == "ok"
    assert backend._client.calls == 3


def test_anthropic_backend_returns_error_string_for_client_error(monkeypatch):
    class FakeClient:
        def __init__(self):
            self.messages = SimpleNamespace(create=self.create)

        def create(self, **kwargs):
            raise FakeAnthropicAPIError("invalid key", status_code=401)

    monkeypatch.setattr(anthropic_backend.anthropic, "Anthropic", lambda **kwargs: FakeClient())
    monkeypatch.setattr(anthropic_backend.anthropic, "RateLimitError", FakeAnthropicRateLimitError)
    monkeypatch.setattr(anthropic_backend.anthropic, "APITimeoutError", FakeAnthropicAPITimeoutError)
    monkeypatch.setattr(anthropic_backend.anthropic, "APIError", FakeAnthropicAPIError)

    backend = AnthropicBackend("key", "model")
    result = backend.complete("system", [{"role": "user", "content": "hi"}])

    assert result.startswith("[Anthropic API Error after 3 retries]")
    assert "invalid key" in result
