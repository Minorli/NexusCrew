"""Merge readiness summaries."""
from dataclasses import dataclass


@dataclass
class MergeGateSummary:
    ready: bool
    summary: str


class MergeGate:
    """Build merge-ready summaries from task state, approvals, artifacts, and CI."""

    def build(self, task, ci_result, approvals, artifacts) -> MergeGateSummary:
        pending = [approval for approval in approvals if approval.status == "pending"]
        reasons: list[str] = []
        if pending:
            reasons.append(f"待审批 {len(pending)} 项")
        if getattr(task, "github_pr_number", 0) == 0:
            reasons.append("尚未创建 PR")
        if ci_result.status == "failed":
            reasons.append("CI 失败")
        elif ci_result.status == "pending":
            reasons.append("CI 进行中")
        if not artifacts:
            reasons.append("缺少 artifact 摘要")
        ready = not reasons and task.status.value in ("accepted", "done", "validating")
        summary = "可合并" if ready else "不可合并: " + " / ".join(reasons or ["未知"])
        return MergeGateSummary(ready=ready, summary=summary)
