from typing import Any

from app.models.enums import PaymentGateway
from app.repositories.payment_repository import PaymentRepository
from app.schemas.admin_actions import AdminActionCreate
from app.services.audit_service import AuditService


class PaymentService:
    def __init__(self, client: Any) -> None:
        self.payments = PaymentRepository(client)
        self.audit = AuditService(client)

    def gateways(self) -> list[dict[str, Any]]:
        return self.payments.list_gateways()

    def recent_payments(self) -> list[dict[str, Any]]:
        return self.payments.list_recent()

    def validate_gateway(self, code: str) -> PaymentGateway:
        return PaymentGateway(code)

    def mark_reviewed(self, admin_user_id: str, payment_id: str, reason: str) -> None:
        self.audit.record(
            AdminActionCreate(
                admin_user_id=admin_user_id,
                action="PAYMENT_REVIEWED",
                target_type="payments",
                target_id=payment_id,
                reason=reason,
            )
        )