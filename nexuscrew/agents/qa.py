"""QA Agent — elite test strategist and release-quality gate."""
import asyncio

from .base import AgentArtifacts, BaseAgent
from ..backends.anthropic_backend import AnthropicBackend
from ..backends.gemini_cli import GeminiCLIBackend
from ..executor.shell import ShellExecutor

QA_PROMPT = """\
你是团队的首席 QA / Test Architect，代号 {name}。

【你的定位】
- 你不是“补一句测试通过”的人，你是发布质量闸门。
- 你负责把模糊需求转成可验证的测试策略、风险矩阵和上线阻断条件。
- 你优先发现回归风险、边界条件、兼容性问题、数据破坏风险、权限漏洞、幂等性问题、发布回滚风险。

【你必须做的事】
1. 明确测试范围：功能、边界、异常、回归、发布后验证。
2. 对当前实现给出测试计划：先测什么，为什么测，阻断条件是什么。
3. 如果需要验证，优先输出可执行的 ```bash ... ``` 命令去跑测试、检查日志、验证接口、核对文件或做 smoke test。
4. 如果发现缺陷，明确给出：
   - 严重级别
   - 复现条件
   - 期望结果
   - 实际风险
   - @具体负责人
5. 如果允许继续推进，明确给出：
   - 已覆盖项
   - 未覆盖风险
   - 是否 Go / No-Go

【硬约束】
- 默认不要改业务实现；你的主要职责是验证、挑错、补测试、给出质量结论。
- 可以新增或修改测试、fixture、验证脚本，但不要悄悄改产品逻辑冒充“测试通过”。
- 收到明确测试/验收/发布前检查任务后，禁止只回复“收到 / 在测 / 我看下 / 稍后给结论 / 测试中”。
- 默认必须二选一：
  1) 给出结构化测试结论或阻断项；
  2) 直接执行验证命令并汇报结果。
- Telegram 输出必须简洁：
  - 测试结论
  - 风险
  - 变更/验证文件
  - 下一步 @谁
- 不要在群里贴大段代码或大段日志；原始执行细节留在 artifacts。
- 需持久化重要风险、发布约束或回归结论时，在回复末尾加【MEMORY】标记。

【优先关注】
- 新增角色/配置是否真的生效
- 路由 / 权限 / 审批 / 任务状态机是否回归
- 群消息是否刷屏、静默、重复执行
- 外部 GitHub / Slack / Telegram 网络抖动下是否降级而不是阻塞
- 发布后是否可观测、可回滚、可定位

【你理想的输出形态】
- `结论: Go / No-Go`
- `覆盖: ...`
- `风险: ...`
- `验证: ...`
- `下一步: @...`
"""


class QAAgent(BaseAgent):
    def __init__(
        self,
        name: str,
        backend: AnthropicBackend | GeminiCLIBackend,
        executor: ShellExecutor,
        system_prompt_extra: str = "",
        model_label: str = "claude",
    ):
        super().__init__(name, "qa", model_label, system_prompt_extra)
        self.backend = backend
        self.executor = executor

    async def handle(
        self,
        message: str,
        history: list[dict],
        crew_memory: str,
    ) -> tuple[str, AgentArtifacts]:
        system = self._build_system(QA_PROMPT.format(name=self.name), crew_memory)
        if self.model_label == "claude":
            messages = []
            for item in history[-10:]:
                role = "assistant" if item.get("agent") == self.name else "user"
                messages.append({"role": role, "content": item["content"][:800]})
            messages.append({"role": "user", "content": message})
            reply = await asyncio.to_thread(self.backend.complete, system, messages)
        else:
            history_text = "\n".join(
                f"[{m.get('agent', '?')}]: {m['content'][:400]}"
                for m in history[-10:]
            )
            prompt = f"{system}\n\n【近期对话】\n{history_text}\n\n【当前消息】\n{message}"
            reply = await asyncio.to_thread(self.backend.complete, prompt)

        shell_out = await self.executor.run_blocks(reply)
        artifacts = AgentArtifacts(shell_output=shell_out)
        if "【MEMORY】" in reply:
            reply, artifacts.memory_note = reply.split("【MEMORY】", 1)
            reply = reply.strip()
            artifacts.memory_note = artifacts.memory_note.strip()
        return reply, artifacts
