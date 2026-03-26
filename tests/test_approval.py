"""Tests for risk classification, approvals, and executor gating."""
import asyncio
from pathlib import Path

from nexuscrew.executor import shell as shell_module
from nexuscrew.executor.shell import ShellExecutor
from nexuscrew.policy.approval import ApprovalManager
from nexuscrew.policy.risk import RiskLevel, classify_command, classify_script


async def _direct_to_thread(func, *args, **kwargs):
    return func(*args, **kwargs)


def test_risk_classification_examples():
    assert classify_command("git status") == RiskLevel.LOW
    assert classify_command("pytest tests/ -v") == RiskLevel.MEDIUM
    assert classify_command("rm -rf workspace") == RiskLevel.HIGH
    assert classify_script("echo hi\nrm -rf workspace") == RiskLevel.HIGH


def test_approval_manager_lifecycle():
    manager = ApprovalManager()
    approval = manager.create_request("shell", RiskLevel.HIGH, "rm -rf", {"code": "rm -rf"})
    assert manager.list_pending()[0].id == approval.id
    assert manager.approve(approval.id).status == "approved"


def test_shell_executor_blocks_high_risk_script(tmp_path: Path):
    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr(shell_module.asyncio, "to_thread", _direct_to_thread)
    executor = ShellExecutor(tmp_path)
    executor.set_context(1, "T-0001", "run-1")

    output = asyncio.run(executor.run_blocks("```bash\nrm -rf workspace\n```"))

    assert "[approval required:" in output
    assert executor.list_pending_approvals()
    monkeypatch.undo()


def test_shell_executor_approve_and_run(tmp_path: Path):
    monkeypatch = __import__("pytest").MonkeyPatch()
    monkeypatch.setattr(shell_module.asyncio, "to_thread", _direct_to_thread)
    executor = ShellExecutor(tmp_path)
    executor.set_context(1, "T-0001", "run-1")

    asyncio.run(executor.run_blocks("```bash\nrm -rf workspace\n```"))
    approval = executor.list_pending_approvals()[0]

    result = asyncio.run(executor.approve_and_run(approval.id))

    assert "$ rm -rf workspace" in result
    assert any(event["event_type"] == "approval_executed" for event in executor.hook_manager.read_all())
    monkeypatch.undo()
