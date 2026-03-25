"""Tests for Router — @mention parsing, role aliases, default fallback."""
from nexuscrew.agents.base import BaseAgent, AgentArtifacts
from nexuscrew.registry import AgentRegistry
from nexuscrew.router import Router


class FakeAgent(BaseAgent):
    async def handle(self, message, history, crew_memory):
        return "ok", AgentArtifacts()


def _agent(name: str, role: str) -> FakeAgent:
    return FakeAgent(name=name, role=role, model_label="test")


def _setup(*agents):
    reg = AgentRegistry()
    for a in agents:
        reg.register(a)
    return Router(reg)


class TestDetectFirst:
    def test_exact_name_match(self):
        router = _setup(_agent("alice", "pm"), _agent("bob", "dev"))
        assert router.detect_first("@bob 帮我修 bug").name == "bob"

    def test_role_alias_pm(self):
        router = _setup(_agent("alice", "pm"))
        assert router.detect_first("@pm 帮我拆任务").name == "alice"

    def test_role_alias_architect(self):
        router = _setup(_agent("dave", "architect"))
        assert router.detect_first("@arch Code Review").name == "dave"

    def test_no_mention_returns_none(self):
        router = _setup(_agent("alice", "pm"))
        assert router.detect_first("没有 mention 的消息") is None

    def test_unknown_mention_returns_none(self):
        router = _setup(_agent("alice", "pm"))
        assert router.detect_first("@ghost 你好") is None

    def test_name_takes_priority_over_role(self):
        """If an agent is literally named 'dev', exact match wins."""
        router = _setup(_agent("dev", "pm"))  # name='dev' but role='pm'
        result = router.detect_first("@dev 来")
        assert result.name == "dev"
        assert result.role == "pm"


class TestDetectAll:
    def test_multiple_mentions(self):
        router = _setup(
            _agent("bob", "dev"),
            _agent("charlie", "dev"),
            _agent("dave", "architect"),
        )
        agents = router.detect_all("@bob 做步骤1，@charlie 做步骤2，@dave Review")
        names = [a.name for a in agents]
        assert names == ["bob", "charlie", "dave"]

    def test_dedup_same_agent(self):
        router = _setup(_agent("bob", "dev"))
        agents = router.detect_all("@bob 先做A，@bob 再做B")
        assert len(agents) == 1


class TestDefaultAgent:
    def test_default_is_pm(self):
        router = _setup(_agent("alice", "pm"), _agent("bob", "dev"))
        assert router.default_agent().role == "pm"

    def test_default_none_when_no_pm(self):
        router = _setup(_agent("bob", "dev"))
        assert router.default_agent() is None
