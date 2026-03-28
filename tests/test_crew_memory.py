"""Tests for crew memory tail reads and auto compaction."""
from pathlib import Path

from nexuscrew.memory.crew_memory import CrewMemory


def test_crew_memory_read_returns_tail_without_full_file_load(tmp_path: Path):
    path = tmp_path / "crew_memory.md"
    path.write_text("\n".join(f"line {i}" for i in range(200)), encoding="utf-8")

    mem = CrewMemory(path)

    assert mem.read(tail_lines=3) == "line 197\nline 198\nline 199"


def test_crew_memory_compacts_large_file_before_overwrite(tmp_path: Path, monkeypatch):
    path = tmp_path / "crew_memory.md"
    path.write_text(
        "# NexusCrew 共享记忆\n\n" + "\n".join(f"memo {i}" for i in range(5000)),
        encoding="utf-8",
    )
    monkeypatch.setattr(CrewMemory, "MAX_FILE_BYTES", 512, raising=False)
    monkeypatch.setattr(CrewMemory, "COMPACT_KEEP_LINES", 20, raising=False)
    monkeypatch.setattr(CrewMemory, "TAIL_READ_BYTES", 256, raising=False)

    mem = CrewMemory(path)
    mem.overwrite_section("项目简报", "briefing")

    text = path.read_text(encoding="utf-8")
    assert "## 项目简报" in text
    assert "briefing" in text
    assert "## 历史摘要（自动压缩）" in text
    assert path.stat().st_size < 4096


def test_crew_memory_append_reads_from_sqlite_primary_store(tmp_path: Path):
    path = tmp_path / "crew_memory.md"
    mem = CrewMemory(path)

    mem.append("alice", "记住这个约定")

    assert "记住这个约定" in mem.read(tail_lines=20)
    assert path.with_suffix(".db").exists()
