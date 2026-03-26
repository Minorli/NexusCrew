"""Recovery manager for interrupted background jobs."""
import asyncio


class RecoveryManager:
    """Attempt to recover interrupted background runs from persisted state."""

    def __init__(self, runner, orchestrator):
        self.runner = runner
        self.orchestrator = orchestrator

    async def recover(self, send_factory) -> list[str]:
        recovered: list[str] = []
        for job in self.runner.list_runs():
            if job.status != "interrupted":
                continue
            if not job.task_id:
                continue
            chat_id = getattr(job, "chat_id", 0) or None
            found = None
            if chat_id is not None:
                found = self.orchestrator.task_tracker.get(chat_id, job.task_id)
            if found is None:
                # Fallback by task_id across known chats.
                for cid, tasks in self.orchestrator.task_tracker._tasks.items():
                    if job.task_id in tasks:
                        found = tasks[job.task_id]
                        chat_id = cid
                        break
            if found is None or chat_id is None:
                continue
            send = send_factory(chat_id)
            async def resume_coro(chat_id=chat_id, task_id=job.task_id, send=send):
                ok = await self.orchestrator.resume_task(chat_id, task_id, send)
                if ok:
                    return
                # When no checkpoint is available, fall back to replay so interrupted work can continue.
                replay_ok = await self.orchestrator.replay_task(chat_id, task_id, send)
                if not replay_ok:
                    raise RuntimeError(f"resume/replay failed for {task_id}")
            if self.runner.resume_existing(job.id, resume_coro()):
                self.runner.mark_status(job.id, "running")
                recovered.append(job.id)
        await asyncio.sleep(0)
        return recovered
