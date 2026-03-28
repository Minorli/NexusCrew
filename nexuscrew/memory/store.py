"""SQLite-backed scoped memory storage."""
import json
import sqlite3
from dataclasses import dataclass, field
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
    """SQLite-backed scoped memory store with legacy JSONL import."""

    def __init__(self, path: Path):
        self.legacy_path = path if path.suffix != ".db" else None
        self.path = path if path.suffix == ".db" else path.with_suffix(".db")
        self._init_db()
        self._migrate_legacy_if_needed()

    def _connect(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scope TEXT NOT NULL,
                    actor TEXT NOT NULL,
                    content TEXT NOT NULL,
                    importance INTEGER NOT NULL,
                    ts TEXT NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_scope_ts ON memory_entries(scope, ts)"
            )

    def _migrate_legacy_if_needed(self):
        if self.legacy_path is None or not self.legacy_path.exists():
            return
        with self._connect() as conn:
            row = conn.execute("SELECT COUNT(*) AS n FROM memory_entries").fetchone()
            if row["n"] > 0:
                return
        imported = 0
        with self.legacy_path.open(encoding="utf-8", errors="replace") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    entry = MemoryEntry(**record)
                except Exception:
                    continue
                self.append(
                    entry.scope,
                    entry.actor,
                    entry.content,
                    importance=entry.importance,
                    ts=entry.ts,
                )
                imported += 1
        if imported == 0:
            return

    def append(
        self,
        scope: str,
        actor: str,
        content: str,
        importance: int = 1,
        ts: str | None = None,
    ):
        entry = MemoryEntry(
            scope=scope,
            actor=actor,
            content=content,
            importance=importance,
            ts=ts or datetime.now().isoformat(),
        )
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_entries (scope, actor, content, importance, ts)
                VALUES (?, ?, ?, ?, ?)
                """,
                (entry.scope, entry.actor, entry.content, entry.importance, entry.ts),
            )

    def read(self, scope: str, last_n: int = 10) -> list[MemoryEntry]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT scope, actor, content, importance, ts
                FROM memory_entries
                WHERE scope = ?
                ORDER BY ts DESC, id DESC
                LIMIT ?
                """,
                (scope, last_n),
            ).fetchall()
        entries = [MemoryEntry(**dict(row)) for row in reversed(rows)]
        return entries

    def read_many(self, scopes: list[str], last_n: int = 10) -> list[MemoryEntry]:
        if not scopes:
            return []
        placeholders = ", ".join("?" for _ in scopes)
        with self._connect() as conn:
            rows = conn.execute(
                f"""
                SELECT scope, actor, content, importance, ts
                FROM memory_entries
                WHERE scope IN ({placeholders})
                ORDER BY importance DESC, ts DESC, id DESC
                LIMIT ?
                """,
                (*scopes, max(last_n * max(len(scopes), 1), last_n)),
            ).fetchall()
        entries = [MemoryEntry(**dict(row)) for row in rows]
        entries.sort(key=lambda item: (item.importance, item.ts))
        return entries[-last_n:]
