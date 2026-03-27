"""Shell executor — extracts and runs bash/sh blocks from agent replies."""
import asyncio
import re
import subprocess
from pathlib import Path
from ..hooks import HookManager
from ..policy.approval import ApprovalManager
from ..policy.risk import RiskLevel, classify_script
from ..runtime.sqlite_store import DurableStateStore

_BLOCK_RE = re.compile(r"```(?:bash|sh)\n(.*?)\n```", re.DOTALL)
FAIL_KEYWORDS = ("error", "traceback", "failed", "exception", "command not found",
                 "no such file", "permission denied")


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
            if payload and payload not in files:
                files.append(payload)
            if len(files) >= limit:
                break
        return files

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
