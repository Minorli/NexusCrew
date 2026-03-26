"""Tests for next-phase workflow helpers, memory, trace, and UX commands."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

from nexuscrew.artifacts import ArtifactRecord, ArtifactStore
from nexuscrew.git.ci import CIResult
from nexuscrew.git.merge_gate import MergeGate
from nexuscrew.git.pr import PRWorkflow
from nexuscrew.git.session import BranchSession, BranchSessionStore
from nexuscrew.hr.analytics import build_trend_report, recommend_staffing
from nexuscrew.memory.crew_memory import CrewMemory
from nexuscrew.memory.retriever import MemoryRetriever
from nexuscrew.memory.store import ScopedMemoryStore
from nexuscrew.runtime.events import RunEvent
from nexuscrew.runtime.stuck import StuckDetector
from nexuscrew.runtime.store import EventStore
from nexuscrew.skills.registry import SkillRegistry
from nexuscrew.telegram.bot import NexusCrewBot
from nexuscrew.metrics import AgentMetrics
from nexuscrew.hr.metrics_store import MetricsStore


def test_branch_session_store_and_pr_draft(tmp_path: Path):
    store = BranchSessionStore(tmp_path / "branch_sessions.jsonl")
    session = BranchSession(chat_id=1, task_id="T-0001", branch_name="feat/t-0001-demo", base_branch="main")
    store.save(session)

    workflow = PRWorkflow()

    class Task:
        id = "T-0001"
        description = "实现缓存"
        github_issue_url = "https://example/issues/1"
        github_pr_number = 0
        github_pr_url = ""

    draft = asyncio.run(workflow.draft_for_task(Task(), session, "完成缓存层", "pytest passed"))

    assert store.get(1, "T-0001").branch_name == "feat/t-0001-demo"
    assert "Summary" in draft.body
    assert draft.head == "feat/t-0001-demo"


def test_merge_gate_and_artifact_store(tmp_path: Path):
    artifacts = ArtifactStore(tmp_path / "artifacts.jsonl")
    artifacts.append(ArtifactRecord(
        task_id="T-0001",
        run_id="run-1",
        type="shell_output",
        source="bob",
        summary="pytest passed",
    ))

    class Task:
        github_pr_number = 1
        status = SimpleNamespace(value="accepted")

    gate = MergeGate().build(Task(), CIResult(status="passed", summary="all green"), [], artifacts.list_for_task("T-0001"))

    assert gate.ready is True
    assert "可合并" in gate.summary


def test_scoped_memory_retriever(tmp_path: Path):
    crew_memory = CrewMemory(tmp_path / "crew_memory.md")
    scoped = ScopedMemoryStore(tmp_path / "scoped_memory.jsonl")
    scoped.append("shared", "human", "共享上下文")
    scoped.append("task:T-0001", "pm", "任务上下文")
    scoped.append("agent:bob", "hr", "对 bob 的提醒")

    retrieved = MemoryRetriever(crew_memory, scoped).retrieve("dev", "bob", "T-0001")

    assert "共享上下文" in retrieved
    assert "任务上下文" in retrieved
    assert "对 bob 的提醒" in retrieved


def test_trace_store_and_stuck_detector(tmp_path: Path):
    store = EventStore(tmp_path / "events.jsonl")
    store.append(RunEvent(run_id="run-1", chat_id=1, task_id="T-0001", type="shell_finished", actor="bob", payload={"output": "failed"}))
    store.append(RunEvent(run_id="run-1", chat_id=1, task_id="T-0001", type="shell_finished", actor="bob", payload={"output": "failed"}))
    history = [
        {"agent": "alice", "content": "A"},
        {"agent": "bob", "content": "B"},
        {"agent": "alice", "content": "A"},
        {"agent": "bob", "content": "B"},
        {"agent": "alice", "content": "A"},
        {"agent": "bob", "content": "B"},
    ]

    report = StuckDetector().analyze("T-0001", history, store.read_all())

    assert report is not None
    assert "ping_pong" in report.labels or "repeat_shell_failure" in report.labels


def test_hr_analytics_reports(tmp_path: Path):
    store = MetricsStore(tmp_path / "metrics_history.jsonl")
    metrics = AgentMetrics()
    metrics.record_task_start()
    metrics.record_task_complete(1000)
    store.append_snapshot(1, "alice", 3.25, metrics)
    store.append_snapshot(2, "alice", 3.25, metrics)
    store.append_snapshot(3, "bob", 3.75, metrics)

    trend = build_trend_report(store, ["alice", "bob"])
    recommendation = recommend_staffing(store, ["alice", "bob"])

    assert "alice" in trend and "bob" in trend
    assert "切换更高规格模型" in recommendation or "更高优先级任务" in recommendation


def test_skill_registry_and_bot_commands():
    bot = NexusCrewBot()
    replies: list[str] = []
    bot._orch = SimpleNamespace(
        task_tracker=SimpleNamespace(
            create=lambda chat_id, text: SimpleNamespace(id="T-0001", description=text),
            get=lambda chat_id, task_id: SimpleNamespace(id=task_id, assigned_to="alice", history=[]),
        ),
        doctor_report=lambda chat_id: "doctor",
        format_task_detail=lambda chat_id, task_id: f"task:{task_id}",
    )
    bot.registry.register(SimpleNamespace(name="alice", role="pm", model_label="gemini"))

    class FakeMessage:
        chat_id = 1

        async def reply_text(self, text: str):
            replies.append(text)

    update = SimpleNamespace(message=FakeMessage())

    asyncio.run(bot.cmd_skills(update, SimpleNamespace(args=[])))
    asyncio.run(bot.cmd_new(update, SimpleNamespace(args=["fix", "ci"])))
    asyncio.run(bot.cmd_doctor(update, SimpleNamespace(args=[])))

    assert "Skills" in replies[0]
    assert "已创建任务" in replies[1]
    assert replies[2] == "doctor"


def test_trace_artifacts_pr_ci_and_board_commands():
    bot = NexusCrewBot()
    replies: list[str] = []
    bot._orch = SimpleNamespace(
        trace_summary=lambda task_id: f"trace:{task_id}",
        artifacts_summary=lambda task_id: f"artifacts:{task_id}",
        pr_summary=lambda chat_id, task_id: f"pr:{task_id}",
        ci_summary=lambda chat_id, task_id: f"ci:{task_id}",
        format_status=lambda chat_id: "status",
    )
    bot.registry.register(SimpleNamespace(name="alice", role="pm", model_label="gemini"))

    class FakeMessage:
        chat_id = 1

        async def reply_text(self, text: str):
            replies.append(text)

    update = SimpleNamespace(message=FakeMessage())

    asyncio.run(bot.cmd_trace(update, SimpleNamespace(args=["T-0001"])))
    asyncio.run(bot.cmd_artifacts(update, SimpleNamespace(args=["T-0001"])))
    asyncio.run(bot.cmd_pr(update, SimpleNamespace(args=["T-0001"])))
    asyncio.run(bot.cmd_ci(update, SimpleNamespace(args=["T-0001"])))
    asyncio.run(bot.cmd_board(update, SimpleNamespace(args=[])))

    assert replies[0] == "trace:T-0001"
    assert replies[1] == "artifacts:T-0001"
    assert replies[2] == "pr:T-0001"
    assert replies[3] == "ci:T-0001"
    assert "Status Board" in replies[4]
