"""Gemini CLI backend — calls local OAuth-authenticated gemini CLI via subprocess."""
import subprocess


class GeminiCLIBackend:
    """
    Wraps the local `gemini` CLI tool (Google Gemini, OAuth login required).
    Supports two modes:
      - flag mode:  gemini [--model MODEL] -p "<prompt>"
      - stdin mode: echo "<prompt>" | gemini [--model MODEL]
    Configure via secrets.py: GEMINI_CLI_CMD, GEMINI_PROMPT_FLAG, GEMINI_MODEL.
    """

    def __init__(self, cmd: list[str], prompt_flag: str | None = "-p",
                 model: str | None = None, timeout: int = 180):
        self.cmd = cmd                  # e.g. ["gemini"]
        self.prompt_flag = prompt_flag  # e.g. "-p"; None → use stdin
        self.model = model              # e.g. "gemini-2.5-pro"
        self.timeout = timeout

    def _base_args(self) -> list[str]:
        args = list(self.cmd)
        if self.model:
            args += ["--model", self.model]
        return args

    def complete(self, prompt: str) -> str:
        """Blocking call — run via asyncio.to_thread in async contexts."""
        try:
            base = self._base_args()
            if self.prompt_flag:
                args = base + [self.prompt_flag, prompt]
                r = subprocess.run(args, capture_output=True, text=True,
                                   timeout=self.timeout)
            else:
                r = subprocess.run(base, input=prompt,
                                   capture_output=True, text=True,
                                   timeout=self.timeout)
            out = r.stdout.strip()
            if not out and r.returncode != 0:
                return f"[Gemini CLI error (rc={r.returncode})]\n{r.stderr.strip()[:500]}"
            return out
        except subprocess.TimeoutExpired:
            return f"[Gemini CLI timeout after {self.timeout}s]"
        except FileNotFoundError:
            return f"[Gemini CLI not found: {self.cmd[0]}]"
