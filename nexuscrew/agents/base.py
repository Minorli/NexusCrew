"""BaseAgent ABC and shared data structures."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class AgentArtifacts:
    shell_output: str = ""   # stdout/stderr from executed bash blocks
    memory_note: str = ""    # content to write to crew_memory.md


class BaseAgent(ABC):
    """All agent roles inherit from this class."""

    def __init__(self, name: str, role: str, model_label: str,
                 system_prompt_extra: str = ""):
        self.name = name
        self.role = role
        self.model_label = model_label
        self.system_prompt_extra = system_prompt_extra

    @abstractmethod
    async def handle(
        self,
        message: str,
        history: list[dict],
        crew_memory: str,
    ) -> tuple[str, AgentArtifacts]:
        """
        Process a message and return (reply_text, artifacts).
        reply_text will be sent to Telegram.
        artifacts carries side-channel data (shell output, memory notes).
        """

    def _build_system(self, base_prompt: str, crew_memory: str) -> str:
        parts = [base_prompt]
        if crew_memory:
            # Task 3.4 完成: HR 通知通过 crew_memory 注入到所有 Agent prompt。
            parts.append(f"\n\n【项目共识/共享记忆】\n{crew_memory}")
        if self.system_prompt_extra:
            parts.append(f"\n\n【项目特定约束】\n{self.system_prompt_extra}")
        return "\n".join(parts)

    def __repr__(self) -> str:
        return f"<{self.role}/{self.name} [{self.model_label}]>"
