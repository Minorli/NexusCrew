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

        reply = await asyncio.to_thread(self.backend.complete, system, messages)

        artifacts = AgentArtifacts()
        if "【MEMORY】" in reply:
            reply, artifacts.memory_note = reply.split("【MEMORY】", 1)
            reply = reply.strip()
            artifacts.memory_note = artifacts.memory_note.strip()
        return reply, artifacts
