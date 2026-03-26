"""Access control for operator, approver, and admin actions."""


class AccessController:
    """Simple ID-based access controller for Telegram actions."""

    def __init__(
        self,
        operator_ids: list[int] | None = None,
        approver_ids: list[int] | None = None,
        admin_ids: list[int] | None = None,
    ):
        self.operator_ids = set(operator_ids or [])
        self.approver_ids = set(approver_ids or [])
        self.admin_ids = set(admin_ids or [])

    def can_operate(self, user_id: int | None) -> bool:
        if not self.operator_ids:
            return True
        return user_id in self.operator_ids

    def can_approve(self, user_id: int | None) -> bool:
        if self.approver_ids:
            return user_id in self.approver_ids
        return self.can_operate(user_id)

    def can_administer(self, user_id: int | None) -> bool:
        if self.admin_ids:
            return user_id in self.admin_ids
        return self.can_approve(user_id)
