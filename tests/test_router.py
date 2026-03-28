"""Tests for Router — @mention parsing, role aliases, default fallback."""
from nexuscrew import router as router_module
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

    def test_role_alias_hr(self):
        router = _setup(_agent("nexus-hr-01", "hr"))
        assert router.detect_first("@hr 出绩效报告").name == "nexus-hr-01"

    def test_role_alias_qa(self):
        router = _setup(_agent("nexus-qa-01", "qa"))
        assert router.detect_first("@qa 做回归测试").name == "nexus-qa-01"

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

    def test_exact_hr_name_match(self):
        router = _setup(_agent("nexus-hr-01", "hr"), _agent("alice", "pm"))
        assert router.detect_first("@nexus-hr-01 评估 dev").name == "nexus-hr-01"

    def test_bot_username_maps_to_agent_name(self, monkeypatch):
        monkeypatch.setattr(
            router_module.cfg,
            "BOT_USERNAME_MAP",
            {"nexus_dev_02_bot": "nexus-dev-02"},
            raising=False,
        )
        router = _setup(_agent("nexus-dev-02", "dev"), _agent("alice", "pm"))
        assert router.detect_first("@nexus_dev_02_bot 你呢").name == "nexus-dev-02"

    def test_qa_bot_username_maps_to_agent_name(self, monkeypatch):
        monkeypatch.setattr(
            router_module.cfg,
            "BOT_USERNAME_MAP",
            {"nexus_qa_01_bot": "nexus-qa-01"},
            raising=False,
        )
        router = _setup(_agent("nexus-qa-01", "qa"), _agent("alice", "pm"))
        assert router.detect_first("@nexus_qa_01_bot 做一轮验收").name == "nexus-qa-01"


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
