"""Shared command service for Telegram / Slack surfaces."""

from datetime import datetime
import re


class ChatOpsService:
    """Surface-agnostic command layer."""
    FOLLOW_UP_HINTS = (
        "然后", "进度", "在吗", "还在", "继续", "没回复", "没有进度",
        "又没有", "很久", "怎么还", "卡住", "更新", "看下",
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

        text = status_table(self.registry.list_all())
        if self.orchestrator:
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
        return (
            f"后台任务 {job_id} 已取消。"
            if await self.runner.cancel(job_id)
            else f"无法取消后台任务: {job_id}"
        )

    def memory_text(self, tail_lines: int = 30) -> str:
        if self.crew_memory is None:
            return ""
        return self.crew_memory.read(tail_lines=tail_lines)

    def reset_text(self, chat_id: int) -> str:
        if self.orchestrator and hasattr(self.orchestrator, "reset_history"):
            self.orchestrator.reset_history(chat_id)
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

    def trace_text(self, task_id: str) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        return self.orchestrator.trace_summary(task_id)

    def artifacts_text(self, task_id: str) -> str:
        if not self.orchestrator:
            return "请先初始化编组。"
        return self.orchestrator.artifacts_summary(task_id)

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
            initial_agent = router.detect_first(message) or router.default_agent()
        task = None
        explicit_task_id = self._extract_task_id(message)
        if explicit_task_id:
            task = self.orchestrator.task_tracker.get(chat_id, explicit_task_id)
        if initial_agent is not None:
            if task is None and self._looks_like_follow_up(message):
                task = self.orchestrator.task_tracker.latest_active_for_assignee(
                    chat_id,
                    initial_agent.name,
                )
        if task is None:
            task = self.orchestrator.task_tracker.create(chat_id, message)
        else:
            task.history.append(
                f"human_follow_up at {datetime.now().isoformat()}: {message[:200]}"
            )
            self.orchestrator.state_store.save_task(chat_id, task)
        run_id = self.orchestrator._new_run_id()

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
