"""Runtime event models."""
from dataclasses import asdict, dataclass, field
from datetime import datetime
from uuid import uuid4


@dataclass
class RunEvent:
    """One append-only orchestration event."""

    run_id: str
    chat_id: int
    type: str
    actor: str
    payload: dict = field(default_factory=dict)
    task_id: str = ""
    id: str = field(default_factory=lambda: uuid4().hex)
    ts: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_record(self) -> dict:
        return asdict(self)

    @classmethod
    def from_record(cls, record: dict) -> "RunEvent":
        return cls(**record)
