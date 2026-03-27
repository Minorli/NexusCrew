"""Pull request drafting and optional creation."""
import asyncio
import json
import urllib.request
from dataclasses import asdict, dataclass
from ..http_retry import with_retries, NETWORK_ERRORS


@dataclass
class PullRequestDraft:
    title: str
    body: str
    head: str
    base: str
    number: int = 0
    url: str = ""

    def to_record(self) -> dict:
        return asdict(self)


class PRWorkflow:
    """Build PR drafts and optionally create PRs on GitHub."""

    def __init__(self, repo: str = "", token: str = "",
                 api_url: str = "https://api.github.com",
                 title_prefix: str = "NexusCrew"):
        self.repo = repo
        self.token = token
        self.api_url = api_url.rstrip("/")
        self.title_prefix = title_prefix

    async def draft_for_task(self, task, branch_session, summary: str,
                             test_summary: str = "") -> PullRequestDraft:
        title = f"{self.title_prefix} [{task.id}] {task.description[:72]}"
        body = (
            "## Summary\n\n"
            f"{summary or '(待补充)'}\n\n"
            "## Validation\n\n"
            f"{test_summary or '(待补充)'}\n\n"
            f"## Linked Task\n\n- GitHub Issue: {task.github_issue_url or '(未同步)'}"
        )
        return PullRequestDraft(
            title=title,
            body=body,
            head=branch_session.branch_name,
            base=branch_session.base_branch or "main",
        )

    async def ensure_pr(self, task, branch_session, summary: str,
                        test_summary: str = "") -> PullRequestDraft:
        draft = await self.draft_for_task(task, branch_session, summary, test_summary)
        if task.github_pr_number or not self.repo or not self.token:
            return draft
        if draft.base == "unknown":
            return draft
        ok, created = self._safe_create_pull_request(draft)
        if not ok:
            return draft
        draft.number = created["number"]
        draft.url = created["html_url"]
        task.github_pr_number = draft.number
        task.github_pr_url = draft.url
        branch_session.pr_number = draft.number
        branch_session.pr_url = draft.url
        return draft

    def _create_pull_request(self, draft: PullRequestDraft) -> dict:
        def do_request():
            payload = json.dumps({
                "title": draft.title,
                "body": draft.body,
                "head": draft.head,
                "base": draft.base,
                "draft": True,
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{self.api_url}/repos/{self.repo}/pulls",
                data=payload,
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=20) as resp:
                return json.loads(resp.read().decode("utf-8"))

        return with_retries(do_request)

    def _safe_create_pull_request(self, draft: PullRequestDraft):
        try:
            return True, self._create_pull_request(draft)
        except NETWORK_ERRORS:
            return False, None
