"""Shared command service for Telegram / Slack surfaces."""


class ChatOpsService:
    """Surface-agnostic command layer."""

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
        task = self.orchestrator.task_tracker.create(chat_id, message)
        run_id = self.orchestrator._new_run_id()
        return self.runner.submit(
            message,
            self.orchestrator.run_chain(message, chat_id, send, run_id=run_id, task=task),
            chat_id=chat_id,
            task_id=task.id,
            run_id=run_id,
        )
