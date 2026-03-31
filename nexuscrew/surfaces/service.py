"""Shared command service for Telegram / Slack surfaces."""

from datetime import datetime
import re


class ChatOpsService:
    """Surface-agnostic command layer."""
    FOLLOW_UP_HINTS = (
        "然后", "进度", "在吗", "还在", "继续", "没回复", "没有进度",
        "又没有", "很久", "怎么还", "卡住", "更新", "看下",
    )
    CHILD_TASK_HINTS = (
        "子任务", "并行", "另外处理", "单独处理", "拆出", "拆分", "parallel",
        "follow-up task", "separate task", "new task under",
    )

    def __init__(self, registry, orchestrator, runner, executor, skills,
                 board_getter, board_updater, crew_memory=None):
        self.registry = registry
        self.orchestrator = orchestrator
        self.runner = runner
        self.executor = executor
        self.skills = skills
        self.board_getter = board_getter
        self.board_updater = board_updater
        self.crew_memory = crew_memory

    def status_text(self, chat_id: int) -> str:
        from ..telegram.formatter import status_table

        roster = self.registry.list_all()
        if self.orchestrator:
            if hasattr(self.orchestrator, "agent_presence"):
                roster = self.orchestrator.agent_presence(
                    chat_id,
                    inflight_task_ids=self.runner.active_task_ids(),
                    waiting_task_ids=self.runner.waiting_task_ids(),
                )
        text = status_table(roster)
        if self.orchestrator:
            try:
                text += "\n\n" + self.orchestrator.format_status(
                    chat_id,
                    inflight_task_ids=self.runner.active_task_ids(),
                    waiting_task_ids=self.runner.waiting_task_ids(),
                )
            except TypeError:
                text += "\n\n" + self.orchestrator.format_status(chat_id)
        text += "\n\n" + self.runner.format_status()
        return text

    def tasks_text(self) -> str:
        return self.runner.format_status()

    def failed_text(self) -> str:
        return self.runner.format_failed()

    def task_text(self, chat_id: int, task_id: str) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        return self.orchestrator.format_task_detail(chat_id, task_id)

    async def cancel_job(self, job_id: str) -> str:
        try:
            cancelled = await self.runner.cancel(job_id)
        except Exception as err:
            return f"取消后台任务失败: {job_id} ({err})"
        return (
            f"后台任务 {job_id} 已取消。"
            if cancelled
            else f"无法取消后台任务: {job_id}"
        )

    def memory_text(self, tail_lines: int = 30) -> str:
        if self.crew_memory is None:
            return ""
        return self.crew_memory.read(tail_lines=tail_lines)

    def reset_text(self, chat_id: int) -> str:
        saved: list[str] = []
        if self.orchestrator and hasattr(self.orchestrator, "checkpoint_active_continuations"):
            saved = list(self.orchestrator.checkpoint_active_continuations(chat_id))
        if self.orchestrator and hasattr(self.orchestrator, "reset_history"):
            self.orchestrator.reset_history(chat_id)
        if saved:
            return f"对话历史已清空，并为 {len(saved)} 个活跃任务写入续接摘要。"
        return "对话历史已清空。"

    def approvals_text(self) -> str:
        if not self.executor:
            return "当前无可用执行器。"
        pending = self.executor.list_pending_approvals()
        if not pending:
            return "当前无待审批动作。"
        lines = ["⏳ 待审批动作：", ""]
        for approval in pending:
            lines.append(
                f"  [{approval.id}] {approval.risk_level} / {getattr(approval, 'action_type', 'unknown')} — {approval.summary}"
            )
        return "\n".join(lines)

    async def approve(self, approval_id: str) -> str:
        if not self.executor:
            return "当前无可用执行器。"
        result = await self.executor.approve_and_run(approval_id)
        if isinstance(result, str) and (
            result.startswith("未找到审批")
            or result.startswith("审批状态无效")
        ):
            return result
        return f"✅ {approval_id} 已批准并执行。\n\n```\n{result[:3200]}\n```"

    def reject(self, approval_id: str) -> str:
        if not self.executor:
            return "当前无可用执行器。"
        return self.executor.reject(approval_id)

    async def create_task(self, chat_id: int, text: str) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        task = self.orchestrator.task_tracker.create(chat_id, text)
        suggestions = self.skills.suggest(text)
        lines = [f"已创建任务: {task.id}", f"描述: {task.description}"]
        if suggestions:
            lines.append("推荐 Skills: " + ", ".join(skill.name for skill in suggestions))
        await self.board_updater(chat_id)
        return "\n".join(lines)

    def doctor_text(self, chat_id: int) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        try:
            return self.orchestrator.doctor_report(
                chat_id,
                inflight_task_ids=self.runner.active_task_ids(),
                waiting_task_ids=self.runner.waiting_task_ids(),
                lane_summaries=self.runner.lane_summaries(),
            )
        except TypeError:
            return self.orchestrator.doctor_report(chat_id)

    async def handoff(self, chat_id: int, task_id: str, agent_name: str) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        task = self.orchestrator.task_tracker.get(chat_id, task_id)
        agent = None
        if hasattr(self.registry, "get_by_name"):
            agent = self.registry.get_by_name(agent_name)
        elif hasattr(self.registry, "list_all"):
            for item in self.registry.list_all():
                if item.get("name") == agent_name:
                    agent = type("Agent", (), {"name": agent_name})()
                    break
        if task is None or agent is None:
            return "任务或 Agent 不存在。"
        task.assigned_to = agent.name
        if hasattr(task, "history"):
            task.history.append(f"handoff -> {agent.name}")
        await self.board_updater(chat_id)
        return f"任务 {task.id} 已转交给 @{agent.name}"

    def skills_text(self) -> str:
        lines = ["🧰 Skills：", ""]
        for skill in self.skills.list_all():
            lines.append(f"  {skill.name} — {skill.description}")
        return "\n".join(lines)

    def trace_text(self, task_id: str, chat_id: int | None = None) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        try:
            return self.orchestrator.trace_summary(task_id, chat_id=chat_id)
        except TypeError:
            return self.orchestrator.trace_summary(task_id)

    def presence_text(self, chat_id: int) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        rows = self.orchestrator.agent_presence(
            chat_id,
            inflight_task_ids=self.runner.active_task_ids(),
            waiting_task_ids=self.runner.waiting_task_ids(),
        )
        if not rows:
            return "(无 agent presence)"
        lines = ["👥 Agent Presence：", ""]
        for row in rows:
            current_task = row.get("current_task_id", "")
            current = f" / {current_task}" if current_task else ""
            lines.append(
                f"  @{row['name']}: {row.get('presence', 'unknown')} / load={row.get('load', 'unknown')} / queue={row.get('queue_size', 0)} / blocked={row.get('blocked_count', 0)} / inflight={row.get('inflight_count', 0)} / waiting={row.get('waiting_count', 0)}{current}"
            )
        return "\n".join(lines)

    def queues_text(self, chat_id: int) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        rows = self.orchestrator.agent_queue_summaries(
            chat_id,
            inflight_task_ids=self.runner.active_task_ids(),
            waiting_task_ids=self.runner.waiting_task_ids(),
        )
        if not rows:
            return "(无 queues)"
        lines = ["🧵 Agent Queues：", ""]
        for row in rows:
            if not row["queue"]:
                lines.append(f"  @{row['agent']}: (empty)")
                continue
            preview = ", ".join(
                f"{item['task_id']}:{item['runtime_state']}" + (f"->{item['next_action']}" if item.get("next_action") else "")
                for item in row["queue"][:6]
            )
            lines.append(f"  @{row['agent']}: {preview}")
        return "\n".join(lines)

    def lanes_text(self, chat_id: int) -> str:
        raw_rows = self.runner.lane_summaries()
        if self.orchestrator and hasattr(self.orchestrator, "lane_runtime_summaries"):
            rows = self.orchestrator.lane_runtime_summaries(
                chat_id,
                raw_rows,
                inflight_task_ids=self.runner.active_task_ids(),
                waiting_task_ids=self.runner.waiting_task_ids(),
            )
        else:
            rows = [
                row for row in raw_rows
                if not chat_id or row.get("chat_id") in (0, chat_id)
            ]
        if not rows:
            return "(无 session lanes)"
        lines = ["🛣️ Session Lanes：", ""]
        for row in rows:
            jobs = ", ".join(
                f"{item['id']}:{item['status']}" + (f"/{item['task_id']}" if item.get("task_id") else "")
                for item in row["jobs"][:6]
            )
            extra = ""
            if row.get("head_task_id") or row.get("next_action") or row.get("head_blocked_reason") or row.get("ready_to_close"):
                extra = (
                    f" / task={row.get('head_task_id', '(none)') or '(none)'}"
                    f" / blocked={row.get('head_blocked_reason', '(none)') or '(none)'}"
                    f" / next={row.get('next_action', '(none)') or '(none)'}"
                    f" / closeout={'yes' if row.get('ready_to_close') else 'no'}"
                )
            lines.append(
                f"  {row['lane_key']}: {row.get('state', 'active')} / inflight={row['inflight']} waiting={row['waiting']} / {jobs}{extra}"
            )
        return "\n".join(lines)

    def lane_text(self, chat_id: int, lane_key: str) -> str:
        if self.orchestrator and hasattr(self.orchestrator, "lane_summary"):
            return self.orchestrator.lane_summary(
                chat_id,
                lane_key,
                lane_summaries=self.runner.lane_summaries(),
                inflight_task_ids=self.runner.active_task_ids(),
                waiting_task_ids=self.runner.waiting_task_ids(),
            )
        return f"未找到 lane: {lane_key}"

    def lane_trace_text(self, chat_id: int, lane_key: str) -> str:
        if self.orchestrator and hasattr(self.orchestrator, "lane_trace_summary"):
            return self.orchestrator.lane_trace_summary(
                chat_id,
                lane_key,
                lane_summaries=self.runner.lane_summaries(),
            )
        return "(无 lane trace)"

    def proactive_text(self, chat_id: int) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        try:
            recs = self.orchestrator.proactive_recommendations(
                chat_id,
                inflight_task_ids=self.runner.active_task_ids(),
                waiting_task_ids=self.runner.waiting_task_ids(),
                lane_summaries=self.runner.lane_summaries(),
            )
        except TypeError:
            recs = self.orchestrator.proactive_recommendations(
                chat_id,
                inflight_task_ids=self.runner.active_task_ids(),
                waiting_task_ids=self.runner.waiting_task_ids(),
            )
        if not recs:
            return "(无 proactive recommendations)"
        if hasattr(self.orchestrator, "_format_proactive_recommendations"):
            return self.orchestrator._format_proactive_recommendations(recs)
        lines = ["🤖 Proactive Recommendations", ""]
        for item in recs[:6]:
            lines.append(str(item))
        return "\n".join(lines)

    def control_text(self, chat_id: int) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        if hasattr(self.orchestrator, "control_plane_text"):
            try:
                return self.orchestrator.control_plane_text(
                    chat_id,
                    inflight_task_ids=self.runner.active_task_ids(),
                    waiting_task_ids=self.runner.waiting_task_ids(),
                    lane_summaries=self.runner.lane_summaries(),
                )
            except TypeError:
                return self.orchestrator.control_plane_text(
                    chat_id,
                    inflight_task_ids=self.runner.active_task_ids(),
                    waiting_task_ids=self.runner.waiting_task_ids(),
                )
        if hasattr(self.orchestrator, "control_plane_summary"):
            try:
                summary = self.orchestrator.control_plane_summary(
                    chat_id,
                    inflight_task_ids=self.runner.active_task_ids(),
                    waiting_task_ids=self.runner.waiting_task_ids(),
                    lane_summaries=self.runner.lane_summaries(),
                )
            except TypeError:
                summary = self.orchestrator.control_plane_summary(
                    chat_id,
                    inflight_task_ids=self.runner.active_task_ids(),
                    waiting_task_ids=self.runner.waiting_task_ids(),
                )
            lines = ["🧠 Control Plane Summary", ""]
            for key, value in summary.items():
                lines.append(f"{key}: {value}")
            return "\n".join(lines)
        return "(无 control plane summary)"

    def artifacts_text(self, task_id: str, chat_id: int | None = None) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        try:
            return self.orchestrator.artifacts_summary(task_id, chat_id=chat_id)
        except TypeError:
            return self.orchestrator.artifacts_summary(task_id)

    def gates_text(self, task_id: str, chat_id: int | None = None) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        try:
            return self.orchestrator.gate_summary(task_id, chat_id=chat_id)
        except TypeError:
            return self.orchestrator.gate_summary(task_id)

    def continuation_text(self, task_id: str, chat_id: int | None = None) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        try:
            return self.orchestrator.continuation_summary(task_id, chat_id=chat_id)
        except TypeError:
            return self.orchestrator.continuation_summary(task_id)

    def family_text(self, chat_id: int, family_id: str) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        return self.orchestrator.family_summary(chat_id, family_id)

    def session_text(self, chat_id: int, session_key: str) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        return self.orchestrator.session_summary(chat_id, session_key)

    def pr_text(self, chat_id: int, task_id: str) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        return self.orchestrator.pr_summary(chat_id, task_id)

    def ci_text(self, chat_id: int, task_id: str) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        return self.orchestrator.ci_summary(chat_id, task_id)

    async def board_text(self, chat_id: int) -> str:
        await self.board_updater(chat_id)
        return self.board_getter(chat_id)

    def pause(self, chat_id: int, task_id: str) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        return (
            f"任务 {task_id} 将在下一个可暂停点暂停。"
            if self.orchestrator.pause_task(chat_id, task_id)
            else f"未找到任务: {task_id}"
        )

    async def resume(self, chat_id: int, task_id: str, send) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        return (
            f"任务 {task_id} 已恢复执行。"
            if await self.orchestrator.resume_task(chat_id, task_id, send)
            else f"无法恢复任务: {task_id}"
        )

    async def replay(self, chat_id: int, task_id: str, send) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        return (
            f"任务 {task_id} 已重新执行。"
            if await self.orchestrator.replay_task(chat_id, task_id, send)
            else f"无法重放任务: {task_id}"
        )

    def submit_message(self, chat_id: int, message: str, send) -> str:
        if not self.orchestrator:
            return ""
        router = getattr(self.orchestrator, "router", None)
        initial_agent = None
        if router is not None:
            routed_initial = router.detect_first_routed(message) if hasattr(router, "detect_first_routed") else None
            if routed_initial:
                if routed_initial["kind"] == "role_alias" and hasattr(self.orchestrator, "_pick_best_agent_for_role"):
                    initial_agent = self.orchestrator._pick_best_agent_for_role(
                        routed_initial["role"],
                        chat_id,
                    ) or routed_initial["agent"]
                else:
                    initial_agent = routed_initial["agent"]
            else:
                initial_agent = router.detect_first(message) or router.default_agent()
        task = None
        route_reason = "new_task"
        explicit_task_id = self._extract_task_id(message)
        if explicit_task_id:
            task = self.orchestrator.task_tracker.get(chat_id, explicit_task_id)
            if task is not None:
                route_reason = "explicit_task_id"
                if self._wants_child_task(message):
                    task = self.orchestrator.task_tracker.create(
                        chat_id,
                        message,
                        parent_task=task,
                    )
                    route_reason = "child_task"
        if initial_agent is not None:
            if task is None and self._looks_like_follow_up(message):
                task = self.orchestrator.task_tracker.latest_active_for_assignee(
                    chat_id,
                    initial_agent.name,
                )
                if task is not None:
                    route_reason = "assignee_follow_up"
            if task is None and hasattr(self.orchestrator.task_tracker, "find_related_active"):
                task = self.orchestrator.task_tracker.find_related_active(
                    chat_id,
                    message,
                    preferred_assignee=initial_agent.name,
                )
                if task is not None:
                    route_reason = "same_topic_task"
        if task is None:
            task = self.orchestrator.task_tracker.create(chat_id, message)
        else:
            task.history.append(
                f"human_follow_up at {datetime.now().isoformat()}: {message[:200]}"
            )
            self.orchestrator.state_store.save_task(chat_id, task)
        run_id = self.orchestrator._new_run_id()
        if hasattr(self.orchestrator, "record_route_decision"):
            self.orchestrator.record_route_decision(
                run_id,
                chat_id,
                task,
                message,
                initial_agent,
                route_reason,
            )

        async def notify_failure(job, err):
            await send(f"⚠️ 后台任务 {job.id} 执行失败：{err}")

        async def notify_heartbeat(job):
            current = self.orchestrator.task_tracker.get(chat_id, task.id) or task
            assignee = f"@{current.assigned_to}" if current.assigned_to else "@未分配"
            await send(
                f"⏳ 任务 {current.id} 仍在处理中：{assignee} 正在执行，"
                f"当前状态 `{current.status.value}`。"
            )

        async def notify_complete(job):
            current = self.orchestrator.task_tracker.get(chat_id, task.id) or task
            status_value = getattr(current.status, "value", str(current.status))
            if status_value in ("done", "failed"):
                return "completed"
            return "waiting"

        return self.runner.submit(
            message,
            self.orchestrator.run_chain(message, chat_id, send, run_id=run_id, task=task),
            chat_id=chat_id,
            task_id=task.id,
            run_id=run_id,
            lane_key=getattr(task, "session_key", "") or f"chat:{chat_id}:task:{task.id}",
            on_error=notify_failure,
            on_complete=notify_complete,
            on_heartbeat=notify_heartbeat,
        )

    def _extract_task_id(self, message: str) -> str | None:
        match = re.search(r"\bT-\d{4}\b", message)
        return match.group(0) if match else None

    def _looks_like_follow_up(self, message: str) -> bool:
        lowered = message.lower()
        return any(hint in lowered for hint in self.FOLLOW_UP_HINTS)

    def _wants_child_task(self, message: str) -> bool:
        lowered = message.lower()
        return any(hint in lowered for hint in self.CHILD_TASK_HINTS)
