"""Tests for Architect thinking/light-mode selection."""
import asyncio
from types import SimpleNamespace

from nexuscrew.agents.architect import ArchitectAgent
from nexuscrew.agents import architect as architect_module
from nexuscrew.backends import anthropic_backend as anthropic_backend_module
from nexuscrew.backends.anthropic_backend import AnthropicBackend


class FakeAnthropicRateLimitError(Exception):
    pass


class FakeAnthropicAPITimeoutError(Exception):
    pass


class FakeAnthropicAPIError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


async def _direct_to_thread(func, *args, **kwargs):
    return func(*args, **kwargs)


def test_anthropic_backend_uses_thinking_and_light_model(monkeypatch):
    calls: list[dict] = []

    class FakeClient:
        def __init__(self):
            self.messages = SimpleNamespace(create=self.create)

        def create(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(content=[SimpleNamespace(text="ok")])

    monkeypatch.setattr(
        anthropic_backend_module.anthropic,
        "Anthropic",
        lambda **kwargs: FakeClient(),
    )
    monkeypatch.setattr(
        anthropic_backend_module.anthropic,
        "RateLimitError",
        FakeAnthropicRateLimitError,
    )
    monkeypatch.setattr(
        anthropic_backend_module.anthropic,
        "APITimeoutError",
        FakeAnthropicAPITimeoutError,
    )
    monkeypatch.setattr(
        anthropic_backend_module.anthropic,
        "APIError",
        FakeAnthropicAPIError,
    )

    backend = AnthropicBackend(
        "key",
        "claude-opus-4-6",
        model_light="claude-sonnet-4-6",
    )

    backend.complete("system", [{"role": "user", "content": "hi"}], use_thinking=True)
    backend.complete("system", [{"role": "user", "content": "hi"}], light_mode=True)

    assert calls[0]["model"] == "claude-opus-4-6"
    assert calls[0]["thinking"]["budget_tokens"] == backend.budget_tokens
    assert calls[1]["model"] == "claude-sonnet-4-6"
    assert "thinking" not in calls[1]


def test_architect_agent_selects_backend_modes(monkeypatch):
    monkeypatch.setattr(architect_module.asyncio, "to_thread", _direct_to_thread)
    calls: list[tuple[bool, bool]] = []

    class FakeBackend:
        def complete(self, system, messages, use_thinking=False, light_mode=False):
            calls.append((use_thinking, light_mode))
            return "LGTM"

    agent = ArchitectAgent("dave", FakeBackend())

    asyncio.run(agent.handle("帮我设计缓存架构", [], "memory"))
    asyncio.run(agent.handle("Review 一下这个 PR", [], "memory"))

    assert calls[0] == (True, False)
    assert calls[1] == (False, True)
