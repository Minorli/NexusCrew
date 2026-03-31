"""Core orchestrator — agent chain runner."""
import asyncio
import json
import re
import time
from datetime import date, datetime
from pathlib import Path
from uuid import uuid4
from .agents.base import AgentArtifacts
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
from .task_state import Task, TaskStatus, TaskTracker

MAX_CHAIN_HOPS = 10
MAX_DEV_RETRY  = 5
_CODE_BLOCK_RE = re.compile(r"```(?:bash|sh)?\n.*?\n```", re.DOTALL)


class Orchestrator:
    def __init__(self, registry: AgentRegistry, router: Router,
                 crew_memory: CrewMemory, shell_executor: ShellExecutor,
                 max_chain_hops: int = MAX_CHAIN_HOPS,
                 max_dev_retry: int = MAX_DEV_RETRY,
                 pressure_max_prompt_len: int = 500,
                 hr_auto_eval_daily_limit: int = 1,
                 agent_heartbeat_seconds: int = 20,
                 agent_max_silence_seconds: int = 120,
                 task_stage_sla_seconds: int = 600,
                 task_watchdog_interval_seconds: int = 60,
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
        self.hr_auto_eval_daily_limit = max(int(hr_auto_eval_daily_limit), 0)
        self.agent_heartbeat_seconds = max(int(agent_heartbeat_seconds), 1)
        self.agent_max_silence_seconds = max(int(agent_max_silence_seconds), self.agent_heartbeat_seconds)
        self.task_stage_sla_seconds = max(int(task_stage_sla_seconds), 30)
        self.task_watchdog_interval_seconds = max(int(task_watchdog_interval_seconds), 10)
        # per chat_id state
        self._histories: dict[int, list[dict]] = {}
        self._dev_retries: dict[int, int]       = {}
        self._evaluation_counter = 0
        self._hr_auto_eval_counts: dict[tuple[int, str], int] = {}
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
        self._task_watchdog_sent_at: dict[tuple[int, str], float] = {}
        self._proactive_sent_at: dict[tuple[int, str], float] = {}
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

    def checkpoint_active_continuations(self, chat_id: int) -> list[str]:
        saved: list[str] = []
        for task in self.task_tracker.list_active(chat_id):
            run_id = self._task_run_ids.get((chat_id, task.id), self._new_run_id())
            self._append_continuation_artifact(task, run_id)
            self._record_event(
                run_id,
                chat_id,
                "continuation_checkpointed",
                "system",
                task,
                {"task_id": task.id, "reason": "manual_reset"},
            )
            saved.append(task.id)
        return saved

    # ── main entry point ──────────────────────────────────────────────
    async def run_chain(
        self,
        message: str,
        chat_id: int,
        send,            # async callable(text: str, agent_name: str | None = None)
        initial_agent=None,
        run_id: str | None = None,
        task=None,
        route_trace: list[str] | None = None,
    ):
        """
        Run the agent chain starting from initial_agent (or router default).
        Each agent reply is scanned for @mentions to route the next hop.
        """
        routed_initial = self.router.detect_first_routed(message) if initial_agent is None else None
        agent = initial_agent
        if agent is None and routed_initial:
            if routed_initial["kind"] == "role_alias":
                agent = self._pick_best_agent_for_role(
                    routed_initial["role"],
                    chat_id,
                ) or routed_initial["agent"]
            else:
                agent = routed_initial["agent"]
        if agent is None:
            agent = self.router.default_agent()
        if not agent:
            await send("[NexusCrew] 没有可用的 Agent，请先使用 /crew 编组。")
            return
        route_trace = list(route_trace or [])
        if self._would_create_route_loop(route_trace, agent.name):
            await send(f"⚠️ 检测到 @{agent.name} 的循环路由，停止当前链路。")
            return
        route_trace.append(agent.name)

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
            history  = self._histories.get(chat_id, [])
            memory   = self.retriever.retrieve(agent.role, agent.name, task.id)
            self.executor.set_context(chat_id, task.id, run_id)
            metrics = self.metrics.get(agent.name)
            metrics.record_task_start()
            t0 = time.monotonic()
            reply, artifacts, timed_out = await self._run_agent_with_watchdog(
                run_id,
                chat_id,
                task,
                agent,
                message,
                history,
                memory,
                send,
            )
            reply, artifacts = await self._enforce_substantive_reply(
                task,
                agent,
                message,
                history,
                memory,
                reply,
                artifacts,
            )
            metrics.record_task_complete(
                int((time.monotonic() - t0) * 1000)
            )
            if timed_out:
                metrics.record_task_fail()
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

            public_reply = await self._build_public_reply(
                chat_id,
                task,
                agent,
                reply,
                artifacts,
            )
            mirror_reply = self._build_mirror_reply(
                task,
                agent,
                reply,
                artifacts,
                public_reply,
            )

            # Send reply + shell output to Telegram
            self._add_history(chat_id, agent.name, reply)
            await send(public_reply, agent_name=agent.name)
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
            if agent.role == "dev":
                review_packet = public_reply.split("\n", 1)[1] if "\n" in public_reply else public_reply
                self.artifact_store.append(ArtifactRecord(
                    task_id=task.id,
                    run_id=run_id,
                    type="review_packet",
                    source=agent.name,
                    summary=review_packet.splitlines()[0][:120] if review_packet else "dev handoff",
                    content=review_packet,
                ))
            self._append_gate_decision_artifact(task, run_id, agent, reply)
            await self.github_sync.mirror_comment(task, agent.name, mirror_reply)
            await self.slack_sync.mirror_comment(task, agent.name, mirror_reply)
            self.scoped_memory.append(f"task:{task.id}", agent.name, reply[:500], importance=2)
            if artifacts.shell_output:
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
                shell_summary = self._build_shell_summary(agent, artifacts.shell_output)
                if shell_summary:
                    await self.github_sync.mirror_comment(
                        task,
                        f"{agent.name}/run",
                        shell_summary,
                    )
                    await self.slack_sync.mirror_comment(
                        task,
                        f"{agent.name}/run",
                        shell_summary,
                    )
                    if self.executor.is_failure(artifacts.shell_output):
                        await send(
                            f"⚠️ [{agent.name}] 执行异常\n{shell_summary}",
                            agent_name=agent.name,
                        )
            self._save_checkpoint(
                run_id=run_id,
                chat_id=chat_id,
                task=task,
                hop=hop,
                current_agent=agent.name,
                current_message=reply,
            )

            routing_reply = public_reply if agent.role == "dev" else reply
            self._update_task_blocked_reason(task, agent, reply, routing_reply, artifacts)
            self._advance_task_after_reply(task, agent, reply, routing_reply=routing_reply)
            await self._maybe_sync_pr(chat_id, task, routing_reply)
            self._update_stuck_report(chat_id, task.id)

            # Detect next agent
            next_agents = []
            for routed in self.router.detect_all_routed(routing_reply):
                candidate = routed["agent"]
                if routed["kind"] == "role_alias":
                    candidate = self._pick_best_agent_for_role(
                        routed["role"],
                        chat_id,
                        exclude_name=agent.name,
                    ) or candidate
                if candidate.name != agent.name:
                    next_agents.append(candidate)
            if agent.role == "pm" and self._is_status_query(message):
                next_agents = []
            if not next_agents:
                break

            # Fan out when an agent explicitly hands work to multiple teammates.
            if len(next_agents) > 1:
                for next_agent in next_agents:
                    await self.run_chain(
                        reply,
                        chat_id,
                        send,
                        next_agent,
                        run_id=run_id,
                        task=task,
                        route_trace=route_trace,
                    )
                self._record_event(
                    run_id,
                    chat_id,
                    "run_completed",
                    "system",
                    task,
                    {"mode": "sequential_fanout", "targets": [a.name for a in next_agents]},
                )
                return

            next_agent = next_agents[0]
            if self._would_create_route_loop(route_trace, next_agent.name):
                await send(f"⚠️ 检测到 @{next_agent.name} 的循环路由，停止当前链路。")
                break
            route_trace.append(next_agent.name)
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
        self._append_continuation_artifact(task, run_id)
        self._save_checkpoint(
            run_id=run_id,
            chat_id=chat_id,
            task=task,
            hop=hop if "hop" in locals() else 0,
            current_agent=agent.name,
            current_message=message,
        )
        hr_agent = self.registry.get_by_role("hr")
        if hr_agent and agent.role != "hr" and self._consume_hr_auto_eval_budget(chat_id):
            # Task 3.3 完成: 任务链结束后异步触发 HR 评估。
            asyncio.create_task(self._hr_evaluate(hr_agent, chat_id, send))

    def _would_create_route_loop(self, route_trace: list[str], next_agent_name: str) -> bool:
        prospective = list(route_trace) + [next_agent_name]
        if prospective.count(next_agent_name) > 3:
            return True
        if len(prospective) >= 6:
            recent6 = prospective[-6:]
            if recent6[:3] == recent6[3:] and len(set(recent6[:3])) >= 2:
                return True
            if (
                len(set(recent6[::2])) == 1
                and len(set(recent6[1::2])) == 1
                and recent6[0] != recent6[1]
            ):
                return True
        return False

    def _pick_best_agent_for_role(
        self,
        role: str,
        chat_id: int,
        exclude_name: str = "",
    ):
        candidates = self.registry.list_by_role(role) if hasattr(self.registry, "list_by_role") else []
        if not candidates:
            return self.registry.get_by_role(role)
        waiting = set()
        inflight = set()
        load_by_agent = {
            row["name"]: row
            for row in self.agent_presence(
                chat_id,
                inflight_task_ids=inflight,
                waiting_task_ids=waiting,
            )
        }

        def score(agent):
            row = load_by_agent.get(agent.name, {})
            presence = row.get("presence", "idle")
            presence_score = {
                "idle": 0,
                "active": 1,
                "waiting": 2,
                "busy": 3,
                "blocked": 4,
                "stale": 5,
            }.get(presence, 9)
            queue = row.get("queue_size", 0)
            penalty = 100 if exclude_name and agent.name == exclude_name else 0
            return (penalty + presence_score, queue, agent.name)

        return sorted(candidates, key=score)[0]

    def _find_recent_dev(self, history: list[dict]):
        # Task 3.2 完成: 从历史中回溯最近一个 Dev，给 review 指标归因。
        for item in reversed(history):
            agent = self.registry.get_by_name(item.get("agent", ""))
            if agent and agent.role == "dev":
                return agent
        return None

    async def _enforce_substantive_reply(
        self,
        task,
        agent,
        message: str,
        history: list[dict],
        memory: str,
        reply: str,
        artifacts,
    ):
        if not self._should_enforce_substantive_reply(agent, message):
            return reply, artifacts
        if not self._is_low_signal_reply(agent, reply, artifacts):
            return reply, artifacts
        correction = self._build_substantive_retry_prompt(agent, message, reply)
        retry_reply, retry_artifacts = await agent.handle(
            correction,
            history + [{"agent": agent.name, "content": reply}],
            memory,
        )
        if self._is_low_signal_reply(agent, retry_reply, retry_artifacts):
            retry_reply = self._build_noncompliant_reply(agent, task)
        if getattr(artifacts, "memory_note", "") and not getattr(retry_artifacts, "memory_note", ""):
            retry_artifacts.memory_note = artifacts.memory_note
        return retry_reply, retry_artifacts

    def _should_enforce_substantive_reply(self, agent, message: str) -> bool:
        lowered = message.lower()
        if agent.role == "dev":
            keywords = (
                "修复", "实现", "处理", "测试", "pytest", "review", "代码", "模块",
                "功能", "bug", "提交", "pr", "架构评审",
            )
            return any(keyword in lowered for keyword in keywords)
        if agent.role == "architect":
            keywords = (
                "评审", "审查", "review", "风险", "架构", "结论", "模块", "lg tm",
                "lgtm", "缺陷", "打回",
            )
            return any(keyword in lowered for keyword in keywords)
        if agent.role == "qa":
            keywords = (
                "测试", "验收", "质量", "回归", "发布", "smoke", "go", "no-go",
                "风险", "验证", "覆盖", "用例", "检查",
            )
            return any(keyword in lowered for keyword in keywords)
        return False

    def _is_low_signal_reply(self, agent, reply: str, artifacts) -> bool:
        text = reply.strip()
        normalized = text.lower().strip("。.!！？ ")
        if agent.role == "dev":
            if getattr(artifacts, "shell_output", ""):
                return False
            if "@architect" in text or "Code Review" in text or "阻塞" in text:
                return False
            low_signal_fragments = (
                "收到", "我来处理", "当前最新进度", "状态如下", "可以，但这里更适合",
                "请把具体需求", "在，我可以直接处理开发任务", "流程", "工作流",
            )
            return len(text) < 400 and any(fragment in text for fragment in low_signal_fragments)
        if agent.role == "architect":
            if "LGTM" in text.upper():
                return False
            if "@" in text and any(fragment in text for fragment in ("打回", "缺陷", "风险", "需改进", "需重构", "修复")):
                return False
            progress_fragments = (
                "正在审计", "审计中", "读代码", "先读代码", "先看代码", "稍后", "随后",
                "先扫", "扫描", "结果随后", "报告随后",
            )
            if any(fragment in text for fragment in progress_fragments):
                return True
            low_signal_replies = (
                "ok", "收到", "在", "收到，先读代码再给结论", "收到，先看代码再给结论",
                "收到，先读代码", "收到，先读代码再给结论。",
            )
            if normalized in low_signal_replies or len(text) < 120:
                return True
            substantive_markers = ("LGTM", "打回", "缺陷", "风险", "需改进", "需重构", "结论")
            return not any(marker in text for marker in substantive_markers)
        if agent.role == "qa":
            if getattr(artifacts, "shell_output", ""):
                return False
            if "@" in text and any(fragment in text for fragment in ("Go", "No-Go", "NO-GO", "阻断", "修复", "回归")):
                return False
            progress_fragments = (
                "在测", "测试中", "稍后给结论", "稍后同步", "先看一下", "先跑一下",
                "正在验证", "结果随后", "回头给结论",
            )
            if any(fragment in text for fragment in progress_fragments):
                return True
            substantive_markers = ("Conditional Go", "Go", "No-Go", "NO-GO", "风险", "覆盖", "验证", "阻断", "结论")
            if any(marker in text for marker in substantive_markers):
                return False
            low_signal_replies = (
                "ok", "收到", "在", "收到，先测一下", "收到，稍后给结论",
                "收到，先验证一下", "收到，测试中。",
            )
            return normalized in low_signal_replies or len(text) < 120
        return False

    def _build_substantive_retry_prompt(self, agent, message: str, reply: str) -> str:
        if agent.role == "dev":
            return (
                f"{message}\n\n"
                f"你刚才的回复是：{reply}\n\n"
                "这不是可执行交付。不要汇报状态、计划或流程。"
                "请直接执行任务并给出 bash，或者只说明一个真实阻塞点。"
            )
        if agent.role == "architect":
            return (
                f"{message}\n\n"
                f"你刚才的回复是：{reply}\n\n"
                "这不是有效评审。请直接给出评审结论："
                "LGTM，或列出具体缺陷并 @具体负责人。不要只确认收到。"
            )
        if agent.role == "qa":
            return (
                f"{message}\n\n"
                f"你刚才的回复是：{reply}\n\n"
                "这不是有效测试结论。请直接给出 Go / No-Go、覆盖项、风险项，"
                "或者直接执行最小必要的验证命令并汇报结果。不要只汇报状态。"
            )
        return message

    async def _run_agent_with_watchdog(
        self,
        run_id: str,
        chat_id: int,
        task,
        agent,
        message: str,
        history: list[dict],
        memory: str,
        send,
    ):
        handle_task = asyncio.create_task(agent.handle(message, history, memory))
        elapsed = 0
        while True:
            done, _ = await asyncio.wait(
                {handle_task},
                timeout=self.agent_heartbeat_seconds,
            )
            if done:
                reply, artifacts = await handle_task
                return reply, artifacts, False

            elapsed += self.agent_heartbeat_seconds
            self._record_event(
                run_id,
                chat_id,
                "agent_heartbeat",
                "system",
                task,
                {"agent": agent.name, "elapsed": elapsed},
            )
            await send(
                f"⏳ @{agent.name} 仍在处理任务 {task.id}，已等待 {elapsed}s。",
            )
            if elapsed >= self.agent_max_silence_seconds:
                handle_task.cancel()
                try:
                    await handle_task
                except BaseException:
                    pass
                reply = self._build_agent_timeout_reply(agent, task)
                self._record_event(
                    run_id,
                    chat_id,
                    "agent_timeout",
                    "system",
                    task,
                    {"agent": agent.name, "elapsed": elapsed},
                )
                return reply, AgentArtifacts(), True

    def _build_agent_timeout_reply(self, agent, task) -> str:
        task_id = task.id if task else "unknown"
        if agent.role == "dev":
            arch = self.registry.get_by_role("architect")
            if arch:
                return f"⚠️ @{agent.name} 在任务 {task_id} 上响应超时。@{arch.name} 请接手排障。"
            return f"⚠️ @{agent.name} 在任务 {task_id} 上响应超时，请人工介入。"
        if agent.role == "architect":
            pm = self.router.default_agent()
            if pm:
                return f"⚠️ @{agent.name} 在任务 {task_id} 上评审超时。@{pm.name} 请调整计划或改派。"
            return f"⚠️ @{agent.name} 在任务 {task_id} 上评审超时，请人工介入。"
        if agent.role == "qa":
            pm = self.router.default_agent()
            if pm:
                return f"⚠️ @{agent.name} 在任务 {task_id} 上测试验证超时。@{pm.name} 请调整计划或改派。"
            return f"⚠️ @{agent.name} 在任务 {task_id} 上测试验证超时，请人工介入。"
        if agent.role == "pm":
            return f"⚠️ @{agent.name} 在任务 {task_id} 上编排超时，请人工介入。"
        return f"⚠️ @{agent.name} 在任务 {task_id} 上响应超时，请人工介入。"

    def _build_noncompliant_reply(self, agent, task) -> str:
        task_id = task.id if task else "unknown"
        if agent.role == "dev":
            arch = self.registry.get_by_role("architect")
            if arch:
                return f"⚠️ @{agent.name} 未给出可执行交付。@{arch.name} 请接手排障（task {task_id}）。"
            return f"⚠️ @{agent.name} 未给出可执行交付，请人工介入（task {task_id}）。"
        if agent.role == "architect":
            pm = self.router.default_agent()
            if pm:
                return f"⚠️ @{agent.name} 未给出有效评审结论。@{pm.name} 请调整计划或改派（task {task_id}）。"
            return f"⚠️ @{agent.name} 未给出有效评审结论，请人工介入（task {task_id}）。"
        if agent.role == "qa":
            pm = self.router.default_agent()
            if pm:
                return f"⚠️ @{agent.name} 未给出有效测试结论。@{pm.name} 请调整计划或改派（task {task_id}）。"
            return f"⚠️ @{agent.name} 未给出有效测试结论，请人工介入（task {task_id}）。"
        if agent.role == "pm":
            return f"⚠️ @{agent.name} 未给出有效编排结果，请人工介入（task {task_id}）。"
        return f"⚠️ @{agent.name} 未给出有效结果，请人工介入（task {task_id}）。"

    def _is_status_query(self, message: str) -> bool:
        lowered = message.lower()
        hints = (
            "进度", "状态", "在吗", "有没有更新", "然后呢", "看下", "看看", "很久没", "没有进度",
            "大家的状态", "成员状态", "目前状态",
        )
        return any(hint in lowered for hint in hints)

    async def watchdog_tick(
        self,
        send_factory,
        active_task_ids: set[str] | None = None,
        notify_chat: bool = False,
    ) -> list[str]:
        alerts: list[str] = []
        now = time.monotonic()
        active_task_ids = set(active_task_ids or set())
        for chat_id, tasks in self.task_tracker._tasks.items():
            stale_running: list[tuple[Task, float]] = []
            auto_closed: list[tuple[Task, float]] = []
            for task in tasks.values():
                if task.status in (TaskStatus.DONE, TaskStatus.FAILED):
                    continue
                age = (datetime.now() - task.last_activity_at()).total_seconds()
                if age < self.task_stage_sla_seconds:
                    continue
                if task.id not in active_task_ids:
                    task.status = TaskStatus.FAILED
                    task.updated_at = datetime.now().isoformat()
                    task.history.append(
                        f"auto_failed_by_watchdog at {task.updated_at} age={int(age)}s"
                    )
                    self.state_store.save_task(chat_id, task)
                    self._record_event(
                        self._task_run_ids.get((chat_id, task.id), self._new_run_id()),
                        chat_id,
                        "task_auto_failed",
                        "system",
                        task,
                        {"age_seconds": int(age), "assigned_to": task.assigned_to, "status": task.status.value},
                    )
                    auto_closed.append((task, age))
                    alerts.append(task.id)
                    continue
                key = (chat_id, task.id)
                if now - self._task_watchdog_sent_at.get(key, 0) < self.task_watchdog_interval_seconds:
                    continue
                self._task_watchdog_sent_at[key] = now
                stale_running.append((task, age))
                alerts.append(task.id)
            if stale_running or auto_closed:
                send = send_factory(chat_id)
                if notify_chat:
                    await send(self._build_task_watchdog_digest(stale_running, auto_closed))
                for task, age in stale_running:
                    self._record_event(
                        self._task_run_ids.get((chat_id, task.id), self._new_run_id()),
                        chat_id,
                        "task_stale",
                        "system",
                        task,
                        {"age_seconds": int(age), "assigned_to": task.assigned_to, "status": task.status.value},
                    )
                    task.history.append(f"watchdog_alert at {datetime.now().isoformat()} age={int(age)}s")
                    self.state_store.save_task(chat_id, task)
        return alerts

    async def proactive_tick(
        self,
        send_factory,
        active_task_ids: set[str] | None = None,
        waiting_task_ids: set[str] | None = None,
        notify_chat: bool = False,
    ) -> list[dict]:
        emitted: list[dict] = []
        active_task_ids = set(active_task_ids or set())
        waiting_task_ids = set(waiting_task_ids or set())
        for chat_id in self.task_tracker._tasks:
            recs = self.proactive_recommendations(
                chat_id,
                inflight_task_ids=active_task_ids,
                waiting_task_ids=waiting_task_ids,
            )
            if not recs:
                continue
            now = time.monotonic()
            deduped: list[dict] = []
            for rec in recs:
                signature = json.dumps(rec, ensure_ascii=False, sort_keys=True)
                key = (chat_id, signature)
                if now - self._proactive_sent_at.get(key, 0) < self.task_watchdog_interval_seconds:
                    continue
                self._proactive_sent_at[key] = now
                deduped.append(rec)
            if not deduped:
                continue
            for rec in deduped:
                self._record_event(
                    self._new_run_id(),
                    chat_id,
                    "proactive_recommendation",
                    "system",
                    None,
                    rec,
                )
            emitted.extend(deduped)
            if notify_chat:
                await send_factory(chat_id)(self._format_proactive_recommendations(deduped))
        return emitted

    def _build_task_watchdog_digest(
        self,
        stale_running: list[tuple[Task, float]],
        auto_closed: list[tuple[Task, float]],
    ) -> str:
        lines = ["⚠️ Task Watchdog"]
        if stale_running:
            lines.append("仍在运行但超时：")
            for task, age in stale_running[:3]:
                assignee = f"@{task.assigned_to}" if task.assigned_to else "@未分配"
                lines.append(
                    f"- {task.id} {assignee} `{task.status.value}` 静默 {int(age)}s"
                )
            if len(stale_running) > 3:
                lines.append(f"- 另外 {len(stale_running) - 3} 个活跃超时任务")
        if auto_closed:
            lines.append("已自动收口为失败：")
            for task, age in auto_closed[:3]:
                assignee = f"@{task.assigned_to}" if task.assigned_to else "@未分配"
                lines.append(
                    f"- {task.id} {assignee} 静默 {int(age)}s，无活跃后台 run"
                )
            if len(auto_closed) > 3:
                lines.append(f"- 另外 {len(auto_closed) - 3} 个历史卡死任务已归档")
        pm = self.router.default_agent()
        if pm:
            lines.append(f"@{pm.name} 请推进、改派或降级方案。")
        return "\n".join(lines)

    def _format_proactive_recommendations(self, recs: list[dict]) -> str:
        lines = ["🤖 Proactive Recommendations", ""]
        for item in recs[:6]:
            if item["type"] == "family_escalation":
                lines.append(f"- family {item['family_id']}: {item['state']} / {item['reason']}")
            elif item["type"] == "family_completion":
                lines.append(f"- family {item['family_id']}: ready / {item['reason']}")
            elif item["type"] == "family_ready_to_close":
                lines.append(f"- family {item['family_id']}: closeout / {item['reason']}")
            elif item["type"] == "agent_rebalance":
                lines.append(f"- @{item['agent']}: {item['reason']} ({item['count']})")
            elif item["type"] == "idle_capacity":
                lines.append(
                    f"- @{item['source_agent']}: {item['reason']} -> available {', '.join('@' + name for name in item['idle_agents'])}"
                )
            elif item["type"] == "continuation_next_action":
                lines.append(
                    f"- family {item['family_id']}: next -> {', '.join(item['actions'])}"
                )
            elif item["type"] == "session_completion":
                lines.append(f"- session {item['session_key']}: {item['state']} / {item['reason']}")
            elif item["type"] == "session_ready_to_close":
                lines.append(f"- session {item['session_key']}: closeout / {item['reason']}")
            elif item["type"] == "lane_congestion":
                lines.append(f"- lane {item['lane_key']}: congested / waiting={item['waiting']} / {item['reason']}")
            elif item["type"] == "lane_backlog":
                lines.append(f"- lane {item['lane_key']}: backlog / waiting={item['waiting']} / {item['reason']}")
            elif item["type"] == "lane_human_decision":
                lines.append(f"- lane {item['lane_key']}: human / waiting={item['waiting']} / {item['reason']}")
            elif item["type"] == "lane_unassigned":
                lines.append(f"- lane {item['lane_key']}: unassigned / waiting={item['waiting']} / {item['reason']}")
            elif item["type"] == "lane_multi_owner":
                lines.append(f"- lane {item['lane_key']}: multi-owner / waiting={item['waiting']} / {item['reason']}")
            elif item["type"] == "lane_ready_to_close":
                lines.append(f"- lane {item['lane_key']}: closeout / {item['reason']}")
            elif item["type"] == "lane_partial_completion":
                lines.append(f"- lane {item['lane_key']}: partial / {item['reason']}")
            elif item["type"] == "lane_review_queue":
                lines.append(f"- lane {item['lane_key']}: review / waiting={item['waiting']} / {item['reason']}")
            elif item["type"] == "lane_quality_queue":
                lines.append(f"- lane {item['lane_key']}: qa / waiting={item['waiting']} / {item['reason']}")
        return "\n".join(lines)

    async def _build_public_reply(self, chat_id: int, task, agent, reply: str, artifacts) -> str:
        if agent.role != "dev":
            return f"**[{agent.name}]**\n{self._strip_code_blocks(reply)}"

        changed_files = await self._task_scoped_changed_files(chat_id, task)
        diff_summary = await self._task_scoped_diff_summary(chat_id, task, changed_files)
        lines = [f"**[{agent.name}]**"]
        summary = self._summarize_dev_reply(reply)
        review_requested = "@architect" in reply or "Code Review 请求" in reply
        if review_requested and not changed_files:
            summary = "交付摘要：仅完成仓库勘察，尚未形成当前任务代码改动。"
        if summary:
            if summary.startswith("交付摘要"):
                lines.append(summary)
            else:
                lines.append(f"交付摘要：{summary}")
        if changed_files:
            preview = ", ".join(changed_files[:5])
            more = f" (+{len(changed_files) - 5})" if len(changed_files) > 5 else ""
            lines.append(f"Files: {preview}{more}")
        elif review_requested:
            lines.append("Files: (no task-scoped changes yet)")
        if diff_summary:
            lines.append(f"Diff: {diff_summary}")
        if task and getattr(task, "branch_name", ""):
            lines.append(f"Branch: {task.branch_name}")
        shell_summary = self._build_shell_summary(agent, getattr(artifacts, "shell_output", ""))
        shell_failed = self.executor.is_failure(getattr(artifacts, "shell_output", ""))
        if shell_summary:
            lines.append(f"Validation: {shell_summary}")
        if review_requested and changed_files and not shell_failed:
            lines.append("Next: @architect review")
        elif review_requested:
            if shell_failed:
                lines.append("Next: fix failing validation")
            else:
                lines.append("Next: continue implementation")
        elif "阻塞" in reply or "求助" in reply:
            lines.append("Next: waiting for unblock")
        elif not changed_files and not shell_summary:
            lines.append("已接单，正在推进。")
        return "\n".join(lines)

    def _build_mirror_reply(self, task, agent, reply: str, artifacts, public_reply: str) -> str:
        if agent.role != "dev":
            return self._strip_code_blocks(reply)
        body = public_reply.split("\n", 1)[1] if "\n" in public_reply else public_reply
        if task and getattr(task, "branch_name", ""):
            body += f"\nBranch: `{task.branch_name}`"
        if task and getattr(task, "github_pr_url", ""):
            body += f"\nPR: {task.github_pr_url}"
        return body

    def _strip_code_blocks(self, text: str) -> str:
        stripped = _CODE_BLOCK_RE.sub("\n[code omitted in Telegram; see artifacts / GitHub]\n", text)
        stripped = re.sub(r"\n{3,}", "\n\n", stripped).strip()
        return stripped

    def _summarize_dev_reply(self, reply: str) -> str:
        stripped = self._strip_code_blocks(reply)
        lines = [line.strip() for line in stripped.splitlines() if line.strip()]
        lines = [line for line in lines if line != "[code omitted in Telegram; see artifacts / GitHub]"]
        for line in lines:
            if line.startswith("交付摘要"):
                return line[:220]
        for line in lines:
            if "Code Review 请求" in line or "阻塞" in line or "求助" in line:
                return line
        if not lines:
            return "已执行开发步骤。"
        summary = lines[0]
        return summary[:220]

    async def _task_scoped_changed_files(self, chat_id: int, task) -> list[str]:
        current = await self.executor.git_changed_files(limit=64)
        if task is None:
            return current
        session = self.branch_sessions.get(chat_id, task.id)
        baseline = getattr(session, "baseline_dirty_files", {}) if session else {}
        if not baseline:
            return current
        current_hashes = await self.executor.file_hashes(current)
        scoped: list[str] = []
        for path in current:
            baseline_hash = baseline.get(path)
            current_hash = current_hashes.get(path, "__missing__")
            if baseline_hash is None or baseline_hash != current_hash:
                scoped.append(path)
        return scoped

    async def _task_scoped_diff_summary(self, chat_id: int, task, files: list[str]) -> str:
        if not files:
            return ""
        if hasattr(self.executor, "git_diff_summary_for_files"):
            return await self.executor.git_diff_summary_for_files(files)
        if hasattr(self.executor, "git_diff_summary"):
            return await self.executor.git_diff_summary()
        return ""

    def _build_shell_summary(self, agent, shell_output: str) -> str:
        if not shell_output:
            return ""
        lines = [line.strip() for line in shell_output.splitlines() if line.strip()]
        pytest_line = next((line for line in lines if " passed" in line or " failed" in line), "")
        if pytest_line:
            return pytest_line[:220]
        if "[approval required:" in shell_output:
            return "需要审批后才能继续执行。"
        if self.executor.is_failure(shell_output):
            stderr_lines = []
            in_stderr = False
            for line in lines:
                if line.startswith("[stderr]"):
                    in_stderr = True
                    continue
                if in_stderr:
                    stderr_lines.append(line)
            search_space = stderr_lines or lines
            err_line = next(
                (
                    line for line in search_space
                    if any(
                        keyword in line.lower()
                        for keyword in ("error collecting", "traceback", "fatal", "timeout", "permission denied", "command not found", "no such file")
                    )
                ),
                next(
                    (line for line in search_space if line.startswith("E ") or line.startswith("E   ")),
                    search_space[-1] if search_space else "执行失败",
                ),
            )
            return err_line[:220]
        command_count = sum(1 for line in lines if line.startswith("$ "))
        if command_count:
            return f"执行了 {command_count} 个步骤，日志已归档。"
        return "执行完成，日志已归档。"

    async def _hr_evaluate(self, hr_agent, chat_id: int, send):
        try:
            summary = self.metrics.all_summaries()
            history = self._histories.get(chat_id, [])
            memory = self.crew_memory.read()
            prompt = (
                "请评估以下任务链路中各 Agent 的表现：\n\n"
                f"【团队指标】\n{summary}\n\n"
                f"【异常信号】\n{self._build_laziness_summary()}\n\n"
                "请使用 3.25/3.5/3.75 评分体系，但只输出简短绩效摘要："
                "1. 总评；2. 关键风险；3. 下一步动作。"
                "不要表格，不要长篇背景，最多 6 行。"
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

    def _consume_hr_auto_eval_budget(self, chat_id: int) -> bool:
        if self.hr_auto_eval_daily_limit <= 0:
            return False
        today = date.today().isoformat()
        key = (chat_id, today)
        used = self._hr_auto_eval_counts.get(key, 0)
        if used >= self.hr_auto_eval_daily_limit:
            return False
        self._hr_auto_eval_counts[key] = used + 1
        return True

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

    def format_status(
        self,
        chat_id: int,
        inflight_task_ids: set[str] | None = None,
        waiting_task_ids: set[str] | None = None,
    ) -> str:
        return self.task_tracker.format_status(
            chat_id,
            inflight_task_ids=inflight_task_ids,
            waiting_task_ids=waiting_task_ids,
            task_stage_sla_seconds=self.task_stage_sla_seconds,
        )

    def agent_presence(
        self,
        chat_id: int,
        inflight_task_ids: set[str] | None = None,
        waiting_task_ids: set[str] | None = None,
    ) -> list[dict]:
        inflight_task_ids = set(inflight_task_ids or set())
        waiting_task_ids = set(waiting_task_ids or set())
        tasks_by_agent: dict[str, list[Task]] = {}
        for task in self.task_tracker.list_active(chat_id):
            if task.assigned_to:
                tasks_by_agent.setdefault(task.assigned_to, []).append(task)

        agents = []
        for item in self.registry.list_all():
            name = item["name"]
            assigned = tasks_by_agent.get(name, [])
            presence = "idle"
            current_task_id = ""
            states: list[str] = []
            if assigned:
                current_task_id = assigned[-1].id
                states = [
                    self.task_tracker._runtime_state_label(
                        task,
                        inflight_task_ids=inflight_task_ids,
                        waiting_task_ids=waiting_task_ids,
                        task_stage_sla_seconds=self.task_stage_sla_seconds,
                    )
                    for task in assigned
                ]
                if "blocked" in states:
                    presence = "blocked"
                elif "inflight" in states:
                    presence = "busy"
                elif "waiting" in states:
                    presence = "waiting"
                elif "stale" in states:
                    presence = "stale"
                else:
                    presence = "active"
            agents.append(
                {
                    **item,
                    "presence": presence,
                    "queue_size": len(assigned),
                    "family_count": len({getattr(task, "family_id", task.id) or task.id for task in assigned}),
                    "current_task_id": current_task_id,
                    "blocked_count": states.count("blocked"),
                    "inflight_count": states.count("inflight"),
                    "waiting_count": states.count("waiting"),
                    "stale_count": states.count("stale"),
                    "load": "idle" if not assigned else "heavy" if len(assigned) >= 3 else "active",
                }
            )
        return agents

    def agent_queue_summaries(
        self,
        chat_id: int,
        inflight_task_ids: set[str] | None = None,
        waiting_task_ids: set[str] | None = None,
    ) -> list[dict]:
        inflight_task_ids = set(inflight_task_ids or set())
        waiting_task_ids = set(waiting_task_ids or set())
        rows = []
        for item in self.registry.list_all():
            queue = self.task_tracker.agent_queue(
                chat_id,
                item["name"],
                inflight_task_ids=inflight_task_ids,
                waiting_task_ids=waiting_task_ids,
                task_stage_sla_seconds=self.task_stage_sla_seconds,
            )
            enriched = []
            for entry in queue:
                task = self.task_tracker.get(chat_id, entry["task_id"])
                enriched.append(
                    {
                        **entry,
                        "session_key": getattr(task, "session_key", "") if task else "",
                        "next_action": self._latest_continuation_next_action_for_chat(entry["task_id"], chat_id),
                    }
                )
            rows.append({"agent": item["name"], "queue": enriched})
        return rows

    def _family_rollups_with_actions(
        self,
        chat_id: int,
        inflight_task_ids: set[str] | None = None,
        waiting_task_ids: set[str] | None = None,
    ) -> list[dict]:
        rollups = self.task_tracker.family_rollups(
            chat_id,
            inflight_task_ids=inflight_task_ids,
            waiting_task_ids=waiting_task_ids,
            task_stage_sla_seconds=self.task_stage_sla_seconds,
        )
        for item in rollups:
            item["next_actions"] = sorted(
                {
                    self._latest_continuation_next_action_for_chat(task.id, chat_id)
                    for task in item["members"]
                    if self._latest_continuation_next_action_for_chat(task.id, chat_id)
                }
            )
        return rollups

    def _session_rollups_with_actions(
        self,
        chat_id: int,
        inflight_task_ids: set[str] | None = None,
        waiting_task_ids: set[str] | None = None,
    ) -> list[dict]:
        rollups = self.task_tracker.session_rollups(
            chat_id,
            inflight_task_ids=inflight_task_ids,
            waiting_task_ids=waiting_task_ids,
            task_stage_sla_seconds=self.task_stage_sla_seconds,
        )
        for item in rollups:
            item["next_actions"] = sorted(
                {
                    self._latest_continuation_next_action_for_chat(task.id, chat_id)
                    for task in item["members"]
                    if self._latest_continuation_next_action_for_chat(task.id, chat_id)
                }
            )
        return rollups

    def lane_runtime_summaries(
        self,
        chat_id: int,
        lane_summaries: list[dict],
        inflight_task_ids: set[str] | None = None,
        waiting_task_ids: set[str] | None = None,
    ) -> list[dict]:
        inflight_task_ids = set(inflight_task_ids or set())
        waiting_task_ids = set(waiting_task_ids or set())
        rows: list[dict] = []
        for lane in lane_summaries:
            if chat_id and lane.get("chat_id") not in (0, chat_id):
                continue
            jobs = list(lane.get("jobs", []))
            members = self.task_tracker.session_members(chat_id, lane.get("lane_key", ""), include_done=True) if lane.get("lane_key") else []
            head = next((job for job in jobs if job.get("status") in {"pending", "running"}), None)
            if head is None:
                head = next((job for job in jobs if job.get("status") == "waiting"), None)
            head_task_id = head.get("task_id", "") if head else ""
            head_task = self.task_tracker.get(chat_id, head_task_id) if head_task_id else None
            next_action = self._latest_continuation_next_action_for_chat(head_task_id, chat_id) if head_task_id else ""
            blocked_reason = getattr(head_task, "blocked_reason", "") if head_task else ""
            blocked_reasons = sorted({getattr(task, "blocked_reason", "") for task in members if getattr(task, "blocked_reason", "")})
            active_agents = sorted({task.assigned_to for task in members if getattr(task, "assigned_to", "")})
            next_actions = sorted(
                {
                    self._latest_continuation_next_action_for_chat(task.id, chat_id)
                    for task in members
                    if self._latest_continuation_next_action_for_chat(task.id, chat_id)
                }
            )
            family_ids = sorted({getattr(task, "family_id", "") or task.id for task in members})
            runtime_state = (
                self.task_tracker._runtime_state_label(
                    head_task,
                    inflight_task_ids=inflight_task_ids,
                    waiting_task_ids=waiting_task_ids,
                    task_stage_sla_seconds=self.task_stage_sla_seconds,
                )
                if head_task is not None
                else ""
            )
            ready_to_close = self.task_tracker.session_ready_to_close(chat_id, lane["lane_key"]) if lane.get("lane_key") else False
            completion_state = self.task_tracker.session_completion_state(chat_id, lane["lane_key"]) if lane.get("lane_key") else "unknown"
            state = lane.get("state", "")
            if blocked_reason and lane.get("waiting", 0) > 0:
                state = "blocked"
            owner = getattr(head_task, "assigned_to", "") if head_task is not None else ""
            head_gate = self._latest_gate_summary_for_chat(head_task_id, chat_id) if head_task_id else ""
            recent_route = self._latest_route_summary_for_chat(head_task_id, chat_id) if head_task_id else ""
            rows.append(
                {
                    **lane,
                    "state": state,
                    "head_task_id": head_task_id,
                    "head_runtime_state": runtime_state,
                    "head_blocked_reason": blocked_reason,
                    "next_action": next_action,
                    "next_actions": next_actions,
                    "completion_state": completion_state,
                    "blocked_reasons": blocked_reasons,
                    "active_agents": active_agents,
                    "family_ids": family_ids,
                    "member_count": len(members),
                    "owner": owner,
                    "head_gate": head_gate,
                    "recent_route": recent_route,
                    "ready_to_close": ready_to_close,
                }
            )
        return rows

    def lane_summary(
        self,
        chat_id: int,
        lane_key: str,
        lane_summaries: list[dict] | None = None,
        inflight_task_ids: set[str] | None = None,
        waiting_task_ids: set[str] | None = None,
    ) -> str:
        rows = self.lane_runtime_summaries(
            chat_id,
            lane_summaries or [],
            inflight_task_ids=inflight_task_ids,
            waiting_task_ids=waiting_task_ids,
        )
        lane = next((item for item in rows if item.get("lane_key") == lane_key), None)
        if lane is None:
            return f"未找到 lane: {lane_key}"
        lines = [f"Lane: {lane_key}", ""]
        lines.append(f"State: {lane.get('state', 'active')}")
        lines.append(f"Completion: {lane.get('completion_state', 'unknown')}")
        lines.append(f"Inflight: {lane.get('inflight', 0)}")
        lines.append(f"Waiting: {lane.get('waiting', 0)}")
        lines.append(f"Members: {lane.get('member_count', 0)}")
        lines.append(f"Owner: @{lane.get('owner', '')}" if lane.get("owner") else "Owner: (none)")
        lines.append(f"Families: {', '.join(lane.get('family_ids', [])) or '(none)'}")
        lines.append(f"Head Job: {lane.get('head_job_id', '(none)') or '(none)'}")
        lines.append(f"Head Task: {lane.get('head_task_id', '(none)') or '(none)'}")
        lines.append(f"Head Gate: {lane.get('head_gate', '(none)') or '(none)'}")
        lines.append(f"Recent Route: {lane.get('recent_route', '(none)') or '(none)'}")
        lines.append(f"Agents: {', '.join('@' + name for name in lane.get('active_agents', [])) or '(none)'}")
        lines.append(f"Blocked: {lane.get('head_blocked_reason', '(none)') or '(none)'}")
        lines.append(f"Blocked Reasons: {', '.join(lane.get('blocked_reasons', [])) or '(none)'}")
        lines.append(f"Next Action: {lane.get('next_action', '(none)') or '(none)'}")
        lines.append(f"Next Actions: {', '.join(lane.get('next_actions', [])) or '(none)'}")
        lines.append(f"Ready To Close: {'yes' if lane.get('ready_to_close') else 'no'}")
        lines.append("")
        for job in lane.get("jobs", [])[:8]:
            lines.append(
                f"- {job['id']} status={job['status']} task={job.get('task_id', '(none)') or '(none)'} label={job.get('label', '')}"
            )
        lines.extend(["", self.lane_trace_summary(chat_id, lane_key, lane_summaries=lane_summaries)])
        return "\n".join(lines)

    def lane_trace_summary(
        self,
        chat_id: int,
        lane_key: str,
        lane_summaries: list[dict] | None = None,
    ) -> str:
        rows = lane_summaries or []
        lane = next((item for item in rows if item.get("lane_key") == lane_key and item.get("chat_id") in (0, chat_id)), None)
        if lane is None:
            return "(无 lane trace)"
        from .trace.store import TraceStore
        task_ids = [task_id for task_id in lane.get("task_ids", []) if task_id]
        return TraceStore(self.event_store).format_lane_timeline(lane_key, chat_id, task_ids)

    def proactive_recommendations(
        self,
        chat_id: int,
        inflight_task_ids: set[str] | None = None,
        waiting_task_ids: set[str] | None = None,
        lane_summaries: list[dict] | None = None,
    ) -> list[dict]:
        inflight_task_ids = set(inflight_task_ids or set())
        waiting_task_ids = set(waiting_task_ids or set())
        recs: list[dict] = []
        for family in self._family_rollups_with_actions(
            chat_id,
            inflight_task_ids=inflight_task_ids,
            waiting_task_ids=waiting_task_ids,
        ):
            reason = self._family_escalation_reason(family)
            if reason:
                recs.append(
                    {
                        "type": "family_escalation",
                        "family_id": family["family_id"],
                        "state": family["state"],
                        "reason": reason,
                    }
                )
            if family.get("completion_state") == "partial" and family["state"] not in {"blocked", "stale"}:
                recs.append(
                    {
                        "type": "family_completion",
                        "family_id": family["family_id"],
                        "state": family["state"],
                        "reason": "partial_family_completion",
                    }
                )
            if family.get("ready_to_close"):
                recs.append(
                    {
                        "type": "family_ready_to_close",
                        "family_id": family["family_id"],
                        "state": family["state"],
                        "reason": "gate_and_acceptance_closeout",
                    }
                )
            next_actions = family.get("next_actions", [])
            if next_actions:
                recs.append(
                    {
                        "type": "continuation_next_action",
                        "family_id": family["family_id"],
                        "actions": next_actions[:3],
                    }
                )
        idle_agents = []
        for row in self.agent_queue_summaries(
            chat_id,
            inflight_task_ids=inflight_task_ids,
            waiting_task_ids=waiting_task_ids,
        ):
            blocked = [item for item in row["queue"] if item["runtime_state"] == "blocked"]
            if len(blocked) >= 2:
                recs.append(
                    {
                        "type": "agent_rebalance",
                        "agent": row["agent"],
                        "reason": "multiple_blocked_tasks",
                        "count": len(blocked),
                    }
                )
            if not row["queue"]:
                idle_agents.append(row["agent"])
        if idle_agents:
            for row in self.agent_queue_summaries(
                chat_id,
                inflight_task_ids=inflight_task_ids,
                waiting_task_ids=waiting_task_ids,
            ):
                if len(row["queue"]) >= 2:
                    recs.append(
                        {
                            "type": "idle_capacity",
                            "source_agent": row["agent"],
                            "idle_agents": idle_agents[:3],
                            "reason": "rebalance_candidate",
                        }
                    )
                    break
        for session in self._session_rollups_with_actions(
            chat_id,
            inflight_task_ids=inflight_task_ids,
            waiting_task_ids=waiting_task_ids,
        ):
            if session.get("completion_state") == "partial":
                recs.append(
                    {
                        "type": "session_completion",
                        "session_key": session["session_key"],
                        "state": session["state"],
                        "reason": "partial_session_completion",
                    }
                )
            if session.get("ready_to_close"):
                recs.append(
                    {
                        "type": "session_ready_to_close",
                        "session_key": session["session_key"],
                        "state": session["state"],
                        "reason": "session_closeout_ready",
                    }
                )
        for lane in self.lane_runtime_summaries(
            chat_id,
            lane_summaries or [],
            inflight_task_ids=inflight_task_ids,
            waiting_task_ids=waiting_task_ids,
        ):
            if lane.get("inflight", 0) > 0 and lane.get("waiting", 0) > 0:
                recs.append(
                    {
                        "type": "lane_congestion",
                        "lane_key": lane["lane_key"],
                        "waiting": lane.get("waiting", 0),
                        "reason": "serialized_session_backlog",
                    }
                )
            elif lane.get("waiting", 0) >= 2:
                recs.append(
                    {
                        "type": "lane_backlog",
                        "lane_key": lane["lane_key"],
                        "waiting": lane.get("waiting", 0),
                        "reason": "queued_session_work",
                    }
                )
            if lane.get("completion_state") == "partial" and not lane.get("ready_to_close"):
                recs.append(
                    {
                        "type": "lane_partial_completion",
                        "lane_key": lane["lane_key"],
                        "reason": "partial_lane_completion",
                    }
                )
            if lane.get("head_blocked_reason") == "human_input_required":
                recs.append(
                    {
                        "type": "lane_human_decision",
                        "lane_key": lane["lane_key"],
                        "waiting": lane.get("waiting", 0),
                        "reason": "head_task_needs_human_decision",
                    }
                )
            if not lane.get("owner") and lane.get("waiting", 0) > 0:
                recs.append(
                    {
                        "type": "lane_unassigned",
                        "lane_key": lane["lane_key"],
                        "waiting": lane.get("waiting", 0),
                        "reason": "head_task_has_no_owner",
                    }
                )
            if len(lane.get("active_agents", [])) > 1 and lane.get("waiting", 0) > 0:
                recs.append(
                    {
                        "type": "lane_multi_owner",
                        "lane_key": lane["lane_key"],
                        "waiting": lane.get("waiting", 0),
                        "reason": "multi_owner_lane_backlog",
                    }
                )
            if lane.get("ready_to_close"):
                recs.append(
                    {
                        "type": "lane_ready_to_close",
                        "lane_key": lane["lane_key"],
                        "reason": "session_closeout_ready",
                    }
                )
            if lane.get("next_action") == "architect review" and lane.get("waiting", 0) > 0:
                recs.append(
                    {
                        "type": "lane_review_queue",
                        "lane_key": lane["lane_key"],
                        "waiting": lane.get("waiting", 0),
                        "reason": "review_step_queued",
                    }
                )
            if lane.get("next_action") == "qa quality gate" and lane.get("waiting", 0) > 0:
                recs.append(
                    {
                        "type": "lane_quality_queue",
                        "lane_key": lane["lane_key"],
                        "waiting": lane.get("waiting", 0),
                        "reason": "qa_step_queued",
                    }
                )
        return recs

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
            f"Session: {getattr(task, 'session_key', '') or '(none)'}",
            f"状态: {task.status.value}",
            f"负责人: @{task.assigned_to or '未分配'}",
            f"Family: {getattr(task, 'family_id', '') or task.id}",
            f"Parent: {getattr(task, 'parent_task_id', '') or '(none)'}",
            f"阻塞: {getattr(task, 'blocked_reason', '') or '(无)'}",
            f"最近路由: {self._latest_route_summary_for_chat(task.id, chat_id)}",
            f"最近 Gate: {self._latest_gate_summary_for_chat(task.id, chat_id)}",
            f"续接摘要: {self._latest_continuation_summary_for_chat(task.id, chat_id)}",
            f"分支: {task.branch_name or '(未创建)'}",
            f"GitHub Issue: {task.github_issue_url or '(未同步)'}",
            f"GitHub PR: {task.github_pr_url or '(未创建)'}",
            f"Slack Thread: {task.slack_channel + ' / ' + task.slack_thread_ts if task.slack_thread_ts else '(未同步)'}",
            f"CI: {ci_result.summary}",
            f"Merge Gate: {merge_gate.summary}",
            "",
            self.trace_summary(task_id, chat_id=chat_id),
            "",
            self.artifact_store.format_for_task(task_id, chat_id=chat_id),
        ]
        report = self._stuck_reports.get((chat_id, task_id))
        if report:
            parts.extend(["", f"Stuck Detector: {report.summary}"])
        return "\n".join(parts)

    def doctor_report(
        self,
        chat_id: int,
        inflight_task_ids: set[str] | None = None,
        waiting_task_ids: set[str] | None = None,
        lane_summaries: list[dict] | None = None,
    ) -> str:
        agent_names = [item["name"] for item in self.registry.list_all()]
        pending = self.executor.list_pending_approvals()
        blocked_tasks = [
            task
            for task in self.task_tracker.list_active(chat_id)
            if getattr(task, "blocked_reason", "")
        ]
        lines = [
            "🩺 NexusCrew Doctor",
            "",
            self.task_tracker.format_status(
                chat_id,
                inflight_task_ids=inflight_task_ids or set(),
                waiting_task_ids=waiting_task_ids or set(),
                task_stage_sla_seconds=self.task_stage_sla_seconds,
            ),
            "",
            f"待审批动作: {len(pending)}",
            build_trend_report(self.metrics_store, agent_names),
            "",
            recommend_staffing(self.metrics_store, agent_names),
        ]
        presence_rows = self.agent_presence(
            chat_id,
            inflight_task_ids=inflight_task_ids,
            waiting_task_ids=waiting_task_ids,
        )
        if presence_rows:
            lines.extend(["", "Agent Presence:"])
            for row in presence_rows:
                current = f" / {row['current_task_id']}" if row["current_task_id"] else ""
                lines.append(
                    f"  @{row['name']}: {row['presence']} / load={row['load']} / queue={row['queue_size']} / families={row['family_count']} / blocked={row['blocked_count']} / inflight={row['inflight_count']} / waiting={row['waiting_count']}{current}"
                )
        if blocked_tasks:
            lines.extend(["", "Blocked Tasks:"])
            for task in blocked_tasks[:5]:
                lines.append(
                    f"  {task.id}: {task.blocked_reason} / {self._latest_route_summary_for_chat(task.id, chat_id)}"
                )
        family_rollups = self._family_rollups_with_actions(
            chat_id,
            inflight_task_ids=inflight_task_ids,
            waiting_task_ids=waiting_task_ids,
        )
        if family_rollups:
            lines.extend(["", "🧬 Task Families:"])
            for item in family_rollups[:6]:
                members = ", ".join(task.id for task in item["members"])
                next_actions = ", ".join(item.get("next_actions", [])[:2]) or "(none)"
                blocked = ", ".join(item.get("blocked_reasons", [])) or "(none)"
                lines.append(
                    f"  {item['family_id']}: {item['state']} / {item['completion_state']} / blocked={blocked} / next={next_actions} / {members}"
                )
        session_rollups = self._session_rollups_with_actions(
            chat_id,
            inflight_task_ids=inflight_task_ids,
            waiting_task_ids=waiting_task_ids,
        )
        if session_rollups:
            lines.extend(["", "🧩 Sessions:"])
            for item in session_rollups[:6]:
                members = ", ".join(task.id for task in item["members"])
                next_actions = ", ".join(item.get("next_actions", [])[:2]) or "(none)"
                blocked = ", ".join(item.get("blocked_reasons", [])) or "(none)"
                lines.append(
                    f"  {item['session_key']}: {item['state']} / {item['completion_state']} / blocked={blocked} / next={next_actions} / {members}"
                )
        queue_rows = self.agent_queue_summaries(
            chat_id,
            inflight_task_ids=inflight_task_ids,
            waiting_task_ids=waiting_task_ids,
        )
        if queue_rows:
            lines.extend(["", "Agent Queues:"])
            for row in queue_rows:
                if not row["queue"]:
                    lines.append(f"  @{row['agent']}: (empty)")
                    continue
                preview = ", ".join(
                    f"{item['task_id']}:{item['runtime_state']}" + (f"->{item['next_action']}" if item.get("next_action") else "")
                    for item in row["queue"][:4]
                )
                lines.append(f"  @{row['agent']}: {preview}")
        filtered_lanes = self.lane_runtime_summaries(
            chat_id,
            lane_summaries or [],
            inflight_task_ids=inflight_task_ids,
            waiting_task_ids=waiting_task_ids,
        )
        if filtered_lanes:
            lines.extend(["", "🛣️ Session Lanes:"])
            for lane in filtered_lanes[:6]:
                lines.append(
                    f"  {lane['lane_key']}: {lane.get('state', 'active')} / {lane.get('completion_state', 'unknown')} / inflight={lane.get('inflight', 0)} / waiting={lane.get('waiting', 0)} / owner={'@' + lane['owner'] if lane.get('owner') else '(none)'} / head={lane.get('head_job_id', '(none)')} / task={lane.get('head_task_id', '(none)')} / gate={lane.get('head_gate', '(none)') or '(none)'} / blocked={lane.get('head_blocked_reason', '(none)') or '(none)'} / next={lane.get('next_action', '(none)') or '(none)'}"
                )
        recommendations = self.proactive_recommendations(
            chat_id,
            inflight_task_ids=inflight_task_ids,
            waiting_task_ids=waiting_task_ids,
            lane_summaries=lane_summaries,
        )
        if recommendations:
            lines.extend(["", "Proactive Recommendations:"])
            for item in recommendations[:6]:
                if item["type"] == "family_escalation":
                    lines.append(
                        f"  family {item['family_id']}: {item['state']} / {item['reason']}"
                    )
                elif item["type"] == "family_completion":
                    lines.append(
                        f"  family {item['family_id']}: ready / {item['reason']}"
                    )
                elif item["type"] == "agent_rebalance":
                    lines.append(
                        f"  @{item['agent']}: {item['reason']} ({item['count']})"
                    )
                elif item["type"] == "idle_capacity":
                    lines.append(
                        f"  @{item['source_agent']}: {item['reason']} -> available {', '.join('@' + name for name in item['idle_agents'])}"
                    )
                elif item["type"] == "continuation_next_action":
                    lines.append(
                        f"  family {item['family_id']}: next -> {', '.join(item['actions'])}"
                    )
                elif item["type"] == "session_completion":
                    lines.append(
                        f"  session {item['session_key']}: {item['state']} / {item['reason']}"
                    )
                elif item["type"] == "family_ready_to_close":
                    lines.append(
                        f"  family {item['family_id']}: closeout / {item['reason']}"
                    )
                elif item["type"] == "session_ready_to_close":
                    lines.append(
                        f"  session {item['session_key']}: closeout / {item['reason']}"
                    )
                elif item["type"] == "lane_congestion":
                    lines.append(
                        f"  lane {item['lane_key']}: waiting={item['waiting']} / {item['reason']}"
                    )
                elif item["type"] == "lane_backlog":
                    lines.append(
                        f"  lane {item['lane_key']}: waiting={item['waiting']} / {item['reason']}"
                    )
                elif item["type"] == "lane_human_decision":
                    lines.append(
                        f"  lane {item['lane_key']}: waiting={item['waiting']} / {item['reason']}"
                    )
                elif item["type"] == "lane_unassigned":
                    lines.append(
                        f"  lane {item['lane_key']}: waiting={item['waiting']} / {item['reason']}"
                    )
                elif item["type"] == "lane_multi_owner":
                    lines.append(
                        f"  lane {item['lane_key']}: waiting={item['waiting']} / {item['reason']}"
                    )
                elif item["type"] == "lane_ready_to_close":
                    lines.append(
                        f"  lane {item['lane_key']}: closeout / {item['reason']}"
                    )
                elif item["type"] == "lane_partial_completion":
                    lines.append(
                        f"  lane {item['lane_key']}: partial / {item['reason']}"
                    )
                elif item["type"] == "lane_review_queue":
                    lines.append(
                        f"  lane {item['lane_key']}: review / waiting={item['waiting']} / {item['reason']}"
                    )
                elif item["type"] == "lane_quality_queue":
                    lines.append(
                        f"  lane {item['lane_key']}: qa / waiting={item['waiting']} / {item['reason']}"
                    )
        if self._stuck_reports:
            lines.extend(["", "Stuck Reports:"])
            for report in self._stuck_reports.values():
                lines.append(f"  {report.task_id}: {report.summary}")
        return "\n".join(lines)

    def control_plane_summary(
        self,
        chat_id: int,
        inflight_task_ids: set[str] | None = None,
        waiting_task_ids: set[str] | None = None,
        lane_summaries: list[dict] | None = None,
    ) -> dict:
        inflight_task_ids = set(inflight_task_ids or set())
        waiting_task_ids = set(waiting_task_ids or set())
        tasks = self.task_tracker.list_active(chat_id)
        task_states = [
            self.task_tracker._runtime_state_label(
                task,
                inflight_task_ids=inflight_task_ids,
                waiting_task_ids=waiting_task_ids,
                task_stage_sla_seconds=self.task_stage_sla_seconds,
            )
            for task in tasks
        ]
        families = self._family_rollups_with_actions(
            chat_id,
            inflight_task_ids=inflight_task_ids,
            waiting_task_ids=waiting_task_ids,
        )
        sessions = self._session_rollups_with_actions(
            chat_id,
            inflight_task_ids=inflight_task_ids,
            waiting_task_ids=waiting_task_ids,
        )
        presence = self.agent_presence(
            chat_id,
            inflight_task_ids=inflight_task_ids,
            waiting_task_ids=waiting_task_ids,
        )
        proactive = self.proactive_recommendations(
            chat_id,
            inflight_task_ids=inflight_task_ids,
            waiting_task_ids=waiting_task_ids,
            lane_summaries=lane_summaries,
        )
        filtered_lanes = self.lane_runtime_summaries(
            chat_id,
            lane_summaries or [],
            inflight_task_ids=inflight_task_ids,
            waiting_task_ids=waiting_task_ids,
        )
        return {
            "tasks_total": len(tasks),
            "tasks_inflight": task_states.count("inflight"),
            "tasks_waiting": task_states.count("waiting"),
            "tasks_blocked": task_states.count("blocked"),
            "tasks_stale": task_states.count("stale"),
            "families_total": len(families),
            "families_ready_to_close": sum(1 for item in families if item.get("ready_to_close")),
            "sessions_total": len(sessions),
            "sessions_ready_to_close": sum(1 for item in sessions if item.get("ready_to_close")),
            "lanes_total": len(filtered_lanes),
            "lanes_congested": sum(1 for item in filtered_lanes if item.get("inflight", 0) > 0 and item.get("waiting", 0) > 0),
            "lanes_waiting": sum(item.get("waiting", 0) for item in filtered_lanes),
            "lanes_blocked": sum(1 for item in filtered_lanes if item.get("head_blocked_reason")),
            "lanes_ready_to_close": sum(1 for item in filtered_lanes if item.get("ready_to_close")),
            "agents_total": len(presence),
            "agents_busy": sum(1 for item in presence if item.get("presence") == "busy"),
            "agents_blocked": sum(1 for item in presence if item.get("presence") == "blocked"),
            "agents_waiting": sum(1 for item in presence if item.get("presence") == "waiting"),
            "agents_idle": sum(1 for item in presence if item.get("presence") == "idle"),
            "proactive_total": len(proactive),
        }

    def control_plane_text(
        self,
        chat_id: int,
        inflight_task_ids: set[str] | None = None,
        waiting_task_ids: set[str] | None = None,
        lane_summaries: list[dict] | None = None,
    ) -> str:
        summary = self.control_plane_summary(
            chat_id,
            inflight_task_ids=inflight_task_ids,
            waiting_task_ids=waiting_task_ids,
            lane_summaries=lane_summaries,
        )
        lines = ["🧠 Control Plane Summary", ""]
        lines.append(
            f"Tasks: total={summary['tasks_total']} inflight={summary['tasks_inflight']} waiting={summary['tasks_waiting']} blocked={summary['tasks_blocked']} stale={summary['tasks_stale']}"
        )
        lines.append(
            f"Families: total={summary['families_total']} ready_to_close={summary['families_ready_to_close']}"
        )
        lines.append(
            f"Sessions: total={summary['sessions_total']} ready_to_close={summary['sessions_ready_to_close']}"
        )
        lines.append(
            f"Lanes: total={summary['lanes_total']} congested={summary['lanes_congested']} waiting_jobs={summary['lanes_waiting']}"
        )
        lines.append(
            f"Lane Closeout: ready={summary['lanes_ready_to_close']} blocked={summary['lanes_blocked']}"
        )
        lines.append(
            f"Agents: total={summary['agents_total']} busy={summary['agents_busy']} blocked={summary['agents_blocked']} waiting={summary['agents_waiting']} idle={summary['agents_idle']}"
        )
        lines.append(f"Proactive: total={summary['proactive_total']}")
        return "\n".join(lines)

    def _latest_route_summary(self, task_id: str) -> str:
        return self._latest_route_summary_for_chat(task_id, None)

    def _latest_route_summary_for_chat(self, task_id: str, chat_id: int | None) -> str:
        events = self.event_store.read_all()
        for event in reversed(events):
            if (
                event.task_id != task_id
                or event.type != "route_decision"
                or (chat_id is not None and event.chat_id != chat_id)
            ):
                continue
            reason = event.payload.get("reason", "unknown")
            agent = event.payload.get("agent", "unknown")
            return f"{reason} -> @{agent}"
        return "(无 route decision)"

    def _latest_gate_summary(self, task_id: str) -> str:
        return self._latest_gate_summary_for_chat(task_id, None)

    def _latest_gate_summary_for_chat(self, task_id: str, chat_id: int | None) -> str:
        for artifact in reversed(self.artifact_store.list_for_task(task_id, chat_id=chat_id)):
            if artifact.type != "gate_decision":
                continue
            return artifact.summary
        return "(无 gate decision)"

    def _latest_continuation_summary(self, task_id: str) -> str:
        return self._latest_continuation_summary_for_chat(task_id, None)

    def _latest_continuation_summary_for_chat(self, task_id: str, chat_id: int | None) -> str:
        for artifact in reversed(self.artifact_store.list_for_task(task_id, chat_id=chat_id)):
            if artifact.type != "continuation_checkpoint":
                continue
            return artifact.summary
        return "(无 continuation)"

    def gate_summary(self, task_id: str, chat_id: int | None = None) -> str:
        gates = [
            artifact for artifact in self.artifact_store.list_for_task(task_id, chat_id=chat_id)
            if artifact.type == "gate_decision"
        ]
        if not gates:
            return "(无 gate decisions)"
        lines = ["🚦 Gate Decisions：", ""]
        for artifact in gates[-8:]:
            lines.append(f"  [{artifact.source}] {artifact.summary}")
        return "\n".join(lines)

    def continuation_summary(self, task_id: str, chat_id: int | None = None) -> str:
        artifacts = [
            artifact for artifact in self.artifact_store.list_for_task(task_id, chat_id=chat_id)
            if artifact.type == "continuation_checkpoint"
        ]
        if not artifacts:
            return "(无 continuation checkpoint)"
        latest = artifacts[-1]
        return latest.content or latest.summary

    def _latest_continuation_record(self, task_id: str) -> dict[str, str]:
        return self._latest_continuation_record_for_chat(task_id, None)

    def _latest_continuation_record_for_chat(self, task_id: str, chat_id: int | None) -> dict[str, str]:
        artifacts = [
            artifact for artifact in self.artifact_store.list_for_task(task_id, chat_id=chat_id)
            if artifact.type == "continuation_checkpoint"
        ]
        if not artifacts:
            return {}
        content = artifacts[-1].content or ""
        record: dict[str, str] = {}
        for line in content.splitlines():
            if ": " not in line:
                continue
            key, value = line.split(": ", 1)
            record[key.strip()] = value.strip()
        return record

    def _latest_continuation_next_action(self, task_id: str) -> str:
        record = self._latest_continuation_record(task_id)
        return record.get("Next Action", "")

    def _latest_continuation_next_action_for_chat(self, task_id: str, chat_id: int | None) -> str:
        record = self._latest_continuation_record_for_chat(task_id, chat_id)
        return record.get("Next Action", "")

    def _pm_reply_rejects_acceptance(self, reply: str) -> bool:
        return any(keyword in reply for keyword in ("验收不通过", "拒绝验收"))

    def _pm_reply_accepts_acceptance(self, reply: str) -> bool:
        negative_markers = (
            "未完成",
            "尚未完成",
            "还未完成",
            "没有完成",
            "未验收通过",
            "尚未验收通过",
            "未通过验收",
            "需要继续验证",
            "继续验证",
        )
        if any(marker in reply for marker in negative_markers):
            return False
        if any(marker in reply for marker in ("验收通过", "通过验收", "任务完成", "已完成", "完成验收")):
            return True
        return bool(re.search(r"\bDONE\b", reply, re.IGNORECASE))

    def _task_chat_id_for_task(self, task) -> int:
        if task is None:
            return 0
        session_key = getattr(task, "session_key", "") or ""
        match = re.match(r"chat:(-?\d+):task:", session_key)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                pass
        for chat_id, tasks in self.task_tracker._tasks.items():
            if tasks.get(task.id) is task:
                return chat_id
        return self._task_chat_id(task.id)

    def _continuation_resume_message(self, task) -> str:
        record = self._latest_continuation_record(task.id)
        if not record:
            return task.description
        next_action = record.get("Next Action") or "continue task"
        return (
            f"[RESUME from continuation]\n"
            f"Task: {task.id}\n"
            f"Goal: {record.get('Goal', task.description)}\n"
            f"Current State: {record.get('Current State', task.status.value)}\n"
            f"Owner: {record.get('Owner', '@未分配')}\n"
            f"Family: {record.get('Family', getattr(task, 'family_id', task.id) or task.id)}\n"
            f"Next Action: {next_action}\n"
            f"Constraints: {record.get('Constraints', '(none)')}\n"
            f"Artifacts: {record.get('Artifacts', '(none)')}\n"
            f"Stop Conditions: {record.get('Stop Conditions', '(none)')}"
        )

    def _continuation_resume_agent(self, task):
        record = self._latest_continuation_record(task.id)
        next_action = (record.get("Next Action", "") or "").lower()
        owner = (record.get("Owner", "") or "").lstrip("@")
        if "architect review" in next_action or "await review verdict" in next_action:
            agent = self.registry.get_by_role("architect")
            if agent is not None:
                return agent
        if "qa quality gate" in next_action:
            agent = self.registry.get_by_role("qa")
            if agent is not None:
                return agent
        if "pm acceptance" in next_action or "request human decision" in next_action:
            agent = self.router.default_agent()
            if agent is not None:
                return agent
        if owner:
            agent = self.registry.get_by_name(owner)
            if agent is not None:
                return agent
        return self.router.detect_first(task.description) or self.router.default_agent()

    def _append_gate_decision_artifact(self, task, run_id: str, agent, reply: str) -> None:
        if task is None:
            return
        gate_stage = ""
        verdict = ""
        upper = reply.upper()
        if agent.role == "architect":
            gate_stage = "review"
            if "LGTM" in upper:
                verdict = "approved"
            elif any(keyword in reply for keyword in ("打回", "修复", "reject")):
                verdict = "changes_requested"
            elif "未给出有效评审结论" in reply:
                verdict = "noncompliant"
        elif agent.role == "qa":
            gate_stage = "quality_gate"
            if "NO-GO" in upper or "NO GO" in upper or "阻断" in reply:
                verdict = "blocked"
            elif "GO" in upper:
                verdict = "approved"
            elif "未给出有效测试结论" in reply:
                verdict = "noncompliant"
        elif agent.role == "pm":
            gate_stage = "acceptance"
            if self._pm_reply_accepts_acceptance(reply):
                verdict = "accepted"
            elif self._pm_reply_rejects_acceptance(reply):
                verdict = "rejected"
        if not gate_stage or not verdict:
            return
        task_chat_id = self._task_chat_id_for_task(task)
        self.artifact_store.append(ArtifactRecord(
            task_id=task.id,
            run_id=run_id,
            type="gate_decision",
            source=agent.name,
            summary=f"{gate_stage}:{verdict}",
            chat_id=task_chat_id,
            content=json.dumps(
                {
                    "stage": gate_stage,
                    "verdict": verdict,
                    "agent": agent.name,
                    "task_id": task.id,
                    "session_key": getattr(task, "session_key", "") or f"chat:{task_chat_id}:task:{task.id}",
                    "family_id": getattr(task, "family_id", "") or task.id,
                    "blocked_reason": getattr(task, "blocked_reason", ""),
                    "reply": reply[:500],
                },
                ensure_ascii=False,
            ),
        ))
        self._record_event(
            run_id,
            task_chat_id,
            "gate_decision",
            agent.name,
            task,
            {
                "stage": gate_stage,
                "verdict": verdict,
                "task_id": task.id,
                "session_key": getattr(task, "session_key", "") or f"chat:{task_chat_id}:task:{task.id}",
                "family_id": getattr(task, "family_id", "") or task.id,
                "blocked_reason": getattr(task, "blocked_reason", ""),
            },
        )

    def _append_continuation_artifact(self, task, run_id: str) -> None:
        if task is None:
            return
        content = "\n".join(
            [
                f"Goal: {task.description}",
                f"Session: {getattr(task, 'session_key', '') or '(none)'}",
                f"Current State: {task.status.value}",
                f"Owner: @{task.assigned_to or '未分配'}",
                f"Family: {getattr(task, 'family_id', '') or task.id}",
                f"Next Action: {self._next_action_summary(task)}",
                f"Constraints: blocked={getattr(task, 'blocked_reason', '') or '(none)'}",
                f"Artifacts: route={self._latest_route_summary_for_chat(task.id, self._task_chat_id_for_task(task))}; gate={self._latest_gate_summary_for_chat(task.id, self._task_chat_id_for_task(task))}; branch={task.branch_name or '(none)'}; pr={task.github_pr_url or '(none)'}",
                f"Stop Conditions: {self._stop_condition_summary(task)}",
            ]
        )
        self.artifact_store.append(
            ArtifactRecord(
                task_id=task.id,
                run_id=run_id,
                type="continuation_checkpoint",
                source="system",
                summary=f"continuation:{task.status.value}",
                chat_id=self._task_chat_id_for_task(task),
                content=content,
            )
        )

    def _next_action_summary(self, task) -> str:
        blocked = getattr(task, "blocked_reason", "")
        if blocked == "approval_required":
            return "wait for approval or operator intervention"
        if blocked == "human_input_required":
            return "request human decision"
        if blocked in {"review_changes_requested", "review_replan_required"}:
            return "return to PM/Dev for review follow-up"
        if blocked in {"quality_gate_blocked", "quality_gate_replan_required"}:
            return "repair quality gate blockers"
        if blocked == "acceptance_rejected":
            return "re-plan and re-implement against acceptance feedback"
        if task.status == TaskStatus.REVIEW_REQ:
            return "architect review"
        if task.status == TaskStatus.REVIEWING:
            return "await review verdict"
        if task.status == TaskStatus.ACCEPTED:
            qa = self.registry.get_by_role("qa")
            return "qa quality gate" if qa else "pm acceptance"
        if task.status == TaskStatus.VALIDATING:
            return "pm acceptance / release readiness"
        if task.status == TaskStatus.DONE:
            return "no action required"
        return "continue implementation"

    def _stop_condition_summary(self, task) -> str:
        blocked = getattr(task, "blocked_reason", "")
        if blocked:
            return f"stop until {blocked} is resolved"
        if task.status == TaskStatus.DONE:
            return "task complete"
        return "continue until next gate or explicit blocker"

    def family_summary(self, chat_id: int, family_id: str) -> str:
        members = self.task_tracker.family_members(chat_id, family_id)
        if not members:
            return f"未找到 family: {family_id}"
        rollups = self._family_rollups_with_actions(chat_id)
        state = next((item["state"] for item in rollups if item["family_id"] == family_id), "active")
        blocked = next((item.get("blocked_reasons", []) for item in rollups if item["family_id"] == family_id), [])
        next_actions = next((item.get("next_actions", []) for item in rollups if item["family_id"] == family_id), [])
        completion = self.task_tracker.family_completion_state(chat_id, family_id)
        lines = [f"Family: {family_id}", ""]
        lines.append(f"State: {state}")
        lines.append(f"Completion: {completion}")
        lines.append(f"Blocked Reasons: {', '.join(blocked) or '(none)'}")
        lines.append(f"Next Actions: {', '.join(next_actions) or '(none)'}")
        lines.append("")
        for task in members:
            lines.append(
                f"- {task.id} status={task.status.value} assigned=@{task.assigned_to or '未分配'} blocked={getattr(task, 'blocked_reason', '') or '(无)'}"
            )
        return "\n".join(lines)

    def session_summary(self, chat_id: int, session_key: str) -> str:
        members = [
            task for task in self.task_tracker.list_all(chat_id)
            if getattr(task, "session_key", "") == session_key
        ]
        if not members:
            return f"未找到 session: {session_key}"
        rollups = self._session_rollups_with_actions(chat_id)
        blocked = next((item.get("blocked_reasons", []) for item in rollups if item["session_key"] == session_key), [])
        next_actions = next((item.get("next_actions", []) for item in rollups if item["session_key"] == session_key), [])
        lines = [f"Session: {session_key}", ""]
        lines.append(f"Completion: {self.task_tracker.session_completion_state(chat_id, session_key)}")
        lines.append(f"Blocked Reasons: {', '.join(blocked) or '(none)'}")
        lines.append(f"Next Actions: {', '.join(next_actions) or '(none)'}")
        lines.append("")
        for task in members:
            lines.append(
                f"- {task.id} status={task.status.value} family={getattr(task, 'family_id', task.id)} blocked={getattr(task, 'blocked_reason', '') or '(无)'} route={self._latest_route_summary_for_chat(task.id, chat_id)} next={self._latest_continuation_next_action_for_chat(task.id, chat_id) or '(none)'} gate={self._latest_gate_summary_for_chat(task.id, chat_id)}"
            )
        return "\n".join(lines)

    def _family_escalation_reason(self, family: dict) -> str:
        state = family.get("state", "")
        completion_state = family.get("completion_state", "")
        blocked_reasons = set(family.get("blocked_reasons", []))
        if "human_input_required" in blocked_reasons:
            return "need_human_decision"
        if "approval_required" in blocked_reasons:
            return "pending_approval"
        if completion_state == "partial":
            return "partial_family_completion"
        if state == "blocked":
            return "replan_or_reassign"
        if state == "stale":
            return "stale_family_follow_up"
        if state == "waiting":
            return "waiting_family_follow_up"
        return ""

    def artifacts_summary(self, task_id: str, chat_id: int | None = None) -> str:
        return self.artifact_store.format_for_task(task_id, chat_id=chat_id)

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

    def _advance_task_after_reply(self, task, agent, reply: str, routing_reply: str | None = None):
        if task is None:
            return
        routing_reply = routing_reply or reply
        upper = reply.upper()
        if agent.role == "dev" and any(
            keyword in routing_reply for keyword in ("@architect", "Review", "review", "Code Review")
        ):
            if task.status == TaskStatus.PLANNING:
                task.transition(TaskStatus.IN_PROGRESS)
            task.transition(TaskStatus.REVIEW_REQ)
        if agent.role == "architect":
            if "LGTM" in upper:
                if task.status == TaskStatus.REVIEW_REQ:
                    task.transition(TaskStatus.REVIEWING)
                task.transition(TaskStatus.ACCEPTED)
            elif any(keyword in reply for keyword in ("打回", "修复", "reject")):
                if task.status == TaskStatus.REVIEW_REQ:
                    task.transition(TaskStatus.REVIEWING)
                task.transition(TaskStatus.IN_PROGRESS)
            elif "未给出有效评审结论" in reply:
                task.transition(TaskStatus.IN_PROGRESS)
                pm = self.router.default_agent()
                if pm is not None:
                    task.assigned_to = pm.name
        if agent.role == "qa":
            if "NO-GO" in upper or "NO GO" in upper or "阻断" in reply:
                task.transition(TaskStatus.IN_PROGRESS)
            elif "GO" in upper:
                if task.status == TaskStatus.ACCEPTED:
                    task.transition(TaskStatus.VALIDATING)
            elif "未给出有效测试结论" in reply:
                task.transition(TaskStatus.IN_PROGRESS)
                pm = self.router.default_agent()
                if pm is not None:
                    task.assigned_to = pm.name
        if agent.role == "pm":
            if self._pm_reply_rejects_acceptance(reply):
                if task.status == TaskStatus.ACCEPTED:
                    task.transition(TaskStatus.VALIDATING)
                if task.status == TaskStatus.VALIDATING:
                    task.transition(TaskStatus.IN_PROGRESS)
            elif self._pm_reply_accepts_acceptance(reply):
                if task.status == TaskStatus.ACCEPTED:
                    task.transition(TaskStatus.VALIDATING)
                if task.status == TaskStatus.VALIDATING:
                    task.transition(TaskStatus.DONE)
        self.state_store.save_task(self._task_chat_id_for_task(task), task)

    def _update_task_blocked_reason(self, task, agent, reply: str, routing_reply: str, artifacts) -> None:
        if task is None:
            return
        shell_output = getattr(artifacts, "shell_output", "")
        shell_failed = self.executor.is_failure(shell_output)
        upper = reply.upper()
        if agent.role == "dev":
            if "[approval required:" in shell_output:
                task.blocked_reason = "approval_required"
            elif shell_failed:
                task.blocked_reason = "validation_failed"
            elif "Next: @architect review" in routing_reply:
                task.blocked_reason = ""
        elif agent.role == "architect":
            if "LGTM" in upper:
                task.blocked_reason = ""
            elif any(keyword in reply for keyword in ("打回", "修复", "reject")):
                task.blocked_reason = "review_changes_requested"
            elif "未给出有效评审结论" in reply:
                task.blocked_reason = "review_replan_required"
        elif agent.role == "qa":
            if "NO-GO" in upper or "NO GO" in upper or "阻断" in reply:
                task.blocked_reason = "quality_gate_blocked"
            elif "CONDITIONAL GO" in upper or "GO" in upper:
                task.blocked_reason = ""
            elif "未给出有效测试结论" in reply:
                task.blocked_reason = "quality_gate_replan_required"
        elif agent.role == "pm":
            if "@HUMAN" in upper:
                task.blocked_reason = "human_input_required"
            elif self._pm_reply_rejects_acceptance(reply):
                task.blocked_reason = "acceptance_rejected"
            elif self._pm_reply_accepts_acceptance(reply):
                task.blocked_reason = ""

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
            baseline_dirty_files = await self.executor.git_changed_files(limit=64)
            baseline_dirty_hashes = await self.executor.file_hashes(baseline_dirty_files)
            await self.executor.git_create_branch(branch)
            task.branch_name = branch
            self.branch_sessions.save(BranchSession(
                chat_id=chat_id,
                task_id=task.id,
                branch_name=branch,
                base_branch=base_branch,
                baseline_dirty_files=baseline_dirty_hashes,
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
        self._paused_tasks.discard((chat_id, task_id))
        if checkpoint is not None:
            self._histories[chat_id] = list(checkpoint.history)
            self._dev_retries[chat_id] = checkpoint.dev_retries
            if checkpoint.task_status:
                try:
                    task.status = TaskStatus(checkpoint.task_status)
                except ValueError:
                    pass
            resume_message = checkpoint.current_message
            agent = self.router.detect_first(checkpoint.current_message)
            if agent is None or agent.name == checkpoint.current_agent:
                agent = self.registry.get_by_name(checkpoint.current_agent)
            if agent is None:
                agent = self.router.detect_first(checkpoint.current_message) or self.router.default_agent()
            hop = checkpoint.hop
        else:
            continuation = self._latest_continuation_record(task.id)
            if not continuation:
                return False
            self._histories[chat_id] = list(self._histories.get(chat_id, []))
            self._dev_retries[chat_id] = self._dev_retries.get(chat_id, 0)
            resume_message = self._continuation_resume_message(task)
            agent = self._continuation_resume_agent(task)
            hop = -1
            if agent is None:
                return False
        self._record_event(
            run_id,
            chat_id,
            "run_resumed",
            "system",
            task,
            {"task_id": task_id, "hop": hop},
        )
        await self.run_chain(
            resume_message,
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
        replay_message = self._continuation_resume_message(task)
        agent = self._continuation_resume_agent(task)
        if agent is None:
            agent = self.router.detect_first(task.description) or self.router.default_agent()
        if agent is None:
            return False
        await self.run_chain(
            replay_message,
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

    def record_route_decision(
        self,
        run_id: str,
        chat_id: int,
        task,
        message: str,
        agent,
        reason: str,
    ) -> None:
        if task is None or agent is None:
            return
        self._record_event(
            run_id,
            chat_id,
            "route_decision",
            "system",
            task,
            {
                "reason": reason,
                "agent": agent.name,
                "role": agent.role,
                "message": message[:240],
                "task_id": task.id,
                "session_key": getattr(task, "session_key", "") or f"chat:{chat_id}:task:{task.id}",
                "family_id": getattr(task, "family_id", "") or task.id,
                "parent_task_id": getattr(task, "parent_task_id", ""),
            },
        )

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
                run_id=self._task_run_ids.get((self._task_chat_id_for_task(task), task.id), ""),
                type="github_pr_event",
                source="github",
                summary=f"PR #{number} {action}",
                content=json.dumps(payload, ensure_ascii=False)[:3000],
            ))
            self.state_store.save_task(self._task_chat_id_for_task(task), task)
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
                run_id=self._task_run_ids.get((self._task_chat_id_for_task(task), task.id), ""),
                type="github_review_event",
                source="github",
                summary=f"review {state or 'submitted'}",
                content=json.dumps(payload, ensure_ascii=False)[:3000],
            ))
            self.state_store.save_task(self._task_chat_id_for_task(task), task)
            return

        if event_type == "pull_request_review_comment":
            pr = payload.get("pull_request", {})
            task = self._find_task_by_pr_number(pr.get("number"))
            if task is None:
                return
            self.artifact_store.append(ArtifactRecord(
                task_id=task.id,
                run_id=self._task_run_ids.get((self._task_chat_id_for_task(task), task.id), ""),
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
                run_id=self._task_run_ids.get((self._task_chat_id_for_task(task), task.id), ""),
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
                    run_id=self._task_run_ids.get((self._task_chat_id_for_task(task), task.id), ""),
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
                session_key=record.get("session_key") or f"chat:{record['chat_id']}:task:{record['id']}",
                family_id=record.get("family_id") or record["id"],
                parent_task_id=record.get("parent_task_id", ""),
                blocked_reason=record.get("blocked_reason", ""),
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
        matches = []
        for chat_id, tasks in self.task_tracker._tasks.items():
            if task_id in tasks:
                matches.append(chat_id)
        if len(matches) == 1:
            return matches[0]
        return 0

    def trace_summary(self, task_id: str, chat_id: int | None = None) -> str:
        from .trace.store import TraceStore
        return TraceStore(self.event_store).format_task_timeline(task_id, chat_id=chat_id)
