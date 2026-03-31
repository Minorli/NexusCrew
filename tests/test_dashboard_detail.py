"""Tests for dashboard detail routes."""
from types import SimpleNamespace

from nexuscrew.telegram.bot import NexusCrewBot


def test_dashboard_detail_routes():
    bot = NexusCrewBot()
    bot._orch = SimpleNamespace(
        task_tracker=SimpleNamespace(
            _tasks={1: {"T-0001": SimpleNamespace(session_key="chat:1:task:T-0001")}},
            get=lambda chat_id, task_id: object() if task_id == "T-0001" else None,
        ),
        agent_presence=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [{"name": "alice", "presence": "busy"}],
        agent_queue_summaries=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [{"agent": "alice", "queue": []}],
        proactive_recommendations=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [{"type": "family_escalation"}],
        control_plane_summary=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: {"tasks_total": 1},
        lane_summary=lambda chat_id, lane_key, lane_summaries=None, inflight_task_ids=None, waiting_task_ids=None: f"lane:{chat_id}:{lane_key}",
        lane_trace_summary=lambda chat_id, lane_key, lane_summaries=None: f"lane-trace:{chat_id}:{lane_key}",
        format_task_detail=lambda chat_id, task_id: f"detail:{task_id}",
        artifacts_summary=lambda task_id: f"artifacts:{task_id}",
        trace_summary=lambda task_id: f"trace:{task_id}",
        gate_summary=lambda task_id: f"gates:{task_id}",
        continuation_summary=lambda task_id: f"continuation:{task_id}",
        family_summary=lambda chat_id, family_id: f"family:{family_id}" if family_id == "T-0001" else f"未找到 family: {family_id}",
        session_summary=lambda chat_id, session_key: f"session:{session_key}" if session_key == "chat:1:task:T-0001" else f"未找到 session: {session_key}",
        state_store=SimpleNamespace(list_run_events=lambda run_id: [{"run_id": run_id}]),
    )
    bot._runner.active_task_ids = lambda: {"T-0001"}
    bot._runner.waiting_task_ids = lambda: set()
    bot._runner.lane_summaries = lambda: [{"lane_key": "chat:1:task:T-0001", "chat_id": 1, "inflight": 1, "waiting": 0, "jobs": []}]

    assert bot._dashboard_detail("/tasks/T-0001") == {"task": "detail:T-0001"}
    assert bot._dashboard_detail("/artifacts/T-0001") == {"artifacts": "artifacts:T-0001"}
    assert bot._dashboard_detail("/trace/T-0001") == {"trace": "trace:T-0001"}
    assert bot._dashboard_detail("/presence") == {"presence": [{"name": "alice", "presence": "busy"}]}
    assert bot._dashboard_detail("/queues") == {"queues": [{"agent": "alice", "queue": []}]}
    assert bot._dashboard_detail("/lanes") == {"lanes": [{"lane_key": "chat:1:task:T-0001", "chat_id": 1, "inflight": 1, "waiting": 0, "jobs": []}]}
    assert bot._dashboard_detail("/lane/chat:1:task:T-0001") == {"lane": "lane:1:chat:1:task:T-0001"}
    assert bot._dashboard_detail("/lane-trace/chat:1:task:T-0001") == {"lane_trace": "lane-trace:1:chat:1:task:T-0001"}
    assert bot._dashboard_detail("/proactive") == {"proactive": [{"type": "family_escalation"}]}
    assert bot._dashboard_detail("/control") == {"control_plane": {"tasks_total": 1}}
    assert bot._dashboard_detail("/gates/T-0001") == {"gates": "gates:T-0001"}
    assert bot._dashboard_detail("/continuation/T-0001") == {"continuation": "continuation:T-0001"}
    assert bot._dashboard_detail("/families/T-0001") == {"family": "family:T-0001"}
    assert bot._dashboard_detail("/sessions/chat:1:task:T-0001") == {"session": "session:chat:1:task:T-0001"}


def test_dashboard_detail_returns_errors_for_missing_items():
    bot = NexusCrewBot()
    bot._orch = SimpleNamespace(
        task_tracker=SimpleNamespace(
            _tasks={1: {}},
            get=lambda chat_id, task_id: None,
        ),
        family_summary=lambda chat_id, family_id: f"未找到 family: {family_id}",
        session_summary=lambda chat_id, session_key: f"未找到 session: {session_key}",
        state_store=SimpleNamespace(list_run_events=lambda run_id: []),
    )

    assert bot._dashboard_detail("/tasks/T-404") == {"error": "task not found: T-404"}
    assert bot._dashboard_detail("/families/F-404") == {"error": "family not found: F-404"}
    assert bot._dashboard_detail("/sessions/S-404") == {"error": "session not found: S-404"}
