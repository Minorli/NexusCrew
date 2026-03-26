"""Tests for Slack task-thread synchronization."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

from nexuscrew.agents.base import AgentArtifacts, BaseAgent
from nexuscrew.executor.shell import ShellExecutor
from nexuscrew.memory.crew_memory import CrewMemory
from nexuscrew.orchestrator import Orchestrator
from nexuscrew.registry import AgentRegistry
from nexuscrew.router import Router
from nexuscrew.slack import sync as slack_sync_module
from nexuscrew.slack.sync import SlackConversationSync


class FakeAgent(BaseAgent):
    def __init__(self, name: str, role: str, reply: str):
        super().__init__(name, role, "test")
        self.reply = reply

    async def handle(self, message, history, crew_memory):
        return self.reply, AgentArtifacts()


class FakeSlackSync:
    def __init__(self):
        self.roots: list[tuple[str, str]] = []
        self.comments: list[tuple[str, str, str]] = []

    async def ensure_task_thread(self, task, initial_message: str = "") -> None:
        if task.slack_thread_ts:
            return
        task.slack_channel = "C1"
        task.slack_message_ts = "1.0"
        task.slack_thread_ts = "1.0"
        self.roots.append((task.id, initial_message))

    async def mirror_comment(self, task, actor: str, body: str) -> None:
        self.comments.append((task.id, actor, body))


async def _direct_to_thread(func, *args, **kwargs):
    return func(*args, **kwargs)


def test_slack_sync_creates_root_and_thread(monkeypatch):
    monkeypatch.setattr(slack_sync_module.asyncio, "to_thread", _direct_to_thread)
    captured: list[tuple[str, str, str | None]] = []

    class FakeClient:
        def post_message(self, channel: str, text: str, thread_ts: str | None = None):
            captured.append((channel, text, thread_ts))
            return {"ok": True, "channel": channel, "ts": "1.23"}

    class Task:
        id = "T-0001"
        description = "实现缓存"
        status = SimpleNamespace(value="planning")
        assigned_to = "alice"
        slack_channel = ""
        slack_message_ts = ""
        slack_thread_ts = ""

    sync = SlackConversationSync(token="xoxb-test", default_channel="C1")
    sync.client = FakeClient()

    task = Task()
    asyncio.run(sync.ensure_task_thread(task, "初始任务"))
    asyncio.run(sync.mirror_comment(task, "alice", "处理中"))

    assert captured[0][0] == "C1"
    assert "NexusCrew" in captured[0][1]
    assert captured[1][2] == "1.23"


def test_orchestrator_mirrors_task_to_slack(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "任务完成"))
    slack_sync = FakeSlackSync()
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
        slack_sync=slack_sync,
    )

    async def send(text: str, agent_name: str | None = None):
        return None

    asyncio.run(orchestrator.run_chain("@alice 处理任务", 1, send))

    assert slack_sync.roots == [("T-0001", "@alice 处理任务")]
    assert any(actor == "human" for _, actor, _ in slack_sync.comments)
    assert any(actor == "alice" for _, actor, _ in slack_sync.comments)
