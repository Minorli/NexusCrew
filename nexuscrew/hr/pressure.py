"""Pressure system — inject HR performance context through shared memory."""
from ..memory.crew_memory import CrewMemory
from ..metrics import AgentMetrics


PRESSURE_LEVELS = {
    0: "normal",
    1: "reminder",
    2: "warning",
    3: "pip",
    4: "replacement",
}


def calculate_pressure_level(
    current_score: float,
    score_history: list[float],
) -> int:
    if current_score >= 3.5:
        if len(score_history) >= 2 and all(
            score_history[i] > score_history[i + 1]
            for i in range(len(score_history) - 1)
        ):
            return 1
        return 0

    if current_score >= 3.25:
        consecutive_low = sum(1 for score in score_history[-3:] if score <= 3.25)
        if consecutive_low >= 3:
            return 4
        if consecutive_low >= 2:
            return 3
        return 2

    return 4


def build_pressure_prompt(
    agent_name: str,
    level: int,
    metrics: AgentMetrics,
    peer_feedback: str = "",
    max_len: int = 500,
) -> str:
    if level == 0:
        return "状态正常，继续保持。"

    parts = [f"【HR 绩效通知 — {agent_name}】"]

    if level >= 1:
        parts.append(f"当前指标: {metrics.to_summary()}")

    if level >= 2:
        parts.append("\n⚠️ 正式警告：以下指标低于团队标准：")
        if metrics.first_pass_rate < 0.6:
            parts.append(f"- 首次通过率: {metrics.first_pass_rate:.0%}（标准 ≥60%）")
        if metrics.retry_ratio > 2.0:
            parts.append(f"- 平均 retry: {metrics.retry_ratio:.1f}（标准 ≤2.0）")
        if peer_feedback:
            parts.append(f"\n同事反馈: {peer_feedback}")
        parts.append("要求：下一个任务重点关注质量，提交前自行检查。")

    if level >= 3:
        parts.append("\n🚨 绩效改进计划（PIP）已启动：")
        parts.append("- 目标: 首次通过率提升至 60% 以上")
        parts.append("- 评估周期: 接下来 3 个任务")
        parts.append("- 未达标: 向 Human 建议更换模型或调整角色")

    if level >= 4:
        parts.append("\n❌ 已向 Human 提交替换建议。")

    return "\n".join(parts)[:max_len]


def apply_pressure(
    crew_memory: CrewMemory,
    agent_name: str,
    level: int,
    metrics: AgentMetrics,
    peer_feedback: str = "",
    max_len: int = 500,
):
    # Task 3.4 完成: 将 HR 督促信息写入共享记忆，供后续 Agent prompt 注入。
    prompt = build_pressure_prompt(
        agent_name,
        level,
        metrics,
        peer_feedback=peer_feedback,
        max_len=max_len,
    )
    crew_memory.overwrite_section(f"HR通知-{agent_name}", prompt)
