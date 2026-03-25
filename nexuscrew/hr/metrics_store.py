"""Metrics history — append-only JSONL store for HR trend analysis."""
import json
from datetime import datetime
from pathlib import Path

from ..metrics import AgentMetrics


class MetricsStore:
    """Persist evaluation snapshots to JSONL."""

    def __init__(self, path: Path):
        # Task 4.4 完成: 持久化 HR 指标快照，支持趋势分析。
        self.path = path

    def append_snapshot(
        self,
        chain_id: int,
        agent_name: str,
        score: float,
        metrics: AgentMetrics,
    ):
        record = {
            "ts": datetime.now().isoformat(),
            "chain_id": chain_id,
            "agent": agent_name,
            "score": score,
            "completion_rate": metrics.completion_rate,
            "first_pass_rate": metrics.first_pass_rate,
            "avg_response_s": metrics.avg_response_time_s,
            "retry_ratio": metrics.retry_ratio,
            "tasks_completed": metrics.tasks_completed,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read_history(self, agent_name: str, last_n: int = 10) -> list[dict]:
        if not self.path.exists():
            return []
        records: list[dict] = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            if record.get("agent") == agent_name:
                records.append(record)
        return records[-last_n:]

    def get_score_history(self, agent_name: str, last_n: int = 5) -> list[float]:
        return [record["score"] for record in self.read_history(agent_name, last_n)]
