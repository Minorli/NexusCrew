"""Shell executor — extracts and runs bash/sh blocks from agent replies."""
import asyncio
import re
import shlex
import subprocess
from hashlib import sha256
from pathlib import Path
from ..hooks import HookManager
from ..policy.approval import ApprovalManager
from ..policy.risk import RiskLevel, classify_script
from ..runtime.sqlite_store import DurableStateStore

_BLOCK_RE = re.compile(r"```(?:bash|sh)\n(.*?)\n```", re.DOTALL)
FAIL_KEYWORDS = ("error", "traceback", "failed", "exception", "command not found",
                 "no such file", "permission denied")
_FAIL_PATTERNS = (
    re.compile(r"\btraceback\b", re.IGNORECASE),
    re.compile(r"\bfatal:\b", re.IGNORECASE),
    re.compile(r"\bcommand not found\b", re.IGNORECASE),
    re.compile(r"\bpermission denied\b", re.IGNORECASE),
    re.compile(r"\bno such file\b", re.IGNORECASE),
    re.compile(r"\btimeout after\b", re.IGNORECASE),
    re.compile(r"^\s*error[:\s]", re.IGNORECASE),
    re.compile(r"^\s*failed[:\s]", re.IGNORECASE),
    re.compile(r"^\s*exception[:\s]", re.IGNORECASE),
    re.compile(r"=+\s+\d+\s+failed\b", re.IGNORECASE),
    re.compile(r"=+\s+\d+\s+errors?\b", re.IGNORECASE),
)
_RUNTIME_PATH_PREFIXES = (
    ".nexuscrew",
    ".nexus_audit",
    "artifacts.jsonl",
    "branch_sessions.jsonl",
    "metrics_history.jsonl",
    "run_checkpoints.jsonl",
    "run_events.jsonl",
    "scoped_memory.jsonl",
    "crew_memory.db",
    "crew_memory.md",
    "scoped_memory.db",
)


class ShellExecutor:
    def __init__(self, work_dir: Path, timeout: int = 120):
        self.work_dir = work_dir
        self.timeout = timeout
        self.state_store = DurableStateStore(self.work_dir / ".nexuscrew_state.db")
        self.approval_manager = ApprovalManager(state_store=self.state_store)
        self.hook_manager = HookManager(self.work_dir / ".nexus_audit.jsonl")
        self._context = {"chat_id": 0, "task_id": "", "run_id": ""}

    def set_context(self, chat_id: int, task_id: str, run_id: str):
        self._context = {"chat_id": chat_id, "task_id": task_id, "run_id": run_id}

    async def run_blocks(self, text: str) -> str:
        """Extract all ```bash blocks from text and run them sequentially."""
        blocks = _BLOCK_RE.findall(text)
        if not blocks:
            return ""
        results = []
        for code in blocks:
            risk = classify_script(code)
            if risk >= RiskLevel.HIGH:
                approval = self.approval_manager.create_request(
                    action_type="shell",
                    risk_level=risk,
                    summary=code.splitlines()[0][:80] if code.splitlines() else "shell block",
                    payload={
                        "code": code,
                        "work_dir": str(self.work_dir),
                        **self._context,
                    },
                )
                self.hook_manager.emit(
                    "approval_requested",
                    f"{approval.id} {approval.summary}",
                    {"risk_level": approval.risk_level, **approval.payload},
                )
                results.append(
                    f"[approval required: {approval.id}] risk={approval.risk_level}\n{approval.summary}"
                )
                continue
            self.hook_manager.emit(
                "shell_before_execute",
                code.splitlines()[0][:80] if code.splitlines() else "shell block",
                {"risk_level": risk.name.lower(), **self._context},
            )
            out = await asyncio.to_thread(self._run_one, code)
            self.hook_manager.emit(
                "shell_after_execute",
                code.splitlines()[0][:80] if code.splitlines() else "shell block",
                {"output": out[:500], **self._context},
            )
            results.append(out)
        return "\n\n".join(results)

    def _run_one(self, code: str) -> str:
        label = code[:60].replace("\n", " ") + ("..." if len(code) > 60 else "")
        try:
            r = subprocess.run(
                code, shell=True, capture_output=True, text=False,
                cwd=str(self.work_dir), timeout=self.timeout,
            )
            out = self._decode_output(r.stdout)
            err = self._decode_output(r.stderr)
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

    def _decode_output(self, payload) -> str:
        if payload is None:
            return ""
        if isinstance(payload, bytes):
            return payload.decode("utf-8", errors="replace")
        return str(payload)

    def is_failure(self, shell_output: str) -> bool:
        """Heuristic: did the last execution indicate an error?"""
        if not shell_output.strip():
            return False
        lines = [line.strip() for line in shell_output.splitlines() if line.strip()]
        if not lines:
            return False
        summary_lines = [
            line for line in lines
            if line.startswith("=") or " passed" in line.lower() or " failed" in line.lower()
        ]
        if summary_lines and all("passed" in line.lower() for line in summary_lines):
            return False
        for line in lines:
            lowered = line.lower()
            if lowered.startswith("$ ") or "[approval required:" in lowered:
                continue
            if any(pattern.search(line) for pattern in _FAIL_PATTERNS):
                return True
        lo = shell_output.lower()
        return any(kw in lo for kw in FAIL_KEYWORDS if kw not in {"error", "failed", "exception"})

    async def git_create_branch(self, branch_name: str) -> str:
        # Task 4.3 完成: 为任务提供 Git 分支辅助创建能力。
        self.hook_manager.emit(
            "git_before_execute",
            f"create_branch {branch_name}",
            {"risk_level": "medium", **self._context},
        )
        return await asyncio.to_thread(
            self._run_one,
            f"git checkout -b {branch_name}",
        )

    async def git_commit(self, message: str) -> str:
        self.hook_manager.emit(
            "git_before_execute",
            f"commit {message[:60]}",
            {"risk_level": "medium", **self._context},
        )
        return await asyncio.to_thread(
            self._run_one,
            f'git add -A && git commit -m "{message}"',
        )

    async def git_current_branch(self) -> str:
        result = await asyncio.to_thread(
            self._run_one,
            "git branch --show-current",
        )
        lowered = result.lower()
        if "not a git repository" in lowered or "fatal:" in lowered:
            return "unknown"
        for line in result.splitlines():
            if line.startswith("$"):
                continue
            stripped = line.strip()
            if stripped and not stripped.startswith("[stderr]"):
                return stripped
        return "unknown"

    async def git_changed_files(self, limit: int = 8) -> list[str]:
        result = await asyncio.to_thread(
            self._run_one,
            "git status --porcelain",
        )
        lowered = result.lower()
        if "not a git repository" in lowered or "fatal:" in lowered:
            return []
        files: list[str] = []
        for line in result.splitlines():
            if not line or line.startswith("$") or line.startswith("[stderr]"):
                continue
            payload = line[3:].strip() if len(line) >= 4 else line.strip()
            if "->" in payload:
                payload = payload.split("->", 1)[1].strip()
            if payload and not self._is_runtime_path(payload) and payload not in files:
                files.append(payload)
            if len(files) >= limit:
                break
        return files

    async def git_diff_summary(self, limit: int = 6) -> str:
        result = await asyncio.to_thread(
            self._run_one,
            "git diff --stat --compact-summary HEAD",
        )
        lowered = result.lower()
        if "not a git repository" in lowered or "fatal:" in lowered:
            return ""
        lines: list[str] = []
        for line in result.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("$") or stripped.startswith("[stderr]") or stripped == "(no output)":
                continue
            lines.append(stripped)
            if len(lines) >= limit:
                break
        if lines:
            return "; ".join(lines)
        status_result = await asyncio.to_thread(self._run_one, "git status --porcelain")
        fallback_lines: list[str] = []
        for line in status_result.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("$") or stripped.startswith("[stderr]"):
                continue
            status = stripped[:2].strip() or "M"
            payload = stripped[3:].strip() if len(stripped) >= 4 else stripped
            if "->" in payload:
                payload = payload.split("->", 1)[1].strip()
            if not payload or self._is_runtime_path(payload):
                continue
            normalized = "A" if status == "??" else status
            fallback_lines.append(f"{normalized} {payload}")
            if len(fallback_lines) >= limit:
                break
        return "; ".join(fallback_lines)

    async def git_diff_summary_for_files(self, files: list[str], limit: int = 6) -> str:
        filtered = [path for path in files if path and not self._is_runtime_path(path)]
        if not filtered:
            return ""
        quoted = " ".join(shlex.quote(path) for path in filtered)
        result = await asyncio.to_thread(
            self._run_one,
            f"git diff --stat --compact-summary HEAD -- {quoted}",
        )
        lines: list[str] = []
        for line in result.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("$") or stripped.startswith("[stderr]") or stripped == "(no output)":
                continue
            lines.append(stripped)
            if len(lines) >= limit:
                break
        if lines:
            return "; ".join(lines)
        return "; ".join(f"A {path}" for path in filtered[:limit])

    async def file_hashes(self, paths: list[str]) -> dict[str, str]:
        return await asyncio.to_thread(self._file_hashes_sync, paths)

    def _file_hashes_sync(self, paths: list[str]) -> dict[str, str]:
        hashes: dict[str, str] = {}
        for rel_path in paths:
            path = self.work_dir / rel_path
            if not path.exists() or not path.is_file():
                hashes[rel_path] = "__missing__"
                continue
            try:
                hashes[rel_path] = sha256(path.read_bytes()).hexdigest()
            except OSError:
                hashes[rel_path] = "__unreadable__"
        return hashes

    def _is_runtime_path(self, path: str) -> bool:
        normalized = path.strip()
        return any(
            normalized == prefix or normalized.startswith(prefix)
            for prefix in _RUNTIME_PATH_PREFIXES
        )

    def list_pending_approvals(self):
        return self.approval_manager.list_pending()

    async def approve_and_run(self, approval_id: str) -> str:
        request = self.approval_manager.approve(approval_id)
        if request is None:
            return f"未找到审批: {approval_id}"
        if request.status != "approved":
            return f"审批状态无效: {request.status}"
        self.hook_manager.emit(
            "approval_approved",
            approval_id,
            request.payload,
        )
        result = await asyncio.to_thread(self._run_one, request.payload["code"])
        request.transition("executed")
        self.approval_manager._persist(request)
        self.hook_manager.emit(
            "approval_executed",
            approval_id,
            {"output": result[:500], **request.payload},
        )
        return result

    def reject(self, approval_id: str) -> str:
        request = self.approval_manager.reject(approval_id)
        if request is None:
            return f"未找到审批: {approval_id}"
        self.hook_manager.emit(
            "approval_rejected",
            approval_id,
            request.payload,
        )
        return f"审批 {approval_id} 已拒绝。"
