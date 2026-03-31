"""Merge readiness summaries."""
from dataclasses import dataclass, field


@dataclass
class MergeGateSummary:
    ready: bool
    summary: str
    reasons: list[str] = field(default_factory=list)


class MergeGate:
    """Build merge-ready summaries from task state, approvals, artifacts, and CI."""

    def build(self, task, ci_result, approvals, artifacts) -> MergeGateSummary:
        pending = [approval for approval in approvals if approval.status == "pending"]
        reasons: list[str] = []
        if pending:
            pending_labels = ", ".join(
                f"{approval.id}:{approval.risk_level}/{approval.action_type}"
                for approval in pending[:3]
            )
            suffix = "" if len(pending) <= 3 else f" 等 {len(pending)} 项"
            reasons.append(f"待审批 {pending_labels}{suffix}")
        if getattr(task, "github_pr_number", 0) == 0:
            reasons.append("尚未创建 PR")
        if ci_result.status == "failed":
            reasons.append(f"CI 失败: {ci_result.summary}")
        elif ci_result.status == "pending":
            reasons.append(f"CI 进行中: {ci_result.summary}")
        if getattr(task, "blocked_reason", ""):
            reasons.append(f"任务阻塞: {task.blocked_reason}")
        if getattr(task, "status", None) is not None and getattr(task.status, "value", str(task.status)) not in ("accepted", "done", "validating"):
            reasons.append(f"任务状态未就绪: {getattr(task.status, 'value', str(task.status))}")
        if not artifacts:
            reasons.append("缺少 artifact 摘要")
        ready = not reasons
        summary = "可合并" if ready else "不可合并: " + " / ".join(reasons)
        return MergeGateSummary(ready=ready, summary=summary, reasons=reasons)
