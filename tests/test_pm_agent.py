"""Tests for PM agent backend routing and memory behavior."""
import asyncio

from nexuscrew.agents.pm import PMAgent
from nexuscrew.agents import pm as pm_module


class FakeGeminiBackend:
    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


class FakeAnthropicBackend:
    def __init__(self, reply: str):
        self.reply = reply
        self.calls: list[tuple[str, list[dict]]] = []

    def complete(
        self,
        system: str,
        messages: list[dict],
        use_thinking: bool = False,
        light_mode: bool = False,
    ) -> str:
        self.calls.append((system, messages))
        return self.reply


async def _direct_to_thread(func, *args, **kwargs):
    return func(*args, **kwargs)


def test_pm_agent_supports_gemini_backend(monkeypatch):
    monkeypatch.setattr(pm_module.asyncio, "to_thread", _direct_to_thread)
    backend = FakeGeminiBackend("任务已拆解【MEMORY】记录验收标准")
    agent = PMAgent("alice", backend, model_label="gemini")

    reply, artifacts = asyncio.run(agent.handle("拆解任务", [], "memory"))

    assert reply == "任务已拆解"
    assert artifacts.memory_note == "记录验收标准"
    assert "拆解任务" in backend.prompts[0]
    assert agent.model_label == "gemini"


def test_pm_agent_supports_anthropic_backend(monkeypatch):
    monkeypatch.setattr(pm_module.asyncio, "to_thread", _direct_to_thread)
    backend = FakeAnthropicBackend("任务已拆解【MEMORY】记录验收标准")
    agent = PMAgent("alice", backend, model_label="claude")

    reply, artifacts = asyncio.run(agent.handle("拆解任务", [], "memory"))

    assert reply == "任务已拆解"
    assert artifacts.memory_note == "记录验收标准"
    assert "Technical PM" in backend.calls[0][0]
    assert backend.calls[0][1][-1]["content"] == "拆解任务"
    assert agent.model_label == "claude"
