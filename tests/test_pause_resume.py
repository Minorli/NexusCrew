"""Tests for pause/resume/replay task controls."""
import asyncio
from pathlib import Path

from nexuscrew.agents.base import AgentArtifacts, BaseAgent
from nexuscrew.executor.shell import ShellExecutor
from nexuscrew.memory.crew_memory import CrewMemory
from nexuscrew.orchestrator import Orchestrator
from nexuscrew.registry import AgentRegistry
from nexuscrew.router import Router
from nexuscrew.runtime.store import EventStore
from nexuscrew.runtime.checkpoints import CheckpointStore


class FakeAgent(BaseAgent):
    def __init__(self, name: str, role: str, reply: str):
        super().__init__(name, role, "test")
        self.reply = reply

    async def handle(self, message, history, crew_memory):
        return self.reply, AgentArtifacts()


def test_orchestrator_pause_stops_next_hop(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "@bob 继续处理"))
    registry.register(FakeAgent("bob", "dev", "完成"))
    executor = ShellExecutor(tmp_path)
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        executor,
        event_store=EventStore(tmp_path / "events.jsonl"),
        checkpoint_store=CheckpointStore(tmp_path / "checkpoints.jsonl"),
    )

    sent: list[tuple[str, str | None]] = []

    async def send(text: str, agent_name: str | None = None):
        sent.append((text, agent_name))
        if text.startswith("**[alice]**"):
            orchestrator.pause_task(1, "T-0001")

    asyncio.run(orchestrator.run_chain("@alice 开始任务", 1, send))

    assert not any(agent_name == "bob" for _, agent_name in sent)
    assert any("已暂停" in text for text, _ in sent)
    assert any(event.type == "run_paused" for event in orchestrator.event_store.read_all())


def test_orchestrator_resume_from_checkpoint(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "@bob 继续处理"))
    registry.register(FakeAgent("bob", "dev", "完成"))
    executor = ShellExecutor(tmp_path)

    async def fake_git_create_branch(branch_name: str):
        return "ok"

    setattr(executor, "git_create_branch", fake_git_create_branch)
    setattr(executor, "git_current_branch", lambda: asyncio.sleep(0, result="main"))
    setattr(executor, "git_changed_files", lambda limit=8: asyncio.sleep(0, result=[]))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        executor,
        event_store=EventStore(tmp_path / "events.jsonl"),
        checkpoint_store=CheckpointStore(tmp_path / "checkpoints.jsonl"),
    )

    sent: list[tuple[str, str | None]] = []

    async def send(text: str, agent_name: str | None = None):
        sent.append((text, agent_name))
        if text.startswith("**[alice]**"):
            orchestrator.pause_task(1, "T-0001")

    asyncio.run(orchestrator.run_chain("@alice 开始任务", 1, send))

    resumed: list[tuple[str, str | None]] = []

    async def send_resume(text: str, agent_name: str | None = None):
        resumed.append((text, agent_name))

    assert asyncio.run(orchestrator.resume_task(1, "T-0001", send_resume)) is True
    assert any(agent_name == "bob" for _, agent_name in resumed)
    assert any(event.type == "run_resumed" for event in orchestrator.event_store.read_all())
