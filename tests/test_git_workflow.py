"""Tests for git workflow helpers and orchestration branch creation."""
import asyncio
from pathlib import Path

from nexuscrew.agents.base import AgentArtifacts, BaseAgent
from nexuscrew.executor import shell as shell_module
from nexuscrew.executor.shell import ShellExecutor
from nexuscrew.git.session import BranchSession
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


async def _direct_to_thread(func, *args, **kwargs):
    return func(*args, **kwargs)


def test_shell_executor_git_current_branch_returns_unknown(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(shell_module.asyncio, "to_thread", _direct_to_thread)
    executor = ShellExecutor(tmp_path)

    assert asyncio.run(executor.git_current_branch()) == "unknown"


def test_orchestrator_creates_feature_branch_for_dev_task(tmp_path: Path, monkeypatch):
    registry = AgentRegistry()
    registry.register(FakeAgent("bob", "dev", "done"))
    executor = ShellExecutor(tmp_path)
    created: list[str] = []

    async def fake_git_create_branch(branch_name: str):
        created.append(branch_name)
        return "ok"

    monkeypatch.setattr(executor, "git_create_branch", fake_git_create_branch)
    monkeypatch.setattr(executor, "git_current_branch", lambda: asyncio.sleep(0, result="main"))
    monkeypatch.setattr(executor, "git_changed_files", lambda limit=8: asyncio.sleep(0, result=[]))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        executor,
    )

    async def send(text: str, agent_name: str | None = None):
        return None

    asyncio.run(orchestrator.run_chain("@bob 修复缓存逻辑", 1, send))

    assert created == ["feat/t-0001-bob"]


def test_orchestrator_scopes_dev_review_packet_to_task_changes(tmp_path: Path, monkeypatch):
    registry = AgentRegistry()
    registry.register(FakeAgent("bob", "dev", "@architect Code Review 请求：已完成 config 校验修复"))
    executor = ShellExecutor(tmp_path)
    monkeypatch.setattr(executor, "git_current_branch", lambda: asyncio.sleep(0, result="feat/t-0001-bob"))
    monkeypatch.setattr(executor, "git_create_branch", lambda branch_name: asyncio.sleep(0, result="ok"))
    monkeypatch.setattr(
        executor,
        "git_changed_files",
        lambda limit=64: asyncio.sleep(
            0,
            result=[
                "nexuscrew/agents/dev.py",
                "nexuscrew/agents/pm.py",
                "nexuscrew/config.py",
                "tests/test_config_validation.py",
            ],
        ),
    )
    monkeypatch.setattr(
        executor,
        "file_hashes",
        lambda paths: asyncio.sleep(
            0,
            result={
                "nexuscrew/agents/dev.py": "baseline-dev",
                "nexuscrew/agents/pm.py": "baseline-pm",
                "nexuscrew/config.py": "task-config",
                "tests/test_config_validation.py": "task-test",
            },
        ),
    )
    monkeypatch.setattr(
        executor,
        "git_diff_summary_for_files",
        lambda files, limit=6: asyncio.sleep(0, result="M nexuscrew/config.py; A tests/test_config_validation.py"),
    )
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        executor,
    )
    task = orchestrator.task_tracker.create(1, "修复 config 校验")
    task.branch_name = "feat/t-0001-bob"
    orchestrator.branch_sessions.save(
        BranchSession(
            chat_id=1,
            task_id="T-0001",
            branch_name="feat/t-0001-bob",
            base_branch="main",
            baseline_dirty_files={
                "nexuscrew/agents/dev.py": "baseline-dev",
                "nexuscrew/agents/pm.py": "baseline-pm",
            },
        )
    )

    public_reply = asyncio.run(
        orchestrator._build_public_reply(
            1,
            task,
            registry.get_by_name("bob"),
            "@architect Code Review 请求：已完成 config 校验修复",
            AgentArtifacts(shell_output="3 passed"),
        )
    )

    assert "Files: nexuscrew/config.py, tests/test_config_validation.py" in public_reply
    assert "agents/dev.py" not in public_reply
    assert "Diff: M nexuscrew/config.py; A tests/test_config_validation.py" in public_reply


def test_shell_executor_run_one_tolerates_non_utf8_output(tmp_path: Path, monkeypatch):
    executor = ShellExecutor(tmp_path)

    class FakeCompleted:
        stdout = b"ok:\xf3\x28\n"
        stderr = b"warn:\xff\n"

    monkeypatch.setattr(shell_module.subprocess, "run", lambda *args, **kwargs: FakeCompleted())

    result = executor._run_one("printf 'demo'")

    assert "$ printf 'demo'" in result
    assert "ok:�(" in result
    assert "[stderr]" in result
    assert "warn:�" in result
