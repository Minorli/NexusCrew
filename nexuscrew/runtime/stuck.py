"""Stuck detector 2.0."""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class StuckReport:
    task_id: str
    labels: list[str]
    summary: str
    severity: str = "warning"
    ts: str = field(default_factory=lambda: datetime.now().isoformat())


class StuckDetector:
    """Detect looping and no-progress execution patterns."""

    def analyze(self, task_id: str, history: list[dict], events: list) -> StuckReport | None:
        labels: list[str] = []
        if self._detect_ping_pong(history):
            labels.append("ping_pong")
        if self._detect_repeat_reply(history):
            labels.append("repeat_reply")
        if self._detect_repeat_shell_failure(events):
            labels.append("repeat_shell_failure")
        if not labels:
            return None
        return StuckReport(
            task_id=task_id,
            labels=labels,
            summary=" / ".join(labels),
            severity="critical" if "repeat_shell_failure" in labels else "warning",
        )

    def _detect_ping_pong(self, history: list[dict]) -> bool:
        agents = [item.get("agent") for item in history if item.get("agent") not in ("human", "shell")]
        if len(agents) < 6:
            return False
        tail = agents[-6:]
        return len(set(tail)) == 2 and tail[0] == tail[2] == tail[4] and tail[1] == tail[3] == tail[5]

    def _detect_repeat_reply(self, history: list[dict]) -> bool:
        if len(history) < 3:
            return False
        tail = [item.get("content", "")[:120] for item in history[-3:]]
        return tail[0] == tail[1] == tail[2] and bool(tail[0])

    def _detect_repeat_shell_failure(self, events: list) -> bool:
        failures = [
            event for event in events[-5:]
            if getattr(event, "type", "") == "shell_finished"
            and "failed" in str(getattr(event, "payload", {})).lower()
        ]
        return len(failures) >= 2
