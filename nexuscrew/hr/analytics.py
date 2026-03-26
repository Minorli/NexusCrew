"""HR trend reports and staffing/model recommendations."""


def build_trend_report(metrics_store, agent_names: list[str]) -> str:
    lines = ["📈 HR 趋势报告：", ""]
    for name in agent_names:
        history = metrics_store.get_score_history(name, last_n=5)
        if not history:
            lines.append(f"  {name}: 暂无历史评分")
            continue
        trend = "→"
        if len(history) >= 2 and history[-1] > history[-2]:
            trend = "↑"
        elif len(history) >= 2 and history[-1] < history[-2]:
            trend = "↓"
        lines.append(f"  {name}: {' -> '.join(map(str, history))} {trend}")
    return "\n".join(lines)


def recommend_staffing(metrics_store, agent_names: list[str]) -> str:
    lines = ["🧠 Staffing / Model 建议：", ""]
    for name in agent_names:
        history = metrics_store.get_score_history(name, last_n=3)
        if len(history) >= 2 and all(score <= 3.25 for score in history[-2:]):
            lines.append(f"  {name}: 建议切换更高规格模型或降低任务复杂度")
        elif history and history[-1] >= 3.75:
            lines.append(f"  {name}: 可承担更高优先级任务")
    if len(lines) == 2:
        lines.append("  当前无明确 staffing 调整建议")
    return "\n".join(lines)
