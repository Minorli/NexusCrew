"""Tests for HR agent wiring and memory behavior."""
import asyncio
from pathlib import Path

from nexuscrew.agents.hr import HRAgent
from nexuscrew.agents import hr as hr_module
from nexuscrew.memory.crew_memory import CrewMemory
from nexuscrew.orchestrator import Orchestrator
from nexuscrew.registry import AgentRegistry
from nexuscrew.router import Router
from nexuscrew.executor.shell import ShellExecutor


class FakeGeminiBackend:
    def __init__(self, reply: str):
        self.reply = reply
        self.prompts: list[str] = []

    def complete(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


async def _direct_to_thread(func, *args, **kwargs):
    return func(*args, **kwargs)


def test_hr_agent_extracts_memory_note(monkeypatch):
    monkeypatch.setattr(hr_module.asyncio, "to_thread", _direct_to_thread)
    backend = FakeGeminiBackend("绩效正常【MEMORY】保留亮点")
    agent = HRAgent("carol", backend)

    reply, artifacts = asyncio.run(agent.handle("出绩效报告", [], "memory"))

    assert reply == "绩效正常"
    assert artifacts.memory_note == "保留亮点"
    assert "出绩效报告" in backend.prompts[0]


def test_init_from_config_registers_hr_agent(tmp_path: Path, monkeypatch):
    from nexuscrew.config import AgentSpec, CrewConfig
    from nexuscrew.telegram.bot import NexusCrewBot

    monkeypatch.setattr(hr_module.asyncio, "to_thread", _direct_to_thread)
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    bot = NexusCrewBot()

    async def fake_scan(project):
        return "brief"

    monkeypatch.setattr(bot.scanner, "scan", fake_scan)
    bot._bot_pool = None

    replies: list[str] = []

    class FakeMessage:
        chat_id = 123

        async def reply_text(self, text: str):
            replies.append(text)

    update = type("Update", (), {"message": FakeMessage()})()
    config = CrewConfig(
        project_dir=project_dir,
        agents=[AgentSpec(role="hr", name="carol", model="gemini")],
    )

    asyncio.run(bot._init_from_config(config, update))

    assert bot.registry.get_by_name("carol").role == "hr"
    assert any("编组完成" in reply for reply in replies)

def test_orchestrator_persists_hr_memory_note(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(hr_module.asyncio, "to_thread", _direct_to_thread)
    registry = AgentRegistry()
    registry.register(HRAgent("carol", FakeGeminiBackend("报告【MEMORY】写入记忆")))
    memory = CrewMemory(tmp_path / "crew_memory.md")
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        memory,
        ShellExecutor(tmp_path),
    )

    sent: list[tuple[str, str | None]] = []

    async def send(text: str, agent_name: str | None = None):
        sent.append((text, agent_name))

    asyncio.run(orchestrator.run_chain("@carol 出绩效报告", 1, send))

    text = memory.read(tail_lines=20)
    assert "写入记忆" in text
    assert sent[-1][1] == "carol"
