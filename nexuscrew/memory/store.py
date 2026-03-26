"""Scoped memory storage."""
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class MemoryEntry:
    scope: str
    actor: str
    content: str
    importance: int = 1
    ts: str = field(default_factory=lambda: datetime.now().isoformat())


class ScopedMemoryStore:
    """Append-only scoped memory store."""

    def __init__(self, path: Path):
        self.path = path
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def append(self, scope: str, actor: str, content: str, importance: int = 1):
        entry = MemoryEntry(scope=scope, actor=actor, content=content, importance=importance)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(entry), ensure_ascii=False) + "\n")

    def read(self, scope: str, last_n: int = 10) -> list[MemoryEntry]:
        entries = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record["scope"] == scope:
                entries.append(MemoryEntry(**record))
        return entries[-last_n:]

    def read_many(self, scopes: list[str], last_n: int = 10) -> list[MemoryEntry]:
        entries: list[MemoryEntry] = []
        for scope in scopes:
            entries.extend(self.read(scope, last_n=last_n))
        entries.sort(key=lambda item: (item.importance, item.ts))
        return entries[-last_n:]
