"""Tests for CLI argument handling."""
import sys
from pathlib import Path
from types import SimpleNamespace

from nexuscrew import cli


def test_cli_defaults_to_start(monkeypatch):
    seen: dict[str, object] = {}

    class FakeApp:
        def run_polling(self):
            seen["ran"] = True

    class FakeBot:
        def __init__(self):
            self.preload_config = None

        def build_app(self):
            seen["preload_config"] = self.preload_config
            return FakeApp()

    monkeypatch.setattr(cli, "NexusCrewBot", FakeBot)
    monkeypatch.setattr(cli, "load_crew_config", lambda path: {"path": path})
    monkeypatch.setattr(cli, "load_local_secrets", lambda: SimpleNamespace(TELEGRAM_BOT_TOKEN="test-token"))
    monkeypatch.setattr(sys, "argv", ["nexuscrew"])
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: None)

    cli.main()

    assert seen["ran"] is True
    assert seen["preload_config"] is None


def test_cli_preloads_config(monkeypatch):
    seen: dict[str, object] = {}
    sentinel = SimpleNamespace(name="config")

    class FakeApp:
        def run_polling(self):
            seen["ran"] = True

    class FakeBot:
        def __init__(self):
            self.preload_config = None

        def build_app(self):
            seen["preload_config"] = self.preload_config
            return FakeApp()

    monkeypatch.setattr(cli, "NexusCrewBot", FakeBot)
    monkeypatch.setattr(cli, "load_crew_config", lambda path: sentinel)
    monkeypatch.setattr(cli, "load_local_secrets", lambda: SimpleNamespace(TELEGRAM_BOT_TOKEN="test-token"))
    monkeypatch.setattr(sys, "argv", ["nexuscrew", "start", "-c", "crew.yaml"])
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: None)

    cli.main()

    assert seen["ran"] is True
    assert seen["preload_config"] is sentinel


def test_cli_starts_setup_wizard_when_secret_missing(monkeypatch):
    seen: dict[str, object] = {}

    class FakeWizard:
        def __init__(self, base_dir, host="127.0.0.1", port=8766):
            seen["base_dir"] = base_dir
            seen["host"] = host
            seen["port"] = port

        def serve_forever(self):
            seen["served"] = True

    monkeypatch.setattr(cli, "SetupWizardServer", FakeWizard)
    monkeypatch.setattr(cli, "load_local_secrets", lambda: SimpleNamespace(TELEGRAM_BOT_TOKEN=""))
    monkeypatch.setattr(sys, "argv", ["nexuscrew"])
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: None)

    cli.main()

    assert seen["served"] is True


def test_cli_autoloads_crew_local_yaml(monkeypatch, tmp_path: Path):
    seen: dict[str, object] = {}

    class FakeApp:
        def run_polling(self):
            seen["ran"] = True

    class FakeBot:
        def __init__(self):
            self.preload_config = None

        def build_app(self):
            seen["preload_config"] = self.preload_config
            return FakeApp()

    monkeypatch.chdir(tmp_path)
    (tmp_path / "crew.local.yaml").write_text("project_dir: /tmp/project\nagents: []\n", encoding="utf-8")
    monkeypatch.setattr(cli, "NexusCrewBot", FakeBot)
    monkeypatch.setattr(cli, "load_crew_config", lambda path: {"path": str(path)})
    monkeypatch.setattr(cli, "load_local_secrets", lambda: SimpleNamespace(TELEGRAM_BOT_TOKEN="test-token"))
    monkeypatch.setattr(sys, "argv", ["nexuscrew"])
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: None)

    cli.main()

    assert seen["ran"] is True
    assert seen["preload_config"] == {"path": "crew.local.yaml"}


def test_cli_setup_command_starts_wizard(monkeypatch):
    seen: dict[str, object] = {}

    class FakeWizard:
        def __init__(self, base_dir, host="127.0.0.1", port=8766):
            seen["base_dir"] = base_dir
            seen["host"] = host
            seen["port"] = port

        def serve_forever(self):
            seen["served"] = True

    monkeypatch.setattr(cli, "SetupWizardServer", FakeWizard)
    monkeypatch.setattr(sys, "argv", ["nexuscrew", "setup", "--host", "0.0.0.0", "--port", "9000"])
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: None)

    cli.main()

    assert seen["host"] == "0.0.0.0"
    assert seen["port"] == 9000
    assert seen["served"] is True
