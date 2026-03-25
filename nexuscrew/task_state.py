"""Task state machine — tracks lifecycle of user requests."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class TaskStatus(Enum):
    PLANNING = "planning"
    IN_PROGRESS = "in_progress"
    REVIEW_REQ = "review_requested"
    REVIEWING = "reviewing"
    ACCEPTED = "accepted"
    VALIDATING = "validating"
    DONE = "done"
    FAILED = "failed"


TRANSITIONS = {
    TaskStatus.PLANNING: [TaskStatus.IN_PROGRESS],
    TaskStatus.IN_PROGRESS: [TaskStatus.REVIEW_REQ, TaskStatus.FAILED],
    TaskStatus.REVIEW_REQ: [TaskStatus.REVIEWING],
    TaskStatus.REVIEWING: [TaskStatus.ACCEPTED, TaskStatus.IN_PROGRESS],
    TaskStatus.ACCEPTED: [TaskStatus.VALIDATING],
    TaskStatus.VALIDATING: [TaskStatus.DONE, TaskStatus.IN_PROGRESS],
    TaskStatus.DONE: [],
    TaskStatus.FAILED: [TaskStatus.PLANNING],
}


@dataclass
class Task:
    id: str
    description: str
    status: TaskStatus = TaskStatus.PLANNING
    assigned_to: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = ""
    history: list[str] = field(default_factory=list)

    def transition(self, new_status: TaskStatus) -> bool:
        if new_status in TRANSITIONS.get(self.status, []):
            old = self.status
            self.status = new_status
            self.updated_at = datetime.now().isoformat()
            self.history.append(
                f"{old.value} -> {new_status.value} at {self.updated_at}"
            )
            return True
        return False


class TaskTracker:
    """Track all tasks per chat."""

    def __init__(self):
        # Task 4.2 完成: 引入任务状态机与任务看板。
        self._tasks: dict[int, dict[str, Task]] = {}
        self._counter: dict[int, int] = {}

    def create(self, chat_id: int, description: str) -> Task:
        self._counter.setdefault(chat_id, 0)
        self._counter[chat_id] += 1
        task_id = f"T-{self._counter[chat_id]:04d}"
        task = Task(id=task_id, description=description)
        self._tasks.setdefault(chat_id, {})[task_id] = task
        return task

    def get(self, chat_id: int, task_id: str) -> Task | None:
        return self._tasks.get(chat_id, {}).get(task_id)

    def list_active(self, chat_id: int) -> list[Task]:
        return [
            task for task in self._tasks.get(chat_id, {}).values()
            if task.status not in (TaskStatus.DONE, TaskStatus.FAILED)
        ]

    def latest_active(self, chat_id: int) -> Task | None:
        tasks = self.list_active(chat_id)
        return tasks[-1] if tasks else None

    def format_status(self, chat_id: int) -> str:
        tasks = self.list_active(chat_id)
        if not tasks:
            return "当前无活跃任务。"
        lines = ["📋 活跃任务：", ""]
        emoji_map = {
            TaskStatus.PLANNING: "📝",
            TaskStatus.IN_PROGRESS: "🔨",
            TaskStatus.REVIEW_REQ: "📤",
            TaskStatus.REVIEWING: "🔍",
            TaskStatus.ACCEPTED: "✅",
            TaskStatus.VALIDATING: "🏁",
            TaskStatus.DONE: "🎉",
            TaskStatus.FAILED: "❌",
        }
        for task in tasks:
            emoji = emoji_map.get(task.status, "❓")
            assignee = f"@{task.assigned_to}" if task.assigned_to else "@未分配"
            lines.append(
                f"  {emoji} [{task.id}] {task.description[:50]} -> {assignee}"
            )
        return "\n".join(lines)
