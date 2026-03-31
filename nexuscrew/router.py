"""Router — parses @mentions and resolves them via AgentRegistry."""
import re
from .registry import AgentRegistry
from .agents.base import BaseAgent
from .local_secrets import load_local_secrets

# Role aliases: these map common @mentions to canonical role names
ROLE_ALIASES: dict[str, str] = {
    "pm": "pm",
    "dev": "dev",
    "dev_1": "dev",
    "dev_2": "dev",
    "architect": "architect",
    "arch": "architect",
    "hr": "hr",  # Task 1.2 完成: 支持 HR 角色别名路由。
    "qa": "qa",
    "test": "qa",
    "tester": "qa",
}

_MENTION_RE = re.compile(r"@(\w+)")
cfg = load_local_secrets()


class Router:
    def __init__(self, registry: AgentRegistry):
        self.registry = registry

    def detect_first_routed(self, text: str) -> dict | None:
        """Return the first valid routed mention with metadata."""
        for token in self._iter_tokens(text):
            routed = self._resolve_routed(token)
            if routed:
                return routed
        return None

    def detect_first(self, text: str) -> BaseAgent | None:
        """Return the first valid agent mentioned in text."""
        routed = self.detect_first_routed(text)
        return routed["agent"] if routed else None

    def detect_all(self, text: str) -> list[BaseAgent]:
        """Return all unique agents mentioned, in order of appearance."""
        seen: set[str] = set()
        agents: list[BaseAgent] = []
        for routed in self.detect_all_routed(text):
            agent = routed["agent"]
            if agent and agent.name not in seen:
                seen.add(agent.name)
                agents.append(agent)
        return agents

    def detect_all_routed(self, text: str) -> list[dict]:
        """Return all unique routed mentions with metadata."""
        seen: set[str] = set()
        routed_mentions: list[dict] = []
        for token in self._iter_tokens(text):
            routed = self._resolve_routed(token)
            if not routed:
                continue
            agent = routed["agent"]
            if agent.name in seen:
                continue
            seen.add(agent.name)
            routed_mentions.append(routed)
        return routed_mentions

    def default_agent(self) -> BaseAgent | None:
        """Fall back to any PM when no @mention found."""
        return self.registry.get_by_role("pm")

    def _resolve(self, token: str) -> BaseAgent | None:
        routed = self._resolve_routed(token)
        return routed["agent"] if routed else None

    def _resolve_routed(self, token: str) -> dict | None:
        tl = token.lower()
        # 1. Exact name match
        agent = self.registry.get_by_name(tl)
        if agent:
            return {"agent": agent, "kind": "exact_name", "token": tl, "role": agent.role}
        # 1.5. Dedicated bot username match
        mapped_name = getattr(cfg, "BOT_USERNAME_MAP", {}).get(tl)
        if mapped_name:
            agent = self.registry.get_by_name(mapped_name.lower())
            if agent:
                return {"agent": agent, "kind": "bot_username", "token": tl, "role": agent.role}
        # 2. Role alias
        role = ROLE_ALIASES.get(tl)
        if role:
            agent = self.registry.get_by_role(role)
            if agent:
                return {"agent": agent, "kind": "role_alias", "token": tl, "role": role}
        return None

    def _iter_tokens(self, text: str):
        r"""Preserve the core @(\w+) contract, then extend through hyphens."""
        # Task 1.2 完成: 支持 @nexus-hr-01 这类带连字符的精确名字路由。
        for match in _MENTION_RE.finditer(text):
            token = match.group(1)
            end = match.end(1)
            while end < len(text) and (text[end].isalnum() or text[end] in "_-"):
                token += text[end]
                end += 1
            yield token
