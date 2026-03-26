"""Lifecycle hooks and audit events for executable actions."""
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class HookEvent:
    event_type: str
    summary: str
    payload: dict
    ts: str = field(default_factory=lambda: datetime.now().isoformat())


class HookManager:
    """Persist action audit events."""

    def __init__(self, path: Path):
        # Task B4 完成: 统一执行前 Hook 与审计事件记录。
        self.path = path
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def emit(self, event_type: str, summary: str, payload: dict | None = None):
        payload = payload or {}
        event = HookEvent(event_type=event_type, summary=summary, payload=payload)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(event), ensure_ascii=False) + "\n")

    def read_all(self) -> list[dict]:
        if not self.path.exists():
            return []
        rows: list[dict] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        return rows
