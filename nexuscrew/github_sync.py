"""GitHub conversation sync for tasks and task-linked discussions."""
import asyncio
import json
import math
import urllib.error
import urllib.request
from .http_retry import NETWORK_ERRORS, with_retries

GITHUB_SYNC_ERRORS = (urllib.error.HTTPError,) + NETWORK_ERRORS


class NullGitHubSync:
    """No-op GitHub sink when GitHub sync is disabled or unconfigured."""

    async def ensure_task_issue(self, task, initial_message: str = "") -> None:
        return None

    async def mirror_comment(self, task, actor: str, body: str) -> None:
        return None


class GitHubConversationSync:
    """Mirror Telegram task conversations to GitHub issues/comments."""
    ISSUE_BODY_LIMIT = 60000
    COMMENT_BODY_LIMIT = 60000

    def __init__(
        self,
        repo: str,
        token: str,
        api_url: str = "https://api.github.com",
        labels: list[str] | None = None,
        title_prefix: str = "NexusCrew",
        timeout: int = 20,
    ):
        self.repo = repo
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.labels = labels or []
        self.title_prefix = title_prefix
        self.timeout = timeout

    async def ensure_task_issue(self, task, initial_message: str = "") -> None:
        if getattr(task, "github_issue_number", 0):
            return
        ok, result = await asyncio.to_thread(
            self._safe_create_issue,
            task,
            initial_message,
        )
        if not ok:
            return
        task.github_issue_number = result["number"]
        task.github_issue_url = result["html_url"]

    async def mirror_comment(self, task, actor: str, body: str) -> None:
        if not body.strip():
            return
        await self.ensure_task_issue(task)
        if not getattr(task, "github_issue_number", 0):
            return
        for part in self._split_comment(actor, body):
            ok, _ = await asyncio.to_thread(
                self._safe_create_comment,
                task.github_issue_number,
                part,
            )
            if not ok:
                return

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "X-GitHub-Api-Version": "2022-11-28",
        }

    def _request(self, method: str, path: str, payload: dict) -> dict:
        def do_request():
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                f"{self.api_url}{path}",
                data=data,
                headers=self._headers(),
                method=method,
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))

        return with_retries(do_request)

    def _create_issue(self, task, initial_message: str) -> dict:
        title = f"{self.title_prefix} [{task.id}] {task.description[:80]}"
        body = (
            f"## NexusCrew Task Mirror\n\n"
            f"- Task ID: `{task.id}`\n"
            f"- Created From: Telegram\n"
            f"- Status: `{task.status.value}`\n"
            f"- Assigned To: `{task.assigned_to or 'unassigned'}`\n\n"
            f"### Initial Request\n\n{initial_message or task.description}"
        )
        body = self._truncate(body, self.ISSUE_BODY_LIMIT)
        payload = {"title": title, "body": body}
        if self.labels:
            payload["labels"] = self.labels
        return self._request("POST", f"/repos/{self.repo}/issues", payload)

    def _create_comment(self, issue_number: int, body: str) -> dict:
        return self._request(
            "POST",
            f"/repos/{self.repo}/issues/{issue_number}/comments",
            {"body": body},
        )

    def _safe_create_issue(self, task, initial_message: str):
        try:
            return True, self._create_issue(task, initial_message)
        except GITHUB_SYNC_ERRORS:
            return False, None

    def _safe_create_comment(self, issue_number: int, body: str):
        try:
            return True, self._create_comment(issue_number, body)
        except GITHUB_SYNC_ERRORS:
            return False, None

    def _format_comment(self, actor: str, body: str) -> str:
        return f"### {actor}\n\n{body}"

    def _truncate(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        suffix = "\n\n...[truncated by NexusCrew for GitHub mirror]..."
        return text[: limit - len(suffix)] + suffix

    def _split_comment(self, actor: str, body: str) -> list[str]:
        header = f"### {actor}\n\n"
        limit = self.COMMENT_BODY_LIMIT - len(header)
        if limit <= 0:
            return [header]
        if len(body) <= limit:
            return [self._format_comment(actor, body)]
        parts = []
        total = math.ceil(len(body) / limit)
        for idx, start in enumerate(range(0, len(body), limit), start=1):
            chunk = body[start:start + limit]
            chunk_header = f"### {actor} ({idx}/{total})\n\n" if total > 1 else header
            parts.append(chunk_header + chunk)
        return parts
