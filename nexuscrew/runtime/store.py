"""Append-only runtime event store."""
import json
from pathlib import Path

from .events import RunEvent


class EventStore:
    """Persist orchestration events to JSONL."""

    def __init__(self, path: Path):
        # Task A1 完成: 提供事件日志持久化。
        self.path = path
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def append(self, event: RunEvent) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(event.to_record(), ensure_ascii=False) + "\n")

    def read_all(self) -> list[RunEvent]:
        if not self.path.exists():
            return []
        events: list[RunEvent] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            events.append(RunEvent.from_record(json.loads(line)))
        return events

    def list_run(self, run_id: str) -> list[RunEvent]:
        return [event for event in self.read_all() if event.run_id == run_id]
