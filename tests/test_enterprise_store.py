"""Tests for SQLite-backed durable runtime state."""
import asyncio
from pathlib import Path

from nexuscrew.policy.approval import ApprovalManager
from nexuscrew.policy.risk import RiskLevel
from nexuscrew.runtime.events import RunEvent
from nexuscrew.runtime.runner import BackgroundTaskRunner
from nexuscrew.runtime.sqlite_store import DurableStateStore
from nexuscrew.runtime.checkpoints import RunCheckpoint


def test_durable_state_store_events_and_checkpoints(tmp_path: Path):
    store = DurableStateStore(tmp_path / ".nexuscrew_state.db")
    event = RunEvent(
        run_id="run-1",
        chat_id=1,
        task_id="T-0001",
        type="run_created",
        actor="system",
        payload={"message": "hello"},
    )
    checkpoint = RunCheckpoint(
        run_id="run-1",
        chat_id=1,
        task_id="T-0001",
        hop=1,
        current_agent="alice",
        current_message="next",
        history=[{"agent": "human", "content": "hello"}],
    )

    store.append_event(event)
    store.save_checkpoint(checkpoint)

    assert store.list_run_events("run-1")[0]["type"] == "run_created"
    latest = store.load_latest_checkpoint("run-1")
    assert latest is not None
    assert latest["current_agent"] == "alice"


def test_approval_manager_recovers_from_sqlite(tmp_path: Path):
    store = DurableStateStore(tmp_path / ".nexuscrew_state.db")
    manager = ApprovalManager(state_store=store)
    approval = manager.create_request("shell", RiskLevel.HIGH, "rm -rf", {"code": "rm -rf"})

    recovered = ApprovalManager(state_store=store)

    assert recovered.get(approval.id) is not None
    assert recovered.get(approval.id).status == "pending"


def test_background_runner_recovers_interrupted_jobs(tmp_path: Path):
    store = DurableStateStore(tmp_path / ".nexuscrew_state.db")
    runner = BackgroundTaskRunner(state_store=store)

    async def work():
        await asyncio.sleep(0)

    async def main():
        job_id = runner.submit("demo", work())
        await asyncio.sleep(0)
        return job_id

    job_id = asyncio.run(main())

    recovered = BackgroundTaskRunner(state_store=store)
    job = recovered.get(job_id)

    assert job is not None
    assert job.status in ("interrupted", "completed")


def test_durable_state_store_webhook_delivery_dedupe(tmp_path: Path):
    store = DurableStateStore(tmp_path / ".nexuscrew_state.db")

    assert store.has_webhook_delivery("github", "d1") is False
    store.save_webhook_delivery("github", "d1", "pull_request", "2026-03-25T00:00:00")
    assert store.has_webhook_delivery("github", "d1") is True
