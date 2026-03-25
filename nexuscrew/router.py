"""Router — parses @mentions and resolves them via AgentRegistry."""
import re
from .registry import AgentRegistry
from .agents.base import BaseAgent

# Role aliases: these map common @mentions to canonical role names
ROLE_ALIASES: dict[str, str] = {
    "pm": "pm",
    "dev": "dev",
    "dev_1": "dev",
    "dev_2": "dev",
    "architect": "architect",
    "arch": "architect",
}

_MENTION_RE = re.compile(r"@(\w+)")


class Router:
    def __init__(self, registry: AgentRegistry):
        self.registry = registry

    def detect_first(self, text: str) -> BaseAgent | None:
        """Return the first valid agent mentioned in text."""
        for token in _MENTION_RE.findall(text):
            agent = self._resolve(token)
            if agent:
                return agent
        return None

    def detect_all(self, text: str) -> list[BaseAgent]:
        """Return all unique agents mentioned, in order of appearance."""
        seen: set[str] = set()
        agents: list[BaseAgent] = []
        for token in _MENTION_RE.findall(text):
            agent = self._resolve(token)
            if agent and agent.name not in seen:
                seen.add(agent.name)
                agents.append(agent)
        return agents

    def default_agent(self) -> BaseAgent | None:
        """Fall back to any PM when no @mention found."""
        return self.registry.get_by_role("pm")

    def _resolve(self, token: str) -> BaseAgent | None:
        tl = token.lower()
        # 1. Exact name match
        agent = self.registry.get_by_name(tl)
        if agent:
            return agent
        # 2. Role alias
        role = ROLE_ALIASES.get(tl)
        if role:
            return self.registry.get_by_role(role)
        return None
