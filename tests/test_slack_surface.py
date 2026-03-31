"""Tests for Slack command surface and App Home."""
import asyncio

from nexuscrew.slack.app_home import build_home_view
from nexuscrew.slack.server import _verify_slack_signature
from nexuscrew.telegram.bot import NexusCrewBot


def test_build_home_view_contains_counts():
    view = build_home_view({
        "agents": [{"name": "alice"}],
        "tasks": [{"id": "T-0001"}],
        "approvals": [{"id": "APR-0001"}],
        "background_runs": [{"id": "BG-0001"}],
        "doctor": "all good",
    })

    assert view["type"] == "home"
    assert "NexusCrew Control Center" in str(view)
    assert "*Agents*: 1" in str(view)


def test_verify_slack_signature():
    body = b"token=demo&text=hello"
    timestamp = "1700000000"
    secret = b"secret"
    import hashlib
    import hmac
    import time

    expected = "v0=" + hmac.new(
        secret,
        f"v0:{timestamp}:".encode("utf-8") + body,
        hashlib.sha256,
    ).hexdigest()

    original_time = time.time
    time.time = lambda: 1700000000  # type: ignore[assignment]
    try:
        assert _verify_slack_signature(secret, expected, timestamp, body) is True
    finally:
        time.time = original_time  # type: ignore[assignment]


def test_bot_handles_slack_commands():
    bot = NexusCrewBot()
    bot._status_board_by_chat[0] = "board"
    scheduled = []
    bot._schedule_coro = lambda coro: scheduled.append(coro)
    bot._executor = type("Exec", (), {
        "list_pending_approvals": lambda self: [type("A", (), {"id": "APR-0001", "risk_level": "high", "summary": "rm -rf"})()],
        "approve_and_run": lambda self, approval_id: asyncio.sleep(0, result=f"approved:{approval_id}"),
        "reject": lambda self, approval_id: f"rejected:{approval_id}",
    })()
    bot._orch = type("Orch", (), {
        "control_plane_text": lambda self, chat_id, inflight_task_ids=None, waiting_task_ids=None: "control",
        "doctor_report": lambda self, chat_id: "doctor",
        "agent_presence": lambda self, chat_id, inflight_task_ids=None, waiting_task_ids=None: [{"name": "alice", "role": "pm", "model": "gemini", "presence": "busy", "queue_size": 1, "blocked_count": 0, "inflight_count": 1, "waiting_count": 0, "current_task_id": "T-0001", "load": "active"}],
        "agent_queue_summaries": lambda self, chat_id, inflight_task_ids=None, waiting_task_ids=None: [{"agent": "alice", "queue": [{"task_id": "T-0001", "runtime_state": "inflight", "next_action": "architect review"}]}],
        "proactive_recommendations": lambda self, chat_id, inflight_task_ids=None, waiting_task_ids=None: [{"type": "family_escalation", "family_id": "T-0001", "state": "blocked", "reason": "pending_approval"}],
        "lane_summary": lambda self, chat_id, lane_key, lane_summaries=None, inflight_task_ids=None, waiting_task_ids=None: f"lane:{lane_key}",
        "lane_trace_summary": lambda self, chat_id, lane_key, lane_summaries=None: f"lane-trace:{lane_key}",
        "gate_summary": lambda self, task_id: f"gates:{task_id}",
        "continuation_summary": lambda self, task_id: f"continuation:{task_id}",
        "family_summary": lambda self, chat_id, family_id: f"family:{family_id}",
        "session_summary": lambda self, chat_id, session_key: f"session:{session_key}",
        "pause_task": lambda self, chat_id, task_id: True,
        "replay_task": lambda self, chat_id, task_id, send: asyncio.sleep(0, result=True),
        "task_tracker": type("Tracker", (), {
            "_tasks": {1: {"T-0001": object()}},
            "get": lambda self, chat_id, task_id: object() if task_id == "T-0001" else None,
            "create": lambda self, chat_id, text: type("Task", (), {"id": "T-0002", "description": text})(),
        })(),
        "format_task_detail": lambda self, chat_id, task_id: f"task:{task_id}",
    })()
    bot._runner.active_task_ids = lambda: {"T-0001"}
    bot._runner.waiting_task_ids = lambda: set()
    bot._runner.lane_summaries = lambda: [{"lane_key": "chat:1:task:T-0001", "chat_id": 1, "inflight": 1, "waiting": 0, "jobs": [{"id": "BG-0001", "status": "running", "task_id": "T-0001", "label": "demo"}]}]

    assert bot._handle_slack_command({"command": "/nexus-status", "text": ""}) == "board"
    assert bot._handle_slack_command({"command": "/nexus-control", "text": ""}) == "control"
    assert bot._handle_slack_command({"command": "/nexus-doctor", "text": ""}) == "doctor"
    assert "Agent Presence" in bot._handle_slack_command({"command": "/nexus-presence", "text": ""})
    assert "Agent Queues" in bot._handle_slack_command({"command": "/nexus-queues", "text": ""})
    assert "Session Lanes" in bot._handle_slack_command({"command": "/nexus-lanes", "text": ""})
    assert bot._handle_slack_command({"command": "/nexus-lane", "text": "chat:1:task:T-0001"}) == "lane:chat:1:task:T-0001"
    assert bot._handle_slack_command({"command": "/nexus-lane-trace", "text": "chat:1:task:T-0001"}) == "lane-trace:chat:1:task:T-0001"
    assert "family_escalation" in bot._handle_slack_command({"command": "/nexus-proactive", "text": ""})
    assert "APR-0001" in bot._handle_slack_command({"command": "/nexus-approvals", "text": ""})
    assert bot._handle_slack_command({"command": "/nexus-task", "text": "T-0001"}) == "task:T-0001"
    assert bot._handle_slack_command({"command": "/nexus-gates", "text": "T-0001"}) == "gates:T-0001"
    assert bot._handle_slack_command({"command": "/nexus-continuation", "text": "T-0001"}) == "continuation:T-0001"
    assert bot._handle_slack_command({"command": "/nexus-family", "text": "T-0001"}) == "family:T-0001"
    assert bot._handle_slack_command({"command": "/nexus-session", "text": "chat:1:task:T-0001"}) == "session:chat:1:task:T-0001"
    assert bot._handle_slack_command({"command": "/nexus-new", "text": "ship it"}).startswith("已提交创建任务请求")
    assert bot._handle_slack_command({"command": "/nexus-approve", "text": "APR-0001"}) == "已提交批准执行: APR-0001"
    assert bot._handle_slack_command({"command": "/nexus-reject", "text": "APR-0001"}) == "rejected:APR-0001"
    assert bot._handle_slack_command({"command": "/nexus-pause", "text": "T-0001"}) == "任务 T-0001 将在下一个可暂停点暂停。"
    assert bot._handle_slack_command({"command": "/nexus-replay", "text": "T-0001"}) == "已提交重放: T-0001"
    for coro in scheduled:
        if hasattr(coro, "close"):
            coro.close()
    assert scheduled


def test_publish_slack_home_noop_when_disabled():
    bot = NexusCrewBot()
    asyncio.run(bot._publish_slack_home_if_enabled())


def test_start_slack_home_refresh_creates_task(monkeypatch):
    bot = NexusCrewBot()
    created = {}

    class FakeTask:
        pass

    def fake_create_task(coro):
        created["task"] = coro
        coro.close()
        return FakeTask()

    monkeypatch.setattr("nexuscrew.telegram.bot.asyncio.create_task", fake_create_task)
    monkeypatch.setattr("nexuscrew.telegram.bot.cfg.SLACK_APP_HOME_REFRESH_SECONDS", 10, raising=False)

    bot._start_slack_home_refresh_if_enabled()

    assert "task" in created
