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

    assert task.session_key == "chat:1:task:T-0001"
    assert task.transition(TaskStatus.IN_PROGRESS) is True
    assert task.transition(TaskStatus.DONE) is False
    assert "T-0001" in tracker.format_status(1)


def test_task_tracker_format_status_distinguishes_runtime_states():
    tracker = TaskTracker()
    t1 = tracker.create(1, "执行中的任务")
    t1.assigned_to = "alice"
    t1.transition(TaskStatus.IN_PROGRESS)
    t2 = tracker.create(1, "等待中的任务")
    t2.assigned_to = "bob"
    t2.transition(TaskStatus.IN_PROGRESS)
    t3 = tracker.create(1, "陈旧任务")
    t3.assigned_to = "carol"
    t3.transition(TaskStatus.IN_PROGRESS)
    t3.created_at = "2026-03-26T00:00:00"
    t3.updated_at = "2026-03-26T00:00:00"

    text = tracker.format_status(
        1,
        inflight_task_ids={"T-0001"},
        waiting_task_ids={"T-0002"},
        task_stage_sla_seconds=1,
    )

    assert "🚧 [T-0001]" in text
    assert "(inflight)" in text
    assert "⏸️ [T-0002]" in text
    assert "(waiting)" in text
    assert "🕸️ [T-0003]" in text
    assert "(stale)" in text


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


def test_task_tracker_finds_related_active_task_by_message_overlap():
    tracker = TaskTracker()
    t1 = tracker.create(1, "排查并修复 dev-01 零交付根因")
    t1.assigned_to = "nexus-dev-02"
    t1.transition(TaskStatus.IN_PROGRESS)
    t1.history.append("human_follow_up: 请继续看 dev-01 的零交付问题")

    t2 = tracker.create(1, "无关任务")
    t2.assigned_to = "nexus-dev-01"
    t2.transition(TaskStatus.IN_PROGRESS)

    related = tracker.find_related_active(
        1,
        "请继续排查 dev-01 的零交付根因，不要重开新任务",
        preferred_assignee="nexus-dev-02",
    )

    assert related is t1


def test_task_tracker_format_status_includes_family_summary():
    tracker = TaskTracker()
    parent = tracker.create(1, "父任务")
    parent.assigned_to = "alice"
    parent.transition(TaskStatus.IN_PROGRESS)
    child = tracker.create(1, "子任务", parent_task=parent)
    child.assigned_to = "bob"
    child.transition(TaskStatus.IN_PROGRESS)

    text = tracker.format_status(1)

    assert "🧬 Task Families：" in text
    assert "[T-0001] active / T-0001, T-0002" in text


def test_task_tracker_family_rollups_report_blocked_state():
    tracker = TaskTracker()
    parent = tracker.create(1, "父任务")
    parent.assigned_to = "alice"
    parent.transition(TaskStatus.IN_PROGRESS)
    child = tracker.create(1, "子任务", parent_task=parent)
    child.assigned_to = "bob"
    child.transition(TaskStatus.IN_PROGRESS)
    child.blocked_reason = "approval_required"

    rollups = tracker.family_rollups(1)

    assert rollups[0]["family_id"] == "T-0001"
    assert rollups[0]["state"] == "blocked"
    assert rollups[0]["blocked_reasons"] == ["approval_required"]


def test_task_tracker_family_rollups_include_single_active_task():
    tracker = TaskTracker()
    task = tracker.create(1, "单任务")
    task.assigned_to = "alice"
    task.transition(TaskStatus.IN_PROGRESS)

    rollups = tracker.family_rollups(1)

    assert rollups[0]["family_id"] == "T-0001"
    assert rollups[0]["state"] == "active"


def test_task_tracker_agent_queue_sorts_blocked_before_waiting():
    tracker = TaskTracker()
    t1 = tracker.create(1, "blocked")
    t1.assigned_to = "alice"
    t1.transition(TaskStatus.IN_PROGRESS)
    t1.blocked_reason = "approval_required"
    t2 = tracker.create(1, "waiting")
    t2.assigned_to = "alice"
    t2.transition(TaskStatus.IN_PROGRESS)

    queue = tracker.agent_queue(1, "alice", waiting_task_ids={"T-0002"})

    assert [item["task_id"] for item in queue] == ["T-0001", "T-0002"]


def test_task_tracker_session_rollups_report_partial_completion():
    tracker = TaskTracker()
    parent = tracker.create(1, "父任务")
    parent.transition(TaskStatus.IN_PROGRESS)
    child = tracker.create(1, "子任务", parent_task=parent)
    child.transition(TaskStatus.IN_PROGRESS)
    child.transition(TaskStatus.REVIEW_REQ)
    child.transition(TaskStatus.REVIEWING)
    child.transition(TaskStatus.ACCEPTED)
    child.transition(TaskStatus.VALIDATING)
    child.transition(TaskStatus.DONE)

    rollups = tracker.session_rollups(1)

    assert rollups[0]["session_key"] == "chat:1:task:T-0001"
    assert rollups[0]["completion_state"] == "partial"


def test_task_tracker_session_rollups_include_single_active_task():
    tracker = TaskTracker()
    task = tracker.create(1, "单任务")
    task.transition(TaskStatus.IN_PROGRESS)

    rollups = tracker.session_rollups(1)

    assert rollups[0]["session_key"] == "chat:1:task:T-0001"
    assert rollups[0]["state"] == "active"


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
            "session_key": "chat:1:task:T-0001",
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
    assert task.session_key == "chat:1:task:T-0001"
    assert task.history == ["restored"]
