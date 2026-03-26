"""Slack task-thread synchronization."""
import asyncio

from .client import SlackClient


class NullSlackSync:
    """No-op Slack sink."""

    async def ensure_task_thread(self, task, initial_message: str = "") -> None:
        return None

    async def mirror_comment(self, task, actor: str, body: str) -> None:
        return None


class SlackConversationSync:
    """Mirror NexusCrew task activity into a Slack thread."""

    def __init__(
        self,
        token: str,
        default_channel: str,
        api_url: str = "https://slack.com/api",
        title_prefix: str = "NexusCrew",
    ):
        self.client = SlackClient(token=token, api_url=api_url)
        self.default_channel = default_channel
        self.title_prefix = title_prefix

    async def ensure_task_thread(self, task, initial_message: str = "") -> None:
        if getattr(task, "slack_thread_ts", ""):
            return
        response = await asyncio.to_thread(
            self.client.post_message,
            self.default_channel,
            self._build_root_message(task, initial_message),
        )
        task.slack_channel = response.get("channel", self.default_channel)
        task.slack_message_ts = response.get("ts", "")
        task.slack_thread_ts = response.get("ts", "")

    async def mirror_comment(self, task, actor: str, body: str) -> None:
        if not body.strip():
            return
        await self.ensure_task_thread(task)
        await asyncio.to_thread(
            self.client.post_message,
            task.slack_channel or self.default_channel,
            self._format_comment(actor, body),
            task.slack_thread_ts or None,
        )

    def _build_root_message(self, task, initial_message: str) -> str:
        return (
            f"*{self.title_prefix}* `[{task.id}]` {task.description}\n"
            f"Status: `{task.status.value}`\n"
            f"Assignee: `{task.assigned_to or 'unassigned'}`\n\n"
            f"{initial_message or task.description}"
        )

    def _format_comment(self, actor: str, body: str) -> str:
        return f"*{actor}*\n{body}"
