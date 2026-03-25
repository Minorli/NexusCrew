# secrets.example.py — 配置模板
# cp secrets.example.py secrets.py，然后填入真实值
# secrets.py 已在 .gitignore，切勿提交

# ── Telegram ───────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
TELEGRAM_ALLOWED_CHAT_IDS: list[int] = []  # 空列表=接受所有，生产建议填白名单

# 多 Bot 模式：每个 Agent 独立 @username（可选，留空则由 Dispatcher 代发）
AGENT_BOT_TOKENS: dict[str, str] = {
    # "nexus-pm-01":   "7xxx:AAA...",
    # "nexus-dev-01":  "7xxx:BBB...",
    # "nexus-dev-02":  "7xxx:CCC...",
    # "nexus-arch-01": "7xxx:DDD...",
}
# Bot @username → Agent 名字映射
BOT_USERNAME_MAP: dict[str, str] = {
    # "nexus_pm_01_bot":   "nexus-pm-01",
    # "nexus_dev_01_bot":  "nexus-dev-01",
    # "nexus_dev_02_bot":  "nexus-dev-02",
    # "nexus_arch_01_bot": "nexus-arch-01",
}

# ── OpenAI / Codex  (Dev Agent: nexus-dev-XX) ──────────────────────
OPENAI_API_KEY  = "sk-YOUR_OPENAI_KEY"
OPENAI_BASE_URL = "https://api.openai.com/v1"
OPENAI_MODEL    = "gpt-4.5"          # 默认；推荐高质量任务用 gpt-4.5-xhigh
OPENAI_MODEL_HIGH = "gpt-4.5"        # xhigh effort 变体，按需切换

# ── Anthropic / Claude  (Architect Agent: nexus-arch-XX) ───────────
ANTHROPIC_API_KEY  = "sk-ant-YOUR_ANTHROPIC_KEY"
ANTHROPIC_BASE_URL = "https://api.anthropic.com"  # 自定义中转地址填这里
# 首选带 extended thinking 的 Opus；Sonnet 作为轻量审查降级选项
ANTHROPIC_MODEL_OPUS   = "claude-opus-4-6"          # Code Review / 重大决策
ANTHROPIC_MODEL_SONNET = "claude-sonnet-4-6"        # 轻量审查降级
ANTHROPIC_MODEL        = ANTHROPIC_MODEL_OPUS        # 当前使用的默认（bot.py 读取此变量）

# ── Gemini CLI  (PM/PO Agent: nexus-pm-XX) — OAuth 登录，无 API key ─
# 先执行：gemini auth login
GEMINI_CLI_CMD     = ["gemini"]      # CLI 路径，如 ["/usr/local/bin/gemini"]
GEMINI_PROMPT_FLAG = "-p"            # 传 prompt 的 flag；用 stdin 则设为 None
GEMINI_MODEL       = "gemini-2.5-pro" # CLI 内部模型，通过 --model 参数传入

# ── 运行时 ──────────────────────────────────────────────────────────
WORKSPACE_DIR = "."                  # Dev 执行根目录
