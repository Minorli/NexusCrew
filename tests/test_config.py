"""Tests for YAML crew config loading and /load command wiring."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexuscrew.config import load_crew_config
from nexuscrew.telegram.bot import NexusCrewBot


def test_load_crew_config_parses_example_yaml():
    config = load_crew_config("crew.example.yaml")

    assert config.project_dir == Path("~/myproject").expanduser()
    assert config.project_prefix == "nexus"
    assert [agent.role for agent in config.agents] == [
        "pm", "dev", "dev", "architect", "hr",
    ]
    assert config.orchestrator.max_chain_hops == 10
    assert config.hr.anomaly_triggers["dev_retry_threshold"] == 3


def test_load_crew_config_requires_project_dir(tmp_path: Path):
    path = tmp_path / "crew.yaml"
    path.write_text("agents: []\n", encoding="utf-8")

    with pytest.raises(ValueError, match="project_dir"):
        load_crew_config(path)


@pytest.mark.asyncio
async def test_cmd_load_uses_shared_initializer(tmp_path: Path, monkeypatch):
    crew_file = tmp_path / "crew.yaml"
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    crew_file.write_text(
        f"project_dir: {project_dir}\n"
        "agents:\n"
        "  - role: pm\n"
        "    name: alice\n"
        "    model: gemini\n",
        encoding="utf-8",
    )

    bot = NexusCrewBot()
    seen: dict[str, object] = {}

    async def fake_init(config, update):
        seen["config"] = config
        seen["chat_id"] = update.message.chat_id

    monkeypatch.setattr(bot, "_init_from_config", fake_init)

    replies: list[str] = []

    class FakeMessage:
        chat_id = 123

        async def reply_text(self, text: str):
            replies.append(text)

    update = SimpleNamespace(message=FakeMessage())
    context = SimpleNamespace(args=[str(crew_file)])

    await bot.cmd_load(update, context)

    assert seen["chat_id"] == 123
    assert seen["config"].project_dir == project_dir
    assert replies == []


@pytest.mark.asyncio
async def test_cmd_load_requires_path_argument():
    bot = NexusCrewBot()
    replies: list[str] = []

    class FakeMessage:
        chat_id = 123

        async def reply_text(self, text: str):
            replies.append(text)

    update = SimpleNamespace(message=FakeMessage())
    context = SimpleNamespace(args=[])

    await bot.cmd_load(update, context)

    assert replies == ["用法: /load <crew.yaml 路径>"]


@pytest.mark.asyncio
async def test_post_init_applies_preload_config(tmp_path: Path, monkeypatch):
    bot = NexusCrewBot()
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    config = load_crew_config("crew.example.yaml")
    config.project_dir = project_dir
    bot.preload_config = config

    seen: dict[str, object] = {}

    async def fake_apply(applied_config):
        seen["config"] = applied_config
        return "ok"

    monkeypatch.setattr(bot, "_apply_config", fake_apply)

    await bot._post_init(None)

    assert seen["config"] is config
