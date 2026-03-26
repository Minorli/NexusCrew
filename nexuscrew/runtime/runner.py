"""Background task runner for long-lived orchestration jobs."""
import asyncio
from dataclasses import dataclass, field
from datetime import datetime
from types import SimpleNamespace


@dataclass
class BackgroundRun:
    id: str
    label: str
    status: str = "pending"
    chat_id: int = 0
    task_id: str = ""
    run_id: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = ""
    error: str = ""

    def touch(self):
        self.updated_at = datetime.now().isoformat()


class BackgroundTaskRunner:
    """Track and manage background async tasks."""

    def __init__(self, state_store=None):
        # Task A4 完成: 后台任务执行器与可查询状态。
        self._counter = 0
        self._jobs: dict[str, BackgroundRun] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._state_store = state_store
        self._load_from_store()

    def _load_from_store(self):
        if self._state_store is None:
            return
        for record in self._state_store.load_background_runs():
            job = BackgroundRun(**record)
            if job.status in ("pending", "running"):
                job.status = "interrupted"
                job.touch()
            self._jobs[job.id] = job
            try:
                self._counter = max(self._counter, int(job.id.split("-")[-1]))
            except ValueError:
                pass

    def _persist(self, job: BackgroundRun):
        if not hasattr(job, "created_at"):
            job.created_at = datetime.now().isoformat()
        if not hasattr(job, "updated_at"):
            job.updated_at = ""
        if not hasattr(job, "error"):
            job.error = ""
        if not hasattr(job, "task_id"):
            job.task_id = ""
        if not hasattr(job, "run_id"):
            job.run_id = ""
        if not hasattr(job, "chat_id"):
            job.chat_id = 0
        if self._state_store is not None:
            self._state_store.save_background_run(job)

    def submit(self, label: str, coro, chat_id: int = 0, task_id: str = "", run_id: str = "") -> str:
        self._counter += 1
        job_id = f"BG-{self._counter:04d}"
        job = BackgroundRun(
            id=job_id,
            label=label,
            chat_id=chat_id,
            task_id=task_id,
            run_id=run_id,
        )
        self._jobs[job_id] = job
        self._start_job(job, coro)
        return job_id

    def resume_existing(self, job_id: str, coro) -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            return False
        # Continue the original background run under the same job id.
        self._start_job(job, coro)
        return True

    def _start_job(self, job: BackgroundRun, coro):
        self._persist(job)

        async def runner():
            job.status = "running"
            job.error = ""
            job.touch()
            self._persist(job)
            try:
                await coro
                if job.status != "cancelled":
                    job.status = "completed"
                    job.touch()
                    self._persist(job)
            except asyncio.CancelledError:
                job.status = "cancelled"
                job.touch()
                self._persist(job)
                raise
            except Exception as err:
                job.status = "failed"
                job.error = str(err)
                job.touch()
                self._persist(job)

        self._tasks[job.id] = asyncio.create_task(runner())

    def list_runs(self) -> list[BackgroundRun]:
        return [self._jobs[job_id] for job_id in sorted(self._jobs)]

    def get(self, job_id: str) -> BackgroundRun | None:
        return self._jobs.get(job_id)

    async def cancel(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        job = self._jobs.get(job_id)
        if task is None or job is None or task.done():
            return False
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        job.status = "cancelled"
        job.touch()
        self._persist(job)
        return True

    def mark_status(self, job_id: str, status: str, error: str = "") -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            return False
        if not hasattr(job, "created_at"):
            job.created_at = datetime.now().isoformat()
        if not hasattr(job, "updated_at"):
            job.updated_at = ""
        if not hasattr(job, "error"):
            job.error = ""
        if not hasattr(job, "task_id"):
            job.task_id = ""
        if not hasattr(job, "run_id"):
            job.run_id = ""
        job.status = status
        if error:
            job.error = error
        if hasattr(job, "touch"):
            job.touch()
        else:
            job.updated_at = datetime.now().isoformat()
        self._persist(job)
        return True

    def format_status(self) -> str:
        jobs = self.list_runs()
        if not jobs:
            return "当前无后台任务。"
        lines = ["🧵 后台任务：", ""]
        for job in jobs:
            lines.append(f"  [{job.id}] {job.status} — {job.label[:60]}")
        return "\n".join(lines)
