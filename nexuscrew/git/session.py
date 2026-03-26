"""Branch session tracking for task-oriented git workflows."""
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class BranchSession:
    chat_id: int
    task_id: str
    branch_name: str
    base_branch: str = "unknown"
    pr_number: int = 0
    pr_url: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = ""
    commits: list[str] = field(default_factory=list)

    def touch(self):
        self.updated_at = datetime.now().isoformat()


class BranchSessionStore:
    """Persist branch sessions as latest snapshots."""

    def __init__(self, path: Path):
        self.path = path
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def save(self, session: BranchSession):
        session.touch()
        sessions = {(s.chat_id, s.task_id): s for s in self.read_all()}
        sessions[(session.chat_id, session.task_id)] = session
        with self.path.open("w", encoding="utf-8") as f:
            for item in sessions.values():
                f.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")

    def read_all(self) -> list[BranchSession]:
        if not self.path.exists():
            return []
        rows: list[BranchSession] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                rows.append(BranchSession(**json.loads(line)))
        return rows

    def get(self, chat_id: int, task_id: str) -> BranchSession | None:
        for session in self.read_all():
            if session.chat_id == chat_id and session.task_id == task_id:
                return session
        return None
