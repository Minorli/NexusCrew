"""Core orchestrator — agent chain runner."""
import asyncio
import time
from pathlib import Path
from .registry import AgentRegistry
from .router import Router
from .memory.crew_memory import CrewMemory
from .executor.shell import ShellExecutor
from .hr.laziness import detect_all as detect_laziness
from .hr.metrics_store import MetricsStore
from .hr.pressure import apply_pressure, calculate_pressure_level
from .metrics import MetricsCollector
from .task_state import TaskStatus, TaskTracker

MAX_CHAIN_HOPS = 10
MAX_DEV_RETRY  = 5


class Orchestrator:
    def __init__(self, registry: AgentRegistry, router: Router,
                 crew_memory: CrewMemory, shell_executor: ShellExecutor,
                 max_chain_hops: int = MAX_CHAIN_HOPS,
                 max_dev_retry: int = MAX_DEV_RETRY,
                 pressure_max_prompt_len: int = 500):
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
        self.metrics = MetricsCollector()
        self.metrics_store = MetricsStore(
            self.crew_memory.path.with_name("metrics_history.jsonl")
        )
        self.task_tracker = TaskTracker()

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

        task = self._ensure_task(chat_id, message, agent, initial_agent is None)
        self._add_history(chat_id, "human", message)

        for hop in range(self.max_hops):
            self._advance_task_before_handle(task, agent)
            await self._ensure_dev_branch(chat_id, task, agent)
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
            memory   = self.crew_memory.read()
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
            if artifacts.shell_output:
                await send(
                    f"```\n{artifacts.shell_output[:3500]}\n```",
                    agent_name=agent.name,
                )
                self._add_history(chat_id, "shell", artifacts.shell_output[:600])

            self._advance_task_after_reply(task, agent, reply)

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
                    self.run_chain(reply, chat_id, send, a)
                    for a in next_agents
                ])
                return

            message = reply
            agent   = next_agent
        else:
            await send("⚠️ 达到最大跳转上限，请人工介入。")
            return

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

    def _ensure_task(self, chat_id: int, message: str, agent, create_new: bool):
        task = self.task_tracker.latest_active(chat_id)
        if create_new or task is None:
            task = self.task_tracker.create(chat_id, message)
        task.assigned_to = agent.name
        return task

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

    async def _ensure_dev_branch(self, chat_id: int, task, agent):
        if task is None or agent.role != "dev":
            return
        key = (chat_id, task.id)
        if key in self._task_branch_attempts:
            return
        self._task_branch_attempts.add(key)
        branch = self._build_task_branch_name(task)
        try:
            await self.executor.git_create_branch(branch)
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
