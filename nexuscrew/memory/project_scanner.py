"""ProjectScanner — auto-detects project type and generates a briefing."""
import asyncio
import subprocess
from datetime import datetime
from pathlib import Path

# Files that indicate a tech stack
STACK_SIGNALS: list[tuple[str, str]] = [
    ("pyproject.toml", "Python"),
    ("requirements.txt", "Python"),
    ("setup.py", "Python"),
    ("package.json", "Node.js"),
    ("go.mod", "Go"),
    ("Cargo.toml", "Rust"),
    ("pom.xml", "Java/Maven"),
    ("build.gradle", "Java/Gradle"),
    ("Gemfile", "Ruby"),
    ("composer.json", "PHP"),
    ("mix.exs", "Elixir"),
]


class ProjectScanner:
    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    async def scan(self, project_dir: Path) -> str:
        """Return a structured project briefing string."""
        parts = await asyncio.gather(
            asyncio.to_thread(self._detect_stack, project_dir),
            asyncio.to_thread(self._read_readme, project_dir),
            asyncio.to_thread(self._tree, project_dir),
            asyncio.to_thread(self._git_log, project_dir),
        )
        ts = datetime.now().strftime("%Y-%m-%d %H:%M")
        header = f"## 项目简报（自动生成 {ts}）\n\n**路径**: {project_dir}"
        return header + "\n\n" + "\n\n".join(p for p in parts if p)

    def _detect_stack(self, path: Path) -> str:
        detected = [label for fname, label in STACK_SIGNALS
                    if (path / fname).exists()]
        if not detected:
            detected = ["(未检测到已知技术栈)"]
        return "**技术栈**: " + ", ".join(detected)

    def _read_readme(self, path: Path) -> str:
        for name in ("README.md", "README.rst", "README.txt", "README"):
            f = path / name
            if f.exists():
                content = f.read_text(encoding="utf-8", errors="replace")[:2000]
                return f"### README 摘要\n\n{content}"
        return ""

    def _tree(self, path: Path) -> str:
        ignore = "node_modules|.git|__pycache__|.venv|dist|build|.next"
        try:
            r = subprocess.run(
                ["tree", "-L", "2", "--ignore-case", "-I", ignore, str(path)],
                capture_output=True, text=True, timeout=self.timeout,
            )
            out = r.stdout.strip()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            # Fallback: find
            try:
                r = subprocess.run(
                    ["find", str(path), "-maxdepth", "2", "-not",
                     "-path", "*/.git/*", "-not", "-path", "*/node_modules/*"],
                    capture_output=True, text=True, timeout=self.timeout,
                )
                out = "\n".join(r.stdout.splitlines()[:80])
            except Exception:
                return ""
        return f"### 目录结构\n\n```\n{out[:1500]}\n```"

    def _git_log(self, path: Path) -> str:
        try:
            log = subprocess.run(
                ["git", "-C", str(path), "log", "--oneline", "-20"],
                capture_output=True, text=True, timeout=self.timeout,
            ).stdout.strip()
            branch = subprocess.run(
                ["git", "-C", str(path), "branch", "--show-current"],
                capture_output=True, text=True, timeout=self.timeout,
            ).stdout.strip()
            if not log:
                return ""
            return f"**当前分支**: {branch}\n\n### 近期提交\n\n```\n{log}\n```"
        except Exception:
            return ""
