"""Multi-Bot dispatcher — receives via one bot, sends via per-agent bots."""
from telegram import Bot

from ..local_secrets import load_local_secrets
from .formatter import chunk

cfg = load_local_secrets()


class AgentBotPool:
    """Manage per-agent Telegram bots with dispatcher fallback."""

    def __init__(self, fallback_token: str):
        # Task 2.1 完成: 提供多 Bot 发送池与 dispatcher fallback。
        self._fallback = Bot(token=fallback_token)
        self._bots: dict[str, Bot] = {}
        self._bot_ids: dict[str, int] = {}
        for agent_name, token in cfg.AGENT_BOT_TOKENS.items():
            if token:
                self._bots[agent_name] = Bot(token=token)

    def get_bot(self, agent_name: str | None) -> Bot:
        if not agent_name:
            return self._fallback
        # Task 2.3 完成: 无专属 token 时自动降级为 dispatcher 单 Bot 模式。
        return self._bots.get(agent_name, self._fallback)

    async def send_as_agent(self, agent_name: str | None, chat_id: int, text: str):
        bot = self.get_bot(agent_name)
        for part in chunk(text):
            await bot.send_message(chat_id=chat_id, text=part)

    @property
    def is_multi_bot(self) -> bool:
        return bool(self._bots)

    async def validate_group(self, chat_id: int) -> list[str]:
        # Task 2.2 完成: 校验专属 Agent Bot 是否已加入目标群组。
        missing: list[str] = []
        for agent_name, bot in self._bots.items():
            try:
                bot_id = self._bot_ids.get(agent_name)
                if bot_id is None:
                    bot_id = (await bot.get_me()).id
                    self._bot_ids[agent_name] = bot_id
                member = await bot.get_chat_member(chat_id, bot_id)
                if member.status in ("left", "kicked"):
                    missing.append(agent_name)
            except Exception:
                missing.append(agent_name)
        return missing
