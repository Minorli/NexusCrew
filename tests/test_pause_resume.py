"""Tests for pause/resume/replay task controls."""
import asyncio
from pathlib import Path

from nexuscrew.agents.base import AgentArtifacts, BaseAgent
from nexuscrew.artifacts import ArtifactRecord
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


def test_orchestrator_resume_without_checkpoint_uses_continuation(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "继续推进"))
    executor = ShellExecutor(tmp_path)
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        executor,
        event_store=EventStore(tmp_path / "events.jsonl"),
        checkpoint_store=CheckpointStore(tmp_path / "checkpoints.jsonl"),
    )
    task = orchestrator.task_tracker.create(1, "处理需求")
    task.assigned_to = "alice"
    orchestrator._task_run_ids[(1, "T-0001")] = "run-1"
    orchestrator.artifact_store.append(
        ArtifactRecord(
            task_id="T-0001",
            run_id="run-1",
            type="continuation_checkpoint",
            source="system",
            summary="continuation:in_progress",
            content="\n".join(
                [
                    "Goal: 处理需求",
                    "Session: chat:1:task:T-0001",
                    "Current State: in_progress",
                    "Owner: @alice",
                    "Family: T-0001",
                    "Next Action: request human decision",
                    "Constraints: blocked=human_input_required",
                    "Artifacts: route=(none)",
                    "Stop Conditions: stop until human_input_required is resolved",
                ]
            ),
        )
    )

    resumed: list[tuple[str, str | None]] = []

    async def send_resume(text: str, agent_name: str | None = None):
        resumed.append((text, agent_name))

    assert asyncio.run(orchestrator.resume_task(1, "T-0001", send_resume)) is True
    assert any(agent_name == "alice" for _, agent_name in resumed)
    assert any(event.type == "run_resumed" for event in orchestrator.event_store.read_all())


def test_orchestrator_replay_prefers_continuation_owner_and_next_action(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("dave", "architect", "LGTM"))
    executor = ShellExecutor(tmp_path)
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        executor,
    )
    task = orchestrator.task_tracker.create(1, "处理需求")
    task.assigned_to = "dave"
    orchestrator.artifact_store.append(
        ArtifactRecord(
            task_id="T-0001",
            run_id="run-1",
            type="continuation_checkpoint",
            source="system",
            summary="continuation:review_requested",
            content="\n".join(
                [
                    "Goal: 处理需求",
                    "Session: chat:1:task:T-0001",
                    "Current State: review_requested",
                    "Owner: @dave",
                    "Family: T-0001",
                    "Next Action: architect review",
                    "Constraints: blocked=(none)",
                    "Artifacts: route=(none)",
                    "Stop Conditions: continue until next gate or explicit blocker",
                ]
            ),
        )
    )

    replayed: list[tuple[str, str | None]] = []

    async def send_replay(text: str, agent_name: str | None = None):
        replayed.append((text, agent_name))

    assert asyncio.run(orchestrator.replay_task(1, "T-0001", send_replay)) is True
    assert any(agent_name == "dave" for _, agent_name in replayed)
