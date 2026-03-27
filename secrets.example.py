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

# ── Gemini CLI  (可选) — 仅在某些 Agent 显式走 gemini 路线时需要 ───────
# 先执行：gemini auth login
GEMINI_CLI_CMD     = ["gemini"]      # CLI 路径，如 ["/usr/local/bin/gemini"]
GEMINI_PROMPT_FLAG = "-p"            # 传 prompt 的 flag；用 stdin 则设为 None
GEMINI_MODEL       = "gemini-2.5-pro" # CLI 内部模型，通过 --model 参数传入

# ── 运行时 ──────────────────────────────────────────────────────────
WORKSPACE_DIR = "."                  # Dev 执行根目录

# ── GitHub Sync（可选，但推荐开启，用于把任务链沟通沉淀到 issue / PR） ───────
GITHUB_SYNC_ENABLED = False
GITHUB_API_URL = "https://api.github.com"
GITHUB_TOKEN = "ghp_YOUR_GITHUB_TOKEN"
GITHUB_REPO = "owner/repo"
GITHUB_ISSUE_LABELS = ["nexuscrew", "task-log"]
GITHUB_ISSUE_TITLE_PREFIX = "NexusCrew"
GITHUB_WEBHOOK_ENABLED = False
GITHUB_WEBHOOK_HOST = "127.0.0.1"
GITHUB_WEBHOOK_PORT = 8788
GITHUB_WEBHOOK_SECRET = "your-webhook-secret"

# ── Slack Sync（可选，企业协作面） ────────────────────────────────────
SLACK_SYNC_ENABLED = False
SLACK_API_URL = "https://slack.com/api"
SLACK_BOT_TOKEN = "xoxb-your-slack-bot-token"
SLACK_DEFAULT_CHANNEL = "C0123456789"
SLACK_TITLE_PREFIX = "NexusCrew"
SLACK_COMMANDS_ENABLED = False
SLACK_COMMANDS_HOST = "127.0.0.1"
SLACK_COMMANDS_PORT = 8789
SLACK_SIGNING_SECRET = "your-slack-signing-secret"
SLACK_APP_HOME_ENABLED = False
SLACK_APP_HOME_USER_IDS: list[str] = []
SLACK_APP_HOME_REFRESH_SECONDS = 0
SLACK_DEFAULT_CHAT_ID = 0

# ── 企业控制面（可选） ───────────────────────────────────────────────
TELEGRAM_OPERATOR_USER_IDS: list[int] = []
TELEGRAM_APPROVER_USER_IDS: list[int] = []
TELEGRAM_ADMIN_USER_IDS: list[int] = []

DASHBOARD_ENABLED = False
DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 8787

AUTO_RECOVER_BACKGROUND_RUNS = False
SYSTEM_NOTIFICATION_CHAT_ID = 0
