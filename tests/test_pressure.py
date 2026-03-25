"""Tests for HR pressure prompt injection."""
from nexuscrew.agents.base import AgentArtifacts, BaseAgent
from nexuscrew.hr.pressure import apply_pressure, build_pressure_prompt, calculate_pressure_level
from nexuscrew.memory.crew_memory import CrewMemory
from nexuscrew.metrics import AgentMetrics
from nexuscrew.orchestrator import Orchestrator
from nexuscrew.registry import AgentRegistry
from nexuscrew.router import Router
from nexuscrew.executor.shell import ShellExecutor


class FakeAgent(BaseAgent):
    async def handle(self, message, history, crew_memory):
        return "ok", AgentArtifacts()


def test_calculate_pressure_level_examples():
    assert calculate_pressure_level(3.25, [3.5, 3.25]) == 2
    assert calculate_pressure_level(3.25, [3.25, 3.25, 3.25]) == 4


def test_apply_pressure_writes_memory_and_respects_max_len(tmp_path):
    memory = CrewMemory(tmp_path / "crew_memory.md")
    metrics = AgentMetrics(tasks_assigned=5, tasks_completed=3, total_retries=10)

    apply_pressure(memory, "bob", 2, metrics, peer_feedback="需要更主动", max_len=120)

    text = memory.read(tail_lines=20)
    assert "## HR通知-bob" in text
    prompt = build_pressure_prompt("bob", 2, metrics, peer_feedback="需要更主动", max_len=120)
    assert len(prompt) <= 120


def test_refresh_pressure_notices_updates_shared_memory(tmp_path):
    registry = AgentRegistry()
    registry.register(FakeAgent("bob", "dev", "test"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
        pressure_max_prompt_len=120,
    )

    metrics = orchestrator.metrics.get("bob")
    metrics.record_task_start()
    metrics.record_task_complete(1000)
    metrics.record_task_fail()
    metrics.record_review_result(False)

    orchestrator._refresh_pressure_notices()

    text = orchestrator.crew_memory.read(tail_lines=30)
    assert "## HR通知-bob" in text
    assert "HR 绩效通知" in text
