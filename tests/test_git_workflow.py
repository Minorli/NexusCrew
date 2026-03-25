"""Tests for git workflow helpers and orchestration branch creation."""
import asyncio
from pathlib import Path

from nexuscrew.agents.base import AgentArtifacts, BaseAgent
from nexuscrew.executor import shell as shell_module
from nexuscrew.executor.shell import ShellExecutor
from nexuscrew.memory.crew_memory import CrewMemory
from nexuscrew.orchestrator import Orchestrator
from nexuscrew.registry import AgentRegistry
from nexuscrew.router import Router


class FakeAgent(BaseAgent):
    def __init__(self, name: str, role: str, reply: str):
        super().__init__(name, role, "test")
        self.reply = reply

    async def handle(self, message, history, crew_memory):
        return self.reply, AgentArtifacts()


async def _direct_to_thread(func, *args, **kwargs):
    return func(*args, **kwargs)


def test_shell_executor_git_current_branch_returns_unknown(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(shell_module.asyncio, "to_thread", _direct_to_thread)
    executor = ShellExecutor(tmp_path)

    assert asyncio.run(executor.git_current_branch()) == "unknown"


def test_orchestrator_creates_feature_branch_for_dev_task(tmp_path: Path, monkeypatch):
    registry = AgentRegistry()
    registry.register(FakeAgent("bob", "dev", "done"))
    executor = ShellExecutor(tmp_path)
    created: list[str] = []

    async def fake_git_create_branch(branch_name: str):
        created.append(branch_name)
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

    asyncio.run(orchestrator.run_chain("@bob 修复缓存逻辑", 1, send))

    assert created == ["feat/t-0001-bob"]
