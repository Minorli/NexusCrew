"""Tests for dispatcher bot pool and send routing."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexuscrew.agents.base import AgentArtifacts, BaseAgent
from nexuscrew.executor.shell import ShellExecutor
from nexuscrew.memory.crew_memory import CrewMemory
from nexuscrew.orchestrator import Orchestrator
from nexuscrew.registry import AgentRegistry
from nexuscrew.router import Router
from nexuscrew.telegram import dispatcher


class FakeBot:
    def __init__(self, token: str):
        self.token = token
        self.sent: list[tuple[int, str]] = []

    async def send_message(self, chat_id: int, text: str):
        self.sent.append((chat_id, text))

    async def get_me(self):
        return SimpleNamespace(id=hash(self.token) & 0xFFFF)

    async def get_chat_member(self, chat_id: int, bot_id: int):
        return SimpleNamespace(status="member")


def test_agent_bot_pool_prefers_dedicated_bot(monkeypatch):
    monkeypatch.setattr(dispatcher, "Bot", FakeBot)
    monkeypatch.setattr(
        dispatcher.cfg,
        "AGENT_BOT_TOKENS",
        {"alice": "alice-token"},
        raising=False,
    )

    pool = dispatcher.AgentBotPool("dispatcher-token")

    assert pool.is_multi_bot is True
    assert pool.get_bot("alice").token == "alice-token"
    assert pool.get_bot("bob").token == "dispatcher-token"


@pytest.mark.asyncio
async def test_agent_bot_pool_falls_back_for_missing_token(monkeypatch):
    monkeypatch.setattr(dispatcher, "Bot", FakeBot)
    monkeypatch.setattr(
        dispatcher.cfg,
        "AGENT_BOT_TOKENS",
        {},
        raising=False,
    )

    pool = dispatcher.AgentBotPool("dispatcher-token")
    await pool.send_as_agent("alice", 42, "hello")

    assert pool.is_multi_bot is False
    assert pool.get_bot("alice").sent == [(42, "hello")]


@pytest.mark.asyncio
async def test_validate_group_reports_missing_bots(monkeypatch):
    class FakeValidationBot(FakeBot):
        async def get_chat_member(self, chat_id: int, bot_id: int):
            if self.token == "missing-token":
                return SimpleNamespace(status="left")
            return SimpleNamespace(status="member")

    monkeypatch.setattr(dispatcher, "Bot", FakeValidationBot)
    monkeypatch.setattr(
        dispatcher.cfg,
        "AGENT_BOT_TOKENS",
        {"alice": "ok-token", "bob": "missing-token"},
        raising=False,
    )

    pool = dispatcher.AgentBotPool("dispatcher-token")

    assert await pool.validate_group(42) == ["bob"]


class FakeAgent(BaseAgent):
    def __init__(self, name: str, role: str, reply: str):
        super().__init__(name, role, "test")
        self.reply = reply

    async def handle(self, message, history, crew_memory):
        return self.reply, AgentArtifacts()


@pytest.mark.asyncio
async def test_orchestrator_sends_agent_messages_with_agent_name(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )

    sent: list[tuple[str, str | None]] = []

    async def send(text: str, agent_name: str | None = None):
        sent.append((text, agent_name))

    await orchestrator.run_chain("@alice 开始", 1, send)

    assert sent[0][1] is None
    assert sent[1][1] == "alice"
    assert sent[1][0].startswith("**[alice]**")


@pytest.mark.asyncio
async def test_init_from_config_reports_missing_agent_bots(tmp_path: Path, monkeypatch):
    from nexuscrew.config import AgentSpec, CrewConfig
    from nexuscrew.telegram.bot import NexusCrewBot

    project_dir = tmp_path / "project"
    project_dir.mkdir()

    bot = NexusCrewBot()
    async def fake_scan(project):
        return "brief"

    monkeypatch.setattr(bot.scanner, "scan", fake_scan)

    class FakePool:
        async def validate_group(self, chat_id: int):
            return ["alice"]

    bot._bot_pool = FakePool()

    replies: list[str] = []

    class FakeMessage:
        chat_id = 123

        async def reply_text(self, text: str):
            replies.append(text)

    update = SimpleNamespace(message=FakeMessage())
    config = CrewConfig(
        project_dir=project_dir,
        agents=[AgentSpec(role="pm", name="alice", model="gemini")],
    )

    await bot._init_from_config(config, update)

    assert replies[-1] == "以下 Agent Bot 尚未加入群组: alice"
