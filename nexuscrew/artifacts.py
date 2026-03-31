"""Artifact store for execution side outputs."""
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from uuid import uuid4


@dataclass
class ArtifactRecord:
    task_id: str
    run_id: str
    type: str
    source: str
    summary: str
    content: str = ""
    chat_id: int = 0
    id: str = field(default_factory=lambda: uuid4().hex)
    ts: str = field(default_factory=lambda: datetime.now().isoformat())


class ArtifactStore:
    """Append-only artifact metadata store."""

    def __init__(self, path: Path):
        self.path = path
        if not self.path.exists():
            self.path.write_text("", encoding="utf-8")

    def append(self, artifact: ArtifactRecord):
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(asdict(artifact), ensure_ascii=False) + "\n")

    def list_for_task(self, task_id: str, chat_id: int | None = None) -> list[ArtifactRecord]:
        if not self.path.exists():
            return []
        records: list[ArtifactRecord] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            payload = json.loads(line)
            if payload.get("task_id") != task_id:
                continue
            if chat_id is not None and payload.get("chat_id") not in (None, 0, chat_id):
                continue
            records.append(ArtifactRecord(**payload))
        return records

    def format_for_task(self, task_id: str, chat_id: int | None = None) -> str:
        artifacts = self.list_for_task(task_id, chat_id=chat_id)
        if not artifacts:
            return "(无 artifacts)"
        lines = ["📦 Artifacts：", ""]
        for artifact in artifacts[-8:]:
            lines.append(f"  [{artifact.type}] {artifact.summary}")
        return "\n".join(lines)
