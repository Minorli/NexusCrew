"""Task state machine — tracks lifecycle of user requests."""
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
import re


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
    session_key: str = ""
    family_id: str = ""
    parent_task_id: str = ""
    blocked_reason: str = ""
    branch_name: str = ""
    github_issue_number: int = 0
    github_issue_url: str = ""
    github_pr_number: int = 0
    github_pr_url: str = ""
    slack_channel: str = ""
    slack_message_ts: str = ""
    slack_thread_ts: str = ""
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

    def last_activity_at(self) -> datetime:
        stamp = self.updated_at or self.created_at
        return datetime.fromisoformat(stamp)


class TaskTracker:
    """Track all tasks per chat."""

    _MESSAGE_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+|[\u4e00-\u9fff]{2,}")
    _REFERENCE_RE = re.compile(r"\b(?:T|APR|BG)-\d{4}\b", re.IGNORECASE)
    _STOP_TOKENS = {
        "请", "继续", "处理", "修复", "问题", "任务", "一下", "帮我", "看看",
        "当前", "现在", "这个", "那个", "继续推进", "排查", "根因",
        "the", "and", "for", "with", "this", "that", "please", "continue",
    }

    def __init__(self):
        # Task 4.2 完成: 引入任务状态机与任务看板。
        self._tasks: dict[int, dict[str, Task]] = {}
        self._counter: dict[int, int] = {}

    def create(self, chat_id: int, description: str, parent_task: Task | None = None) -> Task:
        self._counter.setdefault(chat_id, 0)
        self._counter[chat_id] += 1
        task_id = f"T-{self._counter[chat_id]:04d}"
        task = Task(
            id=task_id,
            description=description,
            session_key=parent_task.session_key if parent_task and parent_task.session_key else f"chat:{chat_id}:task:{task_id}",
            family_id=parent_task.family_id if parent_task and parent_task.family_id else task_id,
            parent_task_id=parent_task.id if parent_task else "",
        )
        if not task.family_id:
            task.family_id = task_id
        self._tasks.setdefault(chat_id, {})[task_id] = task
        return task

    def restore(self, chat_id: int, task: Task) -> None:
        if not task.session_key:
            task.session_key = f"chat:{chat_id}:task:{task.id}"
        if not task.family_id:
            task.family_id = task.id
        self._tasks.setdefault(chat_id, {})[task.id] = task
        try:
            numeric = int(task.id.split("-")[-1])
            self._counter[chat_id] = max(self._counter.get(chat_id, 0), numeric)
        except ValueError:
            pass

    def get(self, chat_id: int, task_id: str) -> Task | None:
        return self._tasks.get(chat_id, {}).get(task_id)

    def list_active(self, chat_id: int) -> list[Task]:
        return [
            task for task in self._tasks.get(chat_id, {}).values()
            if task.status not in (TaskStatus.DONE, TaskStatus.FAILED)
        ]

    def list_all(self, chat_id: int) -> list[Task]:
        return list(self._tasks.get(chat_id, {}).values())

    def latest_active(self, chat_id: int) -> Task | None:
        tasks = self.list_active(chat_id)
        return tasks[-1] if tasks else None

    def latest_active_for_assignee(self, chat_id: int, assignee: str) -> Task | None:
        tasks = [
            task for task in self.list_active(chat_id)
            if task.assigned_to == assignee
        ]
        return tasks[-1] if tasks else None

    def find_related_active(
        self,
        chat_id: int,
        message: str,
        preferred_assignee: str = "",
    ) -> Task | None:
        tasks = list(reversed(self.list_active(chat_id)))
        if not tasks:
            return None

        references = set(self._REFERENCE_RE.findall(message))
        if references:
            for task in tasks:
                haystack = self._task_search_text(task)
                if any(ref in haystack for ref in references):
                    return task

        message_tokens = self._message_tokens(message)
        if not message_tokens:
            return None

        best_task = None
        best_score = 0.0
        for task in tasks:
            task_tokens = self._message_tokens(self._task_search_text(task))
            overlap = len(message_tokens & task_tokens)
            if overlap == 0:
                continue
            score = float(overlap)
            if preferred_assignee and task.assigned_to == preferred_assignee:
                score += 0.5
            if score > best_score:
                best_score = score
                best_task = task

        if best_task is None:
            return None
        if best_score >= 2:
            return best_task
        if best_score >= 1.5:
            return best_task
        return None

    def _task_search_text(self, task: Task) -> str:
        history_tail = " ".join(task.history[-6:])
        return f"{task.description} {history_tail}".strip()

    def _message_tokens(self, text: str) -> set[str]:
        tokens: set[str] = set()
        for raw in self._MESSAGE_TOKEN_RE.findall(text.lower()):
            token = raw.strip("-_.:/ ")
            if len(token) < 2:
                continue
            if token in self._STOP_TOKENS:
                continue
            tokens.add(token)
        return tokens

    def format_status(
        self,
        chat_id: int,
        inflight_task_ids: set[str] | None = None,
        waiting_task_ids: set[str] | None = None,
        task_stage_sla_seconds: int = 0,
    ) -> str:
        tasks = self.list_active(chat_id)
        if not tasks:
            return "当前无活跃任务。"
        inflight_task_ids = set(inflight_task_ids or set())
        waiting_task_ids = set(waiting_task_ids or set())
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
            runtime_state = self._runtime_state_label(
                task,
                inflight_task_ids=inflight_task_ids,
                waiting_task_ids=waiting_task_ids,
                task_stage_sla_seconds=task_stage_sla_seconds,
            )
            if runtime_state == "inflight":
                emoji = "🚧"
            elif runtime_state == "blocked":
                emoji = "⛔"
            elif runtime_state == "waiting":
                emoji = "⏸️"
            elif runtime_state == "stale":
                emoji = "🕸️"
            else:
                emoji = emoji_map.get(task.status, "❓")
            assignee = f"@{task.assigned_to}" if task.assigned_to else "@未分配"
            suffix = ""
            if runtime_state == "inflight":
                suffix = " (inflight)"
            elif runtime_state == "waiting":
                suffix = " (waiting)"
            elif runtime_state == "blocked":
                suffix = f" (blocked:{task.blocked_reason or 'unknown'})"
            elif runtime_state == "stale":
                suffix = " (stale)"
            lines.append(f"  {emoji} [{task.id}] {task.description[:50]} -> {assignee}{suffix}")
        family_lines = self.format_family_summary(chat_id)
        if family_lines:
            lines.extend(["", *family_lines])
        return "\n".join(lines)

    def _runtime_state_label(
        self,
        task: Task,
        inflight_task_ids: set[str],
        waiting_task_ids: set[str],
        task_stage_sla_seconds: int,
    ) -> str:
        if task.id in inflight_task_ids:
            return "inflight"
        if task.blocked_reason:
            return "blocked"
        if task.id in waiting_task_ids:
            return "waiting"
        if task_stage_sla_seconds > 0:
            age = (datetime.now() - task.last_activity_at()).total_seconds()
            if age >= task_stage_sla_seconds:
                return "stale"
        return task.status.value

    def family_members(self, chat_id: int, family_id: str, include_done: bool = False) -> list[Task]:
        tasks = self.list_all(chat_id) if include_done else self.list_active(chat_id)
        return [
            task for task in tasks
            if (getattr(task, "family_id", "") or task.id) == family_id
        ]

    def session_members(self, chat_id: int, session_key: str, include_done: bool = False) -> list[Task]:
        tasks = self.list_all(chat_id) if include_done else self.list_active(chat_id)
        return [
            task for task in tasks
            if getattr(task, "session_key", "") == session_key
        ]

    def format_family_summary(self, chat_id: int) -> list[str]:
        interesting = self.family_rollups(chat_id)
        if not interesting:
            return []
        lines = ["🧬 Task Families："]
        for item in interesting:
            family_id = item["family_id"]
            members = item["members"]
            member_labels = ", ".join(task.id for task in members)
            lines.append(f"  [{family_id}] {item['state']} / {member_labels}")
        return lines

    def family_rollups(
        self,
        chat_id: int,
        inflight_task_ids: set[str] | None = None,
        waiting_task_ids: set[str] | None = None,
        task_stage_sla_seconds: int = 0,
    ) -> list[dict]:
        inflight_task_ids = set(inflight_task_ids or set())
        waiting_task_ids = set(waiting_task_ids or set())
        families: dict[str, list[Task]] = {}
        for task in self.list_active(chat_id):
            family_id = getattr(task, "family_id", "") or task.id
            families.setdefault(family_id, []).append(task)
        rollups: list[dict] = []
        for family_id, members in families.items():
            states = [
                self._runtime_state_label(
                    task,
                    inflight_task_ids=inflight_task_ids,
                    waiting_task_ids=waiting_task_ids,
                    task_stage_sla_seconds=task_stage_sla_seconds,
                )
                for task in members
            ]
            if "blocked" in states:
                state = "blocked"
            elif "inflight" in states:
                state = "inflight"
            elif "waiting" in states:
                state = "waiting"
            elif "stale" in states:
                state = "stale"
            else:
                state = "active"
            rollups.append(
                {
                    "family_id": family_id,
                    "state": state,
                    "completion_state": self.family_completion_state(chat_id, family_id),
                    "ready_to_close": self.family_ready_to_close(chat_id, family_id),
                    "members": members,
                    "blocked_reasons": sorted({task.blocked_reason for task in members if getattr(task, "blocked_reason", "")}),
                    "next_actions": [],
                }
            )
        return rollups

    def session_rollups(
        self,
        chat_id: int,
        inflight_task_ids: set[str] | None = None,
        waiting_task_ids: set[str] | None = None,
        task_stage_sla_seconds: int = 0,
    ) -> list[dict]:
        inflight_task_ids = set(inflight_task_ids or set())
        waiting_task_ids = set(waiting_task_ids or set())
        sessions: dict[str, list[Task]] = {}
        for task in self.list_all(chat_id):
            session_key = getattr(task, "session_key", "") or f"chat:{chat_id}:task:{task.id}"
            sessions.setdefault(session_key, []).append(task)
        rollups: list[dict] = []
        for session_key, members in sessions.items():
            if (
                len(members) <= 1
                and not any(getattr(task, "parent_task_id", "") for task in members)
                and not any(task.status not in (TaskStatus.DONE, TaskStatus.FAILED) for task in members)
            ):
                continue
            states = [
                self._runtime_state_label(
                    task,
                    inflight_task_ids=inflight_task_ids,
                    waiting_task_ids=waiting_task_ids,
                    task_stage_sla_seconds=task_stage_sla_seconds,
                )
                for task in members
            ]
            if "blocked" in states:
                state = "blocked"
            elif "inflight" in states:
                state = "inflight"
            elif "waiting" in states:
                state = "waiting"
            elif "stale" in states:
                state = "stale"
            else:
                state = "active"
            rollups.append(
                {
                    "session_key": session_key,
                    "state": state,
                    "completion_state": self.session_completion_state(chat_id, session_key),
                    "ready_to_close": self.session_ready_to_close(chat_id, session_key),
                    "members": members,
                    "blocked_reasons": sorted({task.blocked_reason for task in members if getattr(task, "blocked_reason", "")}),
                    "next_actions": [],
                }
            )
        return rollups

    def family_completion_state(self, chat_id: int, family_id: str) -> str:
        members = self.family_members(chat_id, family_id, include_done=True)
        if not members:
            return "unknown"
        statuses = {task.status for task in members}
        if statuses == {TaskStatus.DONE}:
            return "completed"
        if statuses <= {TaskStatus.DONE, TaskStatus.FAILED} and TaskStatus.FAILED in statuses:
            return "failed"
        if TaskStatus.DONE in statuses:
            return "partial"
        return "open"

    def family_ready_to_close(self, chat_id: int, family_id: str) -> bool:
        members = self.family_members(chat_id, family_id)
        if not members:
            return False
        if any(getattr(task, "blocked_reason", "") for task in members):
            return False
        return all(task.status in {TaskStatus.ACCEPTED, TaskStatus.VALIDATING, TaskStatus.DONE} for task in members)

    def session_completion_state(self, chat_id: int, session_key: str) -> str:
        members = self.session_members(chat_id, session_key, include_done=True)
        if not members:
            return "unknown"
        statuses = {task.status for task in members}
        if statuses == {TaskStatus.DONE}:
            return "completed"
        if statuses <= {TaskStatus.DONE, TaskStatus.FAILED} and TaskStatus.FAILED in statuses:
            return "failed"
        if TaskStatus.DONE in statuses:
            return "partial"
        return "open"

    def session_ready_to_close(self, chat_id: int, session_key: str) -> bool:
        members = self.session_members(chat_id, session_key)
        if not members:
            return False
        if any(getattr(task, "blocked_reason", "") for task in members):
            return False
        return all(task.status in {TaskStatus.ACCEPTED, TaskStatus.VALIDATING, TaskStatus.DONE} for task in members)

    def agent_queue(
        self,
        chat_id: int,
        assignee: str,
        inflight_task_ids: set[str] | None = None,
        waiting_task_ids: set[str] | None = None,
        task_stage_sla_seconds: int = 0,
    ) -> list[dict]:
        inflight_task_ids = set(inflight_task_ids or set())
        waiting_task_ids = set(waiting_task_ids or set())
        tasks = [
            task for task in self.list_active(chat_id)
            if task.assigned_to == assignee
        ]
        ordered = sorted(
            tasks,
            key=lambda task: (
                self._queue_priority(
                    self._runtime_state_label(
                        task,
                        inflight_task_ids=inflight_task_ids,
                        waiting_task_ids=waiting_task_ids,
                        task_stage_sla_seconds=task_stage_sla_seconds,
                    )
                ),
                task.updated_at or task.created_at,
            ),
        )
        rows = []
        for task in ordered:
            runtime_state = self._runtime_state_label(
                task,
                inflight_task_ids=inflight_task_ids,
                waiting_task_ids=waiting_task_ids,
                task_stage_sla_seconds=task_stage_sla_seconds,
            )
            rows.append(
                {
                    "task_id": task.id,
                    "family_id": getattr(task, "family_id", "") or task.id,
                    "runtime_state": runtime_state,
                    "blocked_reason": getattr(task, "blocked_reason", ""),
                }
            )
        return rows

    def _queue_priority(self, runtime_state: str) -> int:
        order = {
            "inflight": 0,
            "blocked": 1,
            "waiting": 2,
            "stale": 3,
            "planning": 4,
            "in_progress": 5,
            "review_requested": 6,
            "reviewing": 7,
            "accepted": 8,
            "validating": 9,
        }
        return order.get(runtime_state, 99)
