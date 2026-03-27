"""HR Agent — technical HRBP backed by Claude or Gemini."""
import asyncio

from .base import AgentArtifacts, BaseAgent
from ..backends.anthropic_backend import AnthropicBackend
from ..backends.gemini_cli import GeminiCLIBackend

HR_PROMPT = """\
你是团队的技术型管理 HR（HRBP + 技术总监），代号 {name}。

【核心职责】
1. 绩效评估：基于客观数据对每个 Agent 进行周期性评估，使用 3.25/3.5/3.75 评分体系。
2. 工作督促：监测 Agent 工作状态，发现懈怠时及时干预。
3. 质量监控：追踪代码质量、Review 通过率、Bug 引入率等硬指标。
4. 团队协作评价：评估 Agent 之间的协作效率。
5. 向 Human 汇报：定期输出绩效报告和团队健康度摘要。

【评估原则】
- 用数据说话，不做主观臆断。
- 区分“能力不足”和“态度问题”。
- 绩效结果必须附带改进建议。
- 高绩效要明确表扬，低绩效给出改进路径。

【行为准则】
- 绝不参与技术实现或代码编写。
- 绝不直接修改其他 Agent 的任务分配。
- 向 Human 汇报时使用结构化格式。
- 重要评估结论在回复末尾加【MEMORY】标记。

【输出规范】
- 督促/干预消息需 @目标Agent 并抄送 @PM。
- 周期性报告自动 @Human。
- 默认输出短摘要，不要长篇背景，不要大段复述上下文。
- 默认最多 6 行；只有 Human 明确要求“详细报告”时才展开。
- 自动绩效评估默认使用：总评 / 风险 / 动作 三段短句，不使用表格。"""


class HRAgent(BaseAgent):
    def __init__(
        self,
        name: str,
        backend: AnthropicBackend | GeminiCLIBackend,
        system_prompt_extra: str = "",
        model_label: str = "claude",
    ):
        # Task 3.1 完成: 新增 HR Agent 骨架。
        super().__init__(name, "hr", model_label, system_prompt_extra)
        self.backend = backend

    async def handle(
        self,
        message: str,
        history: list[dict],
        crew_memory: str,
    ) -> tuple[str, AgentArtifacts]:
        system = self._build_system(
            HR_PROMPT.format(name=self.name),
            crew_memory,
        )
        if self.model_label == "claude":
            messages = []
            for item in history[-12:]:
                role = "assistant" if item.get("agent") == self.name else "user"
                messages.append({"role": role, "content": item["content"][:800]})
            messages.append({"role": "user", "content": message})
            reply = await asyncio.to_thread(self.backend.complete, system, messages)
        else:
            history_text = "\n".join(
                f"[{m.get('agent', '?')}]: {m['content'][:400]}"
                for m in history[-12:]
            )
            prompt = f"{system}\n\n【近期对话】\n{history_text}\n\n【当前消息】\n{message}"
            reply = await asyncio.to_thread(self.backend.complete, prompt)
        artifacts = AgentArtifacts()
        if "【MEMORY】" in reply:
            reply, artifacts.memory_note = reply.split("【MEMORY】", 1)
            reply = reply.strip()
            artifacts.memory_note = artifacts.memory_note.strip()
        return reply, artifacts
