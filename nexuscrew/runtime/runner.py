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
    lane_key: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = ""
    error: str = ""

    def touch(self):
        self.updated_at = datetime.now().isoformat()


class BackgroundTaskRunner:
    """Track and manage background async tasks."""
    TERMINAL_STATUSES = {"completed", "failed", "cancelled", "recovered"}
    INFLIGHT_STATUSES = {"pending", "running"}
    ACTIVE_STATUSES = {"pending", "running", "waiting"}

    def __init__(self, state_store=None):
        # Task A4 完成: 后台任务执行器与可查询状态。
        self._counter = 0
        self._jobs: dict[str, BackgroundRun] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._lane_active: dict[str, str] = {}
        self._lane_waiting: dict[str, list[str]] = {}
        self._deferred: dict[str, tuple] = {}
        self._state_store = state_store
        self._load_from_store()

    def _load_from_store(self):
        if self._state_store is None:
            return
        for record in self._state_store.load_background_runs():
            job = BackgroundRun(**record)
            if job.status in ("pending", "running", "waiting"):
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
        if not hasattr(job, "lane_key"):
            job.lane_key = ""
        if self._state_store is not None:
            self._state_store.save_background_run(job)

    def submit(
        self,
        label: str,
        coro,
        chat_id: int = 0,
        task_id: str = "",
        run_id: str = "",
        lane_key: str = "",
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
            lane_key=lane_key,
        )
        self._jobs[job_id] = job
        self._start_or_queue_job(
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
        lane_key: str = "",
        on_error=None,
        on_complete=None,
        on_heartbeat=None,
        heartbeat_interval: float = 45,
        first_heartbeat_delay: float = 20,
    ) -> bool:
        job = self._jobs.get(job_id)
        if job is None:
            return False
        if lane_key:
            job.lane_key = lane_key
        # Continue the original background run under the same job id.
        self._start_or_queue_job(
            job,
            coro,
            on_error=on_error,
            on_complete=on_complete,
            on_heartbeat=on_heartbeat,
            heartbeat_interval=heartbeat_interval,
            first_heartbeat_delay=first_heartbeat_delay,
        )
        return True

    def _start_or_queue_job(
        self,
        job: BackgroundRun,
        coro,
        on_error=None,
        on_complete=None,
        on_heartbeat=None,
        heartbeat_interval: float = 45,
        first_heartbeat_delay: float = 20,
    ):
        lane_key = getattr(job, "lane_key", "") or ""
        active_job_id = self._lane_active.get(lane_key) if lane_key else None
        active_task = self._tasks.get(active_job_id, None) if active_job_id else None
        if lane_key and active_job_id and active_job_id != job.id and active_task is not None and not active_task.done():
            job.status = "waiting"
            job.touch()
            self._persist(job)
            self._lane_waiting.setdefault(lane_key, []).append(job.id)
            self._deferred[job.id] = (
                coro,
                on_error,
                on_complete,
                on_heartbeat,
                heartbeat_interval,
                first_heartbeat_delay,
            )
            return
        self._start_job(
            job,
            coro,
            on_error=on_error,
            on_complete=on_complete,
            on_heartbeat=on_heartbeat,
            heartbeat_interval=heartbeat_interval,
            first_heartbeat_delay=first_heartbeat_delay,
        )

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
        lane_key = getattr(job, "lane_key", "") or ""
        if lane_key:
            self._lane_active[lane_key] = job.id

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
            finally:
                self._release_lane(job)

        self._tasks[job.id] = asyncio.create_task(runner())

    def _release_lane(self, job: BackgroundRun):
        lane_key = getattr(job, "lane_key", "") or ""
        if not lane_key:
            return
        if self._lane_active.get(lane_key) == job.id:
            self._lane_active.pop(lane_key, None)
        waiting = self._lane_waiting.get(lane_key, [])
        while waiting:
            next_job_id = waiting.pop(0)
            deferred = self._deferred.pop(next_job_id, None)
            next_job = self._jobs.get(next_job_id)
            if deferred is None or next_job is None or next_job.status == "cancelled":
                continue
            coro, on_error, on_complete, on_heartbeat, heartbeat_interval, first_heartbeat_delay = deferred
            self._start_job(
                next_job,
                coro,
                on_error=on_error,
                on_complete=on_complete,
                on_heartbeat=on_heartbeat,
                heartbeat_interval=heartbeat_interval,
                first_heartbeat_delay=first_heartbeat_delay,
            )
            break
        if not waiting:
            self._lane_waiting.pop(lane_key, None)

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

    def list_waiting_runs(self) -> list[BackgroundRun]:
        return [job for job in self.list_runs() if job.status == "waiting"]

    def lane_summaries(self) -> list[dict]:
        lanes: dict[str, list[BackgroundRun]] = {}
        for job in self.list_runs():
            lane_key = getattr(job, "lane_key", "") or ""
            if not lane_key:
                continue
            lanes.setdefault(lane_key, []).append(job)
        rows: list[dict] = []
        for lane_key, jobs in sorted(lanes.items()):
            inflight = [job for job in jobs if job.status in self.INFLIGHT_STATUSES]
            waiting = [job for job in jobs if job.status == "waiting"]
            state = "congested" if inflight and waiting else "active" if inflight else "queued" if waiting else "idle"
            rows.append(
                {
                    "lane_key": lane_key,
                    "chat_id": next((getattr(job, "chat_id", 0) for job in jobs if getattr(job, "chat_id", 0)), 0),
                    "task_ids": [job.task_id for job in jobs if getattr(job, "task_id", "")],
                    "state": state,
                    "inflight": len(inflight),
                    "waiting": len(waiting),
                    "backlog": len(waiting),
                    "head_job_id": inflight[0].id if inflight else (waiting[0].id if waiting else ""),
                    "jobs": [
                        {
                            "id": job.id,
                            "status": job.status,
                            "task_id": getattr(job, "task_id", ""),
                            "label": job.label,
                        }
                        for job in jobs
                    ],
                }
            )
        return rows

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

    def waiting_task_ids(self) -> set[str]:
        return {
            job.task_id
            for job in self.list_waiting_runs()
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
        if job.status == "waiting":
            lane_key = getattr(job, "lane_key", "") or ""
            if lane_key in self._lane_waiting:
                self._lane_waiting[lane_key] = [item for item in self._lane_waiting[lane_key] if item != job_id]
                if not self._lane_waiting[lane_key]:
                    self._lane_waiting.pop(lane_key, None)
            deferred = self._deferred.pop(job_id, None)
            if deferred:
                coro = deferred[0]
                if hasattr(coro, "close"):
                    coro.close()
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
                lane = f" / {job.lane_key}" if getattr(job, "lane_key", "") else ""
                lines.append(f"  [{job.id}] {job.status} — {job.label[:60]}{lane}")
        if waiting:
            if lines:
                lines.append("")
            lines.extend(["⏸️ 待续任务：", ""])
            for job in waiting:
                lane = f" / {job.lane_key}" if getattr(job, "lane_key", "") else ""
                lines.append(f"  [{job.id}] {job.status} — {job.label[:60]}{lane}")
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
