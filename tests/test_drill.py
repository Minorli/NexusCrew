"""Tests for internal drill runner and command wiring."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
import random

from nexuscrew.config import AgentSpec, CrewConfig
from nexuscrew.drill import DrillScenario, DrillStageResult, TeamDrillRunner
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
            AgentSpec(role="qa", name="qa-1", model="claude"),
        ],
    )

    runner = TeamDrillRunner(config, lambda spec, executor: None)
    drill_config = runner._build_drill_config(tmp_path / "workspace")

    assert [agent.role for agent in drill_config.agents] == ["pm", "dev", "architect", "hr", "qa"]
    assert [agent.name for agent in drill_config.agents] == ["pm-1", "dev-1", "arch-1", "hr-1", "qa-1"]
    assert drill_config.hr.auto_eval_daily_limit == 0


def test_team_drill_runner_chooses_random_scenario(monkeypatch, tmp_path: Path):
    config = CrewConfig(
        project_dir=tmp_path,
        agents=[
            AgentSpec(role="pm", name="pm-1", model="claude"),
            AgentSpec(role="dev", name="dev-1", model="codex"),
            AgentSpec(role="architect", name="arch-1", model="claude"),
        ],
    )
    runner = TeamDrillRunner(config, lambda spec, executor: None)
    monkeypatch.setattr(random, "SystemRandom", lambda: SimpleNamespace(choice=lambda items: items[0]))

    scenario = runner._choose_scenario("team")

    assert isinstance(scenario, DrillScenario)
    assert scenario.id == runner.SCENARIOS[0].id


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
    (workspace / "DRILL_NOTE.md").write_text("drill ok\nstatus-board-hardening\n", encoding="utf-8")

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

    scenario = DrillScenario(
        id="status-board-hardening",
        title="Harden stale-task status board behavior",
        summary="summary",
        acceptance=["a", "b"],
        release_goal="release",
    )
    stage_results = [
        DrillStageResult("kickoff", "pm-1", "负责人和验收标准已给出", True),
        DrillStageResult("design", "arch-1", "设计约束明确", True),
        DrillStageResult("implementation", "dev-1", "Files: DRILL_NOTE.md / Validation: 1 passed", True),
        DrillStageResult("review", "arch-1", "LGTM", True),
        DrillStageResult("quality_gate", "qa-1", "结论: Go / 验证: smoke passed", True),
        DrillStageResult("acceptance", "pm-1", "验收通过", True),
        DrillStageResult("release", "pm-1", "发布说明已给出", True),
    ]

    report = asyncio.run(runner._build_report(
        orchestrator,
        config,
        workspace,
        scenario,
        transcript,
        stage_results,
    ))

    assert report.score == 100
    assert "Kickoff: pass" in report.report_text
    assert "Review: pass" in report.report_text
    assert "Quality Gate: pass" in report.report_text
    assert "Team Contributions:" in report.report_text


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
