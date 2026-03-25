"""Tests for task state machine and status board integration."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

from nexuscrew.agents.base import AgentArtifacts, BaseAgent
from nexuscrew.executor.shell import ShellExecutor
from nexuscrew.memory.crew_memory import CrewMemory
from nexuscrew.orchestrator import Orchestrator
from nexuscrew.registry import AgentRegistry
from nexuscrew.router import Router
from nexuscrew.task_state import TaskStatus, TaskTracker
from nexuscrew.telegram.bot import NexusCrewBot


class FakeAgent(BaseAgent):
    def __init__(self, name: str, role: str, reply: str):
        super().__init__(name, role, "test")
        self.reply = reply

    async def handle(self, message, history, crew_memory):
        return self.reply, AgentArtifacts()


def test_task_transitions_and_status_board():
    tracker = TaskTracker()
    task = tracker.create(1, "实现缓存")

    assert task.transition(TaskStatus.IN_PROGRESS) is True
    assert task.transition(TaskStatus.DONE) is False
    assert "T-0001" in tracker.format_status(1)


def test_orchestrator_tracks_review_flow(tmp_path: Path, monkeypatch):
    registry = AgentRegistry()
    registry.register(FakeAgent("bob", "dev", "@dave Code Review"))
    registry.register(FakeAgent("dave", "architect", "LGTM"))
    executor = ShellExecutor(tmp_path)

    async def fake_git_create_branch(branch_name: str):
        return "ok"

    monkeypatch.setattr(executor, "git_create_branch", fake_git_create_branch)
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        executor,
    )

    async def send(text: str, agent_name: str | None = None):
        return None

    asyncio.run(orchestrator.run_chain("@bob 修复问题", 1, send))

    task = orchestrator.task_tracker.latest_active(1)
    assert task is not None
    assert task.status == TaskStatus.ACCEPTED
    assert task.assigned_to == "dave"


def test_status_command_includes_task_board(monkeypatch):
    bot = NexusCrewBot()
    bot._orch = SimpleNamespace(format_status=lambda chat_id: "📋 活跃任务：\n\n  🔨 [T-0001] demo -> @alice")

    replies: list[str] = []

    class FakeMessage:
        chat_id = 123

        async def reply_text(self, text: str):
            replies.append(text)

    update = SimpleNamespace(message=FakeMessage())
    context = SimpleNamespace(args=[])

    asyncio.run(bot.cmd_status(update, context))

    assert "📋 活跃任务" in replies[0]
