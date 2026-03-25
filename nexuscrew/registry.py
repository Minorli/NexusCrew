"""Agent Registry — dynamic name/role lookup with round-robin for roles."""
from collections import defaultdict
from .agents.base import BaseAgent


class AgentRegistry:
    def __init__(self):
        self._by_name: dict[str, BaseAgent] = {}
        self._by_role: dict[str, list[BaseAgent]] = defaultdict(list)
        self._rr: dict[str, int] = defaultdict(int)  # round-robin index

    def register(self, agent: BaseAgent) -> None:
        self._by_name[agent.name.lower()] = agent
        self._by_role[agent.role].append(agent)

    def unregister(self, name: str) -> None:
        name = name.lower()
        agent = self._by_name.pop(name, None)
        if agent:
            lst = self._by_role.get(agent.role, [])
            if agent in lst:
                lst.remove(agent)

    def get_by_name(self, name: str) -> BaseAgent | None:
        return self._by_name.get(name.lower())

    def get_by_role(self, role: str) -> BaseAgent | None:
        """Round-robin across all agents of a given role."""
        lst = self._by_role.get(role, [])
        if not lst:
            return None
        idx = self._rr[role] % len(lst)
        self._rr[role] += 1
        return lst[idx]

    def list_all(self) -> list[dict]:
        return [
            {"name": a.name, "role": a.role, "model": a.model_label}
            for a in self._by_name.values()
        ]

    def clear(self) -> None:
        self._by_name.clear()
        self._by_role.clear()
        self._rr.clear()

    def __len__(self) -> int:
        return len(self._by_name)
