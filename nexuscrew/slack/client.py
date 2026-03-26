"""Minimal Slack Web API client."""
import json
import urllib.request


class SlackClient:
    """Slack Web API wrapper for posting messages."""

    def __init__(self, token: str, api_url: str = "https://slack.com/api", timeout: int = 20):
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout

    def post_message(self, channel: str, text: str, thread_ts: str | None = None) -> dict:
        payload = {"channel": channel, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts
        return self._post("chat.postMessage", payload)

    def publish_view(self, user_id: str, view: dict) -> dict:
        return self._post("views.publish", {"user_id": user_id, "view": view})

    def _post(self, method: str, payload: dict) -> dict:
        request = urllib.request.Request(
            f"{self.api_url}/{method}",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
