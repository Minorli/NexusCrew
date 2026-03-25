"""Tests for metrics history persistence."""
import asyncio
from pathlib import Path

from nexuscrew.agents.base import AgentArtifacts, BaseAgent
from nexuscrew.hr.metrics_store import MetricsStore
from nexuscrew.metrics import AgentMetrics
from nexuscrew.executor.shell import ShellExecutor
from nexuscrew.memory.crew_memory import CrewMemory
from nexuscrew.orchestrator import Orchestrator
from nexuscrew.registry import AgentRegistry
from nexuscrew.router import Router
from nexuscrew import orchestrator as orchestrator_module


class FakeAgent(BaseAgent):
    def __init__(self, name: str, role: str, reply: str):
        super().__init__(name, role, "test")
        self.reply = reply

    async def handle(self, message, history, crew_memory):
        return self.reply, AgentArtifacts()


def test_metrics_store_appends_and_reads_history(tmp_path: Path):
    store = MetricsStore(tmp_path / "metrics_history.jsonl")
    metrics = AgentMetrics()
    metrics.record_task_start()
    metrics.record_task_complete(1000)

    store.append_snapshot(1, "alice", 3.5, metrics)
    store.append_snapshot(2, "alice", 3.25, metrics)

    assert len(store.read_history("alice")) == 2
    assert store.get_score_history("alice") == [3.5, 3.25]


def test_hr_evaluation_persists_snapshots(tmp_path: Path, monkeypatch):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "任务完成"))
    registry.register(FakeAgent("carol", "hr", "评分 3.5"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )

    tasks: list[asyncio.Task] = []
    real_create_task = asyncio.create_task

    def tracking_create_task(coro):
        task = real_create_task(coro)
        tasks.append(task)
        return task

    monkeypatch.setattr(orchestrator_module.asyncio, "create_task", tracking_create_task)

    async def send(text: str, agent_name: str | None = None):
        return None

    async def main():
        await orchestrator.run_chain("@alice 处理任务", 1, send)
        await asyncio.gather(*tasks)

    asyncio.run(main())

    history_path = tmp_path / "metrics_history.jsonl"
    assert history_path.exists()
    assert "alice" in history_path.read_text(encoding="utf-8")
