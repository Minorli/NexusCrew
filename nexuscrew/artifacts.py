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

    def list_for_task(self, task_id: str) -> list[ArtifactRecord]:
        return [
            ArtifactRecord(**json.loads(line))
            for line in self.path.read_text(encoding="utf-8").splitlines()
            if line.strip() and json.loads(line).get("task_id") == task_id
        ] if self.path.exists() else []

    def format_for_task(self, task_id: str) -> str:
        artifacts = self.list_for_task(task_id)
        if not artifacts:
            return "(无 artifacts)"
        lines = ["📦 Artifacts：", ""]
        for artifact in artifacts[-8:]:
            lines.append(f"  [{artifact.type}] {artifact.summary}")
        return "\n".join(lines)
