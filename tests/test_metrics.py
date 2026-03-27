"""Tests for metrics collection and orchestrator integration."""
from pathlib import Path

from nexuscrew.agents.base import AgentArtifacts, BaseAgent
from nexuscrew.executor.shell import ShellExecutor
from nexuscrew.memory.crew_memory import CrewMemory
from nexuscrew.metrics import AgentMetrics, MetricsCollector
from nexuscrew.orchestrator import Orchestrator
from nexuscrew.registry import AgentRegistry
from nexuscrew.router import Router


class FakeAgent(BaseAgent):
    def __init__(self, name: str, role: str, reply: str,
                 artifacts: AgentArtifacts | None = None):
        super().__init__(name, role, "test")
        self.reply = reply
        self.artifacts = artifacts or AgentArtifacts()

    async def handle(self, message, history, crew_memory):
        return self.reply, self.artifacts


def test_agent_metrics_summary_and_rates():
    metrics = AgentMetrics()
    metrics.record_task_start()
    metrics.record_task_complete(1500)
    metrics.record_review_result(True)
    metrics.record_memory_note()

    assert metrics.completion_rate == 1.0
    assert metrics.first_pass_rate == 1.0
    assert metrics.avg_response_time_s == 1.5
    assert "任务: 1/1" in metrics.to_summary()


def test_metrics_collector_formats_markdown():
    collector = MetricsCollector()
    alice = collector.get("alice")
    alice.record_task_start()
    alice.record_task_complete(1000)

    table = collector.all_summaries()

    assert "| Agent | 完成率 |" in table
    assert "| alice | 100% |" in table


def test_orchestrator_collects_dev_shell_failure_metrics(tmp_path: Path, monkeypatch):
    registry = AgentRegistry()
    registry.register(FakeAgent(
        "bob",
        "dev",
        "done",
        AgentArtifacts(shell_output="error: command failed"),
    ))
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

    sent: list[tuple[str, str | None]] = []

    async def send(text: str, agent_name: str | None = None):
        sent.append((text, agent_name))

    import asyncio
    asyncio.run(orchestrator.run_chain("@bob 修复", 1, send))

    metrics = orchestrator.metrics.get("bob")
    assert metrics.tasks_assigned == 1
    assert metrics.tasks_completed == 1
    assert metrics.tasks_failed == 1
    assert metrics.total_retries == 1
    assert metrics.shell_commands_run == 1
    assert metrics.shell_failures == 1
    assert sent[-1][1] == "bob"


def test_orchestrator_records_architect_review_result(tmp_path: Path, monkeypatch):
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

    import asyncio
    asyncio.run(orchestrator.run_chain("@bob 修复", 1, send))

    metrics = orchestrator.metrics.get("bob")
    assert metrics.review_pass_first == 1
    assert metrics.review_reject == 0
