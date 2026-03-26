"""Approval request state machine."""
from dataclasses import dataclass, field
from datetime import datetime

from .risk import RiskLevel


@dataclass
class ApprovalRequest:
    id: str
    action_type: str
    risk_level: str
    summary: str
    payload: dict
    status: str = "pending"
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    updated_at: str = ""

    def transition(self, status: str):
        self.status = status
        self.updated_at = datetime.now().isoformat()


class ApprovalManager:
    """Manage pending approvals for risky actions."""

    def __init__(self, state_store=None):
        # Task B2 完成: 审批请求状态机。
        self._counter = 0
        self._requests: dict[str, ApprovalRequest] = {}
        self._state_store = state_store
        self._load_from_store()

    def _load_from_store(self):
        if self._state_store is None:
            return
        for record in self._state_store.load_approvals():
            request = ApprovalRequest(
                id=record["id"],
                action_type=record["action_type"],
                risk_level=record["risk_level"],
                summary=record["summary"],
                payload=record["payload"],
                status=record["status"],
                created_at=record["created_at"],
                updated_at=record["updated_at"],
            )
            self._requests[request.id] = request
            try:
                self._counter = max(self._counter, int(request.id.split("-")[-1]))
            except ValueError:
                pass

    def _persist(self, request: ApprovalRequest):
        if self._state_store is not None:
            self._state_store.save_approval(request)

    def create_request(self, action_type: str, risk_level: RiskLevel,
                       summary: str, payload: dict) -> ApprovalRequest:
        self._counter += 1
        approval_id = f"APR-{self._counter:04d}"
        request = ApprovalRequest(
            id=approval_id,
            action_type=action_type,
            risk_level=risk_level.name.lower(),
            summary=summary,
            payload=payload,
        )
        self._requests[approval_id] = request
        self._persist(request)
        return request

    def get(self, approval_id: str) -> ApprovalRequest | None:
        return self._requests.get(approval_id)

    def list_pending(self) -> list[ApprovalRequest]:
        return [request for request in self._requests.values() if request.status == "pending"]

    def approve(self, approval_id: str) -> ApprovalRequest | None:
        request = self.get(approval_id)
        if request and request.status == "pending":
            request.transition("approved")
            self._persist(request)
        return request

    def reject(self, approval_id: str) -> ApprovalRequest | None:
        request = self.get(approval_id)
        if request and request.status == "pending":
            request.transition("rejected")
            self._persist(request)
        return request
