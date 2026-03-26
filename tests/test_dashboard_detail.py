"""Tests for dashboard detail routes."""
from types import SimpleNamespace

from nexuscrew.telegram.bot import NexusCrewBot


def test_dashboard_detail_routes():
    bot = NexusCrewBot()
    bot._orch = SimpleNamespace(
        task_tracker=SimpleNamespace(
            _tasks={1: {"T-0001": object()}},
            get=lambda chat_id, task_id: object() if task_id == "T-0001" else None,
        ),
        format_task_detail=lambda chat_id, task_id: f"detail:{task_id}",
        artifacts_summary=lambda task_id: f"artifacts:{task_id}",
        trace_summary=lambda task_id: f"trace:{task_id}",
        state_store=SimpleNamespace(list_run_events=lambda run_id: [{"run_id": run_id}]),
    )

    assert bot._dashboard_detail("/tasks/T-0001") == {"task": "detail:T-0001"}
    assert bot._dashboard_detail("/artifacts/T-0001") == {"artifacts": "artifacts:T-0001"}
    assert bot._dashboard_detail("/trace/T-0001") == {"trace": "trace:T-0001"}
    assert bot._dashboard_detail("/runs/run-1") == {"events": [{"run_id": "run-1"}]}
