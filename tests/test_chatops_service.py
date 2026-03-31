"""Tests for shared chat surface command service."""
import asyncio
from types import SimpleNamespace

from nexuscrew.surfaces.service import ChatOpsService


def test_chatops_service_shared_paths():
    board = {}
    seen: dict[str, object] = {}

    def fake_submit(
        label,
        coro,
        chat_id=0,
        task_id="",
        run_id="",
        lane_key="",
        on_error=None,
        on_complete=None,
        on_heartbeat=None,
        heartbeat_interval=45,
        first_heartbeat_delay=20,
    ):
        seen["task_id"] = task_id
        seen["lane_key"] = lane_key
        seen["heartbeat_interval"] = heartbeat_interval
        seen["first_heartbeat_delay"] = first_heartbeat_delay
        coro.close()
        return "BG-0001"

    active_task = SimpleNamespace(
        id="T-0009",
        description="active",
        assigned_to="alice",
        history=[],
        status=SimpleNamespace(value="in_progress"),
    )

    service = ChatOpsService(
        registry=SimpleNamespace(list_all=lambda: [{"name": "alice", "role": "pm", "model": "gemini"}]),
        orchestrator=SimpleNamespace(
            _new_run_id=lambda: "run-1",
            agent_presence=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [
                {
                    "name": "alice",
                    "role": "pm",
                    "model": "gemini",
                    "presence": "busy",
                    "queue_size": 1,
                    "family_count": 1,
                    "current_task_id": "T-0001",
                }
            ],
            agent_queue_summaries=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [
                {"agent": "alice", "queue": [{"task_id": "T-0001", "runtime_state": "inflight", "next_action": "architect review"}]}
            ],
            proactive_recommendations=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [
                {"type": "continuation_next_action", "family_id": "T-0001", "actions": ["architect review"]}
            ],
            lane_runtime_summaries=lambda chat_id, lane_summaries, inflight_task_ids=None, waiting_task_ids=None: [
                {
                    "lane_key": "chat:1:task:T-0001",
                    "chat_id": 1,
                    "state": "active",
                    "inflight": 1,
                    "waiting": 0,
                    "jobs": [{"id": "BG-0001", "status": "running", "task_id": "T-0001", "label": "hello"}],
                    "head_task_id": "T-0001",
                    "head_blocked_reason": "",
                    "next_action": "architect review",
                    "ready_to_close": False,
                }
            ],
            lane_summary=lambda chat_id, lane_key, lane_summaries=None, inflight_task_ids=None, waiting_task_ids=None: f"lane:{lane_key}",
            lane_trace_summary=lambda chat_id, lane_key, lane_summaries=None: f"lane-trace:{lane_key}",
            control_plane_text=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: "control",
            router=SimpleNamespace(
                detect_first=lambda message: SimpleNamespace(name="alice", role="pm"),
                default_agent=lambda: SimpleNamespace(name="alice", role="pm"),
            ),
            state_store=SimpleNamespace(save_task=lambda chat_id, task: None),
            format_status=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: f"status:{sorted(inflight_task_ids or [])}:{sorted(waiting_task_ids or [])}",
            run_chain=lambda message, chat_id, send, run_id=None, task=None: asyncio.sleep(0),
            checkpoint_active_continuations=lambda chat_id: ["T-0001"],
            reset_history=lambda chat_id: None,
            task_tracker=SimpleNamespace(
                create=lambda chat_id, text: SimpleNamespace(id="T-0001", description=text),
                get=lambda chat_id, task_id: SimpleNamespace(id=task_id, assigned_to="alice", history=[]),
                latest_active_for_assignee=lambda chat_id, assignee: active_task,
            ),
            format_task_detail=lambda chat_id, task_id: f"task:{task_id}",
            doctor_report=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: "doctor",
            trace_summary=lambda task_id: f"trace:{task_id}",
            artifacts_summary=lambda task_id: f"artifacts:{task_id}",
            gate_summary=lambda task_id: f"gates:{task_id}",
            continuation_summary=lambda task_id: f"continuation:{task_id}",
            family_summary=lambda chat_id, family_id: f"family:{family_id}",
            session_summary=lambda chat_id, session_key: f"session:{session_key}",
            pr_summary=lambda chat_id, task_id: f"pr:{task_id}",
            ci_summary=lambda chat_id, task_id: f"ci:{task_id}",
            pause_task=lambda chat_id, task_id: True,
            resume_task=lambda chat_id, task_id, send: asyncio.sleep(0, result=True),
            replay_task=lambda chat_id, task_id, send: asyncio.sleep(0, result=True),
        ),
        runner=SimpleNamespace(
            format_status=lambda: "jobs",
            active_task_ids=lambda: {"T-0001"},
            waiting_task_ids=lambda: set(),
            lane_summaries=lambda: [{"lane_key": "chat:1:task:T-0001", "chat_id": 1, "inflight": 1, "waiting": 0, "jobs": [{"id": "BG-0001", "status": "running", "task_id": "T-0001", "label": "hello"}]}],
            cancel=lambda job_id: asyncio.sleep(0, result=True),
            submit=fake_submit,
        ),
        executor=SimpleNamespace(
            list_pending_approvals=lambda: [SimpleNamespace(id="APR-0001", risk_level="high", action_type="shell", summary="rm -rf")],
            approve_and_run=lambda approval_id: asyncio.sleep(0, result="ok"),
            reject=lambda approval_id: f"reject:{approval_id}",
        ),
        skills=SimpleNamespace(
            suggest=lambda text: [SimpleNamespace(name="fix-ci")],
            list_all=lambda: [SimpleNamespace(name="fix-ci", description="fix")],
        ),
        board_getter=lambda chat_id: board.get(chat_id, "(无状态板)"),
        board_updater=lambda chat_id: asyncio.sleep(0, result=board.setdefault(chat_id, "board")),
        crew_memory=SimpleNamespace(read=lambda tail_lines=30: f"memory:{tail_lines}"),
    )

    status = service.status_text(1)
    assert "status:['T-0001']:[]" in status
    assert "busy / q=1 / T-0001" in status
    assert "APR-0001" in service.approvals_text()
    assert asyncio.run(service.create_task(1, "fix ci")).startswith("已创建任务")
    assert service.doctor_text(1) == "doctor"
    assert asyncio.run(service.handoff(1, "T-0001", "alice")) == "任务 T-0001 已转交给 @alice"
    assert service.trace_text("T-0001") == "trace:T-0001"
    assert "Agent Presence" in service.presence_text(1)
    assert "Agent Queues" in service.queues_text(1)
    assert "Session Lanes" in service.lanes_text(1)
    assert "next=architect review" in service.lanes_text(1)
    assert service.lane_text(1, "chat:1:task:T-0001") == "lane:chat:1:task:T-0001"
    assert service.lane_trace_text(1, "chat:1:task:T-0001") == "lane-trace:chat:1:task:T-0001"
    assert "Proactive Recommendations" in service.proactive_text(1)
    assert service.control_text(1) == "control"
    assert service.gates_text("T-0001") == "gates:T-0001"
    assert service.continuation_text("T-0001") == "continuation:T-0001"
    assert service.family_text(1, "T-0001") == "family:T-0001"
    assert service.session_text(1, "chat:1:task:T-0001") == "session:chat:1:task:T-0001"
    assert asyncio.run(service.board_text(1)) == "board"
    assert service.memory_text(12) == "memory:12"
    assert service.reset_text(1) == "对话历史已清空，并为 1 个活跃任务写入续接摘要。"
    job_id = service.submit_message(1, "hello", lambda text, agent_name=None: None)
    assert job_id.startswith("BG-")
    assert seen["task_id"] == "T-0001"
    assert seen["lane_key"] == "chat:1:task:T-0001"
    assert seen["first_heartbeat_delay"] == 20


def test_chatops_service_reuses_active_task_for_follow_up():
    board = {}
    seen: dict[str, object] = {}

    def fake_submit(label, coro, chat_id=0, task_id="", run_id="", **kwargs):
        seen["task_id"] = task_id
        coro.close()
        return "BG-0001"

    active_task = SimpleNamespace(
        id="T-0009",
        description="active",
        assigned_to="alice",
        history=[],
        status=SimpleNamespace(value="in_progress"),
    )

    service = ChatOpsService(
        registry=SimpleNamespace(list_all=lambda: []),
        orchestrator=SimpleNamespace(
            _new_run_id=lambda: "run-1",
            router=SimpleNamespace(
                detect_first=lambda message: SimpleNamespace(name="alice", role="pm"),
                default_agent=lambda: SimpleNamespace(name="alice", role="pm"),
            ),
            state_store=SimpleNamespace(save_task=lambda chat_id, task: None),
            run_chain=lambda message, chat_id, send, run_id=None, task=None: asyncio.sleep(0),
            task_tracker=SimpleNamespace(
                create=lambda chat_id, text: SimpleNamespace(id="T-0010", description=text),
                latest_active_for_assignee=lambda chat_id, assignee: active_task,
            ),
        ),
        runner=SimpleNamespace(submit=fake_submit, active_task_ids=lambda: set(), waiting_task_ids=lambda: set()),
        executor=None,
        skills=SimpleNamespace(),
        board_getter=lambda chat_id: board.get(chat_id, "(无状态板)"),
        board_updater=lambda chat_id: asyncio.sleep(0, result=board.setdefault(chat_id, "board")),
        crew_memory=None,
    )

    service.submit_message(1, "@pm 然后呢", lambda text, agent_name=None: None)

    assert seen["task_id"] == "T-0009"


def test_chatops_service_prefers_less_loaded_role_alias_candidate():
    board = {}
    seen: dict[str, object] = {"events": []}

    def fake_submit(label, coro, chat_id=0, task_id="", run_id="", **kwargs):
        seen["task_id"] = task_id
        coro.close()
        return "BG-0001"

    def fake_record(run_id, chat_id, task, message, agent, reason):
        seen["events"].append((run_id, chat_id, task.id, agent.name, reason))

    service = ChatOpsService(
        registry=SimpleNamespace(list_all=lambda: []),
        orchestrator=SimpleNamespace(
            _new_run_id=lambda: "run-1",
            _pick_best_agent_for_role=lambda role, chat_id, exclude_name="": SimpleNamespace(name="charlie", role=role),
            router=SimpleNamespace(
                detect_first_routed=lambda message: {"agent": SimpleNamespace(name="bob", role="dev"), "kind": "role_alias", "role": "dev"},
                detect_first=lambda message: None,
                default_agent=lambda: SimpleNamespace(name="alice", role="pm"),
            ),
            state_store=SimpleNamespace(save_task=lambda chat_id, task: None),
            run_chain=lambda message, chat_id, send, run_id=None, task=None: asyncio.sleep(0),
            record_route_decision=fake_record,
            task_tracker=SimpleNamespace(
                create=lambda chat_id, text: SimpleNamespace(id="T-0010", description=text),
                get=lambda chat_id, task_id: None,
                latest_active_for_assignee=lambda chat_id, assignee: None,
                find_related_active=lambda chat_id, message, preferred_assignee="": None,
            ),
        ),
        runner=SimpleNamespace(submit=fake_submit, active_task_ids=lambda: set(), waiting_task_ids=lambda: set()),
        executor=None,
        skills=SimpleNamespace(),
        board_getter=lambda chat_id: board.get(chat_id, "(无状态板)"),
        board_updater=lambda chat_id: asyncio.sleep(0, result=board.setdefault(chat_id, "board")),
        crew_memory=None,
    )

    service.submit_message(1, "@dev 修复一下", lambda text, agent_name=None: None)

    assert seen["events"] == [("run-1", 1, "T-0010", "charlie", "new_task")]


def test_chatops_service_reuses_related_task_without_follow_up_hint():
    board = {}
    seen: dict[str, object] = {"events": []}

    def fake_submit(label, coro, chat_id=0, task_id="", run_id="", **kwargs):
        seen["task_id"] = task_id
        coro.close()
        return "BG-0001"

    active_task = SimpleNamespace(
        id="T-0009",
        description="排查并修复 dev-01 零交付根因",
        assigned_to="nexus-dev-02",
        history=["human_follow_up: 继续看 dev-01 的零交付问题"],
        status=SimpleNamespace(value="in_progress"),
    )

    tracker = SimpleNamespace(
        create=lambda chat_id, text: SimpleNamespace(id="T-0010", description=text),
        get=lambda chat_id, task_id: None,
        latest_active_for_assignee=lambda chat_id, assignee: None,
        find_related_active=lambda chat_id, message, preferred_assignee="": active_task,
    )

    def fake_record(run_id, chat_id, task, message, agent, reason):
        seen["events"].append((run_id, chat_id, task.id, agent.name, reason))

    service = ChatOpsService(
        registry=SimpleNamespace(list_all=lambda: []),
        orchestrator=SimpleNamespace(
            _new_run_id=lambda: "run-1",
            router=SimpleNamespace(
                detect_first=lambda message: SimpleNamespace(name="nexus-dev-02", role="dev"),
                default_agent=lambda: SimpleNamespace(name="nexus-pm-01", role="pm"),
            ),
            state_store=SimpleNamespace(save_task=lambda chat_id, task: None),
            run_chain=lambda message, chat_id, send, run_id=None, task=None: asyncio.sleep(0),
            record_route_decision=fake_record,
            task_tracker=tracker,
        ),
        runner=SimpleNamespace(submit=fake_submit, active_task_ids=lambda: set(), waiting_task_ids=lambda: set()),
        executor=None,
        skills=SimpleNamespace(),
        board_getter=lambda chat_id: board.get(chat_id, "(无状态板)"),
        board_updater=lambda chat_id: asyncio.sleep(0, result=board.setdefault(chat_id, "board")),
        crew_memory=None,
    )

    service.submit_message(1, "请继续排查 dev-01 的零交付根因", lambda text, agent_name=None: None)

    assert seen["task_id"] == "T-0009"
    assert seen["events"] == [("run-1", 1, "T-0009", "nexus-dev-02", "same_topic_task")]


def test_chatops_service_creates_child_task_from_explicit_parent_reference():
    board = {}
    seen: dict[str, object] = {"events": []}

    def fake_submit(label, coro, chat_id=0, task_id="", run_id="", **kwargs):
        seen["task_id"] = task_id
        coro.close()
        return "BG-0001"

    parent_task = SimpleNamespace(
        id="T-0009",
        description="父任务",
        family_id="T-0009",
        assigned_to="nexus-pm-01",
        history=[],
        status=SimpleNamespace(value="in_progress"),
    )
    child_task = SimpleNamespace(
        id="T-0010",
        description="请基于 T-0009 拆出一个并行子任务给 QA",
        family_id="T-0009",
        parent_task_id="T-0009",
        assigned_to="",
        history=[],
        status=SimpleNamespace(value="planning"),
    )

    tracker = SimpleNamespace(
        create=lambda chat_id, text, parent_task=None: child_task,
        get=lambda chat_id, task_id: parent_task if task_id == "T-0009" else None,
        latest_active_for_assignee=lambda chat_id, assignee: None,
        find_related_active=lambda chat_id, message, preferred_assignee="": None,
    )

    def fake_record(run_id, chat_id, task, message, agent, reason):
        seen["events"].append((run_id, chat_id, task.id, agent.name, reason))

    service = ChatOpsService(
        registry=SimpleNamespace(list_all=lambda: []),
        orchestrator=SimpleNamespace(
            _new_run_id=lambda: "run-1",
            router=SimpleNamespace(
                detect_first=lambda message: SimpleNamespace(name="nexus-qa-01", role="qa"),
                default_agent=lambda: SimpleNamespace(name="nexus-pm-01", role="pm"),
            ),
            state_store=SimpleNamespace(save_task=lambda chat_id, task: None),
            run_chain=lambda message, chat_id, send, run_id=None, task=None: asyncio.sleep(0),
            record_route_decision=fake_record,
            task_tracker=tracker,
        ),
        runner=SimpleNamespace(submit=fake_submit, active_task_ids=lambda: set(), waiting_task_ids=lambda: set()),
        executor=None,
        skills=SimpleNamespace(),
        board_getter=lambda chat_id: board.get(chat_id, "(无状态板)"),
        board_updater=lambda chat_id: asyncio.sleep(0, result=board.setdefault(chat_id, "board")),
        crew_memory=None,
    )

    service.submit_message(1, "请基于 T-0009 拆出一个并行子任务给 QA", lambda text, agent_name=None: None)



def test_chatops_service_reports_approval_lookup_failure():
    service = ChatOpsService(
        registry=SimpleNamespace(list_all=lambda: []),
        orchestrator=None,
        runner=SimpleNamespace(),
        executor=SimpleNamespace(approve_and_run=lambda approval_id: asyncio.sleep(0, result=f"未找到审批: {approval_id}")),
        skills=SimpleNamespace(),
        board_getter=lambda chat_id: "",
        board_updater=lambda chat_id: asyncio.sleep(0),
        crew_memory=None,
    )

    assert asyncio.run(service.approve("APR-404")) == "未找到审批: APR-404"


def test_chatops_service_reports_cancel_failure():
    async def fail_cancel(job_id: str):
        raise RuntimeError("runner offline")

    service = ChatOpsService(
        registry=SimpleNamespace(list_all=lambda: []),
        orchestrator=None,
        runner=SimpleNamespace(cancel=fail_cancel),
        executor=None,
        skills=SimpleNamespace(),
        board_getter=lambda chat_id: "",
        board_updater=lambda chat_id: asyncio.sleep(0),
        crew_memory=None,
    )

    text = asyncio.run(service.cancel_job("BG-0009"))

    assert text.startswith("取消后台任务失败: BG-0009")
    assert "runner offline" in text
