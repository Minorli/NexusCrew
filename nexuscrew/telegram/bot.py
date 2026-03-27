"""Telegram bot — handlers and setup."""
import asyncio
import concurrent.futures
import copy
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes,
)

from ..orchestrator import Orchestrator
from ..dashboard.server import DashboardServer
from ..registry import AgentRegistry
from ..config import AgentSpec, CrewConfig, load_crew_config
from ..memory.crew_memory import CrewMemory
from ..memory.project_scanner import ProjectScanner
from ..github_sync import GitHubConversationSync, NullGitHubSync
from ..agents.pm import PMAgent
from ..agents.dev import DevAgent
from ..agents.architect import ArchitectAgent
from ..agents.hr import HRAgent
from ..backends.gemini_cli import GeminiCLIBackend
from ..backends.openai_backend import OpenAIBackend
from ..backends.anthropic_backend import AnthropicBackend
from ..executor.shell import ShellExecutor
from ..git.pr import PRWorkflow
from ..git.webhooks import GitHubWebhookServer
from ..drill import TeamDrillRunner
from ..local_secrets import load_local_secrets
from ..policy.access import AccessController
from ..router import Router
from ..runtime.runner import BackgroundTaskRunner
from ..runtime.recovery import RecoveryManager
from ..skills.registry import SkillRegistry
from ..slack.app_home import SlackAppHomePublisher
from ..slack.server import SlackCommandServer
from ..slack.sync import NullSlackSync, SlackConversationSync
from ..surfaces.service import ChatOpsService
from .dispatcher import AgentBotPool
from .formatter import chunk, status_table

cfg = load_local_secrets()


MODEL_DEFAULTS = {
    "pm": "claude",
    "dev": "codex",
    "architect": "claude",
    "hr": "claude",
}


def _make_backend(model: str, executor: ShellExecutor, spec: dict):
    if model == "gemini":
        return GeminiCLIBackend(
            cfg.GEMINI_CLI_CMD,
            cfg.GEMINI_PROMPT_FLAG,
            model=spec.get("gemini_model") or getattr(cfg, "GEMINI_MODEL", None),
        )
    if model == "codex":
        openai_model = spec.get("openai_model") or cfg.OPENAI_MODEL
        return OpenAIBackend(cfg.OPENAI_API_KEY, cfg.OPENAI_BASE_URL, openai_model)
    if model == "claude":
        anthropic_model = spec.get("anthropic_model") or cfg.ANTHROPIC_MODEL
        anthropic_model_light = (
            spec.get("anthropic_model_light")
            or getattr(cfg, "ANTHROPIC_MODEL_SONNET", None)
        )
        return AnthropicBackend(
            cfg.ANTHROPIC_API_KEY, anthropic_model,
            base_url=getattr(cfg, "ANTHROPIC_BASE_URL", None),
            model_light=anthropic_model_light,
        )
    raise ValueError(f"Unknown model: {model}")


def _make_agent(role: str, name: str, model: str, executor: ShellExecutor,
                extra: str = "", spec: dict | None = None):
    spec = spec or {}
    backend = _make_backend(model, executor, spec)
    if role == "pm":
        return PMAgent(name, backend, extra, model_label=model)
    if role == "dev":
        return DevAgent(name, backend, executor, extra)
    if role == "architect":
        return ArchitectAgent(name, backend, extra)
    if role == "hr":
        return HRAgent(name, backend, extra, model_label=model)
    raise ValueError(f"Unknown role: {role}")


def parse_crew_args(args: list[str]) -> tuple[Path, list[dict]]:
    """
    Parse: <project_path> [role:name[(model)]] ...
    Returns (project_path, [{role, name, model}])
    """
    import re
    if not args:
        raise ValueError("用法: /crew <path> [role:name[(model)]] ...")
    project_dir = Path(args[0]).expanduser()
    specs = []
    for token in args[1:]:
        m = re.fullmatch(r"(\w+):(\w+)(?:\((\w+)\))?", token)
        if not m:
            raise ValueError(f"无法解析 '{token}'，格式应为 role:name 或 role:name(model)")
        role, name, model = m.groups()
        model = model or MODEL_DEFAULTS.get(role)
        if not model:
            raise ValueError(f"未知角色 '{role}'")
        specs.append({"role": role, "name": name, "model": model})
    return project_dir, specs


class NexusCrewBot:
    def __init__(self):
        self.registry  = AgentRegistry()
        self.crew_memory = CrewMemory(Path("./crew_memory.md"))
        self.scanner   = ProjectScanner()
        self._executor: ShellExecutor | None = None
        self._orch: Orchestrator | None = None
        self._app = None
        self._bot_pool: AgentBotPool | None = None
        self.preload_config: CrewConfig | None = None
        self.current_config: CrewConfig | None = None
        self._github_sync = NullGitHubSync()
        self._slack_sync = NullSlackSync()
        self._runner = BackgroundTaskRunner()
        self._skills = SkillRegistry()
        self._status_board_by_chat: dict[int, str] = {}
        self._dashboard: DashboardServer | None = None
        self._github_webhook: GitHubWebhookServer | None = None
        self._slack_commands: SlackCommandServer | None = None
        self._slack_app_home: SlackAppHomePublisher | None = None
        self._slack_home_refresh_task: asyncio.Task | None = None
        self._task_watchdog_task: asyncio.Task | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._startup_warnings: list[str] = []
        self._access = AccessController(
            operator_ids=list(getattr(cfg, "TELEGRAM_OPERATOR_USER_IDS", [])),
            approver_ids=list(getattr(cfg, "TELEGRAM_APPROVER_USER_IDS", [])),
            admin_ids=list(getattr(cfg, "TELEGRAM_ADMIN_USER_IDS", [])),
        )
        self._allowed  = set(cfg.TELEGRAM_ALLOWED_CHAT_IDS)

    def _make_send(self, chat_id: int):
        return lambda text, agent_name=None: self._send_as(chat_id, agent_name, text)

    def _service(self):
        return ChatOpsService(
            registry=self.registry,
            orchestrator=self._orch,
            runner=self._runner,
            executor=self._executor,
            skills=self._skills,
            board_getter=lambda chat_id: self._status_board_by_chat.get(chat_id, "(无状态板)"),
            board_updater=self._update_status_board,
            crew_memory=self.crew_memory,
        )

    def _get_orch(self, project_dir: Path) -> Orchestrator:
        if self._executor is None or self._executor.work_dir != project_dir:
            self._executor = ShellExecutor(project_dir)
        router = Router(self.registry)
        self._orch = Orchestrator(
            self.registry, router, self.crew_memory, self._executor
        )
        return self._orch

    async def _send_as(self, chat_id: int, agent_name: str | None, text: str):
        if self._bot_pool:
            await self._bot_pool.send_as_agent(agent_name, chat_id, text)
            return
        if self._app is None:
            raise RuntimeError("Telegram application is not initialized")
        for part in chunk(text):
            await self._app.bot.send_message(chat_id=chat_id, text=part)

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "NexusCrew 已就绪。\n"
            "用法:\n"
            "  /crew ~/myproject pm:alice dev:bob architect:dave\n"
            "  /load ~/myproject/crew.yaml\n"
            "  @alice 帮我加 Redis 缓存\n"
            "  /status   — 查看当前 Agent\n"
            "  /tasks    — 查看后台任务\n"
            "  /failed   — 查看失败后台任务归档\n"
            "  /approvals — 查看待审批动作\n"
            "  /memory   — 查看共享记忆\n"
            "  /reset    — 清空对话历史\n"
            "  /pause <task_id> / /resume <task_id> / /replay <task_id>\n"
            "  /task <task_id> / /cancel <job_id>\n"
            "  /approve <approval_id> / /reject <approval_id>\n"
            "  /new <desc> / /doctor / /handoff <task_id> <agent> / /skills\n"
            "  /trace <task_id> / /artifacts <task_id> / /pr <task_id> / /ci <task_id> / /board\n"
            "  /drill [team] — 运行内部协作演练并输出验收报告"
        )

    async def _apply_config(self, config: CrewConfig) -> str:
        project_dir = config.project_dir
        if not project_dir.exists():
            raise ValueError(f"路径不存在: {project_dir}")

        briefing = await self.scanner.scan(project_dir)
        self.crew_memory.overwrite_section("项目简报", briefing)

        self.registry.clear()
        executor = ShellExecutor(
            project_dir,
            timeout=config.orchestrator.shell_timeout,
        )
        for spec in config.agents:
            agent = _make_agent(
                spec.role,
                spec.name,
                spec.model,
                executor,
                spec.system_prompt_extra,
                spec=vars(spec),
            )
            self.registry.register(agent)

        self._executor = executor
        self._runner = BackgroundTaskRunner(executor.state_store)
        self._github_sync = self._build_github_sync()
        self._slack_sync = self._build_slack_sync()
        self._orch = Orchestrator(
            self.registry,
            Router(self.registry),
            self.crew_memory,
            executor,
            max_chain_hops=config.orchestrator.max_chain_hops,
            max_dev_retry=config.orchestrator.max_dev_retry,
            agent_heartbeat_seconds=config.orchestrator.agent_heartbeat_seconds,
            agent_max_silence_seconds=config.orchestrator.agent_max_silence_seconds,
            task_stage_sla_seconds=config.orchestrator.task_stage_sla_seconds,
            task_watchdog_interval_seconds=config.orchestrator.task_watchdog_interval_seconds,
            pressure_max_prompt_len=config.hr.pressure_max_prompt_len,
            hr_auto_eval_daily_limit=config.hr.auto_eval_daily_limit,
            github_sync=self._github_sync,
            slack_sync=self._slack_sync,
            pr_workflow=self._build_pr_workflow(),
        )
        self.current_config = copy.deepcopy(config)

        roster = status_table(self.registry.list_all())
        self.crew_memory.overwrite_section("当前编组", roster)
        return roster

    async def _init_from_config(
        self,
        config: CrewConfig,
        update: Update,
    ) -> None:
        # Task 1.1 完成: /crew 与 /load 共用初始化路径。
        if not config.project_dir.exists():
            await update.message.reply_text(f"路径不存在: {config.project_dir}")
            return
        await update.message.reply_text(f"正在扫描项目 {config.project_dir} ...")
        roster = await self._apply_config(config)
        await self._recover_background_runs()
        await update.message.reply_text(
            f"编组完成！\n{roster}\n\n"
            "现在可以 @mention Agent 开始工作。"
        )
        if self._bot_pool:
            missing = await self._bot_pool.validate_group(update.message.chat_id)
            if missing:
                await update.message.reply_text(
                    "以下 Agent Bot 尚未加入群组: " + ", ".join(missing)
                )

    async def _post_init(self, app) -> None:
        del app
        self._loop = asyncio.get_running_loop()
        self._start_slack_home_refresh_if_enabled()
        self._start_task_watchdog_if_enabled()
        if self.preload_config is None:
            return
        # Task 2.4 完成: CLI 支持启动时预加载 crew.yaml。
        await self._apply_config(self.preload_config)
        await self._recover_background_runs()

    async def cmd_crew(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.message.chat_id
        if self._allowed and chat_id not in self._allowed:
            return
        try:
            project_dir, specs = parse_crew_args(context.args or [])
        except ValueError as e:
            await update.message.reply_text(f"错误: {e}")
            return
        config = CrewConfig(
            project_dir=project_dir,
            agents=[
                AgentSpec(
                    role=spec["role"],
                    name=spec["name"],
                    model=spec["model"],
                    system_prompt_extra=spec.get("system_prompt_extra", ""),
                )
                for spec in specs
            ],
        )
        await self._init_from_config(config, update)

    async def cmd_load(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat_id = update.message.chat_id
        if self._allowed and chat_id not in self._allowed:
            return
        if not context.args:
            await update.message.reply_text("用法: /load <crew.yaml 路径>")
            return
        try:
            config = load_crew_config(context.args[0])
        except (FileNotFoundError, ValueError) as e:
            await update.message.reply_text(f"配置加载失败: {e}")
            return
        await self._init_from_config(config, update)

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(self._service().status_text(update.message.chat_id))

    async def cmd_tasks(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(self._service().tasks_text())

    async def cmd_failed(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(self._service().failed_text())

    async def cmd_task(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self._orch:
            await update.message.reply_text("请先初始化编组。")
            return
        if not context.args:
            await update.message.reply_text("用法: /task <task_id>")
            return
        await update.message.reply_text(
            self._service().task_text(update.message.chat_id, context.args[0])
        )

    async def cmd_cancel(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not context.args:
            await update.message.reply_text("用法: /cancel <job_id>")
            return
        await update.message.reply_text(
            await self._service().cancel_job(context.args[0])
        )

    async def cmd_memory(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        n = int(context.args[0]) if context.args else 30
        text = self._service().memory_text(tail_lines=n)
        for part in chunk(text):
            await update.message.reply_text(part)

    async def cmd_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(self._service().reset_text(update.message.chat_id))

    def _user_id(self, update: Update) -> int | None:
        user = getattr(update, "effective_user", None)
        if user is not None:
            return getattr(user, "id", None)
        if update.message and getattr(update.message, "from_user", None):
            return getattr(update.message.from_user, "id", None)
        return None

    async def _require_operate(self, update: Update) -> bool:
        if self._access.can_operate(self._user_id(update)):
            return True
        await update.message.reply_text("无操作权限。")
        return False

    async def _require_approve(self, update: Update) -> bool:
        if self._access.can_approve(self._user_id(update)):
            return True
        await update.message.reply_text("无审批权限。")
        return False

    async def _require_admin(self, update: Update) -> bool:
        if self._access.can_administer(self._user_id(update)):
            return True
        await update.message.reply_text("无管理权限。")
        return False

    async def cmd_pause(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._require_operate(update):
            return
        if not self._orch:
            await update.message.reply_text("请先初始化编组。")
            return
        if not context.args:
            await update.message.reply_text("用法: /pause <task_id>")
            return
        await update.message.reply_text(
            self._service().pause(update.message.chat_id, context.args[0])
        )

    async def cmd_approvals(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._require_approve(update):
            return
        await update.message.reply_text(self._service().approvals_text())

    async def cmd_approve(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._require_approve(update):
            return
        if not context.args:
            await update.message.reply_text("用法: /approve <approval_id>")
            return
        result = await self._service().approve(context.args[0])
        for part in chunk(result):
            await update.message.reply_text(part)

    async def cmd_reject(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._require_approve(update):
            return
        if not context.args:
            await update.message.reply_text("用法: /reject <approval_id>")
            return
        await update.message.reply_text(self._service().reject(context.args[0]))

    async def cmd_new(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._require_operate(update):
            return
        if not self._orch:
            await update.message.reply_text("请先初始化编组。")
            return
        if not context.args:
            await update.message.reply_text("用法: /new <任务描述>")
            return
        await update.message.reply_text(
            await self._service().create_task(update.message.chat_id, " ".join(context.args))
        )

    async def cmd_doctor(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._require_operate(update):
            return
        if not self._orch:
            await update.message.reply_text("请先初始化编组。")
            return
        await update.message.reply_text(self._service().doctor_text(update.message.chat_id))

    async def cmd_handoff(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._require_admin(update):
            return
        if not self._orch:
            await update.message.reply_text("请先初始化编组。")
            return
        if len(context.args) < 2:
            await update.message.reply_text("用法: /handoff <task_id> <agent_name>")
            return
        await update.message.reply_text(
            await self._service().handoff(update.message.chat_id, context.args[0], context.args[1])
        )

    async def cmd_skills(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(self._service().skills_text())

    async def cmd_drill(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._require_operate(update):
            return
        if self.current_config is None or self._orch is None:
            await update.message.reply_text("请先初始化编组。")
            return
        scenario = context.args[0] if context.args else "team"
        chat_id = update.message.chat_id

        async def run_drill():
            runner = TeamDrillRunner(
                self.current_config,
                lambda spec, executor: _make_agent(
                    spec.role,
                    spec.name,
                    spec.model,
                    executor,
                    spec.system_prompt_extra,
                    spec=vars(spec),
                ),
            )
            report = await runner.run(scenario)
            await self._send_as(chat_id, None, report.report_text)

        async def on_error(job, err):
            await self._send_as(chat_id, None, f"⚠️ Drill {scenario} 执行失败：{err}")

        async def on_heartbeat(job):
            await self._send_as(chat_id, None, f"⏳ Drill {scenario} 仍在运行，正在做协作验收。")

        self._runner.submit(
            f"drill:{scenario}",
            run_drill(),
            chat_id=chat_id,
            on_error=on_error,
            on_heartbeat=on_heartbeat,
            first_heartbeat_delay=10,
            heartbeat_interval=30,
        )
        await update.message.reply_text(f"🧪 Drill {scenario} 已开始，完成后会回报告。")

    async def cmd_trace(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._require_operate(update):
            return
        if not self._orch or not context.args:
            await update.message.reply_text("用法: /trace <task_id>")
            return
        await update.message.reply_text(self._service().trace_text(context.args[0]))

    async def cmd_artifacts(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._require_operate(update):
            return
        if not self._orch or not context.args:
            await update.message.reply_text("用法: /artifacts <task_id>")
            return
        await update.message.reply_text(self._service().artifacts_text(context.args[0]))

    async def cmd_pr(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._require_operate(update):
            return
        if not self._orch or not context.args:
            await update.message.reply_text("用法: /pr <task_id>")
            return
        await update.message.reply_text(self._service().pr_text(update.message.chat_id, context.args[0]))

    async def cmd_ci(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._require_operate(update):
            return
        if not self._orch or not context.args:
            await update.message.reply_text("用法: /ci <task_id>")
            return
        await update.message.reply_text(self._service().ci_text(update.message.chat_id, context.args[0]))

    async def cmd_board(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._require_operate(update):
            return
        await update.message.reply_text(await self._service().board_text(update.message.chat_id))

    async def cmd_resume(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._require_operate(update):
            return
        if not self._orch:
            await update.message.reply_text("请先初始化编组。")
            return
        if not context.args:
            await update.message.reply_text("用法: /resume <task_id>")
            return
        task_id = context.args[0]
        chat_id = update.message.chat_id

        async def send(text: str, agent_name: str | None = None):
            await self._send_as(chat_id, agent_name, text)

        await update.message.reply_text(await self._service().resume(chat_id, task_id, send))

    async def cmd_replay(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not await self._require_operate(update):
            return
        if not self._orch:
            await update.message.reply_text("请先初始化编组。")
            return
        if not context.args:
            await update.message.reply_text("用法: /replay <task_id>")
            return
        task_id = context.args[0]
        chat_id = update.message.chat_id

        async def send(text: str, agent_name: str | None = None):
            await self._send_as(chat_id, agent_name, text)

        await update.message.reply_text(await self._service().replay(chat_id, task_id, send))

    async def handle_message(self, update: Update,
                             context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return
        chat_id = update.message.chat_id
        if self._allowed and chat_id not in self._allowed:
            return
        if not self._orch:
            await update.message.reply_text(
                "请先使用 /crew <path> [agents] 初始化编组。")
            return
        msg = update.message.text
        send = self._make_send(chat_id)
        self._service().submit_message(chat_id, msg, send)
        await self._update_status_board(chat_id)

    def build_app(self):
        app = ApplicationBuilder() \
            .token(cfg.TELEGRAM_BOT_TOKEN) \
            .post_init(self._post_init) \
            .build()
        self._app = app
        self._bot_pool = AgentBotPool(cfg.TELEGRAM_BOT_TOKEN)
        self._start_dashboard_if_enabled()
        self._start_github_webhook_if_enabled()
        self._start_slack_commands_if_enabled()
        app.add_handler(CommandHandler("start",  self.cmd_start))
        app.add_handler(CommandHandler("crew",   self.cmd_crew))
        app.add_handler(CommandHandler("load",   self.cmd_load))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(CommandHandler("tasks",  self.cmd_tasks))
        app.add_handler(CommandHandler("failed", self.cmd_failed))
        app.add_handler(CommandHandler("task",   self.cmd_task))
        app.add_handler(CommandHandler("memory", self.cmd_memory))
        app.add_handler(CommandHandler("reset",  self.cmd_reset))
        app.add_handler(CommandHandler("cancel", self.cmd_cancel))
        app.add_handler(CommandHandler("approvals", self.cmd_approvals))
        app.add_handler(CommandHandler("approve", self.cmd_approve))
        app.add_handler(CommandHandler("reject", self.cmd_reject))
        app.add_handler(CommandHandler("new", self.cmd_new))
        app.add_handler(CommandHandler("doctor", self.cmd_doctor))
        app.add_handler(CommandHandler("handoff", self.cmd_handoff))
        app.add_handler(CommandHandler("skills", self.cmd_skills))
        app.add_handler(CommandHandler("drill", self.cmd_drill))
        app.add_handler(CommandHandler("trace", self.cmd_trace))
        app.add_handler(CommandHandler("artifacts", self.cmd_artifacts))
        app.add_handler(CommandHandler("pr", self.cmd_pr))
        app.add_handler(CommandHandler("ci", self.cmd_ci))
        app.add_handler(CommandHandler("board", self.cmd_board))
        app.add_handler(CommandHandler("pause",  self.cmd_pause))
        app.add_handler(CommandHandler("resume", self.cmd_resume))
        app.add_handler(CommandHandler("replay", self.cmd_replay))
        app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, self.handle_message))
        return app

    def _build_github_sync(self):
        enabled = getattr(cfg, "GITHUB_SYNC_ENABLED", False)
        repo = getattr(cfg, "GITHUB_REPO", "")
        token = getattr(cfg, "GITHUB_TOKEN", "")
        if not enabled or not repo or not token:
            return NullGitHubSync()
        return GitHubConversationSync(
            repo=repo,
            token=token,
            api_url=getattr(cfg, "GITHUB_API_URL", "https://api.github.com"),
            labels=list(getattr(cfg, "GITHUB_ISSUE_LABELS", ["nexuscrew", "task-log"])),
            title_prefix=getattr(cfg, "GITHUB_ISSUE_TITLE_PREFIX", "NexusCrew"),
        )

    def _build_pr_workflow(self):
        repo = getattr(cfg, "GITHUB_REPO", "")
        token = getattr(cfg, "GITHUB_TOKEN", "")
        return PRWorkflow(
            repo=repo,
            token=token,
            api_url=getattr(cfg, "GITHUB_API_URL", "https://api.github.com"),
            title_prefix=getattr(cfg, "GITHUB_ISSUE_TITLE_PREFIX", "NexusCrew"),
        )

    def _build_slack_sync(self):
        enabled = getattr(cfg, "SLACK_SYNC_ENABLED", False)
        token = getattr(cfg, "SLACK_BOT_TOKEN", "")
        channel = getattr(cfg, "SLACK_DEFAULT_CHANNEL", "")
        if not enabled or not token or not channel:
            return NullSlackSync()
        return SlackConversationSync(
            token=token,
            default_channel=channel,
            api_url=getattr(cfg, "SLACK_API_URL", "https://slack.com/api"),
            title_prefix=getattr(cfg, "SLACK_TITLE_PREFIX", "NexusCrew"),
        )

    def _build_slack_app_home(self):
        enabled = getattr(cfg, "SLACK_APP_HOME_ENABLED", False)
        token = getattr(cfg, "SLACK_BOT_TOKEN", "")
        if not enabled or not token:
            return None
        return SlackAppHomePublisher(
            token=token,
            api_url=getattr(cfg, "SLACK_API_URL", "https://slack.com/api"),
        )

    async def _update_status_board(self, chat_id: int):
        if not self._orch:
            return
        board = self._build_status_board(chat_id)
        self._status_board_by_chat[chat_id] = board
        await self._publish_slack_home_if_enabled()

    async def _recover_background_runs(self):
        if not self._orch:
            return
        enabled = getattr(cfg, "AUTO_RECOVER_BACKGROUND_RUNS", False)
        if not enabled:
            return
        recovered = await RecoveryManager(self._runner, self._orch).recover(self._make_send)
        notify_chat = getattr(cfg, "SYSTEM_NOTIFICATION_CHAT_ID", 0)
        if notify_chat and recovered:
            await self._send_as(
                notify_chat,
                None,
                "已恢复后台任务: " + ", ".join(recovered),
            )

    def _snapshot(self) -> dict:
        tasks = []
        approvals = []
        doctor = ""
        if self._orch:
            for chat_id, items in getattr(self._orch.task_tracker, "_tasks", {}).items():
                for task in items.values():
                    tasks.append({
                        "chat_id": chat_id,
                        "id": task.id,
                        "status": task.status.value,
                        "assigned_to": task.assigned_to,
                        "github_issue_url": getattr(task, "github_issue_url", ""),
                        "github_pr_url": getattr(task, "github_pr_url", ""),
                        "slack_thread_ts": getattr(task, "slack_thread_ts", ""),
                    })
            if self._executor:
                approvals = [
                    {
                        "id": approval.id,
                        "status": approval.status,
                        "risk_level": approval.risk_level,
                        "summary": approval.summary,
                    }
                    for approval in self._executor.list_pending_approvals()
                ]
            if tasks:
                doctor = self._orch.doctor_report(tasks[0]["chat_id"])
        return {
            "agents": self.registry.list_all(),
            "tasks": tasks,
            "approvals": approvals,
            "background_runs": [
                {"id": run.id, "status": run.status, "label": run.label}
                for run in self._runner.list_runs()
            ],
            "doctor": doctor,
        }

    def _build_status_board(self, chat_id: int) -> str:
        lines = [
            "📌 NexusCrew Status Board",
            "",
            status_table(self.registry.list_all()),
        ]
        if self._startup_warnings:
            lines.extend([
                "",
                "⚠️ Startup Warnings",
                *[f"- {warning}" for warning in self._startup_warnings[-5:]],
            ])
        if self._orch:
            if hasattr(self._orch, "format_status"):
                lines.extend(["", self._orch.format_status(chat_id)])
            lines.extend(["", self._runner.format_status()])
        return "\n".join(lines)

    def _record_startup_warning(self, component: str, err: Exception):
        self._startup_warnings.append(f"{component}: {err}")

    def _start_dashboard_if_enabled(self):
        enabled = getattr(cfg, "DASHBOARD_ENABLED", False)
        if not enabled or self._dashboard is not None:
            return
        self._dashboard = DashboardServer(
            host=getattr(cfg, "DASHBOARD_HOST", "127.0.0.1"),
            port=int(getattr(cfg, "DASHBOARD_PORT", 8787)),
            snapshot_provider=self._snapshot,
            detail_provider=self._dashboard_detail,
        )
        try:
            self._dashboard.start()
        except OSError as err:
            self._record_startup_warning("dashboard", err)
            self._dashboard = None

    def _start_github_webhook_if_enabled(self):
        enabled = getattr(cfg, "GITHUB_WEBHOOK_ENABLED", False)
        if not enabled or self._github_webhook is not None or self._orch is None:
            return
        self._github_webhook = GitHubWebhookServer(
            host=getattr(cfg, "GITHUB_WEBHOOK_HOST", "127.0.0.1"),
            port=int(getattr(cfg, "GITHUB_WEBHOOK_PORT", 8788)),
            secret=getattr(cfg, "GITHUB_WEBHOOK_SECRET", ""),
            handler=self._handle_github_webhook_event,
        )
        try:
            self._github_webhook.start()
        except OSError as err:
            self._record_startup_warning("github_webhook", err)
            self._github_webhook = None

    def _handle_github_webhook_event(self, event_type: str, payload: dict, delivery_id: str):
        if self._orch is None:
            return
        if delivery_id and self._orch.state_store.has_webhook_delivery("github", delivery_id):
            return
        if delivery_id:
            self._orch.state_store.save_webhook_delivery(
                "github",
                delivery_id,
                event_type,
                datetime.now().isoformat(),
            )
        self._orch.ingest_github_event(event_type, payload)

    def _start_slack_commands_if_enabled(self):
        enabled = getattr(cfg, "SLACK_COMMANDS_ENABLED", False)
        if not enabled or self._slack_commands is not None:
            return
        self._slack_commands = SlackCommandServer(
            host=getattr(cfg, "SLACK_COMMANDS_HOST", "127.0.0.1"),
            port=int(getattr(cfg, "SLACK_COMMANDS_PORT", 8789)),
            signing_secret=getattr(cfg, "SLACK_SIGNING_SECRET", ""),
            handler=self._handle_slack_command,
        )
        try:
            self._slack_commands.start()
        except OSError as err:
            self._record_startup_warning("slack_commands", err)
            self._slack_commands = None

    def _handle_slack_command(self, form: dict) -> str:
        text = form.get("text", "").strip()
        command = form.get("command", "")
        if self._orch is None:
            return "NexusCrew 尚未初始化。"
        service = self._service()
        if command.endswith("status"):
            return self._status_board_by_chat.get(0) or self._build_status_board(0)
        if command.endswith("doctor"):
            return service.doctor_text(0)
        if command.endswith("approvals"):
            return service.approvals_text()
        if command.endswith("new"):
            if not text:
                return "用法: /nexus-new <任务描述>"
            chat_id = self._slack_default_chat_id()
            if chat_id is None:
                return "没有可用的默认 chat_id。"
            future = self._schedule_coro(service.create_task(chat_id, text))
            if future and hasattr(future, "result"):
                return future.result()
            return f"已提交创建任务请求: {text}"
        if command.endswith("approve"):
            if not text:
                return "用法: /nexus-approve <approval_id>"
            self._schedule_coro(service.approve(text))
            return f"已提交批准执行: {text}"
        if command.endswith("reject"):
            if not text:
                return "用法: /nexus-reject <approval_id>"
            return service.reject(text)
        if command.endswith("pause"):
            if not text:
                return "用法: /nexus-pause <task_id>"
            chat_id = self._slack_default_chat_id()
            if chat_id is None:
                return "没有可用的默认 chat_id。"
            return service.pause(chat_id, text)
        if command.endswith("replay"):
            if not text:
                return "用法: /nexus-replay <task_id>"
            chat_id = self._slack_default_chat_id()
            if chat_id is None:
                return "没有可用的默认 chat_id。"
            self._schedule_coro(service.replay(chat_id, text, self._make_send(chat_id)))
            return f"已提交重放: {text}"
        if command.endswith("board"):
            return self._status_board_by_chat.get(0) or self._build_status_board(0)
        if command.endswith("task"):
            if not text:
                return "用法: /nexus-task <task_id>"
            for chat_id in self._orch.task_tracker._tasks:
                task = self._orch.task_tracker.get(chat_id, text)
                if task is not None:
                    return service.task_text(chat_id, text)
            return f"未找到任务: {text}"
        return "可用 Slack 命令: /nexus-status /nexus-doctor /nexus-approvals /nexus-task /nexus-new /nexus-approve /nexus-reject /nexus-pause /nexus-replay /nexus-board"

    async def _publish_slack_home_if_enabled(self):
        self._slack_app_home = self._slack_app_home or self._build_slack_app_home()
        if self._slack_app_home is None:
            return
        user_ids = list(getattr(cfg, "SLACK_APP_HOME_USER_IDS", []))
        if not user_ids:
            return
        snapshot = self._snapshot()
        for user_id in user_ids:
            try:
                await self._slack_app_home.publish(user_id, snapshot)
            except Exception:
                continue

    def _schedule_coro(self, coro):
        if self._loop is None:
            return None
        return asyncio.run_coroutine_threadsafe(coro, self._loop)

    def _slack_default_chat_id(self) -> int | None:
        chat_id = int(getattr(cfg, "SLACK_DEFAULT_CHAT_ID", 0))
        if chat_id:
            return chat_id
        if self._orch and self._orch.task_tracker._tasks:
            return next(iter(self._orch.task_tracker._tasks))
        return None

    def _start_slack_home_refresh_if_enabled(self):
        interval = int(getattr(cfg, "SLACK_APP_HOME_REFRESH_SECONDS", 0))
        if interval <= 0 or self._slack_home_refresh_task is not None:
            return

        async def loop():
            while True:
                try:
                    await self._publish_slack_home_if_enabled()
                except Exception:
                    pass
                await asyncio.sleep(interval)

        try:
            self._slack_home_refresh_task = asyncio.create_task(loop())
        except RuntimeError as err:
            self._record_startup_warning("slack_app_home", err)
            self._slack_home_refresh_task = None

    def _start_task_watchdog_if_enabled(self):
        if self._task_watchdog_task is not None:
            return

        async def loop():
            while True:
                try:
                    if self._orch is not None:
                        await self._orch.watchdog_tick(
                            self._make_send,
                            active_task_ids=self._runner.active_task_ids(),
                            notify_chat=False,
                        )
                except Exception:
                    pass
                await asyncio.sleep(15)

        try:
            self._task_watchdog_task = asyncio.create_task(loop())
        except RuntimeError as err:
            self._record_startup_warning("task_watchdog", err)
            self._task_watchdog_task = None

    def _dashboard_detail(self, path: str):
        if self._orch is None:
            return None
        if path.startswith("/tasks/"):
            task_id = path.split("/", 2)[2]
            for chat_id in self._orch.task_tracker._tasks:
                task = self._orch.task_tracker.get(chat_id, task_id)
                if task is not None:
                    return {"task": self._orch.format_task_detail(chat_id, task_id)}
            return {"error": f"task not found: {task_id}"}
        if path.startswith("/artifacts/"):
            task_id = path.split("/", 2)[2]
            return {"artifacts": self._orch.artifacts_summary(task_id)}
        if path.startswith("/trace/"):
            task_id = path.split("/", 2)[2]
            return {"trace": self._orch.trace_summary(task_id)}
        if path.startswith("/runs/"):
            run_id = path.split("/", 2)[2]
            return {"events": self._orch.state_store.list_run_events(run_id)}
        return None
