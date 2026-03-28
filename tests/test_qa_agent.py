"""Tests for QA agent shell execution and memory behavior."""
import asyncio
from pathlib import Path

from nexuscrew.agents.qa import QAAgent
from nexuscrew.agents import qa as qa_module
from nexuscrew.executor.shell import ShellExecutor


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

    def complete(self, system: str, messages: list[dict], use_thinking: bool = False,
                 light_mode: bool = False) -> str:
        self.calls.append((system, messages))
        return self.reply


async def _direct_to_thread(func, *args, **kwargs):
    return func(*args, **kwargs)


def test_qa_agent_extracts_memory_note_and_runs_shell(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(qa_module.asyncio, "to_thread", _direct_to_thread)
    executor = ShellExecutor(tmp_path)

    async def fake_run_blocks(reply: str) -> str:
        assert "```bash" in reply
        return "pytest passed"

    monkeypatch.setattr(executor, "run_blocks", fake_run_blocks)
    agent = QAAgent(
        "qa-1",
        FakeAnthropicBackend("```bash\npytest -q\n```\n结论: Go【MEMORY】保留质量闸门"),
        executor,
        model_label="claude",
    )

    reply, artifacts = asyncio.run(agent.handle("做回归测试", [], "memory"))

    assert reply == "```bash\npytest -q\n```\n结论: Go"
    assert artifacts.shell_output == "pytest passed"
    assert artifacts.memory_note == "保留质量闸门"


def test_qa_agent_supports_gemini_backend(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(qa_module.asyncio, "to_thread", _direct_to_thread)
    executor = ShellExecutor(tmp_path)

    async def fake_run_blocks(reply: str) -> str:
        return ""

    monkeypatch.setattr(executor, "run_blocks", fake_run_blocks)
    backend = FakeGeminiBackend("结论: No-Go")
    agent = QAAgent("qa-1", backend, executor, model_label="gemini")

    reply, artifacts = asyncio.run(agent.handle("做冒烟测试", [], "memory"))

    assert reply == "结论: No-Go"
    assert artifacts.memory_note == ""
    assert "做冒烟测试" in backend.prompts[0]
