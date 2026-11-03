from datetime import datetime, timezone
import logging
from typing import Any

from app.repositories.payment_repository import PaymentRepository
from app.repositories.refund_repository import RefundRepository
from app.schemas.admin_actions import AdminActionCreate
from app.services.audit_service import AuditService

logger = logging.getLogger(__name__)


class RefundService:
    def __init__(self, client: Any) -> None:
        self.client = client
        self.refunds = RefundRepository(client)
        self.payments = PaymentRepository(client)
        self.audit = AuditService(client)

    def count_metrics(self) -> dict[str, Any]:
        all_refunds = self.refunds.list_refunds(limit=500)
        pending = sum(1 for r in all_refunds if (r.get("status") or "").upper() in ("REQUESTED", "PENDING"))
        approved = sum(1 for r in all_refunds if (r.get("status") or "").upper() in ("APPROVED", "COMPLETED"))
        rejected = sum(1 for r in all_refunds if (r.get("status") or "").upper() == "REJECTED")
        total_refunded_amount = sum(
            float(r.get("amount") or 0)
            for r in all_refunds
            if (r.get("status") or "").upper() in ("APPROVED", "COMPLETED")
        )
        return {
            "all": len(all_refunds),
            "pending": pending,
            "approved": approved,
            "rejected": rejected,
            "total_refunded_zar": round(total_refunded_amount, 2),
        }

    def list_refunds(self, status: str | None = None) -> list[dict[str, Any]]:
        return self.refunds.list_refunds(status=status)

    def list_refundable_payments(self, limit: int = 50) -> list[dict[str, Any]]:
        """Finds completed/paid payments that can be refunded."""
        payments = self.payments.list_recent(limit=100)
        refundable: list[dict[str, Any]] = []
        for p in payments:
            st = (p.get("status") or "").upper()
            if st not in ("PAID", "PARTIALLY_REFUNDED"):
                continue
            amt = float(p.get("amount") or 0)
            if amt <= 0:
                continue

            # Check existing refunds
            existing = self.refunds.get_refunds_for_payment(p["id"])
            already_refunded = sum(
                float(r.get("amount") or 0)
                for r in existing
                if (r.get("status") or "").upper() in ("APPROVED", "COMPLETED", "REQUESTED", "PENDING")
            )
            remaining = round(amt - already_refunded, 2)
            if remaining > 0:
                p_copy = dict(p)
                p_copy["already_refunded"] = already_refunded
                p_copy["remaining_refundable"] = remaining
                refundable.append(p_copy)
                if len(refundable) >= limit:
                    break
        return refundable

    def issue_refund(
        self,
        admin_user_id: str,
        payment_id: str,
        amount: float,
        reason: str,
        auto_approve: bool = True,
    ) -> dict[str, Any]:
        if amount <= 0:
            raise ValueError("Refund amount must be greater than zero.")

        # 1. Fetch payment
        payment = self.payments.get_by_id(payment_id)
        if not payment:
            raise ValueError(f"Payment {payment_id} not found.")

        current_status = (payment.get("status") or "").upper()
        if current_status not in ("PAID", "PARTIALLY_REFUNDED"):
            raise ValueError(f"Payment with status '{current_status}' cannot be refunded.")

        payment_total = float(payment.get("amount") or 0)
        existing = self.refunds.get_refunds_for_payment(payment_id)
        already_refunded = sum(
            float(r.get("amount") or 0)
            for r in existing
            if (r.get("status") or "").upper() in ("APPROVED", "COMPLETED", "REQUESTED", "PENDING")
        )
        remaining = round(payment_total - already_refunded, 2)

        if amount > remaining:
            raise ValueError(
                f"Requested refund ZAR {amount:.2f} exceeds remaining refundable balance ZAR {remaining:.2f}."
            )

        now_iso = datetime.now(timezone.utc).isoformat()
        refund_status = "APPROVED" if auto_approve else "REQUESTED"

        # 2. Insert into refunds table
        refund_payload = {
            "payment_id": payment_id,
            "booking_id": payment.get("booking_id"),
            "requested_by": payment.get("payer_id") or admin_user_id,
            "amount": amount,
            "reason": reason,
            "status": refund_status,
            "processed_at": now_iso if auto_approve else None,
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        created = self.refunds.create(refund_payload)
        refund_id = created.get("id") or "new-refund"

        # 3. If auto_approved, apply effects; otherwise audit request
        if auto_approve:
            self._apply_approved_refund(payment, amount, already_refunded, refund_id, admin_user_id, reason)
        else:
            self.audit.record(
                AdminActionCreate(
                    admin_user_id=admin_user_id,
                    action="REFUND_REQUESTED",
                    target_type="refunds",
                    target_id=refund_id,
                    reason=reason,
                    metadata={"payment_id": payment_id, "amount": amount},
                )
            )

        return created

    def approve_refund(self, admin_user_id: str, refund_id: str, reason: str = "Approved by administrator") -> None:
        refund = self.refunds.get_by_id(refund_id)
        if not refund:
            raise ValueError(f"Refund {refund_id} not found.")

        current_status = (refund.get("status") or "").upper()
        if current_status in ("APPROVED", "COMPLETED"):
            return

        now_iso = datetime.now(timezone.utc).isoformat()
        self.refunds.update_status(refund_id, "APPROVED", processed_at=now_iso)

        payment_id = refund.get("payment_id")
        payment = self.payments.get_by_id(payment_id) if payment_id else None
        amount = float(refund.get("amount") or 0)

        if payment:
            existing = self.refunds.get_refunds_for_payment(payment_id)
            already_refunded = sum(
                float(r.get("amount") or 0)
                for r in existing
                if r.get("id") != refund_id and (r.get("status") or "").upper() in ("APPROVED", "COMPLETED")
            )
            self._apply_approved_refund(payment, amount, already_refunded, refund_id, admin_user_id, reason)
        else:
            self.audit.record(
                AdminActionCreate(
                    admin_user_id=admin_user_id,
                    action="REFUND_APPROVED",
                    target_type="refunds",
                    target_id=refund_id,
                    reason=reason,
                )
            )

    def reject_refund(self, admin_user_id: str, refund_id: str, reason: str = "Rejected by administrator") -> None:
        refund = self.refunds.get_by_id(refund_id)
        if not refund:
            raise ValueError(f"Refund {refund_id} not found.")

        now_iso = datetime.now(timezone.utc).isoformat()
        self.refunds.update_status(refund_id, "REJECTED", processed_at=now_iso)
        self.audit.record(
            AdminActionCreate(
                admin_user_id=admin_user_id,
                action="REFUND_REJECTED",
                target_type="refunds",
                target_id=refund_id,
                reason=reason,
            )
        )

    def _apply_approved_refund(
        self,
        payment: dict[str, Any],
        amount: float,
        prior_approved_refunds: float,
        refund_id: str,
        admin_user_id: str,
        reason: str,
    ) -> None:
        now_iso = datetime.now(timezone.utc).isoformat()
        payment_id = payment["id"]
        total_payment = float(payment.get("amount") or 0)
        total_refunded = round(prior_approved_refunds + amount, 2)

        new_payment_status = "REFUNDED" if total_refunded >= total_payment else "PARTIALLY_REFUNDED"

        # 1. Update payments table
        try:
            self.client.table("payments").update({
                "status": new_payment_status,
                "refunded_at": now_iso,
                "updated_at": now_iso,
            }).eq("id", payment_id).execute()
        except Exception:
            logger.exception("Failed to update payment status for %s", payment_id)

        # 2. Reverse cleaner earnings in cleaner_ledger_entries if applicable
        cleaner_id = payment.get("payee_id")
        cleaner_net = float(payment.get("cleaner_net_amount") or 0)
        booking_id = payment.get("booking_id")

        if cleaner_id and cleaner_net > 0 and total_payment > 0:
            prop = min(1.0, amount / total_payment)
            cleaner_deduction = round(cleaner_net * prop, 2)
            if cleaner_deduction > 0:
                try:
                    ledger_entry = {
                        "cleaner_id": cleaner_id,
                        "booking_id": booking_id,
                        "payment_id": payment_id,
                        "transaction_type": "REFUND",
                        "amount": cleaner_deduction,
                        "direction": "DEBIT",
                        "status": "POSTED",
                        "reference": f"Refund deduction for payment {payment_id[:8]}",
                        "created_at": now_iso,
                    }
                    self.client.table("cleaner_ledger_entries").insert(ledger_entry).execute()
                except Exception:
                    logger.exception("Failed to insert cleaner ledger entry for refund %s", refund_id)

        # 3. Record audit log
        self.audit.record(
            AdminActionCreate(
                admin_user_id=admin_user_id,
                action="REFUND_APPROVED",
                target_type="refunds",
                target_id=refund_id,
                reason=reason,
                metadata={
                    "payment_id": payment_id,
                    "refund_amount": amount,
                    "payment_status": new_payment_status,
                },
            )
        )
