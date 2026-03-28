from pathlib import Path

import pytest

from nexuscrew.config import load_crew_config


def write_config(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "crew.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_crew_config_rejects_missing_project_dir(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
agents:
  - role: dev
    name: dev-01
    model: gpt-5
""".strip(),
    )

    with pytest.raises(ValueError, match="project_dir"):
        load_crew_config(path)


def test_load_crew_config_rejects_non_list_agents(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
project_dir: /tmp/project
agents: {}
""".strip(),
    )

    with pytest.raises(ValueError, match="'agents' must be a list"):
        load_crew_config(path)


def test_load_crew_config_rejects_agent_missing_required_fields(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
project_dir: /tmp/project
agents:
  - role: dev
    name: dev-01
""".strip(),
    )

    with pytest.raises(ValueError, match="missing required fields"):
        load_crew_config(path)


def test_load_crew_config_rejects_non_mapping_orchestrator(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
project_dir: /tmp/project
agents:
  - role: dev
    name: dev-01
    model: gpt-5
orchestrator: []
""".strip(),
    )

    with pytest.raises(ValueError, match="'orchestrator' must be a mapping"):
        load_crew_config(path)


def test_load_crew_config_accepts_minimal_valid_file(tmp_path: Path) -> None:
    path = write_config(
        tmp_path,
        """
project_dir: /tmp/project
project_prefix: demo
agents:
  - role: dev
    name: dev-01
    model: gpt-5
""".strip(),
    )

    config = load_crew_config(path)

    assert config.project_dir == Path("/tmp/project")
    assert config.project_prefix == "demo"
    assert len(config.agents) == 1
    assert config.agents[0].name == "dev-01"
