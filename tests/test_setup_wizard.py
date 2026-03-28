"""Tests for local secrets loader and setup wizard persistence."""
from pathlib import Path

from nexuscrew.local_secrets import load_local_secrets
from nexuscrew.setup_wizard import (
    _parse_agent_bot_specs,
    build_launch_report,
    build_crew_local_yaml,
    build_secrets_py,
    build_saved_files_summary,
    launch_nexuscrew,
    probe_process,
    read_launch_record,
    render_console_html,
    render_success_html,
    render_setup_html,
    run_live_checks,
    save_setup,
    validate_setup,
    write_launch_record,
)


def test_load_local_secrets_uses_defaults_when_missing(tmp_path: Path):
    cfg = load_local_secrets(tmp_path)
    assert cfg.TELEGRAM_BOT_TOKEN == ""
    assert cfg.GEMINI_CLI_CMD == ["gemini"]


def test_setup_wizard_builds_secrets_and_crew(tmp_path: Path):
    form = {
        "telegram_bot_token": "123:abc",
        "agent_bot_specs": "nexus-pm-01|123:AAA|nexus_pm_01_bot",
        "openai_api_key": "sk-test",
        "anthropic_api_key": "sk-ant-test",
        "project_dir": "/tmp/project",
        "project_prefix": "nexus",
        "agent_specs": "pm:nexus-pm-01(claude)\ndev:nexus-dev-01(codex)\narchitect:nexus-arch-01(claude)\nhr:nexus-hr-01(claude)\nqa:nexus-qa-01(claude)",
    }

    secrets_text = build_secrets_py(form)
    crew_yaml = build_crew_local_yaml(form)

    assert "TELEGRAM_BOT_TOKEN = '123:abc'" in secrets_text
    assert "'nexus-pm-01': '123:AAA'" in secrets_text
    assert "'nexus_pm_01_bot': 'nexus-pm-01'" in secrets_text
    assert "OPENAI_API_KEY = 'sk-test'" in secrets_text
    assert "project_dir: /tmp/project" in crew_yaml
    assert "role: pm" in crew_yaml


def test_parse_agent_bot_specs_and_write_mappings():
    tokens, usernames = _parse_agent_bot_specs(
        "nexus-pm-01|123:AAA|nexus_pm_01_bot\nnexus-dev-01|456:BBB|nexus_dev_01_bot"
    )
    assert tokens == {
        "nexus-pm-01": "123:AAA",
        "nexus-dev-01": "456:BBB",
    }
    assert usernames == {
        "nexus_pm_01_bot": "nexus-pm-01",
        "nexus_dev_01_bot": "nexus-dev-01",
    }


def test_structured_agent_bot_rows_write_mappings():
    form = {
        "telegram_bot_token": "123:abc",
        "agent_bot_name_1": "nexus-pm-01",
        "agent_bot_token_1": "123:AAA",
        "agent_bot_username_1": "nexus_pm_01_bot",
    }

    secrets_text = build_secrets_py(form)

    assert "'nexus-pm-01': '123:AAA'" in secrets_text
    assert "'nexus_pm_01_bot': 'nexus-pm-01'" in secrets_text


def test_save_setup_writes_local_files(tmp_path: Path):
    form = {
        "telegram_bot_token": "123:abc",
        "project_dir": "/tmp/project",
        "agent_specs": "pm:nexus-pm-01(claude)",
    }

    save_setup(form, tmp_path)

    assert (tmp_path / "secrets.py").exists()
    assert (tmp_path / "crew.local.yaml").exists()


def test_validate_setup_catches_missing_or_invalid_fields(tmp_path: Path):
    issues = validate_setup(
        {
            "telegram_bot_token": "",
            "project_dir": "/definitely/missing",
            "agent_specs": "bad spec",
            "telegram_allowed_chat_ids": "foo",
        },
        tmp_path,
    )

    assert issues
    assert any("TELEGRAM_BOT_TOKEN" in issue for issue in issues)


def test_validate_setup_allows_blank_gemini_when_unused(tmp_path: Path):
    project_dir = tmp_path / "project"
    project_dir.mkdir()

    issues = validate_setup(
        {
            "telegram_bot_token": "123:abc",
            "project_dir": str(project_dir),
            "agent_specs": "pm:nexus-pm-01(claude)\ndev:nexus-dev-01(codex)\nhr:nexus-hr-01(claude)",
            "gemini_cli_cmd": "",
        },
        tmp_path,
    )

    assert not any("GEMINI_CLI_CMD" in issue for issue in issues)


def test_build_crew_local_yaml_keeps_qa_as_claude():
    crew_yaml = build_crew_local_yaml({
        "project_dir": "/tmp/project",
        "agent_specs": "qa:nexus-qa-01",
    })

    assert "role: qa" in crew_yaml
    assert "model: claude" in crew_yaml


def test_launch_nexuscrew_uses_local_yaml_when_present(tmp_path: Path, monkeypatch):
    seen: dict[str, object] = {}
    (tmp_path / "crew.local.yaml").write_text("project_dir: /tmp/project\nagents: []\n", encoding="utf-8")

    class FakeProc:
        pid = 12345

    def fake_popen(args, cwd, stdout, stderr, stdin, start_new_session):
        seen["args"] = args
        seen["cwd"] = cwd
        return FakeProc()

    monkeypatch.setattr("nexuscrew.setup_wizard.subprocess.Popen", fake_popen)

    proc = launch_nexuscrew(tmp_path)

    assert proc.pid == 12345
    assert seen["args"][-2:] == ["-c", "crew.local.yaml"]


def test_launch_record_and_report(tmp_path: Path, monkeypatch):
    write_launch_record(tmp_path, 4321)
    monkeypatch.setattr("nexuscrew.setup_wizard.probe_process", lambda pid: pid == 4321)

    assert read_launch_record(tmp_path) == {"pid": 4321}
    report = build_launch_report(tmp_path)
    assert any("PID: 4321" in line for line in report)
    assert any("运行中" in line for line in report)
    assert build_saved_files_summary(tmp_path) == []


def test_read_setup_defaults_includes_local_values(tmp_path: Path):
    (tmp_path / "secrets.py").write_text(
        "TELEGRAM_BOT_TOKEN='123:abc'\n"
        "GITHUB_SYNC_ENABLED=True\n"
        "GITHUB_REPO='owner/repo'\n"
        "SLACK_SYNC_ENABLED=True\n"
        "SLACK_DEFAULT_CHANNEL='C0123456789'\n"
        "AGENT_BOT_TOKENS={'nexus-pm-01':'123:AAA'}\n"
        "BOT_USERNAME_MAP={'nexus_pm_01_bot':'nexus-pm-01'}\n",
        encoding="utf-8",
    )
    (tmp_path / "crew.local.yaml").write_text(
        "project_dir: /tmp/project\n"
        "project_prefix: nexus\n"
        "agents:\n"
        "  - role: pm\n"
        "    name: nexus-pm-01\n"
        "    model: claude\n"
        "orchestrator:\n"
        "  max_chain_hops: 12\n",
        encoding="utf-8",
    )

    from nexuscrew.setup_wizard import read_setup_defaults

    defaults = read_setup_defaults(tmp_path)

    assert defaults["telegram_bot_token"] == "123:abc"
    assert defaults["github_sync_enabled"] == "1"
    assert defaults["slack_sync_enabled"] == "1"
    assert "nexus-pm-01|123:AAA|nexus_pm_01_bot" in defaults["agent_bot_specs"]
    assert defaults["max_chain_hops"] == "12"


def test_run_live_checks_uses_helpers(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("nexuscrew.setup_wizard.which", lambda cmd: "/usr/bin/gemini")
    calls = []

    def fake_http_json(url, headers=None, method="GET", data=None):
        calls.append((url, method))
        if "telegram" in url:
            return {"ok": True, "result": {"username": "nexus_bot"}}
        if "api.github.com" in url:
            return {"full_name": "owner/repo"}
        return {"ok": True, "user": "slack-bot"}

    monkeypatch.setattr("nexuscrew.setup_wizard._http_json", fake_http_json)

    checks = run_live_checks({
        "telegram_bot_token": "123456:abcdefghijklmnopqrstuvwxyz123456",
        "project_dir": str(tmp_path),
        "gemini_cli_cmd": "gemini",
        "github_sync_enabled": "1",
        "github_token": "ghp_test",
        "github_repo": "owner/repo",
        "slack_sync_enabled": "1",
        "slack_bot_token": "xoxb-test",
        "slack_default_channel": "C0123456789",
    }, tmp_path)

    assert any("Telegram Bot" == item["title"] for item in checks)
    assert any("GitHub Repo Access" == item["title"] for item in checks)
    assert any("Slack Bot" == item["title"] for item in checks)
    assert calls


def test_render_setup_html_includes_stepper_and_checks():
    html = render_setup_html(
        message="ok",
        launch_ready=True,
        defaults={"telegram_bot_token": "123:abc"},
        checks=["Telegram token 格式看起来有效"],
        checks_heading="连接测试结果",
        launch_report=["PID: 1234"],
    )

    assert "step-pill" in html
    assert "测试连接" in html
    assert "连接测试结果" in html
    assert "123:abc" in html
    assert "运行状态" in html
    assert "qa:nexus-qa-01(claude)" in html


def test_render_success_and_console_html(tmp_path: Path):
    (tmp_path / "secrets.py").write_text("x=1\n", encoding="utf-8")
    write_launch_record(tmp_path, 4321)

    success = render_success_html(
        title="done",
        subtitle="ok",
        saved_files=["/tmp/secrets.py"],
        launch_info={"pid": "4321", "status": "运行中", "command": "python3 -m nexuscrew"},
    )
    console = render_console_html(tmp_path)

    assert "/console" in success
    assert "NexusCrew Local Console" in console
    assert "secrets.py" in console
