"""Tests for recovery manager and GitHub webhook ingestion."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

from nexuscrew.agents.base import AgentArtifacts, BaseAgent
from nexuscrew.executor.shell import ShellExecutor
from nexuscrew.memory.crew_memory import CrewMemory
from nexuscrew.orchestrator import Orchestrator
from nexuscrew.registry import AgentRegistry
from nexuscrew.router import Router
from nexuscrew.runtime.recovery import RecoveryManager
from nexuscrew.runtime.runner import BackgroundRun


class FakeAgent(BaseAgent):
    def __init__(self, name: str, role: str, reply: str):
        super().__init__(name, role, "test")
        self.reply = reply

    async def handle(self, message, history, crew_memory):
        return self.reply, AgentArtifacts()


def test_recovery_manager_recovers_interrupted_job(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    executor = ShellExecutor(tmp_path)
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        executor,
    )
    task = orchestrator.task_tracker.create(1, "demo")
    orchestrator._task_run_ids[(1, task.id)] = "run-1"
    runner = __import__("nexuscrew.runtime.runner", fromlist=["BackgroundTaskRunner"]).BackgroundTaskRunner(executor.state_store)
    runner._jobs["BG-0001"] = BackgroundRun(
        id="BG-0001",
        label="demo",
        status="interrupted",
        chat_id=1,
        task_id=task.id,
        run_id="run-1",
    )
    runner._counter = 1
    runner._counter = 1

    recovered_jobs: list[str] = []

    def send_factory(chat_id: int):
        async def send(text: str, agent_name: str | None = None):
            return None
        return send

    original_resume = runner.resume_existing

    def capture_resume(job_id, coro):
        recovered_jobs.append(job_id)
        return original_resume(job_id, coro)

    runner.resume_existing = capture_resume

    asyncio.run(RecoveryManager(runner, orchestrator).recover(send_factory))

    assert recovered_jobs == ["BG-0001"]
    assert runner.get("BG-0001").status in ("running", "completed", "failed", "cancelled")


def test_recovery_manager_marks_failed_resume(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    executor = ShellExecutor(tmp_path)
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        executor,
    )
    task = orchestrator.task_tracker.create(1, "demo")
    orchestrator._task_run_ids[(1, task.id)] = "run-1"
    runner = __import__("nexuscrew.runtime.runner", fromlist=["BackgroundTaskRunner"]).BackgroundTaskRunner(executor.state_store)
    runner._jobs["BG-0001"] = BackgroundRun(
        id="BG-0001",
        label="demo",
        status="interrupted",
        chat_id=1,
        task_id=task.id,
        run_id="run-1",
    )
    runner._counter = 1

    async def fake_resume(chat_id, task_id, send):
        return False

    async def fake_replay(chat_id, task_id, send):
        return False

    orchestrator.resume_task = fake_resume
    orchestrator.replay_task = fake_replay

    def send_factory(chat_id: int):
        async def send(text: str, agent_name: str | None = None):
            return None
        return send

    async def main():
        await RecoveryManager(runner, orchestrator).recover(send_factory)
        await asyncio.sleep(0)
        await asyncio.sleep(0)

    asyncio.run(main())

    assert runner.get("BG-0001").status == "failed"


def test_recovery_manager_falls_back_to_replay(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    executor = ShellExecutor(tmp_path)
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        executor,
    )
    task = orchestrator.task_tracker.create(1, "demo")
    orchestrator._task_run_ids[(1, task.id)] = "run-1"
    runner = __import__("nexuscrew.runtime.runner", fromlist=["BackgroundTaskRunner"]).BackgroundTaskRunner(executor.state_store)
    runner._jobs["BG-0001"] = BackgroundRun(
        id="BG-0001",
        label="demo",
        status="interrupted",
        chat_id=1,
        task_id=task.id,
        run_id="run-1",
    )
    runner._counter = 1

    async def fake_resume(chat_id, task_id, send):
        return False

    async def fake_replay(chat_id, task_id, send):
        return True

    orchestrator.resume_task = fake_resume
    orchestrator.replay_task = fake_replay

    def send_factory(chat_id: int):
        async def send(text: str, agent_name: str | None = None):
            return None
        return send

    asyncio.run(RecoveryManager(runner, orchestrator).recover(send_factory))

    assert runner.get("BG-0001").status in ("running", "completed")


def test_orchestrator_ingests_github_pr_and_ci_events(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )
    task = orchestrator.task_tracker.create(1, "demo")
    task.github_pr_number = 12
    task.github_pr_url = "https://example/pr/12"

    orchestrator.ingest_github_event("pull_request", {
        "action": "closed",
        "pull_request": {"number": 12, "html_url": "https://example/pr/12", "merged": True},
    })
    orchestrator.ingest_github_event("check_suite", {
        "check_suite": {"status": "completed", "conclusion": "success", "pull_requests": [{"number": 12}]},
    })

    assert task.status.value == "done"
    assert orchestrator.ci_summary(1, task.id).startswith("CI: passed")


def test_orchestrator_ingests_review_and_issue_comment_events(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )
    task = orchestrator.task_tracker.create(1, "demo")
    task.github_pr_number = 12
    task.github_issue_number = 34

    orchestrator.ingest_github_event("pull_request_review", {
        "review": {"state": "approved"},
        "pull_request": {"number": 12},
    })
    orchestrator.ingest_github_event("pull_request_review_comment", {
        "action": "created",
        "pull_request": {"number": 12},
    })
    orchestrator.ingest_github_event("issue_comment", {
        "action": "created",
        "issue": {"number": 34},
    })

    assert task.status.value == "accepted"
    summaries = orchestrator.artifact_store.format_for_task(task.id)
    assert "github_review_event" in summaries or "review approved" in summaries


def test_orchestrator_ingests_pull_request_lifecycle_actions(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )
    task = orchestrator.task_tracker.create(1, "demo")
    task.github_pr_number = 12

    orchestrator.ingest_github_event("pull_request", {
        "action": "ready_for_review",
        "pull_request": {"number": 12, "html_url": "https://example/pr/12"},
    })
    assert task.status.value == "reviewing"

    orchestrator.ingest_github_event("pull_request", {
        "action": "converted_to_draft",
        "pull_request": {"number": 12, "html_url": "https://example/pr/12"},
    })
    assert task.status.value == "in_progress"
