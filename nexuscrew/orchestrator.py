"""Core orchestrator — agent chain runner."""
import asyncio
from pathlib import Path
from .registry import AgentRegistry
from .router import Router
from .memory.crew_memory import CrewMemory
from .executor.shell import ShellExecutor

MAX_CHAIN_HOPS = 10
MAX_DEV_RETRY  = 5


class Orchestrator:
    def __init__(self, registry: AgentRegistry, router: Router,
                 crew_memory: CrewMemory, shell_executor: ShellExecutor,
                 max_chain_hops: int = MAX_CHAIN_HOPS,
                 max_dev_retry: int = MAX_DEV_RETRY):
        self.registry      = registry
        self.router        = router
        self.crew_memory   = crew_memory
        self.executor      = shell_executor
        self.max_hops      = max_chain_hops
        self.max_retry     = max_dev_retry
        # per chat_id state
        self._histories: dict[int, list[dict]] = {}
        self._dev_retries: dict[int, int]       = {}

    # ── history helpers ───────────────────────────────────────────────
    def _add_history(self, chat_id: int, agent: str, content: str):
        h = self._histories.setdefault(chat_id, [])
        h.append({"agent": agent, "content": content})
        if len(h) > 20:
            self._histories[chat_id] = h[-20:]

    def reset_history(self, chat_id: int):
        self._histories.pop(chat_id, None)
        self._dev_retries.pop(chat_id, None)

    # ── main entry point ──────────────────────────────────────────────
    async def run_chain(
        self,
        message: str,
        chat_id: int,
        send,            # async callable(text: str)
        initial_agent=None,
    ):
        """
        Run the agent chain starting from initial_agent (or router default).
        Each agent reply is scanned for @mentions to route the next hop.
        """
        agent = initial_agent or self.router.detect_first(message) \
                              or self.router.default_agent()
        if not agent:
            await send("[NexusCrew] 没有可用的 Agent，请先使用 /crew 编组。")
            return

        self._add_history(chat_id, "human", message)

        for hop in range(self.max_hops):
            # Auto-escalate dev on too many failures
            if agent.role == "dev" and \
               self._dev_retries.get(chat_id, 0) >= self.max_retry:
                arch = self.registry.get_by_role("architect")
                if arch:
                    await send(f"⚠️ {agent.name} 连续失败 {self.max_retry} 次，自动升级给 @{arch.name}。")
                    message = (f"@{arch.name} 自动升级求助（Dev 连续失败 {self.max_retry} 次）：\n"
                               f"{message}")
                    agent = arch
                    self._dev_retries[chat_id] = 0

            # Call agent
            await send(f"_[{agent.name}/{agent.model_label} 处理中...]_")
            history  = self._histories.get(chat_id, [])
            memory   = self.crew_memory.read()
            reply, artifacts = await agent.handle(message, history, memory)

            # Persist memory note
            if artifacts.memory_note:
                self.crew_memory.append(agent.name, artifacts.memory_note)

            # Track dev failures
            if agent.role == "dev" and artifacts.shell_output:
                if self.executor.is_failure(artifacts.shell_output):
                    self._dev_retries[chat_id] = \
                        self._dev_retries.get(chat_id, 0) + 1
                else:
                    self._dev_retries[chat_id] = 0

            # Send reply + shell output to Telegram
            self._add_history(chat_id, agent.name, reply)
            await send(f"**[{agent.name}]**\n{reply}")
            if artifacts.shell_output:
                await send(f"```\n{artifacts.shell_output[:3500]}\n```")
                self._add_history(chat_id, "shell", artifacts.shell_output[:600])

            # Detect next agent
            next_agents = self.router.detect_all(reply)
            if not next_agents:
                break
            next_agent = next_agents[0]
            if next_agent.name == agent.name:
                break  # self-reference guard

            # Parallel dispatch if PM mentioned multiple devs
            if len(next_agents) > 1 and all(a.role == "dev" for a in next_agents):
                await asyncio.gather(*[
                    self.run_chain(reply, chat_id, send, a)
                    for a in next_agents
                ])
                return

            message = reply
            agent   = next_agent
        else:
            await send("⚠️ 达到最大跳转上限，请人工介入。")
