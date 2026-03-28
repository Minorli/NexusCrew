"""Internal behavior drill runner for full-lifecycle team simulation."""
import asyncio
import copy
import random
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .artifacts import ArtifactRecord
from .config import CrewConfig
from .executor.shell import ShellExecutor
from .github_sync import NullGitHubSync
from .memory.crew_memory import CrewMemory
from .memory.retriever import MemoryRetriever
from .memory.store import ScopedMemoryStore
from .orchestrator import Orchestrator
from .registry import AgentRegistry
from .router import Router
from .slack.sync import NullSlackSync


@dataclass(frozen=True)
class DrillScenario:
    id: str
    title: str
    summary: str
    acceptance: list[str]
    release_goal: str


@dataclass
class DrillStageResult:
    name: str
    owner: str
    summary: str
    success: bool


@dataclass
class DrillResult:
    scenario: str
    workspace_dir: Path
    score: int
    checks: list[str]
    report_text: str
    transcript: list[tuple[str | None, str]]
    stage_results: list[DrillStageResult]


class TeamDrillRunner:
    """Run a full-lifecycle collaboration drill in an isolated temp workspace."""

    IGNORE_NAMES = {
        ".venv",
        ".pytest_cache",
        "__pycache__",
        "node_modules",
        "openspec",
        ".nexuscrew_state.db",
        "run_events.jsonl",
        "run_checkpoints.jsonl",
        "artifacts.jsonl",
        "metrics_history.jsonl",
        "scoped_memory.jsonl",
        ".nexus_audit.jsonl",
        "branch_sessions.jsonl",
    }

    SCENARIOS = [
        DrillScenario(
            id="status-board-hardening",
            title="Harden stale-task status board behavior",
            summary="Improve stale task handling so old dead tasks are auto-closed and status views stay clean.",
            acceptance=[
                "stale tasks with no active background run are auto-closed",
                "Telegram receives summary-only updates",
                "tests document the watchdog behavior",
            ],
            release_goal="prepare a release note describing the reduced Telegram noise and stronger anti-stall runtime",
        ),
        DrillScenario(
            id="github-timeout-resilience",
            title="Improve GitHub network resilience",
            summary="Add safer handling for timeout / handshake failures in issue, comment, or PR network calls.",
            acceptance=[
                "network errors do not break the main task chain",
                "summary of degraded behavior is preserved",
                "tests cover the retry / fallback path",
            ],
            release_goal="prepare an operational note for GitHub degradation behavior",
        ),
        DrillScenario(
            id="telegram-noise-reduction",
            title="Reduce Telegram delivery noise",
            summary="Ensure Dev work is summarized for Telegram while raw shell detail stays in artifacts.",
            acceptance=[
                "Telegram contains only concise summaries",
                "artifacts still contain shell details",
                "review stage verifies the public message style",
            ],
            release_goal="prepare a release note for summary-first Telegram delivery UX",
        ),
        DrillScenario(
            id="task-follow-up-routing",
            title="Refine follow-up routing behavior",
            summary="Make only genuine progress follow-ups attach to older tasks while fresh requests create new tasks.",
            acceptance=[
                "status follow-ups attach correctly",
                "new work creates a fresh task",
                "tests cover both paths",
            ],
            release_goal="prepare release guidance for task continuity and follow-up handling",
        ),
    ]

    def __init__(self, config: CrewConfig, agent_factory):
        self.config = copy.deepcopy(config)
        self.agent_factory = agent_factory

    async def run(self, scenario: str = "team") -> DrillResult:
        selected = self._choose_scenario(scenario)
        workspace = self._prepare_workspace(self.config.project_dir)
        drill_config = self._build_drill_config(workspace)
        executor = ShellExecutor(
            workspace,
            timeout=drill_config.orchestrator.shell_timeout,
        )
        registry = AgentRegistry()
        for spec in drill_config.agents:
            registry.register(self.agent_factory(spec, executor))
        memory_path = workspace / ".nexuscrew_drill_memory.md"
        scoped_memory = ScopedMemoryStore(workspace / ".nexuscrew_drill_scoped_memory.jsonl")
        crew_memory = CrewMemory(memory_path)
        orchestrator = Orchestrator(
            registry,
            Router(registry),
            crew_memory,
            executor,
            max_chain_hops=drill_config.orchestrator.max_chain_hops,
            max_dev_retry=drill_config.orchestrator.max_dev_retry,
            pressure_max_prompt_len=drill_config.hr.pressure_max_prompt_len,
            hr_auto_eval_daily_limit=0,
            github_sync=NullGitHubSync(),
            slack_sync=NullSlackSync(),
            scoped_memory=scoped_memory,
            retriever=MemoryRetriever(crew_memory, scoped_memory),
        )

        transcript: list[tuple[str | None, str]] = []
        stage_results: list[DrillStageResult] = []

        async def capture_send(text: str, agent_name: str | None = None):
            transcript.append((agent_name, text))

        task = await self._run_full_flow(orchestrator, drill_config, selected, capture_send, transcript, stage_results)
        report = await self._build_report(
            orchestrator,
            drill_config,
            workspace,
            selected,
            transcript,
            stage_results,
        )
        if task is not None:
            orchestrator.artifact_store.append(ArtifactRecord(
                task_id=task.id,
                run_id=orchestrator._task_run_ids.get((0, task.id), ""),
                type="drill_report",
                source="drill",
                summary=f"score={report.score}",
                content=report.report_text,
            ))
        return report

    def _choose_scenario(self, scenario: str) -> DrillScenario:
        if scenario in ("team", "full", "random", ""):
            return random.SystemRandom().choice(self.SCENARIOS)
        for item in self.SCENARIOS:
            if item.id == scenario:
                return item
        raise ValueError(f"unsupported drill scenario: {scenario}")

    def _prepare_workspace(self, source: Path) -> Path:
        target = Path(tempfile.mkdtemp(prefix="nexuscrew-drill-"))
        shutil.copytree(
            source,
            target,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(*self.IGNORE_NAMES),
        )
        return target

    def _build_drill_config(self, workspace: Path) -> CrewConfig:
        config = copy.deepcopy(self.config)
        selected = []
        seen_roles = set()
        for spec in config.agents:
            if spec.role in ("pm", "dev", "architect", "qa", "hr") and spec.role not in seen_roles:
                selected.append(copy.deepcopy(spec))
                seen_roles.add(spec.role)
        missing = {"pm", "dev", "architect"} - seen_roles
        if missing:
            missing_text = ", ".join(sorted(missing))
            raise ValueError(f"drill requires pm/dev/architect, missing: {missing_text}")
        config.project_dir = workspace
        config.agents = selected
        config.hr.auto_eval_daily_limit = 0
        return config

    async def _run_full_flow(
        self,
        orchestrator: Orchestrator,
        config: CrewConfig,
        scenario: DrillScenario,
        send,
        transcript: list[tuple[str | None, str]],
        stage_results: list[DrillStageResult],
    ):
        pm = next(spec for spec in config.agents if spec.role == "pm").name
        dev = next(spec for spec in config.agents if spec.role == "dev").name
        architect = next(spec for spec in config.agents if spec.role == "architect").name
        qa = next((spec.name for spec in config.agents if spec.role == "qa"), "")
        hr = next((spec.name for spec in config.agents if spec.role == "hr"), "")

        task = None
        task = await self._run_stage(
            orchestrator, send, task, stage_results,
            stage_name="kickoff",
            owner=pm,
            prompt=self._prompt_kickoff(pm, dev, architect, scenario),
        )
        task = await self._run_stage(
            orchestrator, send, task, stage_results,
            stage_name="design",
            owner=architect,
            prompt=self._prompt_design(pm, dev, architect, scenario),
        )
        task = await self._run_stage(
            orchestrator, send, task, stage_results,
            stage_name="implementation",
            owner=dev,
            prompt=self._prompt_implementation(dev, architect, scenario),
        )
        task = await self._run_stage(
            orchestrator, send, task, stage_results,
            stage_name="review",
            owner=architect,
            prompt=self._prompt_review(dev, architect, scenario),
        )
        if qa:
            task = await self._run_stage(
                orchestrator, send, task, stage_results,
                stage_name="quality_gate",
                owner=qa,
                prompt=self._prompt_quality_gate(qa, dev, architect, scenario),
            )
        task = await self._run_stage(
            orchestrator, send, task, stage_results,
            stage_name="acceptance",
            owner=pm,
            prompt=self._prompt_acceptance(pm, dev, architect, scenario),
        )
        task = await self._run_stage(
            orchestrator, send, task, stage_results,
            stage_name="release",
            owner=pm,
            prompt=self._prompt_release(pm, scenario),
        )
        if hr:
            task = await self._run_stage(
                orchestrator, send, task, stage_results,
                stage_name="retrospective",
                owner=hr,
                prompt=self._prompt_retrospective(hr, scenario),
            )
        return task

    async def _run_stage(
        self,
        orchestrator: Orchestrator,
        send,
        task,
        stage_results: list[DrillStageResult],
        stage_name: str,
        owner: str,
        prompt: str,
    ):
        initial_agent = orchestrator.registry.get_by_name(owner)
        await orchestrator.run_chain(
            f"@{owner} {prompt}",
            0,
            send,
            initial_agent=initial_agent,
            task=task,
        )
        task = task or orchestrator.task_tracker.latest_active(0)
        # Read the last visible message from the stage owner for the stage summary.
        summary = self._last_agent_message(orchestrator, owner) or f"{stage_name} completed"
        success = self._stage_success(stage_name, summary)
        stage_results.append(DrillStageResult(stage_name, owner, summary[:300], success))
        return task

    def _prompt_kickoff(self, pm: str, dev: str, architect: str, scenario: DrillScenario) -> str:
        acceptance = "\n".join(f"- {item}" for item in scenario.acceptance)
        return (
            f"[DRILL stage=kickoff scenario={scenario.id}] 这是一次内部全流程演练。\n"
            f"任务标题：{scenario.title}\n"
            f"任务摘要：{scenario.summary}\n"
            f"验收标准：\n{acceptance}\n\n"
            f"请由 @{pm} 输出一份真实工程 kickoff：需求澄清、风险、负责人分配、验收标准。"
            f"只允许把开发任务派给 @{dev}，审查任务派给 @{architect}。"
            "不要拉全员做状态汇报。"
        )

    def _prompt_design(self, pm: str, dev: str, architect: str, scenario: DrillScenario) -> str:
        return (
            f"[DRILL stage=design scenario={scenario.id}] 请做设计/风险评审。\n"
            f"目标：{scenario.summary}\n"
            f"要求：给 @{dev} 明确设计约束、review 关注点、发布前检查项。"
            "必须给出具体结论，不要只说收到。"
        )

    def _prompt_implementation(self, dev: str, architect: str, scenario: DrillScenario) -> str:
        return (
            f"[DRILL stage=implementation scenario={scenario.id}] 请直接在当前临时工作区做一个最小真实实现。\n"
            "要求：\n"
            "- 落一个真实文件改动\n"
            "- 跑至少一个验证命令\n"
            "- Telegram 只给摘要，不要贴大段代码\n"
            f"- 完成后请求 @{architect} review\n"
            f"- 推荐创建/更新 `DRILL_NOTE.md` 并包含 `drill ok` 与 `{scenario.id}`\n"
        )

    def _prompt_review(self, dev: str, architect: str, scenario: DrillScenario) -> str:
        return (
            f"[DRILL stage=review scenario={scenario.id}] 请 review @{dev} 的实现结果。\n"
            "要求：必须给出 `LGTM` 或具体问题，不要只确认收到。"
        )

    def _prompt_acceptance(self, pm: str, dev: str, architect: str, scenario: DrillScenario) -> str:
        return (
            f"[DRILL stage=acceptance scenario={scenario.id}] 请由 @{pm} 做验收。\n"
            f"结合 @{dev} 的实现和 @{architect} 的审查，给出：是否验收通过、风险总结、是否允许进入发布准备。"
        )

    def _prompt_quality_gate(self, qa: str, dev: str, architect: str, scenario: DrillScenario) -> str:
        return (
            f"[DRILL stage=quality_gate scenario={scenario.id}] 请由 @{qa} 做测试与发布前质量闸门。\n"
            f"结合 @{dev} 的实现和 @{architect} 的 review，给出：测试覆盖、阻断风险、Go/No-Go 结论。"
            "如果需要验证，请直接执行最小必要的测试命令；不要只回复状态。"
        )

    def _prompt_release(self, pm: str, scenario: DrillScenario) -> str:
        return (
            f"[DRILL stage=release scenario={scenario.id}] 请由 @{pm} 输出一份发布准备说明。\n"
            f"发布目标：{scenario.release_goal}\n"
            "要求包含：发布摘要、风险、回滚点、发布后验证。"
        )

    def _prompt_retrospective(self, hr: str, scenario: DrillScenario) -> str:
        return (
            f"[DRILL stage=retrospective scenario={scenario.id}] 请由 @{hr} 输出团队复盘。\n"
            "要求：逐个说明 PM / Dev / Architect 做了什么、哪里卡住、哪里做得好。保持简洁。"
        )

    async def _build_report(
        self,
        orchestrator: Orchestrator,
        config: CrewConfig,
        workspace: Path,
        scenario: DrillScenario,
        transcript: list[tuple[str | None, str]],
        stage_results: list[DrillStageResult],
    ) -> DrillResult:
        changed_files = await orchestrator.executor.git_changed_files()
        checks: list[str] = []
        score = 100

        stage_index = {result.name: result for result in stage_results}

        kickoff = stage_index.get("kickoff")
        if kickoff and kickoff.success:
            checks.append("Kickoff: pass — PM 完成了有效的需求拆解与任务分配。")
        else:
            score -= 15
            checks.append("Kickoff: fail — PM 的 kickoff 不够明确。")

        design = stage_index.get("design")
        if design and design.success:
            checks.append("Design: pass — Architect 提供了明确设计约束。")
        else:
            score -= 15
            checks.append("Design: fail — Architect 未给出有效设计结论。")

        implementation = stage_index.get("implementation")
        if implementation and implementation.success:
            checks.append("Implementation: pass — Dev 给出了有效执行结果。")
        else:
            score -= 20
            checks.append("Implementation: fail — Dev 没有形成有效实现交付。")

        review = stage_index.get("review")
        if review and review.success:
            checks.append("Review: pass — Review 阶段有明确结果。")
        else:
            score -= 15
            checks.append("Review: fail — Review 阶段不完整。")

        quality_gate = stage_index.get("quality_gate")
        if quality_gate:
            if quality_gate.success:
                checks.append("Quality Gate: pass — QA 给出了有效测试结论。")
            else:
                score -= 10
                checks.append("Quality Gate: fail — QA 未给出有效质量闸门结论。")

        acceptance = stage_index.get("acceptance")
        if acceptance and acceptance.success:
            checks.append("Acceptance: pass — PM 完成了验收判断。")
        else:
            score -= 10
            checks.append("Acceptance: fail — 验收阶段不完整。")

        release = stage_index.get("release")
        if release and release.success:
            checks.append("Release: pass — 发布准备说明已产出。")
        else:
            score -= 10
            checks.append("Release: fail — 发布准备说明缺失。")

        retrospective = stage_index.get("retrospective")
        if retrospective:
            checks.append("Retrospective: pass — 团队复盘已生成。")

        if changed_files:
            checks.append("Workspace: pass — 临时工作区存在实际文件改动。")
        else:
            score -= 15
            checks.append("Workspace: fail — 临时工作区未检测到改动。")

        drill_note = workspace / "DRILL_NOTE.md"
        if drill_note.exists() and scenario.id in drill_note.read_text(encoding="utf-8", errors="replace"):
            checks.append("Drill Artifact: pass — DRILL_NOTE.md 已写入场景痕迹。")
        else:
            score -= 10
            checks.append("Drill Artifact: fail — 缺少 DRILL_NOTE.md 场景痕迹。")

        contributions = self._build_contributions(stage_results)
        report_lines = [
            "🧪 Drill Report",
            "",
            f"Scenario: {scenario.id}",
            f"Title: {scenario.title}",
            f"Score: {max(score, 0)}/100",
            f"Workspace: {workspace}",
            "",
            "Checks:",
            *[f"- {item}" for item in checks],
            "",
            "Team Contributions:",
            *[f"- {line}" for line in contributions],
        ]

        return DrillResult(
            scenario=scenario.id,
            workspace_dir=workspace,
            score=max(score, 0),
            checks=checks,
            report_text="\n".join(report_lines),
            transcript=transcript,
            stage_results=stage_results,
        )

    def _build_contributions(self, stage_results: list[DrillStageResult]) -> list[str]:
        lines = []
        for result in stage_results:
            summary = result.summary.replace("\n", " ")[:180]
            state = "ok" if result.success else "needs_attention"
            lines.append(f"{result.owner} [{result.name}/{state}] {summary}")
        return lines

    def _last_agent_message(self, orchestrator: Orchestrator, agent_name: str) -> str:
        history = orchestrator._histories.get(0, [])
        for item in reversed(history):
            if item.get("agent") == agent_name:
                return item.get("content", "")
        return ""

    def _stage_success(self, stage_name: str, text: str) -> bool:
        lowered = text.lower()
        if stage_name == "kickoff":
            return "[p0]" in lowered or "验收" in text or "负责人" in text
        if stage_name == "design":
            return any(token in text for token in ("约束", "风险", "审查", "设计"))
        if stage_name == "implementation":
            return any(token in text for token in ("Files:", "Validation:", "Code Review 请求", "阻塞"))
        if stage_name == "review":
            return any(token in text for token in ("LGTM", "打回", "问题", "需改进"))
        if stage_name == "quality_gate":
            return any(token in text for token in ("Go", "No-Go", "NO-GO", "风险", "覆盖", "阻断", "验证"))
        if stage_name == "acceptance":
            return any(token in text for token in ("验收", "通过", "不通过", "风险"))
        if stage_name == "release":
            return any(token in text for token in ("发布", "回滚", "验证", "release"))
        if stage_name == "retrospective":
            return any(token in text for token in ("PM", "Dev", "Architect", "团队"))
        return False
