"""Tests for RBAC and dashboard snapshot generation."""
import asyncio
from types import SimpleNamespace

from nexuscrew.telegram import bot as bot_module
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
        agent_presence=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [
            {
                "name": "alice",
                "role": "pm",
                "model": "gemini",
                "presence": "blocked",
                "queue_size": 1,
                "family_count": 1,
                "current_task_id": "T-0001",
            }
        ],
        agent_queue_summaries=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [
            {"agent": "alice", "queue": [{"task_id": "T-0001", "runtime_state": "blocked"}]}
        ],
        proactive_recommendations=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [
            {"type": "family_escalation", "family_id": "T-0001", "state": "blocked", "reason": "pending_approval"}
        ],
        task_tracker=SimpleNamespace(
            _tasks={
                1: {
                    "T-0001": SimpleNamespace(
                        id="T-0001",
                        session_key="chat:1:task:T-0001",
                        status=SimpleNamespace(value="in_progress"),
                        assigned_to="alice",
                        family_id="T-0001",
                        parent_task_id="",
                        blocked_reason="approval_required",
                        github_issue_url="https://example/issues/1",
                        github_pr_url="",
                        slack_thread_ts="1.0",
                    )
                }
            },
            _runtime_state_label=lambda task, inflight_task_ids, waiting_task_ids, task_stage_sla_seconds: "blocked",
        ),
        _family_rollups_with_actions=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [
            {"family_id": "T-0001", "state": "blocked", "completion_state": "open", "ready_to_close": False, "blocked_reasons": ["approval_required"], "next_actions": ["qa quality gate"], "members": [SimpleNamespace(id="T-0001")]}
        ],
        _session_rollups_with_actions=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [
            {"session_key": "chat:1:task:T-0001", "state": "blocked", "completion_state": "open", "ready_to_close": False, "blocked_reasons": ["approval_required"], "next_actions": ["qa quality gate"], "members": [SimpleNamespace(id="T-0001")]}
        ],
        lane_runtime_summaries=lambda chat_id, lane_summaries, inflight_task_ids=None, waiting_task_ids=None: [
            {"lane_key": "chat:1:task:T-0001", "chat_id": 1, "state": "active", "completion_state": "open", "inflight": 1, "waiting": 0, "blocked_reasons": ["approval_required"], "active_agents": ["alice"], "next_actions": ["qa quality gate"], "jobs": [{"id": "BG-0001", "status": "running", "task_id": "T-0001", "label": "demo"}]}
        ],
        _latest_route_summary=lambda task_id: "same_topic_task -> @alice",
        _latest_gate_summary=lambda task_id: "review:approved",
        task_stage_sla_seconds=600,
        doctor_report=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: "doctor",
        control_plane_summary=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: {"tasks_total": 1},
    )
    bot._runner.active_task_ids = lambda: {"T-0001"}
    bot._runner.waiting_task_ids = lambda: set()
    bot._runner.lane_summaries = lambda: [{"lane_key": "chat:1:task:T-0001", "chat_id": 1, "inflight": 1, "waiting": 0, "jobs": [{"id": "BG-0001", "status": "running", "task_id": "T-0001", "label": "demo"}]}]

    snapshot = bot._snapshot()

    assert snapshot["agents"][0]["name"] == "alice"
    assert snapshot["agent_presence"][0]["presence"] == "blocked"
    assert snapshot["families"][0]["family_id"] == "T-0001"
    assert snapshot["families"][0]["state"] == "blocked"
    assert snapshot["families"][0]["completion_state"] == "open"
    assert snapshot["families"][0]["blocked_reasons"] == ["approval_required"]
    assert snapshot["families"][0]["next_actions"] == ["qa quality gate"]
    assert snapshot["sessions"][0]["session_key"] == "chat:1:task:T-0001"
    assert snapshot["sessions"][0]["completion_state"] == "open"
    assert snapshot["sessions"][0]["ready_to_close"] is False
    assert snapshot["sessions"][0]["blocked_reasons"] == ["approval_required"]
    assert snapshot["sessions"][0]["next_actions"] == ["qa quality gate"]
    assert snapshot["agent_queues"][0]["agent"] == "alice"
    assert snapshot["lanes"][0]["lane_key"] == "chat:1:task:T-0001"
    assert snapshot["lanes"][0]["state"] == "active"
    assert snapshot["proactive"][0]["reason"] == "pending_approval"
    assert snapshot["control_plane"]["tasks_total"] == 1
    assert snapshot["tasks"][0]["id"] == "T-0001"
    assert snapshot["tasks"][0]["session_key"] == "chat:1:task:T-0001"
    assert snapshot["tasks"][0]["runtime_state"] == "blocked"
    assert snapshot["tasks"][0]["blocked_reason"] == "approval_required"
    assert snapshot["tasks"][0]["recent_route"] == "same_topic_task -> @alice"
    assert snapshot["tasks"][0]["latest_gate"] == "review:approved"
    assert snapshot["tasks"][0]["next_action"] == ""
    assert snapshot["approvals"][0]["id"] == "APR-0001"
    assert snapshot["background_runs"][0]["id"] == "BG-0001"
    assert snapshot["background_runs"][0]["lane_key"] == ""


def test_dashboard_snapshot_handles_empty_task_set():
    bot = NexusCrewBot()
    bot._orch = SimpleNamespace(
        task_tracker=SimpleNamespace(_tasks={}),
        agent_presence=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [],
        _family_rollups_with_actions=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [],
        _session_rollups_with_actions=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [],
    )
    bot._runner.active_task_ids = lambda: set()
    bot._runner.waiting_task_ids = lambda: set()
    bot._runner.list_runs = lambda: []
    bot._runner.lane_summaries = lambda: []
    bot._executor = None

    snapshot = bot._snapshot()

    assert snapshot["tasks"] == []
    assert snapshot["agent_queues"] == []
    assert snapshot["lanes"] == []
    assert snapshot["proactive"] == []
    assert snapshot["control_plane"] == {}


def test_dashboard_snapshot_aggregates_control_plane_without_double_counting_agents():
    bot = NexusCrewBot()
    bot.registry.register(SimpleNamespace(name="alice", role="pm", model_label="gemini"))
    bot._orch = SimpleNamespace(
        task_tracker=SimpleNamespace(
            _tasks={
                1: {
                    "T-0001": SimpleNamespace(
                        id="T-0001",
                        session_key="chat:1:task:T-0001",
                        status=SimpleNamespace(value="in_progress"),
                        assigned_to="alice",
                        family_id="T-0001",
                        parent_task_id="",
                        blocked_reason="",
                        github_issue_url="",
                        github_pr_url="",
                        slack_thread_ts="",
                    )
                },
                2: {
                    "T-0001": SimpleNamespace(
                        id="T-0001",
                        session_key="chat:2:task:T-0001",
                        status=SimpleNamespace(value="in_progress"),
                        assigned_to="alice",
                        family_id="T-0001",
                        parent_task_id="",
                        blocked_reason="approval_required",
                        github_issue_url="",
                        github_pr_url="",
                        slack_thread_ts="",
                    )
                },
            },
            _runtime_state_label=lambda task, inflight_task_ids, waiting_task_ids, task_stage_sla_seconds: "blocked" if getattr(task, "blocked_reason", "") else "inflight",
            get=lambda chat_id, task_id: bot._orch.task_tracker._tasks.get(chat_id, {}).get(task_id),
        ),
        agent_presence=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [
            {
                "name": "alice",
                "role": "pm",
                "model": "gemini",
                "presence": "blocked" if chat_id == 2 else "busy",
                "queue_size": 1,
                "family_count": 1,
                "current_task_id": "T-0001",
                "blocked_count": 1 if chat_id == 2 else 0,
                "inflight_count": 0 if chat_id == 2 else 1,
                "waiting_count": 0,
                "stale_count": 0,
                "load": "active",
            }
        ],
        _family_rollups_with_actions=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [
            {"family_id": f"T-0001-chat-{chat_id}", "state": "blocked" if chat_id == 2 else "inflight", "completion_state": "open", "ready_to_close": False, "blocked_reasons": ["approval_required"] if chat_id == 2 else [], "next_actions": [], "members": [SimpleNamespace(id="T-0001")]}
        ],
        _session_rollups_with_actions=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [
            {"session_key": f"chat:{chat_id}:task:T-0001", "state": "blocked" if chat_id == 2 else "inflight", "completion_state": "open", "ready_to_close": False, "blocked_reasons": ["approval_required"] if chat_id == 2 else [], "next_actions": [], "members": [SimpleNamespace(id="T-0001")]}
        ],
        lane_runtime_summaries=lambda chat_id, lane_summaries, inflight_task_ids=None, waiting_task_ids=None: [
            {"lane_key": f"chat:{chat_id}:task:T-0001", "chat_id": chat_id, "state": "blocked" if chat_id == 2 else "active", "completion_state": "open", "inflight": 1, "waiting": 0, "blocked_reasons": ["approval_required"] if chat_id == 2 else [], "active_agents": ["alice"], "next_actions": [f"next:{chat_id}:T-0001"], "jobs": [{"id": f"BG-{chat_id:04d}", "status": "running", "task_id": "T-0001", "label": "demo"}]},
        ],
        _latest_route_summary_for_chat=lambda task_id, chat_id: f"route:{chat_id}:{task_id}",
        _latest_gate_summary_for_chat=lambda task_id, chat_id: f"gate:{chat_id}:{task_id}",
        _latest_continuation_summary_for_chat=lambda task_id, chat_id: f"continuation:{chat_id}:{task_id}",
        _latest_continuation_next_action_for_chat=lambda task_id, chat_id: f"next:{chat_id}:{task_id}",
        task_stage_sla_seconds=600,
        doctor_report=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: f"doctor:{chat_id}",
        agent_queue_summaries=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [{"agent": "alice", "queue": [{"task_id": "T-0001", "runtime_state": "blocked" if chat_id == 2 else "inflight", "next_action": f'next:{chat_id}'}]}],
        proactive_recommendations=lambda chat_id, inflight_task_ids=None, waiting_task_ids=None: [{"type": "family_escalation", "family_id": f"T-0001-chat-{chat_id}", "state": "blocked" if chat_id == 2 else "inflight", "reason": "pending_approval" if chat_id == 2 else "rebalance_candidate"}],
    )
    bot._runner.active_task_ids = lambda: {"T-0001"}
    bot._runner.waiting_task_ids = lambda: set()
    bot._runner.list_runs = lambda: []
    bot._executor = None

    snapshot = bot._snapshot()

    assert snapshot["control_plane"]["tasks_total"] == 2
    assert snapshot["control_plane"]["tasks_inflight"] == 1
    assert snapshot["control_plane"]["tasks_blocked"] == 1
    assert snapshot["control_plane"]["lanes_total"] == 2
    assert snapshot["control_plane"]["agents_total"] == 1
    assert snapshot["control_plane"]["agents_blocked"] == 1
    assert snapshot["control_plane"]["proactive_total"] == 2
    assert len(snapshot["lanes"]) == 2


def test_build_app_ignores_dashboard_start_failure(monkeypatch):
    class FakeBuilder:
        def token(self, value):
            return self

        def post_init(self, callback):
            return self

        def build(self):
            return SimpleNamespace(add_handler=lambda handler: None)

    class FakePool:
        def __init__(self, token):
            self.token = token

    bot = NexusCrewBot()

    monkeypatch.setattr(bot_module, "ApplicationBuilder", lambda: FakeBuilder())
    monkeypatch.setattr(bot_module, "AgentBotPool", FakePool)
    monkeypatch.setattr(bot_module.cfg, "DASHBOARD_ENABLED", True, raising=False)
    monkeypatch.setattr(bot_module.cfg, "GITHUB_WEBHOOK_ENABLED", False, raising=False)
    monkeypatch.setattr(bot_module.cfg, "SLACK_COMMANDS_ENABLED", False, raising=False)
    monkeypatch.setattr(bot_module.cfg, "SLACK_APP_HOME_REFRESH_SECONDS", 0, raising=False)

    class BrokenDashboard:
        def __init__(self, **kwargs):
            pass

        def start(self):
            raise PermissionError("Operation not permitted")

    monkeypatch.setattr(bot_module, "DashboardServer", BrokenDashboard)

    app = bot.build_app()



def test_handle_slack_command_returns_usage_and_init_errors():
    bot = NexusCrewBot()

    assert bot._handle_slack_command({"command": "/nexus-status", "text": ""}) == "NexusCrew 尚未初始化。"

    bot._orch = SimpleNamespace(task_tracker=SimpleNamespace(_tasks={}))
    bot._service = lambda: SimpleNamespace(
        doctor_text=lambda chat_id: "doctor",
        approvals_text=lambda: "approvals",
        reject=lambda text: f"reject:{text}",
        pause=lambda chat_id, text: f"pause:{text}",
        replay=lambda chat_id, text, send: asyncio.sleep(0, result=f"replay:{text}"),
        task_text=lambda chat_id, text: f"task:{text}",
        create_task=lambda chat_id, text: asyncio.sleep(0, result=f"create:{text}"),
        approve=lambda text: asyncio.sleep(0, result=f"approve:{text}"),
    )
    bot._build_status_board = lambda chat_id: "board"
    bot._slack_default_chat_id = lambda: None
    bot._schedule_coro = lambda coro: None

    assert bot._handle_slack_command({"command": "/nexus-approve", "text": ""}) == "用法: /nexus-approve <approval_id>"
    assert bot._handle_slack_command({"command": "/nexus-reject", "text": ""}) == "用法: /nexus-reject <approval_id>"
    assert bot._handle_slack_command({"command": "/nexus-new", "text": ""}) == "用法: /nexus-new <任务描述>"
    assert bot._handle_slack_command({"command": "/nexus-pause", "text": "T-0001"}) == "没有可用的默认 chat_id。"
    assert bot._handle_slack_command({"command": "/nexus-replay", "text": "T-0001"}) == "没有可用的默认 chat_id。"


def test_handle_github_webhook_event_dedupes_delivery_id():
    bot = NexusCrewBot()
    seen: list[tuple[str, str]] = []
    state_store = SimpleNamespace(
        has_webhook_delivery=lambda provider, delivery_id: delivery_id == "dup-1",
        save_webhook_delivery=lambda provider, delivery_id, event_type, received_at: seen.append((provider, delivery_id)),
    )
    bot._orch = SimpleNamespace(
        state_store=state_store,
        ingest_github_event=lambda event_type, payload: seen.append((event_type, payload.get("id", ""))),
    )

    bot._handle_github_webhook_event("pull_request", {"id": "first"}, "dup-1")
    bot._handle_github_webhook_event("pull_request", {"id": "second"}, "fresh-1")

    assert seen == [("github", "fresh-1"), ("pull_request", "second")]
