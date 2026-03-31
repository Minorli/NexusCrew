"""Tests for next-phase workflow helpers, memory, trace, and UX commands."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import urllib.error

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
from nexuscrew.policy.access import AccessController
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


def test_pr_workflow_returns_draft_on_network_error():
    workflow = PRWorkflow(repo="owner/repo", token="token")

    class Task:
        id = "T-0001"
        description = "实现缓存"
        github_issue_url = ""
        github_pr_number = 0
        github_pr_url = ""

    session = BranchSession(chat_id=1, task_id="T-0001", branch_name="feat/t-0001-demo", base_branch="main")

    def fail_create(draft):
        raise urllib.error.URLError("handshake timeout")

    workflow._create_pull_request = fail_create

    draft = asyncio.run(workflow.ensure_pr(Task(), session, "完成缓存层", "pytest passed"))

    assert draft.number == 0
    assert draft.url == ""


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
        blocked_reason = ""
        status = SimpleNamespace(value="accepted")

    gate = MergeGate().build(Task(), CIResult(status="passed", summary="all green"), [], artifacts.list_for_task("T-0001"))

    assert gate.ready is True
    assert "可合并" in gate.summary
    assert gate.reasons == []


def test_merge_gate_reports_structured_blockers(tmp_path: Path):
    artifacts = ArtifactStore(tmp_path / "artifacts.jsonl")

    class Task:
        github_pr_number = 0
        blocked_reason = "approval_required"
        status = SimpleNamespace(value="in_progress")

    approvals = [SimpleNamespace(id="APR-0001", status="pending", risk_level="high", action_type="shell")]

    gate = MergeGate().build(Task(), CIResult(status="pending", summary="checks pending"), approvals, artifacts.list_for_task("T-0001"))

    assert gate.ready is False
    assert "待审批 APR-0001:high/shell" in gate.summary
    assert "尚未创建 PR" in gate.summary
    assert "CI 进行中: checks pending" in gate.summary
    assert "任务阻塞: approval_required" in gate.summary
    assert "任务状态未就绪: in_progress" in gate.summary
    assert "缺少 artifact 摘要" in gate.summary


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


def test_scoped_memory_store_imports_legacy_jsonl(tmp_path: Path):
    legacy = tmp_path / "scoped_memory.jsonl"
    legacy.write_text(
        '{"scope":"shared","actor":"human","content":"旧上下文","importance":2,"ts":"2026-03-28T10:00:00"}\n',
        encoding="utf-8",
    )

    store = ScopedMemoryStore(legacy)

    entries = store.read("shared", last_n=5)

    assert [item.content for item in entries] == ["旧上下文"]
    assert legacy.with_suffix(".db").exists()


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
    bot._allowed = set()
    bot._access = AccessController()
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
    bot._allowed = set()
    bot._access = AccessController()
    replies: list[str] = []
    bot._orch = SimpleNamespace(
        trace_summary=lambda task_id: f"trace:{task_id}",
        artifacts_summary=lambda task_id: f"artifacts:{task_id}",
        gate_summary=lambda task_id: f"gates:{task_id}",
        continuation_summary=lambda task_id: f"continuation:{task_id}",
        family_summary=lambda chat_id, family_id: f"family:{family_id}",
        session_summary=lambda chat_id, session_key: f"session:{session_key}",
        agent_presence=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [{"name": "alice", "role": "pm", "model": "gemini", "presence": "busy", "load": "active", "queue_size": 1, "blocked_count": 0, "inflight_count": 1, "waiting_count": 0, "current_task_id": "T-0001"}],
        agent_queue_summaries=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [{"agent": "alice", "queue": [{"task_id": "T-0001", "runtime_state": "inflight", "next_action": "architect review"}]}],
        proactive_recommendations=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [{"type": "continuation_next_action", "family_id": "T-0001", "actions": ["architect review"]}],
        control_plane_summary=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: {"tasks_total": 1},
        pr_summary=lambda chat_id, task_id: f"pr:{task_id}",
        ci_summary=lambda chat_id, task_id: f"ci:{task_id}",
        format_status=lambda chat_id: "status",
    )
    bot._runner.active_task_ids = lambda: {"T-0001"}
    bot._runner.waiting_task_ids = lambda: set()
    bot.registry.register(SimpleNamespace(name="alice", role="pm", model_label="gemini"))

    class FakeMessage:
        chat_id = 1

        async def reply_text(self, text: str):
            replies.append(text)

    update = SimpleNamespace(message=FakeMessage())

    asyncio.run(bot.cmd_trace(update, SimpleNamespace(args=["T-0001"])))
    asyncio.run(bot.cmd_presence(update, SimpleNamespace(args=[])))
    asyncio.run(bot.cmd_queues(update, SimpleNamespace(args=[])))
    asyncio.run(bot.cmd_proactive(update, SimpleNamespace(args=[])))
    asyncio.run(bot.cmd_control(update, SimpleNamespace(args=[])))
    asyncio.run(bot.cmd_artifacts(update, SimpleNamespace(args=["T-0001"])))
    asyncio.run(bot.cmd_pr(update, SimpleNamespace(args=["T-0001"])))
    asyncio.run(bot.cmd_ci(update, SimpleNamespace(args=["T-0001"])))
    asyncio.run(bot.cmd_gates(update, SimpleNamespace(args=["T-0001"])))
    asyncio.run(bot.cmd_continuation(update, SimpleNamespace(args=["T-0001"])))
    asyncio.run(bot.cmd_family(update, SimpleNamespace(args=["T-0001"])))
    asyncio.run(bot.cmd_session(update, SimpleNamespace(args=["chat:1:task:T-0001"])))
    asyncio.run(bot.cmd_board(update, SimpleNamespace(args=[])))

    assert replies[0] == "trace:T-0001"
    assert "Agent Presence" in replies[1]
    assert "Agent Queues" in replies[2]
    assert "Proactive Recommendations" in replies[3]
    assert "Control Plane Summary" in replies[4]
    assert replies[5] == "artifacts:T-0001"
    assert replies[6] == "pr:T-0001"
    assert replies[7] == "ci:T-0001"
    assert replies[8] == "gates:T-0001"
    assert replies[9] == "continuation:T-0001"
    assert replies[10] == "family:T-0001"
    assert replies[11] == "session:chat:1:task:T-0001"
    assert "Status Board" in replies[12]
