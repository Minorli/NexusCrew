"""Tests for substantive reply enforcement."""
import asyncio
from pathlib import Path

from nexuscrew.agents.base import AgentArtifacts, BaseAgent
from nexuscrew.executor.shell import ShellExecutor
from nexuscrew.memory.crew_memory import CrewMemory
from nexuscrew.orchestrator import Orchestrator
from nexuscrew.registry import AgentRegistry
from nexuscrew.router import Router
from nexuscrew.task_state import TaskStatus


class SequencedAgent(BaseAgent):
    def __init__(self, name: str, role: str, replies: list[str]):
        super().__init__(name, role, "test")
        self.replies = replies
        self.seen_messages: list[str] = []

    async def handle(self, message, history, crew_memory):
        self.seen_messages.append(message)
        return self.replies.pop(0), AgentArtifacts()


def test_orchestrator_retries_low_signal_dev_reply(tmp_path: Path, monkeypatch):
    registry = AgentRegistry()
    agent = SequencedAgent(
        "bob",
        "dev",
        [
            "收到，我来处理",
            "@architect Code Review 请求：已完成修复",
        ],
    )
    registry.register(agent)
    executor = ShellExecutor(tmp_path)
    monkeypatch.setattr(executor, "git_current_branch", lambda: asyncio.sleep(0, result="main"))
    monkeypatch.setattr(executor, "git_create_branch", lambda branch_name: asyncio.sleep(0, result="ok"))
    monkeypatch.setattr(executor, "git_changed_files", lambda limit=8: asyncio.sleep(0, result=[]))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        executor,
    )

    sent: list[str] = []

    async def send(text: str, agent_name: str | None = None):
        sent.append(text)

    asyncio.run(orchestrator.run_chain("@bob 修复缓存并跑测试", 1, send))

    assert len(agent.seen_messages) == 2
    assert "不要汇报状态" in agent.seen_messages[1]
    assert any("Code Review 请求" in text for text in sent)


def test_orchestrator_retries_low_signal_architect_reply(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(SequencedAgent(
        "dave",
        "architect",
        [
            "收到，先读代码再给结论。",
            "@nexus-dev-01 打回：并发安全缺失，需补锁。",
        ],
    ))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )

    sent: list[str] = []

    async def send(text: str, agent_name: str | None = None):
        sent.append(text)

    asyncio.run(orchestrator.run_chain("@dave 请评审 orchestrator 模块并给结论", 1, send))

    assert any("打回" in text for text in sent)


def test_orchestrator_escalates_when_architect_stays_low_signal_twice(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(SequencedAgent(
        "alice",
        "pm",
        ["收到，我来调整计划。"],
    ))
    registry.register(SequencedAgent(
        "dave",
        "architect",
        [
            "正在审计，稍后给结论。",
            "读代码中，报告随后。",
        ],
    ))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )

    sent: list[str] = []

    async def send(text: str, agent_name: str | None = None):
        sent.append(text)

    asyncio.run(orchestrator.run_chain("@dave 请评审 orchestrator 模块并给结论", 1, send))

    assert any("未给出有效评审结论" in text for text in sent)
    assert any("@alice" in text for text in sent)


def test_orchestrator_escalates_when_qa_stays_low_signal_twice(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(SequencedAgent(
        "alice",
        "pm",
        ["收到，我来调整计划。"],
    ))
    registry.register(SequencedAgent(
        "qa-1",
        "qa",
        [
            "在测，稍后给结论。",
            "先跑一下，结果随后。",
        ],
    ))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )

    sent: list[str] = []

    async def send(text: str, agent_name: str | None = None):
        sent.append(text)

    asyncio.run(orchestrator.run_chain("@qa-1 请做回归测试并给 Go / No-Go", 1, send))

    assert any("未给出有效测试结论" in text for text in sent)
    assert any("@alice" in text for text in sent)


def test_orchestrator_watchdog_reports_and_times_out(tmp_path: Path):
    class SlowAgent(BaseAgent):
        def __init__(self, name: str, role: str):
            super().__init__(name, role, "test")

        async def handle(self, message, history, crew_memory):
            await asyncio.sleep(0.05)
            return "晚到的回复", AgentArtifacts()

    registry = AgentRegistry()
    registry.register(SlowAgent("alice", "pm"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
        agent_heartbeat_seconds=1,
        agent_max_silence_seconds=2,
    )

    sent: list[str] = []

    async def send(text: str, agent_name: str | None = None):
        sent.append(text)

    original_wait = asyncio.wait

    async def fast_wait(tasks, timeout=None):
        return await original_wait(tasks, timeout=0.01 if timeout else timeout)

    from nexuscrew import orchestrator as orchestrator_module
    orchestrator_module.asyncio.wait = fast_wait
    try:
        asyncio.run(orchestrator.run_chain("@alice 开始任务", 1, send))
    finally:
        orchestrator_module.asyncio.wait = original_wait

    assert any("仍在处理任务" in text for text in sent)
    assert any("编排超时" in text for text in sent)


def test_orchestrator_watchdog_tick_reports_stale_task(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(SequencedAgent("alice", "pm", ["done"]))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
        task_stage_sla_seconds=1,
        task_watchdog_interval_seconds=1,
    )
    task = orchestrator.task_tracker.create(1, "demo")
    task.assigned_to = "alice"
    task.created_at = "2026-03-26T00:00:00"

    sent: list[str] = []

    def send_factory(chat_id: int):
        async def send(text: str, agent_name: str | None = None):
            sent.append(text)
        return send

    alerts = asyncio.run(
        orchestrator.watchdog_tick(
            send_factory,
            active_task_ids={"T-0001"},
            notify_chat=True,
        )
    )

    assert alerts == ["T-0001"]
    assert any("Task Watchdog" in text for text in sent)
    assert any("仍在运行但超时" in text for text in sent)


def test_orchestrator_watchdog_auto_fails_inactive_stale_task(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(SequencedAgent("alice", "pm", ["done"]))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
        task_stage_sla_seconds=1,
        task_watchdog_interval_seconds=1,
    )
    task = orchestrator.task_tracker.create(1, "demo")
    task.assigned_to = "alice"
    task.created_at = "2026-03-26T00:00:00"

    sent: list[str] = []

    def send_factory(chat_id: int):
        async def send(text: str, agent_name: str | None = None):
            sent.append(text)
        return send

    alerts = asyncio.run(orchestrator.watchdog_tick(send_factory, active_task_ids=set()))

    assert alerts == ["T-0001"]
    assert task.status == TaskStatus.FAILED
    assert sent == []


def test_orchestrator_compacts_dev_reply_for_telegram(tmp_path: Path, monkeypatch):
    class DevLikeAgent(BaseAgent):
        def __init__(self):
            super().__init__("bob", "dev", "codex")

        async def handle(self, message, history, crew_memory):
            return (
                "```bash\npytest tests/ -v\n```\n@architect Code Review 请求：已完成修复",
                AgentArtifacts(shell_output="$ pytest tests/ -v\n12 passed"),
            )

    registry = AgentRegistry()
    registry.register(DevLikeAgent())
    executor = ShellExecutor(tmp_path)
    monkeypatch.setattr(executor, "git_current_branch", lambda: asyncio.sleep(0, result="main"))
    monkeypatch.setattr(executor, "git_create_branch", lambda branch_name: asyncio.sleep(0, result="ok"))
    monkeypatch.setattr(executor, "git_changed_files", lambda limit=8: asyncio.sleep(0, result=["app.py", "tests/test_app.py"]))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        executor,
    )

    sent: list[str] = []

    async def send(text: str, agent_name: str | None = None):
        sent.append(text)

    asyncio.run(orchestrator.run_chain("@bob 修复缓存并跑测试", 1, send))

    assert any("Files: app.py, tests/test_app.py" in text for text in sent)
    assert any("Validation: 12 passed" in text for text in sent)
    assert not any("```bash" in text for text in sent)


def test_orchestrator_keeps_status_query_pm_only(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(SequencedAgent("alice", "pm", ["当前状态：dev 空闲，arch 待命。@bob @dave"]))
    registry.register(SequencedAgent("bob", "dev", ["dev reply"]))
    registry.register(SequencedAgent("dave", "architect", ["arch reply"]))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )

    sent: list[tuple[str | None, str]] = []

    async def send(text: str, agent_name: str | None = None):
        sent.append((agent_name, text))

    asyncio.run(orchestrator.run_chain("@alice 看下大家的进度", 1, send))

    assert any(agent_name == "alice" for agent_name, _ in sent)
    assert not any(agent_name == "bob" for agent_name, _ in sent)
    assert not any(agent_name == "dave" for agent_name, _ in sent)
