# NexusCrew — 实现交付文档

> **面向对象**：Codex / 实现者。本文档将 DESIGN.md 的架构设计映射为可直接执行的编码任务。
> 每个任务包含：要改的文件、函数签名、依赖关系、验收标准。

---

## 实现总览

### 当前状态

已有代码骨架打通了核心链路：

```
Telegram (bot.py) → Router → Orchestrator → Agent.handle() → Backend.complete()
                                                ↓
                                         ShellExecutor (Dev only)
                                                ↓
                                         crew_memory.md
```

以下功能 **已实现且可工作**（仅需增强，不需重写）：

| 模块 | 文件 | 状态 |
|------|------|------|
| BaseAgent ABC | `agents/base.py` | ✅ 完成 |
| PMAgent | `agents/pm.py` | ✅ 完成 |
| DevAgent | `agents/dev.py` | ✅ 完成 |
| ArchitectAgent | `agents/architect.py` | ✅ 基础完成，需增强 |
| GeminiCLIBackend | `backends/gemini_cli.py` | ✅ 完成 |
| OpenAIBackend | `backends/openai_backend.py` | ⚠️ 可工作，缺错误处理 |
| AnthropicBackend | `backends/anthropic_backend.py` | ⚠️ 可工作，缺 extended thinking |
| AgentRegistry | `registry.py` | ✅ 完成 |
| Router | `router.py` | ⚠️ 缺 HR 别名 |
| Orchestrator | `orchestrator.py` | ⚠️ 核心完成，缺 HR 集成和指标采集 |
| CrewMemory | `memory/crew_memory.py` | ✅ 完成 |
| ProjectScanner | `memory/project_scanner.py` | ✅ 完成 |
| ShellExecutor | `executor/shell.py` | ✅ 完成 |
| TelegramBot | `telegram/bot.py` | ⚠️ 仅单 Bot 模式 |
| Formatter | `telegram/formatter.py` | ✅ 完成 |
| CLI | `cli.py` | ⚠️ 仅单 Bot 模式入口 |

### 依赖关系图

```
Phase 1: Foundation Fixes（无依赖，可并行）
  ├── 1.1 YAML 配置加载器
  ├── 1.2 Router HR 别名
  └── 1.3 Backend 错误处理

Phase 2: Multi-Bot Dispatcher（依赖 1.1）
  ├── 2.1 Dispatcher Bot 架构
  ├── 2.2 Agent Bot 独立发送
  ├── 2.3 单 Bot 降级兼容
  └── 2.4 CLI 入口更新 ──────────── 依赖 1.1 + 2.1

Phase 3: HR Agent（依赖 1.2）
  ├── 3.1 HR Agent 骨架
  ├── 3.2 AgentMetrics 数据采集 ──── 依赖 3.1
  ├── 3.3 HR 评估触发器 ──────────── 依赖 3.1 + 3.2
  ├── 3.4 督促 prompt 注入 ────────── 依赖 3.3
  └── 3.5 懈怠检测 ──────────────── 依赖 3.2

Phase 4: Advanced（依赖 Phase 1-3）
  ├── 4.1 Anthropic extended thinking ── 依赖 1.3
  ├── 4.2 任务状态机 ───────────────── 独立
  ├── 4.3 Git 工作流 ───────────────── 独立
  └── 4.4 指标持久化 ───────────────── 依赖 3.2
```

### 建议执行顺序

```
1.1 → 1.2 → 1.3 → 2.1 → 2.2 → 2.3 → 2.4 → 3.1 → 3.2 → 3.3 → 3.4 → 3.5 → 4.1 → 4.2 → 4.3 → 4.4
      ↑并行↑        ↑────── 可并行 ──────↑                               ↑───── 可并行 ─────↑
```

---

## Phase 1: Foundation Fixes

### Task 1.1 — YAML 配置加载器

**问题**：`crew.example.yaml` 定义了完整配置 schema，但代码中 `/crew` 命令只支持命令行参数 `parse_crew_args()`，没有 YAML 加载路径。

**新建文件**：`nexuscrew/config.py`

```python
"""Configuration loader — reads crew.yaml and returns structured config."""
from pathlib import Path
from dataclasses import dataclass, field
import yaml


@dataclass
class AgentSpec:
    """Single agent specification from YAML."""
    role: str                          # "pm" | "dev" | "architect" | "hr"
    name: str                          # "nexus-dev-01"
    model: str                         # "gemini" | "codex" | "claude"
    system_prompt_extra: str = ""
    # Model-specific overrides (passed to _make_backend)
    gemini_model: str | None = None
    openai_model: str | None = None
    anthropic_model: str | None = None
    anthropic_model_light: str | None = None


@dataclass
class OrchestratorConfig:
    max_chain_hops: int = 10
    max_dev_retry: int = 5
    history_window: int = 20
    memory_tail_lines: int = 120
    shell_timeout: int = 120


@dataclass
class HRConfig:
    eval_per_task: bool = True
    summary_interval: int = 5
    pressure_cooldown: int = 2
    pressure_max_prompt_len: int = 500
    pressure_ttl: int = 3
    anomaly_triggers: dict = field(default_factory=lambda: {
        "dev_retry_threshold": 3,
        "review_reject_threshold": 2,
        "response_time_threshold": 120,
        "chain_hops_threshold": 8,
    })


@dataclass
class CrewConfig:
    """Top-level crew configuration."""
    project_dir: Path
    project_prefix: str = "nexus"
    agents: list[AgentSpec] = field(default_factory=list)
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)
    hr: HRConfig = field(default_factory=HRConfig)


def load_crew_config(path: str | Path) -> CrewConfig:
    """
    Load and validate a crew YAML file.

    Args:
        path: Path to crew.yaml

    Returns:
        CrewConfig with all fields populated

    Raises:
        FileNotFoundError: if YAML file doesn't exist
        ValueError: if required fields are missing or invalid
    """
    path = Path(path)
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not raw or "project_dir" not in raw:
        raise ValueError("crew.yaml must contain 'project_dir'")

    agents = []
    for a in raw.get("agents", []):
        # Validate required fields
        if "role" not in a or "name" not in a or "model" not in a:
            raise ValueError(f"Agent spec missing required fields: {a}")
        agents.append(AgentSpec(
            role=a["role"],
            name=a["name"],
            model=a["model"],
            system_prompt_extra=a.get("system_prompt_extra", ""),
            gemini_model=a.get("gemini_model"),
            openai_model=a.get("openai_model"),
            anthropic_model=a.get("anthropic_model"),
            anthropic_model_light=a.get("anthropic_model_light"),
        ))

    orch_raw = raw.get("orchestrator", {})
    hr_raw = raw.get("hr", {})

    return CrewConfig(
        project_dir=Path(raw["project_dir"]).expanduser(),
        project_prefix=raw.get("project_prefix", "nexus"),
        agents=agents,
        orchestrator=OrchestratorConfig(**{
            k: v for k, v in orch_raw.items()
            if k in OrchestratorConfig.__dataclass_fields__
        }),
        hr=HRConfig(**{
            k: v for k, v in hr_raw.items()
            if k in HRConfig.__dataclass_fields__
        }),
    )
```

**修改文件**：`telegram/bot.py`

在 `NexusCrewBot` 中新增 `/load` 命令处理，与现有 `/crew` 并存：

```python
# 新增 import
from ..config import load_crew_config

# 新增命令处理器
async def cmd_load(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """从 YAML 文件加载编组配置: /load <path_to_crew.yaml>"""
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
    # 复用现有的初始化逻辑
    await self._init_from_config(config, update, context)

async def _init_from_config(self, config, update, context):
    """共用的初始化逻辑 — /crew 和 /load 最终都走这里。"""
    project_dir = config.project_dir
    if not project_dir.exists():
        await update.message.reply_text(f"路径不存在: {project_dir}")
        return
    await update.message.reply_text(f"正在扫描项目 {project_dir} ...")
    briefing = await self.scanner.scan(project_dir)
    self.crew_memory.overwrite_section("项目简报", briefing)
    self.registry.clear()
    executor = ShellExecutor(project_dir)
    for spec in config.agents:
        agent = _make_agent(
            spec.role, spec.name, spec.model, executor,
            spec.system_prompt_extra,
            spec=vars(spec),  # pass full spec dict for model overrides
        )
        self.registry.register(agent)
    self._executor = executor
    self._orch = Orchestrator(
        self.registry, Router(self.registry),
        self.crew_memory, executor,
        max_chain_hops=config.orchestrator.max_chain_hops,
        max_dev_retry=config.orchestrator.max_dev_retry,
    )
    roster = status_table(self.registry.list_all())
    self.crew_memory.overwrite_section("当前编组", roster)
    await update.message.reply_text(
        f"编组完成！\n{roster}\n\n现在可以 @mention Agent 开始工作。"
    )

# 在 build_app() 中注册
app.add_handler(CommandHandler("load", self.cmd_load))
```

**依赖**：无

**验收标准**：
1. `load_crew_config("crew.example.yaml")` 正常解析，返回 `CrewConfig` 对象
2. `/load ~/myproject/crew.yaml` 在 Telegram 中可用，效果与 `/crew` 一致
3. 旧的 `/crew path role:name(model)` 方式继续可用

---

### Task 1.2 — Router 添加 HR 别名

**问题**：`router.py` 的 `ROLE_ALIASES` 没有 `"hr"` 映射，`@hr` 和 `@nexus-hr-01` 无法路由。

**修改文件**：`nexuscrew/router.py`

```python
# 在 ROLE_ALIASES 字典中添加：
ROLE_ALIASES: dict[str, str] = {
    "pm": "pm",
    "dev": "dev",
    "dev_1": "dev",
    "dev_2": "dev",
    "architect": "architect",
    "arch": "architect",
    "hr": "hr",           # ← 新增
}
```

**依赖**：无

**验收标准**：
1. `router.detect_first("@hr 出绩效报告")` 返回 HR Agent 实例
2. `router.detect_first("@nexus-hr-01 评估 dev")` 精确匹配 HR Agent

---

### Task 1.3 — Backend 错误处理与重试

**问题**：`openai_backend.py` 和 `anthropic_backend.py` 没有 retry、rate limit、timeout 处理，生产环境下 API 调用失败会直接抛异常中断整个链路。

**修改文件**：`nexuscrew/backends/openai_backend.py`

```python
"""OpenAI backend — uses openai.OpenAI (sync) with retry and error handling."""
import time
import openai


class OpenAIBackend:
    def __init__(self, api_key: str, base_url: str, model: str,
                 max_retries: int = 3, timeout: int = 120):
        self._client = openai.OpenAI(api_key=api_key, base_url=base_url)
        self.model = model
        self.max_retries = max_retries
        self.timeout = timeout

    def complete(self, messages: list[dict]) -> str:
        """Blocking — run via asyncio.to_thread. Retries on transient errors."""
        last_err = None
        for attempt in range(self.max_retries):
            try:
                resp = self._client.chat.completions.create(
                    model=self.model,
                    messages=messages,
                    timeout=self.timeout,
                )
                return resp.choices[0].message.content
            except openai.RateLimitError as e:
                last_err = e
                wait = min(2 ** attempt * 5, 60)  # 5s, 10s, 20s
                time.sleep(wait)
            except openai.APITimeoutError as e:
                last_err = e
                # Retry immediately on timeout
            except openai.APIError as e:
                last_err = e
                if e.status_code and e.status_code >= 500:
                    time.sleep(2 ** attempt)
                else:
                    break  # Client error, don't retry
        return f"[OpenAI API Error after {self.max_retries} retries] {last_err}"
```

**修改文件**：`nexuscrew/backends/anthropic_backend.py`

```python
"""Anthropic backend — with retry and error handling."""
import time
import anthropic


class AnthropicBackend:
    def __init__(self, api_key: str, model: str,
                 max_tokens: int = 8096, max_retries: int = 3,
                 timeout: int = 120):
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model
        self.max_tokens = max_tokens
        self.max_retries = max_retries
        self.timeout = timeout

    def complete(self, system: str, messages: list[dict]) -> str:
        """Blocking — run via asyncio.to_thread. Retries on transient errors."""
        last_err = None
        for attempt in range(self.max_retries):
            try:
                resp = self._client.messages.create(
                    model=self.model,
                    max_tokens=self.max_tokens,
                    system=system,
                    messages=messages,
                    timeout=self.timeout,
                )
                return resp.content[0].text
            except anthropic.RateLimitError as e:
                last_err = e
                wait = min(2 ** attempt * 5, 60)
                time.sleep(wait)
            except anthropic.APITimeoutError as e:
                last_err = e
            except anthropic.APIError as e:
                last_err = e
                if hasattr(e, 'status_code') and e.status_code and e.status_code >= 500:
                    time.sleep(2 ** attempt)
                else:
                    break
        return f"[Anthropic API Error after {self.max_retries} retries] {last_err}"
```

**依赖**：无

**验收标准**：
1. 在 API key 无效时返回 error string 而非抛异常
2. 在 rate limit 时自动 backoff 重试最多 3 次
3. 原有的 `complete()` 调用签名不变，现有 Agent 代码无需修改

---

## Phase 2: Multi-Bot Dispatcher

> 参考 DESIGN.md Section 13

### Task 2.1 — Dispatcher Bot 架构

**问题**：当前 `telegram/bot.py` 是单 Bot 模式 — 一个 Bot 接收所有消息并代发所有回复。DESIGN.md Section 13 设计了 Dispatcher + N Agent Bot 的多 Bot 架构，但代码中 `AGENT_BOT_TOKENS` 和 `BOT_USERNAME_MAP`（在 `secrets.example.py` 中已定义）完全未使用。

**新建文件**：`nexuscrew/telegram/dispatcher.py`

```python
"""Multi-Bot Dispatcher — receives messages via one bot, sends via per-agent bots."""
from telegram import Bot
import secrets as cfg


class AgentBotPool:
    """
    Manages a pool of Telegram Bot instances, one per agent.
    If an agent has a dedicated bot token, messages are sent via that bot.
    Otherwise, falls back to the dispatcher bot.
    """

    def __init__(self, fallback_token: str):
        self._fallback = Bot(token=fallback_token)
        self._bots: dict[str, Bot] = {}  # agent_name → Bot
        # Initialize from secrets
        for agent_name, token in cfg.AGENT_BOT_TOKENS.items():
            if token:
                self._bots[agent_name] = Bot(token=token)

    def get_bot(self, agent_name: str) -> Bot:
        """Return the dedicated bot for an agent, or fallback."""
        return self._bots.get(agent_name, self._fallback)

    async def send_as_agent(self, agent_name: str, chat_id: int, text: str):
        """Send a message to a chat as a specific agent's bot."""
        bot = self.get_bot(agent_name)
        # Telegram max message length = 4096
        from .formatter import chunk
        for part in chunk(text):
            await bot.send_message(chat_id=chat_id, text=part)

    @property
    def is_multi_bot(self) -> bool:
        """True if at least one agent has a dedicated bot."""
        return len(self._bots) > 0
```

**修改文件**：`nexuscrew/telegram/bot.py`

核心改动：将 `_send()` 方法替换为通过 `AgentBotPool` 发送。

```python
# 在 NexusCrewBot.__init__ 中：
from .dispatcher import AgentBotPool
self._bot_pool: AgentBotPool | None = None

# 在 build_app() 中初始化 pool：
self._bot_pool = AgentBotPool(cfg.TELEGRAM_BOT_TOKEN)

# 修改 _send() 为支持 agent_name：
async def _send_as(self, chat_id: int, agent_name: str, text: str):
    """Send message via agent's dedicated bot, or fallback."""
    if self._bot_pool:
        await self._bot_pool.send_as_agent(agent_name, chat_id, text)
    else:
        # Legacy fallback
        for part in chunk(text):
            await self._app.bot.send_message(chat_id=chat_id, text=part)
```

Orchestrator 的 `send` callback 需要改造 — 当前 `send` 是 `lambda t: self._send(context, chat_id, t)`，需要改为能携带 `agent_name` 的版本。

建议方案：修改 Orchestrator 的 `send` callback 签名：

```python
# 当前（orchestrator.py line 72, 91, 93）：
await send(f"**[{agent.name}]**\n{reply}")

# 改为 — send 接受 (text, agent_name=None)：
await send(f"**[{agent.name}]**\n{reply}", agent_name=agent.name)
```

`bot.py` 中的 `handle_message` 传入的 `send` lambda 相应调整：

```python
send = lambda t, agent_name=None: self._send_as(chat_id, agent_name or "dispatcher", t)
```

**依赖**：无

**验收标准**：
1. 当 `AGENT_BOT_TOKENS` 有值时，每个 Agent 的回复从各自 Bot 发出（在 Telegram 群中显示不同 @username）
2. 当 `AGENT_BOT_TOKENS` 为空时，行为与当前单 Bot 模式完全一致
3. Orchestrator 系统消息（如 "处理中..."、"达到跳转上限"）仍通过 Dispatcher Bot 发送

---

### Task 2.2 — Agent Bot 初始化与群组验证

**修改文件**：`nexuscrew/telegram/dispatcher.py`（扩展 Task 2.1 的文件）

新增群组验证和 Bot 信息缓存：

```python
async def validate_group(self, chat_id: int) -> list[str]:
    """
    检查所有 Agent Bot 是否已被拉入目标群组。
    返回未加入群组的 agent 名称列表。

    调用时机：/crew 或 /load 命令执行后。
    """
    missing = []
    for agent_name, bot in self._bots.items():
        try:
            member = await bot.get_chat_member(chat_id, (await bot.get_me()).id)
            if member.status in ("left", "kicked"):
                missing.append(agent_name)
        except Exception:
            missing.append(agent_name)
    return missing
```

**验收标准**：
1. `/crew` 执行后自动检查所有 Agent Bot 是否在群中
2. 缺失的 Bot 给出提示："以下 Agent Bot 尚未加入群组: ..."

---

### Task 2.3 — 单 Bot 降级模式

**说明**：当 `AGENT_BOT_TOKENS` 为空字典时，系统自动降级为单 Bot 模式。这不需要单独的代码路径 —— `AgentBotPool.get_bot()` 已会返回 fallback。

**验收标准**：
1. 删除 `secrets.py` 中的所有 `AGENT_BOT_TOKENS` 值后，系统正常工作
2. 所有消息从 Dispatcher Bot 统一发出，但消息前缀会显示 `**[nexus-dev-01]**` 标明来源

---

### Task 2.4 — CLI 入口更新

**修改文件**：`nexuscrew/cli.py`

```python
"""CLI entry point: nexuscrew start [--config crew.yaml]"""
import argparse
from pathlib import Path
from .telegram.bot import NexusCrewBot


def main():
    import secrets as cfg

    parser = argparse.ArgumentParser(description="NexusCrew — Multi-Agent Dev Team")
    sub = parser.add_subparsers(dest="command")

    start = sub.add_parser("start", help="启动 Telegram Bot")
    start.add_argument("--config", "-c", type=str, default=None,
                       help="crew.yaml 配置文件路径（可选，也可通过 /crew 或 /load 命令加载）")

    args = parser.parse_args()

    if args.command is None:
        args.command = "start"  # default

    if args.command == "start":
        if not cfg.TELEGRAM_BOT_TOKEN or cfg.TELEGRAM_BOT_TOKEN.startswith("YOUR_"):
            raise SystemExit("请先在 secrets.py 中填写 TELEGRAM_BOT_TOKEN")
        bot = NexusCrewBot()
        if args.config:
            from .config import load_crew_config
            bot.preload_config = load_crew_config(args.config)
        app = bot.build_app()
        print("NexusCrew 启动，监听 Telegram 消息...")
        app.run_polling()


if __name__ == "__main__":
    main()
```

**依赖**：Task 1.1（config.py）

**验收标准**：
1. `nexuscrew start` — 启动空白 Bot，等待 `/crew` 或 `/load` 命令
2. `nexuscrew start -c crew.yaml` — 启动时预加载配置，首条消息即可使用
3. `python -m nexuscrew` 继续可用

---

## Phase 3: HR Agent

> 参考 DESIGN.md Sections 18-21

### Task 3.1 — HR Agent 骨架

**新建文件**：`nexuscrew/agents/hr.py`

```python
"""HR Agent — 技术型管理 HRBP, backed by Gemini CLI."""
import asyncio
from .base import BaseAgent, AgentArtifacts
from ..backends.gemini_cli import GeminiCLIBackend

HR_PROMPT = """\
你是团队的技术型管理 HR（HRBP + 技术总监），代号 {name}。

【核心职责】
1. 绩效评估：基于客观数据对每个 Agent 进行周期性评估，使用 3.25/3.5/3.75 评分体系。
2. 工作督促：监测 Agent 工作状态，发现懈怠时及时干预。
3. 质量监控：追踪代码质量、Review 通过率、Bug 引入率等硬指标。
4. 团队协作评价：评估 Agent 之间的协作效率。
5. 向 Human 汇报：定期输出绩效报告和团队健康度摘要。

【评估原则】
- 用数据说话，不做主观臆断。
- 区分"能力不足"和"态度问题"。
- 绩效结果必须附带改进建议。
- 高绩效要明确表扬，低绩效给出改进路径。

【行为准则】
- 绝不参与技术实现或代码编写。
- 绝不直接修改其他 Agent 的任务分配（建议权，非决策权）。
- 向 Human 汇报时使用结构化格式。
- 重要评估结论在回复末尾加【MEMORY】标记。

【输出规范】
- 督促/干预消息需 @目标Agent 并抄送 @PM。
- 周期性报告自动 @Human。
- 绩效报告使用表格格式。"""


class HRAgent(BaseAgent):
    def __init__(self, name: str, backend: GeminiCLIBackend,
                 system_prompt_extra: str = ""):
        super().__init__(name, "hr", "gemini", system_prompt_extra)
        self.backend = backend

    async def handle(
        self, message: str, history: list[dict], crew_memory: str
    ) -> tuple[str, AgentArtifacts]:
        system = self._build_system(
            HR_PROMPT.format(name=self.name), crew_memory
        )
        history_text = "\n".join(
            f"[{m.get('agent', '?')}]: {m['content'][:400]}"
            for m in history[-12:]  # HR needs more context than other agents
        )
        prompt = f"{system}\n\n【近期对话】\n{history_text}\n\n【当前消息】\n{message}"
        reply = await asyncio.to_thread(self.backend.complete, prompt)
        artifacts = AgentArtifacts()
        if "【MEMORY】" in reply:
            reply, artifacts.memory_note = reply.split("【MEMORY】", 1)
            reply = reply.strip()
            artifacts.memory_note = artifacts.memory_note.strip()
        return reply, artifacts
```

**修改文件**：`nexuscrew/telegram/bot.py`

在 `_make_agent()` 函数中添加 HR 分支：

```python
# 在 _make_agent() 中添加：
from ..agents.hr import HRAgent

if role == "hr":
    return HRAgent(name, backend, extra)
```

在 `MODEL_DEFAULTS` 中添加：

```python
MODEL_DEFAULTS = {"pm": "gemini", "dev": "codex", "architect": "claude", "hr": "gemini"}
```

**依赖**：Task 1.2（Router 需要认识 hr）

**验收标准**：
1. `/crew ~/proj pm:alice dev:bob architect:dave hr:carol` 成功创建包含 HR 的编组
2. `@carol 出绩效报告` 能正确路由到 HR Agent
3. HR Agent 回复中的 `【MEMORY】` 标记正确写入 crew_memory

---

### Task 3.2 — AgentMetrics 数据采集

**新建文件**：`nexuscrew/metrics.py`

```python
"""Agent performance metrics — collection and derived calculations."""
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class AgentMetrics:
    """Per-agent raw metrics, updated after each handle() call."""

    # ── 产出量 ──
    tasks_assigned: int = 0
    tasks_completed: int = 0
    tasks_failed: int = 0

    # ── 效率 ──
    total_response_time_ms: int = 0
    total_retries: int = 0
    escalations: int = 0

    # ── 质量（Dev 专属）──
    review_pass_first: int = 0
    review_reject: int = 0
    shell_commands_run: int = 0
    shell_failures: int = 0

    # ── 协作 ──
    mentions_sent: int = 0
    mentions_received: int = 0
    memory_notes: int = 0

    # ── 时间窗口 ──
    first_active: str = ""
    last_active: str = ""
    active_chains: int = 0

    def record_task_start(self):
        self.tasks_assigned += 1
        now = datetime.now().isoformat()
        if not self.first_active:
            self.first_active = now
        self.last_active = now

    def record_task_complete(self, response_time_ms: int):
        self.tasks_completed += 1
        self.total_response_time_ms += response_time_ms

    def record_task_fail(self):
        self.tasks_failed += 1
        self.total_retries += 1

    def record_shell_run(self, is_failure: bool):
        self.shell_commands_run += 1
        if is_failure:
            self.shell_failures += 1

    def record_review_result(self, passed: bool):
        if passed:
            self.review_pass_first += 1
        else:
            self.review_reject += 1

    def record_memory_note(self):
        self.memory_notes += 1

    # ── Derived metrics ──

    @property
    def completion_rate(self) -> float:
        return self.tasks_completed / max(self.tasks_assigned, 1)

    @property
    def first_pass_rate(self) -> float:
        total = self.review_pass_first + self.review_reject
        return self.review_pass_first / max(total, 1)

    @property
    def avg_response_time_s(self) -> float:
        return (self.total_response_time_ms / 1000) / max(self.tasks_completed, 1)

    @property
    def retry_ratio(self) -> float:
        return self.total_retries / max(self.tasks_completed, 1)

    def to_summary(self) -> str:
        """Human-readable summary for HR Agent context injection."""
        return (
            f"任务: {self.tasks_completed}/{self.tasks_assigned} "
            f"(完成率 {self.completion_rate:.0%}) | "
            f"首次通过: {self.first_pass_rate:.0%} | "
            f"平均响应: {self.avg_response_time_s:.1f}s | "
            f"Retry/任务: {self.retry_ratio:.1f}"
        )


class MetricsCollector:
    """Central metrics store, keyed by agent name."""

    def __init__(self):
        self._metrics: dict[str, AgentMetrics] = {}

    def get(self, agent_name: str) -> AgentMetrics:
        if agent_name not in self._metrics:
            self._metrics[agent_name] = AgentMetrics()
        return self._metrics[agent_name]

    def all_summaries(self) -> str:
        """Format all agent metrics as a summary table for HR."""
        lines = ["| Agent | 完成率 | 首次通过 | 平均响应 | Retry/任务 |",
                 "|-------|--------|---------|---------|-----------|"]
        for name, m in sorted(self._metrics.items()):
            lines.append(
                f"| {name} | {m.completion_rate:.0%} | "
                f"{m.first_pass_rate:.0%} | "
                f"{m.avg_response_time_s:.1f}s | "
                f"{m.retry_ratio:.1f} |"
            )
        return "\n".join(lines)

    def reset(self):
        self._metrics.clear()
```

**修改文件**：`nexuscrew/orchestrator.py`

在 `run_chain()` 的 agent 调用前后插入指标采集：

```python
# __init__ 中新增：
from .metrics import MetricsCollector
self.metrics = MetricsCollector()

# run_chain() 中，调用 agent.handle() 前后：
import time

metrics = self.metrics.get(agent.name)
metrics.record_task_start()
t0 = time.monotonic()

reply, artifacts = await agent.handle(message, history, memory)

elapsed_ms = int((time.monotonic() - t0) * 1000)
metrics.record_task_complete(elapsed_ms)

# 记录 shell 结果
if artifacts.shell_output:
    metrics.record_shell_run(self.executor.is_failure(artifacts.shell_output))

# 记录 memory note
if artifacts.memory_note:
    metrics.record_memory_note()

# 检测 Review 结果（Architect 回复中的 LGTM / reject 信号）
if agent.role == "architect":
    if "LGTM" in reply.upper():
        # 找到被 review 的 dev，记录通过
        # （从 history 中回溯最近的 dev agent）
        for m in reversed(history):
            if self.registry.get_by_name(m.get("agent", "")) \
               and self.registry.get_by_name(m["agent"]).role == "dev":
                self.metrics.get(m["agent"]).record_review_result(passed=True)
                break
    elif any(kw in reply for kw in ("打回", "修复", "reject", "问题")):
        for m in reversed(history):
            if self.registry.get_by_name(m.get("agent", "")) \
               and self.registry.get_by_name(m["agent"]).role == "dev":
                self.metrics.get(m["agent"]).record_review_result(passed=False)
                break
```

**依赖**：Task 3.1

**验收标准**：
1. 每次 `agent.handle()` 调用后，`metrics.get(agent.name)` 的计数器正确递增
2. `metrics.all_summaries()` 返回格式化的 Markdown 表格
3. Dev retry 和 shell failure 被正确记录

---

### Task 3.3 — HR 评估触发器

**修改文件**：`nexuscrew/orchestrator.py`

在 `run_chain()` 的 for 循环结束后（链路完成时）触发 HR 评估：

```python
# run_chain() 末尾，for 循环正常结束后：

# 链路完成，触发 HR 评估（异步，不阻塞）
hr_agent = self.registry.get_by_role("hr")
if hr_agent:
    asyncio.create_task(
        self._hr_evaluate(hr_agent, chat_id, send)
    )

async def _hr_evaluate(self, hr_agent, chat_id, send):
    """HR Agent 异步评估，不阻塞主链路。"""
    try:
        summary = self.metrics.all_summaries()
        history = self._histories.get(chat_id, [])
        memory = self.crew_memory.read()
        eval_prompt = (
            f"请评估以下任务链路中各 Agent 的表现：\n\n"
            f"【团队指标】\n{summary}\n\n"
            f"请使用 3.25/3.5/3.75 评分体系，输出绩效评估报告。"
        )
        reply, artifacts = await hr_agent.handle(eval_prompt, history, memory)
        if artifacts.memory_note:
            self.crew_memory.append(hr_agent.name, artifacts.memory_note)
        await send(f"📊 [{hr_agent.name}] 绩效评估：\n{reply}",
                   agent_name=hr_agent.name)
    except Exception as e:
        await send(f"[HR 评估异常] {e}")
```

**依赖**：Task 3.1 + 3.2

**验收标准**：
1. 每个任务链完成后，HR Agent 自动发送绩效评估
2. 评估是异步的，不影响主链路的响应时间
3. 评估结果通过 HR Agent 的专属 Bot 发送（如果有）

---

### Task 3.4 — 督促 Prompt 注入

**新建文件**：`nexuscrew/hr/pressure.py`

```python
"""Pressure system — HR injects performance context into agent prompts via crew_memory."""
from ..metrics import AgentMetrics
from ..memory.crew_memory import CrewMemory


PRESSURE_LEVELS = {
    0: "normal",      # 绩效 ≥ 3.5, 无额外注入
    1: "reminder",    # 绩效 3.5 但有下降趋势
    2: "warning",     # 绩效 = 3.25
    3: "pip",         # 连续 2 次 3.25
    4: "replacement", # 连续 3 次 3.25 或任何 3.0
}


def calculate_pressure_level(
    current_score: float,
    score_history: list[float],  # 最近 N 次评分
) -> int:
    """根据当前评分和历史趋势计算督促级别。"""
    if current_score >= 3.5:
        # Check for declining trend
        if len(score_history) >= 2 and all(
            score_history[i] > score_history[i+1]
            for i in range(len(score_history)-1)
        ):
            return 1  # Declining trend
        return 0

    if current_score >= 3.25:
        consecutive_low = sum(1 for s in score_history[-3:] if s <= 3.25)
        if consecutive_low >= 3:
            return 4
        if consecutive_low >= 2:
            return 3
        return 2

    return 4  # score < 3.25


def build_pressure_prompt(
    agent_name: str,
    level: int,
    metrics: AgentMetrics,
    peer_feedback: str = "",
    max_len: int = 500,
) -> str:
    """构建督促 prompt，注入到 crew_memory 的 HR通知 section。"""
    if level == 0:
        return "状态正常，继续保持。"

    parts = [f"【HR 绩效通知 — {agent_name}】"]

    if level >= 1:
        parts.append(f"当前指标: {metrics.to_summary()}")

    if level >= 2:
        parts.append(f"\n⚠️ 正式警告：以下指标低于团队标准：")
        if metrics.first_pass_rate < 0.6:
            parts.append(f"- 首次通过率: {metrics.first_pass_rate:.0%}（标准 ≥60%）")
        if metrics.retry_ratio > 2.0:
            parts.append(f"- 平均 retry: {metrics.retry_ratio:.1f}（标准 ≤2.0）")
        if peer_feedback:
            parts.append(f"\n同事反馈: {peer_feedback}")
        parts.append("要求：下一个任务重点关注质量，提交前自行检查。")

    if level >= 3:
        parts.append("\n🚨 绩效改进计划（PIP）已启动：")
        parts.append("- 目标: 首次通过率提升至 60% 以上")
        parts.append("- 评估周期: 接下来 3 个任务")
        parts.append("- 未达标: 向 Human 建议更换模型或调整角色")

    if level >= 4:
        parts.append("\n❌ 已向 Human 提交替换建议。")

    result = "\n".join(parts)
    return result[:max_len]


def apply_pressure(
    crew_memory: CrewMemory,
    agent_name: str,
    level: int,
    metrics: AgentMetrics,
    peer_feedback: str = "",
    max_len: int = 500,
):
    """将督促 prompt 写入 crew_memory 的专用 section。"""
    prompt = build_pressure_prompt(agent_name, level, metrics, peer_feedback, max_len)
    crew_memory.overwrite_section(f"HR通知-{agent_name}", prompt)
```

**依赖**：Task 3.2 + 3.3

**验收标准**：
1. `calculate_pressure_level(3.25, [3.5, 3.25])` 返回 2
2. `calculate_pressure_level(3.25, [3.25, 3.25, 3.25])` 返回 4
3. `apply_pressure()` 写入 crew_memory 后，目标 Agent 下次被调用时能在 context 中看到督促信息
4. 注入的 prompt 不超过 `max_len` 字符

---

### Task 3.5 — 懈怠检测

**新建文件**：`nexuscrew/hr/laziness.py`

```python
"""Laziness detection — heuristic patterns for agent underperformance."""
import re
from difflib import SequenceMatcher


def detect_shallow_response(reply: str) -> bool:
    """模式1: 回复过短或套话过多。"""
    if len(reply.strip()) < 100:
        return True
    filler_phrases = ["好的", "收到", "明白", "了解", "没问题"]
    filler_count = sum(reply.count(p) for p in filler_phrases)
    return filler_count >= 3


def detect_execution_avoidance(reply: str, role: str) -> bool:
    """模式2: Dev 应该写代码但只给文字建议。"""
    if role != "dev":
        return False
    has_code = "```" in reply
    has_suggestion = any(kw in reply for kw in ["建议", "可以考虑", "推荐", "方案"])
    return not has_code and has_suggestion


def detect_stale_retry(current_reply: str, previous_reply: str,
                       threshold: float = 0.85) -> bool:
    """模式3: 重试内容与上次高度相似。"""
    if not previous_reply:
        return False
    ratio = SequenceMatcher(None, current_reply[:500], previous_reply[:500]).ratio()
    return ratio > threshold


def detect_buck_passing(reply: str) -> bool:
    """模式4: 频繁推诿，@多个角色但自身无实质输出。"""
    mentions = re.findall(r"@\w+", reply)
    # 去除 code block 中的 @ (可能是邮箱等)
    code_blocks = re.findall(r"```.*?```", reply, re.DOTALL)
    code_text = " ".join(code_blocks)
    real_mentions = [m for m in mentions if m not in code_text]

    has_own_work = any(kw in reply for kw in [
        "我已", "完成", "实现", "修复", "我认为", "分析", "结论"
    ])
    return len(real_mentions) > 2 and not has_own_work


def detect_all(reply: str, role: str,
               previous_reply: str = "") -> list[str]:
    """
    运行所有检测器，返回触发的模式标签列表。
    空列表 = 正常。
    """
    triggered = []
    if detect_shallow_response(reply):
        triggered.append("shallow_response: 回复敷衍，缺少实质内容")
    if detect_execution_avoidance(reply, role):
        triggered.append("execution_avoidance: Dev 应执行代码而非仅给建议")
    if detect_stale_retry(reply, previous_reply):
        triggered.append("stale_retry: 重试内容与上次高度相似")
    if detect_buck_passing(reply):
        triggered.append("buck_passing: 过度推诿，未尝试自行解决")
    return triggered
```

**集成点**：在 Orchestrator 的 `run_chain()` 中，agent.handle() 返回后调用 `detect_all()`，将结果存入 `AgentMetrics` 或直接传给 HR evaluate。

**依赖**：Task 3.2

**验收标准**：
1. `detect_shallow_response("好的收到明白")` 返回 True
2. `detect_execution_avoidance("建议使用 Redis 做缓存", "dev")` 返回 True
3. `detect_stale_retry("foo bar baz", "foo bar baz")` 返回 True
4. 正常回复不触发任何检测器

---

## Phase 4: Advanced Features

### Task 4.1 — Anthropic Extended Thinking + 双模型切换

> 参考 DESIGN.md Section 14, crew.example.yaml `anthropic_model` / `anthropic_model_light`

**修改文件**：`nexuscrew/backends/anthropic_backend.py`

```python
class AnthropicBackend:
    def __init__(self, api_key: str, model: str,
                 model_light: str | None = None,
                 max_tokens: int = 16000,
                 budget_tokens: int = 10000,
                 max_retries: int = 3, timeout: int = 180):
        self._client = anthropic.Anthropic(api_key=api_key)
        self.model = model                  # e.g. claude-opus-4-6
        self.model_light = model_light      # e.g. claude-sonnet-4-6
        self.max_tokens = max_tokens
        self.budget_tokens = budget_tokens
        self.max_retries = max_retries
        self.timeout = timeout

    def complete(self, system: str, messages: list[dict],
                 use_thinking: bool = False,
                 light_mode: bool = False) -> str:
        """
        Args:
            use_thinking: Enable extended thinking (Opus only)
            light_mode: Use lighter model (Sonnet) for routine reviews
        """
        model = self.model_light if (light_mode and self.model_light) else self.model

        kwargs = dict(
            model=model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            timeout=self.timeout,
        )

        # Extended thinking — only for Opus-class models
        if use_thinking and "opus" in model:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": self.budget_tokens,
            }

        last_err = None
        for attempt in range(self.max_retries):
            try:
                resp = self._client.messages.create(**kwargs)
                # Extract text from response (thinking blocks are separate)
                text_parts = [
                    block.text for block in resp.content
                    if hasattr(block, 'text')
                ]
                return "\n".join(text_parts)
            except anthropic.RateLimitError as e:
                last_err = e
                time.sleep(min(2 ** attempt * 5, 60))
            except anthropic.APIError as e:
                last_err = e
                if hasattr(e, 'status_code') and e.status_code >= 500:
                    time.sleep(2 ** attempt)
                else:
                    break
        return f"[Anthropic API Error] {last_err}"
```

**修改文件**：`nexuscrew/agents/architect.py`

```python
# handle() 方法中，根据消息类型选择模式：
def _should_use_thinking(self, message: str) -> bool:
    """架构级问题用 thinking，常规 review 不用。"""
    heavy_keywords = ["架构", "设计", "安全", "性能", "重构", "迁移", "方案"]
    return any(kw in message for kw in heavy_keywords)

def _should_use_light(self, message: str) -> bool:
    """常规 Code Review 用 Sonnet 节省额度。"""
    light_keywords = ["LGTM", "review", "Review", "检查", "看一下"]
    return any(kw in message for kw in light_keywords)

async def handle(self, message, history, crew_memory):
    system = self._build_system(ARCHITECT_PROMPT, crew_memory)
    messages = [...]  # same as current

    reply = await asyncio.to_thread(
        self.backend.complete,
        system, messages,
        use_thinking=self._should_use_thinking(message),
        light_mode=self._should_use_light(message),
    )
    # ... rest same
```

**修改文件**：`nexuscrew/telegram/bot.py` — `_make_backend()` 传入 `model_light`

```python
if model == "claude":
    anthropic_model = spec.get("anthropic_model", cfg.ANTHROPIC_MODEL)
    anthropic_model_light = spec.get("anthropic_model_light", None)
    return AnthropicBackend(
        cfg.ANTHROPIC_API_KEY,
        anthropic_model,
        model_light=anthropic_model_light,
    )
```

**依赖**：Task 1.3

**验收标准**：
1. Architect 处理 "帮我设计缓存架构" 时使用 Opus + thinking
2. Architect 处理 "Review 一下这个 PR" 时使用 Sonnet（如果配置了 `anthropic_model_light`）
3. 未配置 `model_light` 时全部走主模型
4. `use_thinking=True` 时正确设置 `thinking.budget_tokens`

---

### Task 4.2 — 任务状态机

> 参考 DESIGN.md Section 15

**新建文件**：`nexuscrew/task_state.py`

```python
"""Task state machine — tracks lifecycle of each user request."""
from enum import Enum
from dataclasses import dataclass, field
from datetime import datetime


class TaskStatus(Enum):
    PLANNING = "planning"
    IN_PROGRESS = "in_progress"
    REVIEW_REQ = "review_requested"
    REVIEWING = "reviewing"
    ACCEPTED = "accepted"
    VALIDATING = "validating"
    DONE = "done"
    FAILED = "failed"


# Valid transitions
TRANSITIONS = {
    TaskStatus.PLANNING:    [TaskStatus.IN_PROGRESS],
    TaskStatus.IN_PROGRESS: [TaskStatus.REVIEW_REQ, TaskStatus.FAILED],
    TaskStatus.REVIEW_REQ:  [TaskStatus.REVIEWING],
    TaskStatus.REVIEWING:   [TaskStatus.ACCEPTED, TaskStatus.IN_PROGRESS],  # reject → back to dev
    TaskStatus.ACCEPTED:    [TaskStatus.VALIDATING],
    TaskStatus.VALIDATING:  [TaskStatus.DONE, TaskStatus.IN_PROGRESS],      # PM reject → back
    TaskStatus.DONE:        [],
    TaskStatus.FAILED:      [TaskStatus.PLANNING],  # retry from scratch
}


@dataclass
class Task:
    id: str
    description: str
    status: TaskStatus = TaskStatus.PLANNING
    assigned_to: str = ""        # agent name
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = ""
    history: list[str] = field(default_factory=list)

    def transition(self, new_status: TaskStatus) -> bool:
        """Attempt state transition. Returns True if valid."""
        if new_status in TRANSITIONS.get(self.status, []):
            old = self.status
            self.status = new_status
            self.updated_at = datetime.now().isoformat()
            self.history.append(f"{old.value} → {new_status.value} at {self.updated_at}")
            return True
        return False


class TaskTracker:
    """Tracks all tasks per chat_id."""

    def __init__(self):
        self._tasks: dict[int, dict[str, Task]] = {}  # chat_id → {task_id → Task}
        self._counter: dict[int, int] = {}

    def create(self, chat_id: int, description: str) -> Task:
        self._counter.setdefault(chat_id, 0)
        self._counter[chat_id] += 1
        task_id = f"T-{self._counter[chat_id]:04d}"
        task = Task(id=task_id, description=description)
        self._tasks.setdefault(chat_id, {})[task_id] = task
        return task

    def get(self, chat_id: int, task_id: str) -> Task | None:
        return self._tasks.get(chat_id, {}).get(task_id)

    def list_active(self, chat_id: int) -> list[Task]:
        return [
            t for t in self._tasks.get(chat_id, {}).values()
            if t.status not in (TaskStatus.DONE, TaskStatus.FAILED)
        ]

    def format_status(self, chat_id: int) -> str:
        """Format active tasks as a status board."""
        tasks = self.list_active(chat_id)
        if not tasks:
            return "当前无活跃任务。"
        lines = ["📋 活跃任务：", ""]
        for t in tasks:
            emoji = {
                TaskStatus.PLANNING: "📝",
                TaskStatus.IN_PROGRESS: "🔨",
                TaskStatus.REVIEW_REQ: "📤",
                TaskStatus.REVIEWING: "🔍",
                TaskStatus.ACCEPTED: "✅",
                TaskStatus.VALIDATING: "🏁",
            }.get(t.status, "❓")
            lines.append(f"  {emoji} [{t.id}] {t.description[:50]} → @{t.assigned_to}")
        return "\n".join(lines)
```

**集成思路**：
- PM Agent 在分解任务时，Orchestrator 解析回复中的任务条目并创建 `Task`
- Dev 开始工作 → `IN_PROGRESS`
- Dev 回复 `@architect Review` → `REVIEW_REQ`
- Architect 回复 `LGTM` → `ACCEPTED`
- PM 验收 → `DONE`

状态转换的触发可以通过在 Orchestrator 的 `run_chain()` 中解析 Agent 回复中的关键词来驱动。具体的关键词-状态映射：

```python
STATE_TRIGGERS = {
    "review_request": (["@architect", "Review", "review", "Code Review"], TaskStatus.REVIEW_REQ),
    "lgtm": (["LGTM", "lgtm", "审查通过"], TaskStatus.ACCEPTED),
    "reject": (["打回", "修复", "reject"], TaskStatus.IN_PROGRESS),
    "done": (["验收通过", "DONE", "完成"], TaskStatus.DONE),
}
```

**依赖**：无（独立模块）

**验收标准**：
1. `task.transition(TaskStatus.IN_PROGRESS)` 从 PLANNING 返回 True
2. `task.transition(TaskStatus.DONE)` 从 PLANNING 返回 False（非法跳转）
3. `/status` 命令显示当前活跃任务及其状态

---

### Task 4.3 — Git 工作流集成

> 参考 DESIGN.md Section 16

**修改文件**：`nexuscrew/executor/shell.py`

新增 Git 辅助方法：

```python
class ShellExecutor:
    # ... existing code ...

    async def git_create_branch(self, branch_name: str) -> str:
        """创建并切换到新分支。"""
        return await asyncio.to_thread(
            self._run_one,
            f"git checkout -b {branch_name}"
        )

    async def git_commit(self, message: str) -> str:
        """Stage all changes and commit with conventional format."""
        code = f'git add -A && git commit -m "{message}"'
        return await asyncio.to_thread(self._run_one, code)

    async def git_current_branch(self) -> str:
        """Return current branch name."""
        result = await asyncio.to_thread(
            self._run_one, "git branch --show-current"
        )
        # Extract branch name from output
        for line in result.splitlines():
            if not line.startswith("$"):
                return line.strip()
        return "unknown"
```

**集成思路**：在 Dev Agent 的 system prompt 中已有 git 相关指导。实际的分支创建由 Orchestrator 在任务开始时自动执行：

```python
# Orchestrator 中，当新任务开始时：
if agent.role == "dev" and task:
    branch = f"feat/{task.id.lower()}-{task.description[:20].replace(' ', '-')}"
    await self.executor.git_create_branch(branch)
```

**依赖**：Task 4.2（需要 Task ID 来命名分支）

**验收标准**：
1. Dev 开始新任务时自动创建 `feat/t-0001-xxx` 分支
2. 分支命名符合 conventional commits 规范
3. 现有非 git 项目不受影响（git 命令失败时不中断流程）

---

### Task 4.4 — 指标历史持久化

> 参考 DESIGN.md Section 20.6

**新建文件**：`nexuscrew/hr/metrics_store.py`

```python
"""Metrics history — append-only JSONL for trend analysis."""
import json
from datetime import datetime
from pathlib import Path
from ..metrics import AgentMetrics


class MetricsStore:
    """Persists evaluation snapshots to metrics_history.jsonl."""

    def __init__(self, path: Path):
        self.path = path

    def append_snapshot(self, chain_id: int, agent_name: str,
                        score: float, metrics: AgentMetrics):
        """Append one evaluation record."""
        record = {
            "ts": datetime.now().isoformat(),
            "chain_id": chain_id,
            "agent": agent_name,
            "score": score,
            "completion_rate": metrics.completion_rate,
            "first_pass_rate": metrics.first_pass_rate,
            "avg_response_s": metrics.avg_response_time_s,
            "retry_ratio": metrics.retry_ratio,
            "tasks_completed": metrics.tasks_completed,
        }
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read_history(self, agent_name: str, last_n: int = 10) -> list[dict]:
        """Read last N records for an agent."""
        if not self.path.exists():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            if r["agent"] == agent_name:
                records.append(r)
        return records[-last_n:]

    def get_score_history(self, agent_name: str, last_n: int = 5) -> list[float]:
        """Get recent scores for pressure level calculation."""
        return [r["score"] for r in self.read_history(agent_name, last_n)]
```

**依赖**：Task 3.2

**验收标准**：
1. 每次 HR 评估后，自动追加一条 JSONL 记录到 `metrics_history.jsonl`
2. `get_score_history("nexus-dev-01")` 返回最近 N 次评分列表
3. JSONL 文件可被外部工具（jq, pandas）直接读取

---

## 新建文件清单

| 文件 | Phase | 说明 |
|------|-------|------|
| `nexuscrew/config.py` | 1.1 | YAML 配置加载器 |
| `nexuscrew/telegram/dispatcher.py` | 2.1 | Multi-Bot 发送池 |
| `nexuscrew/agents/hr.py` | 3.1 | HR Agent |
| `nexuscrew/metrics.py` | 3.2 | AgentMetrics + MetricsCollector |
| `nexuscrew/hr/__init__.py` | 3.4 | HR 子包 |
| `nexuscrew/hr/pressure.py` | 3.4 | 督促 prompt 注入 |
| `nexuscrew/hr/laziness.py` | 3.5 | 懈怠检测 |
| `nexuscrew/task_state.py` | 4.2 | 任务状态机 |
| `nexuscrew/hr/metrics_store.py` | 4.4 | 指标历史持久化 |

## 修改文件清单

| 文件 | 涉及 Task | 改动范围 |
|------|-----------|---------|
| `nexuscrew/router.py` | 1.2 | 添加 1 行 alias |
| `nexuscrew/backends/openai_backend.py` | 1.3 | 重写 complete() 加 retry |
| `nexuscrew/backends/anthropic_backend.py` | 1.3 + 4.1 | 重写 complete()，加 thinking/dual-model |
| `nexuscrew/telegram/bot.py` | 1.1, 2.1, 3.1 | 新增 /load 命令, _send_as(), HR 支持 |
| `nexuscrew/cli.py` | 2.4 | argparse 入口 |
| `nexuscrew/orchestrator.py` | 3.2, 3.3 | metrics 采集, HR 评估触发 |
| `nexuscrew/agents/architect.py` | 4.1 | thinking/light mode 选择 |
| `nexuscrew/executor/shell.py` | 4.3 | git 辅助方法 |

## secrets.example.py 更新

无需新增字段 — HR Agent 使用 Gemini CLI（与 PM 共享 OAuth），已有的 `GEMINI_CLI_CMD`、`GEMINI_PROMPT_FLAG`、`GEMINI_MODEL` 足够。`AGENT_BOT_TOKENS` 中需添加 HR bot 的示例注释。

```python
AGENT_BOT_TOKENS: dict[str, str] = {
    # "nexus-pm-01":   "7xxx:AAA...",
    # "nexus-dev-01":  "7xxx:BBB...",
    # "nexus-dev-02":  "7xxx:CCC...",
    # "nexus-arch-01": "7xxx:DDD...",
    # "nexus-hr-01":   "7xxx:EEE...",    # ← 新增
}
```

## pyproject.toml 依赖检查

当前依赖已足够：
- `pyyaml` — YAML 加载（Task 1.1）
- `anthropic` — Anthropic API（已有，Task 4.1 用到 thinking 参数）
- `openai` — OpenAI API（已有）
- `python-telegram-bot` — Telegram Bot（已有，Multi-Bot 用同一个库）

无需新增依赖。

---

## 端到端验证流程

实现完成后，按以下流程验证整个系统：

```
1. 配置 secrets.py（填入真实 token）
2. nexuscrew start -c crew.example.yaml
3. 在 Telegram 群中：
   a. 发送 /status → 看到 5 个 Agent（pm + dev×2 + arch + hr）
   b. 发送 "@nexus-pm-01 帮我写一个 hello world API"
   c. 观察链路：PM 分解 → Dev 实现 → Architect Review → PM 验收
   d. 链路结束后观察 HR 自动发送绩效评估
   e. 发送 "@nexus-hr-01 出绩效报告" → 看到结构化报告
   f. 发送 /memory → 看到 HR 写入的绩效快照
4. 验证 Multi-Bot：每个 Agent 的消息从不同 Bot @username 发出
5. 验证降级：清空 AGENT_BOT_TOKENS 后重启，所有消息从 Dispatcher 发出
```
