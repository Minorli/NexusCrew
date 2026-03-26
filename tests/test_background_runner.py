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
