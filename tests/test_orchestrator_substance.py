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


def test_orchestrator_shell_summary_prefers_real_error_over_code_line(tmp_path: Path):
    orchestrator = Orchestrator(
        AgentRegistry(),
        Router(AgentRegistry()),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )

    shell_output = (
        "$ sed -n '1,240p' nexuscrew/config.py\n"
        "    except (TypeError, ValueError):\n"
        "[stderr]\n"
        "ERROR collecting tests/test_config_validation.py\n"
    )

    assert orchestrator._build_shell_summary(None, shell_output) == "ERROR collecting tests/test_config_validation.py"


def test_orchestrator_allows_pm_revisit_after_architect_noncompliance(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(SequencedAgent("alice", "pm", ["@dave 请评审 config 修复", "收到，我来调整计划。"]))
    registry.register(SequencedAgent("dave", "architect", ["收到，先看代码再给结论。", "稍后给结论。"]))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )

    sent: list[str] = []

    async def send(text: str, agent_name: str | None = None):
        sent.append(text)

    asyncio.run(orchestrator.run_chain("@alice 开始推进", 1, send))

    assert any("收到，我来调整计划" in text for text in sent)
    assert not any("循环路由" in text for text in sent)


def test_architect_noncompliant_reply_moves_task_back_to_in_progress(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(SequencedAgent("alice", "pm", ["收到，我来调整计划。"]))
    arch = SequencedAgent("dave", "architect", ["收到，先看代码再给结论。", "稍后给结论。"])
    registry.register(arch)
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )
    task = orchestrator.task_tracker.create(1, "demo")
    task.transition(TaskStatus.IN_PROGRESS)
    task.transition(TaskStatus.REVIEW_REQ)
    task.transition(TaskStatus.REVIEWING)

    sent: list[str] = []

    async def send(text: str, agent_name: str | None = None):
        sent.append(text)

    asyncio.run(
        orchestrator.run_chain(
            "@dave 请评审",
            1,
            send,
            initial_agent=registry.get_by_name("dave"),
            task=task,
        )
    )

    assert task.status == TaskStatus.IN_PROGRESS
    assert task.assigned_to == "alice"


def test_orchestrator_dev_review_request_without_task_changes_stays_in_implementation(tmp_path: Path, monkeypatch):
    from nexuscrew.git.session import BranchSession

    class DevLikeAgent(BaseAgent):
        def __init__(self):
            super().__init__("bob", "dev", "codex")

        async def handle(self, message, history, crew_memory):
            return (
                "@architect Code Review 请求：已完成修复",
                AgentArtifacts(shell_output="$ git status --short\nM nexuscrew/agents/dev.py"),
            )

    registry = AgentRegistry()
    registry.register(DevLikeAgent())
    executor = ShellExecutor(tmp_path)
    monkeypatch.setattr(executor, "git_current_branch", lambda: asyncio.sleep(0, result="feat/t-0001-bob"))
    monkeypatch.setattr(executor, "git_create_branch", lambda branch_name: asyncio.sleep(0, result="ok"))
    monkeypatch.setattr(executor, "git_changed_files", lambda limit=64: asyncio.sleep(0, result=["nexuscrew/agents/dev.py"]))
    monkeypatch.setattr(executor, "file_hashes", lambda paths: asyncio.sleep(0, result={"nexuscrew/agents/dev.py": "baseline-dev"}))
    monkeypatch.setattr(executor, "git_diff_summary_for_files", lambda files, limit=6: asyncio.sleep(0, result=""))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        executor,
    )
    task = orchestrator.task_tracker.create(1, "demo")
    task.branch_name = "feat/t-0001-bob"
    orchestrator.branch_sessions.save(
        BranchSession(
            chat_id=1,
            task_id="T-0001",
            branch_name="feat/t-0001-bob",
            base_branch="main",
            baseline_dirty_files={"nexuscrew/agents/dev.py": "baseline-dev"},
        )
    )

    public_reply = asyncio.run(
        orchestrator._build_public_reply(
            1,
            task,
            registry.get_by_name("bob"),
            "@architect Code Review 请求：已完成修复",
            AgentArtifacts(shell_output="$ git status --short\nM nexuscrew/agents/dev.py"),
        )
    )

    assert "Files: (no task-scoped changes yet)" in public_reply
    assert "Review ask: @architect review" not in public_reply
    assert "Next: continue implementation" in public_reply


def test_orchestrator_does_not_route_architect_when_dev_has_no_task_scoped_changes(tmp_path: Path, monkeypatch):
    from nexuscrew.git.session import BranchSession

    class DevLikeAgent(BaseAgent):
        def __init__(self):
            super().__init__("bob", "dev", "codex")

        async def handle(self, message, history, crew_memory):
            return (
                "@architect Code Review 请求：已完成修复",
                AgentArtifacts(shell_output="$ git status --short\nM nexuscrew/agents/dev.py"),
            )

    class ArchitectLikeAgent(BaseAgent):
        def __init__(self):
            super().__init__("dave", "architect", "claude")
            self.seen = 0

        async def handle(self, message, history, crew_memory):
            self.seen += 1
            return "LGTM", AgentArtifacts()

    registry = AgentRegistry()
    dev = DevLikeAgent()
    arch = ArchitectLikeAgent()
    registry.register(dev)
    registry.register(arch)
    executor = ShellExecutor(tmp_path)
    monkeypatch.setattr(executor, "git_current_branch", lambda: asyncio.sleep(0, result="feat/t-0001-bob"))
    monkeypatch.setattr(executor, "git_create_branch", lambda branch_name: asyncio.sleep(0, result="ok"))
    monkeypatch.setattr(executor, "git_changed_files", lambda limit=64: asyncio.sleep(0, result=["nexuscrew/agents/dev.py"]))
    monkeypatch.setattr(executor, "file_hashes", lambda paths: asyncio.sleep(0, result={"nexuscrew/agents/dev.py": "baseline-dev"}))
    monkeypatch.setattr(executor, "git_diff_summary_for_files", lambda files, limit=6: asyncio.sleep(0, result=""))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        executor,
    )
    task = orchestrator.task_tracker.create(1, "demo")
    task.branch_name = "feat/t-0001-bob"
    orchestrator.branch_sessions.save(
        BranchSession(
            chat_id=1,
            task_id="T-0001",
            branch_name="feat/t-0001-bob",
            base_branch="main",
            baseline_dirty_files={"nexuscrew/agents/dev.py": "baseline-dev"},
        )
    )

    sent: list[tuple[str | None, str]] = []

    async def send(text: str, agent_name: str | None = None):
        sent.append((agent_name, text))

    asyncio.run(
        orchestrator.run_chain(
            "@bob 修复 demo",
            1,
            send,
            initial_agent=registry.get_by_name("bob"),
            task=task,
        )
    )

    assert arch.seen == 0
    assert task.status in (TaskStatus.PLANNING, TaskStatus.IN_PROGRESS)
    assert any("Next: continue implementation" in text for _, text in sent)


def test_orchestrator_dev_does_not_request_review_when_validation_failed(tmp_path: Path, monkeypatch):
    from nexuscrew.git.session import BranchSession

    class DevLikeAgent(BaseAgent):
        def __init__(self):
            super().__init__("bob", "dev", "codex")

        async def handle(self, message, history, crew_memory):
            return (
                "@architect Code Review 请求：已完成修复",
                AgentArtifacts(
                    shell_output="$ pytest tests/test_config_validation.py -q\n"
                    "==================================== ERRORS ====================================\n"
                    "ERROR collecting tests/test_config_validation.py\n"
                ),
            )

    registry = AgentRegistry()
    registry.register(DevLikeAgent())
    executor = ShellExecutor(tmp_path)
    monkeypatch.setattr(executor, "git_current_branch", lambda: asyncio.sleep(0, result="feat/t-0001-bob"))
    monkeypatch.setattr(executor, "git_create_branch", lambda branch_name: asyncio.sleep(0, result="ok"))
    monkeypatch.setattr(executor, "git_changed_files", lambda limit=64: asyncio.sleep(0, result=["nexuscrew/config.py", "tests/test_config_validation.py"]))
    monkeypatch.setattr(executor, "file_hashes", lambda paths: asyncio.sleep(0, result={"nexuscrew/config.py": "new1", "tests/test_config_validation.py": "new2"}))
    monkeypatch.setattr(executor, "git_diff_summary_for_files", lambda files, limit=6: asyncio.sleep(0, result="M nexuscrew/config.py; A tests/test_config_validation.py"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        executor,
    )
    task = orchestrator.task_tracker.create(1, "demo")
    task.branch_name = "feat/t-0001-bob"
    orchestrator.branch_sessions.save(
        BranchSession(chat_id=1, task_id="T-0001", branch_name="feat/t-0001-bob", base_branch="main")
    )

    public_reply = asyncio.run(
        orchestrator._build_public_reply(
            1,
            task,
            registry.get_by_name("bob"),
            "@architect Code Review 请求：已完成修复",
            AgentArtifacts(
                shell_output="$ pytest tests/test_config_validation.py -q\n"
                "==================================== ERRORS ====================================\n"
                "ERROR collecting tests/test_config_validation.py\n"
            ),
        )
    )

    assert "Next: @architect review" not in public_reply
    assert "Next: fix failing validation" in public_reply
    assert "Validation: ERROR collecting tests/test_config_validation.py" in public_reply
