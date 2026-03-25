"""Configuration loader — reads crew.yaml and returns structured config."""
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class AgentSpec:
    """Single agent specification from YAML."""

    role: str
    name: str
    model: str
    system_prompt_extra: str = ""
    gemini_model: str | None = None
    openai_model: str | None = None
    anthropic_model: str | None = None
    anthropic_model_light: str | None = None


@dataclass
class OrchestratorConfig:
    max_chain_hops: int = 10
    max_dev_retry: int = 5
    history_window: int = 20
    memory_tail_lines: int = 120
    shell_timeout: int = 120


@dataclass
class HRConfig:
    eval_per_task: bool = True
    summary_interval: int = 5
    pressure_cooldown: int = 2
    pressure_max_prompt_len: int = 500
    pressure_ttl: int = 3
    anomaly_triggers: dict[str, int] = field(default_factory=lambda: {
        "dev_retry_threshold": 3,
        "review_reject_threshold": 2,
        "response_time_threshold": 120,
        "chain_hops_threshold": 8,
    })


@dataclass
class CrewConfig:
    """Top-level crew configuration."""

    project_dir: Path
    project_prefix: str = "nexus"
    agents: list[AgentSpec] = field(default_factory=list)
    orchestrator: OrchestratorConfig = field(default_factory=OrchestratorConfig)
    hr: HRConfig = field(default_factory=HRConfig)


def _filter_dataclass_fields(raw: dict, cls) -> dict:
    return {k: v for k, v in raw.items() if k in cls.__dataclass_fields__}


def _load_hr_config(raw: dict) -> HRConfig:
    defaults = HRConfig()
    values = _filter_dataclass_fields(raw, HRConfig)
    anomaly_triggers = defaults.anomaly_triggers | values.get("anomaly_triggers", {})
    values["anomaly_triggers"] = anomaly_triggers
    return HRConfig(**values)


def load_crew_config(path: str | Path) -> CrewConfig:
    """
    Load and validate a crew YAML file.

    Raises:
        FileNotFoundError: if YAML file does not exist
        ValueError: if required fields are missing or invalid
    """
    # Task 1.1 完成: 提供 YAML crew 配置加载器。
    path = Path(path).expanduser()
    if not path.exists():
        raise FileNotFoundError(path)

    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    if "project_dir" not in raw:
        raise ValueError("crew.yaml must contain 'project_dir'")

    agents_raw = raw.get("agents", [])
    if not isinstance(agents_raw, list):
        raise ValueError("'agents' must be a list")

    agents: list[AgentSpec] = []
    for agent_raw in agents_raw:
        if not isinstance(agent_raw, dict):
            raise ValueError(f"Agent spec must be a mapping: {agent_raw!r}")
        missing = [key for key in ("role", "name", "model") if key not in agent_raw]
        if missing:
            raise ValueError(
                f"Agent spec missing required fields {missing}: {agent_raw}"
            )
        agents.append(AgentSpec(
            role=agent_raw["role"],
            name=agent_raw["name"],
            model=agent_raw["model"],
            system_prompt_extra=agent_raw.get("system_prompt_extra", ""),
            gemini_model=agent_raw.get("gemini_model"),
            openai_model=agent_raw.get("openai_model"),
            anthropic_model=agent_raw.get("anthropic_model"),
            anthropic_model_light=agent_raw.get("anthropic_model_light"),
        ))

    orch_raw = raw.get("orchestrator", {})
    hr_raw = raw.get("hr", {})
    if not isinstance(orch_raw, dict):
        raise ValueError("'orchestrator' must be a mapping")
    if not isinstance(hr_raw, dict):
        raise ValueError("'hr' must be a mapping")

    return CrewConfig(
        project_dir=Path(raw["project_dir"]).expanduser(),
        project_prefix=raw.get("project_prefix", "nexus"),
        agents=agents,
        orchestrator=OrchestratorConfig(**_filter_dataclass_fields(
            orch_raw, OrchestratorConfig,
        )),
        hr=_load_hr_config(hr_raw),
    )
