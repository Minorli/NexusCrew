"""Core orchestrator — agent chain runner."""
import asyncio
import json
import time
from pathlib import Path
from uuid import uuid4
from .artifacts import ArtifactRecord, ArtifactStore
from .git.ci import CIResult, CIResultProvider
from .git.merge_gate import MergeGate
from .git.pr import PRWorkflow
from .git.session import BranchSession, BranchSessionStore
from .registry import AgentRegistry
from .router import Router
from .memory.crew_memory import CrewMemory
from .memory.retriever import MemoryRetriever
from .memory.store import ScopedMemoryStore
from .executor.shell import ShellExecutor
from .github_sync import NullGitHubSync
from .hr.analytics import build_trend_report, recommend_staffing
from .hr.laziness import detect_all as detect_laziness
from .hr.metrics_store import MetricsStore
from .hr.pressure import apply_pressure, calculate_pressure_level
from .metrics import MetricsCollector
from .runtime.checkpoints import CheckpointStore, RunCheckpoint
from .runtime.events import RunEvent
from .runtime.sqlite_store import DurableStateStore
from .runtime.stuck import StuckDetector
from .runtime.store import EventStore
from .slack.sync import NullSlackSync
from .task_state import TaskStatus, TaskTracker

MAX_CHAIN_HOPS = 10
MAX_DEV_RETRY  = 5


class Orchestrator:
    def __init__(self, registry: AgentRegistry, router: Router,
                 crew_memory: CrewMemory, shell_executor: ShellExecutor,
                 max_chain_hops: int = MAX_CHAIN_HOPS,
                 max_dev_retry: int = MAX_DEV_RETRY,
                 pressure_max_prompt_len: int = 500,
                 event_store: EventStore | None = None,
                 github_sync=None,
                 checkpoint_store: CheckpointStore | None = None,
                 slack_sync=None,
                 branch_sessions: BranchSessionStore | None = None,
                 pr_workflow: PRWorkflow | None = None,
                 ci_provider: CIResultProvider | None = None,
                 artifact_store: ArtifactStore | None = None,
                 scoped_memory: ScopedMemoryStore | None = None,
                 retriever: MemoryRetriever | None = None,
                 stuck_detector: StuckDetector | None = None,
                 merge_gate: MergeGate | None = None):
        self.registry      = registry
        self.router        = router
        self.crew_memory   = crew_memory
        self.executor      = shell_executor
        self.max_hops      = max_chain_hops
        self.max_retry     = max_dev_retry
        self.pressure_max_prompt_len = pressure_max_prompt_len
        # per chat_id state
        self._histories: dict[int, list[dict]] = {}
        self._dev_retries: dict[int, int]       = {}
        self._evaluation_counter = 0
        self._task_branch_attempts: set[tuple[int, str]] = set()
        self._paused_tasks: set[tuple[int, str]] = set()
        self._task_run_ids: dict[tuple[int, str], str] = {}
        self.metrics = MetricsCollector()
        self.metrics_store = MetricsStore(
            self.crew_memory.path.with_name("metrics_history.jsonl")
        )
        self.state_store = getattr(self.executor, "state_store", DurableStateStore(
            self.crew_memory.path.with_name(".nexuscrew_state.db")
        ))
        self.event_store = event_store or EventStore(
            self.crew_memory.path.with_name("run_events.jsonl")
        )
        self.checkpoint_store = checkpoint_store or CheckpointStore(
            self.crew_memory.path.with_name("run_checkpoints.jsonl")
        )
        self.branch_sessions = branch_sessions or BranchSessionStore(
            self.crew_memory.path.with_name("branch_sessions.jsonl")
        )
        self.pr_workflow = pr_workflow or PRWorkflow()
        self.ci_provider = ci_provider or CIResultProvider()
        self.artifact_store = artifact_store or ArtifactStore(
            self.crew_memory.path.with_name("artifacts.jsonl")
        )
        self.scoped_memory = scoped_memory or ScopedMemoryStore(
            self.crew_memory.path.with_name("scoped_memory.jsonl")
        )
        self.retriever = retriever or MemoryRetriever(self.crew_memory, self.scoped_memory)
        self.stuck_detector = stuck_detector or StuckDetector()
        self.merge_gate = merge_gate or MergeGate()
        self.github_sync = github_sync or NullGitHubSync()
        self.slack_sync = slack_sync or NullSlackSync()
        self.task_tracker = TaskTracker()
        self._stuck_reports: dict[tuple[int, str], object] = {}
        self._ci_overrides: dict[str, CIResult] = {}
        self._restore_tasks_from_state()

    # ── history helpers ───────────────────────────────────────────────
    def _add_history(self, chat_id: int, agent: str, content: str):
        h = self._histories.setdefault(chat_id, [])
        h.append({"agent": agent, "content": content})
        if len(h) > 20:
            self._histories[chat_id] = h[-20:]

    def reset_history(self, chat_id: int):
        self._histories.pop(chat_id, None)
        self._dev_retries.pop(chat_id, None)

    # ── main entry point ──────────────────────────────────────────────
    async def run_chain(
        self,
        message: str,
        chat_id: int,
        send,            # async callable(text: str, agent_name: str | None = None)
        initial_agent=None,
        run_id: str | None = None,
        task=None,
    ):
        """
        Run the agent chain starting from initial_agent (or router default).
        Each agent reply is scanned for @mentions to route the next hop.
        """
        agent = initial_agent or self.router.detect_first(message) \
                              or self.router.default_agent()
        if not agent:
            await send("[NexusCrew] 没有可用的 Agent，请先使用 /crew 编组。")
            return

        run_id = run_id or self._new_run_id()
        task, task_created = self._ensure_task(
            chat_id,
            message,
            agent,
            initial_agent is None and task is None,
            task=task,
        )
        self._task_run_ids[(chat_id, task.id)] = run_id
        if initial_agent is None:
            self._record_event(
                run_id,
                chat_id,
                "run_created",
                "system",
                task,
                {"message": message},
            )
        if task_created:
            self._record_event(
                run_id,
                chat_id,
                "task_created",
                "human",
                task,
                {"description": task.description},
            )
            await self.github_sync.ensure_task_issue(task, initial_message=message)
            await self.slack_sync.ensure_task_thread(task, initial_message=message)
            self.scoped_memory.append("project", "system", task.description, importance=2)
        self.scoped_memory.append("shared", "human", message, importance=2)
        self.scoped_memory.append(f"task:{task.id}", "human", message, importance=2)
        self._add_history(chat_id, "human", message)
        if initial_agent is None:
            await self.github_sync.mirror_comment(task, "human", message)
            await self.slack_sync.mirror_comment(task, "human", message)

        for hop in range(self.max_hops):
            if self._is_task_paused(chat_id, task.id):
                self._record_event(
                    run_id,
                    chat_id,
                    "run_paused",
                    "system",
                    task,
                    {"task_id": task.id, "hop": hop},
                )
                await send(f"⏸️ 任务 {task.id} 已暂停。")
                return
            self._advance_task_before_handle(task, agent)
            await self._ensure_dev_branch(chat_id, task, agent)
            self._record_event(
                run_id,
                chat_id,
                "agent_selected",
                "system",
                task,
                {"agent": agent.name, "role": agent.role, "hop": hop},
            )
            # Auto-escalate dev on too many failures
            if agent.role == "dev" and \
               self._dev_retries.get(chat_id, 0) >= self.max_retry:
                arch = self.registry.get_by_role("architect")
                if arch:
                    await send(f"⚠️ {agent.name} 连续失败 {self.max_retry} 次，自动升级给 @{arch.name}。")
                    message = (f"@{arch.name} 自动升级求助（Dev 连续失败 {self.max_retry} 次）：\n"
                               f"{message}")
                    agent = arch
                    self._dev_retries[chat_id] = 0

            # Call agent
            await send(f"_[{agent.name}/{agent.model_label} 处理中...]_")
            history  = self._histories.get(chat_id, [])
            memory   = self.retriever.retrieve(agent.role, agent.name, task.id)
            self.executor.set_context(chat_id, task.id, run_id)
            metrics = self.metrics.get(agent.name)
            metrics.record_task_start()
            t0 = time.monotonic()
            reply, artifacts = await agent.handle(message, history, memory)
            metrics.record_task_complete(
                int((time.monotonic() - t0) * 1000)
            )
            laziness_signals = detect_laziness(
                reply,
                agent.role,
                previous_reply=self._find_previous_reply(history, agent.name),
            )
            metrics.record_laziness_signals(laziness_signals)

            # Persist memory note
            if artifacts.memory_note:
                self.crew_memory.append(agent.name, artifacts.memory_note)
                metrics.record_memory_note()
                self.scoped_memory.append("shared", agent.name, artifacts.memory_note, importance=3)
                self.scoped_memory.append(f"agent:{agent.name}", agent.name, artifacts.memory_note, importance=3)
                self.scoped_memory.append(f"task:{task.id}", agent.name, artifacts.memory_note, importance=3)

            # Track dev failures
            if agent.role == "dev" and artifacts.shell_output:
                is_failure = self.executor.is_failure(artifacts.shell_output)
                metrics.record_shell_run(is_failure)
                if is_failure:
                    self._dev_retries[chat_id] = \
                        self._dev_retries.get(chat_id, 0) + 1
                    metrics.record_task_fail()
                else:
                    self._dev_retries[chat_id] = 0

            if agent.role == "architect":
                reviewed_dev = self._find_recent_dev(history)
                if reviewed_dev:
                    if "LGTM" in reply.upper():
                        self.metrics.get(reviewed_dev.name).record_review_result(True)
                    elif any(keyword in reply for keyword in ("打回", "修复", "reject", "问题")):
                        self.metrics.get(reviewed_dev.name).record_review_result(False)

            # Send reply + shell output to Telegram
            self._add_history(chat_id, agent.name, reply)
            await send(f"**[{agent.name}]**\n{reply}", agent_name=agent.name)
            self._record_event(
                run_id,
                chat_id,
                "agent_reply",
                agent.name,
                task,
                {"role": agent.role, "reply": reply},
            )
            self.artifact_store.append(ArtifactRecord(
                task_id=task.id,
                run_id=run_id,
                type="agent_reply",
                source=agent.name,
                summary=reply[:120],
                content=reply,
            ))
            await self.github_sync.mirror_comment(task, agent.name, reply)
            await self.slack_sync.mirror_comment(task, agent.name, reply)
            self.scoped_memory.append(f"task:{task.id}", agent.name, reply[:500], importance=2)
            if artifacts.shell_output:
                await send(
                    f"```\n{artifacts.shell_output[:3500]}\n```",
                    agent_name=agent.name,
                )
                self._add_history(chat_id, "shell", artifacts.shell_output[:600])
                self._record_event(
                    run_id,
                    chat_id,
                    "shell_finished",
                    agent.name,
                    task,
                    {"output": artifacts.shell_output[:1200]},
                )
                self.artifact_store.append(ArtifactRecord(
                    task_id=task.id,
                    run_id=run_id,
                    type="shell_output",
                    source=agent.name,
                    summary=artifacts.shell_output.splitlines()[0][:120],
                    content=artifacts.shell_output[:3000],
                ))
                await self.github_sync.mirror_comment(
                    task,
                    f"{agent.name}/shell",
                    f"```\n{artifacts.shell_output[:3000]}\n```",
                )
                await self.slack_sync.mirror_comment(
                    task,
                    f"{agent.name}/shell",
                    f"```{artifacts.shell_output[:3000]}```",
                )
            self._save_checkpoint(
                run_id=run_id,
                chat_id=chat_id,
                task=task,
                hop=hop,
                current_agent=agent.name,
                current_message=reply,
            )

            self._advance_task_after_reply(task, agent, reply)
            await self._maybe_sync_pr(chat_id, task, reply)
            self._update_stuck_report(chat_id, task.id)

            # Detect next agent
            next_agents = self.router.detect_all(reply)
            if not next_agents:
                break
            next_agent = next_agents[0]
            if next_agent.name == agent.name:
                break  # self-reference guard

            # Parallel dispatch if PM mentioned multiple devs
            if len(next_agents) > 1 and all(a.role == "dev" for a in next_agents):
                await asyncio.gather(*[
                    self.run_chain(
                        reply,
                        chat_id,
                        send,
                        a,
                        run_id=run_id,
                        task=task,
                    )
                    for a in next_agents
                ])
                self._record_event(
                    run_id,
                    chat_id,
                    "run_completed",
                    "system",
                    task,
                    {"mode": "parallel_dev_dispatch"},
                )
                return

            message = reply
            agent   = next_agent
        else:
            await send("⚠️ 达到最大跳转上限，请人工介入。")
            self._record_event(
                run_id,
                chat_id,
                "run_failed",
                "system",
                task,
                {"reason": "max_chain_hops"},
            )
            await self.github_sync.mirror_comment(
                task,
                "system",
                "任务链达到最大跳转上限，需要人工介入。",
            )
            self._save_checkpoint(
                run_id=run_id,
                chat_id=chat_id,
                task=task,
                hop=self.max_hops,
                current_agent=agent.name,
                current_message=message,
            )
            return

        self._record_event(
            run_id,
            chat_id,
            "run_completed",
            "system",
            task,
            {"final_agent": agent.name},
        )
        await self.github_sync.mirror_comment(
            task,
            "system",
            f"任务链完成，最终处理 Agent: `{agent.name}`，当前状态: `{task.status.value}`。",
        )
        await self.slack_sync.mirror_comment(
            task,
            "system",
            f"任务链完成，最终处理 Agent: `{agent.name}`，当前状态: `{task.status.value}`。",
        )
        self.artifact_store.append(ArtifactRecord(
            task_id=task.id,
            run_id=run_id,
            type="run_summary",
            source="system",
            summary=f"run completed by {agent.name}",
            content=f"final status={task.status.value}",
        ))
        self._save_checkpoint(
            run_id=run_id,
            chat_id=chat_id,
            task=task,
            hop=hop if "hop" in locals() else 0,
            current_agent=agent.name,
            current_message=message,
        )
        hr_agent = self.registry.get_by_role("hr")
        if hr_agent and agent.role != "hr":
            # Task 3.3 完成: 任务链结束后异步触发 HR 评估。
            asyncio.create_task(self._hr_evaluate(hr_agent, chat_id, send))

    def _find_recent_dev(self, history: list[dict]):
        # Task 3.2 完成: 从历史中回溯最近一个 Dev，给 review 指标归因。
        for item in reversed(history):
            agent = self.registry.get_by_name(item.get("agent", ""))
            if agent and agent.role == "dev":
                return agent
        return None

    async def _hr_evaluate(self, hr_agent, chat_id: int, send):
        try:
            summary = self.metrics.all_summaries()
            history = self._histories.get(chat_id, [])
            memory = self.crew_memory.read()
            prompt = (
                "请评估以下任务链路中各 Agent 的表现：\n\n"
                f"【团队指标】\n{summary}\n\n"
                f"【异常信号】\n{self._build_laziness_summary()}\n\n"
                "请使用 3.25/3.5/3.75 评分体系，输出绩效评估报告。"
            )
            reply, artifacts = await hr_agent.handle(prompt, history, memory)
            if artifacts.memory_note:
                self.crew_memory.append(hr_agent.name, artifacts.memory_note)
            self._persist_metric_snapshots()
            self._refresh_pressure_notices()
            await send(
                f"📊 [{hr_agent.name}] 绩效评估：\n{reply}",
                agent_name=hr_agent.name,
            )
            task = self.task_tracker.latest_active(chat_id)
            if task:
                self._record_event(
                    self._new_run_id(),
                    chat_id,
                    "hr_evaluation",
                    hr_agent.name,
                    task,
                    {"reply": reply},
                )
                await self.github_sync.mirror_comment(
                    task,
                    hr_agent.name,
                    f"📊 绩效评估\n\n{reply}",
                )
                await self.slack_sync.mirror_comment(
                    task,
                    hr_agent.name,
                    f"📊 绩效评估\n\n{reply}",
                )
                self.artifact_store.append(ArtifactRecord(
                    task_id=task.id,
                    run_id=self._task_run_ids.get((chat_id, task.id), ""),
                    type="hr_evaluation",
                    source=hr_agent.name,
                    summary=reply[:120],
                    content=reply,
                ))
        except Exception as err:
            await send(f"[HR 评估异常] {err}")

    def _derive_pressure_score(self, metrics) -> float:
        if metrics.retry_ratio > 2.0:
            return 3.25
        if (metrics.review_pass_first + metrics.review_reject) and metrics.first_pass_rate < 0.6:
            return 3.25
        if metrics.tasks_completed and metrics.first_pass_rate >= 0.8 and metrics.retry_ratio <= 1.0:
            return 3.75
        return 3.5

    def _refresh_pressure_notices(self):
        # Task 3.4 完成: 基于当前指标刷新每个 Agent 的 HR 督促 section。
        for agent_name, metrics in self.metrics.items():
            agent = self.registry.get_by_name(agent_name)
            if not agent or agent.role == "hr":
                continue
            current_score = self._derive_pressure_score(metrics)
            history = self.metrics_store.get_score_history(agent_name, last_n=5)
            if not history or history[-1] != current_score:
                history = (history + [current_score])[-5:]
            level = calculate_pressure_level(current_score, history)
            apply_pressure(
                self.crew_memory,
                agent_name,
                level,
                metrics,
                max_len=self.pressure_max_prompt_len,
            )

    def _find_previous_reply(self, history: list[dict], agent_name: str) -> str:
        for item in reversed(history):
            if item.get("agent") == agent_name:
                return item.get("content", "")
        return ""

    def _build_laziness_summary(self) -> str:
        lines: list[str] = []
        for agent_name, metrics in self.metrics.items():
            if metrics.laziness_signals:
                lines.append(
                    f"- {agent_name}: " + "; ".join(metrics.laziness_signals[-3:])
                )
        return "\n".join(lines) if lines else "(无)"

    def format_status(self, chat_id: int) -> str:
        return self.task_tracker.format_status(chat_id)

    def format_task_detail(self, chat_id: int, task_id: str) -> str:
        task = self.task_tracker.get(chat_id, task_id)
        if task is None:
            return f"未找到任务: {task_id}"
        branch_session = self.branch_sessions.get(chat_id, task_id)
        ci_result = self.ci_provider.get_for_pr_sync(task.github_pr_number)
        approvals = self.executor.list_pending_approvals()
        artifacts = self.artifact_store.list_for_task(task_id)
        merge_gate = self.merge_gate.build(task, ci_result, approvals, artifacts)
        parts = [
            f"任务: {task.id}",
            f"状态: {task.status.value}",
            f"负责人: @{task.assigned_to or '未分配'}",
            f"分支: {task.branch_name or '(未创建)'}",
            f"GitHub Issue: {task.github_issue_url or '(未同步)'}",
            f"GitHub PR: {task.github_pr_url or '(未创建)'}",
            f"Slack Thread: {task.slack_channel + ' / ' + task.slack_thread_ts if task.slack_thread_ts else '(未同步)'}",
            f"CI: {ci_result.summary}",
            f"Merge Gate: {merge_gate.summary}",
            "",
            self.trace_summary(task_id),
            "",
            self.artifact_store.format_for_task(task_id),
        ]
        report = self._stuck_reports.get((chat_id, task_id))
        if report:
            parts.extend(["", f"Stuck Detector: {report.summary}"])
        return "\n".join(parts)

    def doctor_report(self, chat_id: int) -> str:
        agent_names = [item["name"] for item in self.registry.list_all()]
        pending = self.executor.list_pending_approvals()
        lines = [
            "🩺 NexusCrew Doctor",
            "",
            self.task_tracker.format_status(chat_id),
            "",
            f"待审批动作: {len(pending)}",
            build_trend_report(self.metrics_store, agent_names),
            "",
            recommend_staffing(self.metrics_store, agent_names),
        ]
        if self._stuck_reports:
            lines.extend(["", "Stuck Reports:"])
            for report in self._stuck_reports.values():
                lines.append(f"  {report.task_id}: {report.summary}")
        return "\n".join(lines)

    def artifacts_summary(self, task_id: str) -> str:
        return self.artifact_store.format_for_task(task_id)

    def pr_summary(self, chat_id: int, task_id: str) -> str:
        task = self.task_tracker.get(chat_id, task_id)
        if task is None:
            return f"未找到任务: {task_id}"
        session = self.branch_sessions.get(chat_id, task_id)
        lines = [f"PR 摘要: {task_id}", ""]
        lines.append(f"分支: {session.branch_name if session else '(未创建)'}")
        lines.append(f"PR: {task.github_pr_url or '(未创建)'}")
        if session and session.base_branch:
            lines.append(f"Base: {session.base_branch}")
        return "\n".join(lines)

    def ci_summary(self, chat_id: int, task_id: str) -> str:
        task = self.task_tracker.get(chat_id, task_id)
        if task is None:
            return f"未找到任务: {task_id}"
        result = self._ci_overrides.get(task_id) or self.ci_provider.get_for_pr_sync(task.github_pr_number)
        return f"CI: {result.status}\n{result.summary}"

    def _ensure_task(self, chat_id: int, message: str, agent, create_new: bool, task=None):
        created = False
        task = task or self.task_tracker.latest_active(chat_id)
        if create_new or task is None:
            task = self.task_tracker.create(chat_id, message)
            created = True
        task.assigned_to = agent.name
        self.state_store.save_task(chat_id, task)
        return task, created

    def _advance_task_before_handle(self, task, agent):
        if task is None:
            return
        task.assigned_to = agent.name
        if agent.role == "dev" and task.status == TaskStatus.PLANNING:
            task.transition(TaskStatus.IN_PROGRESS)
        elif agent.role == "architect" and task.status == TaskStatus.REVIEW_REQ:
            task.transition(TaskStatus.REVIEWING)
        elif agent.role == "pm" and task.status == TaskStatus.ACCEPTED:
            task.transition(TaskStatus.VALIDATING)
        self.state_store.save_task(self._task_chat_id(task.id), task)

    def _advance_task_after_reply(self, task, agent, reply: str):
        if task is None:
            return
        if agent.role == "dev" and any(
            keyword in reply for keyword in ("@architect", "Review", "review", "Code Review")
        ):
            if task.status == TaskStatus.PLANNING:
                task.transition(TaskStatus.IN_PROGRESS)
            task.transition(TaskStatus.REVIEW_REQ)
        if agent.role == "architect":
            if "LGTM" in reply.upper():
                if task.status == TaskStatus.REVIEW_REQ:
                    task.transition(TaskStatus.REVIEWING)
                task.transition(TaskStatus.ACCEPTED)
            elif any(keyword in reply for keyword in ("打回", "修复", "reject")):
                if task.status == TaskStatus.REVIEW_REQ:
                    task.transition(TaskStatus.REVIEWING)
                task.transition(TaskStatus.IN_PROGRESS)
        if agent.role == "pm" and any(keyword in reply for keyword in ("验收通过", "DONE", "完成")):
            if task.status == TaskStatus.ACCEPTED:
                task.transition(TaskStatus.VALIDATING)
            if task.status == TaskStatus.VALIDATING:
                task.transition(TaskStatus.DONE)
        self.state_store.save_task(self._task_chat_id(task.id), task)

    async def _ensure_dev_branch(self, chat_id: int, task, agent):
        if task is None or agent.role != "dev":
            return
        key = (chat_id, task.id)
        if key in self._task_branch_attempts:
            return
        self._task_branch_attempts.add(key)
        branch = self._build_task_branch_name(task)
        try:
            base_branch = await self.executor.git_current_branch()
            await self.executor.git_create_branch(branch)
            task.branch_name = branch
            self.branch_sessions.save(BranchSession(
                chat_id=chat_id,
                task_id=task.id,
                branch_name=branch,
                base_branch=base_branch,
            ))
            self.artifact_store.append(ArtifactRecord(
                task_id=task.id,
                run_id=self._task_run_ids.get((chat_id, task.id), ""),
                type="branch_session",
                source="git",
                summary=f"{branch} <- {base_branch}",
                content=f"branch={branch} base={base_branch}",
            ))
        except Exception:
            return

    def _build_task_branch_name(self, task) -> str:
        slug = []
        for char in task.description.lower():
            if char.isascii() and char.isalnum():
                slug.append(char)
            elif char in (" ", "-", "_", "/"):
                slug.append("-")
        normalized = "".join(slug).strip("-")
        while "--" in normalized:
            normalized = normalized.replace("--", "-")
        short = (normalized or "task")[:20].strip("-") or "task"
        return f"feat/{task.id.lower()}-{short}"

    def _persist_metric_snapshots(self):
        self._evaluation_counter += 1
        for agent_name, metrics in self.metrics.items():
            agent = self.registry.get_by_name(agent_name)
            if not agent or agent.role == "hr":
                continue
            score = self._derive_pressure_score(metrics)
            self.metrics_store.append_snapshot(
                self._evaluation_counter,
                agent_name,
                score,
                metrics,
            )

    def _new_run_id(self) -> str:
        return f"run-{uuid4().hex[:12]}"

    def pause_task(self, chat_id: int, task_id: str) -> bool:
        task = self.task_tracker.get(chat_id, task_id)
        if not task:
            return False
        # Task A3 完成: 支持在 hop 边界暂停任务链。
        self._paused_tasks.add((chat_id, task_id))
        return True

    async def resume_task(self, chat_id: int, task_id: str, send) -> bool:
        task = self.task_tracker.get(chat_id, task_id)
        run_id = self._task_run_ids.get((chat_id, task_id))
        if not task or not run_id:
            return False
        checkpoint = self.checkpoint_store.load_latest(run_id)
        if checkpoint is None:
            return False
        self._paused_tasks.discard((chat_id, task_id))
        self._histories[chat_id] = list(checkpoint.history)
        self._dev_retries[chat_id] = checkpoint.dev_retries
        if checkpoint.task_status:
            try:
                task.status = TaskStatus(checkpoint.task_status)
            except ValueError:
                pass
        agent = self.router.detect_first(checkpoint.current_message)
        if agent is None or agent.name == checkpoint.current_agent:
            agent = self.registry.get_by_name(checkpoint.current_agent)
        if agent is None:
            agent = self.router.detect_first(checkpoint.current_message) or self.router.default_agent()
        self._record_event(
            run_id,
            chat_id,
            "run_resumed",
            "system",
            task,
            {"task_id": task_id, "hop": checkpoint.hop},
        )
        await self.run_chain(
            checkpoint.current_message,
            chat_id,
            send,
            initial_agent=agent,
            run_id=run_id,
            task=task,
        )
        return True

    async def replay_task(self, chat_id: int, task_id: str, send) -> bool:
        task = self.task_tracker.get(chat_id, task_id)
        if not task:
            return False
        self._paused_tasks.discard((chat_id, task_id))
        agent = self.router.detect_first(task.description) or self.router.default_agent()
        if agent is None:
            return False
        await self.run_chain(
            task.description,
            chat_id,
            send,
            initial_agent=agent,
            task=task,
        )
        return True

    def _is_task_paused(self, chat_id: int, task_id: str) -> bool:
        return (chat_id, task_id) in self._paused_tasks

    def _record_event(
        self,
        run_id: str,
        chat_id: int,
        event_type: str,
        actor: str,
        task,
        payload: dict,
    ) -> None:
        event = RunEvent(
            run_id=run_id,
            chat_id=chat_id,
            task_id=task.id if task else "",
            type=event_type,
            actor=actor,
            payload=payload,
        )
        self.event_store.append(event)
        self.state_store.append_event(event)

    def _save_checkpoint(
        self,
        run_id: str,
        chat_id: int,
        task,
        hop: int,
        current_agent: str,
        current_message: str,
    ) -> None:
        checkpoint = RunCheckpoint(
            run_id=run_id,
            chat_id=chat_id,
            task_id=task.id if task else "",
            hop=hop,
            current_agent=current_agent,
            current_message=current_message,
            history=list(self._histories.get(chat_id, [])),
            dev_retries=self._dev_retries.get(chat_id, 0),
            task_status=task.status.value if task else "",
            metrics_summary=self.metrics.all_summaries(),
        )
        self.checkpoint_store.save(checkpoint)
        self.state_store.save_checkpoint(checkpoint)

    async def _maybe_sync_pr(self, chat_id: int, task, reply: str):
        if task is None:
            return
        if "@architect" not in reply and "Code Review" not in reply and "Review" not in reply:
            return
        branch_session = self.branch_sessions.get(chat_id, task.id)
        if branch_session is None:
            return
        draft = await self.pr_workflow.ensure_pr(task, branch_session, reply)
        self.branch_sessions.save(branch_session)
        self.artifact_store.append(ArtifactRecord(
            task_id=task.id,
            run_id=self._task_run_ids.get((chat_id, task.id), ""),
            type="pr_draft",
            source="git",
            summary=draft.title,
            content=draft.body,
        ))
        if draft.url:
            await self.github_sync.mirror_comment(
                task,
                "system",
                f"已创建 Draft PR: {draft.url}",
            )

    def _update_stuck_report(self, chat_id: int, task_id: str):
        events = self.event_store.read_all()
        history = self._histories.get(chat_id, [])
        report = self.stuck_detector.analyze(task_id, history, events)
        if report:
            self._stuck_reports[(chat_id, task_id)] = report
            self.artifact_store.append(ArtifactRecord(
                task_id=task_id,
                run_id=self._task_run_ids.get((chat_id, task_id), ""),
                type="stuck_report",
                source="system",
                summary=report.summary,
                content=",".join(report.labels),
            ))
        else:
            self._stuck_reports.pop((chat_id, task_id), None)

    def ingest_github_event(self, event_type: str, payload: dict):
        # Enterprise expansion: GitHub webhook -> task state / CI / PR sync.
        if event_type == "pull_request":
            pr = payload.get("pull_request", {})
            number = pr.get("number")
            action = payload.get("action", "")
            task = self._find_task_by_pr_number(number)
            if task is None:
                return
            task.github_pr_number = number or task.github_pr_number
            task.github_pr_url = pr.get("html_url", task.github_pr_url)
            if action in ("opened", "reopened", "ready_for_review", "review_requested"):
                task.status = TaskStatus.REVIEWING
            elif action in ("synchronize",):
                task.status = TaskStatus.REVIEW_REQ
            elif action in ("converted_to_draft",):
                task.status = TaskStatus.IN_PROGRESS
            elif action == "closed" and pr.get("merged"):
                task.status = TaskStatus.DONE
            elif action == "closed":
                task.status = TaskStatus.IN_PROGRESS
            self.artifact_store.append(ArtifactRecord(
                task_id=task.id,
                run_id=self._task_run_ids.get((self._task_chat_id(task.id), task.id), ""),
                type="github_pr_event",
                source="github",
                summary=f"PR #{number} {action}",
                content=json.dumps(payload, ensure_ascii=False)[:3000],
            ))
            self.state_store.save_task(self._task_chat_id(task.id), task)
            return

        if event_type == "pull_request_review":
            review = payload.get("review", {})
            pr = payload.get("pull_request", {})
            task = self._find_task_by_pr_number(pr.get("number"))
            if task is None:
                return
            state = (review.get("state") or "").lower()
            if state == "approved":
                task.status = TaskStatus.ACCEPTED
            elif state == "changes_requested":
                task.status = TaskStatus.IN_PROGRESS
            self.artifact_store.append(ArtifactRecord(
                task_id=task.id,
                run_id=self._task_run_ids.get((self._task_chat_id(task.id), task.id), ""),
                type="github_review_event",
                source="github",
                summary=f"review {state or 'submitted'}",
                content=json.dumps(payload, ensure_ascii=False)[:3000],
            ))
            self.state_store.save_task(self._task_chat_id(task.id), task)
            return

        if event_type == "pull_request_review_comment":
            pr = payload.get("pull_request", {})
            task = self._find_task_by_pr_number(pr.get("number"))
            if task is None:
                return
            self.artifact_store.append(ArtifactRecord(
                task_id=task.id,
                run_id=self._task_run_ids.get((self._task_chat_id(task.id), task.id), ""),
                type="github_review_comment",
                source="github",
                summary=payload.get("action", "comment"),
                content=json.dumps(payload, ensure_ascii=False)[:3000],
            ))
            return

        if event_type == "issue_comment":
            issue = payload.get("issue", {})
            task = self._find_task_by_issue_number(issue.get("number"))
            if task is None:
                return
            self.artifact_store.append(ArtifactRecord(
                task_id=task.id,
                run_id=self._task_run_ids.get((self._task_chat_id(task.id), task.id), ""),
                type="github_issue_comment",
                source="github",
                summary=payload.get("action", "comment"),
                content=json.dumps(payload, ensure_ascii=False)[:3000],
            ))
            return

        if event_type in ("check_suite", "check_run", "status"):
            pr_numbers = self._extract_pr_numbers(payload)
            summary = self._build_ci_summary_from_event(event_type, payload)
            for number in pr_numbers:
                task = self._find_task_by_pr_number(number)
                if task is None:
                    continue
                self._ci_overrides[task.id] = summary
                self.artifact_store.append(ArtifactRecord(
                    task_id=task.id,
                    run_id=self._task_run_ids.get((self._task_chat_id(task.id), task.id), ""),
                    type="ci_event",
                    source="github",
                    summary=summary.summary,
                    content=json.dumps(payload, ensure_ascii=False)[:3000],
                ))

    def _find_task_by_pr_number(self, pr_number: int | None):
        if not pr_number:
            return None
        for tasks in self.task_tracker._tasks.values():
            for task in tasks.values():
                if task.github_pr_number == pr_number:
                    return task
        return None

    def _find_task_by_issue_number(self, issue_number: int | None):
        if not issue_number:
            return None
        for tasks in self.task_tracker._tasks.values():
            for task in tasks.values():
                if task.github_issue_number == issue_number:
                    return task
        return None

    def _extract_pr_numbers(self, payload: dict) -> list[int]:
        numbers: list[int] = []
        if "pull_request" in payload and payload["pull_request"].get("number"):
            numbers.append(payload["pull_request"]["number"])
        for pr in payload.get("check_suite", {}).get("pull_requests", []):
            if pr.get("number"):
                numbers.append(pr["number"])
        if payload.get("check_run", {}).get("pull_requests"):
            for pr in payload["check_run"]["pull_requests"]:
                if pr.get("number"):
                    numbers.append(pr["number"])
        return numbers

    def _build_ci_summary_from_event(self, event_type: str, payload: dict) -> CIResult:
        status = "pending"
        summary = event_type
        if event_type == "check_suite":
            suite = payload.get("check_suite", {})
            status = suite.get("conclusion") or suite.get("status") or "pending"
            summary = f"check_suite: {status}"
        elif event_type == "check_run":
            run = payload.get("check_run", {})
            status = run.get("conclusion") or run.get("status") or "pending"
            summary = f"check_run {run.get('name', '')}: {status}"
        elif event_type == "status":
            status_obj = payload.get("state", "pending")
            status = status_obj
            summary = f"status: {status_obj}"
        normalized = "passed" if status in ("success", "completed", "neutral") else "failed" if status in ("failure", "timed_out", "cancelled") else "pending"
        return CIResult(status=normalized, summary=summary, checks=[payload])

    def _restore_tasks_from_state(self):
        for record in self.state_store.load_tasks():
            status = TaskStatus(record["status"])
            task = self.task_tracker.get(record["chat_id"], record["id"])
            if task is not None:
                continue
            restored = Task(
                id=record["id"],
                description=record["description"],
                status=status,
                assigned_to=record["assigned_to"],
                branch_name=record["branch_name"],
                github_issue_number=record["github_issue_number"],
                github_issue_url=record["github_issue_url"],
                github_pr_number=record["github_pr_number"],
                github_pr_url=record["github_pr_url"],
                slack_channel=record["slack_channel"],
                slack_message_ts=record["slack_message_ts"],
                slack_thread_ts=record["slack_thread_ts"],
                created_at=record["created_at"],
                updated_at=record["updated_at"],
                history=record["history"],
            )
            self.task_tracker.restore(record["chat_id"], restored)

    def _task_chat_id(self, task_id: str) -> int:
        for chat_id, tasks in self.task_tracker._tasks.items():
            if task_id in tasks:
                return chat_id
        return 0

    def trace_summary(self, task_id: str) -> str:
        from .trace.store import TraceStore
        return TraceStore(self.event_store).format_task_timeline(task_id)
