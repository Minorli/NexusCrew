"""Internal behavior drill runner for PM/Dev/Architect collaboration."""
import asyncio
import copy
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


@dataclass
class DrillResult:
    scenario: str
    workspace_dir: Path
    score: int
    checks: list[str]
    report_text: str
    transcript: list[tuple[str | None, str]]


class TeamDrillRunner:
    """Run a lightweight collaboration drill in an isolated temp workspace."""

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

    def __init__(self, config: CrewConfig, agent_factory):
        self.config = copy.deepcopy(config)
        self.agent_factory = agent_factory

    async def run(self, scenario: str = "team") -> DrillResult:
        if scenario != "team":
            raise ValueError(f"unsupported drill scenario: {scenario}")
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

        async def capture_send(text: str, agent_name: str | None = None):
            transcript.append((agent_name, text))

        prompt = self._build_team_prompt(drill_config)
        await orchestrator.run_chain(prompt, 0, capture_send)
        report = await self._build_report(orchestrator, drill_config, workspace, transcript)
        return report

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
            if spec.role in ("pm", "dev", "architect") and spec.role not in seen_roles:
                selected.append(copy.deepcopy(spec))
                seen_roles.add(spec.role)
        if {"pm", "dev", "architect"} - seen_roles:
            missing = ", ".join(sorted({"pm", "dev", "architect"} - seen_roles))
            raise ValueError(f"drill requires pm/dev/architect, missing: {missing}")
        config.project_dir = workspace
        config.agents = selected
        config.hr.auto_eval_daily_limit = 0
        return config

    def _build_team_prompt(self, config: CrewConfig) -> str:
        pm = next(spec for spec in config.agents if spec.role == "pm")
        dev = next(spec for spec in config.agents if spec.role == "dev")
        architect = next(spec for spec in config.agents if spec.role == "architect")
        return (
            "这是一次内部团队演练，不是正式需求。请在当前临时工作区完成一次最小协作闭环。\n\n"
            f"要求：\n"
            f"1. @{pm.name} 先拆成一个最小真实开发任务，并且只指派给 @{dev.name}。\n"
            f"2. @{dev.name} 在临时工作区新增或更新 `DRILL_NOTE.md`，内容包含 `drill ok`。\n"
            f"3. @{dev.name} 至少运行一个最小验证命令，完成后请求 @{architect.name} review。\n"
            f"4. @{architect.name} 给出简短但具体的评审结论：LGTM 或具体问题。\n"
            "5. Telegram 风格要求：只给摘要，不要贴大段代码，不要贴长日志。\n"
            "6. 如果遇到阻塞，只说明一个真实阻塞点。\n"
        )

    async def _build_report(
        self,
        orchestrator: Orchestrator,
        config: CrewConfig,
        workspace: Path,
        transcript: list[tuple[str | None, str]],
    ) -> DrillResult:
        pm = next(spec for spec in config.agents if spec.role == "pm").name
        dev = next(spec for spec in config.agents if spec.role == "dev").name
        architect = next(spec for spec in config.agents if spec.role == "architect").name
        public_messages = [(agent_name or "system", text) for agent_name, text in transcript]
        changed_files = await orchestrator.executor.git_changed_files()

        checks: list[str] = []
        score = 100

        pm_message = next((text for agent_name, text in public_messages if agent_name == pm), "")
        dev_message = next((text for agent_name, text in public_messages if agent_name == dev), "")
        architect_message = next((text for agent_name, text in public_messages if agent_name == architect), "")

        pm_dev_mentions = pm_message.count(f"@{dev}")
        if pm_dev_mentions >= 1 and "@nexus-dev-" not in pm_message.replace(f"@{dev}", ""):
            checks.append("PM: pass — 单一指派明确。")
        else:
            score -= 20
            checks.append("PM: fail — 指派不够清晰或一次点名过多开发者。")

        if "```" in dev_message:
            score -= 25
            checks.append("Dev: fail — Telegram 仍出现代码块泄漏。")
        else:
            checks.append("Dev: pass — Telegram 仅输出摘要。")

        if "Files:" in dev_message and "Validation:" in dev_message:
            checks.append("Dev: pass — 摘要包含变更文件与验证结果。")
        else:
            score -= 15
            checks.append("Dev: fail — 摘要缺少文件或验证信息。")

        if "LGTM" in architect_message or "打回" in architect_message or "需改进" in architect_message:
            checks.append("Architect: pass — 评审结论具体。")
        else:
            score -= 20
            checks.append("Architect: fail — 评审不够具体。")

        drill_note = workspace / "DRILL_NOTE.md"
        if drill_note.exists() and "drill ok" in drill_note.read_text(encoding="utf-8", errors="replace").lower():
            checks.append("Workspace: pass — 临时工作区已产出演练文件。")
        else:
            score -= 20
            checks.append("Workspace: fail — 演练文件未落地。")

        if changed_files:
            checks.append("Git: pass — 临时工作区存在实际改动。")
        else:
            score -= 10
            checks.append("Git: fail — 未检测到工作区改动。")

        report_lines = [
            "🧪 Drill Report",
            "",
            f"Scenario: team",
            f"Score: {max(score, 0)}/100",
            f"Workspace: {workspace}",
            "",
            *checks,
        ]

        task = orchestrator.task_tracker.latest_active(0)
        if task is not None:
            orchestrator.artifact_store.append(ArtifactRecord(
                task_id=task.id,
                run_id=orchestrator._task_run_ids.get((0, task.id), ""),
                type="drill_report",
                source="drill",
                summary=f"score={max(score, 0)}",
                content="\n".join(report_lines),
            ))

        return DrillResult(
            scenario="team",
            workspace_dir=workspace,
            score=max(score, 0),
            checks=checks,
            report_text="\n".join(report_lines),
            transcript=transcript,
        )
