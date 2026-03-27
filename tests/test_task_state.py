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


def test_task_tracker_latest_active_for_assignee():
    tracker = TaskTracker()
    t1 = tracker.create(1, "任务1")
    t1.assigned_to = "alice"
    t1.transition(TaskStatus.IN_PROGRESS)
    t2 = tracker.create(1, "任务2")
    t2.assigned_to = "bob"
    t3 = tracker.create(1, "任务3")
    t3.assigned_to = "alice"
    t3.transition(TaskStatus.IN_PROGRESS)

    latest = tracker.latest_active_for_assignee(1, "alice")

    assert latest is t3


def test_orchestrator_tracks_review_flow(tmp_path: Path, monkeypatch):
    registry = AgentRegistry()
    registry.register(FakeAgent("bob", "dev", "@dave Code Review"))
    registry.register(FakeAgent("dave", "architect", "LGTM"))
    executor = ShellExecutor(tmp_path)

    async def fake_git_create_branch(branch_name: str):
        return "ok"

    monkeypatch.setattr(executor, "git_create_branch", fake_git_create_branch)
    monkeypatch.setattr(executor, "git_current_branch", lambda: asyncio.sleep(0, result="main"))
    monkeypatch.setattr(executor, "git_changed_files", lambda limit=8: asyncio.sleep(0, result=[]))
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


def test_orchestrator_restores_tasks_from_state_store(tmp_path: Path, monkeypatch):
    registry = AgentRegistry()
    executor = ShellExecutor(tmp_path)
    monkeypatch.setattr(
        executor.state_store,
        "load_tasks",
        lambda: [{
            "chat_id": 1,
            "id": "T-0001",
            "description": "恢复任务",
            "status": "planning",
            "assigned_to": "alice",
            "branch_name": "",
            "github_issue_number": 0,
            "github_issue_url": "",
            "github_pr_number": 0,
            "github_pr_url": "",
            "slack_channel": "",
            "slack_message_ts": "",
            "slack_thread_ts": "",
            "created_at": "2026-03-26T00:00:00",
            "updated_at": "",
            "history": ["restored"],
        }],
    )

    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        executor,
    )

    task = orchestrator.task_tracker.get(1, "T-0001")
    assert task is not None
    assert task.description == "恢复任务"
    assert task.history == ["restored"]
