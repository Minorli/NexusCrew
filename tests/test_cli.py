"""Tests for CLI argument handling."""
import sys
from types import SimpleNamespace

import secrets as cfg

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
    monkeypatch.setattr(sys, "argv", ["nexuscrew"])
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: None)
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", "test-token", raising=False)

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
    monkeypatch.setattr(sys, "argv", ["nexuscrew", "start", "-c", "crew.yaml"])
    monkeypatch.setattr("builtins.print", lambda *args, **kwargs: None)
    monkeypatch.setattr(cfg, "TELEGRAM_BOT_TOKEN", "test-token", raising=False)

    cli.main()

    assert seen["ran"] is True
    assert seen["preload_config"] is sentinel
