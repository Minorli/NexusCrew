"""Telegram bot — handlers and setup."""
import asyncio
from pathlib import Path

from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    filters, ContextTypes,
)

from ..orchestrator import Orchestrator
from ..registry import AgentRegistry
from ..config import AgentSpec, CrewConfig, load_crew_config
from ..memory.crew_memory import CrewMemory
from ..memory.project_scanner import ProjectScanner
from ..agents.pm import PMAgent
from ..agents.dev import DevAgent
from ..agents.architect import ArchitectAgent
from ..agents.hr import HRAgent
from ..backends.gemini_cli import GeminiCLIBackend
from ..backends.openai_backend import OpenAIBackend
from ..backends.anthropic_backend import AnthropicBackend
from ..executor.shell import ShellExecutor
from ..router import Router
from .dispatcher import AgentBotPool
from .formatter import chunk, status_table
import secrets as cfg


MODEL_DEFAULTS = {
    "pm": "gemini",
    "dev": "codex",
    "architect": "claude",
    "hr": "gemini",
}


def _make_backend(model: str, executor: ShellExecutor, spec: dict):
    if model == "gemini":
        return GeminiCLIBackend(
            cfg.GEMINI_CLI_CMD,
            cfg.GEMINI_PROMPT_FLAG,
            model=spec.get("gemini_model", getattr(cfg, "GEMINI_MODEL", None)),
        )
    if model == "codex":
        openai_model = spec.get("openai_model", cfg.OPENAI_MODEL)
        return OpenAIBackend(cfg.OPENAI_API_KEY, cfg.OPENAI_BASE_URL, openai_model)
    if model == "claude":
        anthropic_model = spec.get("anthropic_model", cfg.ANTHROPIC_MODEL)
        anthropic_model_light = spec.get(
            "anthropic_model_light",
            getattr(cfg, "ANTHROPIC_MODEL_SONNET", None),
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
        return PMAgent(name, backend, extra)
    if role == "dev":
        return DevAgent(name, backend, executor, extra)
    if role == "architect":
        return ArchitectAgent(name, backend, extra)
    if role == "hr":
        return HRAgent(name, backend, extra)
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
        self._allowed  = set(cfg.TELEGRAM_ALLOWED_CHAT_IDS)

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
            "  /memory   — 查看共享记忆\n"
            "  /reset    — 清空对话历史"
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
        self._orch = Orchestrator(
            self.registry,
            Router(self.registry),
            self.crew_memory,
            executor,
            max_chain_hops=config.orchestrator.max_chain_hops,
            max_dev_retry=config.orchestrator.max_dev_retry,
            pressure_max_prompt_len=config.hr.pressure_max_prompt_len,
        )

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
        if self.preload_config is None:
            return
        # Task 2.4 完成: CLI 支持启动时预加载 crew.yaml。
        await self._apply_config(self.preload_config)

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
        text = status_table(self.registry.list_all())
        if self._orch:
            text += "\n\n" + self._orch.format_status(update.message.chat_id)
        await update.message.reply_text(text)

    async def cmd_memory(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        n = int(context.args[0]) if context.args else 30
        text = self.crew_memory.read(tail_lines=n)
        for part in chunk(text):
            await update.message.reply_text(part)

    async def cmd_reset(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if self._orch:
            self._orch.reset_history(update.message.chat_id)
        await update.message.reply_text("对话历史已清空。")

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
        send = lambda t, agent_name=None: self._send_as(chat_id, agent_name, t)
        asyncio.create_task(self._orch.run_chain(msg, chat_id, send))

    def build_app(self):
        app = ApplicationBuilder() \
            .token(cfg.TELEGRAM_BOT_TOKEN) \
            .post_init(self._post_init) \
            .build()
        self._app = app
        self._bot_pool = AgentBotPool(cfg.TELEGRAM_BOT_TOKEN)
        app.add_handler(CommandHandler("start",  self.cmd_start))
        app.add_handler(CommandHandler("crew",   self.cmd_crew))
        app.add_handler(CommandHandler("load",   self.cmd_load))
        app.add_handler(CommandHandler("status", self.cmd_status))
        app.add_handler(CommandHandler("memory", self.cmd_memory))
        app.add_handler(CommandHandler("reset",  self.cmd_reset))
        app.add_handler(MessageHandler(
            filters.TEXT & ~filters.COMMAND, self.handle_message))
        return app
