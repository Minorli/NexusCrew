"""Tests for asynchronous HR evaluation triggers."""
import asyncio
from pathlib import Path

from nexuscrew.agents.base import AgentArtifacts, BaseAgent
from nexuscrew.executor.shell import ShellExecutor
from nexuscrew.memory.crew_memory import CrewMemory
from nexuscrew.orchestrator import Orchestrator
from nexuscrew.registry import AgentRegistry
from nexuscrew.router import Router
from nexuscrew import orchestrator as orchestrator_module


class FakeAgent(BaseAgent):
    def __init__(self, name: str, role: str, reply: str,
                 artifacts: AgentArtifacts | None = None):
        super().__init__(name, role, "test")
        self.reply = reply
        self.artifacts = artifacts or AgentArtifacts()

    async def handle(self, message, history, crew_memory):
        return self.reply, self.artifacts


async def _run(orchestrator, send):
    await orchestrator.run_chain("@alice 处理任务", 1, send)


def test_orchestrator_triggers_async_hr_evaluation(tmp_path: Path, monkeypatch):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "任务完成"))
    registry.register(FakeAgent(
        "carol",
        "hr",
        "评分 3.5",
        AgentArtifacts(memory_note="绩效快照"),
    ))
    memory = CrewMemory(tmp_path / "crew_memory.md")
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        memory,
        ShellExecutor(tmp_path),
    )

    sent: list[tuple[str, str | None]] = []
    tasks: list[asyncio.Task] = []
    real_create_task = asyncio.create_task

    def tracking_create_task(coro):
        task = real_create_task(coro)
        tasks.append(task)
        return task

    monkeypatch.setattr(orchestrator_module.asyncio, "create_task", tracking_create_task)

    async def send(text: str, agent_name: str | None = None):
        sent.append((text, agent_name))

    async def main():
        await _run(orchestrator, send)
        await asyncio.gather(*tasks)

    asyncio.run(main())

    assert any("绩效评估" in text for text, _ in sent)
    assert any(agent_name == "carol" for _, agent_name in sent)
    assert "绩效快照" in memory.read(tail_lines=20)
