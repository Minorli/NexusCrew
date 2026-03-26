"""Slack App Home helpers."""
import asyncio

from .client import SlackClient


def build_home_view(snapshot: dict) -> dict:
    agents = snapshot.get("agents", [])
    tasks = snapshot.get("tasks", [])
    approvals = snapshot.get("approvals", [])
    background_runs = snapshot.get("background_runs", [])
    doctor = snapshot.get("doctor", "") or "(暂无诊断)"

    return {
        "type": "home",
        "blocks": [
            {"type": "header", "text": {"type": "plain_text", "text": "NexusCrew Control Center"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Agents*: {len(agents)}  |  *Tasks*: {len(tasks)}  |  *Approvals*: {len(approvals)}"}},
            {"type": "section", "text": {"type": "mrkdwn", "text": f"*Background Jobs*: {len(background_runs)}"}},
            {"type": "divider"},
            {"type": "section", "text": {"type": "mrkdwn", "text": "*Doctor Summary*\n" + doctor[:2800]}},
        ],
    }


class SlackAppHomePublisher:
    """Publish NexusCrew status into Slack App Home."""

    def __init__(self, token: str, api_url: str = "https://slack.com/api"):
        self.client = SlackClient(token=token, api_url=api_url)

    async def publish(self, user_id: str, snapshot: dict):
        await asyncio.to_thread(
            self.client.publish_view,
            user_id,
            build_home_view(snapshot),
        )
