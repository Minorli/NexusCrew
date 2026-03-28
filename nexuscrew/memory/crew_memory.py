"""Shared crew memory — SQLite primary store with Markdown summary mirror."""
from datetime import datetime
from pathlib import Path
import re
import sqlite3


class CrewMemory:
    MAX_FILE_BYTES = 2_000_000
    TAIL_READ_BYTES = 512_000
    COMPACT_KEEP_LINES = 4000
    SUMMARY_NOTE_LIMIT = 80

    def __init__(self, path: Path):
        self.path = path
        self.db_path = path.with_suffix(".db")
        had_file = path.exists()
        self._init_db()
        if not path.exists():
            path.write_text(self._initial_text(), encoding="utf-8")
        self._migrate_sections_from_file_if_needed()
        self._compact_if_needed()
        if (not had_file) or self._has_db_content():
            self._render_summary_file()

    def read(self, tail_lines: int = 120) -> str:
        """Return last N lines to limit token injection."""
        if not self._has_db_content():
            return "\n".join(self._read_tail_lines(tail_lines))
        lines = self._render_lines()
        return "\n".join(lines[-tail_lines:])

    def append(self, agent_name: str, note: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_notes (actor, content, created_at)
                VALUES (?, ?, ?)
                """,
                (agent_name, note.strip(), ts),
            )
        self._render_summary_file()

    def overwrite_section(self, header: str, content: str) -> None:
        """Replace or insert a named section (used by ProjectScanner)."""
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO memory_sections (header, content, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(header) DO UPDATE SET
                    content = excluded.content,
                    updated_at = excluded.updated_at
                """,
                (header, content.strip(), datetime.now().isoformat()),
            )
        self._render_summary_file()

    def _initial_text(self) -> str:
        return (
            "# NexusCrew 共享记忆\n\n"
            "> 所有 Agent 共同读写。Agent 回复末尾加【MEMORY】标记自动追加。\n"
            "> 人类可直接编辑此文件向团队广播信息。\n\n"
            "## 项目基础信息\n\n(由 /crew 命令自动填充)\n"
        )

    def _connect(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self):
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS memory_sections (
                    header TEXT PRIMARY KEY,
                    content TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS memory_notes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    actor TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                """
            )

    def _has_db_content(self) -> bool:
        with self._connect() as conn:
            sections = conn.execute("SELECT COUNT(*) AS n FROM memory_sections").fetchone()["n"]
            notes = conn.execute("SELECT COUNT(*) AS n FROM memory_notes").fetchone()["n"]
        return (sections + notes) > 0

    def _migrate_sections_from_file_if_needed(self):
        if not self.path.exists():
            return
        with self._connect() as conn:
            count = conn.execute("SELECT COUNT(*) AS n FROM memory_sections").fetchone()["n"]
            if count > 0:
                return
        text = self.path.read_text(encoding="utf-8", errors="replace")
        matches = list(re.finditer(r"^## (.+)$", text, flags=re.MULTILINE))
        if not matches:
            return
        with self._connect() as conn:
            for idx, match in enumerate(matches):
                header = match.group(1).strip()
                start = match.end()
                end = matches[idx + 1].start() if idx + 1 < len(matches) else len(text)
                content = text[start:end].strip()
                if not content:
                    continue
                conn.execute(
                    """
                    INSERT INTO memory_sections (header, content, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(header) DO NOTHING
                    """,
                    (header, content, datetime.now().isoformat()),
                )

    def _compact_if_needed(self) -> None:
        try:
            size = self.path.stat().st_size
        except FileNotFoundError:
            self.path.write_text(self._initial_text(), encoding="utf-8")
            return
        if size <= self.MAX_FILE_BYTES:
            return
        tail_lines = self._read_tail_lines(self.COMPACT_KEEP_LINES)
        tail_text = "\n".join(line for line in tail_lines if line.strip()).strip()
        compacted = self._initial_text()
        if tail_text:
            compacted += "\n## 历史摘要（自动压缩）\n\n" + tail_text + "\n"
            with self._connect() as conn:
                conn.execute(
                    """
                    INSERT INTO memory_sections (header, content, updated_at)
                    VALUES (?, ?, ?)
                    ON CONFLICT(header) DO UPDATE SET
                        content = excluded.content,
                        updated_at = excluded.updated_at
                    """,
                    ("历史摘要（自动压缩）", tail_text, datetime.now().isoformat()),
                )
        self.path.write_text(compacted, encoding="utf-8")
        if self._has_db_content():
            self._render_summary_file()

    def _read_tail_lines(self, tail_lines: int) -> list[str]:
        if tail_lines <= 0:
            return []
        with self.path.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            if size <= 0:
                return []
            block = b""
            cursor = size
            while cursor > 0 and block.count(b"\n") <= tail_lines + 1:
                read_size = min(self.TAIL_READ_BYTES, cursor)
                cursor -= read_size
                f.seek(cursor)
                block = f.read(read_size) + block
                if len(block) > self.TAIL_READ_BYTES * 4:
                    break
        text = block.decode("utf-8", errors="replace")
        lines = text.splitlines()
        return lines[-tail_lines:]

    def _render_lines(self) -> list[str]:
        lines = [
            "# NexusCrew 共享记忆",
            "",
            "> 运行时记忆主存储已迁移到 SQLite；本文件仅保留摘要镜像。",
            "",
        ]
        with self._connect() as conn:
            sections = conn.execute(
                "SELECT header, content FROM memory_sections ORDER BY updated_at, header"
            ).fetchall()
            notes = conn.execute(
                """
                SELECT actor, content, created_at
                FROM memory_notes
                ORDER BY id DESC
                LIMIT ?
                """,
                (self.SUMMARY_NOTE_LIMIT,),
            ).fetchall()
        if not sections:
            lines.extend(["## 项目基础信息", "", "(由 /crew 命令自动填充)", ""])
        for row in sections:
            lines.extend([f"## {row['header']}", "", row["content"], ""])
        if notes:
            lines.extend(["## 最近记忆", ""])
            for row in reversed(notes):
                lines.extend([
                    f"---",
                    f"**[{row['created_at']}] {row['actor']}**",
                    row["content"],
                    "",
                ])
        return lines

    def _render_summary_file(self):
        self.path.write_text("\n".join(self._render_lines()).rstrip() + "\n", encoding="utf-8")
