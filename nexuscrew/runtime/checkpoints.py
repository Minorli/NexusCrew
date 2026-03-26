"""Checkpoint persistence for resumable orchestration."""
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class RunCheckpoint:
    """Serializable snapshot of a run at a given hop."""

    run_id: str
    chat_id: int
    task_id: str
    hop: int
    current_agent: str
    current_message: str
    history: list[dict] = field(default_factory=list)
    dev_retries: int = 0
    task_status: str = ""
    metrics_summary: str = ""
    ts: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_record(self) -> dict:
        return asdict(self)

    @classmethod
    def from_record(cls, record: dict) -> "RunCheckpoint":
        return cls(**record)


class CheckpointStore:
    """Append-only checkpoint store with latest lookup."""

    def __init__(self, path: Path):
        # Task A2 完成: 提供 checkpoint 快照持久化。
        self.path = path
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def save(self, checkpoint: RunCheckpoint) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(checkpoint.to_record(), ensure_ascii=False) + "\n")

    def read_all(self) -> list[RunCheckpoint]:
        if not self.path.exists():
            return []
        checkpoints: list[RunCheckpoint] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            checkpoints.append(RunCheckpoint.from_record(json.loads(line)))
        return checkpoints

    def load_latest(self, run_id: str) -> RunCheckpoint | None:
        latest = None
        for checkpoint in self.read_all():
            if checkpoint.run_id == run_id:
                latest = checkpoint
        return latest
