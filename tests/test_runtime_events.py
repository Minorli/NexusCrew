"""Tests for runtime event log persistence and GitHub conversation mirroring."""
import asyncio
from pathlib import Path
import urllib.error

from nexuscrew.agents.base import AgentArtifacts, BaseAgent
from nexuscrew.artifacts import ArtifactRecord
from nexuscrew.executor.shell import ShellExecutor
from nexuscrew import github_sync as github_sync_module
from nexuscrew.github_sync import GitHubConversationSync
from nexuscrew.memory.crew_memory import CrewMemory
from nexuscrew.orchestrator import Orchestrator
from nexuscrew.registry import AgentRegistry
from nexuscrew.router import Router
from nexuscrew.runtime.events import RunEvent
from nexuscrew.runtime.store import EventStore
from nexuscrew.task_state import TaskStatus


class FakeAgent(BaseAgent):
    def __init__(self, name: str, role: str, reply: str,
                 artifacts: AgentArtifacts | None = None):
        super().__init__(name, role, "test")
        self.reply = reply
        self.artifacts = artifacts or AgentArtifacts()

    async def handle(self, message, history, crew_memory):
        return self.reply, self.artifacts


class FakeGitHubSync:
    def __init__(self):
        self.issues: list[tuple[str, str]] = []
        self.comments: list[tuple[str, str, str]] = []

    async def ensure_task_issue(self, task, initial_message: str = "") -> None:
        if task.github_issue_number:
            return
        task.github_issue_number = 101
        task.github_issue_url = "https://github.example/issues/101"
        self.issues.append((task.id, initial_message))

    async def mirror_comment(self, task, actor: str, body: str) -> None:
        self.comments.append((task.id, actor, body))


async def _direct_to_thread(func, *args, **kwargs):
    return func(*args, **kwargs)


def test_event_store_append_and_filter(tmp_path: Path):
    store = EventStore(tmp_path / "events.jsonl")
    store.append(RunEvent(run_id="r1", chat_id=1, type="run_created", actor="system"))
    store.append(RunEvent(run_id="r1", chat_id=1, type="agent_reply", actor="alice"))
    store.append(RunEvent(run_id="r2", chat_id=1, type="run_created", actor="system"))

    events = store.list_run("r1")

    assert [event.type for event in events] == ["run_created", "agent_reply"]


def test_orchestrator_records_events_and_github_comments(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "任务完成"))
    store = EventStore(tmp_path / "run_events.jsonl")
    github_sync = FakeGitHubSync()
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
        event_store=store,
        github_sync=github_sync,
    )

    sent: list[tuple[str, str | None]] = []

    async def send(text: str, agent_name: str | None = None):
        sent.append((text, agent_name))

    asyncio.run(orchestrator.run_chain("@alice 处理这个需求", 1, send))

    events = store.read_all()
    event_types = [event.type for event in events]
    assert "run_created" in event_types
    assert "task_created" in event_types
    assert "agent_selected" in event_types
    assert "agent_reply" in event_types
    assert "run_completed" in event_types
    assert github_sync.issues == [("T-0001", "@alice 处理这个需求")]
    assert any(actor == "human" for _, actor, _ in github_sync.comments)
    assert any(actor == "alice" for _, actor, _ in github_sync.comments)
    assert sent[-1][1] == "alice"


def test_orchestrator_records_gate_decision_artifact_for_architect_review(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("dave", "architect", "LGTM"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )
    task = orchestrator.task_tracker.create(1, "demo")
    task.transition(TaskStatus.IN_PROGRESS)
    task.transition(TaskStatus.REVIEW_REQ)

    sent: list[tuple[str, str | None]] = []

    async def send(text: str, agent_name: str | None = None):
        sent.append((text, agent_name))

    asyncio.run(
        orchestrator.run_chain(
            "@dave 请评审",
            1,
            send,
            initial_agent=registry.get_by_name("dave"),
            task=task,
        )
    )

    artifacts = orchestrator.artifact_store.list_for_task("T-0001")
    decisions = [item for item in artifacts if item.type == "gate_decision"]

    assert any(item.summary == "review:approved" for item in decisions)


def test_gate_decision_uses_task_session_chat_id_across_duplicate_task_ids(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("dave", "architect", "LGTM"))
    store = EventStore(tmp_path / "run_events.jsonl")
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
        event_store=store,
    )
    task_a = orchestrator.task_tracker.create(1, "chat1")
    task_b = orchestrator.task_tracker.create(2, "chat2")
    task_b.transition(TaskStatus.IN_PROGRESS)
    task_b.transition(TaskStatus.REVIEW_REQ)

    orchestrator._append_gate_decision_artifact(task_b, "run-2", registry.get_by_name("dave"), "LGTM")

    events = store.list_run("run-2")

    assert events[-1].chat_id == 2
    assert events[-1].payload["session_key"] == task_b.session_key


def test_orchestrator_records_route_decision_event(tmp_path: Path):
    store = EventStore(tmp_path / "run_events.jsonl")
    orchestrator = Orchestrator(
        AgentRegistry(),
        Router(AgentRegistry()),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
        event_store=store,
    )
    task = orchestrator.task_tracker.create(1, "demo")
    agent = type("Agent", (), {"name": "alice", "role": "pm"})()

    orchestrator.record_route_decision(
        "run-1",
        1,
        task,
        "@pm 继续推进 demo",
        agent,
        "same_topic_task",
    )

    events = store.list_run("run-1")

    assert events[-1].type == "route_decision"
    assert events[-1].payload["reason"] == "same_topic_task"
    assert events[-1].payload["agent"] == "alice"
    assert events[-1].payload["task_id"] == task.id
    assert events[-1].payload["session_key"] == task.session_key
    assert events[-1].payload["family_id"] == task.family_id


def test_orchestrator_task_detail_includes_latest_route_summary(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "任务完成"))
    store = EventStore(tmp_path / "run_events.jsonl")
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
        event_store=store,
    )
    task = orchestrator.task_tracker.create(1, "demo")
    task.assigned_to = "alice"
    task.blocked_reason = "approval_required"
    orchestrator.record_route_decision("run-1", 1, task, "@pm 继续推进", registry.get_by_name("alice"), "same_topic_task")
    orchestrator.artifact_store.append(ArtifactRecord(
        task_id="T-0001",
        run_id="run-1",
        type="gate_decision",
        source="dave",
        summary="review:approved",
        content="{}",
    ))

    detail = orchestrator.format_task_detail(1, "T-0001")

    assert "最近路由: same_topic_task -> @alice" in detail
    assert "Family: T-0001" in detail
    assert "阻塞: approval_required" in detail
    assert "最近 Gate: review:approved" in detail
    assert "续接摘要: (无 continuation)" in detail


def test_family_and_session_summary_include_next_actions(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "任务完成"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )
    parent = orchestrator.task_tracker.create(1, "父任务")
    parent.assigned_to = "alice"
    parent.transition(TaskStatus.IN_PROGRESS)
    child = orchestrator.task_tracker.create(1, "子任务", parent_task=parent)
    child.assigned_to = "alice"
    child.transition(TaskStatus.IN_PROGRESS)
    orchestrator.artifact_store.append(
        ArtifactRecord(
            task_id=child.id,
            run_id="run-1",
            type="continuation_checkpoint",
            source="system",
            summary="continuation:in_progress",
            content="\n".join(
                [
                    "Goal: 子任务",
                    f"Session: {child.session_key}",
                    "Current State: in_progress",
                    "Owner: @alice",
                    f"Family: {child.family_id}",
                    "Next Action: architect review",
                    "Constraints: blocked=(none)",
                    "Artifacts: route=(none)",
                    "Stop Conditions: continue until next gate or explicit blocker",
                ]
            ),
        )
    )

    family = orchestrator.family_summary(1, "T-0001")
    session = orchestrator.session_summary(1, "chat:1:task:T-0001")

    assert "Next Actions: architect review" in family
    assert "Next Actions: architect review" in session


def test_trace_store_formats_route_and_gate_decisions(tmp_path: Path):
    store = EventStore(tmp_path / "run_events.jsonl")
    store.append(RunEvent(run_id="run-1", chat_id=1, task_id="T-0001", type="route_decision", actor="system", payload={"reason": "same_topic_task", "agent": "alice", "session_key": "chat:1:task:T-0001", "family_id": "T-0001"}))
    store.append(RunEvent(run_id="run-1", chat_id=1, task_id="T-0001", type="gate_decision", actor="dave", payload={"stage": "review", "verdict": "approved", "blocked_reason": "approval_required"}))
    store.append(RunEvent(run_id="run-1", chat_id=1, task_id="T-0001", type="continuation_checkpointed", actor="system", payload={"task_id": "T-0001", "reason": "manual_reset"}))
    store.append(RunEvent(run_id="run-1", chat_id=1, task_id="T-0001", type="proactive_recommendation", actor="system", payload={"type": "family_escalation", "family_id": "T-0001", "reason": "pending_approval"}))

    from nexuscrew.trace.store import TraceStore

    trace = TraceStore(store).format_task_timeline("T-0001")

    assert "route_decision" in trace
    assert "same_topic_task -> @alice" in trace
    assert "family=T-0001" in trace
    assert "session=chat:1:task:T-0001" in trace
    assert "gate_decision" in trace
    assert "review:approved" in trace
    assert "blocked=approval_required" in trace
    assert "continuation_checkpointed" in trace
    assert "manual_reset" in trace
    assert "proactive_recommendation" in trace
    assert "pending_approval" in trace


def test_artifacts_and_trace_can_be_filtered_by_chat(tmp_path: Path):
    from nexuscrew.artifacts import ArtifactStore, ArtifactRecord
    from nexuscrew.trace.store import TraceStore

    artifacts = ArtifactStore(tmp_path / "artifacts.jsonl")
    artifacts.append(ArtifactRecord(task_id="T-0001", run_id="run-1", type="gate_decision", source="alice", summary="review:approved", chat_id=1))
    artifacts.append(ArtifactRecord(task_id="T-0001", run_id="run-2", type="gate_decision", source="bob", summary="review:changes_requested", chat_id=2))

    store = EventStore(tmp_path / "events.jsonl")
    store.append(RunEvent(run_id="run-1", chat_id=1, task_id="T-0001", type="route_decision", actor="system", payload={"reason": "same_topic_task", "agent": "alice"}))
    store.append(RunEvent(run_id="run-2", chat_id=2, task_id="T-0001", type="route_decision", actor="system", payload={"reason": "same_topic_task", "agent": "bob"}))

    trace = TraceStore(store).format_task_timeline("T-0001", chat_id=2)

    assert [item.summary for item in artifacts.list_for_task("T-0001", chat_id=2)] == ["review:changes_requested"]
    assert "@bob" in trace
    assert "@alice" not in trace


def test_doctor_report_includes_agent_queues_and_proactive_recommendations(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )
    parent = orchestrator.task_tracker.create(1, "父任务")
    parent.assigned_to = "alice"
    parent.transition(TaskStatus.IN_PROGRESS)
    child = orchestrator.task_tracker.create(1, "子任务", parent_task=parent)
    child.assigned_to = "alice"
    child.transition(TaskStatus.IN_PROGRESS)
    child.blocked_reason = "approval_required"
    orchestrator.artifact_store.append(
        ArtifactRecord(
            task_id=child.id,
            run_id="run-1",
            type="continuation_checkpoint",
            source="system",
            summary="continuation:in_progress",
            content="\n".join(
                [
                    "Goal: 子任务",
                    f"Session: {child.session_key}",
                    "Current State: in_progress",
                    "Owner: @alice",
                    f"Family: {child.family_id}",
                    "Next Action: qa quality gate",
                    "Constraints: blocked=approval_required",
                    "Artifacts: route=(none)",
                    "Stop Conditions: stop until approval_required is resolved",
                ]
            ),
        )
    )

    report = orchestrator.doctor_report(
        1,
        lane_summaries=[{"lane_key": "chat:1:task:T-0001", "chat_id": 1, "state": "congested", "inflight": 1, "waiting": 1, "head_job_id": "BG-0001", "jobs": []}],
    )

    assert "Agent Queues:" in report
    assert "@alice: T-0002:blocked" in report
    assert "Proactive Recommendations:" in report
    assert "family T-0001: blocked / pending_approval" in report
    assert "blocked=1" in report
    assert "next -> qa quality gate" in report
    assert "🧩 Sessions:" in report
    assert "🛣️ Session Lanes:" in report
    assert "chat:1:task:T-0001: congested / inflight=1 / waiting=1" in report


def test_agent_queue_summaries_include_session_and_next_action(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )
    task = orchestrator.task_tracker.create(1, "demo")
    task.assigned_to = "alice"
    task.transition(TaskStatus.IN_PROGRESS)
    orchestrator.artifact_store.append(
        ArtifactRecord(
            task_id=task.id,
            run_id="run-1",
            type="continuation_checkpoint",
            source="system",
            summary="continuation:in_progress",
            content="\n".join(
                [
                    "Goal: demo",
                    f"Session: {task.session_key}",
                    "Current State: in_progress",
                    "Owner: @alice",
                    f"Family: {task.family_id}",
                    "Next Action: request human decision",
                    "Constraints: blocked=(none)",
                    "Artifacts: route=(none)",
                    "Stop Conditions: continue until next gate or explicit blocker",
                ]
            ),
        )
    )

    rows = orchestrator.agent_queue_summaries(1)

    assert rows[0]["queue"][0]["session_key"] == task.session_key
    assert rows[0]["queue"][0]["next_action"] == "request human decision"


def test_agent_presence_includes_runtime_counts(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )
    blocked = orchestrator.task_tracker.create(1, "blocked")
    blocked.assigned_to = "alice"
    blocked.transition(TaskStatus.IN_PROGRESS)
    blocked.blocked_reason = "approval_required"
    waiting = orchestrator.task_tracker.create(1, "waiting")
    waiting.assigned_to = "alice"
    waiting.transition(TaskStatus.IN_PROGRESS)

    rows = orchestrator.agent_presence(1, waiting_task_ids={"T-0002"})

    assert rows[0]["presence"] == "blocked"
    assert rows[0]["blocked_count"] == 1
    assert rows[0]["waiting_count"] == 1
    assert rows[0]["queue_size"] == 2


def test_orchestrator_pick_best_agent_for_role_prefers_less_loaded_candidate(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("bob", "dev", "done"))
    registry.register(FakeAgent("charlie", "dev", "done"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )
    heavy = orchestrator.task_tracker.create(1, "重任务")
    heavy.assigned_to = "bob"
    heavy.transition(TaskStatus.IN_PROGRESS)
    blocked = orchestrator.task_tracker.create(1, "堵塞任务")
    blocked.assigned_to = "bob"
    blocked.transition(TaskStatus.IN_PROGRESS)
    blocked.blocked_reason = "approval_required"

    picked = orchestrator._pick_best_agent_for_role("dev", 1)

    assert picked.name == "charlie"


def test_orchestrator_proactive_tick_records_recommendations(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    store = EventStore(tmp_path / "run_events.jsonl")
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
        event_store=store,
    )
    parent = orchestrator.task_tracker.create(1, "父任务")
    parent.assigned_to = "alice"
    parent.transition(TaskStatus.IN_PROGRESS)
    child = orchestrator.task_tracker.create(1, "子任务", parent_task=parent)
    child.assigned_to = "alice"
    child.transition(TaskStatus.IN_PROGRESS)
    child.blocked_reason = "approval_required"

    async def send(text: str, agent_name: str | None = None):
        return None

    emitted = asyncio.run(
        orchestrator.proactive_tick(
            lambda chat_id: send,
            active_task_ids=set(),
            waiting_task_ids=set(),
            notify_chat=False,
        )
    )

    assert any(item["type"] == "family_escalation" for item in emitted)
    events = store.read_all()


def test_orchestrator_records_gate_decision_event_for_qa_and_pm(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("qa-1", "qa", "结论: Conditional Go\n风险: low"))
    registry.register(FakeAgent("alice", "pm", "验收不通过，需要补齐边界验证"))
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
    task.transition(TaskStatus.ACCEPTED)

    async def send(text: str, agent_name: str | None = None):
        return None

    asyncio.run(
        orchestrator.run_chain(
            "@qa-1 请做回归测试并给结论",
            1,
            send,
            initial_agent=registry.get_by_name("qa-1"),
            task=task,
        )
    )

    artifacts = orchestrator.artifact_store.list_for_task(task.id)
    assert any(item.summary == "quality_gate:approved" for item in artifacts if item.type == "gate_decision")
    assert task.status == TaskStatus.VALIDATING
    assert task.blocked_reason == ""

    asyncio.run(
        orchestrator.run_chain(
            "@alice 请验收",
            1,
            send,
            initial_agent=registry.get_by_name("alice"),
            task=task,
        )
    )

    artifacts = orchestrator.artifact_store.list_for_task(task.id)
    assert any(item.summary == "acceptance:rejected" for item in artifacts if item.type == "gate_decision")
    assert task.status == TaskStatus.IN_PROGRESS
    assert task.blocked_reason == "acceptance_rejected"


def test_pm_reply_with_unfinished_language_does_not_complete_task(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "验收还未完成，需要继续验证"))
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
    task.transition(TaskStatus.ACCEPTED)
    task.transition(TaskStatus.VALIDATING)

    orchestrator._append_gate_decision_artifact(task, "run-1", registry.get_by_name("alice"), "验收还未完成，需要继续验证")
    orchestrator._update_task_blocked_reason(task, registry.get_by_name("alice"), "验收还未完成，需要继续验证", "验收还未完成，需要继续验证", AgentArtifacts())
    orchestrator._advance_task_after_reply(task, registry.get_by_name("alice"), "验收还未完成，需要继续验证")

    artifacts = orchestrator.artifact_store.list_for_task(task.id)

    assert not any(item.summary == "acceptance:accepted" for item in artifacts if item.type == "gate_decision")
    assert task.status == TaskStatus.VALIDATING
    assert task.blocked_reason == ""


def test_orchestrator_appends_continuation_checkpoint_on_run_completion(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "任务完成"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )

    async def send(text: str, agent_name: str | None = None):
        return None

    asyncio.run(orchestrator.run_chain("@alice 处理这个需求", 1, send))

    artifacts = orchestrator.artifact_store.list_for_task("T-0001")
    continuation = [item for item in artifacts if item.type == "continuation_checkpoint"]

    assert continuation
    assert "Goal:" in continuation[-1].content
    assert "Session: chat:1:task:T-0001" in continuation[-1].content
    assert "Next Action:" in continuation[-1].content
    assert "Stop Conditions:" in continuation[-1].content


def test_orchestrator_checkpoint_active_continuations_writes_artifacts(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    store = EventStore(tmp_path / "run_events.jsonl")
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
        event_store=store,
    )
    task = orchestrator.task_tracker.create(1, "demo")
    task.assigned_to = "alice"
    task.transition(TaskStatus.IN_PROGRESS)
    orchestrator._task_run_ids[(1, task.id)] = "run-1"

    saved = orchestrator.checkpoint_active_continuations(1)

    assert saved == ["T-0001"]
    artifacts = orchestrator.artifact_store.list_for_task("T-0001")
    assert any(item.type == "continuation_checkpoint" for item in artifacts)
    assert any(event.type == "continuation_checkpointed" for event in store.read_all())


def test_orchestrator_proactive_tick_dedupes_same_recommendation(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    store = EventStore(tmp_path / "run_events.jsonl")
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
        event_store=store,
    )
    parent = orchestrator.task_tracker.create(1, "父任务")
    parent.assigned_to = "alice"
    parent.transition(TaskStatus.IN_PROGRESS)
    child = orchestrator.task_tracker.create(1, "子任务", parent_task=parent)
    child.assigned_to = "alice"
    child.transition(TaskStatus.IN_PROGRESS)
    child.blocked_reason = "approval_required"

    async def send(text: str, agent_name: str | None = None):
        return None

    first = asyncio.run(orchestrator.proactive_tick(lambda chat_id: send, notify_chat=False))
    second = asyncio.run(orchestrator.proactive_tick(lambda chat_id: send, notify_chat=False))

    assert first
    assert second == []


def test_orchestrator_proactive_recommendations_include_idle_capacity_and_family_completion(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    registry.register(FakeAgent("bob", "dev", "done"))
    registry.register(FakeAgent("carol", "qa", "done"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )
    parent = orchestrator.task_tracker.create(1, "父任务")
    parent.assigned_to = "alice"
    parent.transition(TaskStatus.IN_PROGRESS)
    done_child = orchestrator.task_tracker.create(1, "已完成子任务", parent_task=parent)
    done_child.assigned_to = "alice"
    done_child.transition(TaskStatus.IN_PROGRESS)
    done_child.transition(TaskStatus.REVIEW_REQ)
    done_child.transition(TaskStatus.REVIEWING)
    done_child.transition(TaskStatus.ACCEPTED)
    done_child.transition(TaskStatus.VALIDATING)
    done_child.transition(TaskStatus.DONE)
    open_child = orchestrator.task_tracker.create(1, "待收尾子任务", parent_task=parent)
    open_child.assigned_to = "alice"
    open_child.transition(TaskStatus.IN_PROGRESS)
    orchestrator.artifact_store.append(
        ArtifactRecord(
            task_id=open_child.id,
            run_id="run-1",
            type="continuation_checkpoint",
            source="system",
            summary="continuation:in_progress",
            content="\n".join(
                [
                    "Goal: 待收尾子任务",
                    f"Session: {open_child.session_key}",
                    "Current State: in_progress",
                    "Owner: @alice",
                    f"Family: {open_child.family_id}",
                    "Next Action: qa quality gate",
                    "Constraints: blocked=(none)",
                    "Artifacts: route=(none)",
                    "Stop Conditions: continue until next gate or explicit blocker",
                ]
            ),
        )
    )

    extra = orchestrator.task_tracker.create(1, "额外任务")
    extra.assigned_to = "alice"
    extra.transition(TaskStatus.IN_PROGRESS)

    recs = orchestrator.proactive_recommendations(1)

    assert any(item["type"] == "family_completion" for item in recs)
    assert any(item["type"] == "idle_capacity" for item in recs)
    assert any(item["type"] == "continuation_next_action" and "qa quality gate" in item["actions"] for item in recs)
    assert any(item["type"] == "session_completion" for item in recs)


def test_orchestrator_proactive_recommendations_include_ready_to_close_signals(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )
    parent = orchestrator.task_tracker.create(1, "父任务")
    parent.assigned_to = "alice"
    parent.transition(TaskStatus.IN_PROGRESS)
    parent.transition(TaskStatus.REVIEW_REQ)
    parent.transition(TaskStatus.REVIEWING)
    parent.transition(TaskStatus.ACCEPTED)
    child = orchestrator.task_tracker.create(1, "子任务", parent_task=parent)
    child.assigned_to = "alice"
    child.transition(TaskStatus.IN_PROGRESS)
    child.transition(TaskStatus.REVIEW_REQ)
    child.transition(TaskStatus.REVIEWING)
    child.transition(TaskStatus.ACCEPTED)

    recs = orchestrator.proactive_recommendations(1)

    assert any(item["type"] == "family_ready_to_close" for item in recs)
    assert any(item["type"] == "session_ready_to_close" for item in recs)


def test_orchestrator_proactive_recommendations_include_lane_congestion(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )
    task = orchestrator.task_tracker.create(1, "demo")
    task.assigned_to = "alice"
    task.transition(TaskStatus.IN_PROGRESS)

    recs = orchestrator.proactive_recommendations(
        1,
        lane_summaries=[{"lane_key": "chat:1:task:T-0001", "chat_id": 1, "inflight": 1, "waiting": 2}],
    )

    assert any(item["type"] == "lane_congestion" for item in recs)


def test_control_plane_summary_includes_lane_counts(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )
    task = orchestrator.task_tracker.create(1, "demo")
    task.assigned_to = "alice"
    task.transition(TaskStatus.IN_PROGRESS)

    summary = orchestrator.control_plane_summary(
        1,
        lane_summaries=[
            {"lane_key": "chat:1:task:T-0001", "chat_id": 1, "inflight": 1, "waiting": 2},
            {"lane_key": "chat:1:task:T-0002", "chat_id": 1, "inflight": 1, "waiting": 0},
        ],
    )

    assert summary["lanes_total"] == 2
    assert summary["lanes_congested"] == 1
    assert summary["lanes_waiting"] == 2


def test_orchestrator_lane_runtime_summaries_include_head_task_details(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )
    task = orchestrator.task_tracker.create(1, "demo")
    task.assigned_to = "alice"
    task.transition(TaskStatus.IN_PROGRESS)
    task.blocked_reason = "human_input_required"
    orchestrator.artifact_store.append(
        ArtifactRecord(
            task_id=task.id,
            chat_id=1,
            run_id="run-1",
            type="continuation_checkpoint",
            source="system",
            summary="continuation:in_progress",
            content="\n".join(
                [
                    "Goal: demo",
                    f"Session: {task.session_key}",
                    "Current State: in_progress",
                    "Owner: @alice",
                    f"Family: {task.family_id}",
                    "Next Action: request human decision",
                    "Constraints: blocked=human_input_required",
                    "Artifacts: route=(none)",
                    "Stop Conditions: stop until human_input_required is resolved",
                ]
            ),
        )
    )

    rows = orchestrator.lane_runtime_summaries(
        1,
        [{"lane_key": task.session_key, "chat_id": 1, "state": "congested", "inflight": 1, "waiting": 1, "head_job_id": "BG-0001", "jobs": [{"id": "BG-0001", "status": "running", "task_id": task.id, "label": "demo"}]}],
        inflight_task_ids={task.id},
        waiting_task_ids=set(),
    )

    assert rows[0]["head_task_id"] == task.id
    assert rows[0]["head_blocked_reason"] == "human_input_required"
    assert rows[0]["next_action"] == "request human decision"
    assert rows[0]["completion_state"] == "open"
    assert rows[0]["blocked_reasons"] == ["human_input_required"]
    assert rows[0]["active_agents"] == ["alice"]
    assert rows[0]["family_ids"] == ["T-0001"]
    assert rows[0]["member_count"] == 1
    assert rows[0]["owner"] == "alice"
    assert rows[0]["ready_to_close"] is False


def test_orchestrator_lane_summary_formats_lane_detail(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )
    task = orchestrator.task_tracker.create(1, "demo")
    task.assigned_to = "alice"
    task.transition(TaskStatus.IN_PROGRESS)
    orchestrator.artifact_store.append(
        ArtifactRecord(
            task_id=task.id,
            chat_id=1,
            run_id="run-1",
            type="continuation_checkpoint",
            source="system",
            summary="continuation:in_progress",
            content="\n".join(
                [
                    "Goal: demo",
                    f"Session: {task.session_key}",
                    "Current State: in_progress",
                    "Owner: @alice",
                    f"Family: {task.family_id}",
                    "Next Action: architect review",
                    "Constraints: blocked=(none)",
                    "Artifacts: route=(none)",
                    "Stop Conditions: continue until next gate or explicit blocker",
                ]
            ),
        )
    )

    text = orchestrator.lane_summary(
        1,
        task.session_key,
        lane_summaries=[{"lane_key": task.session_key, "chat_id": 1, "state": "active", "inflight": 1, "waiting": 0, "head_job_id": "BG-0001", "jobs": [{"id": "BG-0001", "status": "running", "task_id": task.id, "label": "demo"}]}],
        inflight_task_ids={task.id},
        waiting_task_ids=set(),
    )

    assert f"Lane: {task.session_key}" in text
    assert "Head Task: T-0001" in text
    assert "Next Action: architect review" in text
    assert "Owner: @alice" in text
    assert "Families: T-0001" in text


def test_orchestrator_lane_trace_summary_formats_lane_timeline(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    store = EventStore(tmp_path / "run_events.jsonl")
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
        event_store=store,
    )
    task = orchestrator.task_tracker.create(1, "demo")
    orchestrator.record_route_decision("run-1", 1, task, "@pm continue", registry.get_by_name("alice"), "same_topic_task")

    text = orchestrator.lane_trace_summary(
        1,
        task.session_key,
        lane_summaries=[{"lane_key": task.session_key, "chat_id": 1, "task_ids": [task.id], "jobs": []}],
    )

    assert f"Lane Trace: {task.session_key}" in text
    assert "route_decision" in text


def test_orchestrator_proactive_recommendations_include_lane_human_decision_and_closeout(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )
    ready = orchestrator.task_tracker.create(1, "ready")
    ready.assigned_to = "alice"
    ready.transition(TaskStatus.IN_PROGRESS)
    ready.transition(TaskStatus.REVIEW_REQ)
    ready.transition(TaskStatus.REVIEWING)
    ready.transition(TaskStatus.ACCEPTED)
    blocked = orchestrator.task_tracker.create(1, "blocked")
    blocked.assigned_to = "alice"
    blocked.transition(TaskStatus.IN_PROGRESS)
    blocked.blocked_reason = "human_input_required"
    blocked.history.append("linked")
    blocked.family_id = ready.family_id
    blocked.session_key = ready.session_key

    recs = orchestrator.proactive_recommendations(
        1,
        lane_summaries=[
            {"lane_key": blocked.session_key, "chat_id": 1, "state": "congested", "inflight": 1, "waiting": 1, "head_job_id": "BG-0001", "jobs": [{"id": "BG-0001", "status": "running", "task_id": blocked.id, "label": "demo"}]},
        ],
    )

    assert any(item["type"] == "lane_human_decision" for item in recs)


def test_orchestrator_proactive_recommendations_include_lane_review_and_quality_queue(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )
    review_task = orchestrator.task_tracker.create(1, "review")
    review_task.assigned_to = "alice"
    review_task.transition(TaskStatus.IN_PROGRESS)
    orchestrator.artifact_store.append(
        ArtifactRecord(
            task_id=review_task.id,
            chat_id=1,
            run_id="run-1",
            type="continuation_checkpoint",
            source="system",
            summary="continuation:in_progress",
            content="\n".join(
                [
                    "Goal: review",
                    f"Session: {review_task.session_key}",
                    "Current State: in_progress",
                    "Owner: @alice",
                    f"Family: {review_task.family_id}",
                    "Next Action: architect review",
                    "Constraints: blocked=(none)",
                    "Artifacts: route=(none)",
                    "Stop Conditions: continue until next gate or explicit blocker",
                ]
            ),
        )
    )
    qa_task = orchestrator.task_tracker.create(1, "qa")
    qa_task.assigned_to = "alice"
    qa_task.transition(TaskStatus.IN_PROGRESS)
    orchestrator.artifact_store.append(
        ArtifactRecord(
            task_id=qa_task.id,
            chat_id=1,
            run_id="run-2",
            type="continuation_checkpoint",
            source="system",
            summary="continuation:in_progress",
            content="\n".join(
                [
                    "Goal: qa",
                    f"Session: {qa_task.session_key}",
                    "Current State: in_progress",
                    "Owner: @alice",
                    f"Family: {qa_task.family_id}",
                    "Next Action: qa quality gate",
                    "Constraints: blocked=(none)",
                    "Artifacts: route=(none)",
                    "Stop Conditions: continue until next gate or explicit blocker",
                ]
            ),
        )
    )

    recs = orchestrator.proactive_recommendations(
        1,
        lane_summaries=[
            {"lane_key": review_task.session_key, "chat_id": 1, "state": "congested", "inflight": 1, "waiting": 1, "head_job_id": "BG-0001", "jobs": [{"id": "BG-0001", "status": "running", "task_id": review_task.id, "label": "review"}]},
            {"lane_key": qa_task.session_key, "chat_id": 1, "state": "congested", "inflight": 1, "waiting": 1, "head_job_id": "BG-0002", "jobs": [{"id": "BG-0002", "status": "running", "task_id": qa_task.id, "label": "qa"}]},
        ],
    )

    assert any(item["type"] == "lane_review_queue" for item in recs)
    assert any(item["type"] == "lane_quality_queue" for item in recs)


def test_orchestrator_proactive_recommendations_include_lane_unassigned_and_multi_owner(tmp_path: Path):
    registry = AgentRegistry()
    registry.register(FakeAgent("alice", "pm", "done"))
    orchestrator = Orchestrator(
        registry,
        Router(registry),
        CrewMemory(tmp_path / "crew_memory.md"),
        ShellExecutor(tmp_path),
    )
    task1 = orchestrator.task_tracker.create(1, "one")
    task1.transition(TaskStatus.IN_PROGRESS)
    task2 = orchestrator.task_tracker.create(1, "two")
    task2.transition(TaskStatus.IN_PROGRESS)
    task2.session_key = task1.session_key
    task2.family_id = task1.family_id
    task2.assigned_to = "alice"
    task3 = orchestrator.task_tracker.create(1, "three")
    task3.transition(TaskStatus.IN_PROGRESS)
    task3.session_key = task1.session_key
    task3.family_id = task1.family_id
    task3.assigned_to = "bob"
    orchestrator.artifact_store.append(
        ArtifactRecord(
            task_id=task1.id,
            chat_id=1,
            run_id="run-1",
            type="continuation_checkpoint",
            source="system",
            summary="continuation:in_progress",
            content="\n".join(
                [
                    "Goal: one",
                    f"Session: {task1.session_key}",
                    "Current State: in_progress",
                    "Owner: @未分配",
                    f"Family: {task1.family_id}",
                    "Next Action: continue implementation",
                    "Constraints: blocked=(none)",
                    "Artifacts: route=(none)",
                    "Stop Conditions: continue until next gate or explicit blocker",
                ]
            ),
        )
    )

    recs = orchestrator.proactive_recommendations(
        1,
        lane_summaries=[
            {"lane_key": task1.session_key, "chat_id": 1, "state": "congested", "inflight": 1, "waiting": 1, "head_job_id": "BG-0001", "jobs": [{"id": "BG-0001", "status": "running", "task_id": task1.id, "label": "one"}]},
        ],
    )

    assert any(item["type"] == "lane_unassigned" for item in recs)
    assert any(item["type"] == "lane_multi_owner" for item in recs)



def test_github_sync_formats_issue_payload(monkeypatch):
    monkeypatch.setattr(github_sync_module.asyncio, "to_thread", _direct_to_thread)
    captured: list[tuple[str, str, dict]] = []

    class CaptureSync(GitHubConversationSync):
        def _request(self, method: str, path: str, payload: dict) -> dict:
            captured.append((method, path, payload))
            if path.endswith("/issues"):
                return {"number": 12, "html_url": "https://example/issues/12"}
            return {"id": 1}

    class Task:
        id = "T-0001"
        description = "修复缓存"
        status = type("Status", (), {"value": "planning"})()
        assigned_to = "alice"
        github_issue_number = 0
        github_issue_url = ""

    sync = CaptureSync("owner/repo", "token", labels=["nexuscrew"])
    asyncio.run(sync.ensure_task_issue(Task(), initial_message="请修复缓存问题"))

    method, path, payload = captured[0]
    assert method == "POST"
    assert path == "/repos/owner/repo/issues"
    assert payload["labels"] == ["nexuscrew"]
    assert "Initial Request" in payload["body"]


def test_github_sync_swallows_422_and_splits_large_comment(monkeypatch):
    monkeypatch.setattr(github_sync_module.asyncio, "to_thread", _direct_to_thread)
    captured: list[tuple[str, str, dict]] = []

    class CaptureSync(GitHubConversationSync):
        def _request(self, method: str, path: str, payload: dict) -> dict:
            captured.append((method, path, payload))
            if path.endswith("/issues"):
                return {"number": 12, "html_url": "https://example/issues/12"}
            if len(captured) == 2:
                raise urllib.error.HTTPError(path, 422, "Unprocessable Entity", {}, None)
            return {"id": len(captured)}

    class Task:
        id = "T-0001"
        description = "修复缓存"
        status = type("Status", (), {"value": "planning"})()
        assigned_to = "alice"
        github_issue_number = 0
        github_issue_url = ""

    sync = CaptureSync("owner/repo", "token")
    large_body = "x" * (GitHubConversationSync.COMMENT_BODY_LIMIT + 10)

    asyncio.run(sync.mirror_comment(Task(), "alice", large_body))

    assert captured[0][1].endswith("/issues")
    assert captured[1][1].endswith("/comments")


def test_github_sync_swallows_network_timeout(monkeypatch):
    monkeypatch.setattr(github_sync_module.asyncio, "to_thread", _direct_to_thread)

    class CaptureSync(GitHubConversationSync):
        def _create_issue(self, task, initial_message: str) -> dict:
            raise urllib.error.URLError("handshake timeout")

    class Task:
        id = "T-0001"
        description = "修复缓存"
        status = type("Status", (), {"value": "planning"})()
        assigned_to = "alice"
        github_issue_number = 0
        github_issue_url = ""

    task = Task()
    sync = CaptureSync("owner/repo", "token")

    asyncio.run(sync.ensure_task_issue(task, initial_message="hello"))

    assert task.github_issue_number == 0
