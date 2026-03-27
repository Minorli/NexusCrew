"""Tests for internal drill runner and command wiring."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

from nexuscrew.config import AgentSpec, CrewConfig
from nexuscrew.drill import TeamDrillRunner
from nexuscrew.task_state import Task, TaskStatus
from nexuscrew.telegram import bot as bot_module
from nexuscrew.telegram.bot import NexusCrewBot


def test_team_drill_runner_builds_minimal_config(tmp_path: Path):
    config = CrewConfig(
        project_dir=tmp_path,
        agents=[
            AgentSpec(role="pm", name="pm-1", model="claude"),
            AgentSpec(role="dev", name="dev-1", model="codex"),
            AgentSpec(role="dev", name="dev-2", model="codex"),
            AgentSpec(role="architect", name="arch-1", model="claude"),
            AgentSpec(role="hr", name="hr-1", model="claude"),
        ],
    )

    runner = TeamDrillRunner(config, lambda spec, executor: None)
    drill_config = runner._build_drill_config(tmp_path / "workspace")

    assert [agent.role for agent in drill_config.agents] == ["pm", "dev", "architect"]
    assert [agent.name for agent in drill_config.agents] == ["pm-1", "dev-1", "arch-1"]
    assert drill_config.hr.auto_eval_daily_limit == 0


def test_team_drill_runner_builds_report(tmp_path: Path):
    config = CrewConfig(
        project_dir=tmp_path,
        agents=[
            AgentSpec(role="pm", name="pm-1", model="claude"),
            AgentSpec(role="dev", name="dev-1", model="codex"),
            AgentSpec(role="architect", name="arch-1", model="claude"),
        ],
    )
    runner = TeamDrillRunner(config, lambda spec, executor: None)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "DRILL_NOTE.md").write_text("drill ok\n", encoding="utf-8")

    task = Task(id="T-0001", description="drill", status=TaskStatus.PLANNING)

    class FakeExecutor:
        async def git_changed_files(self, limit: int = 8):
            return ["DRILL_NOTE.md"]

    class FakeArtifactStore:
        def __init__(self):
            self.items = []

        def append(self, artifact):
            self.items.append(artifact)

    orchestrator = SimpleNamespace(
        executor=FakeExecutor(),
        task_tracker=SimpleNamespace(latest_active=lambda chat_id: task),
        _task_run_ids={(0, "T-0001"): "run-1"},
        artifact_store=FakeArtifactStore(),
    )

    transcript = [
        ("pm-1", "**[pm-1]**\n请 @dev-1 处理 drill"),
        ("dev-1", "**[dev-1]**\nFiles: DRILL_NOTE.md\nValidation: 1 passed\nNext: @architect review"),
        ("arch-1", "**[arch-1]**\nLGTM"),
    ]

    report = asyncio.run(runner._build_report(orchestrator, config, workspace, transcript))

    assert report.score == 100
    assert "PM: pass" in report.report_text
    assert "Dev: pass" in report.report_text
    assert "Architect: pass" in report.report_text
    assert orchestrator.artifact_store.items


def test_cmd_drill_submits_background_job(monkeypatch):
    bot = NexusCrewBot()
    bot._allowed = set()
    bot.current_config = CrewConfig(project_dir=Path("/tmp"), agents=[])
    bot._orch = SimpleNamespace()

    submissions: list[dict] = []

    class FakeRunner:
        def submit(self, label, coro, **kwargs):
            submissions.append({"label": label, "kwargs": kwargs})
            coro.close()
            return "BG-0001"

    class FakeDrillRunner:
        def __init__(self, config, factory):
            self.config = config

        async def run(self, scenario="team"):
            return SimpleNamespace(report_text=f"report:{scenario}")

    bot._runner = FakeRunner()
    bot._access = SimpleNamespace(can_operate=lambda user_id: True)
    monkeypatch.setattr(bot_module, "TeamDrillRunner", FakeDrillRunner)

    replies: list[str] = []

    class FakeMessage:
        chat_id = 1
        from_user = SimpleNamespace(id=1)

        async def reply_text(self, text: str):
            replies.append(text)

    update = SimpleNamespace(message=FakeMessage(), effective_user=SimpleNamespace(id=1))

    asyncio.run(bot.cmd_drill(update, SimpleNamespace(args=[])))

    assert submissions[0]["label"] == "drill:team"
    assert "已开始" in replies[0]
