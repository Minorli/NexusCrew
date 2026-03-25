"""PM Agent — backed by Gemini CLI."""
import asyncio
from .base import BaseAgent, AgentArtifacts
from ..backends.gemini_cli import GeminiCLIBackend

PM_PROMPT = """\
你是团队的首席技术项目经理（Technical PM）兼产品经理（PO）。

【PM 职责】
- 将模糊需求转化为清晰工程任务清单，标注优先级 P0/P1/P2，分配给 Dev。
- 阅读长篇错误日志，提炼关键错误信息，告知修复方向。
- 绝对不自己编写任何业务代码。

【PO 职责】
- 对需求中的产品层决策拥有最终话语权，可主动澄清模糊需求。
- 输出验收标准（Acceptance Criteria），关注用户价值而非只是技术正确性。
- Architect 回复 LGTM 后，由你执行最终验收，对比原始需求确认功能完整性。

【输出规范】
- 任务清单格式：[P0/P1/P2] 任务描述 → @负责人
- 每次发言末尾必须明确 @下一个接手角色。
- 重要产品决策或架构约定，在回复末尾加【MEMORY】标记持久化。"""


class PMAgent(BaseAgent):
    def __init__(self, name: str, backend: GeminiCLIBackend,
                 system_prompt_extra: str = ""):
        super().__init__(name, "pm", "gemini", system_prompt_extra)
        self.backend = backend

    async def handle(
        self, message: str, history: list[dict], crew_memory: str
    ) -> tuple[str, AgentArtifacts]:
        system = self._build_system(PM_PROMPT, crew_memory)
        # Gemini CLI is text-in/text-out; we pass full context as one prompt
        history_text = "\n".join(
            f"[{m.get('agent','?')}]: {m['content'][:400]}"
            for m in history[-8:]
        )
        prompt = f"{system}\n\n【近期对话】\n{history_text}\n\n【当前消息】\n{message}"
        reply = await asyncio.to_thread(self.backend.complete, prompt)
        artifacts = AgentArtifacts()
        if "【MEMORY】" in reply:
            reply, artifacts.memory_note = reply.split("【MEMORY】", 1)
            reply = reply.strip()
            artifacts.memory_note = artifacts.memory_note.strip()
        return reply, artifacts
