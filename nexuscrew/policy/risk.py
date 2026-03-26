"""Risk classification for executable actions."""
from enum import IntEnum


class RiskLevel(IntEnum):
    LOW = 1
    MEDIUM = 2
    HIGH = 3


HIGH_PATTERNS = (
    "rm -rf",
    "git push",
    "git pull",
    "git merge",
    "git rebase",
    "sudo ",
    "curl ",
    "wget ",
    "pip install",
    "npm install",
    "apt-get",
    "ssh ",
    "scp ",
    "docker run",
    "docker compose up",
)

MEDIUM_PATTERNS = (
    "git checkout",
    "git commit",
    "pytest",
    "python -m pytest",
    "mv ",
    "cp ",
    "mkdir ",
    "touch ",
)


def classify_command(command: str) -> RiskLevel:
    text = command.strip().lower()
    if not text:
        return RiskLevel.LOW
    if any(pattern in text for pattern in HIGH_PATTERNS):
        return RiskLevel.HIGH
    if any(pattern in text for pattern in MEDIUM_PATTERNS):
        return RiskLevel.MEDIUM
    return RiskLevel.LOW


def classify_script(script: str) -> RiskLevel:
    # Task B1 完成: 风险分级策略用于 shell/git/network 动作。
    levels = [classify_command(line) for line in script.splitlines() if line.strip()]
    return max(levels, default=RiskLevel.LOW)
