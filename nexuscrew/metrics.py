"""Agent performance metrics — collection and derived calculations."""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class AgentMetrics:
    """Per-agent raw metrics, updated after each handle() call."""

    tasks_assigned: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0

    total_response_time_ms: int = 0
    total_retries: int = 0
    escalations: int = 0

    review_pass_first: int = 0
    review_reject: int = 0
    shell_commands_run: int = 0
    shell_failures: int = 0

    mentions_sent: int = 0
    mentions_received: int = 0
    memory_notes: int = 0
    laziness_signals: list[str] = field(default_factory=list)

    first_active: str = ""
    last_active: str = ""
    active_chains: int = 0

    def record_task_start(self):
        self.tasks_assigned += 1
        now = datetime.now().isoformat()
        if not self.first_active:
            self.first_active = now
        self.last_active = now
        self.active_chains += 1

    def record_task_complete(self, response_time_ms: int):
        self.tasks_completed += 1
        self.total_response_time_ms += response_time_ms

    def record_task_fail(self):
        self.tasks_failed += 1
        self.total_retries += 1

    def record_shell_run(self, is_failure: bool):
        self.shell_commands_run += 1
        if is_failure:
            self.shell_failures += 1

    def record_review_result(self, passed: bool):
        if passed:
            self.review_pass_first += 1
        else:
            self.review_reject += 1

    def record_memory_note(self):
        self.memory_notes += 1

    def record_laziness_signals(self, signals: list[str]):
        self.laziness_signals.extend(signals)

    @property
    def completion_rate(self) -> float:
        return self.tasks_completed / max(self.tasks_assigned, 1)

    @property
    def first_pass_rate(self) -> float:
        total = self.review_pass_first + self.review_reject
        return self.review_pass_first / max(total, 1)

    @property
    def avg_response_time_s(self) -> float:
        return (self.total_response_time_ms / 1000) / max(self.tasks_completed, 1)

    @property
    def retry_ratio(self) -> float:
        return self.total_retries / max(self.tasks_completed, 1)

    def to_summary(self) -> str:
        return (
            f"任务: {self.tasks_completed}/{self.tasks_assigned} "
            f"(完成率 {self.completion_rate:.0%}) | "
            f"首次通过: {self.first_pass_rate:.0%} | "
            f"平均响应: {self.avg_response_time_s:.1f}s | "
            f"Retry/任务: {self.retry_ratio:.1f}"
        )


class MetricsCollector:
    """Central metrics store keyed by agent name."""

    def __init__(self):
        # Task 3.2 完成: 引入 Agent 指标采集与汇总存储。
        self._metrics: dict[str, AgentMetrics] = {}

    def get(self, agent_name: str) -> AgentMetrics:
        if agent_name not in self._metrics:
            self._metrics[agent_name] = AgentMetrics()
        return self._metrics[agent_name]

    def all_summaries(self) -> str:
        lines = [
            "| Agent | 完成率 | 首次通过 | 平均响应 | Retry/任务 |",
            "|-------|--------|---------|---------|-----------|",
        ]
        for name, metrics in sorted(self._metrics.items()):
            lines.append(
                f"| {name} | {metrics.completion_rate:.0%} | "
                f"{metrics.first_pass_rate:.0%} | "
                f"{metrics.avg_response_time_s:.1f}s | "
                f"{metrics.retry_ratio:.1f} |"
            )
        return "\n".join(lines)

    def items(self):
        return self._metrics.items()

    def reset(self):
        self._metrics.clear()
