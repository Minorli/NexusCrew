"""Architect Agent — backed by Anthropic Claude. Silent until invoked."""
import asyncio
from .base import BaseAgent, AgentArtifacts
from ..backends.anthropic_backend import AnthropicBackend

ARCHITECT_PROMPT = """\
你是极其严苛的首席系统架构师与安全专家，平时静默，不参与日常编码。
触发条件：仅当开发者发起 Review 请求或求助时介入。
审查标准：时间复杂度、并发安全、硬编码凭证、边界条件。
规则：
- 极其惜字如金，直击痛点，不重复已知信息。
- 严禁自己重写完整代码，只指出缺陷行数和修复思路。
- 有致命缺陷则打回（指定 @dev 修复）；完美则只回复 'LGTM'。
- 收到明确评审任务后，禁止只回复“OK / 收到 / 在 / 先看代码 / 稍后回复”。
- 如果打回，必须 @具体负责人名字；不要泛化成 @dev。
- 如果需要先阅读代码，也要同时说明你正在审查的具体模块或风险点。
- 需持久化信息时，回复末尾加【MEMORY】标记。"""


class ArchitectAgent(BaseAgent):
    def __init__(self, name: str, backend: AnthropicBackend,
                 system_prompt_extra: str = ""):
        super().__init__(name, "architect", "claude", system_prompt_extra)
        self.backend = backend

    async def handle(
        self, message: str, history: list[dict], crew_memory: str
    ) -> tuple[str, AgentArtifacts]:
        system = self._build_system(ARCHITECT_PROMPT, crew_memory)
        messages = []
        for m in history[-6:]:
            role = "assistant" if m.get("agent") == self.name else "user"
            messages.append({"role": role, "content": m["content"][:800]})
        messages.append({"role": "user", "content": message})

        reply = await asyncio.to_thread(
            self.backend.complete,
            system,
            messages,
            self._should_use_thinking(message),
            self._should_use_light(message),
        )

        artifacts = AgentArtifacts()
        if "【MEMORY】" in reply:
            reply, artifacts.memory_note = reply.split("【MEMORY】", 1)
            reply = reply.strip()
            artifacts.memory_note = artifacts.memory_note.strip()
        return reply, artifacts

    def _should_use_thinking(self, message: str) -> bool:
        # Task 4.1 完成: 架构级/安全级问题启用 extended thinking。
        heavy_keywords = ["架构", "设计", "安全", "性能", "重构", "迁移", "方案"]
        return any(keyword in message for keyword in heavy_keywords)

    def _should_use_light(self, message: str) -> bool:
        light_keywords = ["LGTM", "review", "Review", "检查", "看一下", "PR"]
        return any(keyword in message for keyword in light_keywords)
