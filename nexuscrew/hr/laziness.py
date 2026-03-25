"""Laziness detection — heuristic patterns for agent underperformance."""
import re
from difflib import SequenceMatcher


def detect_shallow_response(reply: str) -> bool:
    if len(reply.strip()) < 100:
        return True
    filler_phrases = ["好的", "收到", "明白", "了解", "没问题"]
    filler_count = sum(reply.count(phrase) for phrase in filler_phrases)
    return filler_count >= 3


def detect_execution_avoidance(reply: str, role: str) -> bool:
    if role != "dev":
        return False
    has_code = "```" in reply
    has_suggestion = any(keyword in reply for keyword in ["建议", "可以考虑", "推荐", "方案"])
    return not has_code and has_suggestion


def detect_stale_retry(
    current_reply: str,
    previous_reply: str,
    threshold: float = 0.85,
) -> bool:
    if not previous_reply:
        return False
    ratio = SequenceMatcher(
        None,
        current_reply[:500],
        previous_reply[:500],
    ).ratio()
    return ratio > threshold


def detect_buck_passing(reply: str) -> bool:
    mentions = re.findall(r"@\w+", reply)
    code_blocks = re.findall(r"```.*?```", reply, re.DOTALL)
    code_text = " ".join(code_blocks)
    real_mentions = [mention for mention in mentions if mention not in code_text]

    has_own_work = any(keyword in reply for keyword in [
        "我已", "完成", "实现", "修复", "我认为", "分析", "结论",
    ])
    return len(real_mentions) > 2 and not has_own_work


def detect_all(
    reply: str,
    role: str,
    previous_reply: str = "",
) -> list[str]:
    # Task 3.5 完成: 提供懈怠/推诿检测器集合。
    triggered: list[str] = []
    if detect_shallow_response(reply):
        triggered.append("shallow_response: 回复敷衍，缺少实质内容")
    if detect_execution_avoidance(reply, role):
        triggered.append("execution_avoidance: Dev 应执行代码而非仅给建议")
    if detect_stale_retry(reply, previous_reply):
        triggered.append("stale_retry: 重试内容与上次高度相似")
    if detect_buck_passing(reply):
        triggered.append("buck_passing: 过度推诿，未尝试自行解决")
    return triggered
