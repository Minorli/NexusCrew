"""Tests for background task runner and Telegram task commands."""
import asyncio
from types import SimpleNamespace

from nexuscrew.runtime.runner import BackgroundTaskRunner
from nexuscrew.telegram.bot import NexusCrewBot


def test_background_runner_tracks_completed_job():
    runner = BackgroundTaskRunner()

    async def work():
        return None

    async def main():
        job_id = runner.submit("demo", work())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return job_id

    job_id = asyncio.run(main())
    job = runner.get(job_id)
    assert job is not None
    assert job.status == "completed"


def test_background_runner_can_finish_in_waiting_state():
    runner = BackgroundTaskRunner()

    async def work():
        return None

    async def on_complete(job):
        return "waiting"

    async def main():
        job_id = runner.submit("demo", work(), on_complete=on_complete)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return job_id

    job_id = asyncio.run(main())
    job = runner.get(job_id)
    assert job is not None
    assert job.status == "waiting"


def test_background_runner_waiting_job_is_not_treated_as_inflight():
    runner = BackgroundTaskRunner()

    async def work():
        return None

    async def on_complete(job):
        return "waiting"

    async def main():
        job_id = runner.submit("demo", work(), task_id="T-0001", on_complete=on_complete)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return job_id

    job_id = asyncio.run(main())
    assert runner.get(job_id).status == "waiting"
    assert runner.active_task_ids() == set()
    assert "待续任务" in runner.format_status()


def test_background_runner_cancel_job():
    runner = BackgroundTaskRunner()

    async def work():
        await asyncio.sleep(10)

    async def main():
        job_id = runner.submit("demo", work())
        await asyncio.sleep(0)
        cancelled = await runner.cancel(job_id)
        return job_id, cancelled

    job_id, cancelled = asyncio.run(main())
    assert cancelled is True
    assert runner.get(job_id).status == "cancelled"


def test_cmd_tasks_and_cmd_cancel():
    bot = NexusCrewBot()
    bot._runner._jobs["BG-0001"] = SimpleNamespace(
        id="BG-0001",
        label="demo",
        status="running",
    )

    replies: list[str] = []

    class FakeMessage:
        chat_id = 1

        async def reply_text(self, text: str):
            replies.append(text)

    update = SimpleNamespace(message=FakeMessage())
    context = SimpleNamespace(args=[])

    asyncio.run(bot.cmd_tasks(update, context))

    assert "BG-0001" in replies[0]


def test_cmd_tasks_hides_failed_jobs_by_default():
    bot = NexusCrewBot()
    bot._runner._jobs["BG-0001"] = SimpleNamespace(
        id="BG-0001",
        label="broken",
        status="failed",
        error="boom",
        task_id="T-0001",
        updated_at="2026-03-26T00:00:00",
        created_at="2026-03-26T00:00:00",
    )

    replies: list[str] = []

    class FakeMessage:
        chat_id = 1

        async def reply_text(self, text: str):
            replies.append(text)

    update = SimpleNamespace(message=FakeMessage())
    context = SimpleNamespace(args=[])

    asyncio.run(bot.cmd_tasks(update, context))

    assert "BG-0001" not in replies[0]
    assert "/failed" in replies[0]


def test_cmd_failed_lists_failed_jobs():
    bot = NexusCrewBot()
    bot._runner._jobs["BG-0001"] = SimpleNamespace(
        id="BG-0001",
        label="broken",
        status="failed",
        error="boom",
        task_id="T-0001",
        updated_at="2026-03-26T00:00:00",
        created_at="2026-03-26T00:00:00",
    )

    replies: list[str] = []

    class FakeMessage:
        chat_id = 1

        async def reply_text(self, text: str):
            replies.append(text)

    update = SimpleNamespace(message=FakeMessage())
    context = SimpleNamespace(args=[])

    asyncio.run(bot.cmd_failed(update, context))

    assert "BG-0001" in replies[0]
    assert "boom" in replies[0]


def test_background_runner_resume_existing_job():
    runner = BackgroundTaskRunner()

    async def work():
        return None

    async def main():
        job_id = runner.submit("demo", work())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        runner.mark_status(job_id, "interrupted")
        resumed = runner.resume_existing(job_id, work())
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return job_id, resumed

    job_id, resumed = asyncio.run(main())
    assert resumed is True
    assert runner.get(job_id).status == "completed"


def test_background_runner_notifies_on_failure():
    runner = BackgroundTaskRunner()
    seen: list[str] = []

    async def work():
        raise RuntimeError("boom")

    async def on_error(job, err):
        seen.append(f"{job.id}:{err}")

    async def main():
        job_id = runner.submit("demo", work(), on_error=on_error)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return job_id

    job_id = asyncio.run(main())
    assert runner.get(job_id).status == "failed"
    assert seen == [f"{job_id}:boom"]


def test_background_runner_emits_heartbeat():
    runner = BackgroundTaskRunner()
    seen: list[str] = []

    async def work():
        await asyncio.sleep(0.05)

    async def on_heartbeat(job):
        seen.append(job.id)

    async def main():
        job_id = runner.submit(
            "demo",
            work(),
            on_heartbeat=on_heartbeat,
            first_heartbeat_delay=0.01,
            heartbeat_interval=0.01,
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0.02)
        await asyncio.sleep(0.06)
        return job_id

    job_id = asyncio.run(main())
    assert runner.get(job_id).status == "completed"
    assert seen
    assert all(item == job_id for item in seen)


def test_handle_message_starts_background_job_silently(monkeypatch):
    bot = NexusCrewBot()
    bot._allowed = set()
    bot._orch = SimpleNamespace()
    updates: list[int] = []

    class FakeService:
        def submit_message(self, chat_id, message, send):
            updates.append(chat_id)
            return "BG-0001"

    async def fake_update(chat_id):
        updates.append(chat_id + 1000)

    monkeypatch.setattr(bot, "_service", lambda: FakeService())
    monkeypatch.setattr(bot, "_update_status_board", fake_update)

    replies: list[str] = []

    class FakeMessage:
        chat_id = 1
        text = "hello"

        async def reply_text(self, text: str):
            replies.append(text)

    update = SimpleNamespace(message=FakeMessage())

    asyncio.run(bot.handle_message(update, SimpleNamespace()))

    assert replies == []
    assert updates == [1, 1001]
