"""Tests for shared chat surface command service."""
import asyncio
from types import SimpleNamespace

from nexuscrew.surfaces.service import ChatOpsService


def test_chatops_service_shared_paths():
    board = {}
    def fake_submit(label, coro, chat_id=0, task_id="", run_id=""):
        coro.close()
        return "BG-0001"

    service = ChatOpsService(
        registry=SimpleNamespace(list_all=lambda: [{"name": "alice", "role": "pm", "model": "gemini"}]),
        orchestrator=SimpleNamespace(
            _new_run_id=lambda: "run-1",
            format_status=lambda chat_id: "status",
            run_chain=lambda message, chat_id, send, run_id=None, task=None: asyncio.sleep(0),
            reset_history=lambda chat_id: None,
            task_tracker=SimpleNamespace(
                create=lambda chat_id, text: SimpleNamespace(id="T-0001", description=text),
                get=lambda chat_id, task_id: SimpleNamespace(id=task_id, assigned_to="alice", history=[]),
            ),
            format_task_detail=lambda chat_id, task_id: f"task:{task_id}",
            doctor_report=lambda chat_id: "doctor",
            trace_summary=lambda task_id: f"trace:{task_id}",
            artifacts_summary=lambda task_id: f"artifacts:{task_id}",
            pr_summary=lambda chat_id, task_id: f"pr:{task_id}",
            ci_summary=lambda chat_id, task_id: f"ci:{task_id}",
            pause_task=lambda chat_id, task_id: True,
            resume_task=lambda chat_id, task_id, send: asyncio.sleep(0, result=True),
            replay_task=lambda chat_id, task_id, send: asyncio.sleep(0, result=True),
        ),
        runner=SimpleNamespace(
            format_status=lambda: "jobs",
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

    assert "status" in service.status_text(1)
    assert "APR-0001" in service.approvals_text()
    assert asyncio.run(service.create_task(1, "fix ci")).startswith("已创建任务")
    assert service.doctor_text(1) == "doctor"
    assert asyncio.run(service.handoff(1, "T-0001", "alice")) == "任务 T-0001 已转交给 @alice"
    assert service.trace_text("T-0001") == "trace:T-0001"
    assert asyncio.run(service.board_text(1)) == "board"
    assert service.memory_text(12) == "memory:12"
    assert service.reset_text(1) == "对话历史已清空。"
    job_id = service.submit_message(1, "hello", lambda text, agent_name=None: None)
    assert job_id.startswith("BG-")
