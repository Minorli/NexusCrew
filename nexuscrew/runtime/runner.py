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
    TERMINAL_STATUSES = {"completed", "failed", "cancelled", "recovered", "interrupted"}
    INFLIGHT_STATUSES = {"pending", "running"}
    ACTIVE_STATUSES = {"pending", "running", "waiting"}

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

    def submit(
        self,
        label: str,
        coro,
        chat_id: int = 0,
        task_id: str = "",
        run_id: str = "",
        on_error=None,
        on_complete=None,
        on_heartbeat=None,
        heartbeat_interval: float = 45,
        first_heartbeat_delay: float = 20,
    ) -> str:
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
        self._start_job(
            job,
            coro,
            on_error=on_error,
            on_complete=on_complete,
            on_heartbeat=on_heartbeat,
            heartbeat_interval=heartbeat_interval,
            first_heartbeat_delay=first_heartbeat_delay,
        )
        return job_id

    def resume_existing(
        self,
        job_id: str,
        coro,
        on_error=None,
        on_complete=None,
        on_heartbeat=None,
        heartbeat_interval: float = 45,
        first_heartbeat_delay: float = 20,
    ) -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            return False
        # Continue the original background run under the same job id.
        self._start_job(
            job,
            coro,
            on_error=on_error,
            on_complete=on_complete,
            on_heartbeat=on_heartbeat,
            heartbeat_interval=heartbeat_interval,
            first_heartbeat_delay=first_heartbeat_delay,
        )
        return True

    def _start_job(
        self,
        job: BackgroundRun,
        coro,
        on_error=None,
        on_complete=None,
        on_heartbeat=None,
        heartbeat_interval: float = 45,
        first_heartbeat_delay: float = 20,
    ):
        self._persist(job)

        async def runner():
            job.status = "running"
            job.error = ""
            job.touch()
            self._persist(job)
            try:
                if on_heartbeat is None:
                    await coro
                else:
                    task = asyncio.create_task(coro)
                    timeout = max(first_heartbeat_delay, 0.01)
                    interval = max(heartbeat_interval, 0.01)
                    while True:
                        done, _ = await asyncio.wait({task}, timeout=timeout)
                        if done:
                            await task
                            break
                        job.touch()
                        self._persist(job)
                        await on_heartbeat(job)
                        timeout = interval
                if job.status != "cancelled":
                    next_status = "completed"
                    if on_complete is not None:
                        try:
                            maybe_status = await on_complete(job)
                            if maybe_status:
                                next_status = maybe_status
                        except Exception:
                            next_status = "completed"
                    job.status = next_status
                    job.touch()
                    self._persist(job)
            except asyncio.CancelledError:
                if on_heartbeat is not None:
                    task.cancel()
                    try:
                        await task
                    except BaseException:
                        pass
                job.status = "cancelled"
                job.touch()
                self._persist(job)
                raise
            except Exception as err:
                job.status = "failed"
                job.error = str(err)
                job.touch()
                self._persist(job)
                if on_error is not None:
                    try:
                        await on_error(job, err)
                    except Exception:
                        pass

        self._tasks[job.id] = asyncio.create_task(runner())

    def list_runs(self) -> list[BackgroundRun]:
        return [self._jobs[job_id] for job_id in sorted(self._jobs)]

    def list_active_runs(self) -> list[BackgroundRun]:
        return [
            job for job in self.list_runs()
            if job.status in self.ACTIVE_STATUSES
        ]

    def list_inflight_runs(self) -> list[BackgroundRun]:
        return [
            job for job in self.list_runs()
            if job.status in self.INFLIGHT_STATUSES
        ]

    def list_failed_runs(self) -> list[BackgroundRun]:
        jobs = [job for job in self.list_runs() if job.status == "failed"]
        return sorted(
            jobs,
            key=lambda job: job.updated_at or job.created_at,
            reverse=True,
        )

    def active_task_ids(self) -> set[str]:
        return {
            job.task_id
            for job in self.list_inflight_runs()
            if getattr(job, "task_id", "")
        }

    def get(self, job_id: str) -> BackgroundRun | None:
        return self._jobs.get(job_id)

    async def cancel(self, job_id: str) -> bool:
        task = self._tasks.get(job_id)
        job = self._jobs.get(job_id)
        if job is None:
            return False
        if job.status == "interrupted":
            job.status = "cancelled"
            job.touch()
            self._persist(job)
            return True
        if task is None or task.done():
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
        inflight = self.list_inflight_runs()
        waiting = [job for job in self.list_runs() if job.status == "waiting"]
        if not inflight and not waiting:
            failed_count = len(self.list_failed_runs())
            if failed_count:
                return f"当前无活跃后台任务。\n\n失败归档: {failed_count}，使用 /failed 查看详情。"
            return "当前无活跃后台任务。"
        lines = []
        if inflight:
            lines.extend(["🧵 活跃后台任务：", ""])
            for job in inflight:
                lines.append(f"  [{job.id}] {job.status} — {job.label[:60]}")
        if waiting:
            if lines:
                lines.append("")
            lines.extend(["⏸️ 待续任务：", ""])
            for job in waiting:
                lines.append(f"  [{job.id}] {job.status} — {job.label[:60]}")
        failed_count = len(self.list_failed_runs())
        if failed_count:
            lines.extend(["", f"失败归档: {failed_count}，使用 /failed 查看详情。"])
        return "\n".join(lines)

    def format_failed(self) -> str:
        jobs = self.list_failed_runs()
        if not jobs:
            return "当前无失败后台任务。"
        lines = ["🗂️ 失败后台任务归档：", ""]
        for job in jobs:
            error = (job.error or "(无错误详情)")[:200]
            lines.append(f"  [{job.id}] {job.label[:50]}")
            lines.append(f"    task={job.task_id or '-'} updated={job.updated_at or job.created_at}")
            lines.append(f"    error={error}")
        return "\n".join(lines)
