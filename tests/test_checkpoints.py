"""Tests for runtime checkpoint persistence."""
import asyncio
from pathlib import Path

from nexuscrew.agents.base import AgentArtifacts, BaseAgent
from nexuscrew.executor.shell import ShellExecutor
from nexuscrew.memory.crew_memory import CrewMemory
from nexuscrew.orchestrator import Orchestrator
from nexuscrew.registry import AgentRegistry
from nexuscrew.router import Router
from nexuscrew.runtime.checkpoints import CheckpointStore, RunCheckpoint
from nexuscrew.runtime.store import EventStore


class FakeAgent(BaseAgent):
    def __init__(self, name: str, role: str, reply: str):
        super().__init__(name, role, "test")
        self.reply = reply

    async def handle(self, message, history, crew_memory):
        return self.reply, AgentArtifacts()


def test_checkpoint_store_loads_latest(tmp_path: Path):
    store = CheckpointStore(tmp_path / "checkpoints.jsonl")
    store.save(RunCheckpoint(
        run_id="r1",
        chat_id=1,
        task_id="T-0001",
        hop=0,
        current_agent="alice",
        current_message="first",
    ))
    store.save(RunCheckpoint(
        run_id="r1",
        chat_id=1,
        task_id="T-0001",
        hop=1,
        current_agent="bob",
        current_message="second",
    ))

    latest = store.load_latest("r1")

    assert latest is not None
    assert latest.hop == 1
    assert latest.current_agent == "bob"


def test_orchestrator_saves_checkpoint_per_run(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "任务完成"))
    checkpoint_store = CheckpointStore(tmp_path / "run_checkpoints.jsonl")
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
        event_store=EventStore(tmp_path / "run_events.jsonl"),
        checkpoint_store=checkpoint_store,
    )

    async def send(text: str, agent_name: str | None = None):
        return None

    asyncio.run(orchestrator.run_chain("@alice 处理任务", 1, send))

    checkpoints = checkpoint_store.read_all()
    assert checkpoints
    latest = checkpoints[-1]
    assert latest.task_id == "T-0001"
    assert latest.current_agent == "alice"
    assert latest.task_status
