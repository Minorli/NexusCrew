"""CI status ingestion helpers."""
import asyncio
import json
import subprocess
from dataclasses import dataclass, field


@dataclass
class CIResult:
    status: str = "unknown"
    summary: str = "(无 CI 数据)"
    checks: list[dict] = field(default_factory=list)


class CIResultProvider:
    """Read CI results through gh CLI when available."""

    async def get_for_pr(self, pr_number: int) -> CIResult:
        if not pr_number:
            return CIResult()
        return await asyncio.to_thread(self._read_gh_checks, pr_number)

    def get_for_pr_sync(self, pr_number: int) -> CIResult:
        if not pr_number:
            return CIResult()
        return self._read_gh_checks(pr_number)

    def _read_gh_checks(self, pr_number: int) -> CIResult:
        try:
            result = subprocess.run(
                [
                    "gh", "pr", "checks", str(pr_number),
                    "--json", "name,state,link",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return CIResult()
            checks = json.loads(result.stdout)
            if not checks:
                return CIResult()
            status = "passed"
            if any(item.get("state") in ("FAILURE", "ERROR", "CANCELLED") for item in checks):
                status = "failed"
            elif any(item.get("state") not in ("SUCCESS", "SKIPPED", "NEUTRAL") for item in checks):
                status = "pending"
            summary = ", ".join(f"{item.get('name')}: {item.get('state')}" for item in checks[:6])
            return CIResult(status=status, summary=summary, checks=checks)
        except Exception:
            return CIResult()
