from datetime import date
from typing import Any

from app.schemas.admin_actions import AdminActionCreate
from app.schemas.subscription_schema import SubscriptionPeriod
from app.services.audit_service import AuditService
from app.utils.dates import calculate_subscription_period


class SubscriptionService:
    def __init__(self, client: Any) -> None:
        self.client = client
        self.audit = AuditService(client)

    def calculate_period(self, payment_date: date) -> SubscriptionPeriod:
        start, end = calculate_subscription_period(payment_date)
        return SubscriptionPeriod(start_date=start, end_date=end)

    def list_plans(self) -> list[dict[str, Any]]:
        try:
            res = self.client.table("subscription_plans").select("id,name,amount,currency,billing_period,active").eq("active", True).order("amount").execute()
            plans = getattr(res, "data", None) or []
            if not plans:
                # Ensure default CleanLink Annual Plan exists
                new_plan = self.client.table("subscription_plans").insert({
                    "name": "CleanLink Annual Cleaner Membership",
                    "amount": 350.00,
                    "currency": "ZAR",
                    "billing_period": "ANNUAL",
                    "active": True,
                }).execute()
                plans = getattr(new_plan, "data", None) or []
            return plans
        except Exception:
            return []

    def activate_subscription(self, admin_user_id: str, cleaner_id: str, plan_id: str, payment_id: str, payment_date: date, reason: str) -> dict[str, Any] | None:
        cleaner_id = cleaner_id.strip()
        plan_id = plan_id.strip() if plan_id else ""

        # If cleaner_id happens to be user_id, resolve to cleaner_profiles.id
        try:
            cp_res = self.client.table("cleaner_profiles").select("id").eq("id", cleaner_id).execute()
            cp_rows = getattr(cp_res, "data", None) or []
            if not cp_rows:
                # Try matching by user_id
                u_res = self.client.table("cleaner_profiles").select("id").eq("user_id", cleaner_id).execute()
                u_rows = getattr(u_res, "data", None) or []
                if u_rows:
                    cleaner_id = u_rows[0]["id"]
        except Exception:
            pass

        # If plan_id is missing or default, resolve to first available plan
        if not plan_id or plan_id == "default":
            plans = self.list_plans()
            if plans:
                plan_id = str(plans[0]["id"])

        period = self.calculate_period(payment_date)
        payload = {
            "cleaner_id": cleaner_id,
            "plan_id": plan_id,
            "payment_id": payment_id.strip() if payment_id and payment_id.strip() else None,
            "start_date": period.start_date.isoformat(),
            "end_date": period.end_date.isoformat(),
            "status": "ACTIVE",
        }
        response = self.client.table("cleaner_subscriptions").insert(payload).execute()
        from datetime import datetime, timezone
        updated = self.client.table("cleaner_profiles").update({
            "subscription_status": "ACTIVE",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", cleaner_id).execute()
        if not (getattr(updated, "data", None) or []):
            raise RuntimeError("Subscription saved, but the cleaner profile was not updated.")
        self.audit.record(
            AdminActionCreate(
                admin_user_id=admin_user_id,
                action="SUBSCRIPTION_ACTIVATED",
                target_type="cleaner_profiles",
                target_id=cleaner_id,
                reason=reason,
                metadata={"payment_id": payment_id, "period": payload},
            )
        )
        data = getattr(response, "data", None) or []
        return data[0] if data else None