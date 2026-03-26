"""Tests for laziness detection heuristics and integration."""
import asyncio
from pathlib import Path

from nexuscrew.agents.base import AgentArtifacts, BaseAgent
from nexuscrew.executor.shell import ShellExecutor
from nexuscrew.hr.laziness import (
    detect_all,
    detect_buck_passing,
    detect_execution_avoidance,
    detect_shallow_response,
    detect_stale_retry,
)
from nexuscrew.memory.crew_memory import CrewMemory
from nexuscrew.orchestrator import Orchestrator
from nexuscrew.registry import AgentRegistry
from nexuscrew.router import Router


class FakeAgent(BaseAgent):
    def __init__(self, name: str, role: str, reply: str):
        super().__init__(name, role, "test")
        self.reply = reply

    async def handle(self, message, history, crew_memory):
        return self.reply, AgentArtifacts()


def test_laziness_heuristics_examples():
    assert detect_shallow_response("好的收到明白") is True
    assert detect_execution_avoidance("建议使用 Redis 做缓存", "dev") is True
    assert detect_stale_retry("foo bar baz", "foo bar baz") is True
    assert detect_buck_passing("@pm @architect @dev 看下") is True
    normal_reply = (
        "我已完成缓存层实现，新增 Redis 客户端封装、配置读取和两组单元测试，"
        "并验证了命中与回源路径，补充了异常处理和默认超时配置，"
        "同时执行了 pytest 与手工 smoke 检查确认没有回归，"
        "接下来会整理变更摘要、测试结果和关键设计取舍后再提交给 Architect Review。"
    )
    assert detect_all(normal_reply, "dev") == []


def test_orchestrator_records_laziness_signals(tmp_path: Path, monkeypatch):
    registry = AgentRegistry()
    registry.register(FakeAgent("bob", "dev", "建议使用 Redis 做缓存"))
    executor = ShellExecutor(tmp_path)

    async def fake_git_create_branch(branch_name: str):
        return "ok"

    monkeypatch.setattr(executor, "git_create_branch", fake_git_create_branch)
    monkeypatch.setattr(executor, "git_current_branch", lambda: asyncio.sleep(0, result="main"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        executor,
    )

    async def send(text: str, agent_name: str | None = None):
        return None

    asyncio.run(orchestrator.run_chain("@bob 做缓存", 1, send))

    metrics = orchestrator.metrics.get("bob")
    assert any("execution_avoidance" in signal for signal in metrics.laziness_signals)
