"""Tests for AgentRegistry — registration, lookup, round-robin."""
from nexuscrew.agents.base import BaseAgent, AgentArtifacts
from nexuscrew.registry import AgentRegistry


class FakeAgent(BaseAgent):
    """Minimal concrete agent for testing."""

    async def handle(self, message, history, crew_memory):
        return "ok", AgentArtifacts()


def _agent(name: str, role: str) -> FakeAgent:
    return FakeAgent(name=name, role=role, model_label="test")


class TestRegistry:
    def test_register_and_lookup_by_name(self):
        reg = AgentRegistry()
        a = _agent("alice", "pm")
        reg.register(a)
        assert reg.get_by_name("alice") is a
        assert reg.get_by_name("Alice") is a  # case-insensitive

    def test_lookup_missing_returns_none(self):
        reg = AgentRegistry()
        assert reg.get_by_name("ghost") is None
        assert reg.get_by_role("pm") is None

    def test_get_by_role_round_robin(self):
        reg = AgentRegistry()
        b = _agent("bob", "dev")
        c = _agent("charlie", "dev")
        reg.register(b)
        reg.register(c)
        first = reg.get_by_role("dev")
        second = reg.get_by_role("dev")
        third = reg.get_by_role("dev")
        assert first is b
        assert second is c
        assert third is b  # wraps around

    def test_list_all(self):
        reg = AgentRegistry()
        reg.register(_agent("alice", "pm"))
        reg.register(_agent("bob", "dev"))
        items = reg.list_all()
        assert len(items) == 2
        names = {i["name"] for i in items}
        assert names == {"alice", "bob"}

    def test_list_by_role(self):
        reg = AgentRegistry()
        reg.register(_agent("alice", "pm"))
        reg.register(_agent("bob", "dev"))
        reg.register(_agent("charlie", "dev"))

        items = reg.list_by_role("dev")

        assert [agent.name for agent in items] == ["bob", "charlie"]

    def test_unregister(self):
        reg = AgentRegistry()
        a = _agent("alice", "pm")
        reg.register(a)
        reg.unregister("alice")
        assert reg.get_by_name("alice") is None
        assert reg.get_by_role("pm") is None

    def test_clear(self):
        reg = AgentRegistry()
        reg.register(_agent("alice", "pm"))
        reg.register(_agent("bob", "dev"))
        reg.clear()
        assert len(reg) == 0
