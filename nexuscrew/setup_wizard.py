"""Local setup wizard for first-run configuration."""
import html
import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from shutil import which
from urllib.parse import parse_qs
import urllib.request

import yaml

from .local_secrets import load_local_secrets


def _truthy(value: str | None) -> bool:
    return value in ("1", "true", "on", "yes")


def _csv_ints(value: str) -> list[int]:
    items = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        items.append(int(part))
    return items


def _csv_strs(value: str) -> list[str]:
    return [part.strip() for part in value.split(",") if part.strip()]


def _parse_agent_specs(specs_text: str) -> list[dict]:
    specs: list[dict] = []
    tokens = []
    for line in specs_text.splitlines():
        tokens.extend(line.split())
    for token in tokens:
        match = re.fullmatch(r"([A-Za-z_]\w*):([\w-]+)(?:\(([\w.-]+)\))?", token)
        if not match:
            raise ValueError(f"无法解析 agent spec: {token}")
        role, name, model = match.groups()
        specs.append({"role": role, "name": name, "model": model or _default_model(role)})
    return specs


def _default_model(role: str) -> str:
    return {
        "pm": "gemini",
        "dev": "codex",
        "architect": "claude",
        "hr": "gemini",
    }.get(role, "gemini")


def build_secrets_py(form: dict[str, str]) -> str:
    payload = {
        "TELEGRAM_BOT_TOKEN": form.get("telegram_bot_token", ""),
        "TELEGRAM_ALLOWED_CHAT_IDS": _csv_ints(form.get("telegram_allowed_chat_ids", "")),
        "AGENT_BOT_TOKENS": {},
        "BOT_USERNAME_MAP": {},
        "OPENAI_API_KEY": form.get("openai_api_key", ""),
        "OPENAI_BASE_URL": form.get("openai_base_url", "https://api.openai.com/v1"),
        "OPENAI_MODEL": form.get("openai_model", "gpt-4.5"),
        "OPENAI_MODEL_HIGH": form.get("openai_model_high", form.get("openai_model", "gpt-4.5")),
        "ANTHROPIC_API_KEY": form.get("anthropic_api_key", ""),
        "ANTHROPIC_BASE_URL": form.get("anthropic_base_url", "https://api.anthropic.com"),
        "ANTHROPIC_MODEL_OPUS": form.get("anthropic_model_opus", "claude-opus-4-6"),
        "ANTHROPIC_MODEL_SONNET": form.get("anthropic_model_sonnet", "claude-sonnet-4-6"),
        "ANTHROPIC_MODEL": form.get("anthropic_model_opus", "claude-opus-4-6"),
        "GEMINI_CLI_CMD": _csv_strs(form.get("gemini_cli_cmd", "gemini")) or ["gemini"],
        "GEMINI_PROMPT_FLAG": form.get("gemini_prompt_flag", "-p") or None,
        "GEMINI_MODEL": form.get("gemini_model", "gemini-2.5-pro"),
        "WORKSPACE_DIR": form.get("workspace_dir", "."),
        "GITHUB_SYNC_ENABLED": _truthy(form.get("github_sync_enabled")),
        "GITHUB_API_URL": form.get("github_api_url", "https://api.github.com"),
        "GITHUB_TOKEN": form.get("github_token", ""),
        "GITHUB_REPO": form.get("github_repo", ""),
        "GITHUB_ISSUE_LABELS": _csv_strs(form.get("github_issue_labels", "nexuscrew,task-log")),
        "GITHUB_ISSUE_TITLE_PREFIX": form.get("github_issue_title_prefix", "NexusCrew"),
        "GITHUB_WEBHOOK_ENABLED": _truthy(form.get("github_webhook_enabled")),
        "GITHUB_WEBHOOK_HOST": form.get("github_webhook_host", "127.0.0.1"),
        "GITHUB_WEBHOOK_PORT": int(form.get("github_webhook_port", "8788")),
        "GITHUB_WEBHOOK_SECRET": form.get("github_webhook_secret", ""),
        "SLACK_SYNC_ENABLED": _truthy(form.get("slack_sync_enabled")),
        "SLACK_API_URL": form.get("slack_api_url", "https://slack.com/api"),
        "SLACK_BOT_TOKEN": form.get("slack_bot_token", ""),
        "SLACK_DEFAULT_CHANNEL": form.get("slack_default_channel", ""),
        "SLACK_TITLE_PREFIX": form.get("slack_title_prefix", "NexusCrew"),
        "SLACK_COMMANDS_ENABLED": _truthy(form.get("slack_commands_enabled")),
        "SLACK_COMMANDS_HOST": form.get("slack_commands_host", "127.0.0.1"),
        "SLACK_COMMANDS_PORT": int(form.get("slack_commands_port", "8789")),
        "SLACK_SIGNING_SECRET": form.get("slack_signing_secret", ""),
        "SLACK_APP_HOME_ENABLED": _truthy(form.get("slack_app_home_enabled")),
        "SLACK_APP_HOME_USER_IDS": _csv_strs(form.get("slack_app_home_user_ids", "")),
        "SLACK_APP_HOME_REFRESH_SECONDS": int(form.get("slack_app_home_refresh_seconds", "0")),
        "SLACK_DEFAULT_CHAT_ID": int(form.get("slack_default_chat_id", "0")),
        "TELEGRAM_OPERATOR_USER_IDS": _csv_ints(form.get("telegram_operator_user_ids", "")),
        "TELEGRAM_APPROVER_USER_IDS": _csv_ints(form.get("telegram_approver_user_ids", "")),
        "TELEGRAM_ADMIN_USER_IDS": _csv_ints(form.get("telegram_admin_user_ids", "")),
        "DASHBOARD_ENABLED": _truthy(form.get("dashboard_enabled")),
        "DASHBOARD_HOST": form.get("dashboard_host", "127.0.0.1"),
        "DASHBOARD_PORT": int(form.get("dashboard_port", "8787")),
        "AUTO_RECOVER_BACKGROUND_RUNS": _truthy(form.get("auto_recover_background_runs")),
        "SYSTEM_NOTIFICATION_CHAT_ID": int(form.get("system_notification_chat_id", "0")),
    }

    lines = [
        "# Generated by NexusCrew setup wizard",
        "# This file is local-only and gitignored.",
    ]
    for key, value in payload.items():
        lines.append(f"{key} = {value!r}")
    return "\n".join(lines) + "\n"


def build_crew_local_yaml(form: dict[str, str]) -> str | None:
    project_dir = form.get("project_dir", "").strip()
    if not project_dir:
        return None
    agents = _parse_agent_specs(form.get("agent_specs", "").strip())
    payload = {
        "project_dir": project_dir,
        "project_prefix": form.get("project_prefix", "nexus"),
        "agents": agents,
        "orchestrator": {
            "max_chain_hops": int(form.get("max_chain_hops", "10")),
            "max_dev_retry": int(form.get("max_dev_retry", "5")),
            "history_window": int(form.get("history_window", "20")),
            "memory_tail_lines": int(form.get("memory_tail_lines", "120")),
            "shell_timeout": int(form.get("shell_timeout", "120")),
        },
        "hr": {
            "eval_per_task": True,
            "summary_interval": 5,
            "pressure_cooldown": 2,
            "pressure_max_prompt_len": 500,
            "pressure_ttl": 3,
            "anomaly_triggers": {
                "dev_retry_threshold": 3,
                "review_reject_threshold": 2,
                "response_time_threshold": 120,
                "chain_hops_threshold": 8,
            },
        },
    }
    return yaml.safe_dump(payload, allow_unicode=True, sort_keys=False)


def save_setup(form: dict[str, str], base_dir: Path) -> None:
    (base_dir / "secrets.py").write_text(build_secrets_py(form), encoding="utf-8")
    crew_yaml = build_crew_local_yaml(form)
    if crew_yaml:
        (base_dir / "crew.local.yaml").write_text(crew_yaml, encoding="utf-8")


def validate_setup(form: dict[str, str], base_dir: Path) -> list[str]:
    issues: list[str] = []
    if not form.get("telegram_bot_token", "").strip():
        issues.append("缺少 TELEGRAM_BOT_TOKEN")
    project_dir = form.get("project_dir", "").strip()
    if project_dir and not Path(project_dir).expanduser().exists():
        issues.append(f"project_dir 不存在: {project_dir}")
    try:
        _parse_agent_specs(form.get("agent_specs", "").strip())
    except Exception as err:
        issues.append(str(err))
    for key in (
        "github_webhook_port",
        "slack_commands_port",
        "dashboard_port",
        "system_notification_chat_id",
        "slack_default_chat_id",
    ):
        value = form.get(key, "").strip()
        if value:
            try:
                int(value)
            except ValueError:
                issues.append(f"{key} 不是整数: {value}")
    try:
        _csv_ints(form.get("telegram_allowed_chat_ids", ""))
        _csv_ints(form.get("telegram_operator_user_ids", ""))
        _csv_ints(form.get("telegram_approver_user_ids", ""))
        _csv_ints(form.get("telegram_admin_user_ids", ""))
    except ValueError as err:
        issues.append(f"ID 列表解析失败: {err}")
    gemini_cmd = _csv_strs(form.get("gemini_cli_cmd", "gemini")) or ["gemini"]
    if not gemini_cmd[0]:
        issues.append("GEMINI_CLI_CMD 不能为空")
    return issues


def _http_json(url: str, headers: dict[str, str] | None = None,
               method: str = "GET", data: dict | None = None) -> dict:
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8") if data is not None else None,
        headers=headers or {},
        method=method,
    )
    with urllib.request.urlopen(req, timeout=8) as resp:
        return json.loads(resp.read().decode("utf-8"))


def run_live_checks(form: dict[str, str], base_dir: Path) -> list[dict]:
    checks: list[dict] = []

    token = form.get("telegram_bot_token", "").strip()
    if token:
        try:
            payload = _http_json(f"https://api.telegram.org/bot{token}/getMe")
            ok = bool(payload.get("ok"))
            checks.append({
                "title": "Telegram Bot",
                "ok": ok,
                "detail": payload.get("result", {}).get("username", "(未返回 username)") if ok else str(payload),
            })
        except Exception as err:
            checks.append({"title": "Telegram Bot", "ok": False, "detail": str(err)})
    else:
        checks.append({"title": "Telegram Bot", "ok": False, "detail": "未填写 token"})

    project_dir = form.get("project_dir", "").strip()
    if project_dir:
        path = Path(project_dir).expanduser()
        checks.append({
            "title": "Project Directory",
            "ok": path.exists(),
            "detail": str(path),
        })

    gemini_cmd = _csv_strs(form.get("gemini_cli_cmd", "gemini")) or ["gemini"]
    checks.append({
        "title": "Gemini CLI",
        "ok": which(gemini_cmd[0]) is not None,
        "detail": gemini_cmd[0],
    })

    github_repo = form.get("github_repo", "").strip()
    github_token = form.get("github_token", "").strip()
    if _truthy(form.get("github_sync_enabled")) and github_repo and github_token:
        try:
            payload = _http_json(
                f"{form.get('github_api_url', 'https://api.github.com').rstrip('/')}/repos/{github_repo}",
                headers={
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {github_token}",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
            checks.append({
                "title": "GitHub Repo Access",
                "ok": payload.get("full_name") == github_repo,
                "detail": payload.get("full_name", str(payload)),
            })
        except Exception as err:
            checks.append({"title": "GitHub Repo Access", "ok": False, "detail": str(err)})

    slack_token = form.get("slack_bot_token", "").strip()
    if _truthy(form.get("slack_sync_enabled")) and slack_token:
        try:
            payload = _http_json(
                f"{form.get('slack_api_url', 'https://slack.com/api').rstrip('/')}/auth.test",
                headers={
                    "Authorization": f"Bearer {slack_token}",
                    "Content-Type": "application/json; charset=utf-8",
                },
                method="POST",
                data={},
            )
            checks.append({
                "title": "Slack Bot",
                "ok": bool(payload.get("ok")),
                "detail": payload.get("user", str(payload)),
            })
        except Exception as err:
            checks.append({"title": "Slack Bot", "ok": False, "detail": str(err)})

    return checks


def run_setup_checks(form: dict[str, str], base_dir: Path) -> list[str]:
    checks: list[str] = []
    token = form.get("telegram_bot_token", "").strip()
    if token:
        if re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", token):
            checks.append("Telegram token 格式看起来有效")
        else:
            checks.append("Telegram token 格式可疑")
    project_dir = form.get("project_dir", "").strip()
    if project_dir:
        checks.append(
            f"Project Dir: {'存在' if Path(project_dir).expanduser().exists() else '不存在'}"
        )
    gemini_cmd = _csv_strs(form.get("gemini_cli_cmd", "gemini")) or ["gemini"]
    checks.append(
        f"Gemini CLI: {'可执行' if which(gemini_cmd[0]) else '未在 PATH 中找到'}"
    )
    github_repo = form.get("github_repo", "").strip()
    if github_repo:
        checks.append(
            f"GitHub Repo 格式: {'有效' if re.fullmatch(r'[^/\\s]+/[^/\\s]+', github_repo) else '无效'}"
        )
    slack_channel = form.get("slack_default_channel", "").strip()
    if slack_channel:
        checks.append(
            f"Slack Channel 格式: {'有效' if re.fullmatch(r'[CDG][A-Z0-9]+', slack_channel) else '无效'}"
        )
    try:
        specs = _parse_agent_specs(form.get("agent_specs", "").strip())
        checks.append(f"Agent Specs: 已解析 {len(specs)} 个 agent")
    except Exception as err:
        checks.append(f"Agent Specs: 解析失败 ({err})")
    return checks


def read_setup_defaults(base_dir: Path) -> dict[str, str]:
    cfg = load_local_secrets(base_dir)
    defaults = {
        "telegram_bot_token": getattr(cfg, "TELEGRAM_BOT_TOKEN", ""),
        "telegram_allowed_chat_ids": ",".join(map(str, getattr(cfg, "TELEGRAM_ALLOWED_CHAT_IDS", []))),
        "openai_api_key": getattr(cfg, "OPENAI_API_KEY", ""),
        "openai_base_url": getattr(cfg, "OPENAI_BASE_URL", "https://api.openai.com/v1"),
        "openai_model": getattr(cfg, "OPENAI_MODEL", "gpt-4.5"),
        "anthropic_api_key": getattr(cfg, "ANTHROPIC_API_KEY", ""),
        "anthropic_base_url": getattr(cfg, "ANTHROPIC_BASE_URL", "https://api.anthropic.com"),
        "anthropic_model_opus": getattr(cfg, "ANTHROPIC_MODEL_OPUS", getattr(cfg, "ANTHROPIC_MODEL", "claude-opus-4-6")),
        "anthropic_model_sonnet": getattr(cfg, "ANTHROPIC_MODEL_SONNET", "claude-sonnet-4-6"),
        "gemini_cli_cmd": ",".join(getattr(cfg, "GEMINI_CLI_CMD", ["gemini"])),
        "gemini_prompt_flag": getattr(cfg, "GEMINI_PROMPT_FLAG", "-p") or "",
        "gemini_model": getattr(cfg, "GEMINI_MODEL", "gemini-2.5-pro"),
        "github_token": getattr(cfg, "GITHUB_TOKEN", ""),
        "github_repo": getattr(cfg, "GITHUB_REPO", ""),
        "github_issue_labels": ",".join(getattr(cfg, "GITHUB_ISSUE_LABELS", ["nexuscrew", "task-log"])),
        "slack_bot_token": getattr(cfg, "SLACK_BOT_TOKEN", ""),
        "slack_default_channel": getattr(cfg, "SLACK_DEFAULT_CHANNEL", ""),
    }
    crew_local = base_dir / "crew.local.yaml"
    if crew_local.exists():
        raw = yaml.safe_load(crew_local.read_text(encoding="utf-8")) or {}
        defaults["project_dir"] = raw.get("project_dir", "")
        defaults["project_prefix"] = raw.get("project_prefix", "nexus")
        defaults["agent_specs"] = "\n".join(
            f"{agent.get('role')}:{agent.get('name')}({agent.get('model')})"
            for agent in raw.get("agents", [])
        )
    return defaults


def launch_nexuscrew(base_dir: Path) -> subprocess.Popen:
    args = [sys.executable, "-m", "nexuscrew", "start"]
    if (base_dir / "crew.local.yaml").exists():
        args.extend(["-c", "crew.local.yaml"])
    return subprocess.Popen(
        args,
        cwd=base_dir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def build_saved_files_summary(base_dir: Path) -> list[str]:
    files = []
    for name in ("secrets.py", "crew.local.yaml"):
        path = base_dir / name
        if path.exists():
            files.append(str(path))
    return files


def render_success_html(
    title: str,
    subtitle: str,
    saved_files: list[str],
    launch_info: dict[str, str] | None = None,
) -> str:
    launch_info = launch_info or {}
    saved_list = "".join(f"<li><code>{html.escape(path)}</code></li>" for path in saved_files) or "<li>(无)</li>"
    launch_block = ""
    if launch_info:
        launch_block = f"""
        <div class="card">
          <h2>运行状态</h2>
          <p><strong>PID:</strong> {html.escape(launch_info.get('pid', ''))}</p>
          <p><strong>状态:</strong> {html.escape(launch_info.get('status', ''))}</p>
          <p><strong>入口:</strong> <code>{html.escape(launch_info.get('command', 'python3 -m nexuscrew'))}</code></p>
        </div>
        """
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>NexusCrew Setup Complete</title>
  <style>
    :root {{
      --bg: #0d1321;
      --panel: #111827;
      --line: #263043;
      --text: #e5ecf4;
      --muted: #9fb2c8;
      --accent: #7dd3fc;
      --accent2: #a7f3d0;
    }}
    body {{ margin: 0; font-family: ui-sans-serif, system-ui, sans-serif; background: radial-gradient(circle at top, #16213b, var(--bg)); color: var(--text); }}
    .wrap {{ max-width: 980px; margin: 48px auto; padding: 0 20px; }}
    .hero {{ background: color-mix(in srgb, var(--panel) 92%, black); border: 1px solid var(--line); border-radius: 24px; padding: 28px; box-shadow: 0 20px 80px rgba(0,0,0,.25); }}
    h1 {{ margin: 0 0 8px; font-size: 36px; }}
    p {{ color: var(--muted); }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; margin-top: 24px; }}
    .card {{ border-radius: 18px; padding: 18px; border: 1px solid var(--line); background: rgba(255,255,255,.03); }}
    code {{ color: var(--accent2); }}
    a {{ color: var(--accent); }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="hero">
      <h1>{html.escape(title)}</h1>
      <p>{html.escape(subtitle)}</p>
      <div class="grid">
        <div class="card">
          <h2>已写入本地文件</h2>
          <ul>{saved_list}</ul>
        </div>
        {launch_block}
        <div class="card">
          <h2>下一步</h2>
          <ul>
            <li>如需重新配置，重新访问 <code>/setup</code></li>
            <li>如使用 Telegram，确认 bot 已加入目标群组</li>
            <li>如启用 GitHub / Slack，同步检查 token 与权限</li>
          </ul>
        </div>
      </div>
    </div>
  </div>
</body>
</html>"""


def write_launch_record(base_dir: Path, pid: int) -> None:
    (base_dir / ".nexus_launch.json").write_text(
        json.dumps({"pid": pid}, ensure_ascii=False),
        encoding="utf-8",
    )


def read_launch_record(base_dir: Path) -> dict | None:
    path = base_dir / ".nexus_launch.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def probe_process(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def build_launch_report(base_dir: Path, pid: int | None = None) -> list[str]:
    if pid is None:
        record = read_launch_record(base_dir)
        pid = record.get("pid") if record else None
    if not pid:
        return ["尚未记录启动信息"]
    running = probe_process(int(pid))
    return [
        f"PID: {pid}",
        f"进程状态: {'运行中' if running else '未运行'}",
        f"secrets.py: {'存在' if (base_dir / 'secrets.py').exists() else '缺失'}",
        f"crew.local.yaml: {'存在' if (base_dir / 'crew.local.yaml').exists() else '缺失'}",
    ]


def render_setup_html(message: str = "", launch_ready: bool = False,
                      defaults: dict[str, str] | None = None,
                      checks: list[str] | None = None,
                      checks_heading: str = "预检结果",
                      launch_report: list[str] | None = None) -> str:
    escaped = html.escape(message)
    defaults = defaults or {}
    checks = checks or []
    launch_report = launch_report or []

    def val(name: str, default: str = "") -> str:
        return html.escape(str(defaults.get(name, default)), quote=True)

    def checked(name: str) -> str:
        return "checked" if _truthy(defaults.get(name)) else ""

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>NexusCrew Setup</title>
  <style>
    :root {{
      --bg: #0d1321;
      --panel: #111827;
      --line: #263043;
      --text: #e5ecf4;
      --muted: #9fb2c8;
      --accent: #7dd3fc;
      --accent2: #a7f3d0;
    }}
    body {{ margin: 0; font-family: ui-sans-serif, system-ui, sans-serif; background: radial-gradient(circle at top, #16213b, var(--bg)); color: var(--text); }}
    .wrap {{ max-width: 1040px; margin: 40px auto; padding: 0 20px; }}
    .panel {{ background: color-mix(in srgb, var(--panel) 92%, black); border: 1px solid var(--line); border-radius: 20px; padding: 24px; box-shadow: 0 20px 80px rgba(0,0,0,0.25); }}
    h1 {{ margin-top: 0; font-size: 34px; }}
    h2 {{ margin-top: 30px; font-size: 20px; color: var(--accent); }}
    p, label {{ color: var(--muted); }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; }}
    .field {{ display: flex; flex-direction: column; gap: 6px; margin-bottom: 14px; }}
    input, textarea {{ border-radius: 12px; border: 1px solid var(--line); background: #0b1220; color: var(--text); padding: 12px 14px; font: inherit; }}
    textarea {{ min-height: 120px; resize: vertical; }}
    .row {{ display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }}
    .submit {{ border: 0; border-radius: 999px; padding: 14px 22px; background: linear-gradient(90deg, var(--accent), var(--accent2)); color: #04111d; font-weight: 700; cursor: pointer; }}
    .secondary {{ border: 1px solid var(--line); border-radius: 999px; padding: 14px 22px; background: transparent; color: var(--text); font-weight: 700; cursor: pointer; }}
    .banner {{ margin-bottom: 16px; padding: 14px 16px; border-radius: 14px; background: rgba(125,211,252,.12); border: 1px solid rgba(125,211,252,.24); color: var(--text); }}
    .checks {{ margin-top: 18px; padding: 14px 16px; border-radius: 14px; background: rgba(167,243,208,.08); border: 1px solid rgba(167,243,208,.24); }}
    .checks ul {{ margin: 8px 0 0; padding-left: 20px; }}
    .launch {{ margin-top: 18px; padding: 14px 16px; border-radius: 14px; background: rgba(125,211,252,.09); border: 1px solid rgba(125,211,252,.28); }}
    .launch ul {{ margin: 8px 0 0; padding-left: 20px; }}
    .wizard {{ display: grid; grid-template-columns: 240px 1fr; gap: 22px; }}
    .steps {{ display: grid; gap: 10px; align-content: start; }}
    .step-pill {{ border: 1px solid var(--line); border-radius: 14px; padding: 12px 14px; color: var(--muted); cursor: pointer; }}
    .step-pill.active {{ border-color: rgba(125,211,252,.35); color: var(--text); background: rgba(125,211,252,.08); }}
    .wizard-step {{ display: none; }}
    .wizard-step.active {{ display: block; }}
    .footer-nav {{ display: flex; gap: 10px; justify-content: flex-end; margin-top: 8px; }}
    .meta {{ font-size: 13px; color: var(--muted); }}
    code {{ color: var(--accent2); }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="panel">
      <h1>NexusCrew First-Run Setup</h1>
      <p>在这个页面里完成首次配置，保存后会自动生成本地 <code>secrets.py</code> 和可选的 <code>crew.local.yaml</code>。下次直接运行 <code>python3 -m nexuscrew</code> 即可。</p>
      {f'<div class="banner">{escaped}</div>' if escaped else ''}
      <form method="post" action="/save">
        <div class="wizard">
          <div class="steps">
            <div class="step-pill active" data-step-target="0">1. Telegram</div>
            <div class="step-pill" data-step-target="1">2. Models</div>
            <div class="step-pill" data-step-target="2">3. Integrations</div>
            <div class="step-pill" data-step-target="3">4. Default Crew</div>
            <div class="step-pill" data-step-target="4">5. Validate & Launch</div>
          </div>
          <div>
            <section class="wizard-step active" data-step="0">
              <h2>Telegram</h2>
              <div class="grid">
                <div class="field"><label>TELEGRAM_BOT_TOKEN</label><input name="telegram_bot_token" value="{val('telegram_bot_token')}" required></div>
                <div class="field"><label>Allowed Chat IDs (comma-separated)</label><input name="telegram_allowed_chat_ids" value="{val('telegram_allowed_chat_ids')}" placeholder="-1001234567890"></div>
                <div class="field"><label>Operator User IDs</label><input name="telegram_operator_user_ids" value="{val('telegram_operator_user_ids')}" placeholder="12345,67890"></div>
                <div class="field"><label>Approver User IDs</label><input name="telegram_approver_user_ids" value="{val('telegram_approver_user_ids')}" placeholder="12345,67890"></div>
                <div class="field"><label>Admin User IDs</label><input name="telegram_admin_user_ids" value="{val('telegram_admin_user_ids')}" placeholder="12345,67890"></div>
                <div class="field"><label>System Notification Chat ID</label><input name="system_notification_chat_id" value="{val('system_notification_chat_id', '0')}"></div>
              </div>
              <div class="footer-nav"><button class="secondary" type="button" data-next-step>下一步</button></div>
            </section>

            <section class="wizard-step" data-step="1">
              <h2>Models</h2>
              <div class="grid">
                <div class="field"><label>OpenAI API Key</label><input name="openai_api_key" value="{val('openai_api_key')}"></div>
                <div class="field"><label>OpenAI Base URL</label><input name="openai_base_url" value="{val('openai_base_url', 'https://api.openai.com/v1')}"></div>
                <div class="field"><label>OpenAI Model</label><input name="openai_model" value="{val('openai_model', 'gpt-4.5')}"></div>
                <div class="field"><label>Anthropic API Key</label><input name="anthropic_api_key" value="{val('anthropic_api_key')}"></div>
                <div class="field"><label>Anthropic Base URL</label><input name="anthropic_base_url" value="{val('anthropic_base_url', 'https://api.anthropic.com')}"></div>
                <div class="field"><label>Anthropic Opus Model</label><input name="anthropic_model_opus" value="{val('anthropic_model_opus', 'claude-opus-4-6')}"></div>
                <div class="field"><label>Anthropic Sonnet Model</label><input name="anthropic_model_sonnet" value="{val('anthropic_model_sonnet', 'claude-sonnet-4-6')}"></div>
                <div class="field"><label>Gemini CLI Command (comma-separated)</label><input name="gemini_cli_cmd" value="{val('gemini_cli_cmd', 'gemini')}"></div>
                <div class="field"><label>Gemini Prompt Flag</label><input name="gemini_prompt_flag" value="{val('gemini_prompt_flag', '-p')}"></div>
                <div class="field"><label>Gemini Model</label><input name="gemini_model" value="{val('gemini_model', 'gemini-2.5-pro')}"></div>
              </div>
              <div class="footer-nav"><button class="secondary" type="button" data-prev-step>上一步</button><button class="secondary" type="button" data-next-step>下一步</button></div>
            </section>

            <section class="wizard-step" data-step="2">
              <h2>Integrations</h2>
              <div class="grid">
                <div class="field"><label><input type="checkbox" name="github_sync_enabled" value="1" {checked('github_sync_enabled')}> Enable GitHub Sync</label></div>
                <div class="field"><label>GitHub Token</label><input name="github_token" value="{val('github_token')}"></div>
                <div class="field"><label>GitHub Repo</label><input name="github_repo" value="{val('github_repo')}" placeholder="owner/repo"></div>
                <div class="field"><label>GitHub Issue Labels</label><input name="github_issue_labels" value="{val('github_issue_labels', 'nexuscrew,task-log')}"></div>
                <div class="field"><label><input type="checkbox" name="slack_sync_enabled" value="1" {checked('slack_sync_enabled')}> Enable Slack Sync</label></div>
                <div class="field"><label>Slack Bot Token</label><input name="slack_bot_token" value="{val('slack_bot_token')}"></div>
                <div class="field"><label>Slack Default Channel</label><input name="slack_default_channel" value="{val('slack_default_channel')}" placeholder="C0123456789"></div>
                <div class="field"><label><input type="checkbox" name="dashboard_enabled" value="1" {checked('dashboard_enabled')}> Enable Dashboard API</label></div>
                <div class="field"><label><input type="checkbox" name="auto_recover_background_runs" value="1" {checked('auto_recover_background_runs')}> Auto Recover Background Runs</label></div>
              </div>
              <div class="footer-nav"><button class="secondary" type="button" data-prev-step>上一步</button><button class="secondary" type="button" data-next-step>下一步</button></div>
            </section>

            <section class="wizard-step" data-step="3">
              <h2>Default Crew</h2>
              <div class="grid">
                <div class="field"><label>Project Directory</label><input name="project_dir" value="{val('project_dir')}" placeholder="~/myproject"></div>
                <div class="field"><label>Project Prefix</label><input name="project_prefix" value="{val('project_prefix', 'nexus')}"></div>
                <div class="field"><label>Agent Specs</label><textarea name="agent_specs">{html.escape(defaults.get('agent_specs', 'pm:nexus-pm-01(gemini)\ndev:nexus-dev-01(codex)\narchitect:nexus-arch-01(claude)\nhr:nexus-hr-01(gemini)'))}</textarea></div>
                <div class="field"><label>Max Chain Hops</label><input name="max_chain_hops" value="{val('max_chain_hops', '10')}"></div>
                <div class="field"><label>Max Dev Retry</label><input name="max_dev_retry" value="{val('max_dev_retry', '5')}"></div>
                <div class="field"><label>History Window</label><input name="history_window" value="{val('history_window', '20')}"></div>
                <div class="field"><label>Memory Tail Lines</label><input name="memory_tail_lines" value="{val('memory_tail_lines', '120')}"></div>
                <div class="field"><label>Shell Timeout</label><input name="shell_timeout" value="{val('shell_timeout', '120')}"></div>
              </div>
              <div class="footer-nav"><button class="secondary" type="button" data-prev-step>上一步</button><button class="secondary" type="button" data-next-step>下一步</button></div>
            </section>

            <section class="wizard-step" data-step="4">
              <h2>Validate & Launch</h2>
              <p class="meta">先做校验，再决定是只保存还是直接启动。</p>
              <div class="row">
                <button class="submit" type="submit">保存配置</button>
                <button class="secondary" type="submit" formaction="/validate">校验配置</button>
                <button class="secondary" type="submit" formaction="/test-connections">测试连接</button>
                {"<button class='secondary' type='submit' formaction='/save-and-launch'>保存并启动</button>" if launch_ready else ""}
                <span>保存后不会提交到 GitHub；配置只保存在本地。</span>
              </div>
              {("<div class='checks'><strong>" + html.escape(checks_heading) + "</strong><ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in checks) + "</ul></div>") if checks else ""}
              {("<div class='launch'><strong>运行状态</strong><ul>" + "".join(f"<li>{html.escape(item)}</li>" for item in launch_report) + "</ul></div>") if launch_report else ""}
              <div class="footer-nav"><button class="secondary" type="button" data-prev-step>上一步</button></div>
            </section>
          </div>
        </div>
      </form>
    </div>
  </div>
  <script>
    (() => {{
      const steps = [...document.querySelectorAll('.wizard-step')];
      const pills = [...document.querySelectorAll('.step-pill')];
      let current = 0;
      const activate = (idx) => {{
        current = Math.max(0, Math.min(idx, steps.length - 1));
        steps.forEach((el, i) => el.classList.toggle('active', i === current));
        pills.forEach((el, i) => el.classList.toggle('active', i === current));
      }};
      document.querySelectorAll('[data-next-step]').forEach(btn => btn.addEventListener('click', () => activate(current + 1)));
      document.querySelectorAll('[data-prev-step]').forEach(btn => btn.addEventListener('click', () => activate(current - 1)));
      pills.forEach((pill, idx) => pill.addEventListener('click', () => activate(idx)));
      activate(0);
    }})();
  </script>
</body>
</html>"""


class SetupWizardServer:
    """Serve a simple local-first setup UI and persist secrets.py/crew.local.yaml."""

    def __init__(self, base_dir: Path, host: str = "127.0.0.1", port: int = 8766):
        self.base_dir = base_dir
        self.host = host
        self.port = port
        self._server = None
        self._thread = None

    def start(self):
        if self._server is not None:
            return
        base_dir = self.base_dir

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path not in ("/", "/setup"):
                    self.send_response(404)
                    self.end_headers()
                    return
                payload = render_setup_html(
                    launch_ready=True,
                    defaults=read_setup_defaults(base_dir),
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def do_POST(self):
                if self.path not in ("/save", "/validate", "/save-and-launch", "/test-connections"):
                    self.send_response(404)
                    self.end_headers()
                    return
                length = int(self.headers.get("Content-Length", "0"))
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(length).decode("utf-8")).items()}
                try:
                    issues = validate_setup(form, base_dir)
                    if issues:
                        raise ValueError("；".join(issues))
                    checks = run_setup_checks(form, base_dir)
                    if self.path == "/validate":
                        payload = render_setup_html(
                            "配置校验通过，可以保存。",
                            launch_ready=True,
                            defaults=form,
                            checks=checks,
                            checks_heading="本地预检结果",
                        ).encode("utf-8")
                        self.send_response(200)
                    elif self.path == "/test-connections":
                        live_checks = run_live_checks(form, base_dir)
                        payload = render_setup_html(
                            "连接测试已完成。",
                            launch_ready=True,
                            defaults=form,
                            checks=[f"{item['title']}: {'通过' if item['ok'] else '失败'} — {item['detail']}" for item in live_checks],
                            checks_heading="连接测试结果",
                        ).encode("utf-8")
                        self.send_response(200)
                    else:
                        save_setup(form, base_dir)
                        launch_note = ""
                        launch_report = []
                        if self.path == "/save-and-launch":
                            proc = launch_nexuscrew(base_dir)
                            write_launch_record(base_dir, proc.pid)
                            launch_note = f" 已启动 NexusCrew（pid={proc.pid}）。"
                            launch_report = build_launch_report(base_dir, proc.pid)
                        payload = render_setup_html(
                            "配置已保存到本地。现在可以关闭这个页面，并直接运行 python3 -m nexuscrew。" + launch_note,
                            launch_ready=True,
                            defaults=form,
                            checks=checks,
                            checks_heading="已保存配置摘要",
                            launch_report=launch_report,
                        ).encode("utf-8")
                        self.send_response(200)
                except Exception as err:
                    payload = render_setup_html(
                        f"保存失败: {err}",
                        launch_ready=True,
                        defaults=form,
                        checks_heading="错误",
                    ).encode("utf-8")
                    self.send_response(400)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format, *args):  # noqa: A003
                return

        self._server = ThreadingHTTPServer((self.host, self.port), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def serve_forever(self):
        self.start()
        assert self._server is not None
        self._server.serve_forever()
