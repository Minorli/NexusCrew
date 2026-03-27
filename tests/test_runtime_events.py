"""Tests for runtime event log persistence and GitHub conversation mirroring."""
import asyncio
from pathlib import Path
import urllib.error

from nexuscrew.agents.base import AgentArtifacts, BaseAgent
from nexuscrew.executor.shell import ShellExecutor
from nexuscrew import github_sync as github_sync_module
from nexuscrew.github_sync import GitHubConversationSync
from nexuscrew.memory.crew_memory import CrewMemory
from nexuscrew.orchestrator import Orchestrator
from nexuscrew.registry import AgentRegistry
from nexuscrew.router import Router
from nexuscrew.runtime.events import RunEvent
from nexuscrew.runtime.store import EventStore


class FakeAgent(BaseAgent):
    def __init__(self, name: str, role: str, reply: str,
                 artifacts: AgentArtifacts | None = None):
        super().__init__(name, role, "test")
        self.reply = reply
        self.artifacts = artifacts or AgentArtifacts()

    async def handle(self, message, history, crew_memory):
        return self.reply, self.artifacts


class FakeGitHubSync:
    def __init__(self):
        self.issues: list[tuple[str, str]] = []
        self.comments: list[tuple[str, str, str]] = []

    async def ensure_task_issue(self, task, initial_message: str = "") -> None:
        if task.github_issue_number:
            return
        task.github_issue_number = 101
        task.github_issue_url = "https://github.example/issues/101"
        self.issues.append((task.id, initial_message))

    async def mirror_comment(self, task, actor: str, body: str) -> None:
        self.comments.append((task.id, actor, body))


async def _direct_to_thread(func, *args, **kwargs):
    return func(*args, **kwargs)


def test_event_store_append_and_filter(tmp_path: Path):
    store = EventStore(tmp_path / "events.jsonl")
    store.append(RunEvent(run_id="r1", chat_id=1, type="run_created", actor="system"))
    store.append(RunEvent(run_id="r1", chat_id=1, type="agent_reply", actor="alice"))
    store.append(RunEvent(run_id="r2", chat_id=1, type="run_created", actor="system"))

    events = store.list_run("r1")

    assert [event.type for event in events] == ["run_created", "agent_reply"]


def test_orchestrator_records_events_and_github_comments(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "任务完成"))
    store = EventStore(tmp_path / "run_events.jsonl")
    github_sync = FakeGitHubSync()
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
        event_store=store,
        github_sync=github_sync,
    )

    sent: list[tuple[str, str | None]] = []

    async def send(text: str, agent_name: str | None = None):
        sent.append((text, agent_name))

    asyncio.run(orchestrator.run_chain("@alice 处理这个需求", 1, send))

    events = store.read_all()
    event_types = [event.type for event in events]
    assert "run_created" in event_types
    assert "task_created" in event_types
    assert "agent_selected" in event_types
    assert "agent_reply" in event_types
    assert "run_completed" in event_types
    assert github_sync.issues == [("T-0001", "@alice 处理这个需求")]
    assert any(actor == "human" for _, actor, _ in github_sync.comments)
    assert any(actor == "alice" for _, actor, _ in github_sync.comments)
    assert sent[-1][1] == "alice"


def test_github_sync_formats_issue_payload(monkeypatch):
    monkeypatch.setattr(github_sync_module.asyncio, "to_thread", _direct_to_thread)
    captured: list[tuple[str, str, dict]] = []

    class CaptureSync(GitHubConversationSync):
        def _request(self, method: str, path: str, payload: dict) -> dict:
            captured.append((method, path, payload))
            if path.endswith("/issues"):
                return {"number": 12, "html_url": "https://example/issues/12"}
            return {"id": 1}

    class Task:
        id = "T-0001"
        description = "修复缓存"
        status = type("Status", (), {"value": "planning"})()
        assigned_to = "alice"
        github_issue_number = 0
        github_issue_url = ""

    sync = CaptureSync("owner/repo", "token", labels=["nexuscrew"])
    asyncio.run(sync.ensure_task_issue(Task(), initial_message="请修复缓存问题"))

    method, path, payload = captured[0]
    assert method == "POST"
    assert path == "/repos/owner/repo/issues"
    assert payload["labels"] == ["nexuscrew"]
    assert "Initial Request" in payload["body"]


def test_github_sync_swallows_422_and_splits_large_comment(monkeypatch):
    monkeypatch.setattr(github_sync_module.asyncio, "to_thread", _direct_to_thread)
    captured: list[tuple[str, str, dict]] = []

    class CaptureSync(GitHubConversationSync):
        def _request(self, method: str, path: str, payload: dict) -> dict:
            captured.append((method, path, payload))
            if path.endswith("/issues"):
                return {"number": 12, "html_url": "https://example/issues/12"}
            if len(captured) == 2:
                raise urllib.error.HTTPError(path, 422, "Unprocessable Entity", {}, None)
            return {"id": len(captured)}

    class Task:
        id = "T-0001"
        description = "修复缓存"
        status = type("Status", (), {"value": "planning"})()
        assigned_to = "alice"
        github_issue_number = 0
        github_issue_url = ""

    sync = CaptureSync("owner/repo", "token")
    large_body = "x" * (GitHubConversationSync.COMMENT_BODY_LIMIT + 10)

    asyncio.run(sync.mirror_comment(Task(), "alice", large_body))

    assert captured[0][1].endswith("/issues")
    assert captured[1][1].endswith("/comments")


def test_github_sync_swallows_network_timeout(monkeypatch):
    monkeypatch.setattr(github_sync_module.asyncio, "to_thread", _direct_to_thread)

    class CaptureSync(GitHubConversationSync):
        def _create_issue(self, task, initial_message: str) -> dict:
            raise urllib.error.URLError("handshake timeout")

    class Task:
        id = "T-0001"
        description = "修复缓存"
        status = type("Status", (), {"value": "planning"})()
        assigned_to = "alice"
        github_issue_number = 0
        github_issue_url = ""

    task = Task()
    sync = CaptureSync("owner/repo", "token")

    asyncio.run(sync.ensure_task_issue(task, initial_message="hello"))

    assert task.github_issue_number == 0
