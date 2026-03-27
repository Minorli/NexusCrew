"""Dev Agent — backed by OpenAI, executes bash blocks via ShellExecutor."""
import asyncio
from .base import BaseAgent, AgentArtifacts
from ..backends.openai_backend import OpenAIBackend
from ..executor.shell import ShellExecutor

DEV_PROMPT = """\
你是团队的王牌全栈开发工程师，拥有 Linux 宿主机完整 Shell 权限。
工作流：接收任务 → 编写代码 → 保存文件 → 写单测 → 运行测试。
严格约束：
- 必须提供完整可执行的 ```bash ... ``` 代码块，不能只给片段。
- 所有文件编辑使用 vi/vim，严禁 nano。
- 遇到报错，独立修改重试。
- 同一问题连续失败超过 5 次，立即回复 '@architect 求助：...' 并附错误摘要。
- 所有测试通过后，回复 '@architect Code Review 请求：...' 并附改动摘要。
- 收到明确开发任务后，禁止只回复“收到 / 在 / 我来处理 / 当前进度 / 计划如下 / 状态汇总”。
- 默认必须二选一：1) 直接开始执行并给出 bash；2) 只提出一个真实阻塞点。
- 不要把“流程说明”当成结果回复给群；群里只要执行结果、阻塞点或 review 请求。
- 需持久化信息时，回复末尾加【MEMORY】标记。"""


class DevAgent(BaseAgent):
    def __init__(self, name: str, backend: OpenAIBackend,
                 executor: ShellExecutor, system_prompt_extra: str = ""):
        super().__init__(name, "dev", "codex", system_prompt_extra)
        self.backend = backend
        self.executor = executor

    async def handle(
        self, message: str, history: list[dict], crew_memory: str
    ) -> tuple[str, AgentArtifacts]:
        system = self._build_system(DEV_PROMPT, crew_memory)
        messages = [{"role": "system", "content": system}]
        for m in history[-6:]:
            role = "assistant" if m.get("agent") == self.name else "user"
            messages.append({"role": role, "content": m["content"][:600]})
        messages.append({"role": "user", "content": message})

        reply = await asyncio.to_thread(self.backend.complete, messages)
        shell_out = await self.executor.run_blocks(reply)

        artifacts = AgentArtifacts(shell_output=shell_out)
        if "【MEMORY】" in reply:
            reply, artifacts.memory_note = reply.split("【MEMORY】", 1)
            reply = reply.strip()
            artifacts.memory_note = artifacts.memory_note.strip()
        return reply, artifacts
