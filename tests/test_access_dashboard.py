"""Tests for RBAC and dashboard snapshot generation."""
import asyncio
from types import SimpleNamespace

from nexuscrew.policy.access import AccessController
from nexuscrew.telegram.bot import NexusCrewBot


def test_access_controller_rules():
    access = AccessController(
        operator_ids=[1],
        approver_ids=[2],
        admin_ids=[3],
    )

    assert access.can_operate(1) is True
    assert access.can_operate(9) is False
    assert access.can_approve(2) is True
    assert access.can_approve(1) is False
    assert access.can_administer(3) is True
    assert access.can_administer(2) is False


def test_bot_rbac_denies_without_permission():
    bot = NexusCrewBot()
    bot._access = AccessController(operator_ids=[1])
    bot._orch = SimpleNamespace(task_tracker=SimpleNamespace(get=lambda chat_id, task_id: None))

    replies: list[str] = []

    class FakeMessage:
        chat_id = 1
        from_user = SimpleNamespace(id=9)

        async def reply_text(self, text: str):
            replies.append(text)

    update = SimpleNamespace(message=FakeMessage(), effective_user=SimpleNamespace(id=9))

    asyncio.run(bot.cmd_pause(update, SimpleNamespace(args=["T-0001"])))

    assert replies == ["无操作权限。"]


def test_dashboard_snapshot_contains_tasks_and_approvals():
    bot = NexusCrewBot()
    bot.registry.register(SimpleNamespace(name="alice", role="pm", model_label="gemini"))
    bot._runner._jobs["BG-0001"] = SimpleNamespace(id="BG-0001", status="running", label="demo")
    bot._executor = SimpleNamespace(
        list_pending_approvals=lambda: [
            SimpleNamespace(id="APR-0001", status="pending", risk_level="high", summary="rm -rf"),
        ]
    )
    bot._orch = SimpleNamespace(
        task_tracker=SimpleNamespace(
            _tasks={
                1: {
                    "T-0001": SimpleNamespace(
                        id="T-0001",
                        status=SimpleNamespace(value="in_progress"),
                        assigned_to="alice",
                        github_issue_url="https://example/issues/1",
                        github_pr_url="",
                        slack_thread_ts="1.0",
                    )
                }
            }
        ),
        doctor_report=lambda chat_id: "doctor",
    )

    snapshot = bot._snapshot()

    assert snapshot["agents"][0]["name"] == "alice"
    assert snapshot["tasks"][0]["id"] == "T-0001"
    assert snapshot["approvals"][0]["id"] == "APR-0001"
    assert snapshot["background_runs"][0]["id"] == "BG-0001"
