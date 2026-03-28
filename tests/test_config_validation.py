from pathlib import Path

import pytest

from nexuscrew.config import load_config


def _write_config(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "crew.local.yaml"
    path.write_text(body.strip() + "\n")
    return path


def test_missing_bot_token_raises_value_error(tmp_path: Path):
    path = _write_config(
        tmp_path,
        """
        telegram:
          chat_id: "123456"
        agents: []
        """,
    )
    with pytest.raises(ValueError, match="telegram\.bot_token"):
        load_config(path)


def test_missing_chat_id_raises_value_error(tmp_path: Path):
    path = _write_config(
        tmp_path,
        """
        telegram:
          bot_token: "123456:ABCdef123"
        agents: []
        """,
    )
    with pytest.raises(ValueError, match="telegram\.chat_id"):
        load_config(path)


def test_invalid_bot_token_format_raises_value_error(tmp_path: Path):
    path = _write_config(
        tmp_path,
        """
        telegram:
          bot_token: "not-a-valid-token"
          chat_id: "123456"
        agents: []
        """,
    )
    with pytest.raises(ValueError, match="format is invalid"):
        load_config(path)


def test_undefined_roles_raise_value_error(tmp_path: Path):
    path = _write_config(
        tmp_path,
        """
        telegram:
          bot_token: "123456:ABCdef123"
          chat_id: "123456"
        agents:
          - name: dev1
            roles: ["dev", "invalid_role"]
        """,
    )
    with pytest.raises(ValueError, match="undefined roles: invalid_role"):
        load_config(path)


def test_valid_config_loads_successfully(tmp_path: Path):
    path = _write_config(
        tmp_path,
        """
        telegram:
          bot_token: "123456:ABCdef123"
          chat_id: "123456"
        agents:
          - name: dev1
            role: dev
            roles: ["dev"]
        """,
    )
    config = load_config(path)
    assert config.telegram.bot_token == "123456:ABCdef123"
    assert str(config.telegram.chat_id) == "123456"
    assert len(config.agents) == 1
