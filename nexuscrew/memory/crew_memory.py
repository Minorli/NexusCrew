"""Shared crew memory — reads/writes crew_memory.md."""
from datetime import datetime
from pathlib import Path


class CrewMemory:
    def __init__(self, path: Path):
        self.path = path
        if not path.exists():
            path.write_text(
                "# NexusCrew 共享记忆\n\n"
                "> 所有 Agent 共同读写。Agent 回复末尾加【MEMORY】标记自动追加。\n"
                "> 人类可直接编辑此文件向团队广播信息。\n\n"
                "## 项目基础信息\n\n(由 /crew 命令自动填充)\n",
                encoding="utf-8",
            )

    def read(self, tail_lines: int = 120) -> str:
        """Return last N lines to limit token injection."""
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return "\n".join(lines[-tail_lines:])

    def append(self, agent_name: str, note: str) -> None:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        block = f"\n---\n**[{ts}] {agent_name}**\n{note.strip()}\n"
        with self.path.open("a", encoding="utf-8") as f:
            f.write(block)

    def overwrite_section(self, header: str, content: str) -> None:
        """Replace or insert a named section (used by ProjectScanner)."""
        text = self.path.read_text(encoding="utf-8")
        marker = f"## {header}"
        block = f"{marker}\n\n{content.strip()}\n"
        if marker in text:
            # Replace existing section up to the next ##
            import re
            text = re.sub(
                rf"## {re.escape(header)}.*?(?=\n## |\Z)",
                block, text, flags=re.DOTALL,
            )
        else:
            text += f"\n{block}"
        self.path.write_text(text, encoding="utf-8")
