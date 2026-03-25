"""Shell executor — extracts and runs bash/sh blocks from agent replies."""
import asyncio
import re
import subprocess
from pathlib import Path

_BLOCK_RE = re.compile(r"```(?:bash|sh)\n(.*?)\n```", re.DOTALL)
FAIL_KEYWORDS = ("error", "traceback", "failed", "exception", "command not found",
                 "no such file", "permission denied")


class ShellExecutor:
    def __init__(self, work_dir: Path, timeout: int = 120):
        self.work_dir = work_dir
        self.timeout = timeout

    async def run_blocks(self, text: str) -> str:
        """Extract all ```bash blocks from text and run them sequentially."""
        blocks = _BLOCK_RE.findall(text)
        if not blocks:
            return ""
        results = []
        for code in blocks:
            out = await asyncio.to_thread(self._run_one, code)
            results.append(out)
        return "\n\n".join(results)

    def _run_one(self, code: str) -> str:
        label = code[:60].replace("\n", " ") + ("..." if len(code) > 60 else "")
        try:
            r = subprocess.run(
                code, shell=True, capture_output=True, text=True,
                cwd=str(self.work_dir), timeout=self.timeout,
            )
            out = r.stdout
            err = r.stderr
            # Truncate long output
            if len(out) > 2800:
                out = out[:1000] + f"\n...[截断 {len(out)-1800} 字符]...\n" + out[-800:]
            if len(err) > 500:
                err = err[:500]
            combined = out.strip()
            if err.strip():
                combined += f"\n[stderr]\n{err.strip()}"
            return f"$ {label}\n{combined}" if combined else f"$ {label}\n(no output)"
        except subprocess.TimeoutExpired:
            return f"$ {label}\n[timeout after {self.timeout}s]"

    def is_failure(self, shell_output: str) -> bool:
        """Heuristic: did the last execution indicate an error?"""
        lo = shell_output.lower()
        return any(kw in lo for kw in FAIL_KEYWORDS)
