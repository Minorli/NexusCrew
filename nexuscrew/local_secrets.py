"""Load local secrets.py safely without falling back to stdlib secrets."""
from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from types import SimpleNamespace


DEFAULTS = {
    "TELEGRAM_BOT_TOKEN": "",
    "TELEGRAM_ALLOWED_CHAT_IDS": [],
    "AGENT_BOT_TOKENS": {},
    "BOT_USERNAME_MAP": {},
    "OPENAI_API_KEY": "",
    "OPENAI_BASE_URL": "https://api.openai.com/v1",
    "OPENAI_MODEL": "gpt-4.5",
    "OPENAI_MODEL_HIGH": "gpt-4.5",
    "ANTHROPIC_API_KEY": "",
    "ANTHROPIC_BASE_URL": "https://api.anthropic.com",
    "ANTHROPIC_MODEL_OPUS": "claude-opus-4-6",
    "ANTHROPIC_MODEL_SONNET": "claude-sonnet-4-6",
    "ANTHROPIC_MODEL": "claude-opus-4-6",
    "GEMINI_CLI_CMD": ["gemini"],
    "GEMINI_PROMPT_FLAG": "-p",
    "GEMINI_MODEL": "gemini-2.5-pro",
    "WORKSPACE_DIR": ".",
    "GITHUB_SYNC_ENABLED": False,
    "GITHUB_API_URL": "https://api.github.com",
    "GITHUB_TOKEN": "",
    "GITHUB_REPO": "",
    "GITHUB_ISSUE_LABELS": ["nexuscrew", "task-log"],
    "GITHUB_ISSUE_TITLE_PREFIX": "NexusCrew",
    "GITHUB_WEBHOOK_ENABLED": False,
    "GITHUB_WEBHOOK_HOST": "127.0.0.1",
    "GITHUB_WEBHOOK_PORT": 8788,
    "GITHUB_WEBHOOK_SECRET": "",
    "SLACK_SYNC_ENABLED": False,
    "SLACK_API_URL": "https://slack.com/api",
    "SLACK_BOT_TOKEN": "",
    "SLACK_DEFAULT_CHANNEL": "",
    "SLACK_TITLE_PREFIX": "NexusCrew",
    "SLACK_COMMANDS_ENABLED": False,
    "SLACK_COMMANDS_HOST": "127.0.0.1",
    "SLACK_COMMANDS_PORT": 8789,
    "SLACK_SIGNING_SECRET": "",
    "SLACK_APP_HOME_ENABLED": False,
    "SLACK_APP_HOME_USER_IDS": [],
    "SLACK_APP_HOME_REFRESH_SECONDS": 0,
    "SLACK_DEFAULT_CHAT_ID": 0,
    "TELEGRAM_OPERATOR_USER_IDS": [],
    "TELEGRAM_APPROVER_USER_IDS": [],
    "TELEGRAM_ADMIN_USER_IDS": [],
    "DASHBOARD_ENABLED": False,
    "DASHBOARD_HOST": "127.0.0.1",
    "DASHBOARD_PORT": 8787,
    "AUTO_RECOVER_BACKGROUND_RUNS": False,
    "SYSTEM_NOTIFICATION_CHAT_ID": 0,
}


def load_local_secrets(base_dir: Path | None = None):
    base_dir = base_dir or Path.cwd()
    path = base_dir / "secrets.py"
    cfg = SimpleNamespace(**DEFAULTS)
    if not path.exists():
        return cfg

    spec = spec_from_file_location("nexuscrew_local_secrets", path)
    if spec is None or spec.loader is None:
        return cfg

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    for key in DEFAULTS:
        if hasattr(module, key):
            setattr(cfg, key, getattr(module, key))
    return cfg
